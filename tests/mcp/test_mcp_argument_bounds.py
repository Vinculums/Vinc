# -*- coding: utf-8 -*-
"""MCP 인자 경계·커서·표면(P4) — 거절은 도구 결과, 쿼터 전에, 자르지 않는다.

계약 정본: [[decision:mcp-argument-errors-are-tool-results-not-protocol-errors-2026-09-24]]
패키지: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §5 P4 (S10·S11 limits·S13).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.rpc import Dispatcher  # noqa: E402

#: 감사 재현 인자 — `{"v":1,"off":-5,"tc":350}`. 여태 오프셋 0 으로 조용히 당겨졌다.
FORGED = "eyJ2IjoxLCJvZmYiOi01LCJ0YyI6MzUwfQ"


class _CapBridge:
    owner = "user:local"

    def __init__(self, reply=None):
        self.calls: list[tuple] = []
        self.reply = reply if reply is not None else {"ok": True}

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return self.reply


def _call(d, name, arguments, **kw):
    return d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": arguments}}, **kw)


class RangeRefusalTests(unittest.TestCase):
    def test_limit_101_봉투(self):
        b = _CapBridge()
        r = _call(Dispatcher(b), "vinc_search", {"query": "x", "limit": 101})
        self.assertNotIn("error", r)                 # -32602 아님
        res = r["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(
            res["content"][0]["text"],
            "Invalid argument: limit=101 is outside 1..100 for vinc_search. Nothing was run. "
            "Retry with limit between 1 and 100, and continue with next_cursor for more.")
        self.assertEqual(res["_meta"]["vinc_invalid_argument"],
                         {"tool": "vinc_search", "argument": "limit", "value": 101,
                          "minimum": 1, "maximum": 100})
        self.assertEqual(b.calls, [])                # 아무것도 안 돌았다

    def test_하한_0_과_다른_도구의_상한(self):
        b = _CapBridge()
        d = Dispatcher(b)
        for name, args, arg in [
            ("vinc_search", {"query": "x", "limit": 0}, "limit"),
            ("vinc_neighbors", {"node": "concept:x", "limit": 501}, "limit"),
            ("vinc_recall", {"limit": 201}, "limit"),
            ("vinc_graph", {"seed_id": "concept:x", "depth": 5}, "depth"),
            ("vinc_brief", {"query": "x", "max_excerpts": 13}, "max_excerpts"),
            ("vinc_merge_candidates", {"min_score": 1.5}, "min_score"),
        ]:
            with self.subTest(name=name):
                res = _call(d, name, args)["result"]
                self.assertTrue(res["isError"])
                self.assertEqual(res["_meta"]["vinc_invalid_argument"]["argument"], arg)
        self.assertEqual(b.calls, [])

    def test_호스트_search_상한_50(self):
        b = _CapBridge()
        res = _call(Dispatcher(b, allow_target=False), "search",
                    {"query": "x", "limit": 51})["result"]
        self.assertEqual(res["_meta"]["vinc_invalid_argument"]["maximum"], 50)
        self.assertEqual(b.calls, [])

    def test_정수가_아닌_값과_enum(self):
        d = Dispatcher(_CapBridge())
        res = _call(d, "vinc_search", {"query": "x", "limit": "many"})["result"]
        self.assertTrue(res["isError"])
        res = _call(d, "vinc_search", {"query": "x", "detail": "everything"})["result"]
        self.assertEqual(res["_meta"]["vinc_invalid_argument"]["argument"], "detail")

    def test_경계_안은_통과한다(self):
        b = _CapBridge()
        d = Dispatcher(b)
        for lim in (1, 100, "20"):
            res = _call(d, "vinc_search", {"query": "x", "limit": lim})["result"]
            self.assertFalse(res["isError"])
        self.assertEqual(len(b.calls), 3)

    def test_필수_인자_누락_봉투(self):
        res = _call(Dispatcher(_CapBridge()), "vinc_neighbors", {})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["_meta"]["vinc_invalid_argument"],
                         {"tool": "vinc_neighbors", "argument": "node",
                          "reason": "missing required argument"})

    def test_InvalidArgument_는_ValueError_가_아니다(self):
        # 상속하면 rpc 의 (KeyError, ValueError) → -32602 경로가 삼킨다.
        self.assertFalse(issubclass(tools.InvalidArgument, ValueError))


class QuotaOrderTests(unittest.TestCase):
    def test_거절된_호출은_쿼터를_세지_않는다(self):
        charged: list = []

        def quota(owner, plan):
            charged.append(owner)
            return None

        b = _CapBridge()
        d = Dispatcher(b, allow_target=False)
        _call(d, "vinc_search", {"query": "x", "limit": 101},
              forced_owner="user:u", quota=quota)
        _call(d, "vinc_search", {}, forced_owner="user:u", quota=quota)
        _call(d, "vinc_doc_list", {"cursor": FORGED}, forced_owner="user:u", quota=quota)
        self.assertEqual(charged, [])
        _call(d, "vinc_search", {"query": "x"}, forced_owner="user:u", quota=quota)
        self.assertEqual(charged, ["user:u"])

    def test_모르는_도구도_쿼터_전에_프로토콜_오류(self):
        charged: list = []
        r = _call(Dispatcher(_CapBridge()), "vinc_nope", {}, forced_owner="user:u",
                  quota=lambda o, p: charged.append(o))
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(charged, [])


class CursorTests(unittest.TestCase):
    def test_위조_커서는_MCP_층에서_거절(self):
        b = _CapBridge()
        res = _call(Dispatcher(b), "vinc_doc_list", {"cursor": FORGED})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["_meta"]["vinc_invalid_argument"],
                         {"tool": "vinc_doc_list", "argument": "cursor",
                          "reason": "malformed cursor"})
        self.assertEqual(b.calls, [])
        for junk in ("not-base64!!", "eyJ2IjoyfQ"):          # 쓰레기 · 모르는 버전
            res = _call(Dispatcher(b), "vinc_search", {"query": "x", "cursor": junk})["result"]
            self.assertEqual(res["_meta"]["vinc_invalid_argument"]["reason"], "malformed cursor")

    def test_남의_질의_커서는_사이드카_판정을_같은_봉투로(self):
        good_shape = tools._cursor_encode({"v": 1, "off": 30, "qh": "0" * 12})
        b = _CapBridge({"kind": "doc.list", "executed": False, "docs": [], "count": 0,
                        "error": "invalid_cursor: cursor was issued for a different "
                                 "query/scope"})
        res = _call(Dispatcher(b), "vinc_doc_list", {"cursor": good_shape})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["_meta"]["vinc_invalid_argument"]["reason"],
                         "cursor does not belong to this query")


class DocListCursorE2E(unittest.TestCase):
    """실엔진 — list_docs 커서가 목록 지문을 싣고, 남의 것·음수·위조를 거절한다."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        from api import docstore as ds
        self.ds = ds
        for i in range(3):
            ds.put_doc(self.store, owner="user:a", doc_id=f"d{i}", title=f"D{i}",
                       text=f"document number {i}", link=False)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_정상_페이지는_끝까지_이어진다(self):
        seen, cur = [], None
        while True:
            out = self.ds.list_docs(self.store, owner="user:a", limit=1, cursor=cur,
                                    detail="summary")
            seen += [d["doc_id"] for d in out["docs"]]
            cur = out["next_cursor"]
            if not cur:
                break
        self.assertEqual(sorted(seen), ["d0", "d1", "d2"])

    def test_커서_없는_호출은_형태가_그대로다(self):
        out = self.ds.list_docs(self.store, owner="user:a")
        self.assertNotIn("next_cursor", out)
        self.assertEqual(out["count"], 3)

    def test_거절(self):
        first = self.ds.list_docs(self.store, owner="user:a", limit=1, detail="summary")
        cur = first["next_cursor"]
        for kw in ({"owner": "user:b", "detail": "summary"},     # 남의 owner
                   {"owner": "user:a", "detail": "full"}):       # 다른 detail
            with self.subTest(**kw), self.assertRaisesRegex(ValueError, "^invalid_cursor"):
                self.ds.list_docs(self.store, limit=1, cursor=cur, **kw)
        with self.assertRaisesRegex(ValueError, "^invalid_cursor"):
            self.ds.list_docs(self.store, owner="user:a", limit=1, cursor=FORGED,
                              detail="summary")
        neg = tools._cursor_encode({"v": 1, "off": -5, "tc": 3,
                                    "qh": _qh("user:a", "summary")})
        with self.assertRaisesRegex(ValueError, "^invalid_cursor"):
            self.ds.list_docs(self.store, owner="user:a", limit=1, cursor=neg,
                              detail="summary")


def _qh(owner, detail):
    from api.query import _query_fingerprint
    return _query_fingerprint("doc.list", owner, None, detail)


class DebugGateTests(unittest.TestCase):
    def test_호스티드는_debug_를_벗긴다(self):
        b = _CapBridge()
        tools.call("vinc_domains", {"debug": True}, b, forced_owner="user:u",
                   allow_target=False)
        tools.call("vinc_recall", {"debug": True}, b, forced_owner="user:u",
                   allow_target=False)
        for _m, _p, params, _b in b.calls:
            self.assertIsNone(params.get("debug"))

    def test_로컬_사이드카는_debug_를_유지한다(self):
        b = _CapBridge()
        tools.call("vinc_domains", {"debug": True}, b)
        self.assertEqual(b.calls[0][2]["debug"], "true")

    def test_허브_목록은_debug_를_광고하지_않는다(self):
        hub = {t["name"]: t for t in tools.specs(allow_target=False)}
        local = {t["name"]: t for t in tools.specs(allow_target=True)}
        for name in ("vinc_recall", "vinc_domains"):
            self.assertNotIn("debug", hub[name]["inputSchema"]["properties"])
            self.assertIn("debug", local[name]["inputSchema"]["properties"])


class ServerHeaderTests(unittest.TestCase):
    def test_Server_헤더에_런타임_버전이_없다(self):
        app = MCPHTTPApp(Bridge(base="http://127.0.0.1:9", token="t"), static_token="s")
        httpd = make_server(app, "127.0.0.1", 0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=10) as r:
            self.assertEqual(r.headers.get("Server"), "vinc-mcp")


if __name__ == "__main__":
    unittest.main()
