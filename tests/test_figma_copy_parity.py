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

    def test_check_passes_when_the_site_is_here_and_says_so_when_it_is_not(self):
        # ⚠ `check() == 0` 만 단언하면 사이트가 없는 CI 에서도 통과하고, 그건
        #   "검사했다"가 아니라 "건너뛰었다"다. 어느 경로를 탔는지까지 단언한다.
        import contextlib
        import io as _io
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fc.check()
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        if fc.SITE.exists():
            self.assertIn("문구 대조", out)
            self.assertIn("어긋남 0건", out)
        else:
            self.assertIn("SKIP", out)

    def test_the_site_path_can_be_pointed_somewhere_else(self):
        # CI 배치가 로컬과 다르다. 밖에서 지정하지 못하면 게이트는 소비 리포에서 못 돈다.
        import importlib
        import os
        os.environ["VINC_SITE_DIR"] = str(pathlib.Path(__file__).parent)
        try:
            spec = importlib.util.spec_from_file_location(
                "figma_copy_env", HUB / "scripts" / "figma_copy.py")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            self.assertEqual(m.SITE, pathlib.Path(__file__).parent.resolve())
        finally:
            del os.environ["VINC_SITE_DIR"]


if __name__ == "__main__":
    unittest.main()
