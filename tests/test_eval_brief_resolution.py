# -*- coding: utf-8 -*-
"""N1 채점기 — 세 가지가 섞이면 안 된다.

이 리포의 평가 하네스는 **조용한 0점**으로 두 번 물렸다(코퍼스에 정답 문서가 없어서
질의가 필연 miss 였는데 점수만 낮게 나왔다). 그래서 여기서 못 박는 것은 정확도 계산이
아니라 **무엇을 무엇으로 세지 않는가** 다:

1. 답이 없는 질문에 `resolved` 를 주는 것은 **오답이 아니라 정직성 고장**이다. 회수율
   분모에 섞으면 그냥 낮은 점수로 보인다.
2. 확정했는데 틀린 것과 모른다고 한 것은 **다른 고장**이다. 앞은 읽는 쪽이 믿고,
   뒤는 안 믿는다.
3. 정답 노드가 스토어에 없으면 채점을 **거부**한다. 그 행은 필연 오답이고, 세면
   "해소가 나쁘다"가 된다.

근거: [[record:no-node-level-gold-set-exists-2026-09-09]]
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_brief_resolution import check_gold, grade_one, rank_of, summarize  # noqa: E402


def _res(status, node=None, candidates=()):
    return {"status": status,
            "node": {"id": node} if node else {},
            "candidates": [{"id": c} for c in candidates],
            "grounds": []}


class Grading(unittest.TestCase):
    def test_a_confident_wrong_answer_is_not_the_same_as_not_knowing(self):
        row = {"gold_node_id": "concept:a", "answerable": True, "gold_kind": "concept"}
        wrong = grade_one(_res("resolved", "concept:b"), row)
        dunno = grade_one(_res("choose", None, ["concept:b"]), row)
        self.assertEqual(wrong["wrong_node"], 1.0)
        self.assertEqual(dunno["wrong_node"], 0.0, "모른다고 한 것을 오답으로 셌다")
        self.assertEqual(dunno["chose"], 1.0)
        self.assertEqual(wrong["hit"][1], 0.0)

    def test_resolving_an_unanswerable_question_is_counted_as_honesty_not_recall(self):
        row = {"gold_node_id": "", "answerable": False, "gold_kind": "none"}
        bad = grade_one(_res("resolved", "concept:anything"), row)
        ok = grade_one(_res("none"), row)
        self.assertEqual(bad["false_resolved"], 1.0)
        self.assertEqual(bad["wrong_node"], 0.0, "정직성 고장이 오답 칸에 섞였다")
        self.assertEqual(ok["false_resolved"], 0.0)
        # 답 불가 행은 회수율 분자에 절대 안 들어간다.
        self.assertEqual(bad["hit"][1], 0.0)
        self.assertEqual(ok["hit"][10], 0.0)

    def test_rank_reads_the_resolved_node_then_the_candidate_order(self):
        self.assertEqual(rank_of("concept:a", _res("resolved", "concept:a")), 1)
        self.assertEqual(rank_of("concept:a", _res("choose", None,
                                                   ["concept:x", "concept:a"])), 2)
        self.assertEqual(rank_of("concept:a", _res("none")), 0)

    def test_the_summary_splits_the_two_denominators(self):
        rows = [
            grade_one(_res("resolved", "concept:a"),
                      {"gold_node_id": "concept:a", "answerable": True,
                       "gold_kind": "concept", "language_axis": "en"}),
            grade_one(_res("resolved", "concept:z"),
                      {"gold_node_id": "concept:a", "answerable": True,
                       "gold_kind": "concept", "language_axis": "ko"}),
            grade_one(_res("resolved", "concept:q"),
                      {"gold_node_id": "", "answerable": False,
                       "gold_kind": "none", "language_axis": "en"}),
        ]
        s = summarize(rows)
        self.assertEqual(s["answerable"], 2)
        self.assertEqual(s["unanswerable"], 1)
        # 회수율은 답 가능 2건이 분모다 — 3건이 아니다.
        self.assertEqual(s["node_recall"]["R@1"], 0.5)
        self.assertEqual(s["wrong_node_rate"], 0.5)
        # 정직성은 답 불가 1건이 분모다.
        self.assertEqual(s["false_resolved_rate"], 1.0)
        self.assertEqual(s["by_axis"]["en"]["n"], 1)
        self.assertEqual(s["by_kind"]["concept"]["n"], 2)


class GoldFileRules(unittest.TestCase):
    def test_an_answerable_row_without_a_gold_node_is_rejected(self):
        bad = check_gold([{"query": "q", "answerable": True}])
        self.assertTrue(any("gold_node_id" in b for b in bad))

    def test_an_unanswerable_row_carrying_a_gold_node_is_rejected(self):
        """둘 중 하나가 틀린 것이다 — 조용히 한쪽을 믿으면 그 행이 무엇을 재는지 모른다."""
        bad = check_gold([{"query": "q", "answerable": False,
                           "gold_node_id": "concept:a"}])
        self.assertTrue(any("answerable=false" in b for b in bad))

    def test_a_clean_file_passes(self):
        self.assertEqual(check_gold([
            {"query": "q", "answerable": True, "gold_node_id": "concept:a",
             "language_axis": "ko"},
            {"query": "q2", "answerable": False, "gold_node_id": "",
             "language_axis": "en"}]), [])


if __name__ == "__main__":
    unittest.main()
