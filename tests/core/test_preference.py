# -*- coding: utf-8 -*-
"""Tests for the preference-capture surface (pin / tag / touch plan builders)."""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import preference as pref  # noqa: E402


class PinTests(unittest.TestCase):
    def test_pin_sets_true(self):
        plan = pref.pin_plan("2026-06-30-s1")
        self.assertEqual(plan.kind, "pin")
        self.assertIn("SET e.pinned = $pinned", plan.cypher)
        self.assertEqual(plan.params, {"id": "2026-06-30-s1", "pinned": True})

    def test_unpin_sets_false(self):
        plan = pref.pin_plan("s1", pinned=False)
        self.assertIs(plan.params["pinned"], False)


class TagTests(unittest.TestCase):
    def test_tag_is_set_union(self):
        plan = pref.tag_plan("s1", ["billing", "kr"])
        # union: 기존+신규 합집합(중복 제거). kuzu 리스트 컴프리헨션 미지원 →
        # list_distinct(list_cat(...))로 정합(§6-2). 리스트 컴프리헨션 잔존 금지.
        self.assertIn("list_distinct(list_cat(coalesce(e.tags, []), $tags))", plan.cypher)
        self.assertNotIn("[t IN $tags", plan.cypher)
        self.assertEqual(plan.params["tags"], ["billing", "kr"])


class TouchTests(unittest.TestCase):
    def test_touch_increments_access_count(self):
        plan = pref.touch_plan("s1")
        self.assertIn("e.access_count = coalesce(e.access_count, 0) + 1", plan.cypher)
        self.assertNotIn("last_access = $now", plan.cypher)  # no timestamp -> not set
        self.assertNotIn("now", plan.params)

    def test_touch_stamps_last_access_when_now_given(self):
        plan = pref.touch_plan("s1", now="2026-06-30T12:00:00")
        self.assertIn("e.last_access = $now", plan.cypher)
        self.assertEqual(plan.params["now"], "2026-06-30T12:00:00")


class ExecuteTests(unittest.TestCase):
    @mock.patch("store.querybase.open_store", return_value=None)
    def test_pin_without_db_degrades_without_leaking_plan(self, _open):
        """F3 — 쓰기 경로도 같다. 강등이 곧 내부 노출이 되면 안 된다."""
        res = pref.pin("s1")
        self.assertFalse(res["executed"])
        self.assertNotIn("plan", res, f"plan 이 실렸다: {res.get('plan')}")


if __name__ == "__main__":
    unittest.main()
