# -*- coding: utf-8 -*-
"""글씨 크기 게이트(gate_type_scale)의 판정 시험.

잠그는 것: 정의된 토큰과 부모를 따르는 값은 통과, 직접 쓴 값·없는 토큰·대체값 붙은 토큰은
위반, `fs-allow: 이유` 가 붙은 직접 값은 예외로 세고 이유를 함께 낸다. 그리고 지금 앱은 통과한다.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.design import gate_type_scale as G  # noqa: E402

SIZES = {"2xs", "xs", "sm", "base", "lg"}


class JudgeTests(unittest.TestCase):
    def check(self, text):
        return G.check_text(text, "x.css", SIZES)

    def test_토큰과_부모를_따르는_값은_통과한다(self):
        bad, allowed = self.check(".a { font-size: var(--fs-xs); }\n.b { font-size: .85em; }\n"
                                  ".c { font-size: inherit; }\n.d { font-size: 100%; }")
        self.assertEqual((bad, allowed), ([], []))

    def test_직접_쓴_값은_위반이다(self):
        bad, _ = self.check(".a { font-size: .75rem; }\n.b { font-size: 12px; }")
        self.assertEqual(len(bad), 2)
        self.assertIn("직접 쓴 값", bad[0])

    def test_없는_토큰과_대체값_붙은_토큰은_위반이다(self):
        bad, _ = self.check(".a { font-size: var(--fs-md, 1rem); }\n.b { font-size: var(--fs-sm, .8125rem); }")
        self.assertEqual(len(bad), 2)
        self.assertIn("정의되지 않은 토큰", bad[0])
        self.assertIn("대체값", bad[1])

    def test_예외는_이유와_함께_센다(self):
        bad, allowed = self.check(".h1 { font-size: 1.5rem; }  /* fs-allow: 페이지 제목 */")
        self.assertEqual(bad, [])
        self.assertEqual(allowed, ["x.css:1: 1.5rem  [페이지 제목]"])

    def test_인라인_스타일_문자열도_본다(self):
        bad, _ = G.check_text('el.style.cssText = "left:12px;font-size:12px;color:red";', "x.ts", SIZES)
        self.assertEqual(len(bad), 1)


class RepoTests(unittest.TestCase):
    def test_앱은_통과한다(self):
        self.assertEqual(G.main([]), 0)


if __name__ == "__main__":
    unittest.main()
