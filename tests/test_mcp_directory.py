# -*- coding: utf-8 -*-
"""Anthropic 원격 MCP 디렉토리 제출 요건 회귀.

가이드(support.claude.com — 원격 MCP 서버 제출 가이드)가 **거부 사유**로 명시한
항목들을 코드로 고정한다. 특히 안전 주석은 "선택 사항이 아님"이라 누락 = 거부.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import rpc, tools                      # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402

_READ_ONLY = {"vinc_search", "vinc_recall", "vinc_graph", "vinc_decide",
              "vinc_domains", "vinc_suggest_ties"}
_IRREVERSIBLE = {"vinc_delete", "vinc_merge_nodes"}


class _StubBridge(Bridge):
    def __init__(self, payload=None):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self._payload = payload if payload is not None else {"ok": True}

    def request(self, method, path, params=None, body=None, timeout=60):
        return self._payload


# ── 안전 주석 (필수 — 누락 시 거부) ──────────────────────────────────────────
def test_every_tool_carries_annotations():
    for spec in tools.specs():
        ann = spec.get("annotations")
        assert ann, f"{spec['name']} 에 annotations 없음 — 디렉토리 거부 사유"
        assert ann.get("title"), f"{spec['name']} 에 title 없음"
        assert "readOnlyHint" in ann, spec["name"]


def test_read_tools_are_marked_read_only():
    ann = {s["name"]: s["annotations"] for s in tools.specs()}
    for name in _READ_ONLY:
        assert ann[name]["readOnlyHint"] is True, name


def test_irreversible_tools_are_marked_destructive():
    """vinc_delete·vinc_merge_nodes 는 DETACH DELETE — 호스트가 반드시 확인받게."""
    ann = {s["name"]: s["annotations"] for s in tools.specs()}
    for name in _IRREVERSIBLE:
        assert ann[name]["readOnlyHint"] is False, name
        assert ann[name]["destructiveHint"] is True, name


def test_ingest_is_write_but_not_destructive():
    """가산적 exact-id MERGE — destructiveHint 는 스펙상 기본 true 라 명시 false 필요."""
    ann = {s["name"]: s["annotations"] for s in tools.specs()}["vinc_ingest"]
    assert ann["readOnlyHint"] is False
    assert ann["destructiveHint"] is False
    assert ann["idempotentHint"] is True


def test_annotations_survive_tools_list():
    d = rpc.Dispatcher(_StubBridge())
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    for t in r["result"]["tools"]:
        assert t.get("annotations", {}).get("title"), t["name"]


# ── 결과 크기 상한 (25k 토큰 요건) ───────────────────────────────────────────
def test_oversized_result_degrades_to_actionable_error():
    big = {"rows": ["x" * 100 for _ in range(2000)]}     # 상한 초과
    d = rpc.Dispatcher(_StubBridge(big))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph",
                             "arguments": {"seed_id": "concept:x"}}})
    res = r["result"]
    assert res["isError"] is True
    text = res["content"][0]["text"]
    assert "Result too large" in text
    assert "depth" in text                      # 어떻게 줄일지 알려준다
    assert res["_meta"]["vinc_too_large"]["tool"] == "vinc_graph"


def test_normal_result_is_not_touched():
    d = rpc.Dispatcher(_StubBridge({"rows": [1, 2, 3]}))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_domains", "arguments": {}}})
    assert r["result"]["isError"] is False
    assert json.loads(r["result"]["content"][0]["text"]) == {"rows": [1, 2, 3]}


# ── CORS (브라우저 클라이언트 요건) ───────────────────────────────────────────
class _Srv:
    def __init__(self):
        app = MCPHTTPApp(_StubBridge(), static_token="tok")
        self.httpd = make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def preflight(self, origin):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp", method="OPTIONS",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers)

    def stop(self):
        self.httpd.shutdown()


def test_cors_preflight_allows_claude_origins():
    s = _Srv()
    try:
        for origin in ("https://claude.ai", "https://claude.com",
                       "http://localhost:6274"):
            status, h = s.preflight(origin)
            assert status == 204, origin
            assert h.get("Access-Control-Allow-Origin") == origin, origin
            assert "Authorization" in h.get("Access-Control-Allow-Headers", ""), origin
    finally:
        s.stop()


def test_cors_does_not_reflect_unknown_origins():
    """와일드카드·무차별 반사 금지 — 허용 목록에 없으면 CORS 헤더 자체가 없다."""
    s = _Srv()
    try:
        status, h = s.preflight("https://evil.example")
        assert status == 204
        assert "Access-Control-Allow-Origin" not in h
    finally:
        s.stop()
