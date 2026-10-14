# -*- coding: utf-8 -*-
"""W18 — 읽기 전용 키 스코프 집행. 근거: [[decision:scoped-key-tier]]

주장 네 가지를 못 박는다:
1. **전수, 누락 불가** — 도구 목록을 레지스트리(tools.TOOLS)에서 유도해 순회한다.
   읽기 전용 키로 viewer 등급(주석표 유도) 도구는 전부 성공, 그 밖은 전부 거부이고
   거부는 브리지에 닿지 않는다. 새 도구가 주석 없이 들어오면 admin 등급으로 떨어져
   자동으로 거부측이다(fail-closed).
2. **full 키 회귀 무변화** — 같은 순회를 전권 키로 돌리면 거부가 0 이다.
3. **scope 부재 = 쓰기 거부** — verify_key 가 scope 를 안 실으면 read 로 떨어진다.
   유일한 예외(구 계정 응답 + vinc_sk 접두 = full)는 normalize_scope 가 진다.
4. **캐시가 스코프를 잃지 않는다** — 잃으면 TTL 동안 무음 승격이 된다.

sync 게이트도 여기서 함께 잠근다: push 는 도구 디스패치를 우회하는 쓰기라
/mcp 게이트만으로는 스코프가 무음 전권이 된다.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import entitlement                     # noqa: E402
from vinc_mcp import tools                           # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        if path == "/roles":
            return {"executed": True, "roles": [], "next_offset": None}
        return {"executed": False, "echo": path}


# ── 분류는 레지스트리에서 유도한다(하드코딩 목록 금지) ─────────────────────────
READ_TOOLS = sorted(t["name"] for t in tools.TOOLS
                    if tools.tool_tier(t["name"]) == "viewer")
WRITE_TOOLS = sorted(t["name"] for t in tools.TOOLS
                     if tools.tool_tier(t["name"]) != "viewer")


def test_registry_split_is_total():
    """읽기+쓰기 = 전체. 어느 쪽에도 없는 도구가 생길 수 없다(누락 불가)."""
    assert set(READ_TOOLS) | set(WRITE_TOOLS) == {t["name"] for t in tools.TOOLS}
    assert set(READ_TOOLS) & set(WRITE_TOOLS) == set()
    assert READ_TOOLS and WRITE_TOOLS


def _min_args(name: str) -> dict:
    """inputSchema 의 required 에서 최소 인자를 유도한다 — 레지스트리가 정본."""
    sch = next(t for t in tools.TOOLS if t["name"] == name)["inputSchema"]
    out: dict = {}
    if name == "vinc_community_read":
        return {"space": "community:0198c0de-1111-7222-8333-444455556666",
                "action": "agent_detail", "payload": {"shareId": "0198c0de-2222-7222-8333-444455556666"}}
    if name == "vinc_edges":
        out["node"] = "concept:x"
    for req in sch.get("required", []):
        p = (sch.get("properties") or {}).get(req) or {}
        typ = p.get("type")
        out[req] = {"integer": 1, "number": 1.0, "array": [],
                    "object": {}, "boolean": True}.get(typ, "x")
    return out


# ── HTTP E2E ─────────────────────────────────────────────────────────────────
def _vk(raw, team=None):
    return ({"owner": "user:ro", "plan": "free", "scope": "read"}
            if raw == "vinc_ro_key" else
            {"owner": "user:full", "plan": "free", "scope": "full"}
            if raw == "vinc_sk_key" else None)


class _Srv:
    def __init__(self, app):
        self.httpd = make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def post(self, path, obj, token):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def stop(self):
        self.httpd.shutdown()


def _call(srv, name, token):
    return srv.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name,
                                        "arguments": _min_args(name)}}, token)


def _is_ro_refusal(reply: dict) -> bool:
    parts = ((reply.get("result") or {}).get("content")) or []
    text = " ".join(str(p.get("text") or "") for p in parts)
    return bool((reply.get("result") or {}).get("isError")) \
        and "read-only" in text


@pytest.fixture()
def srv():
    bridge = _StubBridge()
    s = _Srv(MCPHTTPApp(bridge, verify_key=_vk,
        verify_community=lambda subject, space: {"owner": space, "visibility": "public", "role": "reader"},
        community_read=lambda *args, **kwargs: {"executed": True, "read": True}))
    s.bridge = bridge
    yield s
    s.stop()


def test_readonly_key_denies_every_write_tool_before_the_bridge(srv):
    """전수: 쓰기 도구 전부 명시 거부 + 브리지 무접촉(거절은 실행이 아니다)."""
    for name in WRITE_TOOLS:
        status, reply = _call(srv, name, "vinc_ro_key")
        assert status == 200
        assert _is_ro_refusal(reply), f"{name}: 읽기 전용 키가 거부되지 않았다"
    assert srv.bridge.calls == [], "거부된 쓰기가 브리지에 닿았다"


def test_readonly_key_serves_every_read_tool(srv, monkeypatch):
    """전수: 읽기 도구 전부 성공(isError 아님) — 스코프가 읽기를 좁히면 안 된다."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    for name in READ_TOOLS:
        status, reply = _call(srv, name, "vinc_ro_key")
        assert status == 200
        assert not (reply.get("result") or {}).get("isError"), \
            f"{name}: 읽기 전용 키로 실패했다: {reply}"
    # 호출수 정확일치는 걸지 않는다 — 브리지 없이 로컬로 답하는 조회 도구가 있다.
    assert srv.bridge.calls, "읽기 순회가 브리지에 한 번도 닿지 않았다"


def test_full_key_regression_no_tool_is_refused(srv):
    """전수: 전권 키는 게이트에 걸리지 않는다(기존 vinc_sk 동작 무변화)."""
    for name in READ_TOOLS + WRITE_TOOLS:
        status, reply = _call(srv, name, "vinc_sk_key")
        assert status == 200
        assert not _is_ro_refusal(reply), f"{name}: 전권 키가 거부됐다"


def test_readonly_key_can_read_prompts(srv):
    """prompts/* 는 조회다 — 읽기 전용 키로 열려 있어야 한다."""
    status, reply = srv.post("/mcp", {"jsonrpc": "2.0", "id": 1,
                                      "method": "prompts/list"}, "vinc_ro_key")
    assert status == 200
    assert "error" not in reply


def test_sync_push_is_gated_but_pull_is_not(srv):
    """sync push 는 디스패치를 우회하는 쓰기 — 같은 스코프 게이트를 진다."""
    st_push, body = srv.post("/sync/push", {"bundle": {}}, "vinc_ro_key")
    assert st_push == 403 and "read-only" in body["error"]
    st_docs, body2 = srv.post("/sync/docs-push", {"payload": {}}, "vinc_ro_key")
    assert st_docs == 403 and "read-only" in body2["error"]
    st_pull, _ = srv.post("/sync/pull", {"since": 0}, "vinc_ro_key")
    assert st_pull == 200
    st_full, _ = srv.post("/sync/push", {"bundle": {}}, "vinc_sk_key")
    assert st_full == 200


# ── resolve_auth 의 스코프 적재 ───────────────────────────────────────────────
def test_resolve_auth_scope_missing_falls_to_read():
    """verify_key 가 scope 를 안 실으면 read 다(무음 승격 금지)."""
    app = MCPHTTPApp(_StubBridge(),
                     verify_key=lambda raw, team=None: {"owner": "user:u1",
                                                        "plan": "free"})
    assert app.resolve_auth("Bearer vinc_sk_x")["scope"] == "read"


def test_resolve_auth_accepts_ro_prefix():
    """vinc_ro_ 접두가 키 경로로 들어간다(verify_key 에 도달)."""
    seen = {}

    def vk(raw, team=None):
        seen["raw"] = raw
        return {"owner": "user:u1", "plan": "free", "scope": "read"}

    app = MCPHTTPApp(_StubBridge(), verify_key=vk)
    got = app.resolve_auth("Bearer vinc_ro_abc")
    assert got["ok"] and got["scope"] == "read" and seen["raw"] == "vinc_ro_abc"


def test_resolve_auth_static_and_oauthless_scope():
    app = MCPHTTPApp(_StubBridge(), static_token="S")
    assert app.resolve_auth("Bearer S")["scope"] == "full"
    assert app.resolve_auth("Bearer nope")["scope"] is None


# ── entitlement: 정규화와 캐시 ────────────────────────────────────────────────
def test_normalize_scope_full_exact_match_only():
    assert entitlement.normalize_scope("full", "vinc_ro_x") == "full"
    assert entitlement.normalize_scope("read", "vinc_sk_x") == "read"


def test_normalize_scope_absent_is_full_only_for_legacy_sk_prefix():
    """부재를 full 로 읽는 곳은 여기 하나이고, vinc_sk 접두에만 열린다."""
    assert entitlement.normalize_scope(None, "vinc_sk_x") == "full"
    assert entitlement.normalize_scope(None, "vinc_ro_x") == "read"


def test_normalize_scope_unknown_degrades_to_read_but_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="vinc.mcp.entitlement"):
        assert entitlement.normalize_scope("write-plus", "vinc_sk_x") == "read"
    assert any("write-plus" in r.getMessage() for r in caplog.records), \
        "미지 스코프 강등이 조용하면 안 된다"


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return json.dumps(self._b).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _with_account(monkeypatch, responder):
    monkeypatch.setattr(entitlement, "_seed", lambda: b"\x01" * 32)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: responder(req))
    entitlement._cache.clear()


def test_verify_key_carries_scope_from_account(monkeypatch):
    _with_account(monkeypatch,
                  lambda req: _Resp({"sub": "u1", "plan": "free", "scope": "read"}))
    try:
        got = entitlement.verify_key("vinc_ro_k")
        assert got == {"owner": "user:u1", "plan": "free", "scope": "read"}
    finally:
        entitlement._cache.clear()


def test_verify_key_legacy_account_without_scope(monkeypatch):
    """구 계정 응답(scope 없음): vinc_sk 는 full, vinc_ro 는 read 로 정규화."""
    _with_account(monkeypatch, lambda req: _Resp({"sub": "u1", "plan": "free"}))
    try:
        assert entitlement.verify_key("vinc_sk_k")["scope"] == "full"
        entitlement._cache.clear()
        assert entitlement.verify_key("vinc_ro_k")["scope"] == "read"
    finally:
        entitlement._cache.clear()


def test_verify_key_cache_keeps_scope(monkeypatch):
    """캐시 히트가 스코프를 잃으면 TTL 동안 무음 승격 — dict 통째 보존을 잠근다."""
    hits = {"n": 0}

    def responder(req):
        hits["n"] += 1
        return _Resp({"sub": "u1", "plan": "free", "scope": "read"})

    _with_account(monkeypatch, responder)
    try:
        first = entitlement.verify_key("vinc_ro_k")
        second = entitlement.verify_key("vinc_ro_k")
        assert hits["n"] == 1, "두 번째 호출은 캐시여야 한다"
        assert second == first and second["scope"] == "read"
    finally:
        entitlement._cache.clear()


def test_verify_key_team_answer_carries_scope(monkeypatch):
    _with_account(monkeypatch,
                  lambda req: _Resp({"sub": "u1", "plan": "team", "scope": "read",
                                     "graph_owner": "team:t1", "role": "editor"}))
    try:
        got = entitlement.verify_key("vinc_ro_k", "t1")
        assert got["owner"] == "team:t1" and got["scope"] == "read"
    finally:
        entitlement._cache.clear()


def test_sync_pull_all_flag_reaches_the_sidecar():
    """모드 전환의 전량 받기(VIN-130). 허브가 `all` 을 안 넘기면 주제 없는 노드와 순번 0 인
    옛 문서가 계정에서 기기로 조용히 안 온다."""
    bridge = _StubBridge()
    s = _Srv(MCPHTTPApp(bridge, verify_key=_vk))
    try:
        s.post("/sync/pull", {"since": 0, "all": True}, "vinc_sk_key")
        s.post("/sync/docs-pull", {"since": 0, "all": True}, "vinc_sk_key")
        s.post("/sync/pull", {"since": 0}, "vinc_sk_key")
    finally:
        s.stop()
    got = [(path, (params or {}).get("all")) for _, path, params, _ in bridge.calls]
    assert got == [("/sync/pull-delta", "1"), ("/sync/docs-export", "1"),
                   ("/sync/pull-delta", None)]
