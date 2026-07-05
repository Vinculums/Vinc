# -*- coding: utf-8 -*-
"""Tests for the query API (plan builders + dry execution)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402


class PlanTests(unittest.TestCase):
    def test_recall_plan_workspace_filter(self):
        plan = q.recall_plan("billing", workspace="planning", limit=5)
        self.assertEqual(plan.kind, "recall")
        self.assertIn("e.workspace = $workspace", plan.cypher)
        self.assertEqual(plan.params["workspace"], "planning")
        self.assertEqual(plan.params["limit"], 5)

    def test_recall_plan_no_filters(self):
        plan = q.recall_plan()
        self.assertNotIn("WHERE", plan.cypher)

    def test_recall_plan_owner_scope(self):
        plan = q.recall_plan(owner="user:weg1456", limit=5)
        self.assertIn("e.owner_id = $owner", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:weg1456")
        # owner is also projected so callers can see ownership
        self.assertIn("e.owner_id AS owner", plan.cypher)

    def test_recall_plan_owner_tenant_workspace_anded(self):
        plan = q.recall_plan("billing", owner="u1", tenant="acme", workspace="planning")
        for frag in ("e.owner_id = $owner", "e.tenant_id = $tenant", "e.workspace = $workspace"):
            self.assertIn(frag, plan.cypher)
        self.assertEqual(plan.cypher.count(" AND "), 3)  # owner AND tenant AND workspace AND query

    def test_recall_domain_filter_is_open(self):
        # domain is a free string filter — any value the learner used, no enum
        plan = q.recall_plan(owner="user:doheon", domain="art")
        self.assertIn("e.domain = $domain", plan.cypher)
        self.assertEqual(plan.params["domain"], "art")
        self.assertIn("e.domain AS domain", plan.cypher)  # projected too

    def test_recall_no_domain_spans_all(self):
        plan = q.recall_plan(owner="user:doheon")
        self.assertNotIn("e.domain = $domain", plan.cypher)
        self.assertNotIn("domain", plan.params)

    def test_domains_discovery_is_data_driven(self):
        plan = q.domains_plan(owner="user:doheon")
        self.assertEqual(plan.kind, "domains")
        self.assertIn("e.domain AS domain", plan.cypher)
        self.assertIn("count(*) AS episodes", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:doheon")

    def test_domains_all_owners(self):
        plan = q.domains_plan()
        self.assertNotIn("owner", plan.params)
        self.assertIn("e.domain IS NOT NULL", plan.cypher)

    def test_learner_model_plan_owner_scoped(self):
        plan = q.learner_model_plan(owner="user:doheon")
        self.assertEqual(plan.kind, "learner_model")
        self.assertIn("MATCH (p:LearnerPattern)", plan.cypher)
        self.assertIn("p.owner_id = $owner", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:doheon")

    def test_graph_plan_owner_and_domain(self):
        plan = q.graph_plan(owner="user:doheon", domain="math")
        self.assertEqual(plan.kind, "graph")
        self.assertIn("MATCH (c:Concept)", plan.cypher)
        self.assertIn("c.owner_id = $owner", plan.cypher)
        self.assertIn("c.domain = $domain", plan.cypher)
        self.assertIn("OPTIONAL MATCH (c)-[r]->(d:Concept)", plan.cypher)


class ShapeGraphTests(unittest.TestCase):
    ROWS = [
        {"id": "math-01", "label": "Lie群", "domain": "math", "rel": "RELATED_TO",
         "target": "math-02", "target_domain": "math"},
        {"id": "math-01", "label": "Lie群", "domain": "math", "rel": "BRIDGE",
         "target": "phys-01", "target_domain": "physics"},   # cross-domain
        {"id": "art-01", "label": "총체예술", "domain": "art", "rel": None,
         "target": None, "target_domain": None},              # isolated node
    ]

    def test_nodes_deduped_with_degree(self):
        g = q.shape_graph(self.ROWS)
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(set(by_id), {"math-01", "math-02", "phys-01", "art-01"})
        self.assertEqual(by_id["math-01"]["degree"], 2)   # two outgoing edges
        self.assertEqual(by_id["art-01"]["degree"], 0)    # isolated

    def test_cross_domain_edge_flagged(self):
        g = q.shape_graph(self.ROWS)
        bridge = [e for e in g["edges"] if e["target"] == "phys-01"][0]
        self.assertTrue(bridge["cross_domain"])
        intra = [e for e in g["edges"] if e["target"] == "math-02"][0]
        self.assertFalse(intra["cross_domain"])

    def test_graph_entrypoint_without_db(self):
        g = q.graph(owner="user:doheon")
        self.assertFalse(g["executed"])
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])

    def test_recall_recency_is_default(self):
        plan = q.recall_plan(owner="u1")
        self.assertIn("ORDER BY e.date DESC", plan.cypher)
        self.assertNotIn("pref_score", plan.cypher)

    def test_recall_preference_plan_fetches_signal_columns_by_date(self):
        # 점수는 백엔드 밖(score_preference)에서 매긴다 — 플랜은 신호 열을 date DESC 로
        # 전량 가져올 뿐(Cypher 내 점수식·방언 없음, §4-2).
        plan = q.recall_plan(owner="u1", rank="preference")
        self.assertIn("access_count", plan.cypher)   # 점수 신호 투영
        self.assertIn("coalesce(e.pinned, false) AS pinned", plan.cypher)
        self.assertIn("ORDER BY e.date DESC", plan.cypher)
        self.assertNotIn("toFloat", plan.cypher)      # 방언 함수 제거
        self.assertNotIn("pref_score", plan.cypher)

    def test_score_preference_pin_and_frequency(self):
        rows = [
            {"id": "a", "pinned": False, "access_count": 0, "tags": []},
            {"id": "b", "pinned": True, "access_count": 0, "tags": []},   # pin boost
            {"id": "c", "pinned": False, "access_count": 100, "tags": []},  # freq
        ]
        ranked = q.score_preference(rows, limit=10)
        self.assertEqual(ranked[0]["id"], "b")        # w_pin(3) > w_freq 포화항
        self.assertEqual({r["id"] for r in ranked}, {"a", "b", "c"})
        self.assertTrue(all("score" in r for r in ranked))

    def test_score_preference_tag_overlap_only_when_tags(self):
        rows = [
            {"id": "a", "pinned": False, "access_count": 0, "tags": ["billing"]},
            {"id": "b", "pinned": False, "access_count": 0, "tags": ["x"]},
        ]
        ranked = q.score_preference(rows, tags=["billing", "kr"], limit=10)
        self.assertEqual(ranked[0]["id"], "a")        # 태그 겹침 부스트
        # 태그 미지정 시 겹침항 0 — 순서는 recency(입력 순서) 유지
        ranked0 = q.score_preference(rows, tags=None, limit=10)
        self.assertEqual([r["id"] for r in ranked0], ["a", "b"])

    def test_score_preference_custom_weights_and_limit(self):
        rows = [
            {"id": "a", "pinned": True, "access_count": 0, "tags": []},
            {"id": "b", "pinned": False, "access_count": 100, "tags": []},
        ]
        ranked = q.score_preference(rows, weights={"w_pin": 0.0}, limit=1)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["id"], "b")        # w_pin=0 → 빈도 우세

    def test_rrf_fuse_combines_arms(self):
        # RRF: 두 암(vector·BM25) 순위를 1/(k+rank) 합산 융합(§4-2, 순수 함수).
        vec = [{"id": "a"}, {"id": "b"}, {"id": "c"}]     # a 최상위
        fts = [{"id": "c"}, {"id": "a"}, {"id": "d"}]     # c 최상위
        fused = q.rrf_fuse([vec, fts], top_k=3)
        ids = [r["id"] for r in fused]
        # a: 1/61+1/62, c: 1/63+1/61 — a·c 가 양쪽 상위라 선두, 단일 등장 b/d 후순위
        self.assertEqual(set(ids[:2]), {"a", "c"})
        self.assertTrue(all("rrf_score" in r for r in fused))

    def test_rrf_single_arm_passthrough(self):
        fused = q.rrf_fuse([[{"id": "x"}, {"id": "y"}]], top_k=1)
        self.assertEqual(fused[0]["id"], "x")

    def test_decide_plan_depth_clamped(self):
        plan = q.decide_plan("g5.b.x", depth=99)
        self.assertIn("*1..4", plan.cypher)  # clamped to 4

    def test_decide_plan_min_depth(self):
        plan = q.decide_plan("t", depth=0)
        self.assertIn("*1..1", plan.cypher)


class ExecuteTests(unittest.TestCase):
    def test_execute_without_db_returns_plan(self):
        # no NEO4J_PASSWORD / no driver -> executed False, plan present
        res = q.recall("x")
        self.assertFalse(res["executed"])
        self.assertEqual(res["plan"]["kind"], "recall")
        self.assertEqual(res["rows"], [])

    def test_search_entrypoint(self):
        # search 는 store.fulltext_search + (선택)vector_search + RRF(§4-2). 인덱스
        # 미구축 시 executed=False 로 강등(무 크래시). plan dict 아님(kind=search).
        res = q.search("query")
        self.assertEqual(res["kind"], "search")
        self.assertIn("executed", res)
        self.assertEqual(res["rows"], [])

    def test_decide_entrypoint(self):
        res = q.decide("topic")
        self.assertEqual(res["plan"]["kind"], "decide")


if __name__ == "__main__":
    unittest.main()
