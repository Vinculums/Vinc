# -*- coding: utf-8 -*-
"""VIN-895 — 들어오는 엣지가 어떤 종류든 연결로 센다(거짓 고아 금지).

component_diagnosis 의 owner 스코프는 개념과 결정만 담는다(_owner_concept_ids). 그래서
레코드(ATTACHES_TO)나 사건(ABOUT)만 붙은 개념은 성분에 저 혼자인 것처럼 보여 고아로
나왔다 — 고아를 지우는 사람이 쓰이는 노드를 지운다. WCC 는 그 레코드·사건을 같은 성분에
넣으므로, 성분의 **실제 노드 수**(종류 불문)가 1일 때만 고아다.
"""
from __future__ import annotations

import unittest

from api import fragment, query as q
from test_decision_type import _fresh_store, _restore

OWNER = "user:diag-orphans"


class DiagnoseOrphansCountAnyKind(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()
        self.addCleanup(_restore, self.keep)
        self.addCleanup(self.store.close)
        res = fragment.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:by-record", "type": "concept", "title": "By record",
                 "domain": "vinc/x"},
                {"id": "concept:by-episode", "type": "concept", "title": "By episode",
                 "domain": "vinc/x"},
                {"id": "concept:alone", "type": "concept", "title": "Alone",
                 "domain": "vinc/x"},
                {"id": "record:r", "type": "record", "title": "Field record",
                 "domain": "vinc/x"},
                {"id": "episode:e", "type": "episode", "title": "A run",
                 "domain": "vinc/x"},
            ],
            "edges": [
                # 레코드가 개념에 붙는다(ATTACHES_TO) — 들어오는 엣지
                {"from": "record:r", "to": "concept:by-record", "type": "relates_to"},
                # 사건이 개념에 붙는다(ABOUT) — 들어오는 엣지
                {"from": "episode:e", "to": "concept:by-episode", "type": "about"},
            ],
        }, store=self.store)
        self.assertFalse(res.get("partial"), res)

    def _diag(self):
        d = q.component_diagnosis(owner=OWNER, store=self.store)
        if not d["executed"]:
            note = str(d.get("note") or "")
            self.assertNotIn("Binder", note, f"투영이 깨졌다: {note}")
            self.assertNotIn("not projected", note, f"투영 누락: {note}")
            self.skipTest(f"algo 확장 미가용: {note}")
        return d

    def test_a_concept_linked_only_by_a_record_is_not_an_orphan(self):
        d = self._diag()
        self.assertNotIn("concept:by-record", d["orphans"], d)

    def test_a_concept_linked_only_by_an_episode_is_not_an_orphan(self):
        d = self._diag()
        self.assertNotIn("concept:by-episode", d["orphans"], d)

    def test_a_truly_unlinked_concept_is_still_an_orphan(self):
        d = self._diag()
        self.assertIn("concept:alone", d["orphans"], d)


if __name__ == "__main__":
    unittest.main()
