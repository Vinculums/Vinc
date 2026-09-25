# -*- coding: utf-8 -*-
"""2D 노드 마크는 256px 로 구워진다.

정본: [[decision:2d-marks-are-baked-at-256-px]].

`@sigma/node-image` 는 그림을 **고유 크기**로 텍스처 판에 굽고 화면에서
확대한다. 기본값(`size:{mode:"max",value:512}`)은 고유 크기를 넘지 않는데, 마크
SVG 의 고유 크기가 64 였던 동안 모든 마크가 64×64 비트맵이 되어 레티나·확대에서
흐릿하고 계단졌다. 두 자리를 함께 못박는다 — 한쪽만 남으면 다른 쪽이 다시 상한이 된다.

앱에는 JS 테스트 러너가 없어 소스를 대조한다(`test_view_url.py` 와 같은 자리)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "src" / "surfaces"


class MarksAreBakedLarge(unittest.TestCase):
    def test_마크_SVG_의_고유_크기가_256_이다(self):
        src = (SRC / "view-planar.ts").read_text(encoding="utf-8")
        m = re.search(r"export const MARK_TEX_PX = (\d+);", src)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 256)
        body = src[src.index("export function markTexture"):]
        body = body[:body.index("\n}\n")]
        self.assertIn('width="${MARK_TEX_PX}" height="${MARK_TEX_PX}"', body)
        # 좌표계(viewBox)는 그대로 64 — 모양은 안 바뀐다.
        self.assertIn('viewBox="0 0 ${TEX} ${TEX}"', body)

    def test_프로그램이_굽는_크기를_강제한다(self):
        """`max` 모드는 고유 크기를 넘지 않는다. `force` 라야 SVG 가 무엇을 선언하든
        그 크기로 굽는다."""
        src = (SRC / "view2d.ts").read_text(encoding="utf-8")
        prog = src[src.index("circle: createNodeImageProgram({"):]
        prog = prog[:prog.index("}),")]
        self.assertRegex(prog, r'size: \{ mode: "force", value: MARK_TEX_PX \}')


if __name__ == "__main__":
    unittest.main()
