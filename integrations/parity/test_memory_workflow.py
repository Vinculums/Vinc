import asyncio
import hashlib
import json

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message, tool
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lg_tool
from vinc_agent_framework import PackageProvider as MafPackageProvider, VincContextProvider
from vinc_client import AsyncVincClient, VincClient, build_package_fragment
from vinc_langgraph import PackageProvider as LgPackageProvider, VincContextMiddleware

ROLE = "concept:agent-reviewer"
KEY = "vinc_ro_" + "a" * 32
DOC_TEXT = "Every mandatory rule survives.\n" + "x" * 5000 + "\nMANDATORY TAIL"


@pytest.mark.parametrize("mode,lg_reads,maf_reads", [("context", 1, 1), ("prefetch", 8, 4), ("package", 4, 2)])
def test_repeated_model_tool_loop_has_explicit_retrieval_and_handoff_cost(mode, lg_reads, maf_reads):
    package = build_package_fragment("Which policy?", "Keep the exact recommendation.",
        claims=[{"claim": "Keep the source state.", "evidence_ids": ["decision:rule"]}],
        produced_by="producer", run_id="offline-loop", package_id="record:package")["nodes"][0]
    source = {"id": "decision:rule", "title": "Rule", "validity": "historical", "rev": 2,
              "superseded_by": "decision:successor", "content_hash": "source-version"}
    def handler(request, seen):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            result = {"found": True, "id": ROLE, "props": {"reads": ["vinc.spec.loop"]}}
        elif request.url.path.startswith("/v1/documents/"):
            result = {"doc_id": "vinc.spec.loop", "executed": True, "verified": True,
                      "text": DOC_TEXT, "content_hash": hashlib.sha256(DOC_TEXT.encode()).hexdigest()}
        elif request.url.path.startswith("/v1/nodes/"):
            result = {"found": True, "node": package if request.url.path.endswith("record:package") else source}
        elif request.url.path.endswith("brief"):
            result = {"status": "resolved", "node": source, "one_liner": "Keep the rule."}
        else:
            result = {"node_hits": [], "arms": "fts", "vector_rebuilding": True, "vector_note": "index rebuilding"}
        return httpx.Response(200, json=result)
    def assert_context(text):
        assert "DATA" in text and "decision:rule" in text
        assert "historical" in text and "decision:successor" in text
        if mode == "prefetch":
            assert "MANDATORY TAIL" in text
        elif mode == "package":
            assert "Keep the exact recommendation." in text and '"use_as_current": false' in text
    class LoopModel(BaseChatModel):
        calls: int = 0
        @property
        def _llm_type(self):
            return "offline-memory-loop"
        def bind_tools(self, tools, **kwargs):
            return self
        def _generate(self, messages, **kwargs):
            self.calls += 1
            assert_context(messages[0].text)
            response = AIMessage("ok") if self.calls == 2 else AIMessage("", tool_calls=[
                {"id": "check-1", "name": "check_rule", "args": {}}])
            return ChatResult(generations=[ChatGeneration(message=response)])
    @lg_tool
    def check_rule() -> str:
        """Perform a deterministic host check."""
        return "checked"
    seen_lg = []
    with VincClient(KEY, transport=httpx.MockTransport(lambda r: handler(r, seen_lg))) as reader:
        middleware = (LgPackageProvider("record:package", space="personal", client=reader)
                      if mode == "package" else VincContextMiddleware(reader,
                          prefetch_role=ROLE if mode == "prefetch" else None))
        model = LoopModel()
        agent = create_agent(model, tools=[check_rule], middleware=[middleware])
        result = agent.invoke({"messages": [HumanMessage("Review memory.")]})
        assert result["messages"][-1].content == "ok" and model.calls == 2
    assert len(seen_lg) == lg_reads
    class LoopClient(FunctionInvocationLayer, BaseChatClient):
        def __init__(self):
            super().__init__()
            self.calls = 0
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            self.calls += 1
            assert_context(str(options.get("instructions", "")) + "\n" + "\n".join(m.text or "" for m in messages))
            response = Message("assistant", ["ok"]) if self.calls == 2 else Message("assistant", [
                Content.from_function_call("check-1", "check_rule", arguments={})])
            return ChatResponse(messages=response)
    @tool(name="check_rule")
    def maf_check() -> str:
        """Perform a deterministic host check."""
        return "checked"
    async def go():
        seen = []
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(lambda r: handler(r, seen))) as reader:
            provider = (MafPackageProvider(reader, "record:package", space="personal")
                        if mode == "package" else VincContextProvider(reader,
                            prefetch_role=ROLE if mode == "prefetch" else None))
            client = LoopClient()
            agent = Agent(client, tools=[maf_check], context_providers=[provider])
            result = await agent.run("Review memory.")
            assert result.text == "ok" and client.calls == 2
            assert len(seen) == maf_reads
    asyncio.run(go())
