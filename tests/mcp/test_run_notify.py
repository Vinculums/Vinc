# -*- coding: utf-8 -*-
"""대기 알림: 실행이 사람 앞에서 멈추면 허브가 계정에 한 번 알린다(웹 푸시).

지키려는 것:

1. 대기가 저장된 뒤에만 알린다. 저장이 실패하면 알리지 않는다.
2. 본문은 소유자·실행·질문 번호·제목·만료 다섯뿐이다. 행동 설명·질문·작업·호스트는 없다.
3. 어서션은 허브 키로 서명되고 `typ: run-waiting`·본문 해시·소유자에 묶인다.
4. 서명키가 없는 프로세스(데스크톱 로컬 MCP)는 아무것도 보내지 않는다.
5. 계정이 답하지 않거나 거절해도 실행은 그대로 대기에 있다.
6. 팀 공간의 실행은 팀 소유자(`team:`)로 알린다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import edverify, hubhttp, run_notify  # noqa: E402
from tests.mcp.test_run_approvals import OWNER, TEAM, _Bridge, call, waiting  # noqa: E402

HUB_SEED = bytes(range(1, 33))


@pytest.fixture
def posts(monkeypatch):
    """허브 서명키를 켜고 계정으로 가는 요청을 가로챈다. 스레드 없이 바로 보낸다."""
    monkeypatch.setenv("VINC_MCP_ED25519_SEED", base64.b64encode(HUB_SEED).decode())
    monkeypatch.setenv("VINC_ACCOUNT_INTERNAL_URL", "http://account.test")
    sent = []

    def fake_request(url, **kw):
        sent.append({"url": url, **kw})
        return hubhttp.Response(200, {}, '{"ok":true}')

    monkeypatch.setattr(run_notify.hubhttp, "request", fake_request)
    real = run_notify.waiting
    monkeypatch.setattr(run_notify, "waiting",
                        lambda owner, eid, props, background=True: real(owner, eid, props,
                                                                         background=False))
    return sent


def _claims(post):
    token = post["headers"]["X-Vinc-Assertion"]
    claims = edverify.verify_assertion(edverify.secret_to_public(HUB_SEED), token, max_age=120)
    assert claims is not None, "허브 공개키로 검증되지 않는다"
    return claims, token


def test_a_waiting_run_is_announced_once_with_only_five_fields(posts):
    bridge = _Bridge()
    run, out = waiting(bridge, "notify-1")
    assert len(posts) == 1
    post = posts[0]
    assert post["url"] == "http://account.test/internal/mcp/run-waiting"
    body = json.loads(post["data"])
    assert set(body) == {"owner", "run", "ask", "title", "expires_at"}
    assert body["owner"] == OWNER and body["run"] == run and body["ask"] == 1
    assert body["expires_at"] == out["expires_at"] != ""
    raw = post["data"].decode("utf-8")
    # 행동 설명·작업·질문이 본문 어디에도 없다.
    for secret in ("run merge for PR 12", "Review the change.", "merge"):
        assert secret not in raw


def test_the_assertion_is_signed_and_bound_to_type_owner_and_body(posts):
    bridge = _Bridge()
    waiting(bridge, "notify-2")
    post = posts[0]
    claims, _ = _claims(post)
    assert claims["iss"] == "mcp" and claims["aud"] == "account"
    assert claims["typ"] == "run-waiting" and claims["sub"] == OWNER
    assert claims["exp"] - claims["iat"] == 120 and len(claims["jti"]) == 32
    want = base64.urlsafe_b64encode(hashlib.sha256(post["data"]).digest()).rstrip(b"=").decode()
    assert claims["bh"] == want


def test_a_team_run_is_announced_as_the_team(posts):
    bridge = _Bridge()
    bridge.owner = TEAM
    from vinc_mcp import run_protocol
    run = run_protocol.start({"role": "concept:agent-dev-core", "task": "t", "run_id": "team-1"},
                             bridge, TEAM)["run"]
    out = run_protocol.submit({"run": run, "output": "Ready to merge once approved.",
                               "person_only_requests": [{"action": "merge", "reason": "r"}]},
                              bridge, TEAM)
    assert out["state"] == "waiting", out
    assert json.loads(posts[-1]["data"])["owner"] == TEAM


def test_output_that_passes_sends_nothing(posts):
    bridge = _Bridge()
    run = call(bridge, "vinc_run_start", role="concept:agent-dev-core", task="t",
               run_id="notify-pass")["run"]
    out = call(bridge, "vinc_run_submit", run=run,
               output="The change reads correctly; two findings follow.")
    assert out.get("state") != "waiting"
    assert posts == []


def test_a_failed_save_sends_nothing(posts):
    bridge = _Bridge()
    run = call(bridge, "vinc_run_start", role="concept:agent-dev-core", task="t",
               run_id="notify-fail")["run"]
    real = bridge.request

    def failing(method, path, params=None, body=None, timeout=60):
        if path == "/ingest/fragment":
            return {"executed": False, "error": "store down"}
        return real(method, path, params, body, timeout)

    bridge.request = failing
    out = call(bridge, "vinc_run_submit", run=run, output="Ready to merge once approved.",
               person_only_requests=[{"action": "merge", "reason": "r"}])
    assert out.get("error") == "run_not_saved"
    assert posts == []


def test_without_the_hub_key_nothing_is_sent(posts, monkeypatch):
    monkeypatch.delenv("VINC_MCP_ED25519_SEED")
    bridge = _Bridge()
    waiting(bridge, "notify-local")
    assert posts == []


def test_an_account_refusal_leaves_the_run_waiting(monkeypatch, posts):
    def refusing(url, **kw):
        raise hubhttp.HubHTTPError(url, 404, body="{}")

    monkeypatch.setattr(run_notify.hubhttp, "request", refusing)
    bridge = _Bridge()
    run, out = waiting(bridge, "notify-refused")
    assert out["state"] == "waiting"
    assert bridge.runs[run]["state"] == "waiting"
    assert run_notify.send({"owner": OWNER, "run": run, "ask": 1, "title": "t",
                            "expires_at": ""}) == "http_404"


def test_an_unreachable_account_is_a_category_not_an_exception(monkeypatch, posts):
    def down(url, **kw):
        raise hubhttp.HubHTTPError(url, None, cause=TimeoutError())

    monkeypatch.setattr(run_notify.hubhttp, "request", down)
    assert run_notify.send({"owner": OWNER, "run": "r", "ask": 1, "title": "t",
                            "expires_at": ""}) == "unreachable"


def test_payload_refuses_a_run_that_is_not_waiting_or_an_odd_owner():
    props = {"state": "working", "role": "concept:agent-x", "run_id": "r", "outcome": "waiting"}
    assert run_notify.payload(OWNER, "episode:run-r", props) is None
    props["state"] = "waiting"
    assert run_notify.payload("community:abc", "episode:run-r", props) is None
    got = run_notify.payload(OWNER, "episode:run-r", {**props, "asks": 2,
                                                       "pending": {"asked_at": "2026-11-08T00:00:00Z"}})
    assert got == {"owner": OWNER, "run": "episode:run-r", "ask": 2,
                   "title": "Run r of agent-x: waiting", "expires_at": "2026-11-08T01:00:00Z"}
