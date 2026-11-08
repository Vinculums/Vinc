# -*- coding: utf-8 -*-
"""사람의 승인함: 기다리는 실행에 사람이 답하고, 에이전트 자격은 사람만 하는 결정에 답하지 못한다.

지키려는 것:

1. 사람만 하는(person_only) 대기에는 도구 호출(MCP·REST·CLI)의 답이 거절된다. 먼저 묻는
   (ask_first) 대기는 호스트가 사람의 선택을 그대로 내도 된다.
2. 사람의 답은 계정의 서명된 세션 프록시(`run-answer`, `auth: "session"`)로만 들어간다. 같은
   서명이라도 세션 표지가 없으면(멤버 키가 계정을 지난 것) 거절한다.
3. 답은 한 번만 들어가고, 누가·언제·어느 길로 답했는지 실행 기록에 남는다.
4. 요청은 만료된다. 만료된 요청에는 누구도 답하지 못하고 실행은 막힌 채 끝난다.
5. 실행 기록의 프로토콜 상태는 `vinc_ingest` 로 고칠 수 없다(그 길로 승인을 꾸밀 수 있다).

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import edverify, rest_api, run_protocol, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp  # noqa: E402

PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
ROLE = "concept:agent-dev-core"
PLAIN = "The change reads correctly; two findings follow."
OWNER = "user:u-1"
TEAM = "team:0191f0aa-1111-7abc-8def-0123456789ab"
ACCOUNT_SEED = bytes(range(32))
_BH_EMPTY = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(b"=").decode()


class _Bridge(Bridge):
    """역할 읽기·실행 읽기·실행 쓰기를 메모리로 흉내 낸다. 그 밖의 요청은 기록만 한다."""

    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner=OWNER)
        self.runs, self.calls = {}, []
        self.props = dict(PROFILES[ROLE])
        self.props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [],
                           "reads": [], "act_policy": {"notify": "ask_first"}})

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
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
            self.runs[node["id"]] = node.get("props") or {}
            return {"executed": True, "merged": {"nodes": 1}}
        return {"executed": True, "path": path}


def call(bridge, name, **args):
    return tools.call(name, args, bridge)


def waiting(bridge, run_id, action="merge", **extra):
    run = call(bridge, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)["run"]
    out = call(bridge, "vinc_run_submit", run=run, output="Ready to merge once approved." if
               action == "merge" else PLAIN,
               person_only_requests=[{"action": action, "reason": f"run {action} for PR 12"}],
               **extra)
    assert out["state"] == "waiting", out
    return run, out


def person(bridge, run, answer="approve", ask=1, **extra):
    return run_protocol.person_answer({"run": run, "ask": ask, "answer": answer, **extra}, bridge,
                                      OWNER, actor=OWNER)


# ── 1. 에이전트 자격의 거절과 ask_first ──────────────────────────────────────

def test_the_waiting_view_says_who_answers_and_keeps_the_exact_action_text():
    bridge = _Bridge()
    run, out = waiting(bridge, "a-1")
    assert out["decision"]["kind"] == "person_only"
    assert out["decision"]["requests"] == [{"action": "merge", "reason": "run merge for PR 12",
                                            "kind": "person_only"}]
    assert "Only a person" in out["next"] and out["expires_at"] == out["decision"]["expires_at"]
    asked = datetime.datetime.strptime(out["asked_at"], "%Y-%m-%dT%H:%M:%SZ")
    until = datetime.datetime.strptime(out["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (until - asked).total_seconds() == run_protocol.EXPIRES_DEFAULT_S


def test_an_agent_answer_to_a_person_only_decision_is_refused_and_changes_nothing():
    bridge = _Bridge()
    run, _ = waiting(bridge, "a-2")
    before = json.dumps(bridge.runs[run], sort_keys=True)
    for answer in ("approve", "decline"):
        out = call(bridge, "vinc_run_submit", run=run, answer=answer)
        assert out["executed"] is False and out["error"] == "person_answer_required"
        assert "Vinc app" in out["note"] and out["run_state"]["state"] == "waiting"
    assert json.dumps(bridge.runs[run], sort_keys=True) == before


def test_a_gate_interrupt_without_a_request_is_person_only():
    bridge = _Bridge()
    run = call(bridge, "vinc_run_start", role=ROLE, task="t", run_id="a-3")["run"]
    out = call(bridge, "vinc_run_submit", run=run, output="Please merge the pull request now.")
    if out["state"] != "waiting":
        pytest.skip("the profile's person_only gate did not interrupt this sentence")
    assert out["decision"]["kind"] == "person_only"
    assert call(bridge, "vinc_run_submit", run=run, answer="approve")["error"] == "person_answer_required"


def test_an_ask_first_decision_stays_answerable_by_the_host():
    bridge = _Bridge()
    run, out = waiting(bridge, "a-4", action="notify")
    assert out["decision"]["kind"] == "ask_first"
    resumed = call(bridge, "vinc_run_submit", run=run, answer="approve")
    assert resumed["state"] == "working" and resumed["approved"] == ["notify"]
    saved = bridge.runs[run]
    assert saved["answer_via"] == "agent" and "answered_by" not in saved


def test_a_mixed_decision_is_person_only():
    bridge = _Bridge()
    run = call(bridge, "vinc_run_start", role=ROLE, task="t", run_id="a-5")["run"]
    out = call(bridge, "vinc_run_submit", run=run, output="Ready to merge once approved.",
               person_only_requests=[{"action": "notify", "reason": "post"},
                                     {"action": "merge", "reason": "merge"}])
    assert out["decision"]["kind"] == "person_only"
    kinds = {r["action"]: r["kind"] for r in out["decision"]["requests"]}
    assert kinds == {"merge": "person_only", "notify": "ask_first"}


def test_a_waiting_record_without_a_kind_is_read_against_the_role():
    pending = {"actions": ["notify"], "asked_at": "2026-11-07T00:00:00Z"}
    policy = {"person_only": [{"action": "merge", "reason": "m"}]}
    assert run_protocol.decision_kind(pending, policy) == "ask_first"
    assert run_protocol.decision_kind({**pending, "actions": ["merge"]}, policy) == "person_only"
    assert run_protocol.decision_kind(pending) == "person_only"
    assert run_protocol.expires_at(pending) == "2026-11-07T01:00:00Z"


# ── 2·3. 사람의 답, 한 번만 ──────────────────────────────────────────────────

def test_a_person_answer_resumes_the_run_once_and_records_who_and_how():
    bridge = _Bridge()
    run, _ = waiting(bridge, "p-1")
    out = person(bridge, run)
    assert out == {"kind": "run.answer", "executed": True, "run": run, "run_id": "p-1", "ask": 1,
                   "answer": "approve", "answered_at": out["answered_at"], "answered_by": OWNER,
                   "via": "person", "state": "working", "outcome": "waiting"}
    saved = bridge.runs[run]
    assert saved["approved"] == ["merge"] and saved["answer_via"] == "person"
    assert saved["answered_by"] == OWNER and "pending" not in saved
    again = person(bridge, run)
    assert again["executed"] is False and again["error"] == "already_answered"
    # 에이전트의 다음 제출은 재개된 실행을 본다. 승인된 행동은 다시 묻지 않는다.
    final = call(bridge, "vinc_run_submit", run=run, output=PLAIN,
                 person_only_requests=[{"action": "merge", "reason": "approved"}])
    assert final["state"] == "done" and final["outcome"] == "success"


def test_an_answer_for_another_question_number_is_refused():
    bridge = _Bridge()
    run, _ = waiting(bridge, "p-2")
    assert person(bridge, run, ask=2)["error"] == "already_answered"
    assert bridge.runs[run]["state"] == "waiting"


def test_a_reply_resumes_without_approving_and_the_host_reads_the_text():
    bridge = _Bridge()
    run, _ = waiting(bridge, "p-3")
    assert person(bridge, run, answer="reply")["error"] == "invalid_text"
    assert person(bridge, run, answer="reply", text="x" * 2001)["error"] == "invalid_text"
    out = person(bridge, run, answer="reply", text="Use the release branch instead.")
    assert out["executed"] and out["state"] == "working"
    saved = bridge.runs[run]
    assert saved.get("approved") == [] and saved["answer_text"] == "Use the release branch instead."
    view = call(bridge, "vinc_run_start", role=ROLE, task="Review the change.", run_id="p-3")
    assert view["last_answer"] == {"answer": "reply", "answered_at": saved["answered_at"],
                                   "via": "person", "text": "Use the release branch instead."}
    asked_again = call(bridge, "vinc_run_submit", run=run, output="Ready to merge once approved.",
                       person_only_requests=[{"action": "merge", "reason": "again"}])
    assert asked_again["state"] == "waiting" and asked_again["ask"] == 2


def test_a_person_decline_ends_the_run_blocked():
    bridge = _Bridge()
    run, _ = waiting(bridge, "p-4")
    out = person(bridge, run, answer="decline", text="Not this week.")
    assert out["state"] == "done" and out["outcome"] == "blocked"
    assert bridge.runs[run]["end_reason"] == "declined by a person"


@pytest.mark.parametrize("args,error", [
    ({"run": "", "ask": 1, "answer": "approve"}, "run_required"),
    ({"run": "p-x", "answer": "approve"}, "ask_required"),
    ({"run": "p-x", "ask": True, "answer": "approve"}, "ask_required"),
    ({"run": "p-x", "ask": 1, "answer": "maybe"}, "invalid_answer"),
    ({"run": "p-x", "ask": 1, "answer": "approve", "text": 5}, "invalid_text"),
    ({"run": "never-started", "ask": 1, "answer": "approve"}, "run_not_found"),
])
def test_person_answer_input_is_checked(args, error):
    assert run_protocol.person_answer(args, _Bridge(), OWNER, actor=OWNER)["error"] == error


def test_a_person_answer_needs_a_person():
    bridge = _Bridge()
    run, _ = waiting(bridge, "p-5")
    out = run_protocol.person_answer({"run": run, "ask": 1, "answer": "approve"}, bridge, OWNER,
                                     actor="")
    assert out["error"] == "person_required" and bridge.runs[run]["state"] == "waiting"


# ── 4. 만료 ──────────────────────────────────────────────────────────────────

def _age(bridge, run, seconds):
    """대기를 과거로 옮긴다: 물은 때와 만료를 `seconds` 만큼 앞당긴다."""
    pending = bridge.runs[run]["pending"]
    for key in ("asked_at", "expires_at"):
        moment = datetime.datetime.strptime(pending[key], "%Y-%m-%dT%H:%M:%SZ")
        pending[key] = (moment - datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_expires_in_is_bounded():
    bridge = _Bridge()
    run = call(bridge, "vinc_run_start", role=ROLE, task="t", run_id="e-0")["run"]
    with pytest.raises(tools.InvalidArgument):
        call(bridge, "vinc_run_submit", run=run, output=PLAIN, expires_in_s=43201,
             person_only_requests=[{"action": "notify", "reason": "x"}])
    assert run_protocol._expires_in({"expires_in_s": 59})[1]
    assert run_protocol._expires_in({"expires_in_s": True})[1]
    assert run_protocol._expires_in({"expires_in_s": 43200}) == (43200, None)
    _, out = waiting(bridge, "e-1", expires_in_s=600)
    asked = datetime.datetime.strptime(out["asked_at"], "%Y-%m-%dT%H:%M:%SZ")
    until = datetime.datetime.strptime(out["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (until - asked).total_seconds() == 600


def test_expires_in_takes_the_same_number_in_any_host_form():
    # 호스트에 따라 3600 이 3600.0 이나 "3600" 으로 온다(Claude 의 MCP 호출이 정수 하나로 거절당했다).
    for raw in (3600, 3600.0, "3600", " 3600 "):
        assert run_protocol._expires_in({"expires_in_s": raw}) == (3600, None), raw
    for raw in (3600.5, "1h", "", "-60", 59.0, "43201", [3600]):
        assert run_protocol._expires_in({"expires_in_s": raw})[1], raw


def test_an_expired_request_refuses_the_person_and_ends_the_run():
    bridge = _Bridge()
    run, _ = waiting(bridge, "e-2")
    _age(bridge, run, run_protocol.EXPIRES_DEFAULT_S + 1)
    out = person(bridge, run)
    assert out["executed"] is False and out["error"] == "request_expired"
    saved = bridge.runs[run]
    assert saved["state"] == "done" and saved["outcome"] == "blocked" and saved["expired_at"]
    assert "answer" not in saved
    assert person(bridge, run)["error"] == "already_answered"


def test_an_expired_request_refuses_the_agent_too():
    bridge = _Bridge()
    run, _ = waiting(bridge, "e-3", action="notify")
    _age(bridge, run, run_protocol.EXPIRES_DEFAULT_S + 1)
    out = call(bridge, "vinc_run_submit", run=run, answer="approve")
    assert out["error"] == "request_expired" and bridge.runs[run]["state"] == "done"


# ── 5. 실행 기록의 상태는 도구 쓰기로 못 고친다 ──────────────────────────────

@pytest.mark.parametrize("field", ["props", "props_patch"])
@pytest.mark.parametrize("key", ["state", "approved", "answer", "pending"])
def test_ingest_cannot_rewrite_a_run_records_protocol_state(field, key):
    bridge = _Bridge()
    out = call(bridge, "vinc_ingest", nodes=[{"id": "episode:run-p-1-0000abcd", "type": "episode",
                                              "title": "Run", field: {key: "working"}}])
    assert out["executed"] is False and out["error"] == "run_state_protected"
    assert not [c for c in bridge.calls if c[1] == "/ingest/fragment"]


def test_ingest_may_still_add_host_fields_to_a_run_record():
    bridge = _Bridge()
    out = call(bridge, "vinc_ingest", nodes=[{"id": "episode:run-p-1-0000abcd", "type": "episode",
                                              "title": "Run", "props": {"host": "codex",
                                                                        "model_family": "gpt"}}])
    assert out.get("error") != "run_state_protected"
    assert [c for c in bridge.calls if c[1] == "/ingest/fragment"]


# ── 2. 세션 프록시 ───────────────────────────────────────────────────────────

def _sign(payload: dict) -> str:
    now = int(time.time())
    return edverify.sign_assertion(ACCOUNT_SEED, {"iss": "account", "aud": "mcp", "iat": now,
                                                  "exp": now + 60, "bh": _BH_EMPTY,
                                                  "jti": uuid.uuid4().hex, **payload})


def _app(bridge):
    return MCPHTTPApp(bridge, static_token="S",
                      account_public=edverify.secret_to_public(ACCOUNT_SEED))


def test_the_session_proxy_answers_for_the_signed_person():
    bridge = _Bridge()
    run, _ = waiting(bridge, "x-1")
    app = _app(bridge)
    out = app.graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": "run-answer",
                                         "auth": "session"}),
                                  {"run": run, "ask": 1, "answer": "approve", "owner": "user:evil"})
    assert out["executed"] and out["answered_by"] == "user:u-1" and out["via"] == "person"
    assert bridge.runs[run]["state"] == "working"


def test_a_proxy_request_without_the_session_mark_cannot_answer():
    bridge = _Bridge()
    run, _ = waiting(bridge, "x-2")
    app = _app(bridge)
    for mark in ({}, {"auth": "key"}, {"auth": "Session"}):
        out = app.graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": "run-answer",
                                             **mark}), {"run": run, "ask": 1, "answer": "approve"})
        assert out["executed"] is False and out["error"] == "person_session_required"
    assert bridge.runs[run]["state"] == "waiting"


def test_the_waiting_list_goes_to_the_sidecar_under_the_signed_owner():
    bridge = _Bridge()
    _app(bridge).graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": "runs-waiting"}),
                                     {"owner": "user:evil", "limit": 5})
    method, path, params, _ = bridge.calls[-1]
    assert (method, path) == ("GET", "/runs/waiting") and params["owner"] == "user:u-1"


def test_in_a_team_a_viewer_lists_and_only_an_editor_or_admin_answers():
    bridge = _Bridge()
    app = _app(bridge)

    def team(op, role, **extra):
        return _sign({"typ": "proxy-team", "sub": "u-1", "op": op, "graph_owner": TEAM,
                      "role": role, **extra})

    app.graph_owner_request(team("runs-waiting", "viewer"), {})
    assert bridge.calls[-1][1] == "/runs/waiting" and bridge.calls[-1][2]["owner"] == TEAM
    with pytest.raises(PermissionError):
        app.graph_owner_request(team("run-answer", "viewer", auth="session"),
                                {"run": "x", "ask": 1, "answer": "approve"})
    out = app.graph_owner_request(team("run-answer", "editor", auth="session"),
                                  {"run": "never-started", "ask": 1, "answer": "approve"})
    assert out["error"] == "run_not_found"
    assert [c for c in bridge.calls if c[1] == "/runs"][-1][2]["owner"] == TEAM


def test_a_community_space_has_no_answer():
    app = _app(_Bridge())
    with pytest.raises(PermissionError):
        app.graph_owner_request(_sign({"typ": "proxy-community", "sub": "u-1", "op": "run-answer",
                                       "graph_owner": "community:0191f0aa-1111-7abc-8def-0123456789ab",
                                       "role": "reader", "visibility": "public",
                                       "auth": "session"}), {})


# ── REST 상태 ────────────────────────────────────────────────────────────────

def test_rest_maps_the_refusals_to_their_own_codes():
    refused = rest_api._failure("vinc_run_submit", {"kind": "run", "executed": False,
                                                    "error": "person_answer_required", "note": "n"})
    assert refused[:2] == (403, "person_answer_required")
    expired = rest_api._failure("vinc_run_submit", {"kind": "run", "executed": False,
                                                    "error": "request_expired", "note": "n"})
    assert expired[:2] == (410, "request_expired")
    guarded = rest_api._failure("vinc_ingest", {"kind": "ingest", "executed": False,
                                                "error": "run_state_protected", "note": "n"})
    assert guarded[:2] == (403, "run_state_protected")


def test_the_web_proxy_ingest_cannot_rewrite_run_state_either():
    """계정의 그래프 프록시는 멤버 키도 지난다. 그 길로 승인을 꾸미지 못한다."""
    bridge = _Bridge()
    app = _app(bridge)
    node = {"id": "episode:run-p-1-0000abcd", "type": "episode", "title": "Run",
            "props": {"state": "working", "approved": ["merge"]}}
    for op, args in (("ingest-fragment", {"nodes": [node], "edges": []}),
                     ("ingest-pack", {"pack": {"fragment": {"nodes": [node], "edges": []}}})):
        out = app.graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": op}), args)
        assert out["error"] == "run_state_protected", op
    assert not [c for c in bridge.calls if c[1].startswith("/ingest/")]
    ok = app.graph_owner_request(_sign({"typ": "proxy", "sub": "u-1", "op": "ingest-fragment"}),
                                 {"nodes": [{"id": "concept:x", "type": "concept", "title": "X"}]})
    assert ok.get("error") != "run_state_protected"
