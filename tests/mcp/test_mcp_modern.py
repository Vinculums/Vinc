# -*- coding: utf-8 -*-
"""MCP 2026-07-28(모던 세대)과 initialize 세대가 한 서버에서 같이 도는가.

두 갈래를 같이 본다. 하나는 **핸드셰이크 세대가 한 글자도 안 바뀌었는가**다: Claude Code·
ChatGPT·Codex·Cursor 는 지금 initialize 로 붙고, 협상한 리비전이 모르는 키를 받으면 끊는
클라이언트가 있다(record:what-the-spec-permits-is-not-what-the-peer-accepts). 다른 하나는
모던 요청이 스펙대로 답하는가다: server/discover, `_meta` 버전, resultType, 캐시 힌트,
HTTP 헤더 대조와 상태 코드, 도구 순서, MRTR 결과 만들기.
근거: [[decision:mcp-modern-era-is-served-per-request-beside-the-initialize-era]]
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import __version__, builtin_prompts, modern, rpc, tools  # noqa: E402
from vinc_mcp import proxy as px  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402

MODERN = "2026-07-28"
META = {"io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {}}
#: 모던 결과에만 붙는 최상위 키. 핸드셰이크 세대 응답에 하나라도 있으면 회귀다.
MODERN_KEYS = {"resultType", "ttlMs", "cacheScope", "supportedVersions"}


class _Stub(Bridge):
    """사이드카 없이 답하는 브리지. 역할 목록은 빈 로스터로 답한다."""

    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "roles": [], "next_offset": None}
        return {"executed": False, "echo": path}


def _msg(method, params=None, msg_id=1, meta=None):
    p = dict(params or {})
    if meta is not None:
        p["_meta"] = meta
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": p}


class HandshakeEraIsUnchanged(unittest.TestCase):
    """`_meta` 버전이 없는 요청은 이 변경 전과 같은 답을 받는다."""

    def setUp(self):
        self.d = rpc.Dispatcher(_Stub())

    def test_initialize_never_negotiates_the_modern_revision(self):
        # 2026-07-28 은 initialize 목록에 없다. 그 판을 요청한 핸드셰이크 클라이언트는
        # 지금처럼 최신 initialize 판을 받는다(모르는 리비전을 답으로 받으면 끊긴다).
        self.assertNotIn(MODERN, rpc.PROTOCOL_VERSIONS)
        for want in (MODERN, "2025-11-25", "2099-01-01", None):
            with self.subTest(want=want):
                params = {} if want is None else {"protocolVersion": want}
                r = self.d.handle(_msg("initialize", params))["result"]
                self.assertEqual(r["protocolVersion"], rpc.LATEST_PROTOCOL_VERSION)
                self.assertEqual(sorted(r), ["capabilities", "protocolVersion", "serverInfo"])
                self.assertEqual(r["capabilities"], {"tools": {}, "prompts": {}})
                self.assertEqual(sorted(r["serverInfo"]), ["name", "version"])

    def test_initialize_with_modern_meta_is_still_the_handshake(self):
        r = self.d.handle(_msg("initialize", {"protocolVersion": "2025-06-18"}, meta=META))
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertFalse(MODERN_KEYS & set(r["result"]))

    def test_results_carry_no_modern_fields(self):
        for msg in (_msg("tools/list"), _msg("prompts/list"), _msg("ping"),
                    _msg("tools/call", {"name": "vinc_search", "arguments": {"query": "x"}}),
                    _msg("prompts/get", {"name": builtin_prompts.BUILD_GRAPH})):
            with self.subTest(method=msg["method"]):
                result = self.d.handle(msg)["result"]
                self.assertFalse(MODERN_KEYS & set(result), sorted(result))
                self.assertNotIn(modern.META_SERVER_INFO, result.get("_meta") or {})
        self.assertEqual(sorted(self.d.handle(_msg("tools/list"))["result"]), ["tools"])
        self.assertEqual(sorted(self.d.handle(_msg("prompts/list"))["result"]), ["prompts"])

    def test_a_handshake_revision_inside_meta_is_served_as_today(self):
        for pv in rpc.PROTOCOL_VERSIONS:
            meta = {**META, "io.modelcontextprotocol/protocolVersion": pv}
            result = self.d.handle(_msg("tools/list", meta=meta))["result"]
            self.assertEqual(sorted(result), ["tools"])

    def test_discover_without_meta_is_still_method_not_found(self):
        self.assertEqual(self.d.handle(_msg("server/discover"))["error"]["code"], -32601)

    def test_a_modern_reply_does_not_leak_into_a_later_handshake_reply(self):
        """내장 프롬프트 dict 는 모듈 수준이다. 모던 봉투가 제자리에서 고치면 다음 응답에 샌다."""
        name = {"name": builtin_prompts.BUILD_GRAPH}
        first = self.d.handle(_msg("prompts/get", name, meta=META))["result"]
        self.assertEqual(first["resultType"], "complete")
        after = self.d.handle(_msg("prompts/get", name))["result"]
        self.assertNotIn("resultType", after)
        self.assertNotIn("_meta", after)


class ModernEra(unittest.TestCase):
    def setUp(self):
        self.d = rpc.Dispatcher(_Stub())

    def _ok(self, msg, **kw):
        r = self.d.handle(msg, **kw)
        self.assertNotIn("error", r, r)
        result = r["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["_meta"][modern.META_SERVER_INFO],
                         {"name": "vinc", "version": __version__})
        return result

    def test_discover_advertises_versions_capabilities_and_identity(self):
        result = self._ok(_msg("server/discover", meta=META))
        self.assertEqual(result["supportedVersions"],
                         [MODERN] + list(rpc.PROTOCOL_VERSIONS))
        self.assertEqual(result["capabilities"], {"tools": {}, "prompts": {}})
        self.assertEqual((result["ttlMs"], result["cacheScope"]),
                         (modern.DISCOVER_TTL_MS, "public"))
        self.assertNotIn("instructions", result)        # 로컬: 고를 스페이스가 없다

    def test_discover_on_the_hub_is_private_and_carries_the_space_paragraph(self):
        result = self._ok(_msg("server/discover", meta=META), forced_owner="team:x")
        self.assertEqual(result["cacheScope"], "private")
        self.assertIn("team's shared graph (team:x)", result["instructions"])

    def test_list_results_carry_cache_hints(self):
        tl = self._ok(_msg("tools/list", meta=META))
        self.assertEqual((tl["ttlMs"], tl["cacheScope"]), (3_600_000, "public"))
        pl = self._ok(_msg("prompts/list", meta=META))
        self.assertEqual((pl["ttlMs"], pl["cacheScope"]), (60_000, "private"))

    def test_the_tool_list_is_the_same_in_both_eras(self):
        legacy = self.d.handle(_msg("tools/list"))["result"]["tools"]
        self.assertEqual(self._ok(_msg("tools/list", meta=META))["tools"], legacy)

    def test_tool_call_and_ping_are_complete_results(self):
        result = self._ok(_msg("tools/call", {"name": "vinc_search",
                                              "arguments": {"query": "x"}}, meta=META))
        self.assertFalse(result["isError"])
        self._ok(_msg("ping", meta=META))

    def test_a_tool_error_is_still_a_complete_result(self):
        result = self._ok(_msg("tools/call", {"name": "vinc_search", "arguments": {}},
                               meta=META))
        self.assertTrue(result["isError"])
        self.assertIn("vinc_invalid_argument", result["_meta"])   # 기존 _meta 는 남는다

    def test_unknown_version_is_unsupported_protocol_version(self):
        meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
        err = self.d.handle(_msg("tools/list", meta=meta, msg_id=9))
        self.assertEqual(err["id"], 9)
        self.assertEqual(err["error"]["code"], -32022)
        self.assertEqual(err["error"]["data"],
                         {"supported": [MODERN] + list(rpc.PROTOCOL_VERSIONS),
                          "requested": "1900-01-01"})

    def test_missing_capabilities_or_a_bad_version_is_invalid_params(self):
        no_caps = {"io.modelcontextprotocol/protocolVersion": MODERN}
        self.assertEqual(self.d.handle(_msg("tools/list", meta=no_caps))["error"]["code"],
                         -32602)
        bad = {**META, "io.modelcontextprotocol/protocolVersion": 20260728}
        self.assertEqual(self.d.handle(_msg("tools/list", meta=bad))["error"]["code"], -32602)

    def test_unknown_method_and_notifications(self):
        self.assertEqual(self.d.handle(_msg("subscriptions/listen", meta=META))["error"]["code"],
                         -32601)
        self.assertIsNone(self.d.handle({"jsonrpc": "2.0", "method": "tools/list",
                                         "params": {"_meta": META}}))


class ToolOrderIsStable(unittest.TestCase):
    """`tools/list` 는 재시작에도 바이트까지 같아야 호스트의 프롬프트 캐시가 산다."""

    _CODE = ("import hashlib, json\n"
             "from vinc_mcp import tools\n"
             "print(' '.join(hashlib.sha256(json.dumps(tools.specs(hub=h), ensure_ascii=False)"
             ".encode()).hexdigest() for h in (False, True)))\n")

    def _digest(self, seed: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        env["PYTHONHASHSEED"] = seed
        out = subprocess.run([sys.executable, "-X", "utf8", "-c", self._CODE], cwd=str(ROOT),
                             env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return out.stdout.strip()

    def test_two_processes_with_different_hash_seeds_list_the_same_bytes(self):
        self.assertEqual(self._digest("1"), self._digest("4242"))

    def test_the_order_is_the_registry_order(self):
        names = [t["name"] for t in tools.specs(hub=True)]
        self.assertEqual(names, [t["name"] for t in tools.TOOLS + tools.HOST_TOOLS])


class HeaderValues(unittest.TestCase):
    def test_round_trip(self):
        for v in ("vinc_search", "Hello, 세계", " padded ", "line1\nline2", "=?base64?x?="):
            with self.subTest(v=v):
                self.assertEqual(modern.decode_header_value(modern.encode_header_value(v)), v)
        self.assertEqual(modern.encode_header_value("vinc_search"), "vinc_search")
        self.assertEqual(modern.encode_header_value("Hello, 世界"),
                         "=?base64?SGVsbG8sIOS4lueVjA==?=")
        with self.assertRaises(ValueError):
            modern.decode_header_value("=?base64?!!!?=")

    def test_request_headers_mirror_the_body(self):
        m = _msg("tools/call", {"name": "vinc_search"}, meta=META)
        self.assertEqual(modern.request_headers(m),
                         {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/call",
                          "Mcp-Name": "vinc_search"})
        self.assertEqual(modern.request_headers(_msg("tools/list")), {})


class InputRequired(unittest.TestCase):
    """MRTR 결과 만들기. 아직 어느 도구에도 연결하지 않는다(실행 프로토콜이 쓴다)."""

    ASK = {"approve": {"method": "elicitation/create",
                       "params": {"mode": "form", "message": "Merge this change?",
                                  "requestedSchema": {"type": "object", "properties": {
                                      "ok": {"type": "boolean"}}}}}}

    def test_builds_an_input_required_result(self):
        out = modern.input_required(self.ASK, request_state="sealed",
                                    client_capabilities={"elicitation": {}})
        self.assertEqual(out, {"resultType": "input_required", "inputRequests": self.ASK,
                               "requestState": "sealed"})
        self.assertNotIn("ttlMs", out)
        # 모던 봉투를 거쳐도 input_required 는 덮이지 않는다
        self.assertEqual(modern.complete(out)["resultType"], "input_required")

    def test_request_state_alone_is_enough(self):
        self.assertEqual(modern.input_required(request_state="s"),
                         {"resultType": "input_required", "requestState": "s"})

    def test_needs_something_to_ask(self):
        with self.assertRaises(ValueError):
            modern.input_required()
        with self.assertRaises(ValueError):
            modern.input_required(request_state="")

    def test_an_undeclared_capability_is_refused(self):
        with self.assertRaises(modern.MissingClientCapability) as cm:
            modern.input_required(self.ASK, client_capabilities={})
        rej = cm.exception.rejection()
        self.assertEqual((rej.code, rej.status), (-32021, 400))
        self.assertEqual(rej.data, {"requiredCapabilities": {"elicitation": {"form": {}}}})
        url = {"k": {"method": "elicitation/create",
                     "params": {"mode": "url", "message": "Sign in",
                                "url": "https://vincs.io/account"}}}
        with self.assertRaises(modern.MissingClientCapability):
            modern.input_required(url, client_capabilities={"elicitation": {}})
        self.assertIn("inputRequests", modern.input_required(
            url, client_capabilities={"elicitation": {"url": {}}}))

    def test_sampling_and_roots_are_not_offered(self):
        for method in ("sampling/createMessage", "roots/list", "tools/call"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                modern.input_required({"k": {"method": method, "params": {}}},
                                      client_capabilities={"sampling": {}, "roots": {}})


class _HTTP(unittest.TestCase):
    TOKEN = "statictok"

    def setUp(self):
        self.app = MCPHTTPApp(_Stub(), static_token=self.TOKEN)
        self.httpd = make_server(self.app, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, body, headers=None):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.TOKEN}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read().decode() or "{}")


class HTTPModern(_HTTP):
    def _modern(self, method, params=None, **override):
        msg = _msg(method, params, meta=META)
        headers = modern.request_headers(msg)
        headers.update(override)
        return msg, {k: v for k, v in headers.items() if v is not None}

    def test_discover_and_tools(self):
        code, hdrs, obj = self._post(*self._modern("server/discover"))
        self.assertEqual(code, 200, obj)
        self.assertEqual(obj["result"]["resultType"], "complete")
        self.assertIn(MODERN, obj["result"]["supportedVersions"])
        self.assertNotIn("Mcp-Session-Id", hdrs)            # 모던은 세션을 반사하지 않는다
        code, _, obj = self._post(*self._modern("tools/list"))
        self.assertEqual((code, obj["result"]["cacheScope"]), (200, "public"))
        code, _, obj = self._post(*self._modern("tools/call", {"name": "vinc_search",
                                                              "arguments": {"query": "x"}}))
        self.assertEqual((code, obj["result"]["resultType"]), (200, "complete"))

    def test_a_base64_name_header_is_decoded_before_comparing(self):
        enc = "=?base64?" + base64.b64encode(b"vinc_search").decode() + "?="
        code, _, obj = self._post(*self._modern(
            "tools/call", {"name": "vinc_search", "arguments": {"query": "x"}}, **{"Mcp-Name": enc}))
        self.assertEqual(code, 200, obj)

    def test_header_mismatches_are_400_with_32020(self):
        call = {"name": "vinc_search", "arguments": {"query": "x"}}
        for override in ({"MCP-Protocol-Version": None}, {"Mcp-Method": None},
                         {"Mcp-Method": "tools/list"}, {"Mcp-Name": None},
                         {"Mcp-Name": "vinc_ingest"}, {"MCP-Protocol-Version": "2025-06-18"}):
            with self.subTest(override=override):
                code, _, obj = self._post(*self._modern("tools/call", call, **override))
                self.assertEqual(code, 400)
                self.assertEqual(obj["error"]["code"], -32020)
                self.assertEqual(obj["id"], 1)

    def test_a_modern_header_on_a_handshake_body_is_a_mismatch(self):
        code, _, obj = self._post(_msg("tools/list"), {"MCP-Protocol-Version": MODERN,
                                                       "Mcp-Method": "tools/list"})
        self.assertEqual((code, obj["error"]["code"]), (400, -32020))

    def test_unsupported_version_is_400_with_32022(self):
        code, _, obj = self._post(_msg("tools/list"), {"MCP-Protocol-Version": "2099-01-01"})
        self.assertEqual((code, obj["error"]["code"]), (400, -32022))
        self.assertEqual(obj["error"]["data"]["requested"], "2099-01-01")
        meta = {**META, "io.modelcontextprotocol/protocolVersion": "2099-01-01"}
        code, _, obj = self._post(_msg("tools/list", meta=meta),
                                  {"MCP-Protocol-Version": "2099-01-01", "Mcp-Method": "tools/list"})
        self.assertEqual((code, obj["error"]["code"]), (400, -32022))

    def test_missing_capabilities_is_400_and_unknown_method_is_404(self):
        msg = _msg("tools/list", meta={"io.modelcontextprotocol/protocolVersion": MODERN})
        code, _, obj = self._post(msg, modern.request_headers(msg))
        self.assertEqual((code, obj["error"]["code"]), (400, -32602))
        code, _, obj = self._post(*self._modern("subscriptions/listen"))
        self.assertEqual((code, obj["error"]["code"]), (404, -32601))

    def test_browser_preflight_allows_the_mirrored_headers(self):
        req = urllib.request.Request(self.url, method="OPTIONS")
        req.add_header("Origin", "https://claude.ai")
        with urllib.request.urlopen(req, timeout=5) as r:
            allowed = r.headers.get("Access-Control-Allow-Headers") or ""
        for h in ("Mcp-Protocol-Version", "Mcp-Method", "Mcp-Name"):
            self.assertIn(h, allowed)


class HTTPHandshakeUnchanged(_HTTP):
    """핸드셰이크 세대 클라이언트가 HTTP 로 보는 것: 200, 세션 반사, 모던 키 없음."""

    def test_initialize_then_list_and_call(self):
        code, hdrs, obj = self._post(_msg("initialize", {"protocolVersion": "2025-06-18",
                                                         "capabilities": {},
                                                         "clientInfo": {"name": "c", "version": "1"}}))
        self.assertEqual((code, obj["result"]["protocolVersion"]), (200, "2025-06-18"))
        self.assertEqual(hdrs.get("Mcp-Session-Id"), "default")
        for msg in (_msg("tools/list", msg_id=2),
                    _msg("tools/call", {"name": "vinc_search", "arguments": {"query": "x"}}, 3)):
            code, hdrs, obj = self._post(msg, {"MCP-Protocol-Version": "2025-06-18",
                                               "Mcp-Session-Id": "s-1"})
            self.assertEqual(code, 200)
            self.assertEqual(hdrs.get("Mcp-Session-Id"), "s-1")
            self.assertFalse(MODERN_KEYS & set(obj["result"]))

    def test_unknown_method_is_still_200(self):
        code, _, obj = self._post(_msg("resources/list"))
        self.assertEqual((code, obj["error"]["code"]), (200, -32601))

    def test_batch_still_works(self):
        code, _, obj = self._post([_msg("ping", msg_id=1), _msg("ping", msg_id=2)])
        self.assertEqual((code, [r["id"] for r in obj]), (200, [1, 2]))


class StdioProxyEndToEnd(_HTTP):
    """계정 모드 stdio 전달기 → 실제 허브 앱. 모던 메시지는 자기 헤더를 실어 대조를 통과한다."""

    def _run(self, lines):
        out = io.StringIO()
        src = io.StringIO("".join(json.dumps(m) + "\n" for m in lines))
        px.serve_remote(self.TOKEN, url=self.url, stdin=src, stdout=out)
        return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]

    def test_modern_and_handshake_sessions_both_pass(self):
        replies = self._run([
            _msg("server/discover", meta=META, msg_id=1),
            _msg("tools/call", {"name": "vinc_search", "arguments": {"query": "x"}},
                 msg_id=2, meta=META),
            _msg("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}, msg_id=3),
            _msg("tools/list", msg_id=4),
        ])
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]["result"]["resultType"], "complete")
        self.assertEqual(replies[1]["result"]["resultType"], "complete")
        self.assertEqual(replies[2]["result"]["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", replies[3]["result"])


class SupportedVersions(unittest.TestCase):
    def test_modern_first_and_never_repeated(self):
        self.assertEqual(modern.supported_versions((MODERN, "2025-06-18")), [MODERN, "2025-06-18"])


if __name__ == "__main__":
    unittest.main()
