"""Role prefetch mode keeps the contract's disallowed tools away from the model."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument
from vinc_langgraph import VincContextMiddleware

KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-copy-writer"
REFUSAL = f"The role {ROLE} does not allow the Bash tool; continue without it."


class Hub:
    def __init__(self, disallowed=("Bash", "Edit")) -> None:
        self.disallowed = disallowed
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": ROLE, "props": {
                "reads": [], "disallowed_tools": self.disallowed}})
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})

    def role_reads(self) -> int:
        return sum(1 for r in self.seen if r.url.path.startswith("/v1/roles/"))


ran: list[str] = []


@tool("Bash")
def bash(command: str) -> str:
    """Run a shell command."""
    ran.append(command)
    return "ran"


@tool
def lookup(query: str) -> str:
    """A local lookup."""
    return "local answer"


class CallsBashThenLookup(BaseChatModel):
    """Calls Bash, then lookup, then answers; keeps the tool names it was given per call."""

    seen_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "calls-bash-then-lookup"

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append(sorted(t.name for t in tools))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        done = sum(1 for m in messages if m.type == "tool")
        if done == 0:
            call = {"name": "Bash", "args": {"command": "rm -rf /"}, "id": "c1"}
        elif done == 1:
            call = {"name": "lookup", "args": {"query": "x"}, "id": "c2"}
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])


def tool_messages(state) -> dict[str, str]:
    return {m.name: m.content for m in state["messages"] if isinstance(m, ToolMessage)}


@pytest.mark.parametrize("async_mode", [False, True])
def test_disallowed_tools_are_hidden_refused_and_unmatched_entries_warned_once(async_mode, caplog):
    ran.clear()

    async def go():
        hub = Hub()
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
        model = CallsBashThenLookup()
        agent = create_agent(model, tools=[bash, lookup], middleware=[VincContextMiddleware(
            **{"async_client" if async_mode else "client": client}, prefetch_role=ROLE)])
        value = {"messages": [HumanMessage("Write the copy.")]}
        with caplog.at_level(logging.WARNING, logger="vinc_client"):
            state = await agent.ainvoke(value) if async_mode else agent.invoke(value)
        # 세 번의 모델 호출 모두 Bash 를 못 본다.
        assert model.seen_tools == [["lookup"]] * 3
        results = tool_messages(state)
        assert results["Bash"] == REFUSAL and results["lookup"] == "local answer"
        assert not ran
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and ROLE in warnings[0] and ": Edit." in warnings[0]
        # 계약은 프리페치가 읽은 것을 쓴다. 역할 읽기는 런마다 한 번이다.
        assert hub.role_reads() == 1

    asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_malformed_tool_list_stops_the_run_before_the_model(async_mode):
    async def go():
        hub = Hub(disallowed="Bash")
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
        model = CallsBashThenLookup()
        agent = create_agent(model, tools=[bash, lookup], middleware=[VincContextMiddleware(
            **{"async_client" if async_mode else "client": client}, prefetch_role=ROLE)])
        value = {"messages": [HumanMessage("Write the copy.")]}
        with pytest.raises(VincInvalidArgument) as caught:
            await agent.ainvoke(value) if async_mode else agent.invoke(value)
        assert caught.value.code == "invalid_role_contract"
        assert not model.seen_tools

    asyncio.run(go())


def test_a_role_without_the_list_changes_nothing(caplog):
    hub = Hub(disallowed=None)
    with VincClient(KEY, transport=httpx.MockTransport(hub)) as client:
        model = CallsBashThenLookup()
        agent = create_agent(model, tools=[bash, lookup], middleware=[VincContextMiddleware(
            client=client, prefetch_role=ROLE)])
        ran.clear()
        with caplog.at_level(logging.WARNING, logger="vinc_client"):
            state = agent.invoke({"messages": [HumanMessage("Write the copy.")]})
        assert model.seen_tools == [["Bash", "lookup"]] * 3
        assert tool_messages(state)["Bash"] == "ran" and ran
        assert not caplog.records


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_tool_call_after_clear_cache_reads_the_contract_again_and_still_refuses(async_mode):
    hub = Hub()

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
        middleware = VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                           prefetch_role=ROLE)
        request = SimpleNamespace(tool_call={"name": "Bash", "args": {}, "id": "c1"},
                                  state={"messages": [HumanMessage("Write.", id="h1")]})

        def handler(_request):
            raise AssertionError("a disallowed tool ran")

        async def ahandler(_request):
            raise AssertionError("a disallowed tool ran")

        result = (await middleware.awrap_tool_call(request, ahandler) if async_mode
                  else middleware.wrap_tool_call(request, handler))
        assert isinstance(result, ToolMessage) and result.content == REFUSAL
        assert result.tool_call_id == "c1" and hub.role_reads() == 1

    asyncio.run(go())
