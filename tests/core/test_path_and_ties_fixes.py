# -*- coding: utf-8 -*-
"""VIN-893 · VIN-892 — 두 읽기 도구의 개념 층 계약.

VIN-893: vinc_path(홉)는 엔진의 모든 rel 테이블을 걸어, 두 개념이 같은 청크에만 함께
언급돼도 Chunk 를 거친 경로를 내고 _node_seq 가 그 청크를 떨궈 length:1(직접 엣지처럼)
로 보고했다. 전원 개념인 경로만 남겨 그 거짓 인접을 없앤다.

VIN-892: vinc_suggest_ties 가 Decision id 에 "unknown concept" 라 답하고 같은 id 를
후보로 되돌려줬다. 비-개념은 개념 전용이라고 분명히 거절하고, 후보에서 자기 자신을 뺀다.
"""
from __future__ import annotations

import unittest

from api import fragment, query as q
from store import pkns
from test_decision_type import _fresh_store, _restore

OWNER = "user:path-ties"


class PathAndTiesConceptLayer(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()
        self.addCleanup(_restore, self.keep)
        self.addCleanup(self.store.close)
        res = fragment.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:c1", "type": "concept", "title": "C1", "domain": "vinc/x"},
                {"id": "concept:c2", "type": "concept", "title": "C2", "domain": "vinc/x"},
                {"id": "concept:c3", "type": "concept", "title": "C3", "domain": "vinc/x"},
                {"id": "decision:d", "type": "decision", "title": "A decision", "domain": "vinc/x"},
            ],
            # c1↔c2 는 저작 엣지, c3 는 개념 엣지가 없다(아래 청크로만 c1 과 엮인다).
            # decision:d 는 c1 에 about 으로 붙인다(붙지 않은 결정은 partial 경고를 낸다).
            "edges": [{"from": "concept:c1", "to": "concept:c2", "type": "relates_to"},
                      {"from": "decision:d", "to": "concept:c1", "type": "about"}],
        }, store=self.store)
        self.assertFalse(res.get("partial"), res)
        # 한 청크가 c1·c3 를 함께 언급한다 — 저작 관계가 아니라 공통 언급일 뿐이다.
        self.store.run(
            "MERGE (ch:Chunk {chunk_id:$chunk}) "
            "SET ch.owner_id=$owner, ch.doc_id=$doc, ch.text=$text, ch.chunk_index=0",
            {"chunk": pkns.key(OWNER, "chunk:prd"), "owner": OWNER,
             "doc": "prd", "text": "FR-01 and FR-05 in one PRD chunk"})
        for cid in ("concept:c1", "concept:c3"):
            self.store.run(
                "MATCH (ch:Chunk {chunk_id:$chunk}), (n:Concept {concept_id:$id}) "
                "MERGE (ch)-[:MENTIONS {method:'title'}]->(n)",
                {"chunk": pkns.key(OWNER, "chunk:prd"), "id": pkns.key(OWNER, cid)})

    # ── VIN-893 ──────────────────────────────────────────────────────────
    def test_authored_concept_edge_is_a_real_length_one_path(self):
        r = q.shortest_path("concept:c1", "concept:c2", owner=OWNER, store=self.store)
        self.assertTrue(r["found"], r)
        self.assertEqual(r["length"], 1)
        self.assertEqual(r["paths"][0]["ids"], ["concept:c1", "concept:c2"])

    def test_a_shared_chunk_is_not_a_path(self):
        # c1 과 c3 는 저작 엣지가 없고 같은 청크에만 함께 언급된다 — length:1 로 나오면
        # 안 된다(그게 VIN-893). 공통 언급은 연결이 아니다.
        r = q.shortest_path("concept:c1", "concept:c3", owner=OWNER, store=self.store)
        self.assertFalse(r["found"], r)
        self.assertEqual(r["paths"], [])
        self.assertIn("no path within max_depth", r.get("error", ""))

    # ── VIN-892 ──────────────────────────────────────────────────────────
    def test_suggest_ties_refuses_a_decision_without_offering_itself(self):
        r = q.suggest_ties(owner=OWNER, node="decision:d", store=self.store)
        self.assertTrue(r["executed"] and not r["found"], r)
        self.assertIn("concept layer only", r.get("error", ""))
        self.assertNotIn("unknown concept", r.get("error", ""))
        # 자기 자신을 후보로 내밀지 않는다(애초에 후보 키가 없다).
        self.assertNotIn("decision:d", str(r.get("candidates", [])))
        self.assertEqual(r["suggestions"], [])

    def test_suggest_ties_on_a_real_concept_still_scores(self):
        # 수리가 정상 개념 경로를 막지 않는지 — c1 은 실재 개념이라 점수를 매긴다.
        r = q.suggest_ties(owner=OWNER, node="concept:c1", store=self.store)
        self.assertTrue(r["executed"], r)
        self.assertNotIn("concept layer only", r.get("error", ""))
        # c2 는 이미 이어져 있어 제안에서 빠지고, 자기 자신도 빠진다.
        self.assertNotIn("concept:c1", [s["id"] for s in r["suggestions"]])
        self.assertNotIn("concept:c2", [s["id"] for s in r["suggestions"]])


if __name__ == "__main__":
    unittest.main()
