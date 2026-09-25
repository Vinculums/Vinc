# -*- coding: utf-8 -*-
"""ChatGPT Apps/Plugins 디렉터리 제출 요건.

정본은 라이브 문서(2026-09-22)와 vinc.plan.mcp-directory-listings.
여기는 허브가 그 스캔을 통과하기 위해 코드로 지켜야 하는 것만 고정한다.
사람 쪽(신원 확인·심사 계정·포털 서류)은 테스트가 못 한다.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import rpc, tools                         # noqa: E402
from vinc_mcp.bridge import Bridge                      # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256             # noqa: E402
from vinc_mcp.store import Store                        # noqa: E402


class _StubBridge(Bridge):
    def __init__(self, payload=None):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.payload = payload if payload is not None else {"ok": True}
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        if callable(self.payload):
            return self.payload(method, path, params, body)
        return self.payload


def _cimd(url: str) -> dict:
    assert url == "https://chatgpt.com/oauth/client.json"
    return {"client_id": url, "client_name": "ChatGPT",
            "redirect_uris": ["https://chatgpt.com/connector/oauth/callback"],
            "token_endpoint_auth_methods_supported": ["none"]}


def test_hub_lists_search_and_fetch_desktop_does_not():
    desktop = {s["name"] for s in tools.specs()}
    hub = {s["name"] for s in tools.specs(allow_target=False)}
    assert "search" not in desktop and "fetch" not in desktop
    assert {"search", "fetch"} <= hub
    search = next(s for s in tools.specs(allow_target=False) if s["name"] == "search")
    fetch = next(s for s in tools.specs(allow_target=False) if s["name"] == "fetch")
    for spec in (search, fetch):
        ann = spec["annotations"]
        assert ann["readOnlyHint"] is True
        assert ann["openWorldHint"] is False
        assert ann["destructiveHint"] is False
        assert "outputSchema" in spec


def test_search_maps_rows_and_nodes_to_citable_results():
    # 제목은 문서 메타(doc_title)다 — 발췌 첫 줄이 아니다(감사 §5 #19: "```mermaid …"
    # 와 "--- doc_id: …" 가 인용 제목으로 나갔다).
    payload = {
        "rows": [{"doc_id": "vinc.plan.x", "doc_title": "Listing plan",
                  "snippet": "```mermaid flowchart TB\nmore"},
                 {"doc_id": "vinc.plan.x", "snippet": "duplicate"},
                 {"doc_id": "", "snippet": "no id"}],
        "node_hits": [{"id": "concept:vinc-mcp", "title": "Vinc MCP server"}],
    }
    out = tools.call("search", {"query": "chatgpt"}, _StubBridge(payload),
                     allow_target=False)
    assert [r["id"] for r in out["results"]] == ["doc:vinc.plan.x", "concept:vinc-mcp"]
    assert out["results"][0]["title"] == "Listing plan"
    assert out["results"][1]["title"] == "Vinc MCP server"
    assert out["results"][0]["url"] == "https://vincs.io/app/?space=personal#view/doc/vinc.plan.x"
    assert out["results"][1]["url"] == "https://vincs.io/app/?space=personal#view/trace/concept:vinc-mcp"


def test_search_without_a_doc_title_falls_back_to_the_id_not_the_snippet():
    payload = {"rows": [{"doc_id": "vinc.canon.mcp-contract",
                         "snippet": "--- doc_id: vinc.canon.mcp-contract layer: canon"}],
               "node_hits": []}
    out = tools.call("search", {"query": "MCP contract"}, _StubBridge(payload),
                     allow_target=False)
    assert out["results"][0]["title"] == "vinc.canon.mcp-contract"


@pytest.mark.parametrize("limit", [1, 3, 20])
def test_search_limit_counts_documents_and_nodes_together(limit):
    # 감사 재현: limit 20 이 문서 20 + 노드 14 = 34 건이 됐다.
    rows = [{"doc_id": f"d{i}", "doc_title": f"D{i}", "snippet": "s"} for i in range(20)]
    hits = [{"id": f"concept:c{i}", "title": f"C{i}"} for i in range(14)]
    b = _StubBridge({"rows": rows, "node_hits": hits})
    out = tools.call("search", {"query": "x", "limit": limit}, b, allow_target=False)
    assert len(out["results"]) == limit
    assert b.calls[0][2]["top_k"] == limit
    if limit >= 2:
        # 번갈아 채운다 — 한쪽이 상한을 다 먹어 다른 쪽이 통째로 빠지지 않는다.
        assert [r["id"] for r in out["results"][:2]] == ["doc:d0", "concept:c0"]


def test_search_never_emits_ids_fetch_cannot_open():
    # fetch 는 노드를 brief 로 여는데 brief 는 domain:·bridge: 를 못 연다(P1).
    hits = [{"id": "domain:vinc/engineering", "title": "engineering"},
            {"id": "bridge:x", "title": "Bridge"},
            {"id": "record:r", "title": "R"},
            {"id": "decision:d", "title": "D"},
            {"id": "episode:e", "title": "E"},
            {"id": "concept:c", "title": "C"}]
    out = tools.call("search", {"query": "x"}, _StubBridge({"rows": [], "node_hits": hits}),
                     allow_target=False)
    ids = [r["id"] for r in out["results"]]
    assert ids == ["record:r", "decision:d", "episode:e", "concept:c"]
    for r in out["results"]:
        assert set(r) == {"id", "title", "url"}


def test_fetch_reads_a_document_and_a_node():
    def _payload(method, path, params, body):
        if path == "/doc":
            return {"title": "Plan", "text": "body", "verified": True}
        if path == "/brief":
            return {"status": "resolved", "one_liner": "A connector.",
                    "node": {"id": "concept:vinc-mcp", "title": "Vinc MCP server",
                             "kind": "concept"},
                    "excerpts": [{"excerpt": "quoted"}]}
        return {}

    b = _StubBridge(_payload)
    doc = tools.call("fetch", {"id": "doc:vinc.plan.x"}, b, allow_target=False)
    assert doc["id"] == "doc:vinc.plan.x"
    assert doc["title"] == "Plan" and doc["text"] == "body"
    assert doc["url"] == "https://vincs.io/app/?space=personal#view/doc/vinc.plan.x"
    node = tools.call("fetch", {"id": "concept:vinc-mcp"}, b, allow_target=False)
    assert node["title"] == "Vinc MCP server"
    assert "A connector." in node["text"] and "quoted" in node["text"]
    assert node["url"] == "https://vincs.io/app/?space=personal#view/trace/concept:vinc-mcp"


def _fetch_missing(method, path, params, body):
    if path == "/doc":
        return {"kind": "doc.get", "executed": False, "doc_id": params["id"],
                "error": "unknown doc_id", "hint": "doc_id must match exactly."}
    if path == "/brief":
        # 없는 id 의 brief 는 none + unreadable 이다(랭킹 대체 없음).
        return {"status": "none", "executed": True, "candidates": [],
                "unreadable": [{"id": params["query"],
                                "note": "unknown node: " + params["query"]}]}
    return {}


@pytest.mark.parametrize("ident", ["doc:vinc.plan.nope", "gabia.cloud-docs.user-guide",
                                   "concept:does-not-exist-audit-probe"])
def test_fetch_of_a_missing_id_is_a_tool_error_not_an_empty_text(ident):
    """감사 §5 결함 10: 없는 id 에 text:"" 인 성공을 주었고, 딥리서치 호스트는
    그것을 인용 가능한 빈 문서로 읽었다. 이제 rpc 가 isError:true 로 내린다."""
    d = rpc.Dispatcher(_StubBridge(_fetch_missing), allow_target=False)
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "fetch", "arguments": {"id": ident}}})
    assert "error" not in r, r                      # 프로토콜 오류가 아니라 도구 오류
    assert r["result"]["isError"] is True
    assert "structuredContent" not in r["result"]
    text = r["result"]["content"][0]["text"]
    assert text.startswith("NotFound:") and ident in text


def test_fetch_does_not_answer_a_node_id_with_another_node():
    """brief 가 다른 노드를 resolved 로 내더라도 fetch 는 그것을 그 id 의 본문으로
    내보내지 않는다."""
    b = _StubBridge({"status": "resolved", "one_liner": "Other.",
                     "node": {"id": "concept:other", "title": "Other"}})
    with pytest.raises(tools.NotFound):
        tools.call("fetch", {"id": "concept:asked"}, b, allow_target=False)


def test_search_and_fetch_carry_structured_content():
    d = rpc.Dispatcher(
        _StubBridge({"rows": [{"doc_id": "d1", "snippet": "T"}], "node_hits": []}),
        allow_target=False)
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "search", "arguments": {"query": "x"}}})
    assert r["result"]["isError"] is False
    body = json.loads(r["result"]["content"][0]["text"])
    assert r["result"]["structuredContent"] == body
    assert body["results"][0]["id"] == "doc:d1"


def test_as_metadata_advertises_cimd():
    meta = OAuthGate("https://mcp.example", None).as_metadata()
    assert meta["client_id_metadata_document_supported"] is True
    assert "none" in meta["token_endpoint_auth_methods_supported"]


def test_cimd_authorizes_without_registering_a_client():
    gate = OAuthGate("https://mcp.example", "adminkey", store=Store(":memory:"),
                     cimd_fetch=_cimd)
    loc = gate.authorize({
        "client_id": "https://chatgpt.com/oauth/client.json",
        "redirect_uri": "https://chatgpt.com/connector/oauth/callback",
        "response_type": "code",
        "code_challenge": _s256("v" * 48),
        "code_challenge_method": "S256",
        "key": "adminkey",
        "resource": "https://mcp.example/mcp",
    })
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    tok = gate.token({
        "grant_type": "authorization_code",
        "code": qs["code"][0],
        "client_id": "https://chatgpt.com/oauth/client.json",
        "redirect_uri": "https://chatgpt.com/connector/oauth/callback",
        "code_verifier": "v" * 48,
        "resource": "https://mcp.example/mcp",
    })
    assert tok["resource"] == "https://mcp.example/mcp"
    rec = gate.resolve(tok["access_token"])
    assert rec["resource"] == "https://mcp.example/mcp"
    assert gate.store.get_client("https://chatgpt.com/oauth/client.json") is None


def test_cimd_rejects_a_redirect_that_is_not_in_the_document():
    gate = OAuthGate("https://mcp.example", "adminkey", store=Store(":memory:"),
                     cimd_fetch=_cimd)
    with pytest.raises(ValueError, match="redirect_uri"):
        gate.authorize({
            "client_id": "https://chatgpt.com/oauth/client.json",
            "redirect_uri": "https://evil.example/cb",
            "response_type": "code",
            "code_challenge": _s256("v" * 48),
            "code_challenge_method": "S256",
            "key": "adminkey",
        })


def test_cimd_rejects_http_client_ids():
    with pytest.raises(ValueError, match="https"):
        from vinc_mcp.oauth import fetch_cimd
        fetch_cimd("http://chatgpt.com/oauth/client.json")


def test_resource_mismatch_is_rejected_at_token_exchange():
    gate = OAuthGate("https://mcp.example", "adminkey", store=Store(":memory:"))
    reg = gate.register({"redirect_uris": ["https://claude.ai/cb"]})
    loc = gate.authorize({
        "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb",
        "response_type": "code", "code_challenge": _s256("v" * 48),
        "code_challenge_method": "S256", "key": "adminkey",
        "resource": "https://mcp.example/mcp",
    })
    code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
    with pytest.raises(ValueError, match="resource"):
        gate.token({
            "grant_type": "authorization_code", "code": code,
            "client_id": reg["client_id"], "redirect_uri": "https://claude.ai/cb",
            "code_verifier": "v" * 48,
            "resource": "https://other.example/mcp",
        })


def test_legacy_token_without_resource_still_works():
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    gate.store.add_token("old", "cid", "user:u1", "free", exp=9e9)
    rec = gate.resolve("old")
    assert rec["owner"] == "user:u1"
    assert not rec.get("resource")
    app = MCPHTTPApp(_StubBridge(), oauth=gate)
    got = app.resolve_auth("Bearer old")
    assert got["ok"] and got["owner"] == "user:u1"


def test_bound_resource_must_match_this_server():
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    gate.store.add_token("tok", "cid", "user:u1", "free", exp=9e9,
                         resource="https://other.example/mcp")
    app = MCPHTTPApp(_StubBridge(), oauth=gate)
    assert app.resolve_auth("Bearer tok")["ok"] is False
    gate.store.add_token("oktok", "cid", "user:u1", "free", exp=9e9,
                         resource="https://mcp.example/mcp")
    assert app.resolve_auth("Bearer oktok")["ok"] is True


def test_a_personal_token_can_still_name_a_team_with_the_space_argument():
    """2026-09-22 회귀. `resource` 검사가 **도착한 주소**가 아니라 `space` 인자와
    비교하고 있어서, 개인 주소(`/mcp`)로 붙은 커넥터가 `space` 로 팀을 고르면
    기대값이 `/t/<uuid>/mcp` 로 잡혀 팀 호출이 전부 "no such space" 였다.

    증상이 고약했던 것은 `vinc_whoami` 가 계정에 묻는 다른 경로라 팀 목록을 계속
    보여 줬다는 점이다. 그리고 정적 키는 이 분기를 안 지나서 멀쩡했다 — 그 대비가
    원인을 갈랐다. 스페이스는 주소가 아니라 인자다
    (decision:the-space-is-an-argument-and-the-default-is-personal)."""
    team = "01a074fe-5709-7735-b2a3-56228be26497"
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    gate.store.add_token("tok", "cid", "user:u1", "free", exp=9e9,
                         resource="https://mcp.example/mcp")
    app = MCPHTTPApp(_StubBridge(), oauth=gate,
                     verify_member=lambda sub, sp: {"owner": f"team:{sp}",
                                                    "role": "admin", "member": sub})
    # 인자로 고른 팀: 요청은 개인 끝점에 도착했다. 기본 presented_at 이 그것이다.
    got = app.resolve_auth("Bearer tok", team)
    assert got["ok"] is True
    assert got["owner"] == f"team:{team}"
    assert got["role"] == "admin"


def test_a_team_address_token_is_refused_at_a_different_team_address():
    """반대쪽도 못 박는다. 주소로 붙은 토큰은 **그 주소**에만 제시할 수 있다 —
    안 그러면 audience 바인딩이 이름만 남는다."""
    a = "01a074fe-5709-7735-b2a3-56228be26497"
    b = "01a05206-553c-7cad-97e7-69d6230c12b7"
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    gate.store.add_token("tok", "cid", "user:u1", "free", exp=9e9,
                         resource=f"https://mcp.example/t/{a}/mcp")
    app = MCPHTTPApp(_StubBridge(), oauth=gate,
                     verify_member=lambda sub, sp: {"owner": f"team:{sp}",
                                                    "role": "admin", "member": sub})
    ok = app.resolve_auth("Bearer tok", a, presented_at=f"/t/{a}/mcp")
    assert ok["ok"] is True
    bad = app.resolve_auth("Bearer tok", b, presented_at=f"/t/{b}/mcp")
    assert bad["ok"] is False
    # 그 토큰은 개인 끝점에도 못 온다.
    assert app.resolve_auth("Bearer tok")["ok"] is False


def test_the_space_argument_reaches_a_team_through_the_real_http_path():
    """⚠ 앞의 두 테스트는 `resolve_auth` 를 **직접** 부른다. 그것만으로는 부족하다.

    2026-09-22 에 그 둘이 통과하는 채로 고침이 반쪽이었다: `_Handler._auth` 가
    `presented_at` 을 `space` 에서 유도하고 있어서, 인자로 고른 팀은 실제 HTTP 경로에서
    여전히 거절됐다. 직접 호출은 그 래퍼를 안 지나므로 초록이었다. 그래서 여기서는
    **소켓으로 tools/call 을 쏜다** — `_auth_for` 와 `_Handler._auth` 를 실제로 태운다.
    """
    team = "01a074fe-5709-7735-b2a3-56228be26497"
    gate = OAuthGate("https://mcp.example", None, store=Store(":memory:"))
    gate.store.add_token("tok", "cid", "user:u1", "free", exp=9e9,
                         resource="https://mcp.example/mcp")
    app = MCPHTTPApp(_StubBridge(), oauth=gate,
                     verify_member=lambda sub, sp: {"owner": f"team:{sp}",
                                                    "role": "admin", "member": sub})
    httpd = make_server(app, "127.0.0.1", 0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "vinc_whoami",
                                      "arguments": {"space": team}}}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp", data=body, method="POST",
            headers={"Authorization": "Bearer tok", "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
        # SSE 봉투일 수도 있다. 마지막 JSON 한 줄만 본다.
        line = [l for l in raw.replace("data: ", "").splitlines() if l.strip().startswith("{")][-1]
        got = json.loads(line)
        assert "no such space" not in json.dumps(got), got
    finally:
        httpd.shutdown()


class _Srv:
    def __init__(self, env=None):
        self._old = os.environ.get("VINC_OPENAI_APPS_CHALLENGE")
        if env is None:
            os.environ.pop("VINC_OPENAI_APPS_CHALLENGE", None)
        else:
            os.environ["VINC_OPENAI_APPS_CHALLENGE"] = env
        app = MCPHTTPApp(_StubBridge(), static_token="tok",
                         oauth=OAuthGate("https://mcp.example", None))
        self.httpd = make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def stop(self):
        self.httpd.shutdown()
        if self._old is None:
            os.environ.pop("VINC_OPENAI_APPS_CHALLENGE", None)
        else:
            os.environ["VINC_OPENAI_APPS_CHALLENGE"] = self._old


def test_openai_apps_challenge_is_missing_until_configured():
    s = _Srv(None)
    try:
        code, ctype, body = s.get("/.well-known/openai-apps-challenge")
        assert code == 404
        assert "json" in (ctype or "")
        assert b"not configured" in body
    finally:
        s.stop()


def test_openai_apps_challenge_returns_the_exact_token():
    s = _Srv("challenge-token-xyz")
    try:
        code, ctype, body = s.get("/.well-known/openai-apps-challenge")
        assert code == 200
        assert ctype.startswith("text/plain")
        assert body == b"challenge-token-xyz"
    finally:
        s.stop()
