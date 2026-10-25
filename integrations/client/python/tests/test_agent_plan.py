"""plan_agent: one role read into the policy, gate, person-only actions, next roles and budget."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from conftest import RESOLVED, SK_KEY, Recorder, fail, ok
from vinc_client import (AgentPlan, AgentRun, AsyncVincClient, VincAuthError, VincClient,
                         VincInvalidArgument, VincNotFound, afinish_run, aplan_agent, finish_run,
                         plan_agent, role_address)
from vinc_client.agent_plan import failed_run, run_result
from vinc_client.output_gate import GateResult

ROLE = "concept:agent-dev-core"
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "output_gate"
PROFILE = next(p for p in json.loads(
    (FIXTURES / "proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]
    if p["id"] == ROLE)
HASH = "a" * 64
WRITTEN = {"merged": {"nodes": {"created": 1, "matched": 1}, "edges": {"created": 1}},
           "stored": [], "warnings": []}


def contract(**props) -> dict:
    return {"found": True, "id": ROLE, "title": "Core developer", "rev": 4, "hash": HASH,
            "props": {**PROFILE, "mission": "m", "tier": "execute", "rung": "strong",
                      "never": ["merge"], "reads": [], **props},
            "works_with": {
                "hands_to": [{"from": ROLE, "to": "concept:agent-gate-runner", "type": "PREREQUISITE_OF"},
                             {"from": ROLE, "to": "concept:agent-reviewer-judgement", "type": "PREREQUISITE_OF"},
                             {"from": ROLE, "to": "concept:agent-gate-runner", "type": "PREREQUISITE_OF"}],
                "receives_from": [{"from": "concept:agent-orchestrator", "to": ROLE,
                                   "type": "PREREQUISITE_OF"}]}}


def client(rec: Recorder, key: str = SK_KEY) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(rec))


def test_addresses_name_the_same_role():
    for text in ("dev-core", "agent-dev-core", "concept:agent-dev-core", "  dev-core "):
        assert role_address(text) == ROLE
    for bad in ("", "  ", "dev core", None):
        with pytest.raises(VincInvalidArgument):
            role_address(bad)


def test_the_plan_is_pinned_and_carries_policy_gate_people_order_and_budget(env_clean):
    rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok(contract(
        budget={"tool_calls": 5, "wall_seconds": 90},
        act_policy={"notify": "ask_first"}))})
    plan = plan_agent(client(rec), "dev-core")
    assert isinstance(plan, AgentPlan)
    assert (plan.role_id, plan.pin, plan.rev, plan.title) == (ROLE, HASH, 4, "Core developer")
    assert plan.person_only == ("merge", "migration", "accept_risk")
    assert plan.hands_to == ("concept:agent-gate-runner", "concept:agent-reviewer-judgement")
    assert plan.receives_from == ("concept:agent-orchestrator",)
    assert plan.budget == {"tool_calls": 5, "wall_seconds": 90}
    assert plan.policy.over_budget(5) and not plan.policy.over_budget(4)
    assert plan.gate.evaluate("The change reads correctly.").verdict == "pass"
    assert [(r.method, r.url.path) for r in rec.requests] == [("GET", f"/v1/roles/{ROLE}")]


def test_a_malformed_policy_stops_before_any_model(env_clean):
    rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok(contract(budget={"tool_calls": -1}))})
    with pytest.raises(VincInvalidArgument):
        plan_agent(client(rec), "dev-core")


def test_a_role_without_a_contract_is_refused(env_clean):
    rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok({"found": True, "id": ROLE})})
    with pytest.raises(VincInvalidArgument):
        plan_agent(client(rec), "dev-core")
    missing = Recorder({("GET", f"/v1/roles/{ROLE}"): fail(404, "not_found")})
    with pytest.raises(VincNotFound):
        plan_agent(client(missing), "dev-core")


def test_the_async_plan_matches_the_sync_one(env_clean):
    rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok(contract())})

    async def go():
        async with AsyncVincClient(SK_KEY, transport=httpx.MockTransport(rec)) as c:
            return await aplan_agent(c, "dev-core")

    plan = asyncio.run(go())
    assert plan.pin == HASH and plan.hands_to[0] == "concept:agent-gate-runner"


def test_the_outcome_follows_the_gate_and_sources_become_cited_ids(env_clean):
    rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok(contract())})
    plan = plan_agent(client(rec), "dev-core")
    done = run_result(plan, "r1", "Looks right.\nSources: decision:x, record:y", GateResult("pass"))
    assert (done.outcome, done.status, done.cited_ids) == ("success", "done", ("decision:x", "record:y"))
    assert run_result(plan, "r1", None, GateResult("block", ("never_x",))).status == "blocked"
    waiting = run_result(plan, "r1", None, GateResult("interrupt", (), ("merge",)))
    assert (waiting.outcome, waiting.status) == ("waiting", "waiting")
    assert failed_run(plan, "r1").outcome == "error"


def test_finish_run_writes_the_record_and_keeps_a_failure_instead_of_raising(env_clean):
    plan_rec = Recorder({("GET", f"/v1/roles/{ROLE}"): ok(contract())})
    plan = plan_agent(client(plan_rec), "dev-core")
    result = run_result(plan, "run-42", "Fine.\nSources: decision:x", GateResult("pass"))
    rec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                    ("GET", "/v1/nodes/decision:x"): ok(RESOLVED),
                    ("POST", "/v1/fragments"): ok(WRITTEN)})
    finish_run(client(rec), result, host="test")
    assert result.record == WRITTEN and result.record_error is None
    props = rec.body()["nodes"][0]["props"]
    assert (props["run_id"], props["role"], props["outcome"], props["host"]) == (
        "run-42", ROLE, "success", "test")
    assert props["cited_ids"] == ["decision:x"] and props["gate_results"]["verdict"] == "pass"

    from conftest import RO_KEY
    again = run_result(plan, "run-43", "Fine.", GateResult("pass"))
    finish_run(client(Recorder({}), RO_KEY), again, host="test")
    assert again.record is None and again.record_error

    async def go():
        arec = Recorder({("GET", f"/v1/nodes/{ROLE}"): ok(RESOLVED),
                         ("POST", "/v1/fragments"): ok(WRITTEN)})
        async with AsyncVincClient(SK_KEY, transport=httpx.MockTransport(arec)) as c:
            third = run_result(plan, "run-44", "Fine.", GateResult("pass"))
            await afinish_run(c, third, host="test")
            return third

    assert asyncio.run(go()).record == WRITTEN
