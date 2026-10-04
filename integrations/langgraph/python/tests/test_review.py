import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from vinc_langgraph import ReviewOutput, UnparsedReview, arun_review, run_review

P1 = json.loads((Path(__file__).parents[3] / "fixtures/reviews/p1-invalid.json").read_text(
    encoding="utf-8"))["raw"]
VALID = {"verdict": "RETURN", "findings": [
    {"what": "Particle follows variable", "cite": ["record:rule"]},
    {"what": "Wrong register", "cite": ["record:register"]},
]}


class ReviewModel(BaseChatModel):
    outputs: list
    calls: list = []
    @property
    def _llm_type(self):
        return "recording-review"
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.outputs[0]))])
        self.outputs.pop(0)
        return result


def test_real_chat_model_fallback_sync_and_async_retry():
    model = ReviewModel(outputs=[P1, json.dumps(VALID)])
    result = run_review(model, "Review", native_structured=False)
    assert len(result.findings) == 2 and len(model.calls) == 2
    assert "only parse retry" in model.calls[1][-1].text
    model = ReviewModel(outputs=[P1, json.dumps(VALID)])
    result = asyncio.run(arun_review(model, "Review", native_structured=False))
    assert len(result.findings) == 2 and len(model.calls) == 2


def test_persistent_saved_failure_is_not_an_empty_review():
    model = ReviewModel(outputs=[P1, P1])
    with pytest.raises(UnparsedReview):
        run_review(model, "Review", native_structured=False)
    assert len(model.calls) == 2


@pytest.mark.parametrize("async_mode", [False, True])
def test_real_native_tool_schema_parser_retries_invalid_arguments(async_mode):
    class NativeToolModel(ReviewModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            assert tools == [ReviewOutput]
            return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls.append(list(messages))
            message = AIMessage(content="", additional_kwargs={"tool_calls": [{
                "id": "review-1", "type": "function", "function": {
                    "name": "ReviewOutput", "arguments": self.outputs.pop(0)}}]})
            return ChatResult(generations=[ChatGeneration(message=message)])
    model = NativeToolModel(outputs=[P1, json.dumps(VALID)])
    result = asyncio.run(arun_review(model, "Review")) if async_mode else run_review(model, "Review")
    assert len(result.findings) == 2 and len(model.calls) == 2


def test_native_schema_wrapper_and_config_are_used_and_parse_failure_retries():
    class NativeRunner:
        calls = []
        def invoke(self, messages, config=None):
            self.calls.append((messages, config))
            return {"parsed": None, "parsing_error": ValueError("private response")} if len(
                self.calls) == 1 else {"parsed": ReviewOutput.model_validate(VALID), "parsing_error": None}
        async def ainvoke(self, messages, config=None):
            return self.invoke(messages, config)
    class NativeModel:
        runner = NativeRunner()
        def with_structured_output(self, schema, *, include_raw):
            assert schema is ReviewOutput and include_raw is True
            return self.runner
    model = NativeModel()
    config = {"tags": ["review"]}
    assert len(run_review(model, "Review", config=config).findings) == 2
    assert all(call[1] is config for call in model.runner.calls)
    assert "private response" not in model.runner.calls[1][0][-1].text
    model.runner.calls.clear()
    assert len(asyncio.run(arun_review(model, "Review", config=config)).findings) == 2


def test_unsupported_native_model_does_not_silently_retry_or_downgrade():
    model = ReviewModel(outputs=[json.dumps(VALID)])
    with pytest.raises(NotImplementedError):
        run_review(model, "Review")
    assert model.calls == []
