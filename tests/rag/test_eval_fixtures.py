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
3. 역방향 셋(evalset_xling_en2ko.json, W16)은 정본 ko 질의의 영어 번역이라 같은
   검증을 방향만 뒤집어 받는다: 정답은 정본 ko 정답의 부분집합(전부 한국어 문서),
   질의는 실제로 영어(한글이 남았으면 ko 동일언어 축과 섞인다 — 1의 오염이
   반대 방향으로 재발하는 형태), dedup·counts 동일.
4. 사람이 친 셋(evalset_human.json, 실험 A)은 앞의 셋과 **출처가 다르다** — 생성기가
   아니라 손으로 썼다. 그래서 기계가 지켜 주던 것(언어 정합·중복 없음)을 여기서 다시
   확인하고, 이 셋의 존재 이유인 "소제목이 아니다"를 기계로 못박는다: 질의 문자열이
   생성 픽스처 어디에도 나타나면 안 된다. 그게 깨지면 이 셋은 정본 셋의 복사본이고
   편향을 가르는 대조군 노릇을 못 한다.
"""
from __future__ import annotations

import json
import pathlib
import unittest

_FIX = pathlib.Path(__file__).resolve().parents[2] / "rag_eval" / "fixtures"


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


class En2koSetTests(unittest.TestCase):
    """W16 역방향 셋 — XlingSetTests 의 거울상이다(en↔ko 만 바뀐다)."""

    def setUp(self):
        self.x = json.loads(
            (_FIX / "evalset_xling_en2ko.json").read_text(encoding="utf-8"))
        self.canon = json.loads((_FIX / "evalset.json").read_text(encoding="utf-8"))

    def test_정답은_정본_ko_정답의_부분집합이다(self):
        """역방향 축의 정의: 영어 질의의 정답은 전부 **한국어** 문서여야 한다.
        부분집합 검사가 doc_id 의 eval/ko/ 세그먼트까지 함께 보장한다."""
        ko_docs = {q["doc_id"] for q in self.canon["queries"] if q["lang"] == "ko"}
        for q in self.x["queries"]:
            self.assertIn(q["doc_id"], ko_docs)

    def test_원문은_정본_ko_질의에_실재한다(self):
        ko_qs = {q["query"] for q in self.canon["queries"] if q["lang"] == "ko"}
        for q in self.x["queries"]:
            self.assertIn(q["ko_query"], ko_qs)

    def test_질의는_전부_영어다(self):
        """번역이 빠지고 ko 원문이 남으면 invalid-first-eval 의 오염이 반대
        방향으로 재발한다("영어" 질의에 한글이 그대로) — 한글 비율 전수 확인."""
        for q in self.x["queries"]:
            self.assertLess(_hangul_ratio(q["query"]), 0.05, q["query"])
            self.assertEqual(q["lang"], "en2ko")

    def test_중복_번역문이_없다(self):
        texts = [q["query"].casefold() for q in self.x["queries"]]
        self.assertEqual(len(texts), len(set(texts)))

    def test_counts_가_실제와_같다(self):
        c = self.x["counts"]
        self.assertEqual(c["selected"], len(self.x["queries"]))
        self.assertEqual(c["source_ko"] - c["dropped_ambiguous"], c["selected"])


class HumanSetTests(unittest.TestCase):
    """손으로 쓴 셋 — 생성기가 지켜 주던 불변식을 사람이 깼는지 본다."""

    def setUp(self):
        self.h = json.loads((_FIX / "evalset_human.json").read_text(encoding="utf-8"))
        self.gen = set()
        for name in ("evalset.json", "evalset_xling.json", "evalset_xling_en2ko.json"):
            for q in json.loads((_FIX / name).read_text(encoding="utf-8"))["queries"]:
                self.gen.add(q["query"].casefold())
                if "en_query" in q:
                    self.gen.add(q["en_query"].casefold())

    def test_교차언어_쌍이_없다(self):
        for q in self.h["queries"]:
            self.assertEqual(q["lang"], q["doc_id"].split("/")[1], q["query"])

    def test_라벨한_언어가_실제_언어다(self):
        """정본 셋을 무효로 만든 그 오염(한국어 라벨에 한글 0자)의 재발 방지."""
        for q in self.h["queries"]:
            r = _hangul_ratio(q["query"])
            if q["lang"] == "ko":
                self.assertGreater(r, 0.15, q["query"])
            else:
                self.assertLess(r, 0.05, q["query"])

    def test_생성_픽스처와_겹치는_질의가_없다(self):
        """이 셋의 존재 이유. 하나라도 겹치면 소제목 편향을 가르는 대조군이 아니다."""
        for q in self.h["queries"]:
            self.assertNotIn(q["query"].casefold(), self.gen, q["query"])

    def test_중복_질의가_없다(self):
        texts = [q["query"].casefold() for q in self.h["queries"]]
        self.assertEqual(len(texts), len(set(texts)))

    def test_문서당_두_개를_넘지_않는다(self):
        per = {}
        for q in self.h["queries"]:
            per[q["doc_id"]] = per.get(q["doc_id"], 0) + 1
        self.assertLessEqual(max(per.values()), 2, per)

    def test_counts_가_실제와_같다(self):
        c = self.h["counts"]
        self.assertEqual(c["selected"], len(self.h["queries"]))
        self.assertGreaterEqual(c["selected"], 20)
        for lang, n in c["by_lang"].items():
            self.assertEqual(n, sum(q["lang"] == lang for q in self.h["queries"]))


class HumanXlingTests(unittest.TestCase):
    """사람이 쓴 교차언어 셋(ko2en). 정본 xling 과 같은 검사를 받되 원문이 소제목이 아니라
    사람이 쓴 자연어라는 것만 다르다. 이 셋이 리랭커 판정의 빈칸이었다: 리랭커가 잘하는
    축은 교차언어인데 그 축이 소제목 질의로만 검증돼 있었다."""

    def setUp(self):
        self.x = json.loads((_FIX / "evalset_human_xling.json").read_text(encoding="utf-8"))
        self.h = json.loads((_FIX / "evalset_human.json").read_text(encoding="utf-8"))

    def test_정답은_사람_셋_en_정답의_부분집합이다(self):
        gold = {q["doc_id"] for q in self.h["queries"] if q["lang"] == "en"}
        for q in self.x["queries"]:
            self.assertIn(q["doc_id"], gold, q["query"])

    def test_원문은_사람_셋_en_질의에_실재한다(self):
        src = {q["query"] for q in self.h["queries"] if q["lang"] == "en"}
        for q in self.x["queries"]:
            self.assertIn(q["en_query"], src, q["query"])

    def test_질의는_전부_한국어다(self):
        """라벨한 언어와 실제 언어가 어긋나면 이 축이 동일언어 축과 섞인다 —
        정본 셋을 한 번 무효로 만든 그 오염이다."""
        for q in self.x["queries"]:
            self.assertGreater(_hangul_ratio(q["query"]), 0.15, q["query"])

    def test_정답_문서는_전부_영어다(self):
        """교차언어의 뜻: 질의는 한국어, 코퍼스는 영어. 정답이 한국어 문서면 축이 아니다."""
        for q in self.x["queries"]:
            self.assertEqual(q["doc_id"].split("/")[1], "en", q["query"])
            self.assertEqual(q["lang"], "ko2en")

    def test_중복_번역문이_없다(self):
        texts = [q["query"].casefold() for q in self.x["queries"]]
        self.assertEqual(len(texts), len(set(texts)))

    def test_기존_픽스처와_겹치는_질의가_없다(self):
        gen = set()
        for name in ("evalset.json", "evalset_xling.json", "evalset_xling_en2ko.json",
                     "evalset_human.json"):
            for q in json.loads((_FIX / name).read_text(encoding="utf-8"))["queries"]:
                gen.add(q["query"].casefold())
        for q in self.x["queries"]:
            self.assertNotIn(q["query"].casefold(), gen, q["query"])

    def test_counts_가_실제와_같다(self):
        c = self.x["counts"]
        self.assertEqual(c["selected"], len(self.x["queries"]))
        self.assertEqual(c["source_en"] - c["dropped_ambiguous"], c["selected"])


if __name__ == "__main__":
    unittest.main()
