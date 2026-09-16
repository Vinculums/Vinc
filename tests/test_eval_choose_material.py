# -*- coding: utf-8 -*-
"""Option C 측정 분류 — 스토어·모델 없이 짝 비교만 잰다.

근거: [[decision:option-c-assembly-is-built-behind-a-flag-left-off-2026-09-13]]
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import eval_choose_material as oc  # noqa: E402


def _choose(first, second=None):
    cands = [{"id": first, "title": first}]
    if second:
        cands.append({"id": second, "title": second})
    return {"status": "choose", "candidates": cands, "grounds": []}


def _resolved(nid):
    return {"status": "resolved", "node": {"id": nid}, "grounds": [nid]}


class ClassifyTests(unittest.TestCase):
    def test_정답_1위_choose_에서_landed_가_그_노드면_맞다(self):
        off = _choose("concept:gold", "concept:other")
        on = {**off, "candidate_material": {
            "candidate": "concept:gold", "landed": True,
            "node": {"id": "concept:gold"}}}
        row = oc.classify(off, on, "concept:gold", True)
        self.assertTrue(row["gold_first"] and row["landed_is_gold"])
        self.assertFalse(row["would_attach_wrong"])
        self.assertTrue(row["identical_except_material"])

    def test_오답_1위_choose_에서_landed_는_틀린_재료다(self):
        off = _choose("concept:wrong", "concept:gold")
        on = {**off, "candidate_material": {
            "candidate": "concept:wrong", "landed": True,
            "node": {"id": "concept:wrong"}}}
        row = oc.classify(off, on, "concept:gold", True)
        self.assertTrue(row["would_attach_wrong"])
        self.assertFalse(row["gold_first"])

    def test_확정_답은_키가_안_붙어야_바이트가_같다(self):
        off = _resolved("concept:gold")
        row = oc.classify(off, dict(off), "concept:gold", True)
        self.assertEqual(row["status"], "resolved")
        self.assertFalse(row["has_material"])
        self.assertTrue(row["identical_except_material"])

    def test_요약은_wrong_first_를_따로_센다(self):
        rows = [
            oc.classify(_choose("concept:gold"), {
                **_choose("concept:gold"),
                "candidate_material": {"landed": True, "node": {"id": "concept:gold"}},
            }, "concept:gold", True),
            oc.classify(_choose("concept:wrong"), {
                **_choose("concept:wrong"),
                "candidate_material": {"landed": True, "node": {"id": "concept:wrong"}},
            }, "concept:gold", True),
            oc.classify(_resolved("concept:gold"), _resolved("concept:gold"),
                        "concept:gold", True),
        ]
        s = oc.summarize(rows)
        self.assertEqual(s["choose"]["gold_first"]["landed_is_gold"], 1)
        self.assertEqual(s["choose"]["wrong_first"]["would_attach_wrong"], 1)
        self.assertTrue(s["non_choose_identical"])
