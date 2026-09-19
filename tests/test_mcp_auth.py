# -*- coding: utf-8 -*-
"""B-B1·B-B2 — resolve_auth(정적/apikey/oauth) + owner 강제 스탬핑(멀티테넌트 봉쇄).

핵심 보안 주장: 원격 인증경로가 소유자를 해석하면, 클라이언트가 args.owner 로
'남의 스코프'를 지정해도 **덮어써진다**(한 회원 토큰으로 타인 데이터 불가).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import tools                          # noqa: E402
from vinc_mcp.bridge import Bridge                  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.oauth import OAuthGate                # noqa: E402
from vinc_mcp.store import Store                    # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": False, "echo": path}


# ── resolve_auth ─────────────────────────────────────────────────────────────
def _app(**kw):
    return MCPHTTPApp(_StubBridge(), **kw)


def test_resolve_auth_static():
    app = _app(static_token="S")
    assert app.resolve_auth("Bearer S") == {"ok": True, "owner": None, "plan": None,
                                            "scope": "full", "source": "static"}
    assert app.resolve_auth("Bearer nope")["ok"] is False
    assert app.resolve_auth(None)["ok"] is False
    assert app.resolve_auth("Basic x")["ok"] is False


def test_resolve_auth_apikey_via_verify_key():
    seen = {}

    def vk(raw):
        seen["raw"] = raw
        return {"owner": "user:u1", "plan": "team"} if raw == "vinc_sk_good" else None

    app = _app(static_token="S", verify_key=vk)
    ok = app.resolve_auth("Bearer vinc_sk_good")
    # scope: 스텁이 scope 를 안 실었다 = 모르는 스코프 → 'read'(fail-closed, W18).
    # 실물 entitlement.verify_key 는 항상 싣는다(구 계정 응답의 vinc_sk 는 full 정규화).
    assert ok == {"ok": True, "owner": "user:u1", "plan": "team", "scope": "read",
                  "source": "apikey"}
    assert seen["raw"] == "vinc_sk_good"
    assert app.resolve_auth("Bearer vinc_sk_bad")["ok"] is False


def test_resolve_auth_apikey_disabled_when_no_verify_key():
    app = _app(static_token="S")                 # verify_key 미주입
    assert app.resolve_auth("Bearer vinc_sk_x")["ok"] is False


def test_resolve_auth_oauth_carries_owner_plan():
    gate = OAuthGate("http://i", admin_key=None, store=Store(":memory:"))
    gate.store.add_token("tokA", "cid", "user:member7", "free", exp=9e9)
    app = _app(oauth=gate)
    got = app.resolve_auth("Bearer tokA")
    assert got["ok"] and got["owner"] == "user:member7" and got["plan"] == "free"
    assert got["source"] == "oauth"


# ── owner 강제 스탬핑(B-B2) ──────────────────────────────────────────────────
def test_forced_owner_overrides_client_owner():
    b = _StubBridge()
    # 공격 시나리오: 클라이언트가 남의 owner 를 지정 → forced_owner 가 덮어써야 함
    tools.call("vinc_recall", {"owner": "user:victim"}, b, forced_owner="user:me")
    assert b.calls[0][2]["owner"] == "user:me"


def test_forced_owner_stamps_ingest_write():
    b = _StubBridge()
    tools.call("vinc_ingest", {"nodes": [{"id": "c:x", "type": "concept"}],
                               "owner": "user:victim"}, b, forced_owner="user:me")
    assert b.calls[0][3]["owner"] == "user:me"      # 쓰기도 강제 owner


def test_no_forced_owner_preserves_legacy():
    b = _StubBridge()
    tools.call("vinc_recall", {"owner": "user:alice"}, b)      # forced 없음
    assert b.calls[0][2]["owner"] == "user:alice"
    tools.call("vinc_domains", {}, b)                          # owner 없음 → bridge 기본
    assert b.calls[1][2]["owner"] == "user:local"


# ── HTTP E2E: Bearer vinc_sk_ → owner 강제 스탬핑 체인 전부 ────────────────────
class _Srv:
    def __init__(self, app):
        self.httpd = make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def post(self, obj, token):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp",
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())

    def stop(self):
        self.httpd.shutdown()


# ── B-D2 쿼터 ────────────────────────────────────────────────────────────────
_QCFG = {"free_limit": 3, "team_limit": 10, "window_s": 86400, "lock_s": 43200}


def _quota_app(plan="free"):
    return MCPHTTPApp(
        _StubBridge(), store=Store(":memory:"), quota_config=_QCFG,
        verify_key=lambda raw: {"owner": "user:u1", "plan": plan})


def test_quota_allows_then_denies_with_human_message():
    app = _quota_app("free")
    for _ in range(3):
        assert app.quota_check("user:u1", "free") is None      # 3회 허용
    denial = app.quota_check("user:u1", "free")                 # 4회째 초과
    assert denial["isError"] is True
    q = denial["_meta"]["vinc_quota"]
    assert q["limit"] == 3 and q["retry_after_s"] == 43200
    assert "vincs.io/account" in q["upgrade_url"]
    assert "Free tier limit reached" in denial["content"][0]["text"]


def test_quota_exempt_owner_is_never_denied_while_others_still_count():
    cfg = dict(_QCFG, exempt_owners=frozenset({"user:owner"}))
    app = MCPHTTPApp(_StubBridge(), store=Store(":memory:"), quota_config=cfg,
                     verify_key=lambda raw: {"owner": "user:owner", "plan": "team"})
    for _ in range(3):
        assert app.quota_check("user:u1", "free") is None
    assert app.quota_check("user:u1", "free")["isError"] is True   # 남은 owner 는 그대로 센다
    for _ in range(_QCFG["team_limit"] * 2):
        assert app.quota_check("user:owner", "team") is None         # 한도·잠금 무관


def test_quota_team_gets_higher_ceiling_and_no_upsell():
    app = _quota_app("team")
    for _ in range(10):
        assert app.quota_check("user:u1", "team") is None        # team_limit=10
    denial = app.quota_check("user:u1", "team")
    assert denial["_meta"]["vinc_quota"]["limit"] == 10
    assert "Upgrade" not in denial["content"][0]["text"]         # 유료 등급에겐 업셀 없음


def test_quota_unlimited_without_config_or_owner():
    app = MCPHTTPApp(_StubBridge(), static_token="S")            # quota_config 없음
    assert app.quota_check("user:u1", "free") is None
    app2 = _quota_app("free")
    assert app2.quota_check("", None) is None                    # owner 없음(로컬) = 무제한


def test_http_quota_blocks_tools_call_but_ping_stays_free():
    app = _quota_app("free")
    srv = _Srv(app)
    try:
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "vinc_domains", "arguments": {}}}
        for _ in range(3):
            _, r = srv.post(call, token="vinc_sk_k")
            assert r["result"]["isError"] is False
        # 4회째 → 쿼터 거부(프로토콜 오류 아님, isError 결과)
        status, r = srv.post(call, token="vinc_sk_k")
        assert status == 200 and r["result"]["isError"] is True
        assert r["result"]["_meta"]["vinc_quota"]["retry_after_s"] > 0
        # 핸드셰이크는 잠금 중에도 무료 — 커넥터가 죽지 않는다
        _, r = srv.post({"jsonrpc": "2.0", "id": 9, "method": "ping"}, token="vinc_sk_k")
        assert r["result"] == {}
    finally:
        srv.stop()


def test_http_apikey_owner_is_stamped_end_to_end():
    stub = _StubBridge()
    app = MCPHTTPApp(stub, verify_key=lambda raw:
                     {"owner": "user:u1", "plan": "team"} if raw == "vinc_sk_k" else None)
    srv = _Srv(app)
    try:
        # 클라이언트가 owner=user:attacker 를 실어도 스탬핑은 user:u1
        status, reply = srv.post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "vinc_recall",
                                             "arguments": {"owner": "user:attacker"}}},
                                 token="vinc_sk_k")
        assert status == 200 and reply["result"]["isError"] is False
        # 사이드카가 실제로 받은 owner = 강제 owner
        recall = [c for c in stub.calls if c[1] == "/recall"][0]
        assert recall[2]["owner"] == "user:u1"
        # 잘못된 키는 401
        try:
            srv.post({"jsonrpc": "2.0", "id": 2, "method": "ping"}, token="vinc_sk_bad")
            assert False, "expected 401"
        except urllib.error.HTTPError as e:
            assert e.code == 401
    finally:
        srv.stop()


# ── 비 UTF-8 본문: 503(커넥션 리셋) 이 아니라 400 ─────────────────────────────
# 2026-08-04 라이브 허브 실사고. 호스트가 CP949 로 인코딩한 질의를 보내자 `_body()` 의
# `.decode("utf-8")` 이 do_POST 를 관통해 죽였다 — 프록시 뒤라 클라이언트는 503 을 받고
# 로그에는 매 요청 스택트레이스가 쌓였다. 잘못된 본문은 JSON 파싱 실패와 같은 취급이어야 한다.
def _raw_post(port: int, body: bytes, token: str = "S"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def test_http_non_utf8_body_is_400_not_a_reset():
    srv = _Srv(MCPHTTPApp(_StubBridge(), static_token="S"))
    try:
        bad = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"q":"\xb9\xab"}}'
        try:
            _raw_post(srv.port, bad)
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
            assert json.loads(e.read().decode())["error"] == "expected a JSON-RPC message"
        # 서버가 죽지 않았다 — 다음 요청은 정상 처리된다
        status, reply = _raw_post(
            srv.port, json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode())
        assert status == 200 and reply["id"] == 2
    finally:
        srv.stop()
