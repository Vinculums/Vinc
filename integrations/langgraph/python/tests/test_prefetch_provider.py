from __future__ import annotations

import asyncio
import httpx
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincClient
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
