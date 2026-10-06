from __future__ import annotations

import asyncio
import hashlib
import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument
from vinc_langgraph import VincContextMiddleware
from test_langgraph import RecordingModel


def test_role_prefetch_reaches_real_model_without_permission_cache():
    seen = []

    def handler(request):
        seen.append(request)
        payload = ({"found": True, "id": "concept:agent-reviewer", "props": {"reads": []}}
                   if request.method == "GET" else {"status": "resolved",
                       "node": {"id": "decision:fixture", "cite_id": "decision:fixture"},
                       "one_liner": "Keep it."} if request.url.path.endswith("brief")
                   else {"node_hits": []})
        return httpx.Response(200, json=payload)

    with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
        model = RecordingModel()
        agent = create_agent(model, middleware=[VincContextMiddleware(
            client=client, prefetch_role="concept:agent-reviewer")])
        for i in range(2):
            agent.invoke({"messages": [HumanMessage(content="Review the policy.")]})
            assert len(seen) == (i + 1) * 3
            assert "decision:fixture" in model.system_text() and "DATA" in model.system_text()

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            model = RecordingModel()
            agent = create_agent(model, middleware=[VincContextMiddleware(
                async_client=client, prefetch_role="concept:agent-reviewer")])
            await agent.ainvoke({"messages": [HumanMessage(content="Review the policy.")]})
            assert "decision:fixture" in model.system_text()

    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_budget_error_stops_model_and_corrected_middleware_runs(async_mode):
    text = "Mandatory rule. " * 2000
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            payload = {"found": True, "id": "concept:agent-test", "props": {"reads": ["large"]}}
        elif request.url.path.startswith("/v1/documents/"):
            payload = {"doc_id": "large", "executed": True, "verified": True, "text": text,
                       "content_hash": hashlib.sha256(text.encode()).hexdigest()}
        else:
            payload = {"status": "none"}
        return httpx.Response(200, json=payload)

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(
            "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
        model = RecordingModel()
        kwargs = {"async_client" if async_mode else "client": client,
                  "prefetch_role": "concept:agent-test"}

        async def invoke(middleware):
            agent = create_agent(model, middleware=[middleware])
            value = {"messages": [HumanMessage(content="Review.")]}
            return await agent.ainvoke(value) if async_mode else agent.invoke(value)

        with pytest.raises(VincInvalidArgument) as caught:
            await invoke(VincContextMiddleware(**kwargs))
        assert not model.calls
        # client 0.1.4: brief/search 는 검증 전에 나갈 수 있고 실패 시 버려진다.
        assert [p for p in seen if not p.endswith(("brief", "search"))] == [
            "/v1/roles/concept:agent-test", "/v1/documents/large"]
        await invoke(VincContextMiddleware(**kwargs,
            prefetch_max_chars=caught.value.details["recommended_max_chars"]))
        assert len(model.calls) == 1
        assert text in model.system_text()

    asyncio.run(go())
