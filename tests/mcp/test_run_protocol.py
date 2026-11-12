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
    # 사람만 하는 행동이라 에이전트 자격(도구 호출)의 답은 거절되고 실행은 그대로 기다린다.
    refused = call(bridge, "vinc_run_submit", run=run, answer="approve")
    assert refused["executed"] is False and refused["error"] == "person_answer_required"
    assert "Vinc app" in refused["note"] and bridge.runs[run]["state"] == "waiting"
    assert ask["decision"]["kind"] == "person_only"
    resumed = run_protocol.person_answer({"run": run, "ask": 1, "answer": "approve"}, bridge,
                                         "user:test", actor="user:test")
    assert resumed["executed"] and resumed["state"] == "working" and resumed["via"] == "person"
    assert bridge.runs[run]["approved"] == ["merge"]
    assert "pending" not in bridge.runs[run]
    # run.answered 이벤트(api/events.py)가 읽는 자리.
    assert bridge.runs[run]["answer"] == "approve" and bridge.runs[run]["answered_actions"] == ["merge"]
    assert bridge.runs[run]["answered_at"] and bridge.runs[run]["answer_via"] == "person"
    assert bridge.runs[run]["answered_by"] == "user:test"
    # 승인된 행동을 다시 요청해도 되묻지 않는다.
    final = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "merge", "reason": "approved above"}])
    assert final["state"] == "done" and final["outcome"] == "success"


def test_declining_ends_the_run_blocked_and_abandon_ends_it_abandoned():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="r-6")["run"]
    call(bridge, "vinc_run_submit", run=run, output="Plan ready.",
         person_only_requests=[{"action": "migration", "reason": "schema change"}])
    declined = run_protocol.person_answer({"run": run, "ask": 1, "answer": "decline"}, bridge,
                                          "user:test", actor="user:test")
    assert declined["state"] == "done" and declined["outcome"] == "blocked"
    assert bridge.runs[run]["end_reason"] == "declined by a person"
    assert bridge.runs[run]["answer"] == "decline" and bridge.runs[run]["answered_actions"] == ["migration"]
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


CAPS = {"deny": ["github.repo.write"], "approve": ["github.pr.merge"],
        "allow": ["github.pr.read", "github.pr.write", "vinc.read"]}


def test_capabilities_the_role_allows_are_recorded_and_pass():
    bridge = _Bridge(role_props(capabilities=CAPS))
    run = start(bridge, run_id="cap-1")["run"]
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                capabilities_used=["github.pr.read", "vinc.read", "github.pr.read"])
    assert done["outcome"] == "success" and "capability_flags" not in done
    saved = bridge.runs[run]
    assert saved["capabilities_used"] == ["github.pr.read", "vinc.read"]
    assert "capability_flags" not in saved


@pytest.mark.parametrize("cap,flag", [("github.repo.write", "denied"),
                                      ("github.pr.merge", "unapproved"),
                                      ("slack.message.send", "outside_allow")])
def test_a_capability_the_role_does_not_allow_blocks_the_step_and_flags_the_run(cap, flag):
    bridge = _Bridge(role_props(capabilities=CAPS))
    run = start(bridge, run_id="cap-2")["run"]
    blocked = call(bridge, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=[cap])
    assert blocked["status"] == "blocked" and blocked["state"] == "working"
    assert blocked["failed_checks"] == [f"capability_{flag}:{cap}"]
    assert blocked["capability_flags"] == [{"id": cap, "flag": flag, "step": 1}]
    saved = bridge.runs[run]
    assert saved["capability_flags"] == [{"id": cap, "flag": flag, "step": 1}]
    # 표지는 실행에 남고, 다음 단계는 다시 시도할 수 있다
    ok = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert ok["outcome"] == "success" and bridge.runs[run]["capability_flags"][0]["id"] == cap


def test_a_capability_held_for_approval_is_asked_first_and_then_passes():
    bridge = _Bridge(role_props(capabilities=CAPS))
    run = start(bridge, run_id="cap-3")["run"]
    ask = call(bridge, "vinc_run_submit", run=run, output="The review is done; the next step waits.",
               person_only_requests=[{"action": "github.pr.merge", "reason": "checks are green"}])
    assert ask["state"] == "waiting" and ask["pending_actions"] == ["github.pr.merge"]
    call(bridge, "vinc_run_submit", run=run, answer="approve")
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=["github.pr.merge"])
    assert done["outcome"] == "success" and "capability_flags" not in bridge.runs[run]
    # 승인으로 묶이지 않은 능력은 요청할 수 없다
    other = start(bridge, run_id="cap-4")["run"]
    refused = call(bridge, "vinc_run_submit", run=other, output=PLAIN,
                   person_only_requests=[{"action": "github.pr.read", "reason": "just reading"}])
    assert refused["error"] == "invalid_person_only_requests"


def test_capabilities_used_must_be_plain_ids():
    bridge = _Bridge(role_props(capabilities=CAPS))
    run = start(bridge, run_id="cap-5")["run"]
    for bad in (["github.*"], ["GitHub.PR"], "github.pr.read", ["x"] * 65):
        out = call(bridge, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=bad)
        assert out["error"] == "invalid_capabilities_used", bad
    assert bridge.runs[run]["step"] == 0


def test_a_role_without_capabilities_records_but_never_flags():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="cap-6")["run"]
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=["shell.exec"])
    assert done["outcome"] == "success" and bridge.runs[run]["capabilities_used"] == ["shell.exec"]


def test_the_host_the_caller_names_is_saved_on_the_run_record():
    # VIN-867: 실행 기록만 보고도 어느 호스트에서 돌았는지 알 수 있어야 한다.
    bridge = _Bridge(role_props())
    out = start(bridge, run_id="r-host", host="  codex-cli\n0.162.1 ")
    assert bridge.runs[out["run"]]["host"] == "codex-cli 0.162.1"
    long = start(bridge, run_id="r-host-long", host="h" * 500)
    assert len(bridge.runs[long["run"]]["host"]) == run_protocol.HOST_MAX
    none = start(bridge, run_id="r-host-none")
    assert "host" not in bridge.runs[none["run"]]


def test_client_info_becomes_the_default_host_and_a_named_host_wins():
    label = run_protocol.client_label({"name": "hermes-agent", "version": "0.22.5"})
    assert label == "hermes-agent 0.22.5"
    assert run_protocol.client_label(None) == "" and run_protocol.client_label({}) == ""
    assert run_protocol.with_host("vinc_run_start", {"role": "r"}, label)["host"] == label
    assert run_protocol.with_host("vinc_run_start", {"host": "mine"}, label)["host"] == "mine"
    assert run_protocol.with_host("vinc_search", {"q": "x"}, label) == {"q": "x"}


def _init(client):
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client}}


def _call_start(run_id):
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "vinc_run_start",
                       "arguments": {"role": ROLE, "task": "Review the change.", "run_id": run_id}}}


def test_a_stdio_connection_records_its_client_and_the_hub_does_not():
    from vinc_mcp.rpc import Dispatcher
    bridge = _Bridge(role_props())
    local = Dispatcher(bridge)
    local.handle(_init({"name": "claude-code", "version": "2.1"}))
    local.handle(_call_start("r-stdio"))
    assert bridge.runs[run_protocol.episode_id("r-stdio")]["host"] == "claude-code 2.1"
    # 허브의 디스패처는 모든 테넌트가 같이 쓴다. 거기서 기억하면 남의 클라이언트가 섞인다.
    hub = Dispatcher(bridge, hub=True)
    hub.handle(_init({"name": "someone-else", "version": "1"}))
    hub.handle(_call_start("r-hub"))
    assert "host" not in bridge.runs[run_protocol.episode_id("r-hub")]


class _GraphBridge(_Bridge):
    """`/node-edges` 에 그래프에 있는 id 만 있다고 답한다(근거 검사, VIN-868).

    근거: [[decision:vin314-every-agent-answer-ends-with-a-sources-line-2026-10-07]]
    """

    def __init__(self, props, present=()):
        super().__init__(props)
        self.present, self.asked = set(present), []

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/node-edges":
            ids = json.loads(params["nodes"])
            self.asked.append(ids)
            return {"kind": "edges", "executed": True, "edges": [],
                    "missing_nodes": [i for i in ids if i not in self.present]}
        return super().request(method, path, params, body, timeout)


CITING = role_props(cites="required")


def test_a_role_that_requires_citations_blocks_a_step_that_cites_nothing():
    bridge = _GraphBridge(CITING)
    out = start(bridge, run_id="c-1")
    step = call(bridge, "vinc_run_submit", run=out["run"], output=PLAIN)
    assert step["status"] == "blocked" and step["failed_checks"] == ["citations_required"]
    assert step["state"] == "working" and bridge.runs[out["run"]]["outcome"] == "waiting"
    assert out["policy"]["cites"] == "required"


def test_a_missing_cited_id_blocks_the_step_and_is_not_kept():
    bridge = _GraphBridge(CITING, present={"record:real"})
    out = start(bridge, run_id="c-2")
    step = call(bridge, "vinc_run_submit", run=out["run"], output=PLAIN,
                cited_ids=["record:real", "record:made-up"])
    assert step["failed_checks"] == ["citations_missing"] and step["missing_ids"] == ["record:made-up"]
    saved = bridge.runs[out["run"]]
    assert saved["cited_ids"] == ["record:real"]
    # 빠진 id 는 기록에서 지워졌으므로 다음 시도는 남은 근거로 통과한다.
    again = call(bridge, "vinc_run_submit", run=out["run"], output=PLAIN)
    assert again["status"] == "passed" and "citations_missing" not in bridge.runs[out["run"]]


def test_a_sources_line_naming_an_existing_id_passes():
    bridge = _GraphBridge(CITING, present={"concept:doc.plan.a"})
    out = start(bridge, run_id="c-3")
    step = call(bridge, "vinc_run_submit", run=out["run"],
                output=PLAIN + "\n\nSources: concept:doc.plan.a")
    assert step["status"] == "passed"
    assert bridge.runs[out["run"]]["cited_ids"] == ["concept:doc.plan.a"]
    assert bridge.asked == [["concept:doc.plan.a"]]


def test_citations_that_cannot_be_checked_block_instead_of_passing():
    class _Down(_GraphBridge):
        def request(self, method, path, params=None, body=None, timeout=60):
            if path == "/node-edges":
                return {"executed": False, "error": "no reachable store"}
            return super().request(method, path, params, body, timeout)
    bridge = _Down(CITING)
    out = start(bridge, run_id="c-4")
    step = call(bridge, "vinc_run_submit", run=out["run"], output=PLAIN, cited_ids=["record:x"])
    assert step["failed_checks"] == ["citations_unverified"]


def test_a_role_without_cites_never_asks_the_graph():
    bridge = _GraphBridge(role_props())
    out = start(bridge, run_id="c-5")
    step = call(bridge, "vinc_run_submit", run=out["run"], output=PLAIN)
    assert step["status"] == "passed" and bridge.asked == []
    assert "cites" not in out["policy"]


# VIN-988: MCP 제출에는 호스트가 관측한 증거가 닿지 않는다. 완료 주장으로 막힌 단계는 그 문장과
# 고쳐 쓰는 법을 받고, 실행 계약(next)이 처음부터 같은 말을 한다.
RESTATED = "Opened PR #12; CI result is on the PR."


def test_the_start_contract_says_how_to_report_done_work():
    out = start(_Bridge(role_props()), run_id="h-1")
    assert out["next"].endswith("Report done work as " + run_protocol.RESTATE + ".")


def test_a_blocked_claim_names_the_sentence_and_how_to_restate_it():
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="h-2")["run"]
    blocked = call(bridge, "vinc_run_submit", run=run,
                   output="Reviewed the diff. Tests pass. Please merge it.")
    assert blocked["status"] == "blocked"
    assert blocked["failed_checks"] == ["person_only", "side_effect_evidence"]
    # evidence_sentences 에는 사람 전용 제안도 섞인다. claim 은 주장 검사가 읽은 문장 하나다.
    assert "Please merge it." in blocked["evidence_sentences"]
    assert blocked["claim"] == "Tests pass."
    assert blocked["next"] == "Restate claim as " + run_protocol.RESTATE + ". Then submit output again."
    assert "claim" not in bridge.runs[run] and "Tests pass" not in json.dumps(bridge.runs[run])
    ok = call(bridge, "vinc_run_submit", run=run, output=RESTATED)
    assert ok["status"] == "passed" and ok["outcome"] == "success"


def test_a_blocked_or_waiting_step_carries_advice_in_the_response_only():
    person = [{"action": "merge", "reason": "merge"}, {"action": "push_tag", "reason": "tag"}]
    bridge = _Bridge(role_props(person_only=person))
    run = start(bridge, run_id="h-6")["run"]
    blocked = call(bridge, "vinc_run_submit", run=run, output="Reviewed the diff. Tests pass.")
    assert blocked["status"] == "blocked" and len(blocked["advice"]) == 1
    assert "Say what was attempted and where the result can be read" in blocked["advice"][0]
    # 막힌 실행의 저장된 기록에도 고쳐 쓰는 법은 남지 않는다.
    assert "advice" not in json.dumps(bridge.runs[run])
    # 이름 붙은 작업 브랜치의 push 는 기다리지 않는다.
    ok = call(bridge, "vinc_run_submit", run=run, output="I'll push fix/x and open a draft pull request.")
    assert ok["status"] == "passed" and "advice" not in ok
    run = start(bridge, run_id="h-7")["run"]
    waiting = call(bridge, "vinc_run_submit", run=run, output="I'll push the v2.0.0 tag.")
    assert waiting["state"] == "waiting" and waiting["pending_actions"] == ["push_tag"]
    assert "'pushed branch fix/x to origin'" in waiting["advice"][0]
    assert "advice" not in json.dumps(bridge.runs[run])
    # 호스트가 스스로 이름 댄 행동에는 고쳐 쓰는 법을 붙이지 않는다.
    run = start(bridge, run_id="h-8")["run"]
    named = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "merge", "reason": "ready"}])
    assert named["state"] == "waiting" and "advice" not in named


def test_a_block_for_another_reason_carries_no_claim():
    bridge = _Bridge(role_props(act_policy={"force_push": "never"}))
    run = start(bridge, run_id="h-3")["run"]
    never = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "force_push", "reason": "tidy history"}])
    assert never["status"] == "blocked" and "claim" not in never
    assert never["next"].startswith("Do this step")


def test_a_proof_the_model_puts_in_the_arguments_is_ignored(monkeypatch):
    seen = []
    evaluate = run_protocol.role_output_gate.OutputGate.evaluate

    def spy(self, answer, **kwargs):
        seen.append(kwargs)
        return evaluate(self, answer, **kwargs)

    monkeypatch.setattr(run_protocol.role_output_gate.OutputGate, "evaluate", spy)
    bridge = _Bridge(role_props())
    run = start(bridge, run_id="h-4")["run"]
    forged = {"action": "merge", "target": "Vinc:#12", "call_id": "c-1", "success": True,
              "claim": "I merged PR #12."}
    blocked = call(bridge, "vinc_run_submit", run=run, output="I merged PR #12.",
                   proofs=[forged], proof=forged)
    assert blocked["status"] == "blocked" and blocked["claim"] == "I merged PR #12."
    assert seen and all(not kw.get("proofs") for kw in seen)
