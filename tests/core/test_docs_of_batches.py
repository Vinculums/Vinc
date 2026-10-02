# -*- coding: utf-8 -*-
"""`_docs_of` 는 한 번만 묻고, 그래도 순서와 중복 제거는 그대로다.

왜 이 파일이 있나: 이 함수는 조건마다 불리고 조건은 스윕에서 수십 개가 된다.
행마다 스토어를 때리던 판은 융합 스윕 하나에 94,400 왕복(조건 80 × 질의 118 × 행 10)
을 냈고 51분을 썼다. 묶어서 묻는 것 자체는 쉬운데, **순위가 질의 결과 순서로 바뀌는**
회귀가 조용하다 — 점수는 계속 나오고 그냥 조금 달라질 뿐이다. 그래서 못 박는다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rag_eval.search import _docs_of  # noqa: E402


class _Store:
    """청크 id → 문서 id. 호출 수를 세고, 반환 순서를 **일부러 뒤집어** 준다 —
    호출부가 스토어 순서를 믿으면 여기서 깨진다."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls = 0

    def run(self, query, params=None):
        self.calls += 1
        ids = (params or {}).get("ids") or []
        rows = [{"i": i, "d": self.mapping[i]} for i in ids if i in self.mapping]
        return list(reversed(rows))


class DocsOf(unittest.TestCase):
    def test_one_query_and_rank_order_survives(self):
        st = _Store({"c1": "docA", "c2": "docB", "c3": "docA", "c4": "docC"})
        rows = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}, {"id": "c4"}]
        self.assertEqual(_docs_of(st, rows), ["docA", "docB", "docC"])
        self.assertEqual(st.calls, 1, "행마다 묻고 있다")

    def test_unknown_and_empty_ids_are_skipped_not_fatal(self):
        st = _Store({"c1": "docA"})
        rows = [{"id": None}, {"nope": 1}, {"id": "ghost"}, {"id": "c1"}]
        self.assertEqual(_docs_of(st, rows), ["docA"])

    def test_no_ids_asks_nothing(self):
        st = _Store({"c1": "docA"})
        self.assertEqual(_docs_of(st, [{"id": None}]), [])
        self.assertEqual(st.calls, 0)


if __name__ == "__main__":
    unittest.main()
