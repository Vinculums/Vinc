# -*- coding: utf-8 -*-
"""문구 대조 게이트가 진짜로 무언가를 지키는가.

왜: 이 게이트는 서명만 비교한다. 서명 함수가 조용히 상수를 돌려주거나 스냅샷이 비면
매번 초록인 채 아무것도 안 지킨다. 그 두 가지를 여기서 막는다.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parent.parent / "design-hub"
_spec = importlib.util.spec_from_file_location("figma_copy", HUB / "scripts" / "figma_copy.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


class TheSignatureSeparatesText(unittest.TestCase):
    def test_one_changed_character_changes_the_signature(self):
        self.assertNotEqual(fc.sig("Three steps."), fc.sig("Three steps!"))

    def test_length_alone_does_not_decide(self):
        self.assertNotEqual(fc.sig("abc"), fc.sig("cba"))

    def test_none_and_empty_are_the_same_and_marked_by_zero_length(self):
        self.assertEqual(fc.sig(None), fc.sig(""))
        self.assertTrue(fc.sig(None).startswith("0:"))


class TheSnapshotIsRealAndCurrent(unittest.TestCase):
    def test_the_snapshot_covers_the_mapped_routes(self):
        if not fc.SIG.exists():
            self.skipTest("스냅샷이 없다")
        snap = json.loads(fc.SIG.read_text(encoding="utf-8"))["signatures"]
        self.assertGreater(len(snap), 100, "스냅샷이 비면 게이트는 통과하며 아무것도 안 지킨다")
        prefixes = {n.split("/")[0] for n in snap}
        self.assertIn("pricing", prefixes)
        self.assertIn("connect", prefixes)

    def test_check_passes_against_the_live_site_or_skips(self):
        # 사이트가 없는 CI 잡에서는 건너뛰되, 있으면 실제로 0 이어야 한다.
        self.assertEqual(fc.check(), 0)


if __name__ == "__main__":
    unittest.main()
