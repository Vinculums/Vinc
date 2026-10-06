import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincClient
from vinc_langgraph import VincContextMiddleware

from test_langgraph import RecordingModel

TEAM = "01a0a0e0-3f91-7128-9876-6b543f28de0a"


def test_sync_and_async_readers_never_share_a_private_context():
    seen = []
    def handler(request):
        seen.append(request)
        title = "TEAM PRIVATE" if request.url.params.get("space") else "PERSONAL PRIVATE"
        return httpx.Response(200, json={"status": "resolved", "node": {"id": "concept:fixture",
            "title": title}, "one_liner": title})
    async def go():
        with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as sync:
            async with AsyncVincClient("vinc_ro_" + "b" * 32, space=TEAM,
                                       transport=httpx.MockTransport(handler)) as async_reader:
                middleware = VincContextMiddleware(sync, async_reader)
                model = RecordingModel()
                agent = create_agent(model, middleware=[middleware])
                for _ in range(2):
                    agent.invoke({"messages": [HumanMessage("same question")]})
                    assert "PERSONAL PRIVATE" in model.system_text()
                    assert "TEAM PRIVATE" not in model.system_text()
                    await agent.ainvoke({"messages": [HumanMessage("same question")]})
                    assert "TEAM PRIVATE" in model.system_text()
                    assert "PERSONAL PRIVATE" not in model.system_text()
                assert len(seen) == 2
    asyncio.run(go())


@pytest.mark.parametrize("change", ["space", "reader", "budget", "excerpts", "fallback"])
def test_changed_effective_reader_space_or_retrieval_options_force_a_read(change):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "resolved", "node": {"id": "concept:fixture"}})
    with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
        middleware = VincContextMiddleware(client)
        model = RecordingModel()
        agent = create_agent(model, middleware=[middleware])
        agent.invoke({"messages": [HumanMessage("same question")]})
        if change == "space":
            client.space = TEAM
        elif change == "reader":
            client._http.headers["Authorization"] = "Bearer vinc_ro_" + "b" * 32
        elif change == "budget":
            middleware.max_chars = 1000
        elif change == "excerpts":
            middleware.max_excerpts = 1
        else:
            middleware.search_fallback = False
        agent.invoke({"messages": [HumanMessage("same question")]})
        assert len(seen) == 2
        if change == "space":
            assert seen[-1].url.params["space"] == TEAM


def test_source_change_requires_ttl_or_explicit_invalidation(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("vinc_langgraph.middleware.time.monotonic", lambda: clock[0])
    source = ["OLD RULE"]
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "resolved",
            "node": {"id": "decision:fixture", "title": source[0]}})
    with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
        middleware = VincContextMiddleware(client, cache_seconds=60)
        model = RecordingModel()
        agent = create_agent(model, middleware=[middleware])
        def invoke():
            agent.invoke({"messages": [HumanMessage("same question")]})
            return model.system_text()
        assert "OLD RULE" in invoke()
        source[0] = "UPDATED RULE"
        assert "OLD RULE" in invoke()
        middleware.clear_cache()
        assert "UPDATED RULE" in invoke()
        source[0] = "EXPIRED RULE"
        clock[0] = 61
        assert "EXPIRED RULE" in invoke()
        assert len(seen) == 3
