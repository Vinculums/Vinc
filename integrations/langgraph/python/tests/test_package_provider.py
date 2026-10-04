from __future__ import annotations

import asyncio

import httpx
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincClient, build_package_fragment
from vinc_langgraph import PackageProvider, aget_vinc_package_context, get_vinc_package_context

from test_langgraph import RecordingModel


def test_create_agent_middleware_reads_package_and_sources_every_model_call():
    node = build_package_fragment("Which limit?", "Keep the 36 hour threshold.",
                                  claims=[{"claim": "Stale after 36 hours.",
                                           "evidence_ids": ["decision:threshold"]}],
                                  produced_by="producer", run_id="mechanical-fixture",
                                  package_id="record:handoff")["nodes"][0]
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        data = node if request.url.path.endswith("record:handoff") else {"id": "decision:threshold"}
        return httpx.Response(200, json={"found": True, "node": data})

    with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
        model = RecordingModel()
        agent = create_agent(model, middleware=[PackageProvider(
            "record:handoff", space="personal", client=client)])
        for _ in range(2):
            agent.invoke({"messages": [HumanMessage(content="Continue from the package.")]})
            assert "Keep the 36 hour threshold." in model.system_text()
            assert "decision:threshold" in model.system_text() and "DATA" in model.system_text()
        assert len(seen) == 4
        assert "record:handoff" in get_vinc_package_context(client, "record:handoff", space="personal")

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            assert "record:handoff" in await aget_vinc_package_context(
                client, "record:handoff", space="personal")
            model = RecordingModel()
            agent = create_agent(model, middleware=[PackageProvider(
                "record:handoff", space="personal", async_client=client)])
            await agent.ainvoke({"messages": [HumanMessage(content="Continue.")]})
            assert "Keep the 36 hour threshold." in model.system_text()

    asyncio.run(go())
