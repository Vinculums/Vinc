# -*- coding: utf-8 -*-
"""`vinc_graph` 의 결정 시드와 노드 예산 — 실엔진(임베디드 스토어)으로 잰다.

감사 §5-6: `decision:codex-vinc-local-environment-2026-09-20` 은 depth 1 과 2 에서 같은
2노드를 돌려줬다. 도달 집합 플랜이 시드를 Concept 으로 못박고 있어서 결정 시드가
0행을 냈다. D3: depth 2 이상의 노드 예산은 MCP 핸들러가 정하고, 사이드카는 그 값을
받았을 때만 쓴다 — 안 보내는 호출(View L2·공유 팩·동기화)은 결과가 그대로다.
근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import fragment as frag        # noqa: E402
from api import query as q              # noqa: E402
from test_decision_type import _fresh_store, _restore   # noqa: E402

OWNER = "user:graph-p5"


def _ids(g):
    return sorted(n["id"] for n in g["nodes"])


def _edges(g):
    return sorted((e["source"], e["target"], e["type"]) for e in g["edges"])


class GraphSeedKindsAndBudget(unittest.TestCase):
    def setUp(self):
        self.store, self._keep = _fresh_store()
        # a - b - c 의 사슬, 결정 d 는 a 를 두고(ABOUT) 내려졌고 e 에서 파생됐다.
        # 개념 c 는 d 에서 파생됐다. hub 는 잎 여덟을 거느린다(깊이 2 에서 잎의 잎).
        # ⚠ 허브 무리는 다른 주제에 둔다. 도달 집합 순회는 IN_DOMAIN 도 홉으로 세서
        # 같은 주제의 개념끼리는 depth 2 에서 서로 닿는다(Domain 노드 경유).
        nodes = [{"id": f"concept:{x}", "type": "concept", "title": x.upper(),
                  "domain": "vinc/x"} for x in ("a", "b", "c")]
        nodes.append({"id": "concept:hub", "type": "concept", "title": "HUB",
                      "domain": "vinc/h"})
        nodes += [{"id": "decision:d", "type": "decision", "title": "D", "domain": "vinc/x"},
                  {"id": "decision:e", "type": "decision", "title": "E", "domain": "vinc/x"}]
        edges = [{"from": "decision:d", "to": "concept:a", "type": "about"},
                 {"from": "concept:a", "to": "concept:b", "type": "relates_to"},
                 {"from": "concept:b", "to": "concept:c", "type": "relates_to"},
                 {"from": "decision:d", "to": "decision:e", "type": "derives_from"},
                 {"from": "concept:c", "to": "decision:d", "type": "derives_from"}]
        for i in range(8):
            nodes.append({"id": f"concept:leaf{i}", "type": "concept",
                          "title": f"Leaf {i}", "domain": "vinc/h"})
            nodes.append({"id": f"concept:leaf{i}-x", "type": "concept",
                          "title": f"Leaf {i} x", "domain": "vinc/h"})
            edges.append({"from": "concept:hub", "to": f"concept:leaf{i}",
                          "type": "relates_to"})
            edges.append({"from": f"concept:leaf{i}", "to": f"concept:leaf{i}-x",
                          "type": "relates_to"})
        res = frag.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges},
                                  store=self.store)
        self.assertEqual(res["merged"]["edges"]["created"], len(edges), res)

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _g(self, seed, depth, **kw):
        return q.graph(seed=seed, owner=OWNER, depth=depth, store=self.store, **kw)

    # ── 결정 시드 ────────────────────────────────────────────────────────────
    def test_a_decision_seed_expands_like_a_concept_seed(self):
        g1 = self._g("decision:d", 1)
        # ABOUT(→a), 계보(→e), 개념에서 온 파생(c→) 세 방향이 다 보인다.
        self.assertEqual(_ids(g1), ["concept:a", "concept:c", "decision:d", "decision:e"])
        self.assertEqual(_edges(g1), [("concept:c", "decision:d", "DERIVES_FROM"),
                                      ("decision:d", "concept:a", "ABOUT"),
                                      ("decision:d", "decision:e", "DERIVES_FROM")])
        g2 = self._g("decision:d", 2)
        self.assertIn("concept:b", _ids(g2), "depth 2 가 depth 1 과 같다 — 결정이 안 퍼진다")
        self.assertIn(("concept:a", "concept:b", "RELATED_TO"), _edges(g2))
        self.assertTrue(g2["found"])

    def test_the_shared_plan_is_byte_identical_for_concept_seeds(self):
        """공유 팩·동기화·decide 가 부르는 모양(seed_label 없음)은 종전 문자열 그대로."""
        plan = q.subgraph_ids_plan("concept:a", depth=2, owner=OWNER)
        self.assertEqual(plan.cypher,
                         "MATCH (n:Concept) WHERE n.concept_id = $seed AND n.owner_id = $owner "
                         "MATCH (n)-[* SHORTEST 1..2]-(m:Concept) WHERE m.owner_id = $owner "
                         "RETURN DISTINCT m.concept_id AS id")

    # ── 노드 예산 ────────────────────────────────────────────────────────────
    def test_without_a_budget_the_graph_is_unchanged(self):
        a = self._g("concept:hub", 2)
        b = self._g("concept:hub", 2, max_nodes=None)
        self.assertEqual(a, b)
        self.assertNotIn("depth_budget", a)
        self.assertEqual(len([i for i in _ids(a) if i.startswith("concept:leaf")]), 16)

    def test_a_budget_lowers_the_depth_a_whole_layer_at_a_time(self):
        g = self._g("concept:hub", 2, max_nodes=10)
        self.assertEqual(g["depth"], 1)
        self.assertEqual(g["depth_budget"], {"requested_depth": 2, "served_depth": 1,
                                             "reach_at_requested": 16, "reach_served": 8,
                                             "max_nodes": 10})
        # 층 중간을 자르지 않는다: 첫 층 여덟은 전부, 둘째 층은 하나도 없다.
        leaves = [i for i in _ids(g) if i.startswith("concept:leaf")]
        self.assertEqual(sorted(leaves), sorted(f"concept:leaf{i}" for i in range(8)))
        # 결과는 같은 시드의 depth 1 과 같다(예산 표식만 다르다).
        plain = self._g("concept:hub", 1)
        self.assertEqual(_ids(g), _ids(plain))
        self.assertEqual(_edges(g), _edges(plain))

    def test_a_budget_that_fits_changes_nothing(self):
        g = self._g("concept:hub", 2, max_nodes=16)
        self.assertNotIn("depth_budget", g)
        self.assertEqual(_ids(g), _ids(self._g("concept:hub", 2)))

    def test_depth_one_ignores_the_budget(self):
        g = self._g("concept:hub", 1, max_nodes=1)
        self.assertNotIn("depth_budget", g)
        self.assertEqual(len(_ids(g)), 9)

    def test_a_decision_seed_honours_the_budget_too(self):
        g = self._g("decision:d", 3, max_nodes=2)
        self.assertEqual(g["depth_budget"]["served_depth"], 1)
        self.assertNotIn("concept:b", _ids(g))


if __name__ == "__main__":
    unittest.main()
