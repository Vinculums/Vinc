# -*- coding: utf-8 -*-
"""주제 색은 공홈 조형의 보석색이고 늘 켜져 있으며 영역이 보석 하나를 입는다.

근거: [[decision:topics-wear-their-area-jewel-2026-10-28]] ·
[[decision:app-nodes-wear-the-hero-glass-2026-09-26]]

지키는 것:
- 보석색은 토큰이고 모드마다 다섯이며, 평면 마크로 그려도 배경 위에서 읽힌다
  (다크 4.5:1 · 라이트 3:1 이상 — 좌측 트리 도트·타임라인·번들은 유리 음영 없이 칠한다).
- 켜고 끄는 토글이 없다(기기별 저장값이 같은 그래프를 기기마다 다른 색으로 만들었다).

앱에는 JS 테스트 러너가 없어 이 리포의 관례대로 소스를 대조한다(`test_view_url.py` 와
같은 자리)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "app" / "src"
TOKENS = json.loads((ROOT / "scripts/design/tokens.json").read_text(encoding="utf-8"))


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _lum(hexstr: str) -> float:
    v = int(hexstr.lstrip("#"), 16)
    ch = []
    for c in ((v >> 16) & 255, (v >> 8) & 255, v & 255):
        c /= 255
        ch.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def _bg(mode: str) -> str:
    ref = TOKENS["semantic"][mode]["background"]            # "{grain_100}" 같은 참조
    ref = ref["$value"] if isinstance(ref, dict) else ref
    prim = TOKENS["primitive"][ref.strip("{}")]
    return prim["$value"] if isinstance(prim, dict) else prim


class JewelTokenTests(unittest.TestCase):
    def test_모드마다_보석_다섯이다(self):
        tj = TOKENS["topicJewels"]
        for mode in ("light", "dark"):
            self.assertEqual(len(tj[mode]), 5, mode)

    def test_평면_마크로도_배경_위에서_읽힌다(self):
        floor = {"dark": 4.5, "light": 3.0}
        for mode, need in floor.items():
            bg = _bg(mode)
            for hexstr in TOKENS["topicJewels"][mode]:
                self.assertGreaterEqual(_contrast(hexstr, bg), need, f"{mode} {hexstr} on {bg}")

    def test_토큰이_theme_ts_로_생성된다(self):
        theme = _read("theme.ts")
        self.assertIn("export const topicJewels = {", theme)
        self.assertNotIn("domainTint", theme)


class AlwaysOnTests(unittest.TestCase):
    def test_토글이_없다(self):
        for rel in ("copy.ts", "surfaces/view.ts", "lnb.ts"):
            self.assertNotIn("domainTint", _read(rel), rel)
        self.assertNotIn("domainTint: boolean", _read("view-prefs.ts"))

    def test_색표는_보석을_순서대로_쓰고_섞지_않는다(self):
        tint = _read("view-tint.ts")
        self.assertIn("const list = topicJewels[mode];", tint)
        self.assertIn("rotateHue(base, turn)", tint)
        self.assertNotIn("GOLDEN", tint)

    def test_영역이_보석_하나를_입고_주제는_그것을_조금_돌린다(self):
        tint = _read("view-tint.ts")
        # 영역 판정은 레이아웃과 한 벌이다(갈리면 같은 영역의 주제가 다른 색으로 부피 밖에 선다).
        self.assertIn('import { areaKeys } from "./area-key";', tint)
        self.assertIn('export { areaKeys };', _read("view-layout-spatial.ts"))
        self.assertIn("const TOPIC_TURN = [0, 4, -4, 8, -8, 12, -12];", tint)
        self.assertIn("export function tintMap(", tint)
        self.assertIn("return tintPlan(domains, mode).topics;", tint)


if __name__ == "__main__":
    unittest.main()
