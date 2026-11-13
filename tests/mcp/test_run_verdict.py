# -*- coding: utf-8 -*-
"""일이 내린 결론(`verdict`): 출력과 함께 보고되고, 검사되고, 저장되고, 실행 보기와 제목에 선다.

`outcome` 은 프로토콜이 실행을 어떻게 끝냈는가(게이트 통과·예산·만료·거절·포기)이고, `verdict` 는
에이전트가 보고한 일의 결론이다. 둘은 따로 남고, 사람이 읽는 낱말(`run_protocol.reading`)은 결론을
보고한 실행을 그냥 success 로 읽히게 하지 않는다. 상태는 실행 기록 Episode 의 props 다 - 메모리
브리지가 그 저장을 흉내 낸다. 실엔진 위의 읽는 쪽은 `test_run_verdict_readers.py` 가 본다.
"""
from __future__ import annotations

import copy
import datetime
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import cli_tools  # noqa: E402
from store import run_state  # noqa: E402
from vinc_mcp import rest_routes, run_protocol, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
MAKER = "concept:agent-maker"
CHECKER = "concept:agent-checker"
PLAIN = "The change reads correctly; two findings follow."
FAIL = "Rows 2 and 4 do not hold; the result is in the review notes of the pull request."
T0 = datetime.datetime(2026, 1, 5, 9, 0, 0, tzinfo=datetime.timezone.utc)


def _role(**extra) -> dict:
    props = dict(PROFILES["concept:agent-dev-core"])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [], "reads": [],
                  **extra})
    return props


ROLES = {
    MAKER: _role(budget={"wall_seconds": 600}),
    CHECKER: _role(tier="verify", budget={"wall_seconds": 600}, person_only=[
        {"action": "override_failed_check", "reason": "overriding a failed check"}]),
    # 검사 역할은 tier 가 아니라 output_kind 로도 정해진다.
    "concept:agent-judge": _role(output_kind="verdict"),
}


class _Bridge(Bridge):
    """역할 읽기(`/roles`), 실행 읽기(`/runs`), 쓰기(`/ingest/fragment`)만 답한다."""

    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.runs, self.titles, self.writes = {}, {}, 0

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Agent role",
                             "domain": "vinc/agent/roles", "props": ROLES[params["role"]]}}
        if path == "/runs":
            hit = self.runs.get(params["run"])
            return {"kind": "runs", "executed": True,
                    "runs": [{"id": params["run"], "props": copy.deepcopy(hit)}] if hit else []}
        if path == "/ingest/fragment":
            node = body["nodes"][0]
            self.runs[node["id"]] = copy.deepcopy(node["props"])
            self.titles[node["id"]] = node["title"]
            self.writes += 1
            return {"executed": True, "merged": {"nodes": 1}}
        return {"executed": False, "error": "unexpected path " + path}


@pytest.fixture()
def clock(monkeypatch):
    moment = {"now": T0}
    monkeypatch.setattr(run_protocol, "_now", lambda: moment["now"])
    return moment


def call(bridge, name, **args):
    return tools.call(name, args, bridge)


def start(bridge, role, run_id):
    out = call(bridge, "vinc_run_start", role=role, task="Check the package.", run_id=run_id)
    assert out["executed"], out
    return out["run"]


def submit(bridge, run, **args):
    """도구 스키마를 거치지 않고 프로토콜에 바로 낸다(스키마가 막지 않는 값을 보려고)."""
    return run_protocol.submit({"run": run, **args}, bridge, "user:test")


# ── 저장과 보기 ──────────────────────────────────────────────────────────────

def test_a_checker_that_reports_fail_is_stored_and_titled_with_its_verdict(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "chk-fail-1")
    done = call(bridge, "vinc_run_submit", run=run, output=FAIL, verdict="fail",
                verdict_note="rows 2 and 4")
    # 게이트는 출력의 말씨만 본다: 그래서 outcome 은 success 지만 결론은 fail 로 따로 남는다.
    assert (done["state"], done["outcome"], done["status"]) == ("done", "success", "passed")
    assert (done["verdict"], done["verdict_note"]) == ("fail", "rows 2 and 4")
    saved = bridge.runs[run]
    assert (saved["verdict"], saved["verdict_note"]) == ("fail", "rows 2 and 4")
    assert saved["gate_results"]["verdict"] == "pass" and saved["end_reason"] == "gates passed"
    assert bridge.titles[run] == "Run chk-fail-1 of agent-checker: verdict fail"
    assert "success" not in bridge.titles[run]
    shown = call(bridge, "vinc_run_start", role=CHECKER, task="again", run_id="chk-fail-1")
    assert shown["resumed"] and shown["verdict"] == "fail"


def test_a_maker_that_reports_blocked_reads_as_blocked_with_what_blocked_it(clock):
    bridge = _Bridge()
    run = start(bridge, MAKER, "mk-blocked-1")
    done = call(bridge, "vinc_run_submit", run=run,
                output="Could not run any command in this session; nothing was changed.",
                verdict="blocked", verdict_note="  every shell command   was refused ")
    assert done["outcome"] == "success" and done["verdict"] == "blocked"
    assert bridge.runs[run]["verdict_note"] == "every shell command was refused"
    assert bridge.titles[run] == "Run mk-blocked-1 of agent-maker: verdict blocked"


@pytest.mark.parametrize("verdict", ["delivered", "partial"])
def test_a_maker_reports_delivered_or_partial(clock, verdict):
    bridge = _Bridge()
    run = start(bridge, MAKER, f"mk-{verdict}")
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN, verdict=verdict)
    assert done["verdict"] == verdict and "verdict_note" not in done
    assert bridge.titles[run].endswith(f": verdict {verdict}")


def test_a_run_that_reports_nothing_keeps_the_title_and_view_it_had(clock):
    bridge = _Bridge()
    run = start(bridge, MAKER, "plain-1")
    done = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert "verdict" not in done and "verdict" not in bridge.runs[run]
    assert bridge.titles[run] == "Run plain-1 of agent-maker: success"


def test_the_verdict_is_kept_from_a_blocked_step_and_replaced_by_a_later_one(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "chk-steps-1")
    blocked = call(bridge, "vinc_run_submit", run=run, verdict="fail", verdict_note="row 2",
                   output="I merged the pull request and deployed it.")
    # 게이트가 막은 단계도 세어지므로 결론이 남는다. 제목은 열린 실행의 낱말 뒤에 결론을 붙인다.
    assert blocked["status"] == "blocked" and blocked["verdict"] == "fail"
    assert bridge.titles[run] == "Run chk-steps-1 of agent-checker: working, verdict fail"
    kept = call(bridge, "vinc_run_submit", run=run, output=FAIL)
    assert kept["outcome"] == "success" and kept["verdict"] == "fail"
    other = start(bridge, CHECKER, "chk-steps-2")
    call(bridge, "vinc_run_submit", run=other, verdict="fail", verdict_note="row 2",
         output="I merged the pull request and deployed it.")
    replaced = call(bridge, "vinc_run_submit", run=other, output=PLAIN, verdict="pass")
    # 새 결론은 앞 결론의 한 줄을 데려가지 않는다.
    assert replaced["verdict"] == "pass" and "verdict_note" not in bridge.runs[other]


def test_a_declined_run_says_both_the_decline_and_the_verdict(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "chk-decline-1")
    waiting = call(bridge, "vinc_run_submit", run=run, output=FAIL, verdict="fail",
                   verdict_note="row 2",
                   person_only_requests=[{"action": "override_failed_check", "reason": "row 2"}])
    assert waiting["state"] == "waiting" and waiting["verdict"] == "fail"
    assert bridge.titles[run] == "Run chk-decline-1 of agent-checker: waiting, verdict fail"
    out = run_protocol.person_answer({"run": run, "ask": 1, "answer": "decline"}, bridge,
                                     "user:test", actor="user:test")
    assert (out["state"], out["outcome"]) == ("done", "blocked")
    assert bridge.runs[run]["end_reason"] == "declined by a person"
    assert bridge.titles[run] == "Run chk-decline-1 of agent-checker: blocked, verdict fail"


def test_output_kind_verdict_makes_a_role_a_checking_role(clock):
    bridge = _Bridge()
    run = start(bridge, "concept:agent-judge", "judge-1")
    assert call(bridge, "vinc_run_submit", run=run, output=PLAIN, verdict="pass")["verdict"] == "pass"
    assert run_protocol.verdicts_for(ROLES["concept:agent-judge"]) == ("pass", "fail", "blocked")
    assert run_protocol.verdicts_for(ROLES[MAKER]) == ("delivered", "partial", "blocked")


# ── 예산의 순서 ───────────────────────────────────────────────────────────────

def test_the_budget_stops_the_run_after_the_verdict_is_stored_and_before_the_gates(clock,
                                                                                  monkeypatch):
    seen = []
    evaluate = run_protocol.role_output_gate.OutputGate.evaluate

    def spy(self, answer, **kwargs):
        seen.append(answer)
        return evaluate(self, answer, **kwargs)

    monkeypatch.setattr(run_protocol.role_output_gate.OutputGate, "evaluate", spy)
    bridge = _Bridge()
    run = start(bridge, CHECKER, "chk-late-1")
    clock["now"] = T0 + datetime.timedelta(seconds=601)
    late = call(bridge, "vinc_run_submit", run=run, output=FAIL, verdict="fail",
                verdict_note="rows 2 and 4")
    # 순서: 결론을 둔다 → 예산이 실행을 끝낸다 → 게이트는 돌지 않는다(단계도 세지 않는다).
    assert seen == []
    assert (late["state"], late["outcome"], late["end_reason"]) == (
        "done", "blocked", "budget_wall_seconds")
    assert (late["verdict"], late["verdict_note"]) == ("fail", "rows 2 and 4")
    saved = bridge.runs[run]
    assert saved["verdict"] == "fail" and saved["end_reason"] == "budget_wall_seconds"
    assert saved["step"] == 0 and "gate_results" not in saved
    assert bridge.titles[run] == "Run chk-late-1 of agent-checker: blocked, verdict fail"


def test_an_over_budget_step_without_a_usable_output_records_no_verdict(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "chk-late-2")
    clock["now"] = T0 + datetime.timedelta(seconds=601)
    late = submit(bridge, run, output="x" * (run_protocol.OUTPUT_MAX + 1), verdict="fail")
    assert late["end_reason"] == "budget_wall_seconds" and "verdict" not in late
    assert "verdict" not in bridge.runs[run]


def test_an_over_budget_step_without_a_verdict_ends_as_on_main(clock):
    bridge = _Bridge()
    run = start(bridge, MAKER, "mk-late-1")
    clock["now"] = T0 + datetime.timedelta(seconds=601)
    late = call(bridge, "vinc_run_submit", run=run, output=PLAIN)
    assert (late["outcome"], late["end_reason"]) == ("blocked", "budget_wall_seconds")
    assert "verdict" not in late and bridge.titles[run] == "Run mk-late-1 of agent-maker: blocked"


# ── 거절: 프로토콜 오류, 아무것도 저장되지 않는다 ───────────────────────────────

@pytest.mark.parametrize("role,args", [
    (CHECKER, {"verdict": "great"}),
    (CHECKER, {"verdict": "delivered"}),
    (MAKER, {"verdict": "pass"}),
    (MAKER, {"verdict": "FAIL"}),
    (CHECKER, {"verdict": ["fail"]}),
    (CHECKER, {"verdict": "blocked"}),
    (CHECKER, {"verdict": "blocked", "verdict_note": "   "}),
    (CHECKER, {"verdict": "fail", "verdict_note": "x" * (run_state.VERDICT_NOTE_MAX + 1)}),
    (CHECKER, {"verdict": "fail", "verdict_note": 7}),
    (CHECKER, {"verdict_note": "row 2"}),
])
def test_an_invalid_verdict_is_refused_and_nothing_is_stored(clock, role, args):
    bridge = _Bridge()
    run = start(bridge, role, "bad-1")
    before, writes = copy.deepcopy(bridge.runs[run]), bridge.writes
    out = submit(bridge, run, output=FAIL, **args)
    assert out["executed"] is False and out["error"] == "invalid_verdict", out
    assert out["note"].startswith("Invalid: ")
    assert bridge.writes == writes and bridge.runs[run] == before


def test_the_refusal_names_the_words_this_role_may_use(clock):
    bridge = _Bridge()
    out = submit(bridge, start(bridge, CHECKER, "bad-2"), output=FAIL, verdict="delivered")
    assert out["note"] == "Invalid: verdict for this role is one of pass, fail, blocked."


@pytest.mark.parametrize("mode", [{"answer": "approve"}, {"abandon": True}])
def test_a_verdict_goes_with_output_only(clock, mode):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "bad-3")
    before, writes = copy.deepcopy(bridge.runs[run]), bridge.writes
    out = submit(bridge, run, verdict="fail", **mode)
    assert out["error"] == "invalid_verdict" and "goes with output" in out["note"]
    assert bridge.writes == writes and bridge.runs[run] == before


def test_an_invalid_verdict_over_budget_is_refused_and_the_run_stays_open(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "bad-4")
    clock["now"] = T0 + datetime.timedelta(seconds=601)
    before, writes = copy.deepcopy(bridge.runs[run]), bridge.writes
    out = submit(bridge, run, output=FAIL, verdict="great")
    assert out["error"] == "invalid_verdict"
    assert bridge.writes == writes and bridge.runs[run] == before


def test_the_tool_schema_refuses_a_word_outside_the_vocabulary_before_the_protocol(clock):
    bridge = _Bridge()
    run = start(bridge, CHECKER, "bad-5")
    before, writes = copy.deepcopy(bridge.runs[run]), bridge.writes
    with pytest.raises(tools.InvalidArgument) as refused:
        call(bridge, "vinc_run_submit", run=run, output=FAIL, verdict="great")
    assert refused.value.meta == {"tool": "vinc_run_submit", "argument": "verdict",
                                  "reason": "must be one of: " + ", ".join(run_state.VERDICTS)}
    assert bridge.writes == writes and bridge.runs[run] == before


# ── 사람이 읽는 낱말 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("props,said", [
    ({"state": "done", "outcome": "success"}, "success"),
    ({"state": "done", "outcome": "success", "verdict": "fail"}, "verdict fail"),
    ({"state": "done", "outcome": "success", "verdict": "pass"}, "verdict pass"),
    ({"state": "done", "outcome": "success", "verdict": "blocked"}, "verdict blocked"),
    ({"state": "done", "outcome": "blocked", "verdict": "fail"}, "blocked, verdict fail"),
    ({"state": "done", "outcome": "abandoned", "verdict": "partial"}, "abandoned, verdict partial"),
    ({"state": "waiting", "outcome": "waiting", "verdict": "fail"}, "waiting, verdict fail"),
    ({"state": "working", "outcome": "waiting"}, "working"),
    ({"state": "done", "outcome": "success", "verdict": "great"}, "success"),
    ({"state": "done", "outcome": "success", "verdict": 3}, "success"),
])
def test_reading_never_says_plain_success_for_a_reported_verdict(props, said):
    assert run_protocol.reading(props) == said


def test_readers_keep_only_known_verdict_words_and_a_bounded_note():
    read = run_state.readable({"verdict": "great", "verdict_note": ["x"]})
    assert "verdict" not in read and "verdict_note" not in read
    long = run_state.readable({"verdict": "fail", "verdict_note": "y" * 1000})
    assert long["verdict"] == "fail" and len(long["verdict_note"]) == run_state.VERDICT_NOTE_MAX
    # 결론은 프로토콜 상태가 아니다: 도구 쓰기가 막지 않고 동기화가 덮어쓰지 않는다.
    assert "verdict" not in run_state.STATE_KEYS and "verdict_note" not in run_state.STATE_KEYS
    assert run_state.writes([{"id": "episode:run-x", "props_patch": {"verdict": "fail"}}]) == []


# ── 도구 문장과 다른 입구 ───────────────────────────────────────────────────

def _served(name: str) -> dict:
    return next(t for t in tools.specs(hub=True) if t["name"] == name)


def test_the_served_text_says_how_to_report_the_verdict_in_one_short_sentence():
    spec = _served("vinc_run_submit")
    props = spec["inputSchema"]["properties"]
    said = props["verdict"]["description"]
    assert props["verdict"]["enum"] == list(run_state.VERDICTS)
    assert said.count(". ") == 0 and said.endswith(".") and len(said) <= 200
    assert "description" not in props["verdict_note"]
    assert props["verdict_note"]["maxLength"] == run_state.VERDICT_NOTE_MAX
    # 요약(호스트가 매번 받는 설명)은 늘지 않는다.
    assert spec["description"].startswith(tools._SUMMARY["vinc_run_submit"])
    assert "verdict" not in tools._SUMMARY["vinc_run_submit"]


def test_rest_and_the_cli_carry_the_verdict_to_the_same_tool():
    route = next(r for r in rest_routes.WRITE_ROUTES if r.tool == "vinc_run_submit")
    assert {"verdict", "verdict_note"} <= set(route.body)
    args = SimpleNamespace(cmd="run", runcmd="submit", owner=None, run="r-1", output=None,
                           answer=None, abandon=False, cited=None, person_only_requests=None,
                           usage=None, verdict="blocked", verdict_note="no shell")
    name, data, _ = cli_tools._tool_request(args)
    assert name == "vinc_run_submit"
    assert data == {"run": "r-1", "verdict": "blocked", "verdict_note": "no shell"}
