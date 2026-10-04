import asyncio
import json
from pathlib import Path

import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, Message

from vinc_agent_framework import ReviewOutput, UnparsedReview, run_review

P1 = json.loads((Path(__file__).parents[3] / "fixtures/reviews/p1-invalid.json").read_text(
    encoding="utf-8"))["raw"]
VALID = json.dumps({"verdict": "RETURN", "findings": [
    {"what": "Particle follows variable", "cite": ["record:rule"]},
    {"what": "Wrong register", "cite": ["record:register"]},
]})


class ReviewClient(BaseChatClient):
    def __init__(self, outputs):
        super().__init__()
        self.outputs = iter(outputs)
        self.calls = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls.append({"messages": list(messages), "options": dict(options)})
        return ChatResponse(messages=Message("assistant", [next(self.outputs)]))


@pytest.mark.parametrize("native", [True, False])
def test_real_agent_retries_saved_failure_once_preserving_findings_and_options(native):
    client = ReviewClient([P1, VALID])
    agent = Agent(client=client, instructions="Review the copy", name="reviewer")
    options = {"temperature": 0}
    async def go():
        return await run_review(agent, "Review these lines", native_structured=native, options=options)
    result = asyncio.run(go())
    assert len(result.findings) == 2 and len(client.calls) == 2
    assert options == {"temperature": 0}
    assert all(call["options"]["tool_choice"] == "none" for call in client.calls)
    assert client.calls[0]["options"].get("response_format") is (ReviewOutput if native else None)
    assert "only parse retry" in client.calls[1]["messages"][-1].text


def test_real_agent_persistent_saved_failure_escalates_without_third_call():
    client = ReviewClient([P1, P1])
    agent = Agent(client=client, name="reviewer")
    async def go():
        return await run_review(agent, "Review these lines")
    with pytest.raises(UnparsedReview):
        asyncio.run(go())
    assert len(client.calls) == 2


def test_agent_service_error_propagates_with_no_retry():
    class FailingAgent:
        calls = 0
        async def run(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("service error")
    agent = FailingAgent()
    with pytest.raises(RuntimeError, match="service error"):
        asyncio.run(run_review(agent, "Review"))
    assert agent.calls == 1
