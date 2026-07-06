# -*- coding: utf-8 -*-
"""vinc_mcp/tools.py 순수 단위 테스트 — DB·사이드카·mcp SDK 불요.

`validate_fragment`(D11 §5 형태 검증)와 `VincTools`(가짜 bridge 주입)의 계약
검증→호출→로그 배선을 검증한다. 실제 서버 등록(FastMCP)은 mcp SDK 설치 환경에서만
도는 test_vinc_mcp_server.py 소관.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import tools as t  # noqa: E402


class ValidateFragmentShapeTests(unittest.TestCase):
    def test_valid_fragment_passes_through(self):
        frag = {"nodes": [{"id": "concept:a", "type": "concept", "title": "A"}],
                "edges": [], "owner": "user:me"}
        out, warnings = t.validate_fragment_shape(frag)
        self.assertEqual(out, frag)
        self.assertEqual(warnings, [])

    def test_no_type_enum_gate_unknown_type_passes(self):
        # 타입은 전역 enum 으로 검증하지 않는다 — 어휘 밖 타입도 통과(서버가 편입).
        out, warnings = t.validate_fragment_shape(
            {"nodes": [{"id": "x", "type": "해체주의", "title": "T"}]})
        self.assertEqual([n["id"] for n in out["nodes"]], ["x"])
        self.assertEqual(warnings, [])

    def test_unknown_edge_type_passes_through(self):
        nodes = [{"id": "a", "type": "T", "title": "A"},
                 {"id": "b", "type": "T", "title": "B"}]
        out, warnings = t.validate_fragment_shape(
            {"nodes": nodes, "edges": [{"from": "a", "to": "b", "type": "영향줌"}]})
        self.assertEqual(len(out["edges"]), 1)
        self.assertEqual(warnings, [])

    def test_node_missing_title_skipped(self):
        out, warnings = t.validate_fragment_shape({"nodes": [{"id": "x", "type": "concept"}]})
        self.assertEqual(out["nodes"], [])
        self.assertTrue(any("title required" in w for w in warnings))

    def test_edge_endpoint_not_a_valid_node_skipped(self):
        nodes = [{"id": "a", "type": "concept", "title": "A"}]
        out, warnings = t.validate_fragment_shape(
            {"nodes": nodes, "edges": [{"from": "a", "to": "ghost", "type": "relates_to"}]})
        self.assertEqual(out["edges"], [])
        self.assertTrue(any("not in valid node set" in w for w in warnings))

    def test_owner_omitted_when_absent(self):
        out, _ = t.validate_fragment_shape({"nodes": []})
        self.assertNotIn("owner", out)

    def test_no_global_type_enum_constants_remain(self):
        self.assertFalse(hasattr(t, "_VALID_NODE_TYPES"))
        self.assertFalse(hasattr(t, "_VALID_EDGE_TYPES"))


class _FakeBridge:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.post_response = {"merged": {"nodes": {"created": 1, "matched": 0},
                                         "edges": {"created": 0}}, "warnings": []}

    def get(self, path, params=None):
        self.get_calls.append((path, params))
        return {"kind": "fake", "path": path, "params": params}

    def post(self, path, body):
        self.post_calls.append((path, body))
        return dict(self.post_response)


class VincToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bridge = _FakeBridge()
        self.tools = t.VincTools(self.bridge, data_dir=self.tmp, session_id="sess-test")

    def _log_lines(self):
        path = Path(self.tmp) / "logs" / "mcp-audit.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_vinc_search_maps_limit_to_top_k(self):
        self.tools.vinc_search("margiela", limit=7, domain="fashion")
        path, params = self.bridge.get_calls[0]
        self.assertEqual(path, "/search")
        self.assertEqual(params, {"query": "margiela", "top_k": 7, "domain": "fashion"})

    def test_vinc_recall_maps_tag_singular_to_tags_param(self):
        self.tools.vinc_recall(tag="deconstruction", owner="user:me")
        path, params = self.bridge.get_calls[0]
        self.assertEqual(path, "/recall")
        self.assertEqual(params["tags"], "deconstruction")
        self.assertEqual(params["owner"], "user:me")

    def test_vinc_graph_returns_explicit_not_implemented_without_bridge_call(self):
        res = self.tools.vinc_graph("concept:a", depth=3)
        self.assertFalse(res["executed"])
        self.assertTrue(any("seed-based traversal" in w for w in res["warnings"]))
        self.assertEqual(self.bridge.get_calls, [])   # 사이드카 호출 없음(오매핑 회피)

    def test_vinc_decide_calls_decide_endpoint(self):
        self.tools.vinc_decide("g5.b.billing", depth=1)
        path, params = self.bridge.get_calls[0]
        self.assertEqual(path, "/decide")
        self.assertEqual(params, {"topic": "g5.b.billing", "depth": 1})

    def test_vinc_ingest_threads_theme_id_and_keeps_unknown_types(self):
        res = self.tools.vinc_ingest({
            "nodes": [{"id": "a", "type": "concept", "title": "A"},
                      {"id": "b", "type": "해체주의", "title": "X"}]},
            theme_id="fashion")
        path, body = self.bridge.post_calls[0]
        self.assertEqual(path, "/ingest")
        self.assertEqual(body["theme_id"], "fashion")
        # 어휘 밖 타입도 제거되지 않고 서버로 전달된다(enum 게이트 없음)
        self.assertEqual([n["id"] for n in body["nodes"]], ["a", "b"])
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})

    def test_vinc_ingest_without_theme_id_rejected_without_bridge_call(self):
        res = self.tools.vinc_ingest({"nodes": []}, theme_id="")
        self.assertFalse(res["executed"])
        self.assertTrue(res["rejected"])
        self.assertEqual(self.bridge.post_calls, [])

    def test_theme_and_type_tools_hit_expected_routes(self):
        self.tools.vinc_themes_create("t1", "Title", ["q?"])
        self.assertEqual(self.bridge.post_calls[-1][0], "/themes")
        self.tools.vinc_themes_activate("t1")
        self.assertEqual(self.bridge.post_calls[-1][0], "/themes/activate")
        self.tools.vinc_types_ratify("t1", "해체주의")
        self.assertEqual(self.bridge.post_calls[-1], ("/types/ratify",
                                                      {"theme_id": "t1", "name": "해체주의"}))
        self.tools.vinc_types_rename("t1", "a", "b")
        self.assertEqual(self.bridge.post_calls[-1][1],
                         {"theme_id": "t1", "from_name": "a", "to_name": "b"})
        self.tools.vinc_types_list("t1", status="proposed")
        self.assertEqual(self.bridge.get_calls[-1][0], "/types")
        self.tools.vinc_themes_list()
        self.assertEqual(self.bridge.get_calls[-1][0], "/themes")

    def test_every_call_writes_one_audit_line(self):
        self.tools.vinc_search("x")
        self.tools.vinc_recall()
        self.tools.vinc_decide("t")
        self.tools.vinc_ingest({"nodes": []}, theme_id="t1")
        self.tools.vinc_types_ratify("t1", "x")
        lines = self._log_lines()
        self.assertEqual(len(lines), 5)
        self.assertEqual([l["tool"] for l in lines],
                         ["vinc_search", "vinc_recall", "vinc_decide",
                          "vinc_ingest", "vinc_types_ratify"])
        self.assertTrue(all(l["session"] == "sess-test" for l in lines))


if __name__ == "__main__":
    unittest.main()
