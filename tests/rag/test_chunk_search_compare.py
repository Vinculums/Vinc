# -*- coding: utf-8 -*-
"""청크 검색 러너의 짝 비교 — 같은 질의끼리만 대고, 질의 목록이 다르면 비교를 거부한다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_eval.chunk_search import compare, paired  # noqa: E402


def _res(scores):
    return {"per_query": {"evalset.json": [{"query": f"q{i}", "ndcg": s} for i, s in enumerate(scores)]}}


class PairedTests(unittest.TestCase):
    def test_같은_점수면_차이가_0이다(self):
        p = paired([0.5, 0.2, 1.0], [0.5, 0.2, 1.0])
        self.assertEqual((p["delta"], p["lo"], p["hi"]), (0.0, 0.0, 0.0))

    def test_모두_오르면_구간이_0을_벗어난다(self):
        p = paired([0.1] * 40, [0.6] * 40)
        self.assertGreater(p["lo"], 0)

    def test_길이가_다르면_거부한다(self):
        with self.assertRaises(ValueError):
            paired([0.1], [0.1, 0.2])


class CompareTests(unittest.TestCase):
    def test_같은_질의끼리_짝짓는다(self):
        out = compare(_res([0.0, 0.5]), _res([1.0, 0.5]))
        self.assertEqual(out["evalset.json"]["delta"], 0.5)

    def test_질의_목록이_다르면_비교하지_않는다(self):
        base = _res([0.0, 0.5])
        base["per_query"]["evalset.json"][0]["query"] = "다른 질의"
        self.assertIn("error", compare(base, _res([1.0, 0.5]))["evalset.json"])


if __name__ == "__main__":
    unittest.main()
