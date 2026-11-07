"""Role prefetch mode applies the contract's capability policy and tool call budget."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest
from agent_framework import (Agent, BaseChatClient, ChatMiddlewareLayer, ChatResponse, Content,
                             FunctionInvocationLayer, Message, tool)
from vinc_agent_framework import VincContextProvider
from vinc_client import AsyncVincClient, VincInvalidArgument

KEY = "vinc_ro_" + "a" * 32
ROLE = "concept:agent-release"
MAP = {"merge_pr": "github.pr.merge", "run_shell": "shell.exec", "lookup": "vinc.read"}

ran: list[str] = []


def hub(**props: Any):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": ROLE,
                                             "props": {"reads": [], **props}})
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "none"})
        return httpx.Response(200, json={"node_hits": []})
    return handler


@tool(name="merge_pr")
def merge_pr(number: int) -> str:
    """Merge a pull request."""
    ran.append(f"merge {number}")
    return "merged"


@tool(name="run_shell")
def run_shell(command: str) -> str:
    """Run a shell command."""
    ran.append(command)
    return "ran"


@tool(name="lookup")
def lookup(query: str) -> str:
    """A local lookup."""
    ran.append(query)
    return "local answer"


class Script(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Makes the scripted calls one per model call, then answers; keeps the tools it saw."""

    def __init__(self, calls) -> None:
        super().__init__()
        self.calls = calls
        self.seen_tools: list[list[str]] = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.seen_tools.append(sorted(t.name for t in options.get("tools") or []))
        done = sum(1 for m in messages for c in m.contents if c.type == "function_result")
        if done < len(self.calls):
            name, args = self.calls[done]
            return ChatResponse(messages=Message("assistant", [
                Content.from_function_call(f"c{done}", name, arguments=args)]))
        return ChatResponse(messages=Message("assistant", ["done"]))


def results(response) -> list[str]:
    return [str(c.result) for m in response.messages for c in m.contents
            if c.type == "function_result"]


def run(chat, handler, **options):
    ran.clear()

    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            agent = Agent(chat, tools=[merge_pr, run_shell, lookup], context_providers=[
                VincContextProvider(client, prefetch_role=ROLE, **options)])
            return await agent.run("Ship it.")
    return asyncio.run(go())


def test_denied_capabilities_are_hidden_and_approval_ones_ask_for_a_person():
    chat = Script([("merge_pr", {"number": 7}), ("run_shell", {"command": "ls"})])
    response = run(chat, hub(capabilities={"deny": ["shell.*"], "approve": ["github.pr.merge"]}),
                   capability_map=MAP)
    assert all(tools == ["lookup", "merge_pr"] for tools in chat.seen_tools)
    first, second = results(response)
    assert "needs a person's approval before the merge_pr tool runs" in first
    assert "does not allow the run_shell tool" in second
    assert ran == []


def test_the_budget_caps_all_tool_calls_of_the_run():
    chat = Script([("lookup", {"query": "a"}), ("lookup", {"query": "b"})])
    response = run(chat, hub(budget={"tool_calls": 1}))
    assert results(response) == [
        "local answer",
        f"The role {ROLE} allows 1 tool calls per run; answer from what you already have."]
    assert ran == ["a"]


def test_capabilities_without_a_map_warn_and_change_nothing(caplog):
    chat = Script([("run_shell", {"command": "ls"})])
    with caplog.at_level(logging.WARNING):
        response = run(chat, hub(capabilities={"deny": ["shell.*"]}))
    assert results(response) == ["ran"]
    assert any("not enforced" in r.getMessage() for r in caplog.records)
    # 숨길 도구가 없으니 채팅 미들웨어 경고(disallows tools)는 나오지 않는다.
    assert not any("disallows tools" in r.getMessage() for r in caplog.records)


def test_a_malformed_policy_stops_the_run_before_the_model():
    chat = Script([])
    with pytest.raises(VincInvalidArgument) as caught:
        run(chat, hub(output_kind="essay"))
    assert caught.value.code == "invalid_role_contract"
    assert chat.seen_tools == []
