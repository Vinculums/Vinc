"""build_agent: a running LangGraph agent from a role address, in one call."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from vinc_client import AsyncVincClient, VincClient, VincRoleChanged
from vinc_client.agent_plan import AgentRun
from vinc_client.output_gate import GateResult, OutputGateStopped
from vinc_langgraph import VincAgent, abuild_agent, build_agent

ROLE = "concept:agent-dev-core"
SK = "vinc_sk_" + "b" * 32
RO = "vinc_ro_" + "a" * 32
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "output_gate"
PROFILE = next(p for p in json.loads(
    (FIXTURES / "proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]
    if p["id"] == ROLE)
PLAIN = "The change reads correctly; two findings follow.\nSources: decision:fixture"


class ScriptedModel(BaseChatModel):
    """Answers with ``reply`` and keeps the system text of every call."""

    reply: str = PLAIN
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def system_text(self) -> str:
        first = self.calls[-1][0]
        return getattr(first, "text", "") or str(first.content)


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


def sync_client(hub: Hub, key: str = SK) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(hub))


def test_one_call_builds_an_agent_that_runs_checks_and_records(env_clean=None):
    hub, model = Hub(), ScriptedModel()
    agent = build_agent("dev-core", model, client=sync_client(hub))
    assert isinstance(agent, VincAgent)
    assert agent.plan.role_id == ROLE and agent.plan.person_only[0] == "merge"
    assert agent.plan.hands_to == ("concept:agent-gate-runner",)
    run = agent.invoke("Review the change.", run_id="run-1")
    assert isinstance(run, AgentRun)
    assert (run.status, run.outcome, run.output) == ("done", "success", PLAIN)
    assert run.cited_ids == ("decision:fixture",) and run.record is not None
    assert "decision:fixture" in model.system_text() and ROLE in model.system_text()
    props = hub.posts()[0]["nodes"][0]["props"]
    assert (props["run_id"], props["role"], props["outcome"], props["host"]) == (
        "run-1", ROLE, "success", "langgraph")
    assert props["gate_results"]["verdict"] == "pass" and props["cited_ids"] == ["decision:fixture"]


def test_a_failing_gate_blocks_the_answer_and_the_run_is_recorded_blocked():
    hub = Hub()
    agent = build_agent("dev-core", ScriptedModel(reply="I merged the pull request and deployed it."),
                        client=sync_client(hub))
    run = agent.invoke("Ship it.", run_id="run-2")
    assert (run.status, run.output) == ("blocked", None)
    assert run.gate is not None and run.gate.verdict == "block" and run.gate.failed_checks
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "blocked"


def test_a_hand_off_reaches_the_person_with_the_decision_named_and_the_run_waits():
    hub = Hub()
    agent = build_agent("dev-core", ScriptedModel(reply="PR #9 is green. Please merge it."),
                        client=sync_client(hub))
    run = agent.invoke("Ship it.", run_id="run-3")
    assert run.status == "waiting" and run.gate.person_only_actions == ("merge",)
    assert run.output.startswith("PR #9 is green. Please merge it.") and "- merge:" in run.output
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "waiting"


def test_a_person_only_stop_ends_the_run_waiting_and_is_recorded():
    hub = Hub()
    agent = build_agent("dev-core", ScriptedModel(), client=sync_client(hub))

    class Stopping:
        def invoke(self, payload, config=None, **kwargs):
            raise OutputGateStopped(GateResult("interrupt", ("person_only",), ("merge",)))

    agent.graph = Stopping()
    run = agent.invoke("Merge it.", run_id="run-3")
    assert (run.status, run.output, run.gate.person_only_actions) == ("waiting", None, ("merge",))
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "waiting"


def test_a_role_that_changed_after_it_was_read_stops_the_next_run():
    hub, model = Hub(), ScriptedModel()
    agent = build_agent("dev-core", model, client=sync_client(hub))
    hub.hash = "b" * 64
    with pytest.raises(VincRoleChanged):
        agent.invoke("Review the change.", run_id="run-4")
    assert model.calls == []
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "error"


def test_record_false_writes_nothing_and_a_read_only_key_keeps_the_answer():
    quiet = Hub()
    run = build_agent("dev-core", ScriptedModel(), client=sync_client(quiet), record=False).invoke(
        "Review the change.")
    assert run.status == "done" and run.record is None and quiet.posts() == []
    readonly = Hub()
    run = build_agent("dev-core", ScriptedModel(), client=sync_client(readonly, RO)).invoke(
        "Review the change.")
    assert run.output == PLAIN and run.record is None and run.record_error
    assert readonly.posts() == []


def test_the_async_form_builds_runs_and_records():
    hub = Hub()

    async def go():
        async with AsyncVincClient(SK, transport=httpx.MockTransport(hub)) as client:
            agent = await abuild_agent("dev-core", ScriptedModel(), async_client=client)
            return await agent.ainvoke("Review the change.", run_id="run-5")

    run = asyncio.run(go())
    assert (run.status, run.output) == ("done", PLAIN)
    assert hub.posts()[0]["nodes"][0]["props"]["run_id"] == "run-5"


def test_a_malformed_task_is_refused_before_any_hub_call():
    hub = Hub()
    agent = build_agent("dev-core", ScriptedModel(), client=sync_client(hub))
    before = len(hub.requests)
    with pytest.raises(TypeError):
        agent.invoke(42)
    assert len(hub.requests) == before
