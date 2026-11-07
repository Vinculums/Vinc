"""Role prefetch mode applies the contract's capability policy and tool call budget."""
from __future__ import annotations

import asyncio
import logging

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
ROLE = "concept:agent-release"
MAP = {"merge_pr": "github.pr.merge", "run_shell": "shell.exec", "lookup": "vinc.read"}


class Hub:
    def __init__(self, **props) -> None:
        self.props = props

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": ROLE,
                                             "props": {"reads": [], **self.props}})
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "none"})
        return httpx.Response(200, json={"node_hits": []})


ran: list[str] = []


@tool
def merge_pr(number: int) -> str:
    """Merge a pull request."""
    ran.append(f"merge {number}")
    return "merged"


@tool
def run_shell(command: str) -> str:
    """Run a shell command."""
    ran.append(command)
    return "ran"


@tool
def lookup(query: str) -> str:
    """A local lookup."""
    ran.append(query)
    return "local answer"


class Script(BaseChatModel):
    """Makes the scripted tool calls one per model call, then answers."""

    calls: list = []
    seen_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "script"

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append(sorted(t.name for t in tools))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        done = sum(1 for m in messages if m.type == "tool")
        if done < len(self.calls):
            name, args = self.calls[done]
            call = {"name": name, "args": args, "id": f"c{done}"}
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


def run(async_mode, hub, calls, **options):
    ran.clear()

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
        model = Script(calls=calls, seen_tools=[])
        agent = create_agent(model, tools=[merge_pr, run_shell, lookup], middleware=[
            VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                  prefetch_role=ROLE, **options)])
        value = {"messages": [HumanMessage("Ship it.")]}
        state = await agent.ainvoke(value) if async_mode else agent.invoke(value)
        return model, [m for m in state["messages"] if isinstance(m, ToolMessage)]
    return asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_denied_capabilities_are_hidden_and_approval_ones_ask_for_a_person(async_mode):
    hub = Hub(capabilities={"deny": ["shell.*"], "approve": ["github.pr.merge"]})
    model, results = run(async_mode, hub, [("merge_pr", {"number": 7}), ("run_shell", {"command": "ls"})],
                         capability_map=MAP)
    assert all(tools == ["lookup", "merge_pr"] for tools in model.seen_tools)
    assert "needs a person's approval before the merge_pr tool runs" in results[0].content
    assert "does not allow the run_shell tool" in results[1].content
    assert ran == []


@pytest.mark.parametrize("async_mode", [False, True])
def test_the_budget_caps_all_tool_calls_of_the_run(async_mode):
    model, results = run(async_mode, Hub(budget={"tool_calls": 1}),
                         [("lookup", {"query": "a"}), ("lookup", {"query": "b"})])
    assert results[0].content == "local answer"
    assert results[1].content == f"The role {ROLE} allows 1 tool calls per run; answer from what you already have."
    assert ran == ["a"]


def test_capabilities_without_a_map_warn_and_change_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        model, results = run(False, Hub(capabilities={"deny": ["shell.*"]}),
                             [("run_shell", {"command": "ls"})])
    assert results[0].content == "ran"
    assert any("not enforced" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_malformed_policy_stops_the_run_before_the_model(async_mode):
    with pytest.raises(VincInvalidArgument) as caught:
        run(async_mode, Hub(act_policy={"merge": "whenever"}), [])
    assert caught.value.code == "invalid_role_contract"


@tool("mcp__github__merge_pull_request")
def gh_merge(number: int) -> str:
    """Merge a pull request (GitHub MCP)."""
    ran.append(f"gh merge {number}")
    return "merged"


@tool("mcp__github__pull_request_read")
def gh_read(number: int) -> str:
    """Read a pull request (GitHub MCP)."""
    ran.append(f"gh read {number}")
    return "pr body"


@pytest.mark.parametrize("async_mode", [False, True])
def test_github_mcp_tools_are_enforced_with_no_capability_map(async_mode):
    # 지도를 주지 않아도 기본 지도가 GitHub MCP 의 도구 이름을 능력 id 에 묶는다
    ran.clear()
    hub = Hub(capabilities={"deny": ["github.pr.merge"], "allow": ["github.pr.read"]})

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(hub))
        model = Script(calls=[("mcp__github__merge_pull_request", {"number": 7}),
                              ("mcp__github__pull_request_read", {"number": 7})], seen_tools=[])
        agent = create_agent(model, tools=[gh_merge, gh_read, lookup], middleware=[
            VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                  prefetch_role=ROLE)])
        value = {"messages": [HumanMessage("Ship it.")]}
        state = await agent.ainvoke(value) if async_mode else agent.invoke(value)
        return model, [m for m in state["messages"] if isinstance(m, ToolMessage)]
    model, results = asyncio.run(go())
    assert all(tools == ["lookup", "mcp__github__pull_request_read"] for tools in model.seen_tools)
    assert "does not allow the mcp__github__merge_pull_request tool" in results[0].content
    assert results[1].content == "pr body" and ran == ["gh read 7"]


@tool("mcp__gh__merge_pull_request")
def gh_alias_merge(number: int) -> str:
    """Merge a pull request through a GitHub server the host named gh."""
    ran.append(f"gh alias merge {number}")
    return "merged"


@pytest.mark.parametrize("where", ["role", "device"])
def test_a_github_server_the_host_named_gh_is_enforced_through_tool_servers(where, monkeypatch):
    # 역할의 tool_servers 나 이 기기의 VINC_TOOL_SERVERS 가 gh 를 GitHub 으로 읽게 한다
    ran.clear()
    props = {"capabilities": {"deny": ["github.pr.merge"]}}
    if where == "role":
        props["tool_servers"] = {"gh": "github"}
        monkeypatch.delenv("VINC_TOOL_SERVERS", raising=False)
    else:
        monkeypatch.setenv("VINC_TOOL_SERVERS", "gh=github")
    client = VincClient(KEY, transport=httpx.MockTransport(Hub(**props)))
    model = Script(calls=[("mcp__gh__merge_pull_request", {"number": 7})], seen_tools=[])
    agent = create_agent(model, tools=[gh_alias_merge, lookup], middleware=[
        VincContextMiddleware(client=client, prefetch_role=ROLE)])
    state = agent.invoke({"messages": [HumanMessage("Ship it.")]})
    results = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert all(tools == ["lookup"] for tools in model.seen_tools)
    assert "does not allow the mcp__gh__merge_pull_request tool" in results[0].content
    assert ran == []
