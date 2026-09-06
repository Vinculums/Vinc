# -*- coding: utf-8 -*-
"""figma_app_copy — 앱 보드(캡처)의 신선도 판정. 2026-09-06 에 글자 대조에서 리비전 대조로 바뀌었다."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parents[1] / "design-hub"
_spec = importlib.util.spec_from_file_location("figma_app_copy", HUB / "scripts" / "figma_app_copy.py")
fac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fac)


class CheckTests(unittest.TestCase):
    def test_no_changes_after_the_capture_passes(self):
        r = fac.check({"sourceRev": "abc1234", "states": [{"id": "view__3d"}]}, [])
        self.assertTrue(r["ok"]); self.assertEqual(r["states"], 1)

    def test_a_commit_touching_app_src_after_the_capture_fails(self):
        r = fac.check({"sourceRev": "abc1234", "states": []}, ["deadbee fix(view): reorder lens rows"])
        self.assertFalse(r["ok"]); self.assertEqual(len(r["changes"]), 1)

    def test_a_manifest_without_a_revision_fails_by_itself(self):
        r = fac.check({"states": []}, [])
        self.assertFalse(r["ok"]); self.assertIn("sourceRev", r["reason"])


class GitTests(unittest.TestCase):
    def test_the_committed_manifest_is_fresh_or_names_the_commits(self):
        """실제 리포 위에서: 매니페스트의 리비전이 존재하고, 결과가 판정 가능한 형태여야 한다."""
        import json
        m = json.loads((HUB / "reports" / "captures" / "app" / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(m.get("sourceRev"), "app manifest carries no sourceRev")
        changes = fac.changes_since(m["sourceRev"])
        self.assertIsInstance(changes, list)

    def test_an_unknown_revision_raises_instead_of_passing(self):
        with self.assertRaises(RuntimeError):
            fac.changes_since("0000000000000000000000000000000000000000")


if __name__ == "__main__":
    unittest.main()
