# -*- coding: utf-8 -*-
"""잇기는 노드 카드의 버튼에서 시작하는 관계 고르기 하나로 간다.

정본: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §3.3 (미뤘던 항목).

끌어서 잇기(Alt 끌기)는 3D 렌더러의 손짓이었고 렌더러와 함께 걷었다. 여기서는
남은 **배선**을 소스로 대조한다(`test_view_retheme.py` 와 같은 관례):

- 쓰는 길은 `openRelationPicker` 하나뿐이다.
- 그래프 탭(나침반)은 끌기 손잡이를 넘기지 않고, 고르기 띠는 없는 손짓을 가르치지 않는다.
"""
from __future__ import annotations

import unittest

from _src import read


class ViewWiringTests(unittest.TestCase):
    view = read("view")

    def test_그래프_탭은_끌어_잇지_않는다(self):
        # 끌어 잇기는 3D 렌더러의 손짓이었다. 나침반에는 없으므로 그래프 탭은 그 손잡이를 넘기지 않는다
        self.assertNotIn("connectByDrag", self.view)

    def test_쓰는_길은_connect_ts_하나(self):
        """앱 전체에서 엣지를 쓰는 잇기 호출은 관계 고르기 안의 하나뿐이다."""
        c = read("connect")
        self.assertEqual(c.count("api.ingestFragment("), 1)
        picker = c[c.index("function openRelationPicker"):]
        self.assertIn("api.ingestFragment(", picker)

    def test_버튼_길이_남아_있다(self):
        self.assertIn('q("#c-connect")!.addEventListener("click"', self.view)
        self.assertIn("m.startConnect(", self.view)

    def test_없는_손짓을_가르치지_않는다(self):
        # 고르기 띠의 "Alt 를 누른 채 끌어도 이을 수 있어요" 줄은 3D 렌더러의 손짓을 가르쳤다
        self.assertNotIn("dragHint", read("connect"))
        self.assertNotIn("dragHint", read("copy"))
        self.assertNotIn("pick-drag", read("app.css"))


if __name__ == "__main__":
    unittest.main()
