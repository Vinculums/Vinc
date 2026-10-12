# -*- coding: utf-8 -*-
"""전체 그래프 읽기의 결과 캐시(`api.graph_cache`) — 깨져도 조용하다.

틀리면 쓰기 뒤에도 옛 그래프가 나가거나(표식을 안 보거나 읽은 뒤에 잡은 경우), 한
소유자의 항목이 다른 소유자의 읽기에 닿는다. 그래서 키와 무효화를 따로 고정한다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import mirror_provenance  # noqa: E402
from api.graph_cache import GraphResultCache, cache_key  # noqa: E402
from api.server import VincApp  # noqa: E402
from api.auth import SessionAuth  # noqa: E402


class _Store:
    driver_name = "fake"

    def reader_view(self):
        return self

    def run(self, cypher, params=None):
        return []

    def close(self):
        pass


def _app():
    return VincApp(store=_Store(), auth=SessionAuth("t"), quiet_boot=True)


class KeyTests(unittest.TestCase):
    def test_stage_controls_do_not_change_the_key(self):
        a = cache_key({"owner": "user:a", "layout": "hub", "stage": "preview"})
        b = cache_key({"owner": "user:a", "layout": "hub", "stage": "complete", "snapshot": "x"})
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_any_other_argument_changes_the_key(self):
        base = {"owner": "user:a", "layout": "hub"}
        for extra in ({"cone": "0.8"}, {"domains": "x"}, {"basis": "content"}, {"depth": "3"}):
            self.assertNotEqual(cache_key(base), cache_key({**base, **extra}))

    def test_point_in_time_seed_and_budget_reads_are_never_cached(self):
        base = {"owner": "user:a", "layout": "hub"}
        for extra in ({"seed": "concept:x"}, {"max_nodes": "50"}, {"as_of": "2026-09"},
                      {"known_at": "2026-09"}, {"historical": "1"}, {"review_mirror": "1"}):
            self.assertIsNone(cache_key({**base, **extra}), extra)
        self.assertIsNone(cache_key({"layout": "hub"}))


class CacheTests(unittest.TestCase):
    def test_marker_and_owner_isolate_entries(self):
        c = GraphResultCache()
        self.assertTrue(c.put("a", "k", "e.1", "A1"))
        self.assertEqual(c.get("a", "k", "e.1"), "A1")
        self.assertIsNone(c.get("a", "k", "e.2"))      # 표식이 바뀌면 다시 읽히지 않는다
        self.assertIsNone(c.get("a", "k", "e.1"))      # 낡은 항목은 그때 버려진다
        c.put("a", "k", "e.2", "A2")
        c.put("b", "k", "e.2", "B2")
        self.assertEqual(c.get("b", "k", "e.2"), "B2")
        self.assertEqual(c.get("a", "k", "e.2"), "A2")

    def test_ttl_entries_and_bytes_are_bounded(self):
        now = [0.0]
        c = GraphResultCache(ttl=10, max_entries=2, max_bytes=10, clock=lambda: now[0])
        c.put("a", "1", "m", "1234")
        now[0] = 11
        self.assertIsNone(c.get("a", "1", "m"))
        now[0] = 12
        c.put("a", "1", "m", "1234"); c.put("a", "2", "m", "1234"); c.put("a", "3", "m", "1234")
        self.assertLessEqual(len(c), 2)
        self.assertLessEqual(c.size_bytes, 10)
        self.assertFalse(c.put("a", "big", "m", "x" * 11))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = 0
        self.app = _app()

        def fake(**kwargs):
            self.calls += 1
            return {"executed": True, "nodes": [{"id": kwargs["owner"], "n": self.calls}], "edges": []}
        patcher = mock.patch.object(mirror_provenance, "graph_with_mirror_review", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.app.close)

    def read(self, **extra):
        code, body = self.app.read_graph({"owner": "user:a", "layout": "hub", **extra})
        self.assertEqual(code, 200)
        return body

    def test_second_read_is_served_from_the_cache_and_is_a_fresh_object(self):
        first = self.read()
        first["nodes"].append({"id": "mutated"})        # 호출부가 고쳐도 번지지 않는다
        second = self.read()
        self.assertEqual(self.calls, 1)
        self.assertEqual(second["nodes"], [{"id": "user:a", "n": 1}])

    def test_a_successful_write_for_that_owner_forces_a_recompute(self):
        self.read()
        self.app.note_write("/ingest/fragment", "user:a", 200, {})
        self.assertEqual(self.read()["nodes"][0]["n"], 2)
        self.app.note_write("/touch", "user:a", 200, {})   # 조용한 쓰기는 세지 않는다
        self.read()
        self.assertEqual(self.calls, 2)

    def test_another_owners_write_does_not_invalidate_and_owners_do_not_share(self):
        self.read()
        self.app.note_write("/ingest/fragment", "user:b", 200, {})
        self.read()
        self.assertEqual(self.calls, 1)
        code, other = self.app.read_graph({"owner": "user:b", "layout": "hub"})
        self.assertEqual(other["nodes"][0]["id"], "user:b")
        self.assertEqual(self.calls, 2)

    def test_an_unowned_write_invalidates_every_owner(self):
        self.read()
        self.app.note_write("/ingest/fragment", None, 200, {})
        self.read()
        self.assertEqual(self.calls, 2)

    def test_failed_results_and_uncacheable_reads_are_not_stored(self):
        self.read(seed="concept:x"); self.read(seed="concept:x")
        self.assertEqual(self.calls, 2)
        with mock.patch.object(mirror_provenance, "graph_with_mirror_review",
                               lambda **k: {"executed": False, "error": "x"}):
            self.read(layout="rank"); self.read(layout="rank")
        self.assertEqual(len(self.app._graph_results), 0)


if __name__ == "__main__":
    unittest.main()
