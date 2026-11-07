# -*- coding: utf-8 -*-
"""Claude Design 으로 나가는 파일에 모션이 실려 있는가.

왜 이 파일이 있나: `push_ssot.py` 는 한때 모션 토큰(`--ease-*`·`--dur-*`)이 빠진 파일을
보냈고, 배포본은 곡선도 시간도 없이 13줄 짧았는데 아무도 세지 않았다. 지금은 사이트와 같은
본문을 스냅샷에서 바로 만들어 보내므로, 그 본문에 모션이 있는지를 지킨다.
근거: [[record:claude-design-tokens-frozen-at-2026-08-08]]
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

DESIGN = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "design"
_spec = importlib.util.spec_from_file_location("push_ssot", DESIGN / "push_ssot.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

MOTION = ("--ease-out", "--ease-in-out", "--dur-fast", "--dur-base")


class TheExportCarriesMotion(unittest.TestCase):
    def test_the_export_is_the_site_body(self):
        self.assertEqual(ps.SITE_CSS, "site-tokens.css")

    def test_the_built_body_has_the_motion_tokens(self):
        css = ps.build_tokens.outputs(ps.build_tokens.load())[ps.SITE_CSS]
        for k in MOTION:
            self.assertIn(k, css, f"{k} 가 보내는 본문에 없다")

    def test_the_export_is_not_committed(self):
        self.assertEqual(ps.OUT.relative_to(DESIGN).parts[0], "out")
        ignore = (DESIGN / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("out/", ignore.split())


if __name__ == "__main__":
    unittest.main()
