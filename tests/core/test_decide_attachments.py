# -*- coding: utf-8 -*-
"""VIN-897 — decide 는 개념 시드에 붙은 레코드·결정·사건을 nodes·edges 로 낸다.

decide 는 그래프 행 조회(graph_rows_for_ids_plan)만 돌렸는데 그건 Concept 전용이라
개념↔개념 엣지만 돌려준다. 그래서 개념에 ATTACHES_TO 로 붙은 레코드와 ABOUT 으로
걸린 결정이 통째로 빠졌다 — "무엇이 이 주제에 걸려 있나"가 빈 답이 됐고, 결정은
conflicts 로만 비쳤다. 수리는 graph() 의 접착제 후조회(축 플랜 ids 모드)를 개념
시드에도 돌리는 것이다. 결정 시드는 종전 경로가 이미 ABOUT·의미층을 싣는다.
"""
from __future__ import annotations

import unittest

from api import fragment, query as q
from test_decision_type import _fresh_store, _restore

OWNER = "user:decide-attach"


class DecideSurfacesAttachments(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()
        self.addCleanup(_restore, self.keep)
        self.addCleanup(self.store.close)
        res = fragment.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:soil", "type": "concept", "title": "Soil moisture sensing",
                 "domain": "vinc/engineering"},
                {"id": "concept:peer", "type": "concept", "title": "Irrigation control",
                 "domain": "vinc/engineering"},
                {"id": "record:field-log", "type": "record", "title": "Field log",
                 "domain": "vinc/engineering"},
                {"id": "decision:d1", "type": "decision", "title": "Use capacitive probes",
                 "domain": "vinc/engineering"},
                {"id": "episode:e1", "type": "episode", "title": "Install run",
                 "domain": "vinc/engineering"},
            ],
            "edges": [
                # 개념끼리 — graph_rows 가 내던 엣지(수리가 이것을 지우지 않는지 확인)
                {"from": "concept:soil", "to": "concept:peer", "type": "relates_to"},
                # 레코드가 개념에 붙는다(ATTACHES_TO) — 수리 전엔 빠지던 엣지
                {"from": "record:field-log", "to": "concept:soil", "type": "relates_to",
                 "evidence": "Measured at 10cm", "confidence": 0.8},
                # 결정이 개념을 두고 내려졌다(ABOUT) — 수리 전엔 conflicts 로만 비쳤다
                {"from": "decision:d1", "to": "concept:soil", "type": "about",
                 "evidence": "capacitive beats resistive here", "confidence": 0.9},
                # 사건도 개념에 붙는다(ABOUT)
                {"from": "episode:e1", "to": "concept:soil", "type": "about"},
            ],
        }, store=self.store)
        self.assertFalse(res.get("partial"), res)
        self.assertEqual(res["merged"]["edges"]["created"], 4, res)

    def test_concept_seed_includes_attached_records_decisions_and_episodes(self):
        res = q.decide("concept:soil", owner=OWNER, store=self.store)
        self.assertTrue(res["executed"] and res["found"], res)
        kinds = {n["id"]: n["kind"] for n in res["nodes"]}
        # 붙은 세 매개자가 전부 nodes 에 선다
        self.assertEqual(kinds.get("record:field-log"), "record", res["nodes"])
        self.assertEqual(kinds.get("decision:d1"), "decision", res["nodes"])
        self.assertEqual(kinds.get("episode:e1"), "episode", res["nodes"])
        edgeset = {(e["source"], e["type"], e["target"]) for e in res["edges"]}
        self.assertIn(("record:field-log", "ATTACHES_TO", "concept:soil"), edgeset)
        self.assertIn(("decision:d1", "ABOUT", "concept:soil"), edgeset)
        self.assertIn(("episode:e1", "ABOUT", "concept:soil"), edgeset)
        # 개념↔개념 엣지는 그대로 남는다(graph_rows 경로 비회귀)
        self.assertIn(("concept:soil", "RELATED_TO", "concept:peer"), edgeset)
        # 같은 물리 엣지를 두 번 세지 않는다(축/행 플랜 분담 — degree 이중 계상 금지)
        self.assertEqual(len(res["edges"]), len(edgeset), res["edges"])

    def test_record_evidence_rides_the_attachment_edge(self):
        res = q.decide("concept:soil", owner=OWNER, store=self.store)
        attach = next(e for e in res["edges"]
                      if e["source"] == "record:field-log" and e["target"] == "concept:soil")
        self.assertEqual(attach.get("evidence"), "Measured at 10cm")
        self.assertEqual(attach.get("confidence"), 0.8)


if __name__ == "__main__":
    unittest.main()
