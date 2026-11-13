# -*- coding: utf-8 -*-
"""실행이 돌린 호출의 기록(`actions_run`·`host_record`)으로 사람 전용 행동을 묻는다.

기록이 있으면 병합·태그 push·게시·발송·마이그레이션은 기록에서 묻고, 글은 참고(notes)만 된다. 기록이 없는
제출(옛 호스트)은 예전처럼 글에서 읽는다. 호스트 기록은 그래프에 등록된 관찰자 키의 서명이 맞을 때만 받는다.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import edverify, role_output_gate, run_protocol, tools  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_protocol import PLAIN, _Bridge, call, role_props, start  # noqa: E402

PERSON = [{"action": a, "reason": a} for a in ("merge", "migration", "push_tag", "publish", "send")]
SEED = bytes(range(32))


def bridge():
    return _Bridge(role_props(person_only=PERSON))


def signed(eid, entries, **extra):
    payload = {"typ": run_protocol.HOST_RECORD_TYP, "aud": run_protocol.HOST_RECORD_AUD, "run": eid,
               "digest": run_protocol.record_digest(entries), "iat": int(time.time()), **extra}
    return {"entries": entries, "attestation": edverify.sign_assertion(SEED, payload)}


@pytest.fixture
def observer_key(monkeypatch):
    public = edverify.b64u_encode(edverify.secret_to_public(SEED))
    monkeypatch.setenv(run_protocol.OBSERVER_KEYS_ENV, json.dumps({"user:test": [public]}))


def test_a_recorded_merge_makes_the_run_wait_and_the_question_shows_the_command():
    b = bridge()
    run = start(b, run_id="rec-1")["run"]
    out = call(b, "vinc_run_submit", run=run, output="Opened draft PR #12; the result is in its checks.",
               actions_run=[{"command": "gh pr merge 12 --squash", "exit_status": 0}])
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert out["decision"]["kind"] == "person_only"
    reason = out["decision"]["requests"][0]["reason"]
    assert reason.startswith("ran: gh pr merge 12 --squash") and "stated by the agent" in reason
    saved = b.runs[run]
    assert saved["actions_run"] == [{"step": 1, "kinds": ["vcs.merge"], "observed_by": "agent", "exit_status": 0}]
    # 고쳐 쓰는 법은 기록된 호출에 붙지 않는다(돌린 호출은 고쳐 쓸 문장이 아니다).
    assert not any(line.startswith("person_only (merge)") for line in out.get("advice", []))


def test_the_word_merge_with_a_record_that_has_no_merge_passes_with_a_note():
    b = bridge()
    run = start(b, run_id="rec-2")["run"]
    out = call(b, "vinc_run_submit", run=run, output="The PR is ready for the owner to merge.",
               actions_run=[{"command": "git push -u origin fix/merge-base-check", "exit_status": 0},
                            {"command": "git merge-base --is-ancestor origin/main HEAD"}])
    assert out["state"] == "done" and out["outcome"] == "success"
    assert out["notes"] and out["notes"][0].startswith("person_only (merge): the text names it")
    assert b.runs[run]["actions_run"] == [{"step": 1, "kinds": ["vcs.push"], "observed_by": "agent",
                                           "exit_status": 0}]
    assert "notes" not in b.runs[run]


def test_a_host_without_a_record_is_read_from_the_text_as_before():
    b = bridge()
    run = start(b, run_id="rec-3")["run"]
    out = call(b, "vinc_run_submit", run=run, output="The PR is ready for the owner to merge.")
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert "actions_run" not in b.runs[run] and "notes" not in out


def test_an_empty_record_is_a_record():
    b = bridge()
    run = start(b, run_id="rec-4")["run"]
    out = call(b, "vinc_run_submit", run=run, output="Please merge #12 when you are ready.", actions_run=[])
    assert out["outcome"] == "success" and out["notes"]


def test_an_approved_merge_is_not_asked_again_when_the_record_shows_it():
    b = bridge()
    run = start(b, run_id="rec-5")["run"]
    call(b, "vinc_run_submit", run=run, output="Asking first.",
         person_only_requests=[{"action": "merge", "reason": "checks are green"}])
    run_protocol.person_answer({"run": run, "ask": 1, "answer": "approve"}, b, "user:test", actor="user:test")
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, actions_run=[{"command": "gh pr merge 12"}])
    assert out["outcome"] == "success"


def test_a_record_cannot_hide_a_tag_push_behind_a_stated_kind():
    b = bridge()
    run = start(b, run_id="rec-6")["run"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN,
               actions_run=[{"command": "git push origin v2.0.0", "kind": "vcs.push"}])
    assert out["pending_actions"] == ["push_tag"]


def test_a_recorded_action_on_a_step_blocked_before_the_gate_is_asked_on_the_next_step():
    b = _Bridge(role_props(person_only=PERSON, capabilities={"deny": ["github.repo.write"]}))
    run = start(b, run_id="rec-7")["run"]
    blocked = call(b, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=["github.repo.write"],
                   actions_run=[{"command": "gh pr merge 12"}])
    assert blocked["status"] == "blocked" and b.runs[run]["record_pending"] == ["merge"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN)
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert "record_pending" not in b.runs[run]


@pytest.mark.parametrize("bad", [
    [{"command": "gh pr merge 1", "observed_by": "host"}],
    [{}], ["gh pr merge 1"], [{"command": 3}], [{"command": "x", "exit_status": True}],
    [{"command": "x", "kind": "vcs.teleport"}], [{"command": "x"}] * 65,
])
def test_a_malformed_record_changes_nothing(bad):
    b = bridge()
    run = start(b, run_id="rec-8")["run"]
    before = json.dumps(b.runs[run], sort_keys=True)
    try:
        out = call(b, "vinc_run_submit", run=run, output=PLAIN, actions_run=bad)
    except tools.InvalidArgument:
        out = {"executed": False}
    assert out["executed"] is False
    assert json.dumps(b.runs[run], sort_keys=True) == before


def test_a_record_goes_with_output_only():
    b = bridge()
    run = start(b, run_id="rec-9")["run"]
    out = call(b, "vinc_run_submit", run=run, abandon=True, actions_run=[{"command": "gh pr merge 1"}])
    assert out["executed"] is False and out["error"] == "invalid_actions_run"


def test_a_signed_host_record_is_kept_as_host_observed(observer_key):
    b = bridge()
    run = start(b, run_id="rec-10")["run"]
    entries = [{"command": "git push origin main", "exit_status": 0}]
    out = call(b, "vinc_run_submit", run=run, output="Pushed the branch.", host_record=signed(run, entries),
               actions_run=[{"command": "git push -u origin fix/x"}])
    assert set(out["pending_actions"]) == {"merge", "push_tag"}
    assert "observed by the host" in out["decision"]["requests"][0]["reason"]
    assert [r["observed_by"] for r in b.runs[run]["actions_run"]] == ["host", "agent"]


@pytest.mark.parametrize("tamper", ["entries", "run", "typ", "key"])
def test_a_host_record_that_does_not_verify_is_refused(observer_key, monkeypatch, tamper):
    b = bridge()
    run = start(b, run_id="rec-11")["run"]
    entries = [{"command": "gh pr merge 3"}]
    record = signed(run, entries, **({"typ": "proxy"} if tamper == "typ" else {}))
    if tamper == "entries":
        record["entries"] = [{"command": "git push -u origin fix/x"}]
    if tamper == "run":
        record = signed(run_protocol.episode_id("other"), entries)
    if tamper == "key":
        monkeypatch.setenv(run_protocol.OBSERVER_KEYS_ENV, json.dumps({"user:test": [
            edverify.b64u_encode(edverify.secret_to_public(bytes(32)))]}))
    before = json.dumps(b.runs[run], sort_keys=True)
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=record)
    assert out["executed"] is False and out["error"] == "invalid_host_record"
    assert json.dumps(b.runs[run], sort_keys=True) == before


def test_a_host_record_without_a_registered_key_is_refused(monkeypatch):
    monkeypatch.delenv(run_protocol.OBSERVER_KEYS_ENV, raising=False)
    b = bridge()
    run = start(b, run_id="rec-12")["run"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=signed(run, [{"command": "ls"}]))
    assert out["executed"] is False and "no host observer key" in out["note"]


def test_the_record_keys_are_protocol_state():
    assert {"actions_run", "record_pending"} <= set(run_protocol.STATE_KEYS)


def test_the_tool_schema_lists_the_gate_kinds():
    schema = next(t for t in tools.TOOLS if t["name"] == "vinc_run_submit")["inputSchema"]["properties"]
    kinds = schema["actions_run"]["items"]["properties"]["kind"]["enum"]
    assert tuple(kinds) == role_output_gate.RECORD_KINDS
    assert set(schema["actions_run"]["items"]["properties"]) == set(run_protocol.ACTION_KEYS)
    assert schema["host_record"]["properties"]["entries"]["items"] == schema["actions_run"]["items"]
