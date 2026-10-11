import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincAuthError, VincClient
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


def test_inflight_read_cannot_repopulate_a_cleared_cache():
    async def go():
        started, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def handler(request):
            seen.append(request)
            first = len(seen) == 1
            if first:
                started.set()
                await asyncio.wait_for(release.wait(), timeout=2)
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "title": "OLD RULE" if first else "UPDATED RULE"}})
        async with AsyncVincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
            middleware = VincContextMiddleware(async_client=client)
            model = RecordingModel()
            agent = create_agent(model, middleware=[middleware])
            first = asyncio.create_task(agent.ainvoke({"messages": [HumanMessage("same question")]}))
            await asyncio.wait_for(started.wait(), timeout=2)
            middleware.clear_cache()
            release.set()
            await first
            await agent.ainvoke({"messages": [HumanMessage("same question")]})
            assert "UPDATED RULE" in model.system_text() and "OLD RULE" not in model.system_text()
            assert len(seen) == 2
    asyncio.run(go())


@pytest.mark.parametrize("invalidation", ["clear", "ttl", "disabled"])
def test_permission_failure_after_invalidation_never_falls_back_to_cached_text(monkeypatch, invalidation):
    clock = [0.0]
    monkeypatch.setattr("vinc_langgraph.middleware.time.monotonic", lambda: clock[0])
    denied = [False]
    seen = []

    def handler(request):
        seen.append(request)
        if denied[0]:
            return httpx.Response(403, json={"error": {"code": "permission_denied"}})
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:synthetic", "title": "Synthetic permitted text"}})

    with VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
        middleware = VincContextMiddleware(client, cache_seconds=0 if invalidation == "disabled" else 60)
        agent = create_agent(RecordingModel(), middleware=[middleware])
        invoke = lambda: agent.invoke({"messages": [HumanMessage("Synthetic question")]})
        invoke()
        denied[0] = True
        if invalidation != "disabled":
            invoke()
            assert len(seen) == 1
        if invalidation == "clear":
            middleware.clear_cache()
        elif invalidation == "ttl":
            clock[0] = 60
        with pytest.raises(VincAuthError, match="permission_denied"):
            invoke()
        assert len(seen) == 2
