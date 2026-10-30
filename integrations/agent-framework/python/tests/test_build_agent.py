"""build_agent: a running Agent Framework agent from a role address, in one call."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_framework import BaseChatClient, ChatResponse, Message
from vinc_agent_framework import VincAgent, build_agent
from vinc_client import AsyncVincClient, VincRoleChanged
from vinc_client.agent_plan import AgentRun
from vinc_client.output_gate import GateResult, OutputGateStopped

ROLE = "concept:agent-dev-core"
SK = "vinc_sk_" + "b" * 32
RO = "vinc_ro_" + "a" * 32
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "output_gate"
PROFILE = next(p for p in json.loads(
    (FIXTURES / "proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]
    if p["id"] == ROLE)
PLAIN = "The change reads correctly; two findings follow.\nSources: decision:fixture"


class ScriptedChatClient(BaseChatClient):
    """Answers with ``reply`` and keeps what the agent sent."""

    def __init__(self, reply: str = PLAIN) -> None:
        super().__init__()
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.calls.append({"messages": list(messages), "options": dict(options)})
        return ChatResponse(messages=Message("assistant", [self.reply]))

    def sent_text(self) -> str:
        call = self.calls[-1]
        parts = [str(call["options"].get("instructions") or "")]
        parts += [m.text or "" for m in call["messages"]]
        return "\n".join(parts)


class Hub:
    """A mock hub: serves one role and records every request."""

    def __init__(self, **props: Any) -> None:
        self.hash = "a" * 64
        self.props = {**PROFILE, "mission": "m", "tier": "execute", "rung": "strong",
                      "never": ["merge"], "reads": [], **props}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            return httpx.Response(200, json={
                "found": True, "id": ROLE, "title": "Core developer", "rev": 2, "hash": self.hash,
                "props": self.props,
                "works_with": {"hands_to": [{"from": ROLE, "to": "concept:agent-gate-runner"}],
                               "receives_from": []}})
        if path.startswith("/v1/nodes/"):
            return httpx.Response(200, json={"executed": True, "found": True,
                                             "node": {"id": path.rsplit("/", 1)[-1]}})
        if path == "/v1/fragments":
            return httpx.Response(200, json={"merged": {"nodes": {"created": 1}}, "stored": [],
                                             "warnings": []})
        if path.endswith("/brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})

    def posts(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests
                if r.method == "POST" and r.url.path == "/v1/fragments"]


def client(hub: Hub, key: str = SK) -> AsyncVincClient:
    return AsyncVincClient(key, transport=httpx.MockTransport(hub))


def run(coro):
    return asyncio.run(coro)


def test_one_call_builds_an_agent_that_runs_checks_and_records():
    hub, chat = Hub(), ScriptedChatClient()

    async def go():
        async with client(hub) as c:
            agent = await build_agent("dev-core", chat, client=c)
            assert isinstance(agent, VincAgent)
            assert agent.plan.role_id == ROLE and agent.plan.person_only[0] == "merge"
            assert agent.plan.hands_to == ("concept:agent-gate-runner",)
            return await agent.run("Review the change.", run_id="run-1")

    result = run(go())
    assert isinstance(result, AgentRun)
    assert (result.status, result.outcome, result.output) == ("done", "success", PLAIN)
    assert result.cited_ids == ("decision:fixture",) and result.record is not None
    assert "decision:fixture" in chat.sent_text() and ROLE in chat.sent_text()
    props = hub.posts()[0]["nodes"][0]["props"]
    assert (props["run_id"], props["role"], props["outcome"], props["host"]) == (
        "run-1", ROLE, "success", "agent-framework")
    assert props["gate_results"]["verdict"] == "pass" and props["cited_ids"] == ["decision:fixture"]


def test_a_failing_gate_blocks_the_answer_and_the_run_is_recorded_blocked():
    hub = Hub()

    async def go():
        async with client(hub) as c:
            agent = await build_agent(
                "dev-core", ScriptedChatClient("I merged the pull request and deployed it."),
                client=c)
            return await agent.run("Ship it.", run_id="run-2")

    result = run(go())
    assert (result.status, result.output) == ("blocked", None)
    assert result.gate is not None and result.gate.verdict == "block" and result.gate.failed_checks
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "blocked"


def test_a_hand_off_reaches_the_person_with_the_decision_named_and_the_run_waits():
    hub = Hub()

    async def go():
        async with client(hub) as c:
            agent = await build_agent(
                "dev-core", ScriptedChatClient("PR #9 is green. Please merge it."), client=c)
            return await agent.run("Ship it.", run_id="run-3")

    result = run(go())
    assert result.status == "waiting" and result.gate.person_only_actions == ("merge",)
    assert result.output.startswith("PR #9 is green. Please merge it.") and "- merge:" in result.output
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "waiting"


def test_a_person_only_stop_ends_the_run_waiting_and_is_recorded():
    hub = Hub()

    class Stopping:
        async def run(self, task, **kwargs):
            raise OutputGateStopped(GateResult("interrupt", ("person_only",), ("merge",)))

    async def go():
        async with client(hub) as c:
            agent = await build_agent("dev-core", ScriptedChatClient(), client=c)
            agent.agent = Stopping()
            return await agent.run("Merge it.", run_id="run-3")

    result = run(go())
    assert (result.status, result.output, result.gate.person_only_actions) == (
        "waiting", None, ("merge",))
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "waiting"


def test_a_role_that_changed_after_it_was_read_stops_the_next_run():
    hub, chat = Hub(), ScriptedChatClient()

    async def go():
        async with client(hub) as c:
            agent = await build_agent("dev-core", chat, client=c)
            hub.hash = "b" * 64
            await agent.run("Review the change.", run_id="run-4")

    with pytest.raises(VincRoleChanged):
        run(go())
    assert chat.calls == []
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "error"


def test_record_false_writes_nothing_and_a_read_only_key_keeps_the_answer():
    quiet = Hub()

    async def quietly():
        async with client(quiet) as c:
            agent = await build_agent("dev-core", ScriptedChatClient(), client=c, record=False)
            return await agent.run("Review the change.")

    result = run(quietly())
    assert result.status == "done" and result.record is None and quiet.posts() == []
    readonly = Hub()

    async def read_only():
        async with client(readonly, RO) as c:
            agent = await build_agent("dev-core", ScriptedChatClient(), client=c)
            return await agent.run("Review the change.")

    result = run(read_only())
    assert result.output == PLAIN and result.record is None and result.record_error
    assert readonly.posts() == []
