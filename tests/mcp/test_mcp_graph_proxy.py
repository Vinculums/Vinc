# -*- coding: utf-8 -*-
"""웹 Vinc 그래프 프록시(Phase 1) — MCP /internal/graph-owner 게이트 계약.

purge 와 같은 신뢰 모델(계정 서명·우리가 검증·루프백)이되 read/write op 를 일반화한다.
핵심 불변: **owner 는 서명된 sub 에서만** 강제되고(바디/args owner 무시), op 는
화이트리스트로만, 위조·만료·typ 재사용은 거부. 실엔진 왕복은 SEC-3/e2e 가 담당 —
여기선 게이트·owner 강제·op 라우팅 계약을 직접 본다(SEC-1 교훈).
"""
from __future__ import annotations

import base64
import hashlib
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp.bridge import Bridge             # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp    # noqa: E402

ACCT_SEED = bytes((i * 9 + 4) & 0xFF for i in range(32))


class _CapBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"ok": True}


#: 빈 본문의 지문. 아래 호출들은 `raw_body` 를 안 넘기므로 허브가 `b""` 를 대조한다.
_BH_EMPTY = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(b"=").decode()


def _assertion(op="search", sub="u-1", seed=ACCT_SEED, **over):
    """계정이 **실제로 서명하는 모양**으로 만든다 — `bh`·`jti` 포함.

    이 둘이 없던 시절 이 헬퍼는 호환 경로(없으면 통과)를 시험하고 있었고, 그래서
    필수로 올리는 순간 전부 떨어졌다. 계정은 `bc22584`(2026-08-20)부터 두 필드를
    싣는다(account/src/services/mcpProxy.ts) — 여기를 그 모양에 맞춘다.

    `jti` 는 **호출마다 새로 만든다.** 고정하면 첫 테스트만 통과하고 나머지가 재생으로
    거부되는데, 그 실패는 "재생 방어가 동작한다"는 뜻이라 원인을 오해하기 쉽다."""
    now = int(time.time())
    body = {"typ": "proxy", "iss": "account", "aud": "mcp",
            "sub": sub, "op": op, "iat": now, "exp": now + 120,
            "bh": _BH_EMPTY, "jti": uuid.uuid4().hex}
    body.update(over)
    return ev.sign_assertion(seed, body)


@pytest.fixture
def app():
    return MCPHTTPApp(_CapBridge(), static_token="t",
                      account_public=ev.secret_to_public(ACCT_SEED))


# ── owner 강제 + op 라우팅 ────────────────────────────────────────────────────
def test_read_op_forces_owner_into_params(app):
    app.graph_owner_request(_assertion(op="search"), {"query": "x", "top_k": 20})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == ("GET", "/search")
    assert params["owner"] == "user:u-1"          # 서명된 sub 에서만
    assert params["query"] == "x" and body is None


def test_node_read_op_forces_owner_into_params(app):
    app.graph_owner_request(_assertion(op="node"),
                            {"node_id": "record:claims", "owner": "user:victim"})
    assert app.bridge.calls == [("GET", "/node", {
        "node_id": "record:claims", "owner": "user:u-1"}, None)]


def test_node_edges_stamps_owner_and_preserves_batch(app):
    app.graph_owner_request(_assertion(op="node-edges"),
                            {"nodes": '["concept:a","concept:b"]',
                             "dir": "out", "type": "PREREQUISITE_OF", "owner": "evil"})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == ("GET", "/node-edges")
    assert params["owner"] == "user:u-1"
    assert params["nodes"] == '["concept:a","concept:b"]'
    assert params["dir"] == "out" and params["type"] == "PREREQUISITE_OF"
    assert body is None


def test_roles_stamps_owner_without_expanding_the_graph(app):
    app.graph_owner_request(_assertion(op="roles"), {"role":"agent-dev-core", "owner":"evil"})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == ("GET", "/roles")
    assert params["owner"] == "user:u-1" and params["role"] == "agent-dev-core"
    assert body is None


def test_debug_is_stripped_on_the_web_plane(app):
    """웹 평면은 쿼리 인자를 그대로 흘린다 — `debug` 는 허브에서 벗긴다(P4 S13)."""
    app.graph_owner_request(_assertion(op="domains"), {"debug": "true"})
    _m, _p, params, _b = app.bridge.calls[0]
    assert "debug" not in params


def test_search_pagination_params_pass_through(app):
    """GM-14 웹 등재 회귀 — cursor/detail 은 **파라미터**라 프록시 코드 변경 없이
    opaque 로 통과해야 한다(등재가 필요한 것은 라우트/op 뿐). 여기가 깨지면 웹에서만
    페이지네이션이 조용히 무시된다."""
    app.graph_owner_request(_assertion(op="search"),
                            {"query": "x", "top_k": 10,
                             "cursor": "CUR123", "detail": "summary"})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == ("GET", "/search")
    assert params["cursor"] == "CUR123"
    assert params["detail"] == "summary"
    assert params["owner"] == "user:u-1"


def test_write_op_forces_owner_into_body(app):
    app.graph_owner_request(_assertion(op="delete"), {"node": "concept:x"})
    method, path, params, body = app.bridge.calls[0]
    assert (method, path) == ("POST", "/delete")
    assert body == {"node": "concept:x", "owner": "user:u-1"}


def test_client_supplied_owner_is_ignored(app):
    """args 의 owner 는 pop 되고 sub 로 덮인다 — 남의 스코프 위조 불가."""
    app.graph_owner_request(_assertion(op="graph", sub="alice"),
                            {"owner": "user:bob", "domain": "d"})
    _, _, params, _ = app.bridge.calls[0]
    assert params["owner"] == "user:alice"        # bob 무시


def test_ingest_fragment_stamps_owner_at_top_level(app):
    app.graph_owner_request(_assertion(op="ingest-fragment"),
                            {"nodes": [{"id": "concept:a", "type": "concept"}], "edges": []})
    method, path, _, body = app.bridge.calls[0]
    assert (method, path) == ("POST", "/ingest/fragment")
    assert body["owner"] == "user:u-1"            # fragment.owner = 강제 owner
    assert body["nodes"][0]["id"] == "concept:a"


def test_activate_strips_client_out_path(app):
    """activate 의 out(호스트 파일경로)은 프록시에서 제거 — 임의 파일쓰기 차단."""
    app.graph_owner_request(_assertion(op="activate"),
                            {"query": "q", "out": "/etc/evil"})
    _, _, _, body = app.bridge.calls[0]
    assert "out" not in body and body["owner"] == "user:u-1"


def test_job_op_validates_id(app):
    app.graph_owner_request(_assertion(op="job"), {"id": "abc123_-", "owner": "user:victim"})
    method, path, params, _ = app.bridge.calls[0]
    assert (method, path) == ("GET", "/jobs/abc123_-")
    # 사이드카가 남의 잡을 없는 잡으로 답하려면 서명된 owner 가 실려야 한다.
    assert params == {"owner": "user:u-1"}


def test_job_op_rejects_path_injection(app):
    for bad in ("../secret", "a/b", "", "x" * 65, "a b"):
        with pytest.raises(ValueError):
            app.graph_owner_request(_assertion(op="job"), {"id": bad})
    assert app.bridge.calls == []


# ── 화이트리스트·거부 ─────────────────────────────────────────────────────────
def test_unknown_op_refused(app):
    for bad in ("themes", "sync", "backup", "types-ratify", "nope", "", None):
        with pytest.raises(ValueError):
            app.graph_owner_request(_assertion(op=bad), {})
    assert app.bridge.calls == [], "거부된 op 는 사이드카로 나가면 안 된다"


def test_rejects_forged_expired_typ_and_iss(app):
    with pytest.raises(PermissionError):          # 공격자 키
        app.graph_owner_request(_assertion(seed=bytes(32)), {})
    with pytest.raises(PermissionError):          # 만료
        app.graph_owner_request(_assertion(iat=1, exp=2), {})
    with pytest.raises(PermissionError):          # typ 위조(purge/aa 를 proxy 자리에)
        app.graph_owner_request(_assertion(typ="purge"), {})
    with pytest.raises(PermissionError):          # iss 위조
        app.graph_owner_request(_assertion(iss="mcp"), {})
    with pytest.raises(ValueError):               # sub 없음
        app.graph_owner_request(_assertion(sub=""), {})
    assert app.bridge.calls == []


def test_disabled_without_account_public():
    app = MCPHTTPApp(_CapBridge(), static_token="t")   # account_public 미핀
    assert not app.graph_enabled()
    with pytest.raises(PermissionError):
        app.graph_owner_request(_assertion(), {})


# ── 감사 M2: 본문 지문과 일회성 id 가 **필수**다 ────────────────────────────────
#
# 이 넷은 예전에 전부 통과했다. `bh`/`jti` 가 없으면 통과시키는 호환 분기가 있었고,
# 계정이 아직 안 보낼 수 있다는 전제 때문이었다. 계정은 bc22584(2026-08-20)부터
# 보내고 이 경로를 부르는 다른 소비자가 없어서 그 전제가 만료됐다.
#
# ⚠ 통과하는 쪽만 시험하면 이 강제는 **조용히 되돌려질 수 있다** — 호환 분기를 되살려도
# 기존 테스트는 전부 초록이기 때문이다. 그래서 거부되는 쪽을 고정한다.

def test_assertion_without_a_body_digest_is_refused(app):
    """`bh` 없는 어서션. 서명은 유효하다 — 거부 사유는 서명이 아니라 결속 부재다."""
    with pytest.raises(PermissionError):
        app.graph_owner_request(_assertion(bh=""), {})
    assert app.bridge.calls == []


def test_assertion_without_a_nonce_is_refused(app):
    with pytest.raises(PermissionError):
        app.graph_owner_request(_assertion(jti=""), {})
    assert app.bridge.calls == []


def test_a_captured_assertion_cannot_be_replayed(app):
    """같은 어서션 두 번. 첫 번째는 정상이고 두 번째가 거부돼야 한다 — 이것이 없으면
    유효 창(120초) 안에서 잡은 어서션을 몇 번이고 다시 쓸 수 있다."""
    token = _assertion(op="search")
    app.graph_owner_request(token, {"query": "x"})
    assert len(app.bridge.calls) == 1
    with pytest.raises(PermissionError):
        app.graph_owner_request(token, {"query": "x"})
    assert len(app.bridge.calls) == 1          # 두 번째는 사이드카에 닿지 않았다


def test_a_body_that_does_not_match_the_digest_is_refused(app):
    """M2 의 실제 공격 모양: 어서션은 진짜인데 **인자만 바꿔치기**한다.

    빈 본문으로 서명된 것을 다른 바이트와 함께 낸다. 서명·typ·iss·aud·exp 가 전부
    유효하므로, 이 검사가 없으면 통과한다."""
    with pytest.raises(PermissionError):
        app.graph_owner_request(_assertion(op="search"), {"query": "x"},
                                raw_body=b'{"query":"attacker chose this"}')
    assert app.bridge.calls == []


def test_an_oversized_body_is_413_not_an_auth_failure(app):
    """상한을 넘는 본문은 읽지 않고 413 이다. 전에는 빈 본문으로 읽혀 지문이 어긋났고,
    그 401 을 계정이 "세션 만료"로 옮겨 로그인된 사람을 로그인 화면으로 보냈다."""
    import http.client
    import threading
    from vinc_mcp.http_server import MAX_BODY, make_server

    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        conn.putrequest("POST", "/internal/graph-owner")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(MAX_BODY + 1))
        conn.putheader("X-Vinc-Assertion", _assertion(op="ingest-pack"))
        conn.endheaders(b"{}")          # 선언한 만큼 보내지 않는다 — 허브는 읽지 않아야 한다
        assert conn.getresponse().status == 413
    finally:
        httpd.shutdown()
    assert app.bridge.calls == []
