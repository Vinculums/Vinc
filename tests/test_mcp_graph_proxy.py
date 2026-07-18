# -*- coding: utf-8 -*-
"""웹 Vinc 그래프 프록시(Phase 1) — MCP /internal/graph-owner 게이트 계약.

purge 와 같은 신뢰 모델(계정 서명·우리가 검증·루프백)이되 read/write op 를 일반화한다.
핵심 불변: **owner 는 서명된 sub 에서만** 강제되고(바디/args owner 무시), op 는
화이트리스트로만, 위조·만료·typ 재사용은 거부. 실엔진 왕복은 SEC-3/e2e 가 담당 —
여기선 게이트·owner 강제·op 라우팅 계약을 직접 본다(SEC-1 교훈).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def _assertion(op="search", sub="u-1", seed=ACCT_SEED, **over):
    now = int(time.time())
    body = {"typ": "proxy", "iss": "account", "aud": "mcp",
            "sub": sub, "op": op, "iat": now, "exp": now + 120}
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
    app.graph_owner_request(_assertion(op="job"), {"id": "abc123_-"})
    method, path, _, _ = app.bridge.calls[0]
    assert (method, path) == ("GET", "/jobs/abc123_-")


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
