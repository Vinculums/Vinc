# -*- coding: utf-8 -*-
"""vinc_mcp — D11 도구 계약·JSON-RPC 디스패처·HTTP 전송·OAuth 게이트."""
from __future__ import annotations

import base64
import hashlib
import json
import os
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
from vinc_mcp.install import (  # noqa: E402
    CLIENTS, client_label, install_desktop, install_json, manual_entry,
    uninstall_desktop, uninstall_json,
)
from vinc_mcp.oauth import OAuthGate, _s256  # noqa: E402
from vinc_mcp.rpc import (  # noqa: E402
    LATEST_PROTOCOL_VERSION, PROTOCOL_VERSIONS, Dispatcher,
)

D11_TOOLS = {"vinc_search", "vinc_recall", "vinc_graph", "vinc_decide",
             "vinc_domains", "vinc_suggest_ties", "vinc_ingest",
             # T3c — 오염 정리 쓰기 표면(비가역). + S4 확장: 엣지 삭제(툼스톤 전파).
             "vinc_delete", "vinc_delete_edge", "vinc_merge_nodes",
             # E-G3 — 구조 진단(조회 전용): WCC 고아·컴포넌트 + Louvain 커뮤니티.
             "vinc_diagnose",
             # E-G4 — 중복 개념 병합 후보(조회 전용, 확정은 merge_nodes 경유).
             # F10-b — 그 짝인 **기각 기억**(쓰기지만 가역: undo). 이게 없으면
             # 한 번 아니라고 판단한 쌍이 매번 다시 올라와 목록이 수렴하지 않는다.
             "vinc_merge_candidates", "vinc_merge_reject",
             # E-G5/G7 — 단일 홉 관측(SoG) · 최단 경로 증거 체인.
             "vinc_neighbors", "vinc_path",
             # D45 P4 — 문서 정본(Chunk=SSoT) 직접 CRUD: get/list(조회) · put/delete(쓰기).
             "vinc_doc_get", "vinc_doc_list", "vinc_doc_put", "vinc_doc_delete",
             # D45-V — 정본에서 개념을 파생한다. 이게 없으면 어시스턴트는 문서를
             # 쓸 수는 있는데 그것을 그래프에 합류시키지 못한다(워크플로 절반이
             # 웹 전용이었다).
             "vinc_doc_extract",
             # F10-b 의 읽는 쪽. 쓸 수만 있고 읽을 수 없던 짝 결손.
             "vinc_merge_rejections",
             # GM-1 — 쓰기 전에 읽는 관례(GRAPH.md). 재가된 어휘를 보는 유일한 읽기 채널.
             "vinc_conventions",
             # W1 — 질문 하나로 답변 재료를 모으는 조인 읽기(조회 전용).
             "vinc_brief",
             # 스페이스가 주소에서 **인자**로 내려오면서 생긴 자리(발주자 지시
             # 2026-08-31). `space` 에 실을 id 를 받을 곳이 없으면 그 인자는 못 쓴다.
             "vinc_whoami"}


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
        # 쓰기 표면은 **명시된 것만**(ingest + 오염 정리 delete/merge + D45 P4 문서
        # 정본 put/delete + F10-b 기각 기억)이어야 한다 — 조회 도구가 몰래 쓰지 않는다.
        b = _StubBridge()
        for t in tools.specs():
            args = {"query": "x", "seed_id": "concept:x", "topic": "x",
                    "node": "concept:x", "nodes": [{"id": "c:x", "type": "concept"}],
                    "node_id": "concept:x", "keep_id": "concept:x", "drop_id": "concept:y",
                    "from_id": "concept:x", "to_id": "concept:y", "type": "relates_to",
                    "a": "concept:x", "b": "concept:y",   # E-G7 vinc_path 필수 인자
                    "text": "x", "doc_id": "d0"}          # D45 P4 문서 정본 필수 인자
            tools.call(t["name"], args, b)
        writes = sorted({c[1] for c in b.calls if c[0] == "POST"})
        # `/doc/extract` 는 apply=false 면 아무것도 안 쓰지만 **쓸 수 있는** 표면이라
        # 여기 든다. 이 목록의 값은 "무엇이 POST 냐"가 아니라 "무엇이 쓸 수 있느냐"를
        # 사람이 한 번 더 확인하는 데 있다 — 조회 도구가 몰래 늘어나면 여기서 걸린다.
        self.assertEqual(writes, ["/delete", "/delete-edge", "/doc/delete", "/doc/extract",
                                  "/doc/put", "/ingest/fragment", "/merge-nodes",
                                  "/merge-reject"])

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

    def test_ingest_schema_is_open_layer(self):
        # F1 — type enum 제거(서버가 라우팅 SSoT — 개방 캐리어를 호스트에서 차단 금지),
        # theme_id·edge.role 은 선택 필드로 추가.
        schema = next(t for t in tools.specs() if t["name"] == "vinc_ingest")["inputSchema"]
        node_type = schema["properties"]["nodes"]["items"]["properties"]["type"]
        edge_props = schema["properties"]["edges"]["items"]["properties"]
        self.assertNotIn("enum", node_type)
        self.assertNotIn("enum", edge_props["type"])
        self.assertIn("role", edge_props)
        self.assertIn("theme_id", schema["properties"])
        desc = next(t for t in tools.specs() if t["name"] == "vinc_ingest")["description"]
        self.assertIn("proposed_types", desc)          # 개방층 계약 안내
        self.assertNotIn("unknown type values are skipped", desc)

    def test_ingest_forwards_theme_id(self):
        b = _StubBridge()
        tools.call("vinc_ingest", {"nodes": [{"id": "p:x", "type": "policy", "title": "X"}],
                                   "theme_id": "art"}, b)
        self.assertEqual(b.calls[0][3]["theme_id"], "art")
        b2 = _StubBridge()
        tools.call("vinc_ingest", {"nodes": [{"id": "c:x", "type": "concept"}]}, b2)
        self.assertNotIn("theme_id", b2.calls[0][3])   # 미지정 시 필드 자체 생략


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

    def test_initialize_falls_back_to_the_latest_supported_version(self):
        """미지원 버전 요청 → **최신**으로 응답(스펙 SHOULD). 구버전으로 떨어뜨리면
        새 리비전으로 협상하는 호스트가 이유 없이 기능을 잃는다."""
        for want in ("1999-01-01", "2099-12-31"):
            r = self._rpc("initialize", {"protocolVersion": want})
            self.assertEqual(r["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)
        self.assertEqual(LATEST_PROTOCOL_VERSION, max(PROTOCOL_VERSIONS))

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
        """`resources/*` 비구현은 실수가 아니라 판정이다 — `vinc_conventions`·
        `vinc_domains` 가 이미 같은 것을 도구로 낸다. W2 가 `prompts/*` 를 열었으므로
        이 못은 이제 **갈림**을 지킨다: prompts 열림, resources 닫힘. 갈림의 반대쪽은
        tests/test_mcp_prompts.py 가 본다(스텁 브리지가 로스터를 답해야 한다)."""
        r = self._rpc("resources/list")
        self.assertEqual(r["error"]["code"], -32601)
        self.assertEqual(self._rpc("resources/subscribe")["error"]["code"], -32601)
        self.assertEqual(self._rpc("completion/complete")["error"]["code"], -32601)

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


class HealthStateTests(unittest.TestCase):
    """/health 가 상태 저장소 진단을 싣는가(2026-08-03).

    이 사고의 급소가 정확히 이것이었다: OAuth 등록이 배포마다 사라지고 있었는데
    /health 는 200 만 답해 **어디에도 드러나지 않았다**. 사람이 기억해서 로그를
    뒤져야 알 수 있는 상태를 만들지 않는다."""

    def _serve(self, app):
        httpd = make_server(app, "127.0.0.1", 0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=5) as r:
            return r.status, json.loads(r.read().decode())

    def test_health_reports_snapshot_state(self):
        from vinc_mcp.store import Store
        tmp = tempfile.mkdtemp()
        store = Store(os.path.join(tmp, "work", "oauth.db"),
                      snapshot=os.path.join(tmp, "persist", "oauth.db"))
        self.addCleanup(store.close)
        app = MCPHTTPApp(_StubBridge(), static_token="t",
                         oauth=OAuthGate("https://mcp.test", None, store=store))
        code, obj = self._serve(app)
        self.assertEqual(code, 200)
        self.assertEqual(obj["status"], "ok")
        st = obj["oauth_state"]
        self.assertEqual(st["snapshot_state"], "ok")      # 영속이 실제로 되고 있다
        self.assertTrue(st["snapshot"].endswith("oauth.db"))
        self.assertNotEqual(st["journal_mode"], "wal")

    def test_health_ok_without_oauth(self):
        """OAuth 를 안 켠 배치(정적 토큰 전용)에서도 /health 는 200 이어야 한다 —
        진단을 얹다가 헬스 자체를 깨뜨리면 CD 검증이 통째로 막힌다."""
        code, obj = self._serve(MCPHTTPApp(_StubBridge(), static_token="t"))
        self.assertEqual(code, 200)
        self.assertEqual(obj["status"], "ok")
        self.assertNotIn("oauth_state", obj)


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

    def test_get_mcp_is_405_and_advertises_usable_methods(self):
        try:
            urllib.request.urlopen(self.base + "/mcp", timeout=5)
            self.fail("expected 405")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)
            # SSE 를 먼저 시도하는 호스트가 다음 수를 알 수 있도록 Allow 를 준다.
            self.assertIn("POST", e.headers.get("Allow") or "")

    def _delete(self, session=None, token=None):
        req = urllib.request.Request(self.base + "/mcp", method="DELETE")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        if session:
            req.add_header("Mcp-Session-Id", session)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_delete_terminates_session_and_is_idempotent(self):
        """세션 종료를 보내는 호스트(Codex·Cursor 등)가 501 을 오류로 보지 않게."""
        self._post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                   token=self.static)
        self.assertEqual(self._delete(token=self.static), 204)
        self.assertEqual(self._delete(token=self.static), 204)   # 멱등
        self.assertEqual(self._delete(session="never-existed", token=self.static), 204)

    def test_delete_requires_auth(self):
        self.assertEqual(self._delete(), 401)

    def test_arbitrary_session_ids_allocate_nothing(self):
        """세션 표 자체가 없어졌다 — 임의 헤더로 증식시킬 상태가 존재하지 않는다.

        전에는 상한 256 + FIFO 축출로 증식을 막았는데, 그 표가 지키던 것이 아무도
        안 읽는 불리언 하나뿐이라 표째 걷어냈다(MCPHTTPApp.__init__). 상한을 재는
        테스트를 '상한이 필요 없다'를 재는 테스트로 바꾼다."""
        first = self.app.dispatcher()
        for _ in range(50):
            self.assertIs(self.app.dispatcher(), first, "Dispatcher 가 공유가 아니다")
        self.assertFalse(hasattr(self.app, "_sessions"),
                         "세션 표가 되살아났다 — 레플리카 간 불일치가 함께 돌아온다")

    def test_batch_request_gets_an_array_back(self):
        """`PROTOCOL_VERSIONS` 가 2025-03-26 을 광고하고 그 리비전은 배치를 지원한다.
        전에는 dict 가 아니면 400 이라 광고한 리비전으로 협상한 클라이언트가 막혔다."""
        code, obj = self._post("/mcp", [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                        {"jsonrpc": "2.0", "id": 2, "method": "ping"}],
                               token=self.static)
        self.assertEqual(code, 200)
        self.assertIsInstance(obj, list)
        self.assertEqual([r["id"] for r in obj], [1, 2])

    def test_batch_of_only_notifications_gets_202(self):
        """응답할 것이 없으면 배치도 본문 없이 202 다(스펙)."""
        req = urllib.request.Request(
            self.base + "/mcp",
            data=json.dumps([{"jsonrpc": "2.0", "method": "notifications/initialized"}]).encode(),
            method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.static}")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 202)

    def test_empty_batch_is_rejected(self):
        code, _ = self._post("/mcp", [], token=self.static)
        self.assertEqual(code, 400)

    def _post_with_headers(self, body, headers):
        req = urllib.request.Request(self.base + "/mcp",
                                     data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.static}")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_protocol_version_header_is_validated(self):
        """2025-06-18 스펙: 지원하지 않는 리비전 헤더에는 400. 전에는 이 헤더 이름이
        CORS 허용목록 문자열에만 있고 코드가 읽지 않았다."""
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self._post_with_headers(ping, {}), 200)               # 없으면 통과
        self.assertEqual(
            self._post_with_headers(ping, {"MCP-Protocol-Version": "2025-06-18"}), 200)
        self.assertEqual(
            self._post_with_headers(ping, {"MCP-Protocol-Version": "1999-01-01"}), 400)

    def test_browser_origin_outside_the_allowlist_is_refused(self):
        """CORS 는 응답을 **읽지** 못하게 할 뿐 요청 도달을 막지 않는다. 스펙이
        요구하는 Origin 게이트가 없어서 DNS 리바인딩 표면이 열려 있었다."""
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self._post_with_headers(ping, {"Origin": "https://evil.example"}), 403)
        self.assertEqual(self._post_with_headers(ping, {"Origin": "https://claude.ai"}), 200)
        # Origin 이 없는 요청(서버측 커넥터·데스크탑 앱)은 절대 막지 않는다.
        self.assertEqual(self._post_with_headers(ping, {}), 200)

    def test_cors_reflects_known_browser_hosts_only(self):
        """브라우저에서 도는 호스트만 반사 — 와일드카드 금지(남의 오리진 차단)."""
        for origin, allowed in (("https://claude.ai", True),
                                ("https://chatgpt.com", True),
                                ("https://evil.example", False)):
            req = urllib.request.Request(self.base + "/mcp", method="OPTIONS")
            req.add_header("Origin", origin)
            with urllib.request.urlopen(req, timeout=5) as r:
                got = r.headers.get("Access-Control-Allow-Origin")
            self.assertEqual(got, origin if allowed else None, origin)

    def test_no_auth_config_refused(self):
        with self.assertRaises(ValueError):
            MCPHTTPApp(_StubBridge())


class OAuthFlowTests(_HTTPFixture):
    static = None

    def setUp(self):
        self.oauth = OAuthGate("http://issuer.test", admin_key="adminkey")
        super().setUp()

    def test_rebind_needs_the_admin_key(self):
        """client_id 지정 재등록은 관리 키에서만 먹는다(심사중 플랫폼 구제용)."""
        _, first = self._post("/register", {"redirect_uris": ["http://127.0.0.1:7777/cb"]})
        cid = first["client_id"]
        # 키 없이 같은 client_id 를 요구하면 무시되고 새 id 가 나온다
        _, anon = self._post("/register", {"client_id": cid,
                                           "redirect_uris": ["http://127.0.0.1:7777/new"]})
        self.assertNotEqual(anon["client_id"], cid)
        self.assertEqual(self.oauth.store.get_client(cid)["redirect_uris"],
                         ["http://127.0.0.1:7777/cb"])
        # 키가 맞으면 같은 id 에 redirect 가 갈린다
        _, admin = self._post("/register?key=adminkey",
                              {"client_id": cid,
                               "redirect_uris": ["http://127.0.0.1:7777/new"]})
        self.assertEqual(admin["client_id"], cid)
        self.assertEqual(self.oauth.store.get_client(cid)["redirect_uris"],
                         ["http://127.0.0.1:7777/new"])

    def test_client_id_may_arrive_in_the_basic_header(self):
        """client_secret 을 쥔 클라이언트는 client_id 를 본문이 아니라 헤더로 보낸다.

        PlayMCP(카카오)가 그랬고, 본문만 보던 교환은 `client_id mismatch` 였다.
        """
        _, reg = self._post("/register", {"redirect_uris": ["http://127.0.0.1:7777/cb"]})
        verifier = "v" * 48
        q = urllib.parse.urlencode({
            "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
            "response_type": "code", "code_challenge": _s256(verifier),
            "code_challenge_method": "S256", "key": "adminkey"})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(self.base + "/authorize?" + q, timeout=5)
            location = resp.headers.get("Location")
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        body = urllib.parse.urlencode({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": reg["redirect_uris"][0],
            "code_verifier": verifier}).encode()          # client_id 를 일부러 뺀다
        creds = base64.b64encode(
            (reg["client_id"] + ":none").encode()).decode()
        req = urllib.request.Request(self.base + "/token", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Authorization", "Basic " + creds)
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("access_token", json.loads(r.read()))

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


class HostNeutralInstallTests(unittest.TestCase):
    """등록 헬퍼가 벤더 중립인지 — 같은 병합 로직이 어느 JSON 호스트에나 든다."""

    def test_cursor_uses_the_same_merge_and_preserves_neighbours(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "mcp.json"
            p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}),
                         encoding="utf-8")
            res = install_json("cursor", p)
            cfg = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(res["label"], "Cursor")
            self.assertIn("vinc", cfg["mcpServers"])
            self.assertIn("other", cfg["mcpServers"])          # 남의 서버 보존
            self.assertTrue(uninstall_json("cursor", p)["removed"])
            self.assertNotIn("vinc", json.loads(p.read_text(encoding="utf-8"))["mcpServers"])

    def test_every_declared_client_has_a_label(self):
        for client in CLIENTS:
            self.assertTrue(client_label(client))
        with self.assertRaises(ValueError):
            client_label("nope")

    def test_manual_entry_is_paste_ready(self):
        """레지스트리 밖 호스트(Kimi CLI·VS Code…)용 조각 — 자동 등록과 같은 명령."""
        entry = manual_entry()["mcpServers"]["vinc"]
        self.assertTrue(entry["command"])
        self.assertIn("mcp", entry["args"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "mcp.json"
            self.assertEqual(install_json("cursor", p)["entry"], entry)


if __name__ == "__main__":
    unittest.main()
