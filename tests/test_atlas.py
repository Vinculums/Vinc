# -*- coding: utf-8 -*-
"""Atlas(D16·D17)·fragment(D11 §5) 계약 테스트 — 무 DB(플랜 순수성) + FakeStore."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q            # noqa: E402
from api import fragment as frag      # noqa: E402


class _FakeStore:
    """run() 호출을 기록하고 미리 정의된 응답을 돌려주는 스토어 더블."""

    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, dict(params or {})))
        for key, rows in self.responses.items():
            if key in cypher:
                return rows
        return []

    def close(self):
        pass


class SubgraphPlanTests(unittest.TestCase):
    def test_seed_traversal_uses_trail_guard(self):
        plan = q.subgraph_ids_plan("concept:fourier", depth=2)
        self.assertIn("TRAIL 1..2", plan.cypher)          # 엣지 무재사용 가드(M3)
        self.assertEqual(plan.params, {"seed": "concept:fourier"})

    def test_depth_clamped_1_to_4(self):
        self.assertIn("1..1", q.subgraph_ids_plan("x", depth=0).cypher)
        self.assertIn("1..4", q.subgraph_ids_plan("x", depth=9).cypher)

    def test_graph_without_db_returns_plan_only(self):
        res = q.graph(owner="user:t", seed="concept:x", store=None)
        # open_store 가 드라이버 부재로 None 이면 executed=False 계약 유지.
        if not res.get("executed"):
            self.assertEqual(res["nodes"], [])
            self.assertEqual(res["edges"], [])

    def test_graph_seed_prunes_ego_boundary(self):
        fake = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "b"}],
            "IN $ids": [
                {"id": "a", "label": "A", "domain": "math",
                 "rel": "RELATED_TO", "target": "b", "target_domain": "physics"},
                # depth+1 유출 엣지 — target 'z' 는 id 집합 밖 → shape 후 제거돼야 함
                {"id": "b", "label": "B", "domain": "physics",
                 "rel": "RELATED_TO", "target": "z", "target_domain": "art"},
            ],
        })
        res = q.graph(owner="user:t", seed="a", depth=1, store=fake)
        self.assertTrue(res["executed"])
        ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(ids, {"a", "b"})                  # z 는 ego 경계 밖
        self.assertEqual(len(res["edges"]), 1)
        self.assertTrue(all("x" in n and "y" in n for n in res["nodes"]))  # D17 좌표


class LayoutTests(unittest.TestCase):
    def _nodes(self):
        return [{"id": "a", "domain": "math", "degree": 2},
                {"id": "b", "domain": "math", "degree": 5},
                {"id": "c", "domain": "art", "degree": 0}]

    def test_deterministic(self):
        n1 = q.layout_positions(self._nodes(), [])
        n2 = q.layout_positions(self._nodes(), [])
        self.assertEqual([(n["x"], n["y"]) for n in n1],
                         [(n["x"], n["y"]) for n in n2])   # 순수 함수(D17)

    def test_domains_form_separate_clusters(self):
        nodes = q.layout_positions(self._nodes(), [])
        by_id = {n["id"]: n for n in nodes}
        # 허브(b, degree 5)가 math 클러스터 중심(반경 0)에 온다.
        math_center = (by_id["b"]["x"], by_id["b"]["y"])
        art_center = (by_id["c"]["x"], by_id["c"]["y"])
        self.assertNotEqual(math_center, art_center)


class AtlasL0Tests(unittest.TestCase):
    def test_ties_plan_counts_cross_domain_pairs(self):
        plan = q.domain_ties_plan(owner="user:t")
        self.assertIn("a.domain <> b.domain", plan.cypher)
        self.assertIn("count(*)", plan.cypher)

    def test_atlas_l0_combines_domains_and_ties(self):
        fake = _FakeStore({
            "count(*) AS episodes": [{"domain": "math", "episodes": 3}],
            "a.domain <> b.domain": [{"source": "math", "target": "art", "ties": 2}],
            # Bridge 축(실데이터에서 크로스도메인은 :Bridge 가 1급 — 합산돼야 함)
            "MATCH (b:Bridge)": [{"source": "math", "target": "physics", "ties": 5}],
        })
        res = q.atlas_l0(owner="user:t", store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["domains"][0]["domain"], "math")
        self.assertEqual(res["ties"],
                         [{"source": "math", "target": "physics", "ties": 5},
                          {"source": "math", "target": "art", "ties": 2}])


class FragmentMergeTests(unittest.TestCase):
    def test_merges_supported_nodes_and_edges(self):
        fake = _FakeStore()   # count 미응답 → 전부 created 경로
        res = frag.merge_fragment({
            "nodes": [
                {"id": "concept:margiela", "type": "concept", "title": "Maison Margiela",
                 "domain": "fashion", "tags": ["deconstruction"], "props": {"note": "x"}},
                {"id": "concept:deconstruction", "type": "concept"},
                {"id": "record:r1", "type": "record", "title": "R1", "props": {"k": 1}},
            ],
            "edges": [
                {"from": "concept:margiela", "to": "concept:deconstruction", "type": "relates_to"},
                {"from": "concept:margiela", "to": "concept:deconstruction", "type": "inherits_from"},
                {"from": "record:r1", "to": "concept:margiela", "type": "relates_to"},
            ],
        }, store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"]["created"], 3)
        self.assertEqual(res["merged"]["edges"]["created"], 3)
        self.assertEqual(res["warnings"], [])
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("MERGE (n:Concept {concept_id: $id})", joined)
        self.assertIn("MERGE (n:Record {record_id: $id})", joined)
        self.assertIn(":INHERITS_FROM", joined)            # §3.2 공백 해소
        self.assertIn(":ATTACHES_TO", joined)              # record 참여 엣지
        self.assertIn('"note": "x"', joined + str([p for _, p in fake.calls]))

    def test_unsupported_types_warn_and_skip(self):
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "x", "type": "policy"}],       # enum 밖 노드
            "edges": [{"from": "a", "to": "b", "type": "blocks"}],  # enum 밖 엣지
        }, store=fake)
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertEqual(len(res["warnings"]), 2)

    def test_no_store_degrades(self):
        res = frag.merge_fragment({"nodes": []}, store=None)
        self.assertFalse(res["executed"])

    def test_matched_counted_on_existing_node(self):
        fake = _FakeStore({"RETURN count(n) AS c": [{"c": 1}]})
        res = frag.merge_fragment(
            {"nodes": [{"id": "concept:a", "type": "concept"}]}, store=fake)
        self.assertEqual(res["merged"]["nodes"], {"created": 0, "matched": 1})

    def test_reMERGE_without_owner_does_not_clobber_owner_id(self):
        # owner 미지정 재-MERGE 가 기존 노드 owner_id 를 기본값으로 덮어쓰면 스코프
        # 필터(§1 불변)가 깨진다 — 생성 시/명시 시에만 스탬핑.
        fake = _FakeStore({"RETURN count(n) AS c": [{"c": 1}]})   # 기존 노드
        frag.merge_fragment({"nodes": [{"id": "concept:a", "type": "concept"}]}, store=fake)
        merge_params = [p for c, p in fake.calls if c.startswith("MERGE")]
        self.assertTrue(all("owner_id" not in p for p in merge_params))
        # 명시 owner 는 기존 노드여도 스탬핑
        fake2 = _FakeStore({"RETURN count(n) AS c": [{"c": 1}]})
        frag.merge_fragment({"nodes": [{"id": "concept:a", "type": "concept"}],
                             "owner": "user:t"}, store=fake2)
        merge_params2 = [p for c, p in fake2.calls if c.startswith("MERGE")]
        self.assertTrue(any(p.get("owner_id") == "user:t" for p in merge_params2))


if __name__ == "__main__":
    unittest.main()
