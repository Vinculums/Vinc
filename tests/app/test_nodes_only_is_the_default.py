# -*- coding: utf-8 -*-
"""Bring & Go 는 걷혔고, 기본 연결 모드는 노드만(`none`)이다. 크기·밀도 기본 0.7.

정본: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]].

앱에는 JS 테스트 러너가 없어 소스를 대조한다(`test_view_url.py` 와 같은 자리).
여기서 고정하는 것:
  ① 선택지에 `bring` 이 없고 `none` 이 있으며 기본이다.
  ② 기본 크기 배율과 밀도(`cone`)가 0.7 이다.
  ③ 옛 기본(1)을 값으로 든 저장값은 판(`pv`)이 없으면 새 기본으로 읽는다 —
     저장이 늘 객체 통째라 손대지 않은 사람도 옛 기본을 들고 있다.
  ④ 저장된 `bring` 은 `none` 으로 읽는다.
  ⑤ 2D·3D 렌더러에 고리·비행 기계가 남지 않았다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "app" / "src"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class Prefs(unittest.TestCase):
    def setUp(self):
        self.src = _read("view-prefs.ts")

    def test_bring_이_없고_none_이_있다(self):
        t = re.search(r"export type EdgeShow = ([^;]+);", self.src).group(1)
        self.assertIn('"none"', t)
        self.assertNotIn('"bring"', t)
        shows = self.src[self.src.index("export const edgeShowsFor"):]
        shows = shows[:shows.index(";")]
        self.assertEqual(shows.count('"none"'), 2, "3D 와 평면 양쪽에서 고를 수 있어야 한다")

    def test_기본값(self):
        d = self.src[self.src.index("const DEFAULTS: ViewPrefs = {"):]
        d = d[:d.index("};")]
        self.assertRegex(d, r'edges: "none"')
        self.assertRegex(d, r"nodeScale: 0\.7")
        self.assertRegex(d, r"cone: 0\.7")

    def test_옛_기본을_든_저장값은_새_기본으로_읽는다(self):
        body = self.src[self.src.index("export function loadPrefs"):]
        # 이 옮겨 읽기는 판 2 에서 생겼다. `PREFS_VERSION` 과 비교하면 판을 올릴 때마다
        # 판 2 사용자가 고른 크기 1 이 기본으로 되돌아간다(VIN-600 에서 판 3 으로 올렸다).
        self.assertIn("if (pv < 2) {", body)
        self.assertNotIn("Number(p.pv) >= PREFS_VERSION", body)
        self.assertIn("if (p.nodeScale === 1) p.nodeScale = DEFAULTS.nodeScale;", body)
        self.assertIn("if (p.cone === 1) p.cone = DEFAULTS.cone;", body)
        self.assertIn("pv: PREFS_VERSION", self.src[self.src.index("export function savePrefs"):])

    def test_저장된_bring_은_none_이다(self):
        self.assertIn('p.edges == null || p.edges === "bring" ? DEFAULTS.edges', self.src)


class Renderers(unittest.TestCase):
    def test_고리_비행_기계가_없다(self):
        for rel in ("surfaces/view2d.ts", "surfaces/view3d.ts"):
            src = _read(rel)
            for word in ("bringOn", "gathered", "planFly", "ringSlots", "flyRaf"):
                self.assertNotIn(word, src, f"{rel}: {word}")

    def test_노드만이면_선을_그리지_않는다(self):
        self.assertIn('if (prefs.edges === "none") return { ...data, hidden: true };',
                      _read("surfaces/view2d.ts"))
        v3 = _read("surfaces/view3d.ts")
        self.assertIn('let nodesOnly = prefs.edges === "none";', v3)
        live_update = v3[v3.index('(g as any).__update ='):]
        self.assertIn('nodesOnly = prefs.edges === "none";', live_update)
        self.assertIn("if (nodesOnly) return pal.link.base + ALPHA0;", v3)

    def test_패널에_노드만_칩이_있다(self):
        v = _read("surfaces/view.ts")
        self.assertIn("none: ART.edgeNone", v)
        self.assertNotIn("edgeBring", v)
        c = _read("copy.ts")
        self.assertIn('none: "nodes only"', c)
        self.assertIn('none: "노드만"', c)
        self.assertNotIn("bring &amp; go", c)


if __name__ == "__main__":
    unittest.main()
