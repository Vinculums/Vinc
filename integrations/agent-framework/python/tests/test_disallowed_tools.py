"""Role prefetch mode keeps the contract's disallowed tools away from the model."""
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
ROLE = "concept:agent-copy-writer"
REFUSAL = f"The role {ROLE} does not allow the Bash tool; continue without it."


def hub(disallowed: Any = ("Bash", "Edit"), seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.startswith("/v1/roles/"):
            props: dict[str, Any] = {"reads": []}
            if disallowed is not None:
                props["disallowed_tools"] = list(disallowed) if isinstance(disallowed, tuple) else disallowed
            return httpx.Response(200, json={"found": True, "id": ROLE, "props": props})
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})
    return handler


ran: list[str] = []


@tool(name="Bash")
def bash(command: str) -> str:
    """Run a shell command."""
    ran.append(command)
    return "ran"


@tool(name="lookup")
def lookup(query: str) -> str:
    """A local lookup."""
    return "local answer"


class _Script:
    """Calls Bash, then lookup, then answers; keeps the tool names the model saw per call."""

    def _setup(self) -> None:
        self.seen_tools: list[list[str]] = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.seen_tools.append(sorted(t.name for t in options.get("tools") or []))
        done = sum(1 for m in messages for c in m.contents if c.type == "function_result")
        if done == 0:
            return ChatResponse(messages=Message("assistant", [
                Content.from_function_call("c1", "Bash", arguments={"command": "rm -rf /"})]))
        if done == 1:
            return ChatResponse(messages=Message("assistant", [
                Content.from_function_call("c2", "lookup", arguments={"query": "x"})]))
        return ChatResponse(messages=Message("assistant", ["done"]))


class LayeredChat(_Script, FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """The documented layer order of a built-in client: the tool loop outside chat middleware."""

    def __init__(self) -> None:
        super().__init__()
        self._setup()


class LoopOnlyChat(_Script, FunctionInvocationLayer, BaseChatClient):
    """A custom client with a tool loop but no chat middleware layer."""

    def __init__(self) -> None:
        super().__init__()
        self._setup()


def function_results(response) -> dict[str, str]:
    calls = {c.call_id: c.name for m in response.messages for c in m.contents
             if c.type == "function_call"}
    return {calls.get(c.call_id, c.call_id): str(c.result) for m in response.messages
            for c in m.contents if c.type == "function_result"}


def run_with(chat, disallowed: Any = ("Bash", "Edit"), seen: list | None = None):
    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(hub(disallowed, seen))) as client:
            agent = Agent(chat, tools=[bash, lookup], context_providers=[VincContextProvider(
                client, prefetch_role=ROLE)])
            return await agent.run("Write the copy.")
    return asyncio.run(go())


def test_disallowed_tools_are_hidden_refused_and_unmatched_entries_warned_once(caplog):
    ran.clear()
    seen: list = []
    chat = LayeredChat()
    with caplog.at_level(logging.WARNING):
        response = run_with(chat, seen=seen)
    assert chat.seen_tools == [["lookup"]] * 3
    results = function_results(response)
    assert results["Bash"] == REFUSAL and results["lookup"] == "local answer"
    assert not ran
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and ROLE in warnings[0] and ": Edit." in warnings[0]
    assert sum(1 for r in seen if r.url.path.startswith("/v1/roles/")) == 1


def test_without_chat_middleware_the_tools_stay_visible_but_calls_are_refused(caplog):
    ran.clear()
    chat = LoopOnlyChat()
    with caplog.at_level(logging.WARNING):
        response = run_with(chat)
    assert chat.seen_tools == [["Bash", "lookup"]] * 3
    assert function_results(response)["Bash"] == REFUSAL and not ran
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "ChatMiddlewareLayer" in warnings[0]


def test_a_role_without_the_list_changes_nothing(caplog):
    ran.clear()
    chat = LayeredChat()
    with caplog.at_level(logging.WARNING):
        response = run_with(chat, disallowed=None)
    assert chat.seen_tools == [["Bash", "lookup"]] * 3
    assert function_results(response)["Bash"] == "ran" and ran
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_malformed_tool_list_stops_the_run_before_the_model():
    chat = LayeredChat()
    with pytest.raises(VincInvalidArgument) as caught:
        run_with(chat, disallowed="Bash")
    assert caught.value.code == "invalid_role_contract"
    assert not chat.seen_tools


def test_concurrent_runs_get_their_own_middleware():
    chat = LayeredChat()

    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(hub())) as client:
            agent = Agent(chat, tools=[bash, lookup], context_providers=[VincContextProvider(
                client, prefetch_role=ROLE)])

            async def one(i):
                return await agent.run(f"Q{i}")
            return await asyncio.gather(*(one(i) for i in range(3)))
    responses = asyncio.run(go())
    assert all(function_results(r)["Bash"] == REFUSAL for r in responses)
    assert all(names == ["lookup"] for names in chat.seen_tools)
