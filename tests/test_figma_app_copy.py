# -*- coding: utf-8 -*-
"""앱 보드 문구 게이트가 실제로 무언가를 지키는가.

이 게이트는 지문 비교라 조용히 죽기 쉽다. 지문 함수가 상수를 돌려주거나, 허용목록이
전부를 덮거나, 덤프가 비면 매번 초록인 채로 아무것도 안 지킨다. 그 셋을 여기서 막는다.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parent.parent / "design-hub"
_spec = importlib.util.spec_from_file_location("figma_app_copy", HUB / "scripts" / "figma_app_copy.py")
fac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fac)


def dump(rows):
    return {"strings": rows}


class TheFingerprintSeparatesStrings(unittest.TestCase):
    def test_one_character_changes_it(self):
        self.assertNotEqual(fac.sig("Share as a pack"), fac.sig("Share as a Pack"))

    def test_order_matters(self):
        self.assertNotEqual(fac.sig("abc"), fac.sig("cba"))


class WhatCountsAsFailure(unittest.TestCase):
    def test_a_string_the_app_never_says_fails(self):
        r = fac.check(dump([[111, "Invented label", "View"]]), {222})
        self.assertEqual(len(r["fail"]), 1)

    def test_a_string_the_app_says_passes(self):
        r = fac.check(dump([[222, "Share", "View"]]), {222})
        self.assertEqual((r["ok"], len(r["fail"])), (1, 0))

    def test_a_declared_exception_passes_and_is_counted_by_category(self):
        fac.ALLOW[999001] = "sample"
        try:
            r = fac.check(dump([[999001, "Gauge symmetry", "View"]]), set())
            self.assertEqual((r["declared"], len(r["fail"])), ({"sample": 1}, 0))
        finally:
            del fac.ALLOW[999001]

    def test_an_open_verdict_is_listed_one_by_one_not_buried_in_a_count(self):
        # 열린 판정은 사람이 정할 것이 남았다는 뜻이다. 분류 숫자에 묻히면 안 된다.
        fac.ALLOW[999002] = "open"
        try:
            r = fac.check(dump([[999002, "Trace from here", "View"]]), set())
            self.assertEqual(len(r["open"]), 1)
            self.assertNotIn("open", r["declared"])
            self.assertEqual(len(r["fail"]), 0)
        finally:
            del fac.ALLOW[999002]

    def test_an_allow_entry_whose_string_left_the_board_fails(self):
        # 보드에서 사라진 예외를 남겨 두면 허용목록이 계속 자란다.
        fac.ALLOW[999003] = "sample"
        try:
            r = fac.check(dump([[222, "Share", "View"]]), {222})
            self.assertIn(999003, r["stale"])
        finally:
            del fac.ALLOW[999003]


class TheRealInputsAreNotEmpty(unittest.TestCase):
    def test_the_dump_is_real(self):
        d = json.loads(fac.DUMP.read_text(encoding="utf-8"))
        self.assertGreater(len(d["strings"]), 300, "덤프가 비면 게이트는 통과하며 아무것도 안 본다")

    def test_the_app_has_many_literals(self):
        if not fac.APP.exists():
            self.skipTest("앱 소스가 없다")
        self.assertGreater(len(fac.code_sigs()), 1000)

    def test_the_allowlist_does_not_swallow_the_board(self):
        d = json.loads(fac.DUMP.read_text(encoding="utf-8"))
        # 허용목록이 보드의 절반을 넘으면 대조가 아니라 면제다.
        self.assertLess(len(fac.ALLOW), len(d["strings"]) // 2)

    def test_the_real_check_is_clean(self):
        if not fac.APP.exists():
            self.skipTest("앱 소스가 없다")
        d = json.loads(fac.DUMP.read_text(encoding="utf-8"))
        r = fac.check(d, fac.code_sigs())
        self.assertEqual((r["fail"], r["stale"]), ([], []))
        self.assertGreater(r["ok"], 150, "코드와 일치하는 것이 이만큼은 돼야 대조가 의미가 있다")


if __name__ == "__main__":
    unittest.main()
