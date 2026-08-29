# -*- coding: utf-8 -*-
"""평가셋 픽스처 무결성 — W10 임베딩 평가 스파이크의 전제를 못박는다.

평가가 틀리면 수치가 조용히 모델 채택을 굳힌다(invalid-first-eval 전례: 라벨한
"한국어" 질의에 한글이 0자였다). 그래서 수치를 내기 전에 확인해야 하는 사실들을
테스트로 고정한다:

1. 정본 평가셋(evalset.json)에는 교차언어 쌍이 **없다** — 질의 언어와 정답 문서
   언어가 전부 같다. 즉 그 셋의 어떤 수치도 교차언어 축(§1.3)의 답이 아니다.
2. 교차언어 셋(evalset_xling.json)은 정본 en 질의의 번역이라 정답이 전부 정본
   en 정답의 부분집합이고, 질의는 전부 실제로 한국어이며, 모호쌍 dedup 이 적용돼
   중복 번역문이 없다.
"""
from __future__ import annotations

import json
import pathlib
import unittest

_FIX = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fixtures"


def _hangul_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "가" <= c <= "힣") / len(letters)


class CanonicalSetTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((_FIX / "evalset.json").read_text(encoding="utf-8"))

    def test_교차언어_쌍이_없다(self):
        """doc_id 는 eval/<언어>/... 꼴이다 — 질의 lang 과 그 세그먼트가 전부
        같아야 한다. 하나라도 어긋나면 이 셋의 언어별 수치 해석이 통째로 무너진다."""
        for q in self.data["queries"]:
            self.assertEqual(q["lang"], q["doc_id"].split("/")[1], q["query"])

    def test_counts_가_실제와_같다(self):
        c = self.data["counts"]
        qs = self.data["queries"]
        self.assertEqual(c["selected"], len(qs))
        for lg, n in c["by_lang"].items():
            self.assertEqual(n, sum(1 for q in qs if q["lang"] == lg))


class XlingSetTests(unittest.TestCase):
    def setUp(self):
        self.x = json.loads((_FIX / "evalset_xling.json").read_text(encoding="utf-8"))
        self.canon = json.loads((_FIX / "evalset.json").read_text(encoding="utf-8"))

    def test_정답은_정본_en_정답의_부분집합이다(self):
        en_docs = {q["doc_id"] for q in self.canon["queries"] if q["lang"] == "en"}
        for q in self.x["queries"]:
            self.assertIn(q["doc_id"], en_docs)

    def test_원문은_정본_en_질의에_실재한다(self):
        en_qs = {q["query"] for q in self.canon["queries"] if q["lang"] == "en"}
        for q in self.x["queries"]:
            self.assertIn(q["en_query"], en_qs)

    def test_질의는_전부_한국어다(self):
        """번역이 빠지고 en 원문이 남으면 1차 측정의 오염(한글 0자 "한국어" 질의)이
        재발한다 — 알파벳 문자 중 한글 비율로 전수 확인한다."""
        for q in self.x["queries"]:
            self.assertGreater(_hangul_ratio(q["query"]), 0.15, q["query"])
            self.assertEqual(q["lang"], "ko2en")

    def test_중복_번역문이_없다(self):
        """번역문이 같아지는 쌍은 정답이 모호하다 — dedup(규칙 3 재적용)이 적용된
        상태여야 한다."""
        texts = [q["query"].casefold() for q in self.x["queries"]]
        self.assertEqual(len(texts), len(set(texts)))

    def test_counts_가_실제와_같다(self):
        c = self.x["counts"]
        self.assertEqual(c["selected"], len(self.x["queries"]))
        self.assertEqual(c["source_en"] - c["dropped_ambiguous"], c["selected"])


if __name__ == "__main__":
    unittest.main()
