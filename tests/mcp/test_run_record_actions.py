# -*- coding: utf-8 -*-
"""실행이 돌린 호출의 기록(`actions_run`·`host_record`)으로 사람 전용 행동을 묻는다.

에이전트가 적은 기록은 질문을 더할 뿐이고 글 읽기는 그대로다. 호스트 기록(그래프에 등록된 관찰자 키로 이
실행·이 단계에 서명된 일회용 기록)이 있는 단계만 병합·태그 push·게시·발송·마이그레이션을 기록이 정하고, 글은
실행에 남는 참고(record_notes)가 된다. 기록이 없는 제출(옛 호스트)은 예전처럼 글에서 읽는다.
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


def signed(eid, entries, *, step=1, jti=None, **extra):
    payload = {"typ": run_protocol.HOST_RECORD_TYP, "aud": run_protocol.HOST_RECORD_AUD, "run": eid,
               "step": step, "jti": jti or f"rec-{eid[-8:]}-{step}-{time.time_ns()}",
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


TEXT = "Please merge PR #12 now, and tag v1.2.0 after; I will publish to PyPI."


@pytest.mark.parametrize("record", [[], [{"command": "ls"}], [{"command": "git push -u origin fix/x"}]])
def test_an_agent_record_cannot_turn_the_text_check_off(record):
    """에이전트가 적은 기록(빈 것, 관계없는 것)은 질문을 더할 뿐이다: 글이 낸 질문은 main 과 같다."""
    b = bridge()
    run = start(b, run_id="rec-2")["run"]
    out = call(b, "vinc_run_submit", run=run, output=TEXT, actions_run=record)
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge", "publish", "push_tag"]
    assert "notes" not in out and "record_notes" not in b.runs[run]
    b = bridge()
    run = start(b, run_id="rec-2b")["run"]
    assert call(b, "vinc_run_submit", run=run, output=TEXT)["pending_actions"] == ["merge", "publish", "push_tag"]


def test_a_host_record_without_the_call_turns_the_text_into_a_note_kept_on_the_run(observer_key):
    b = bridge()
    run = start(b, run_id="rec-3")["run"]
    entries = [{"command": "git push -u origin fix/merge-base-check", "exit_status": 0},
               {"command": "git merge-base --is-ancestor origin/main HEAD"}]
    out = call(b, "vinc_run_submit", run=run, output="The PR is ready for the owner to merge.",
               host_record=signed(run, entries))
    assert out["state"] == "done" and out["outcome"] == "success"
    assert out["notes"] and out["notes"][0].startswith("person_only (merge): the text names it")
    saved = b.runs[run]
    assert saved["record_notes"] == [{"step": 1, "note": out["notes"][0]}]
    assert [r["observed_by"] for r in saved["actions_run"]] == ["host"]


def test_a_host_without_a_record_is_read_from_the_text_as_before():
    b = bridge()
    run = start(b, run_id="rec-4")["run"]
    out = call(b, "vinc_run_submit", run=run, output="The PR is ready for the owner to merge.")
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert "actions_run" not in b.runs[run] and "notes" not in out


def test_an_approved_request_does_not_cover_the_call_recorded_later():
    b = bridge()
    run = start(b, run_id="rec-5")["run"]
    call(b, "vinc_run_submit", run=run, output="Asking first.",
         person_only_requests=[{"action": "merge", "reason": "checks are green"}])
    run_protocol.person_answer({"run": run, "ask": 1, "answer": "approve"}, b, "user:test", actor="user:test")
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, actions_run=[{"command": "gh pr merge 12"}])
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert out["decision"]["requests"][0]["reason"].startswith("ran: gh pr merge 12")


def test_an_approval_covers_only_the_call_it_answered():
    """사람이 본 질문의 호출(merge 12)을 승인해도, 다음 단계의 다른 병합(merge 99 --admin)은 다시 묻는다."""
    b = bridge()
    run = start(b, run_id="rec-5b")["run"]
    first = call(b, "vinc_run_submit", run=run, output="Partial; the result is in PR #12.",
                 actions_run=[{"command": "gh pr merge 12 --squash", "exit_status": 0}])
    assert first["pending_actions"] == ["merge"]
    run_protocol.person_answer({"run": run, "ask": 1, "answer": "approve"}, b, "user:test", actor="user:test")
    for ask, command in ((2, "gh pr merge 99 --admin"), (3, "gh pr merge 12 --squash")):
        out = call(b, "vinc_run_submit", run=run, output="Next; the result is in PR #99.",
                   actions_run=[{"command": command, "exit_status": 0}])
        assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
        assert command in out["decision"]["requests"][0]["reason"]
        run_protocol.person_answer({"run": run, "ask": ask, "answer": "approve"}, b, "user:test",
                                   actor="user:test")
    assert call(b, "vinc_run_submit", run=run, output=PLAIN, actions_run=[])["outcome"] == "success"


def test_a_record_cannot_hide_a_tag_push_behind_a_stated_kind():
    b = bridge()
    run = start(b, run_id="rec-6")["run"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN,
               actions_run=[{"command": "git push origin v2.0.0", "kind": "vcs.push"}])
    assert out["pending_actions"] == ["push_tag"]


def test_an_agent_target_cannot_lower_a_push():
    b = bridge()
    run = start(b, run_id="rec-6b")["run"]
    out = call(b, "vinc_run_submit", run=run, output="Pushed; the result is in the branch.",
               actions_run=[{"command": "git push origin HEAD", "target": "fix/x"}])
    assert out["state"] == "waiting" and set(out["pending_actions"]) == {"merge", "push_tag"}


@pytest.mark.parametrize("command", ["bash -lc 'gh pr merge 12 --squash'", "sudo -u bob gh pr merge 12",
                                     "env -u FOO gh pr merge 12", "timeout -s KILL 30 gh pr merge 12",
                                     "curl -X PUT https://api.github.com/repos/o/r/pulls/12/merge"])
def test_a_wrapped_merge_is_asked(command):
    b = bridge()
    run = start(b, run_id="rec-6c")["run"]
    out = call(b, "vinc_run_submit", run=run, output="Done; see PR #12.",
               actions_run=[{"command": command, "exit_status": 0}])
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]


def test_a_secret_in_the_command_is_masked_before_it_is_stored():
    b = bridge()
    run = start(b, run_id="rec-6d")["run"]
    call(b, "vinc_run_submit", run=run, output="Reviewed.",
         actions_run=[{"command": "GH_TOKEN=ghp_SECRETSECRETSECRET123 gh pr merge 12"}])
    stored = json.dumps(b.runs[run])
    assert "ghp_SECRET" not in stored and "GH_TOKEN=***" in b.runs[run]["pending"]["requests"][0]["reason"]


def test_a_recorded_action_on_a_step_blocked_before_the_gate_is_asked_on_the_next_step():
    b = _Bridge(role_props(person_only=PERSON, capabilities={"deny": ["github.repo.write"]}))
    run = start(b, run_id="rec-7")["run"]
    blocked = call(b, "vinc_run_submit", run=run, output=PLAIN, capabilities_used=["github.repo.write"],
                   actions_run=[{"command": "gh pr merge 12"}])
    assert blocked["status"] == "blocked"
    assert [p["action"] for p in b.runs[run]["record_pending"]] == ["merge"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN)
    assert out["state"] == "waiting" and out["pending_actions"] == ["merge"]
    assert out["decision"]["requests"][0]["reason"].startswith("ran: gh pr merge 12")
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
    out = call(b, "vinc_run_submit", run=run, output="Pushed the branch.", host_record=signed(run, entries, jti="j-rec-10-a"),
               actions_run=[{"command": "git push -u origin fix/x"}])
    assert set(out["pending_actions"]) == {"merge", "push_tag"}
    assert "observed by the host" in out["decision"]["requests"][0]["reason"]
    assert [r["observed_by"] for r in b.runs[run]["actions_run"]] == ["host", "agent"]
    assert b.runs[run]["host_record_ids"] == ["j-rec-10-a"]


@pytest.mark.parametrize("tamper", ["entries", "run", "typ", "key", "step", "no-step", "no-jti", "future",
                                    "nan", "stale"])
def test_a_host_record_that_does_not_verify_is_refused(observer_key, monkeypatch, tamper):
    b = bridge()
    run = start(b, run_id="rec-11")["run"]
    entries = [{"command": "gh pr merge 3"}]
    extra = {"typ": {"typ": "proxy"}, "step": {"step": 2}, "no-step": {"step": None},
             "future": {"iat": int(time.time()) + 10 * 365 * 86400}, "nan": {"iat": float("nan")},
             "stale": {"iat": int(time.time()) - 1000}}.get(tamper, {})
    record = signed(run, entries, **extra)
    if tamper == "entries":
        record["entries"] = [{"command": "git push -u origin fix/x"}]
    if tamper == "run":
        record = signed(run_protocol.episode_id("other"), entries)
    if tamper == "no-jti":
        record = signed(run, entries, jti="short")
    if tamper == "key":
        monkeypatch.setenv(run_protocol.OBSERVER_KEYS_ENV, json.dumps({"user:test": [
            edverify.b64u_encode(edverify.secret_to_public(bytes(32)))]}))
    before = json.dumps(b.runs[run], sort_keys=True)
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=record)
    assert out["executed"] is False and out["error"] == "invalid_host_record"
    assert json.dumps(b.runs[run], sort_keys=True) == before


def test_a_host_record_is_used_once_and_only_for_its_step(observer_key):
    """서명은 실행과 단계에 묶이고 jti 는 한 번만 받는다: 1단계의 기록을 2단계에 다시 낼 수 없다."""
    b = bridge()
    run = start(b, run_id="rec-11b")["run"]
    benign = signed(run, [{"command": "ls", "exit_status": 0}], jti="j-rec-11b-one")
    # 1단계는 완료 주장으로 막혀 실행이 열린 채 남는다.
    first = call(b, "vinc_run_submit", run=run, output="I merged it.", host_record=benign)
    assert first["status"] == "blocked" and b.runs[run]["step"] == 1
    replay = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=benign)
    assert replay["executed"] is False and replay["error"] == "invalid_host_record"
    again = signed(run, [{"command": "ls"}], step=2, jti="j-rec-11b-one")
    refused = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=again)
    assert refused["error"] == "invalid_host_record" and "already used" in refused["note"]
    fresh = signed(run, [{"command": "ls"}], step=2, jti="j-rec-11b-two")
    assert call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=fresh)["executed"] is True
    assert b.runs[run]["host_record_ids"] == ["j-rec-11b-one", "j-rec-11b-two"]


def test_a_host_record_without_a_registered_key_is_refused(monkeypatch):
    monkeypatch.delenv(run_protocol.OBSERVER_KEYS_ENV, raising=False)
    b = bridge()
    run = start(b, run_id="rec-12")["run"]
    out = call(b, "vinc_run_submit", run=run, output=PLAIN, host_record=signed(run, [{"command": "ls"}]))
    assert out["executed"] is False and "no host observer key" in out["note"]


def test_the_record_keys_are_protocol_state():
    assert {"actions_run", "record_pending", "record_notes", "host_record_ids"} <= set(run_protocol.STATE_KEYS)


def test_the_tool_schema_lists_the_gate_kinds():
    schema = next(t for t in tools.TOOLS if t["name"] == "vinc_run_submit")["inputSchema"]["properties"]
    kinds = schema["actions_run"]["items"]["properties"]["kind"]["enum"]
    assert tuple(kinds) == role_output_gate.RECORD_KINDS
    assert set(schema["actions_run"]["items"]["properties"]) == set(run_protocol.ACTION_KEYS)
    assert schema["host_record"]["properties"]["entries"]["items"] == schema["actions_run"]["items"]
