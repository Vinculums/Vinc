# -*- coding: utf-8 -*-
"""남은 호출 수 표시: `/v1/me`·MCP `_meta`·REST 헤더·계정 내부 경로가 같은 값을 낸다.

Curious 는 하루 한도를 세므로 남은 수와 리셋 시각(UTC)을 싣고, 80% 에 닿으면 경고 한 줄을
더한다. 세지 않는 스페이스(Genius·면제·등급을 아직 안 싣는 팀)는 수를 지어내지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_rest_v1 import TEAM, Server                # noqa: E402
from vinc_mcp import edverify as ev                  # noqa: E402
from vinc_mcp import entitlement, toolcall           # noqa: E402
from vinc_mcp import http_server as hs               # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402
from vinc_mcp.store import Store                     # noqa: E402

_QCFG = {"free_limit": 10, "exempt_owners": frozenset({"user:me"})}
ACCT_SEED = bytes((i * 7 + 3) & 0xFF for i in range(32))


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": True, "rows": []}


def _app(plan="free", **kw):
    return MCPHTTPApp(_StubBridge(), store=Store(":memory:"), quota_config=_QCFG,
                      verify_key=lambda raw: {"owner": "user:u1", "plan": plan, "scope": "full"},
                      **kw)


def _spend(app, owner, n, plan="free"):
    for _ in range(n):
        assert app.quota_check(owner, plan) is None


# ── 표시 모양 ─────────────────────────────────────────────────────────────────
def test_a_curious_space_shows_used_remaining_and_the_utc_reset():
    app = _app()
    _spend(app, "user:u1", 3)
    view = app.quota_view("user:u1", "free")
    assert {k: view[k] for k in ("metered", "plan", "limit", "used", "remaining")} == {
        "metered": True, "plan": "free", "limit": 10, "used": 3, "remaining": 7}
    assert view["resets_at"].endswith("T00:00:00Z")
    assert "warning" not in view


def test_the_warning_starts_at_eighty_percent():
    app = _app()
    _spend(app, "user:u1", 7)
    assert "warning" not in app.quota_view("user:u1", "free")
    _spend(app, "user:u1", 1)
    view = app.quota_view("user:u1", "free")
    assert "8 of 10" in view["warning"] and "2 left" in view["warning"]
    assert "at 00:00 UTC" in view["warning"]
    assert view["upgrade_url"] == "https://vincs.io/pricing"


def test_unmetered_spaces_do_not_invent_numbers():
    app = _app()
    assert app.quota_view("user:u1", "genius") == {"metered": False}
    assert app.quota_view("user:me", "free") == {"metered": False}       # 면제
    assert app.quota_view("team:t1", None) == {"metered": False}         # 팀 등급 미전송
    assert app.quota_view("team:t1", "free")["metered"] is True
    assert MCPHTTPApp(_StubBridge(), static_token="s").quota_view("user:u1", "free") is None
    assert app.store.usage_rows() == []                                  # 보기만 한다


# ── MCP ───────────────────────────────────────────────────────────────────────
@pytest.fixture
def serve():
    started = []

    def start(app):
        httpd = hs.make_server(app, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        port = httpd.server_address[1]

        def post(obj):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/mcp", data=json.dumps(obj).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer vinc_sk_k"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode())
        return post
    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def _call(i, name="vinc_domains"):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


def test_mcp_results_carry_the_remaining_calls_and_a_line_at_eighty_percent(serve):
    app = _app()
    post = serve(app)
    first = post(_call(1))["result"]
    assert first["_meta"]["vinc_quota"]["remaining"] == 9
    assert len(first["content"]) == 1
    _spend(app, "user:u1", 6)
    eighth = post(_call(2))["result"]
    assert eighth["isError"] is False and eighth["_meta"]["vinc_quota"]["used"] == 8
    assert eighth["content"][-1]["text"].startswith("You have used 8 of 10 tool calls today")


def test_genius_results_carry_no_quota(serve):
    post = serve(_app("genius"))
    result = post(_call(1))["result"]
    assert "vinc_quota" not in (result.get("_meta") or {})
    assert len(result["content"]) == 1


def test_error_results_are_left_alone():
    reply = {"jsonrpc": "2.0", "id": 1,
             "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}
    before = json.loads(json.dumps(reply))
    assert hs._with_quota_meta(reply, {"metered": True, "warning": "w"}) == before


def test_citation_results_keep_their_body_and_carry_the_warning_in_meta():
    reply = {"jsonrpc": "2.0", "id": 1,
             "result": {"isError": False, "content": [{"type": "text", "text": "{}"}],
                        "structuredContent": {"results": []}}}
    out = hs._with_quota_meta(reply, {"metered": True, "warning": "w"})["result"]
    assert len(out["content"]) == 1 and out["_meta"]["vinc_quota"]["warning"] == "w"


def test_whoami_gives_each_space_its_own_allowance(monkeypatch):
    monkeypatch.setattr(entitlement, "list_spaces", lambda sub: [
        {"id": "t-free", "name": "A", "role": "admin", "plan": "free"},
        {"id": "t-legacy", "name": "B", "role": "editor"}])
    monkeypatch.setattr(entitlement, "list_communities", lambda sub: [])
    app = _app()
    _spend(app, "team:t-free", 4)
    result = toolcall.execute("vinc_whoami", {}, app.bridge, forced_owner="user:u1",
                              plan="free", quota_view=app.quota_view, hub=True).result
    spaces = {s["id"]: s for s in result["spaces"]}
    assert result["quota"]["remaining"] == 10
    assert spaces["personal"]["quota"] == result["quota"]
    assert spaces["t-free"]["quota"]["used"] == 4                 # 팀은 팀이 나눠 쓴다
    assert spaces["t-legacy"]["quota"] == {"metered": False}


# ── REST ──────────────────────────────────────────────────────────────────────
def test_rest_success_and_denial_carry_the_quota_headers():
    srv = Server(quota={"free_limit": 2})
    try:
        status, headers, body = srv.request("GET", "/v1/me")
        assert status == 200 and body["quota"]["used"] == 1      # whoami 도 한 번이다
        assert (headers["Vinc-Quota-Limit"], headers["Vinc-Quota-Remaining"]) == ("2", "1")
        assert headers["Vinc-Quota-Reset"].endswith("T00:00:00Z")
        srv.request("GET", "/v1/domains")
        status, headers, body = srv.request("GET", "/v1/domains")
        assert status == 429 and headers["Vinc-Quota-Reset"].endswith("T00:00:00Z")
        assert body["error"]["details"]["resets_at"] == headers["Vinc-Quota-Reset"]
    finally:
        srv.close()


def test_rest_team_space_without_a_plan_sends_no_quota_headers():
    srv = Server(quota={"free_limit": 2})
    try:
        status, headers, _body = srv.request("GET", "/v1/domains?space=" + TEAM)
        assert status == 200 and "Vinc-Quota-Limit" not in headers
    finally:
        srv.close()


# ── 계정 내부 경로 ────────────────────────────────────────────────────────────
def _bh(raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def _assertion(raw: bytes, **over) -> str:
    now = int(time.time())
    body = {"typ": "quota", "iss": "account", "aud": "mcp", "sub": "user:u1",
            "iat": now, "exp": now + 120, "bh": _bh(raw), "jti": uuid.uuid4().hex}
    body.update(over)
    return ev.sign_assertion(ACCT_SEED, body)


_RAW = json.dumps({"spaces": [{"owner": "user:u1", "plan": "free"},
                              {"owner": "team:t1"}]}).encode()


def test_the_account_reads_each_space_with_a_signed_request():
    app = _app(account_public=ev.secret_to_public(ACCT_SEED))
    _spend(app, "user:u1", 2)
    signed = _assertion(_RAW)
    out = app.quota_request(signed, raw_body=_RAW)
    assert out["kind"] == "quota.view"
    assert [s["owner"] for s in out["spaces"]] == ["user:u1", "team:t1"]
    assert out["spaces"][0]["quota"]["used"] == 2
    assert out["spaces"][1]["quota"] == {"metered": False}
    assert app.quota_request(signed, raw_body=_RAW) == out    # 읽기라 같은 어서션을 다시 받는다


@pytest.mark.parametrize("signed, raw", [
    ("", _RAW),
    (_assertion(_RAW, typ="usage"), _RAW),
    (_assertion(_RAW, aud="account"), _RAW),
    (_assertion(_RAW), json.dumps({"spaces": [{"owner": "user:someone-else"}]}).encode()),
])
def test_unsigned_or_mismatched_requests_are_refused(signed, raw):
    app = _app(account_public=ev.secret_to_public(ACCT_SEED))
    with pytest.raises(PermissionError):
        app.quota_request(signed, raw_body=raw)


@pytest.mark.parametrize("body", [
    {"spaces": [{"owner": "community:x"}]},
    {"spaces": [{"owner": "user:a b"}]},
    {"spaces": "user:u1"},
    {"spaces": [{"owner": f"user:u{i}"} for i in range(33)]},
])
def test_malformed_bodies_are_bad_requests(body):
    app = _app(account_public=ev.secret_to_public(ACCT_SEED))
    raw = json.dumps(body).encode()
    with pytest.raises(ValueError):
        app.quota_request(_assertion(raw), raw_body=raw)


def test_the_http_route_answers_only_a_signed_request():
    app = _app(account_public=ev.secret_to_public(ACCT_SEED))
    httpd = hs.make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/internal/quota"

    def post(signed):
        headers = {"Content-Type": "application/json"}
        if signed is not None:
            headers["X-Vinc-Assertion"] = signed
        req = urllib.request.Request(base, data=_RAW, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    try:
        assert post(None)[0] == 401
        status, body = post(_assertion(_RAW))
        assert status == 200 and body["spaces"][0]["quota"]["metered"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_route_is_closed_without_the_account_key():
    app = _app()
    with pytest.raises(PermissionError):
        app.quota_request(_assertion(_RAW), raw_body=_RAW)
