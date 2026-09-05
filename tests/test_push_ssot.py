# -*- coding: utf-8 -*-
"""Claude Design 으로 나가는 파일에 모션이 실려 있는가.

왜 이 파일이 있나: 2026-09-06 까지 `push_ssot.py` 는 `DESIGN/tokens.css` 를 보냈는데,
모션 토큰(`--ease-*`·`--dur-*`)은 `gen_motion.py` 가 **빌드 산출물에만** 덧붙인다.
그래서 배포본은 곡선도 시간도 없이 13줄 짧았고, 아무도 그걸 세지 않았다. 조용한 결손은
게이트가 없으면 다음 세션에 그대로 돌아온다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

HUB = pathlib.Path(__file__).resolve().parent.parent / "design-hub"
_spec = importlib.util.spec_from_file_location("push_ssot", HUB / "scripts" / "push_ssot.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

MOTION = ("--ease-out", "--ease-in-out", "--dur-fast", "--dur-base")


class TheExportCarriesMotion(unittest.TestCase):
    def test_the_source_is_the_built_file_not_the_hub_file(self):
        # 허브 파일에는 모션이 없다. 그걸 보내는 것이 정확히 그 고장이었다.
        self.assertEqual(ps.CSS.name, "site-tokens.css")
        self.assertIn("dist", ps.CSS.parts)

    def test_the_built_file_has_the_motion_tokens(self):
        if not ps.CSS.exists():
            self.skipTest("빌드 산출물이 없다 — scripts/build_dist.py 선행")
        css = ps.CSS.read_text(encoding="utf-8")
        for k in MOTION:
            self.assertIn(k, css, f"{k} 가 빌드 산출물에 없다")

    def test_the_hub_file_alone_would_not_pass(self):
        # 이 단언이 깨지는 날은 gen_motion 이 허브 파일에도 쓰게 된 날이다. 그때는
        # 위의 경로 단언을 다시 판단할 것 — 지금은 두 파일이 다르다는 것이 전제다.
        hub_css = HUB / "DESIGN" / "tokens.css"
        if not hub_css.exists():
            self.skipTest("DESIGN/tokens.css 가 없다")
        self.assertNotIn("--ease-out", hub_css.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
