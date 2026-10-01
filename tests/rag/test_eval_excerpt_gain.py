# -*- coding: utf-8 -*-
"""Source excerpt gain 도구의 **지표가 맞는가** — scripts/eval_excerpt_gain.py.

test_eval_search.py 와 같은 규율: 이 숫자로 "one_liner 만으로 충분한가, 발췌를
붙일 가치가 있나"를 결정하므로 손으로 계산한 값과 대조해 못박는다. 스토어 없이
도는 순수 함수(token_f1, gold_section)만 검사한다."""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eval_excerpt_gain.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_eval_excerpt_gain", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TokenF1Tests(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_완전히_같으면_1점(self):
        self.assertEqual(self.m.token_f1("양자 얽힘의 핵심", "양자 얽힘의 핵심"), 1.0)

    def test_겹치는_게_없으면_0점(self):
        self.assertEqual(self.m.token_f1("apple banana", "quantum entanglement"), 0.0)

    def test_한쪽이_비면_0점(self):
        self.assertEqual(self.m.token_f1("", "gold text"), 0.0)
        self.assertEqual(self.m.token_f1("candidate", ""), 0.0)

    def test_부분_겹침은_손으로_계산한_값과_같다(self):
        # candidate={a,b,c} gold={b,c,d} → overlap 2, P=2/3, R=2/3, F1=2/3
        f1 = self.m.token_f1("a b c", "b c d")
        self.assertAlmostEqual(f1, 2 / 3)

    def test_더_많은_어휘를_붙이면_재현율_쪽으로_점수가_오른다(self):
        """이 도구의 핵심 가설: one_liner 단독보다 one_liner+발췌가 정답과 더
        겹쳐야 한다(정답이 발췌 쪽에도 있을 때)."""
        gold = "양자 얽힘은 두 입자가 거리와 무관하게 상태를 공유하는 현상이다"
        only = self.m.token_f1("얽힘의 핵심 정리", gold)
        plus = self.m.token_f1("얽힘의 핵심 정리 양자 얽힘은 두 입자가 상태를 공유", gold)
        self.assertGreater(plus, only)

    def test_대소문자를_구분하지_않는다(self):
        self.assertEqual(self.m.token_f1("Quantum", "quantum"), 1.0)


class GoldSectionTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_다음_헤딩_전까지만_자른다(self):
        text = "# 문서\n\n## 첫째\n\n첫째 본문 한 줄.\n\n## 둘째\n\n둘째 본문.\n"
        self.assertEqual(self.m.gold_section(text, "첫째"), "첫째 본문 한 줄.")
        self.assertEqual(self.m.gold_section(text, "둘째"), "둘째 본문.")

    def test_마지막_헤딩은_문서_끝까지(self):
        text = "## 유일한 절\n\n본문 한 줄.\n본문 두 줄.\n"
        self.assertEqual(self.m.gold_section(text, "유일한 절"), "본문 한 줄.\n본문 두 줄.")

    def test_없는_제목이면_빈_문자열(self):
        text = "## 있는 절\n\n본문.\n"
        self.assertEqual(self.m.gold_section(text, "없는 절"), "")


if __name__ == "__main__":
    unittest.main()
