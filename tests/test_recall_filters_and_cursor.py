# -*- coding: utf-8 -*-
"""vinc_recall 의 네 계약 — 태그 필터 · 주제 하위 트리 · 날짜 없는 행은 끝 · 커서 페이징.

2026-09-24 감사에서 넷 다 어긋나 있었다: 없는 태그를 줘도 전량이 나왔고(tags 는
preference 점수에만 쓰였다), `domain=vinc/agent` 는 자식 주제의 에피소드를 0건으로
답했고, 날짜 없는 에피소드가 "가장 최근"으로 맨 위에 섰고, limit 200 을 넘는 타임라인은
끝까지 읽을 길이 없었다. 정렬은 **실엔진에서만** 판정된다(NULL 이 어디로 가는지는
엔진이 정한다) — 그래서 여기 대부분이 ladybug 왕복이다.
근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S1·S2·S3.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402
from vinc_mcp import tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

_DRIVER, _NAME = _import_driver()


# ── 플랜 모양(엔진 없이) ─────────────────────────────────────────────────────
class RecallPlanShape(unittest.TestCase):
    def test_tags_become_an_any_overlap_filter_on_recency(self):
        plan = q.recall_plan(owner="u", tags=["a", "b", "a"])
        self.assertIn("list_contains(coalesce(e.tags, []), $tag0)", plan.cypher)
        self.assertIn("list_contains(coalesce(e.tags, []), $tag1)", plan.cypher)
        self.assertNotIn("$tag2", plan.cypher)            # 중복 태그는 한 번
        self.assertEqual((plan.params["tag0"], plan.params["tag1"]), ("a", "b"))

    def test_preference_keeps_tags_as_a_boost_not_a_filter(self):
        """컨텍스트 팩(activate)은 preference 로 내보낸다 — 걸러 버리면 팩이 준다."""
        plan = q.recall_plan(owner="u", tags=["a"], rank="preference")
        self.assertNotIn("list_contains", plan.cypher)

    def test_offset_adds_skip_only_when_positive(self):
        self.assertNotIn("SKIP", q.recall_plan(owner="u", offset=0).cypher)
        plan = q.recall_plan(owner="u", offset=7, limit=3)
        self.assertIn("SKIP $skip LIMIT $limit", plan.cypher)
        self.assertEqual(plan.params["skip"], 7)


# ── MCP 배선 ───────────────────────────────────────────────────────────────
class _RecordingBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params))
        return {"kind": "recall", "executed": True, "rows": []}


class RecallToolWiring(unittest.TestCase):
    def test_tool_asks_for_pages_and_forwards_the_cursor(self):
        # 모양이 맞는 커서(v1·off 1)를 준다 — 인자 검사가 모양 틀린 커서를 사이드카 전에 거절한다(#392).
        b = _RecordingBridge()
        tools.call("vinc_recall", {"tags": ["x", "y"], "cursor": "eyJ2IjoxLCJvZmYiOjF9"}, b)
        _, path, params = b.calls[-1]
        self.assertEqual(path, "/recall")
        self.assertEqual(params["page"], "true")
        self.assertEqual(params["cursor"], "eyJ2IjoxLCJvZmYiOjF9")
        self.assertEqual(params["tags"], "x,y")

    def test_schema_and_description_name_the_cursor(self):
        spec = next(s for s in tools.specs() if s["name"] == "vinc_recall")
        self.assertIn("cursor", spec["inputSchema"]["properties"])
        for word in ("next_cursor", "has_more", "subtree"):
            self.assertIn(word, spec["description"])

    def test_narrow_hint_mentions_the_cursor(self):
        from vinc_mcp import rpc
        self.assertIn("next_cursor", rpc._NARROW_HINT["vinc_recall"])


# ── 실엔진 ─────────────────────────────────────────────────────────────────
@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RecallE2E(unittest.TestCase):
    OWNER = "user:a"

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
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "episode:reg", "type": "episode", "title": "registered",
             "date": "2026-09-21", "tags": ["playmcp", "oauth"], "domain": "vinc/agent/roles"},
            {"id": "episode:root", "type": "episode", "title": "root",
             "date": "2026-09-20", "tags": ["x"], "domain": "vinc/agent"},
            {"id": "episode:sib", "type": "episode", "title": "sibling prefix",
             "date": "2026-09-22", "domain": "vinc/agentx"},
            {"id": "episode:nodate", "type": "episode", "title": "undated",
             "tags": ["x"], "domain": "vinc/agent"},
            {"id": "episode:old", "type": "episode", "title": "old",
             "date": "2026-01-01", "domain": "other"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ids(self, **kw):
        res = q.recall(owner=self.OWNER, store=self.store, **kw)
        self.assertTrue(res.get("executed"), res)
        return [r["id"] for r in res["rows"]]

    def test_tag_filter_keeps_only_overlapping_episodes(self):
        self.assertEqual(self._ids(tags=["playmcp"]), ["episode:reg"])
        self.assertEqual(self._ids(tags=["nonexistent-tag"]), [])
        self.assertEqual(self._ids(tags=["playmcp", "x"]),
                         ["episode:reg", "episode:root", "episode:nodate"])

    def test_domain_is_a_subtree_not_a_prefix_string(self):
        self.assertEqual(self._ids(domain="vinc/agent"),
                         ["episode:reg", "episode:root", "episode:nodate"])  # vinc/agentx 제외

    def test_undated_rows_come_last(self):
        ids = self._ids(limit=10)
        self.assertEqual(ids[-1], "episode:nodate")
        self.assertEqual(ids[:4], ["episode:sib", "episode:reg", "episode:root", "episode:old"])

    def test_preference_tie_break_is_still_recency_with_undated_last(self):
        res = q.recall(owner=self.OWNER, store=self.store, rank="preference", limit=10)
        self.assertEqual(res["rows"][0]["id"], "episode:sib")
        self.assertEqual(res["rows"][-1]["id"], "episode:nodate")

    def test_no_cursor_keeps_the_old_shape(self):
        res = q.recall(owner=self.OWNER, store=self.store, limit=2)
        self.assertNotIn("has_more", res)
        self.assertNotIn("next_cursor", res)

    def test_cursor_pages_to_the_end(self):
        seen, cursor, pages = [], None, 0
        while True:
            res = q.recall(owner=self.OWNER, store=self.store, limit=2,
                           page=True, cursor=cursor)
            self.assertTrue(res["executed"], res)
            seen += [r["id"] for r in res["rows"]]
            pages += 1
            if not res["has_more"]:
                self.assertIsNone(res["next_cursor"])
                break
            cursor = res["next_cursor"]
        self.assertEqual(pages, 3)
        self.assertEqual(seen, self._ids(limit=10))

    def test_preference_pages_the_same_way(self):
        first = q.recall(owner=self.OWNER, store=self.store, limit=3, page=True,
                         rank="preference")
        second = q.recall(owner=self.OWNER, store=self.store, limit=3, page=True,
                          rank="preference", cursor=first["next_cursor"])
        ids = [r["id"] for r in first["rows"] + second["rows"]]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertFalse(second["has_more"])

    def test_cursor_from_another_query_is_refused(self):
        res = q.recall(owner=self.OWNER, store=self.store, limit=1, page=True)
        other = q.recall(owner=self.OWNER, store=self.store, limit=1,
                         domain="vinc/agent", cursor=res["next_cursor"])
        self.assertFalse(other["executed"])
        self.assertTrue(other["error"].startswith("invalid_cursor"))
        self.assertEqual(other["rows"], [])

    def test_search_cursor_is_not_a_recall_cursor(self):
        c = q.cursor_encode({"v": 1, "off": 1, "qh": q._query_fingerprint(
            "", self.OWNER, None, "summary")})
        res = q.recall(owner=self.OWNER, store=self.store, cursor=c)
        self.assertTrue(res["error"].startswith("invalid_cursor"))

    def test_garbage_and_negative_cursors_are_refused(self):
        self.assertTrue(q.recall(owner=self.OWNER, store=self.store,
                                 cursor="!!!")["error"].startswith("invalid_cursor"))
        qh = q._recall_fingerprint("", self.OWNER, domain=None, tenant=None,
                                   workspace=None, tags=None, rank="recency")
        neg = q.cursor_encode({"v": 1, "off": -5, "qh": qh})
        self.assertTrue(q.recall(owner=self.OWNER, store=self.store,
                                 cursor=neg)["error"].startswith("invalid_cursor"))

    def test_changed_timeline_makes_the_cursor_stale(self):
        res = q.recall(owner=self.OWNER, store=self.store, limit=2, page=True)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "episode:newest", "type": "episode", "title": "newest",
             "date": "2026-12-31"}]}, store=self.store)
        nxt = q.recall(owner=self.OWNER, store=self.store, limit=2,
                       cursor=res["next_cursor"])
        self.assertFalse(nxt["executed"])
        self.assertTrue(nxt["error"].startswith("stale_cursor"))


if __name__ == "__main__":
    unittest.main()
