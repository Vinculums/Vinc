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

    def test_답할_수_없는_질문의_choose_에_붙은_재료를_센다(self):
        landed = {**_choose("concept:any"),
                  "candidate_material": {"landed": True, "node": {"id": "concept:any"}}}
        missed = {**_choose("concept:any"), "candidate_material": {"landed": False}}
        rows = [
            oc.classify(_choose("concept:any"), landed, "", False),
            oc.classify(_choose("concept:any"), missed, "", False),
            oc.classify(_resolved("concept:x"), _resolved("concept:x"), "", False),
            oc.classify(_choose("concept:gold"), {
                **_choose("concept:gold"),
                "candidate_material": {"landed": True, "node": {"id": "concept:gold"}},
            }, "concept:gold", True),
        ]
        s = oc.summarize(rows)
        self.assertEqual(s["unanswerable"], {"n": 3, "landed": 1})
        # 답할 수 없는 행은 wrong_first 에 안 들어간다. 따로 세는 이유다.
        self.assertEqual(s["choose"]["wrong_first"]["n"], 0)

    def test_행과_요약에_arms_가_실린다(self):
        a = {**_choose("concept:a"), "arms": "title+semantic"}
        b = {**_choose("concept:b"), "arms": "title"}
        rows = [oc.classify(a, dict(a), "concept:a", True),
                oc.classify(b, dict(b), "concept:a", True),
                oc.classify(_resolved("concept:a"), _resolved("concept:a"),
                            "concept:a", True)]
        self.assertEqual([r["arms"] for r in rows], ["title+semantic", "title", None])
        self.assertEqual(oc.summarize(rows)["by_arms"],
                         {"title+semantic": 1, "title": 1, "": 1})
