# -*- coding: utf-8 -*-
"""Bring & Go 는 걷혔다: 데려오기는 화면과 문구에 없고, 노드만이라는 이름은 남는다.

정본: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]].
보기 설정(`view-prefs.ts`)에는 선·크기·밀도 칸이 없다: 그래프 보기는 나침반 하나이고 저장하는 칸은
`railW` 하나다([[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]). 이전 판의 저장값이
그대로 읽히는지는 `app/tests/view-graph-read.test.mjs` 가 본다(저장값을 넣고 읽어 보는 행동 시험)."""
from __future__ import annotations

import unittest

from _src import read


class Names(unittest.TestCase):
    def test_노드만_이름이_남고_데려오기는_없다(self):
        # 보기 옵션 패널(견본 칩)은 3D 와 함께 걷었다. 노드만이라는 이름과 데려오기가 없다는 것은 그대로 잡는다
        v = read("view")
        self.assertNotIn("edgeBring", v)
        c = read("copy")
        self.assertIn('none: "nodes only"', c)
        self.assertIn('none: "노드만"', c)
        self.assertNotIn("bring &amp; go", c)


if __name__ == "__main__":
    unittest.main()
