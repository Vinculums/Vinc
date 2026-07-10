# -*- coding: utf-8 -*-
"""Share pack(D19) 계약 테스트 — 스코프·스크럽·버전·owner 재스탬핑 (무 DB/FakeStore)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import share  # noqa: E402


class _FakeStore:
    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, dict(params or {})))
        # 가장 구체적인(긴) 키 우선 — 짧은 키의 우연 매칭 방지
        for key in sorted(self.responses, key=len, reverse=True):
            if key in cypher:
                return self.responses[key]
        return []

    def close(self):
        pass


_CONCEPTS = [
    {"id": "concept:a", "title": "A", "domain": "math", "tags": ["core"],
     "topic": "analysis", "one_liner": "one", "props": None},
    {"id": "concept:b", "title": "B", "domain": "math", "tags": ["misc"],
     "topic": None, "one_liner": None, "props": None},
]
_EPISODES = [
    {"id": "ep:1", "title": "E1", "domain": "math", "date": "2026-07-01",
     "summary": "s", "tags": ["core"]},
]
_EDGE_ROWS = [
    {"id": "concept:a", "label": "A", "domain": "math",
     "rel": "RELATED_TO", "target": "concept:b", "target_domain": "math"},
    # 스코프 밖으로 나가는 엣지 — 팩에서 잘려야 함
    {"id": "concept:b", "label": "B", "domain": "math",
     "rel": "RELATED_TO", "target": "concept:out", "target_domain": "art"},
]


class BuildPackTests(unittest.TestCase):
    def _store(self):
        return _FakeStore({
            "one_liner": _CONCEPTS,
            "e.summary AS summary": _EPISODES,
            "OPTIONAL MATCH": _EDGE_ROWS,
        })

    def test_domain_scope_pack_shape(self):
        pack = share.build_share_pack(owner="user:alice", domain="math",
                                      generated_at="2026-07-10T00:00:00Z", store=self._store())
        self.assertTrue(pack["executed"])
        self.assertEqual(pack["vinc_pack"], 1)
        self.assertEqual(pack["manifest"]["counts"],
                         {"concepts": 2, "records": 1, "edges": 1})   # 경계 밖 엣지 절단
        types = {n["type"] for n in pack["fragment"]["nodes"]}
        self.assertEqual(types, {"concept", "episode"})
        self.assertEqual(pack["fragment"]["edges"],
                         [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}])

    def test_no_preference_signals_in_pack(self):
        pack = share.build_share_pack(owner="user:alice", domain="math",
                                      generated_at="x", store=self._store())
        blob = str(pack)
        for banned in ("pinned", "access_count", "last_access"):
            self.assertNotIn(banned, blob)
        # 투영 자체가 preference 열을 선택하지 않는다
        store = self._store()
        share.build_share_pack(owner="user:alice", domain="math", generated_at="x", store=store)
        for cy, _ in store.calls:
            for banned in ("pinned", "access_count", "last_access"):
                self.assertNotIn(banned, cy)

    def test_tag_scope_filters_overlap(self):
        pack = share.build_share_pack(owner="user:alice", domain="math", tags=["core"],
                                      generated_at="x", store=self._store())
        ids = {n["id"] for n in pack["fragment"]["nodes"]}
        self.assertEqual(ids, {"concept:a", "ep:1"})    # 'misc'만 가진 b 제외

    def test_seed_scope_uses_subgraph_and_skips_records(self):
        store = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "concept:b"}],
            "one_liner": _CONCEPTS,
            "OPTIONAL MATCH": _EDGE_ROWS,
        })
        pack = share.build_share_pack(owner="user:alice", seed="concept:a", depth=1,
                                      generated_at="x", store=store)
        self.assertEqual(pack["manifest"]["scope"]["seed"], "concept:a")
        self.assertEqual(pack["manifest"]["counts"]["records"], 0)   # ego 스코프 = 개념 중심
        self.assertTrue(any("TRAIL" in cy for cy, _ in store.calls))

    def test_without_store_degrades(self):
        pack = share.build_share_pack(owner="user:alice", domain="math", store=None)
        if not pack.get("executed"):
            self.assertIn("note", pack)


class ImportPackTests(unittest.TestCase):
    def test_version_check(self):
        res = share.import_share_pack({"vinc_pack": 99}, owner="user:bob", store=_FakeStore())
        self.assertFalse(res["executed"])
        self.assertIn("unsupported pack version", res["error"])

    def test_import_stamps_recipient_owner(self):
        store = _FakeStore()
        pack = {"vinc_pack": 1, "manifest": {"created": "x"},
                "fragment": {"nodes": [{"id": "concept:a", "type": "concept"},
                                       {"id": "ep:1", "type": "episode",
                                        "date": "2026-07-01", "summary": "s"}],
                             "edges": []}}
        res = share.import_share_pack(pack, owner="user:bob", store=store)
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"]["created"], 2)
        merge_params = [p for cy, p in store.calls if cy.startswith("MERGE")]
        self.assertTrue(all(p.get("owner_id") == "user:bob" for p in merge_params))
        # Episode 콘텐츠 필드 사상
        ep = [p for p in merge_params if p.get("id") == "ep:1"][0]
        self.assertEqual(ep.get("date"), "2026-07-01")
        self.assertEqual(ep.get("summary"), "s")
        self.assertEqual(res["manifest"], {"created": "x"})


if __name__ == "__main__":
    unittest.main()
