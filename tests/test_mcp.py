# -*- coding: utf-8 -*-
"""vinc_mcp — D11 도구 계약·JSON-RPC 디스패처·HTTP 전송·OAuth 게이트."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.install import install_desktop, uninstall_desktop  # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256  # noqa: E402
from vinc_mcp.rpc import Dispatcher  # noqa: E402

D11_TOOLS = {"vinc_search", "vinc_recall", "vinc_graph", "vinc_decide",
             "vinc_domains", "vinc_suggest_ties", "vinc_ingest",
             # T3c — 오염 정리 쓰기 표면(비가역).
             "vinc_delete", "vinc_merge_nodes"}


class _StubBridge(Bridge):
    """사이드카 없이 호출 궤적만 기록하는 브리지."""

    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": False, "echo": path}


class ToolContractTests(unittest.TestCase):
    def test_tool_names_match_d11(self):
        self.assertEqual({t["name"] for t in tools.specs()}, D11_TOOLS)

    def test_specs_have_schema_and_description(self):
        for t in tools.specs():
            self.assertTrue(t["description"], t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])

    def test_write_surfaces_are_explicit(self):
        # 구 계약(쓰기 표면 = ingest 단독)은 T3c 로 확장 — 쓰기 표면은 여전히
        # **명시된 3개뿐**(ingest + 오염 정리용 delete/merge, 둘 다 비가역)이어야 한다.
        b = _StubBridge()
        for t in tools.specs():
            args = {"query": "x", "seed_id": "concept:x", "topic": "x",
                    "node": "concept:x", "nodes": [{"id": "c:x", "type": "concept"}],
                    "node_id": "concept:x", "keep_id": "concept:x", "drop_id": "concept:y"}
            tools.call(t["name"], args, b)
        writes = sorted({c[1] for c in b.calls if c[0] == "POST"})
        self.assertEqual(writes, ["/delete", "/ingest/fragment", "/merge-nodes"])

    def test_owner_default_injected(self):
        b = _StubBridge()
        tools.call("vinc_domains", {}, b)
        self.assertEqual(b.calls[0][2]["owner"], "user:test")
        tools.call("vinc_recall", {"owner": "user:alice"}, b)
        self.assertEqual(b.calls[1][2]["owner"], "user:alice")

    def test_required_argument_enforced(self):
        with self.assertRaises(ValueError):
            tools.call("vinc_search", {}, _StubBridge())
        with self.assertRaises(KeyError):
            tools.call("vinc_nope", {}, _StubBridge())

    def test_ingest_stamps_owner(self):
        b = _StubBridge()
        tools.call("vinc_ingest", {"nodes": [{"id": "c:x", "type": "concept"}]}, b)
        self.assertEqual(b.calls[0][3]["owner"], "user:test")


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.d = Dispatcher(_StubBridge())

    def _rpc(self, method, params=None, msg_id=1):
        return self.d.handle({"jsonrpc": "2.0", "id": msg_id,
                              "method": method, "params": params or {}})

    def test_initialize_echoes_known_version(self):
        r = self._rpc("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_initialize_falls_back_on_unknown_version(self):
        r = self._rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")

    def test_notification_returns_none(self):
        self.assertIsNone(self.d.handle({"jsonrpc": "2.0",
                                         "method": "notifications/initialized"}))
        self.assertTrue(self.d.initialized)

    def test_tools_list_and_call(self):
        r = self._rpc("tools/list")
        self.assertEqual({t["name"] for t in r["result"]["tools"]}, D11_TOOLS)
        r = self._rpc("tools/call", {"name": "vinc_search", "arguments": {"query": "x"}})
        self.assertFalse(r["result"]["isError"])
        payload = json.loads(r["result"]["content"][0]["text"])
        self.assertEqual(payload["echo"], "/search")

    def test_unknown_method_is_32601(self):
        r = self._rpc("resources/list")
        self.assertEqual(r["error"]["code"], -32601)

    def test_bad_arguments_is_32602(self):
        r = self._rpc("tools/call", {"name": "vinc_search", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_tool_runtime_failure_becomes_isError(self):
        real = Bridge(base="http://127.0.0.1:9", token="t")   # 닫힌 포트
        r = Dispatcher(real).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "vinc_search",
                                                "arguments": {"query": "x"}}})
        self.assertTrue(r["result"]["isError"])


class _HTTPFixture(unittest.TestCase):
    oauth = None
    static = "statictok"

    def setUp(self):
        self.app = MCPHTTPApp(_StubBridge(), static_token=self.static, oauth=self.oauth)
        self.httpd = make_server(self.app, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, path, body, token=None, form=False):
        if form:
            data = urllib.parse.urlencode(body).encode()
            ctype = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            ctype = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        req.add_header("Content-Type", ctype)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


class HTTPTransportTests(_HTTPFixture):
    def test_mcp_requires_bearer(self):
        code, obj = self._post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(code, 401)

    def test_static_token_round_trip(self):
        code, obj = self._post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               token=self.static)
        self.assertEqual(code, 200)
        self.assertEqual({t["name"] for t in obj["result"]["tools"]}, D11_TOOLS)

    def test_notification_gets_202(self):
        req = urllib.request.Request(
            self.base + "/mcp",
            data=json.dumps({"jsonrpc": "2.0",
                             "method": "notifications/initialized"}).encode(),
            method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.static}")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 202)

    def test_get_mcp_is_405(self):
        try:
            urllib.request.urlopen(self.base + "/mcp", timeout=5)
            self.fail("expected 405")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)

    def test_no_auth_config_refused(self):
        with self.assertRaises(ValueError):
            MCPHTTPApp(_StubBridge())


class OAuthFlowTests(_HTTPFixture):
    static = None

    def setUp(self):
        self.oauth = OAuthGate("http://issuer.test", admin_key="adminkey")
        super().setUp()

    def _full_flow(self, key="adminkey"):
        _, reg = self._post("/register", {"redirect_uris": ["http://127.0.0.1:7777/cb"],
                                          "client_name": "t"})
        verifier = "v" * 48
        challenge = _s256(verifier)
        q = urllib.parse.urlencode({
            "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "s1", "key": key})
        # redirect 를 따라가지 않도록 저수준 opener
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(self.base + "/authorize?" + q, timeout=5)
            status, location = resp.status, resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            status, location = e.code, e.headers.get("Location")
        if status != 302:
            return status, None
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]
        return self._post("/token", {
            "grant_type": "authorization_code", "code": code,
            "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
            "code_verifier": verifier}, form=True)

    def test_pkce_code_flow_issues_usable_bearer(self):
        code, tok = self._full_flow()
        self.assertEqual(code, 200)
        self.assertEqual(tok["token_type"], "Bearer")
        status, obj = self._post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 token=tok["access_token"])
        self.assertEqual(status, 200)
        self.assertEqual(obj["result"], {})

    def test_wrong_admin_key_is_403(self):
        status, _ = self._full_flow(key="nope")
        self.assertEqual(status, 403)

    def test_wrong_verifier_rejected(self):
        _, reg = self._post("/register", {"redirect_uris": ["http://127.0.0.1:7777/cb"]})
        gate = self.oauth
        loc = gate.authorize({"client_id": reg["client_id"],
                              "redirect_uri": reg["redirect_uris"][0],
                              "response_type": "code",
                              "code_challenge": _s256("right" * 10),
                              "code_challenge_method": "S256", "key": "adminkey"})
        code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
        status, obj = self._post("/token", {
            "grant_type": "authorization_code", "code": code,
            "client_id": reg["client_id"],
            "redirect_uri": reg["redirect_uris"][0],
            "code_verifier": "wrong" * 10}, form=True)
        self.assertEqual(status, 400)

    def test_refresh_token_rotates(self):
        _, tok = self._full_flow()
        status, tok2 = self._post("/token", {"grant_type": "refresh_token",
                                             "refresh_token": tok["refresh_token"]},
                                  form=True)
        self.assertEqual(status, 200)
        self.assertNotEqual(tok2["access_token"], tok["access_token"])
        self.assertTrue(self.oauth.verify(tok2["access_token"]))

    def test_authorize_disabled_without_admin_key(self):
        gate = OAuthGate("http://x", admin_key=None)
        with self.assertRaises(PermissionError):
            gate.authorize({"key": None})


class InstallDesktopTests(unittest.TestCase):
    def test_merge_preserves_existing_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "claude_desktop_config.json"
            p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}},
                                     "theme": "dark"}), encoding="utf-8")
            res = install_desktop(p)
            cfg = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("other", cfg["mcpServers"])       # 기존 항목 보존
            self.assertIn("vinc", cfg["mcpServers"])
            self.assertEqual(cfg["theme"], "dark")
            self.assertTrue(Path(res["backup"]).exists())   # 백업 생성

    def test_fresh_config_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "claude_desktop_config.json"
            res = install_desktop(p)
            self.assertIsNone(res["backup"])
            cfg = json.loads(p.read_text(encoding="utf-8"))
            self.assertIn("vinc", cfg["mcpServers"])

    def test_uninstall_removes_only_vinc_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "claude_desktop_config.json"
            p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}},
                                     "theme": "dark"}), encoding="utf-8")
            install_desktop(p)
            res = uninstall_desktop(p)
            cfg = json.loads(p.read_text(encoding="utf-8"))
            self.assertTrue(res["removed"])
            self.assertNotIn("vinc", cfg["mcpServers"])      # vinc 제거
            self.assertIn("other", cfg["mcpServers"])        # 남의 서버 보존
            self.assertEqual(cfg["theme"], "dark")           # 설정 보존
            self.assertTrue(Path(res["backup"]).exists())    # 백업 생성

    def test_uninstall_idempotent_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "claude_desktop_config.json"
            p.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
            self.assertFalse(uninstall_desktop(p)["removed"])   # 없으면 removed:False(에러 아님)
            missing = Path(tmp) / "nope" / "claude_desktop_config.json"
            self.assertFalse(uninstall_desktop(missing)["removed"])  # 파일 부재도 안전


if __name__ == "__main__":
    unittest.main()
