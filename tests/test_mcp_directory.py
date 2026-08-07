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


# ── 위험 감사 5.5 · 5.1 — 계약과 실동작의 불일치 회귀 ────────────────────────
def test_delete_tools_declare_owner_in_schema():
    """삭제 3종은 핸들러가 owner 를 읽는다 — 계약에도 있어야 한다(감사 5.5).

    없던 동안에는 호스트가 스코프를 지정할 방법이 없었고, 문서만 읽고 안전을
    판단할 수도 없었다."""
    by_name = {s["name"]: s for s in tools.specs()}
    for name in ("vinc_delete", "vinc_delete_edge", "vinc_merge_nodes"):
        props = by_name[name]["inputSchema"]["properties"]
        assert "owner" in props, f"{name} 스키마에 owner 가 없다"


def test_delete_tools_send_an_owner_scope():
    """로컬 stdio 도 무스코프 삭제를 하지 않는다(감사 5.5).

    예전엔 args 에 owner 가 없고 forced_owner 도 없어 owner=None 이 그대로
    사이드카로 갔다 = 스코프 없는 DETACH DELETE."""
    sent: list[dict] = []

    class _B(_StubBridge):
        def request(self, method, path, params=None, body=None, timeout=60):
            sent.append(body or {})
            return {"ok": True}

    b = _B()
    for name, args in (("vinc_delete", {"node_id": "concept:x"}),
                       ("vinc_delete_edge", {"from_id": "concept:a",
                                             "to_id": "concept:b",
                                             "type": "relates_to"}),
                       ("vinc_merge_nodes", {"keep_id": "concept:a",
                                             "drop_id": "concept:b"})):
        sent.clear()
        tools.call(name, args, b)
        assert sent[0].get("owner") == b.owner, f"{name} 이 owner 를 안 실었다"

    # 원격(forced_owner)은 클라이언트가 보낸 owner 를 덮는다 — 멀티테넌트 봉쇄.
    sent.clear()
    tools.call("vinc_delete", {"node_id": "concept:x", "owner": "user:victim"},
               b, forced_owner="user:alice")
    assert sent[0]["owner"] == "user:alice"


def test_hub_profile_hides_and_rejects_target():
    """허브는 이미 원격 그래프다 — target 을 내보내지도 받지도 않는다(감사 5.5)."""
    hub = tools.specs(allow_target=False)
    assert all("target" not in s["inputSchema"]["properties"] for s in hub)
    # 데스크탑 stdio 는 그대로 갖는다(기본값 유지).
    assert all("target" in s["inputSchema"]["properties"] for s in tools.specs())

    # 스키마에서 뺐어도 클라이언트는 보낼 수 있다 — 실행 경로에서도 막는다.
    try:
        tools.call("vinc_search", {"query": "x", "target": "remote"},
                   _StubBridge(), allow_target=False)
    except ValueError as exc:
        assert "target" in str(exc)
    else:
        raise AssertionError("허브 프로파일이 target:remote 를 받아들였다")


def test_bridge_does_not_retry_writes_that_may_have_landed():
    """읽기 타임아웃이 쓰기를 중복 전송하지 않는다(감사 5.1).

    브리지 타임아웃 60초 대 잡 타임아웃 600초라, 큰 doc.put·ingest 는 사이드카가
    아직 일하는 중에 끊긴다. 예전 `except Exception` 은 그것을 두 번 더 보냈다."""
    from vinc_mcp import bridge as br

    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise TimeoutError("read timed out")

    b = Bridge(base="http://127.0.0.1:1", token="t")
    orig = br.urllib.request.urlopen
    br.urllib.request.urlopen = _boom
    try:
        res = b.request("POST", "/ingest/fragment", body={"nodes": []},
                        retries=2, retry_delay=0)
        assert calls["n"] == 1, f"쓰기를 {calls['n']}번 보냈다"
        assert res.get("retryable") is False and "unconfirmed" in res["error"]

        # GET 은 멱등이라 blip 흡수를 계속 한다(원래 재시도의 목적).
        calls["n"] = 0
        try:
            b.request("GET", "/search", params={"query": "x"},
                      retries=2, retry_delay=0)
        except TimeoutError:
            pass
        assert calls["n"] == 3, f"GET 재시도가 {calls['n']}회"

        # 연결 거부는 요청이 닿지 않은 것이 확실하다 — 쓰기도 재시도 안전.
        calls["n"] = 0

        def _refused(*_a, **_k):
            calls["n"] += 1
            raise ConnectionRefusedError("nothing listening")

        br.urllib.request.urlopen = _refused
        try:
            b.request("POST", "/delete", body={"node": "concept:x"},
                      retries=2, retry_delay=0)
        except ConnectionRefusedError:
            pass
        assert calls["n"] == 3, f"연결 거부 재시도가 {calls['n']}회"
    finally:
        br.urllib.request.urlopen = orig
