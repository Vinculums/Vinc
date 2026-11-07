# -*- coding: utf-8 -*-
"""VIN-132 K4: 계정 모드의 `mcp serve` 가 stdio 세션을 호스티드 MCP 로 넘긴다.

가짜 호스티드 서버(루프백 스레드)를 세워, 전달기가 보내는 헤더와 돌려주는 줄을 본다.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import proxy as px  # noqa: E402


class _Hosted(BaseHTTPRequestHandler):
    seen: list = []
    mode = "json"

    def log_message(self, *a):  # 조용히
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(n).decode("utf-8"))
        type(self).seen.append({"msg": msg, "auth": self.headers.get("Authorization"),
                                "sid": self.headers.get("Mcp-Session-Id"),
                                "pv": self.headers.get("MCP-Protocol-Version")})
        if self.headers.get("Authorization") != "Bearer vinc_sk_good":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "id" not in msg:                         # 알림
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "vinc", "version": "hosted"},
                      "instructions": "You are connected to your personal graph."}
        elif msg["method"] == "tools/list":
            result = {"tools": [{"name": "vinc_search", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": "{}"}]}
        reply = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        if type(self).mode == "sse":
            body = f"event: message\ndata: {reply}\n\n".encode("utf-8")
            ctype = "text/event-stream"
        else:
            body = reply.encode("utf-8")
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if msg["method"] == "initialize":
            self.send_header("Mcp-Session-Id", "sess-1")
        self.end_headers()
        self.wfile.write(body)


class ProxyTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hosted)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/mcp"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Hosted.seen = []
        _Hosted.mode = "json"

    def _run(self, token: str, lines: list[dict]) -> list[dict]:
        out = io.StringIO()
        src = io.StringIO("".join(json.dumps(m) + "\n" for m in lines))
        px.serve_remote(token, url=self.url, stdin=src, stdout=out)
        return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]

    def _session(self):
        return [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]

    def test_the_session_is_forwarded_whole_with_the_key_and_session_headers(self):
        replies = self._run("vinc_sk_good", self._session())
        # 알림에는 아무것도 쓰지 않는다: 요청 둘에 답 둘
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["result"]["instructions"],
                         "You are connected to your personal graph.")
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "vinc_search")
        self.assertTrue(all(s["auth"] == "Bearer vinc_sk_good" for s in _Hosted.seen))
        # 세션 id 와 협상된 판은 initialize 뒤의 요청부터 실린다
        self.assertEqual([s["sid"] for s in _Hosted.seen], [None, "sess-1", "sess-1"])
        self.assertEqual([s["pv"] for s in _Hosted.seen], [None, "2025-06-18", "2025-06-18"])

    def test_run_start_carries_the_client_from_initialize_as_its_host(self):
        # 허브는 세션을 추적하지 않아 clientInfo 를 모른다. 프로세스 하나에 클라이언트 하나인
        # 프록시가 run_start 의 host 를 채워 넘긴다(VIN-867). 호출자가 준 host 는 그대로 둔다.
        start = {"role": "agent-research", "task": "t"}
        self._run("vinc_sk_good", self._session()[:2] + [
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "vinc_run_start", "arguments": dict(start)}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "vinc_run_start", "arguments": {**start, "host": "mine"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "vinc_search", "arguments": {"query": "x"}}}])
        sent = [s["msg"]["params"]["arguments"] for s in _Hosted.seen
                if s["msg"].get("method") == "tools/call"]
        self.assertEqual([a.get("host") for a in sent], ["t 0", "mine", None])

    def test_an_event_stream_answer_is_unwrapped(self):
        _Hosted.mode = "sse"
        replies = self._run("vinc_sk_good", self._session())
        self.assertEqual([r["id"] for r in replies], [1, 2])

    def test_a_refused_key_becomes_a_readable_error_per_request(self):
        replies = self._run("vinc_sk_revoked", self._session())
        self.assertEqual([r["id"] for r in replies], [1, 2])
        for r in replies:
            self.assertIn("refused this computer's MCP key (HTTP 401)", r["error"]["message"])

    def test_no_key_answers_initialize_with_sign_in_and_every_request_with_one_error(self):
        replies = self._run("", self._session() + [
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "vinc_search", "arguments": {"query": "x"}}}])
        self.assertEqual(_Hosted.seen, [])                  # 아무것도 밖으로 안 나갔다
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]["result"]["instructions"], px.SIGN_IN)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(replies[2]["result"], {})          # ping 은 살아 있다고 답한다
        for r in (replies[1], replies[3]):
            self.assertEqual(r["error"]["message"], px.SIGN_IN)

    def test_an_unreachable_hub_is_an_error_on_the_request_not_a_crash(self):
        p = px.Proxy("vinc_sk_good", "http://127.0.0.1:9/mcp", timeout=2)
        r = p.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(r[0]["id"], 7)
        self.assertIn("Cannot reach your Vinc account", r[0]["error"]["message"])
        self.assertEqual(p.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}), [])


class ModeTest(unittest.TestCase):

    def _mode(self, **kw):
        base = dict(base=None, token=None, owner=None, env={}, config={})
        base.update(kw)
        return px.wants_account_mode(**base)

    def test_account_only_when_the_app_says_so(self):
        self.assertTrue(self._mode(config={"mode": "account"}))
        self.assertFalse(self._mode(config={"mode": "local", "mode_chosen": True}))

    def test_no_config_stays_local_for_cli_only_and_dev_use(self):
        self.assertFalse(self._mode(config={}))

    def test_pointing_at_a_sidecar_explicitly_is_always_local(self):
        for kw in ({"base": "http://127.0.0.1:8787"}, {"token": "t"}, {"owner": "tenant:local"}):
            self.assertFalse(self._mode(config={"mode": "account"}, **kw))

    def test_the_environment_overrides_the_config(self):
        self.assertTrue(self._mode(env={"VINC_MCP_MODE": "account"}, config={"mode": "local"}))
        self.assertFalse(self._mode(env={"VINC_MCP_MODE": "local"}, config={"mode": "account"}))


class CliTest(unittest.TestCase):
    """`mcp serve` 가 계정 모드에서는 사이드카를 건드리지 않고 전달기로 간다."""

    def test_account_mode_serve_never_builds_a_bridge(self):
        from unittest import mock
        import api.cli as cli
        with mock.patch("vinc_mcp.proxy.wants_account_mode", return_value=True), \
             mock.patch("vinc_mcp.proxy.serve_remote", return_value=0) as sr, \
             mock.patch("vinc_mcp.bridge.Bridge") as br, \
             mock.patch.dict("os.environ", {"VINC_REMOTE_MCP_TOKEN": "vinc_sk_x"}):
            rc = cli.main(["mcp", "serve"])
        self.assertEqual(rc, 0)
        sr.assert_called_once_with("vinc_sk_x")
        br.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class LocalProfileTest(unittest.TestCase):
    """로컬 모드의 stdio 는 `target`·`space` 없이 로컬 스토어만 본다(VIN-132)."""

    def test_the_local_profile_carries_neither_target_nor_space(self):
        from vinc_mcp import tools
        local = tools.specs()
        # 커뮤니티 도구 둘은 계정 서비스로만 돈다. 로컬에는 계정이 없어 늘 실패하므로
        # 이 프로파일에서만 빠진다(VIN-589). 레지스트리의 나머지는 그대로 나간다.
        self.assertEqual({s["name"] for s in local},
                         {t["name"] for t in tools.TOOLS} - set(tools._ACCOUNT_ONLY_TOOLS))
        for s in local:
            props = s["inputSchema"].get("properties") or {}
            self.assertNotIn("target", props, s["name"])
            self.assertNotIn("space", props, s["name"])

    def test_community_tools_leave_only_the_local_profile(self):
        """로컬 stdio 목록에 있던 두 도구는 부르면 언제나 계정 커넥터가 필요하다는 오류였다.
        목록에서 빼되 허브는 그대로 낸다. 레지스트리(`TOOLS`)의 수는 그대로다."""
        from vinc_mcp import community, rpc, tools
        pair = {community.READ_TOOL, community.WRITE_TOOL}
        self.assertEqual(set(tools._ACCOUNT_ONLY_TOOLS), pair)
        local = {s["name"] for s in rpc.Dispatcher(None).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        hosted = {s["name"] for s in tools.specs(hub=True)}
        self.assertFalse(pair & local)
        self.assertTrue(pair <= hosted)
        self.assertTrue(pair <= {t["name"] for t in tools.TOOLS})

    def test_a_remote_target_or_a_space_is_refused_not_ignored(self):
        from vinc_mcp import tools
        for extra, word in (({"target": "remote"}, "local mode"), ({"space": "personal"}, "no spaces")):
            with self.assertRaises(ValueError) as cm:
                tools.call("vinc_domains", dict(extra), bridge=None)
            self.assertIn(word, str(cm.exception))

    def test_stdio_serves_the_local_profile(self):
        import inspect
        from vinc_mcp import stdio
        self.assertIn("Dispatcher(bridge)", inspect.getsource(stdio.serve))


class KeychainTest(unittest.TestCase):
    """엔진이 앱의 OS 키체인 항목을 읽는다. 어떤 실패도 None 이다(VIN-132 K3)."""

    def test_windows_blobs_decode_as_utf16_and_utf8(self):
        from vinc_mcp import keychain
        self.assertEqual(keychain._decode_blob("vinc_sk_abc".encode("utf-16-le")), "vinc_sk_abc")
        self.assertEqual(keychain._decode_blob(b"vinc_sk_abcd"), "vinc_sk_abcd")

    def test_a_failing_backend_reads_as_absent(self):
        from unittest import mock
        from vinc_mcp import keychain
        with mock.patch.object(keychain, "_read_windows", side_effect=OSError("no store")), \
             mock.patch.object(keychain, "_run", side_effect=OSError("no tool")):
            self.assertIsNone(keychain.read(keychain.MCP_KEY))

    def test_the_entry_name_matches_the_app(self):
        from vinc_mcp import keychain
        src = (Path(__file__).resolve().parents[2] / "app/src/account-connect.ts").read_text(encoding="utf-8")
        self.assertIn(f'const K_MCP = "{keychain.MCP_KEY}";', src)
        secret_rs = (Path(__file__).resolve().parents[2] / "app/src-tauri/src/secret.rs").read_text(encoding="utf-8")
        self.assertIn(f'const SERVICE: &str = "{keychain.SERVICE}";', secret_rs)

    def test_the_app_stores_the_mcp_key_on_sign_in_and_deletes_it_on_sign_out(self):
        src = (Path(__file__).resolve().parents[2] / "app/src/account-connect.ts").read_text(encoding="utf-8")
        delivered = src[src.index('if (st.status === "delivered")'):]
        self.assertLess(delivered.index("await storeKey(st.key);"),
                        delivered.index('await storeMcpKey(st.mcp_key || "");'))
        forget = src[src.index("export function forgetAccount"):]
        forget = forget[: forget.index("\n}\n")]
        self.assertIn('s("secret_delete", { name: K_MCP })', forget)
