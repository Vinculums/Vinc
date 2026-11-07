"""Role prefetch mode reads the role once per run, not before every model call."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from vinc_client import AsyncVincClient, VincClient, VincUnavailable
from vinc_langgraph import VincContextMiddleware
from vinc_langgraph.middleware import _run_marker

KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-reviewer"
TEAM = "01a0a0e0-3f91-7128-9876-6b543f28de0a"


class Hub:
    """A hub that answers a role with no required reads; ``fail_roles`` role reads answer 503."""

    def __init__(self, fail_roles: int = 0) -> None:
        self.seen: list[httpx.Request] = []
        self.fail_roles = fail_roles

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            if self.fail_roles:
                self.fail_roles -= 1
                return httpx.Response(503, json={"error": {"code": "upstream_unavailable",
                                                           "message": "down"}})
            return httpx.Response(200, json={"found": True, "id": path.rsplit("/", 1)[-1],
                                             "props": {"reads": []}})
        if path.endswith("brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})

    def role_reads(self) -> int:
        return sum(1 for r in self.seen if r.url.path.startswith("/v1/roles/"))


@tool
def lookup(query: str) -> str:
    """A local tool that is not Vinc."""
    return "local answer"


class TwoToolTurns(BaseChatModel):
    """Calls ``lookup`` in two turns, then answers: three model calls per run."""

    systems: list = []

    @property
    def _llm_type(self) -> str:
        return "two-tool-turns"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        first = messages[0]
        self.systems.append(first.text if isinstance(first, SystemMessage) else "")
        since_human = []
        for message in messages:
            since_human = [] if message.type == "human" else [*since_human, message]
        if sum(1 for m in since_human if m.type == "tool") < 2:
            call = {"name": "lookup", "args": {"query": "x"}, "id": f"t{len(messages)}"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


def make(async_mode: bool, hub: Hub, **kwargs):
    client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
    middleware = VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                       prefetch_role=ROLE, **kwargs)
    model = TwoToolTurns()
    return client, middleware, model, create_agent(model, tools=[lookup], middleware=[middleware])


async def invoke(agent, async_mode: bool, messages):
    value = {"messages": messages}
    return await agent.ainvoke(value) if async_mode else agent.invoke(value)


@pytest.mark.parametrize("async_mode", [False, True])
def test_three_model_calls_in_one_run_read_the_role_once_and_a_new_human_message_reads_again(async_mode):
    async def go():
        hub = Hub()
        _, _, model, agent = make(async_mode, hub)
        state = await invoke(agent, async_mode, [HumanMessage("Review the policy.")])
        assert len(model.systems) == 3
        assert hub.role_reads() == 1
        assert all("decision:fixture" in text for text in model.systems)
        # 같은 대화에 새 사람 메시지가 오면 새 런이다.
        await invoke(agent, async_mode, [*state["messages"], HumanMessage("And the next one?")])
        assert len(model.systems) == 6
        assert hub.role_reads() == 2
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_failed_prefetch_propagates_unchanged_and_is_not_kept(async_mode):
    async def go():
        hub = Hub(fail_roles=1)
        _, _, model, agent = make(async_mode, hub)
        question = HumanMessage("Review the policy.", id="human-1")
        with pytest.raises(VincUnavailable) as caught:
            await invoke(agent, async_mode, [question])
        assert caught.value.code == "upstream_unavailable" and caught.value.status == 503
        assert not model.systems
        # 같은 사람 메시지(같은 id)라도 실패는 담기지 않았으므로 다시 읽는다.
        await invoke(agent, async_mode, [question])
        assert hub.role_reads() == 2
        assert len(model.systems) == 3
    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_clear_cache_drops_the_run_block(async_mode):
    async def go():
        hub = Hub()
        _, middleware, _, agent = make(async_mode, hub)
        question = HumanMessage("Review the policy.", id="human-1")
        await invoke(agent, async_mode, [question])
        await invoke(agent, async_mode, [question])
        assert hub.role_reads() == 1
        middleware.clear_cache()
        await invoke(agent, async_mode, [question])
        assert hub.role_reads() == 2
    asyncio.run(go())


@pytest.mark.parametrize("change", ["role", "budget", "reader", "space"])
def test_a_changed_role_budget_reader_or_space_reads_again(change):
    hub = Hub()
    client, middleware, _, agent = make(False, hub)
    question = HumanMessage("Review the policy.", id="human-1")
    agent.invoke({"messages": [question]})
    if change == "role":
        middleware.prefetch_role = "concept:agent-other"
    elif change == "budget":
        middleware.prefetch_max_chars += 1
    elif change == "reader":
        client._http.headers["Authorization"] = "Bearer vinc_ro_" + "b" * 32
    else:
        client.space = TEAM
    agent.invoke({"messages": [question]})
    assert hub.role_reads() == 2
    if change == "space":
        assert hub.seen[-1].url.params["space"] == TEAM


def test_kept_run_blocks_are_bounded():
    hub = Hub()
    _, middleware, _, agent = make(False, hub)
    for i in range(70):
        agent.invoke({"messages": [HumanMessage("Review.", id=f"human-{i}")]})
    assert hub.role_reads() == 70
    assert len(middleware._prefetched) == 64


def test_run_marker_prefers_the_message_id_and_falls_back_to_text():
    assert _run_marker([HumanMessage("old", id="a"), AIMessage("x"), HumanMessage("new", id="b")]) == ("id", "b")
    assert _run_marker([HumanMessage("old", id="a"), HumanMessage(" new ")]) == ("text", "new")
    assert _run_marker([]) == ("text", "")
