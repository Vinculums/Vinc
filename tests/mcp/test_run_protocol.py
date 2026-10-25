# -*- coding: utf-8 -*-
"""실행 프로토콜(vinc_run_start·vinc_run_submit): 열기, 게이트에서 막기, 사람 앞에서 대기,
답으로 재개, 예산, 멱등. 상태는 실행 기록 Episode 의 props 다 - 메모리 브리지가 그 저장을 흉내 낸다.

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import run_protocol, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
ROLE = "concept:agent-dev-core"
PLAIN = "The change reads correctly; two findings follow."


def role_props(**extra):
    props = dict(PROFILES[ROLE])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": ["merge"],
                  "reads": ["plan.a"], **extra})
    return props


class _Bridge(Bridge):
    """역할 읽기(`/roles`), 실행 읽기(`/runs`), 쓰기(`/ingest/fragment`)만 답한다."""

    def __init__(self, props):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.props, self.runs, self.writes = props, {}, 0

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Agent role",
                             "domain": "vinc/agent/roles", "props": self.props}}
        if path == "/runs":
            hit = self.runs.get(params["run"])
            return {"kind": "runs", "executed": True,
                    "runs": [{"id": params["run"], "props": json.loads(json.dumps(hit))}] if hit else []}
        if path == "/ingest/fragment":
            node = body["nodes"][0]
            assert body["edges"] == [{"from": node["id"], "to": node["props"]["role"], "type": "about"}]
            self.runs[node["id"]] = node["props"]
            self.writes += 1
            return {"executed": True, "merged": {"nodes": 1}}
        return {"executed": False, "error": "unexpected path " + path}


def call(bridge, name, **args):
    return tools.call(name, args, bridge)


def start(bridge, **extra):
    return call(bridge, "vinc_run_start", role=ROLE, task="Review the change.", **extra)


def test_start_opens_a_waiting_outcome_run_with_the_policy_and_is_idempotent():
    bridge = _Bridge(role_props(budget={"model_calls": 3}, output_kind="verdict"))
    out = start(bridge, run_id="r-1", subject="https://example.test/pr/1")
    assert out["executed"] and out["state"] == "working" and out["outcome"] == "waiting"
    assert out["run"] == run_protocol.episode_id("r-1") and re.fullmatch(r"episode:run-r-1-[0-9a-f]{8}", out["run"])
    assert out["policy"]["reads"] == ["plan.a"] and out["policy"]["output_kind"] == "verdict"
    assert out["budget_left"] == {"model_calls": 3} and out["task"] == "Review the change."
    saved = bridge.runs[out["run"]]
    assert saved["state"] == "working" and saved["subject"].endswith("/pr/1") and saved["step"] == 0
    again = start(bridge, run_id="r-1")
    assert again["executed"] and again["resumed"] is True and bridge.writes == 1


def test_start_rejects_bad_input_and_a_run_id_of_another_role():
    bridge = _Bridge(role_props())
    assert call(bridge, "vinc_run_start", role=ROLE, task=" ")["error"] == "task_required"
    assert start(bridge, run_id="bad id")["error"] == "invalid_run_id"
    start(bridge, run_id="r-2")
    clash = tools.call("vinc_run_start", {"role": "concept:agent-other", "task": "t", "run_id": "r-2"},
                       bridge)
    assert clash["executed"] is False and clash["error"] == "run_id_in_use"


def test_a_plain_output_passes_the_gates_and_ends_the_run_once():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-3")["run"]
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN, cited_ids=["record:x", "nope"],
                usage={"tool_calls": 2, "cost_usd": 0.5})
    assert done["state"] == "done" and done["outcome"] == "success" and done["status"] == "passed"
    saved = bridge.runs[run]
    assert saved["cited_ids"] == ["record:x"] and saved["used"] == {"tool_calls": 2, "cost_usd": 0.5}
    assert saved["output_ref"]["chars"] == len(PLAIN) and PLAIN not in json.dumps(saved)
    assert saved["gate_results"]["verdict"] == "pass" and saved["step"] == 1
    writes = bridge.writes
    again = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert again["state"] == "done" and bridge.writes == writes
    assert call(bridge, "vinc_run_submit", run="r-3", abandon=True)["state"] == "done"


def test_a_failing_gate_blocks_and_the_run_stays_open_for_another_try():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-4")["run"]
    blocked = call(bridge, "vinc_run_submit", run=run, output="I merged the pull request and deployed it.")
    assert blocked["status"] == "blocked" and blocked["failed_checks"] and blocked["state"] == "working"
    assert bridge.runs[run]["step"] == 1 and bridge.runs[run]["gate_results"]["verdict"] == "block"
    ok = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert ok["state"] == "done" and ok["outcome"] == "success" and ok["step"] == 2


def test_a_person_only_action_waits_for_an_answer_and_approval_resumes_the_run():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-5")["run"]
    ask = call(bridge, "vinc_run_submit", run=run, output="Ready to merge once approved.",
               person_only_requests=[{"action": "merge", "reason": "all checks are green"}])
    assert ask["state"] == "waiting" and ask["pending_actions"] == ["merge"]
    assert "merge" in ask["question"] and bridge.runs[run]["outcome"] == "waiting"
    stuck = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert stuck["executed"] is False and stuck["error"] == "invalid_run_state"
    assert stuck["run_state"]["state"] == "waiting"
    with pytest.raises(tools.InvalidArgument):
        call(bridge, "vinc_run_submit", run=run, answer="maybe")
    resumed = call(bridge, "vinc_run_submit", run=run, answer="approve")
    assert resumed["state"] == "working" and resumed["approved"] == ["merge"]
    assert "pending" not in bridge.runs[run]
    # 승인된 행동을 다시 요청해도 되묻지 않는다.
    final = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "merge", "reason": "approved above"}])
    assert final["state"] == "done" and final["outcome"] == "success"


def test_declining_ends_the_run_blocked_and_abandon_ends_it_abandoned():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-6")["run"]
    call(bridge, "vinc_run_submit", run=run, output="Plan ready.",
         person_only_requests=[{"action": "migration", "reason": "schema change"}])
    declined = call(bridge, "vinc_run_submit", run=run, answer="decline")
    assert declined["state"] == "done" and declined["outcome"] == "blocked"
    assert declined["end_reason"] == "declined by a person"
    second = start(bridge, run_id="r-7")["run"]
    assert call(bridge, "vinc_run_submit", run=second, abandon=True)["outcome"] == "abandoned"


def test_ask_first_and_never_come_from_the_act_policy():
    bridge = _Bridge(role_props(act_policy={"notify": "ask_first", "force_push": "never"}))
    run = start(bridge, run_id="r-8")["run"]
    never = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "force_push", "reason": "tidy history"}])
    assert never["status"] == "blocked" and never["failed_checks"] == ["action_never:force_push"]
    asks = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                person_only_requests=[{"action": "notify", "reason": "tell the channel"}])
    assert asks["state"] == "waiting" and asks["pending_actions"] == ["notify"]
    approved = call(bridge, "vinc_run_submit", run=run, answer="approve")
    assert approved["state"] == "working"
    bad = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
               person_only_requests=[{"action": "whatever", "reason": "x"}])
    assert bad["error"] == "invalid_person_only_requests"


def test_the_budget_ends_a_run_that_spent_it():
    bridge = _Bridge(role_props(budget={"model_calls": 1, "tool_calls": 5}))
    run = start(bridge, run_id="r-9")["run"]
    call(bridge, "vinc_run_submit", run=run, output="I merged the pull request and deployed it.")
    over = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert over["state"] == "done" and over["outcome"] == "blocked"
    assert over["end_reason"] == "budget_model_calls"
    tool_run = start(bridge, run_id="r-10")["run"]
    spent = call(bridge, "vinc_run_submit", run=tool_run, output=PLAIN, usage={"tool_calls": 9})
    assert spent["state"] == "working" or spent["end_reason"] == "budget_tool_calls"


def test_submit_needs_exactly_one_input_and_a_known_run():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-11")["run"]
    assert call(bridge, "vinc_run_submit", run=run)["error"] == "one_input_required"
    assert call(bridge, "vinc_run_submit", run=run, output=PLAIN, abandon=True)["error"] == "one_input_required"
    missing = call(bridge, "vinc_run_submit", run="never-started", output=PLAIN)
    assert missing["error"] == "run_not_found" and missing["found"] is False
    assert call(bridge, "vinc_run_submit", run=run, answer="approve")["error"] == "invalid_run_state"


@pytest.mark.parametrize("name", ["vinc_run_start", "vinc_run_submit"])
def test_both_tools_are_write_tools(name):
    ann = tools._ANNOTATIONS[name]
    assert ann["readOnlyHint"] is False and ann["destructiveHint"] is False
