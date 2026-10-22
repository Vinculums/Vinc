"""prefetch_pin: 고정한 역할이 바뀌었으면 before_run 이 모델을 부르기 전에 멈춘다."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from agent_framework import Agent
from vinc_agent_framework import VincContextProvider, prefetch
from vinc_client import AsyncVincClient, VincRoleChanged
from test_provider import RecordingChatClient

KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-reviewer"
PIN = "a" * 64
NEW = "b" * 64


class Hub:
    """역할의 지금 해시는 ``current`` 다. 다른 고정이 오면 409 role_changed 로 답한다."""

    def __init__(self, current: str = PIN) -> None:
        self.current = current
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            expected = request.url.params.get("expected_hash")
            if expected is not None and expected != self.current:
                return httpx.Response(409, json={"error": {
                    "code": "role_changed", "message": "The role changed since it was pinned.",
                    "details": {"id": ROLE, "rev": 4, "hash": self.current}}})
            return httpx.Response(200, json={"found": True, "id": ROLE, "props": {"reads": []},
                                             "rev": 3, "hash": self.current})
        if path.endswith("brief"):
            return httpx.Response(200, json={"status": "none"})
        return httpx.Response(200, json={"node_hits": []})

    def role_pins(self) -> list:
        return [r.url.params.get("expected_hash") for r in self.seen
                if r.url.path.startswith("/v1/roles/")]


def run(hub: Hub, pin: str | None, chat: RecordingChatClient) -> None:
    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(hub)) as client:
            await Agent(chat, context_providers=[VincContextProvider(
                client, prefetch_role=ROLE, prefetch_pin=pin)]).run("Review the policy.")
    asyncio.run(go())


def test_a_matching_pin_runs_and_sends_the_pin():
    hub, chat = Hub(), RecordingChatClient()
    run(hub, PIN, chat)
    assert len(chat.calls) == 1
    assert hub.role_pins() == [PIN]


def test_a_changed_role_stops_before_the_model():
    hub, chat = Hub(current=NEW), RecordingChatClient()
    with pytest.raises(VincRoleChanged) as caught:
        run(hub, PIN, chat)
    assert caught.value.details == {"id": ROLE, "rev": 4, "hash": NEW}
    assert chat.calls == []
    assert hub.role_pins() == [PIN]
    assert not any(r.url.path.startswith("/v1/documents/") for r in hub.seen)


def test_a_pin_without_a_role_is_refused_at_construction():
    with pytest.raises(ValueError, match="prefetch_pin needs prefetch_role"):
        VincContextProvider(AsyncVincClient(KEY), prefetch_pin=PIN)


def test_the_prefetch_helper_passes_the_pin():
    hub = Hub(current=NEW)

    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(hub)) as client:
            with pytest.raises(VincRoleChanged):
                await prefetch(client, ROLE, "Review.", pin=PIN)
            assert await prefetch(client, ROLE, "Review.", pin=NEW)
    asyncio.run(go())
    assert hub.role_pins() == [PIN, NEW]
