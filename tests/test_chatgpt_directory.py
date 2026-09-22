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
    payload = {
        "rows": [{"doc_id": "vinc.plan.x", "snippet": "Listing the Vinc MCP server\nmore"},
                 {"doc_id": "vinc.plan.x", "snippet": "duplicate"},
                 {"doc_id": "", "snippet": "no id"}],
        "node_hits": [{"id": "concept:vinc-mcp", "title": "Vinc MCP server"}],
    }
    out = tools.call("search", {"query": "chatgpt"}, _StubBridge(payload),
                     allow_target=False)
    assert [r["id"] for r in out["results"]] == ["doc:vinc.plan.x", "concept:vinc-mcp"]
    assert out["results"][0]["title"] == "Listing the Vinc MCP server"
    assert out["results"][0]["url"] == "https://vincs.io/#view/doc/vinc.plan.x"
    assert out["results"][1]["url"] == "https://vincs.io/#view/trace/concept:vinc-mcp"


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
    assert doc["url"] == "https://vincs.io/#view/doc/vinc.plan.x"
    node = tools.call("fetch", {"id": "concept:vinc-mcp"}, b, allow_target=False)
    assert node["title"] == "Vinc MCP server"
    assert "A connector." in node["text"] and "quoted" in node["text"]
    assert node["url"] == "https://vincs.io/#view/trace/concept:vinc-mcp"


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
