"""prefetch_pin: 고정한 역할이 바뀌었으면 모델을 부르기 전에 런을 멈춘다."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from vinc_client import AsyncVincClient, VincClient, VincRoleChanged
from vinc_langgraph import VincContextMiddleware, aprefetch, prefetch

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


class Answer(BaseChatModel):
    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "answers"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


def make(async_mode: bool, hub: Hub, pin: str | None):
    client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
    middleware = VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                       prefetch_role=ROLE, prefetch_pin=pin)
    model = Answer()
    model.calls = []
    return middleware, model, create_agent(model, middleware=[middleware])


async def invoke(agent, async_mode: bool, messages):
    value = {"messages": messages}
    return await agent.ainvoke(value) if async_mode else agent.invoke(value)


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_matching_pin_runs_and_sends_the_pin(async_mode):
    async def go():
        hub = Hub()
        _, model, agent = make(async_mode, hub, PIN)
        await invoke(agent, async_mode, [HumanMessage("Review the policy.")])
        assert len(model.calls) == 1
        assert hub.role_pins() == [PIN]
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_changed_role_stops_the_run_before_the_model_and_is_not_kept(async_mode):
    async def go():
        hub = Hub(current=NEW)
        _, model, agent = make(async_mode, hub, PIN)
        question = HumanMessage("Review the policy.", id="human-1")
        for _ in range(2):
            with pytest.raises(VincRoleChanged) as caught:
                await invoke(agent, async_mode, [question])
            assert caught.value.details["hash"] == NEW
        assert model.calls == []
        # 실패는 담지 않으므로 같은 런을 다시 시도하면 다시 묻는다.
        assert hub.role_pins() == [PIN, PIN]
        assert not any(r.url.path.startswith("/v1/documents/") for r in hub.seen)
    asyncio.run(go())


def test_moving_the_pin_reads_again():
    hub = Hub(current=NEW)
    middleware, model, agent = make(False, hub, NEW)
    question = HumanMessage("Review the policy.", id="human-1")
    agent.invoke({"messages": [question]})
    middleware.prefetch_pin = PIN
    with pytest.raises(VincRoleChanged):
        agent.invoke({"messages": [question]})
    assert hub.role_pins() == [NEW, PIN] and len(model.calls) == 1


def test_a_pin_without_a_role_is_refused_at_construction():
    with pytest.raises(ValueError, match="prefetch_pin needs prefetch_role"):
        VincContextMiddleware(client=VincClient(KEY), prefetch_pin=PIN)


def test_node_helpers_pass_the_pin():
    hub = Hub(current=NEW)
    with VincClient(KEY, transport=httpx.MockTransport(hub)) as client:
        with pytest.raises(VincRoleChanged):
            prefetch(client, ROLE, "Review.", pin=PIN)
        assert prefetch(client, ROLE, "Review.", pin=NEW)

    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(hub)) as client:
            with pytest.raises(VincRoleChanged):
                await aprefetch(client, ROLE, "Review.", pin=PIN)
    asyncio.run(go())
    assert hub.role_pins() == [PIN, NEW, PIN]
