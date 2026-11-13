"""그래프: 지도에서 고른 노드는 카드와 옆 칸을 함께 연다.

소스 잠금이다. 동작은 데모 빌드를 헤드리스 브라우저로 눌러 확인했다(지도 클릭 → 카드 하나 ·
옆 칸 · `#view/trace/<id>`). 여기서는 그 모양이 조용히 옛 자리로 돌아가지 않게 붙잡는다:
지도 클릭이 다시 카드 없이 열리면 사이드바에서 고른 것과 지도에서 고른 것이 다르게 보인다.
영역 부피의 이름표는 3D 렌더러와 함께 걷었다.

근거: [[decision:a-node-picked-in-the-graph-opens-its-card-and-the-side-pane-2026-10-29]]
"""
from __future__ import annotations

import unittest

from _src import func, read

VIEW = read("view")


class MapClick(unittest.TestCase):
    def test_the_3d_click_route_is_gone_from_the_graph_tab(self):
        # 그래프 탭은 나침반 하나다. 3D 의 지도 클릭 길(openFromMap)은 걷었고, 노드 상세는 openDetail 이 연다
        self.assertNotIn("function openFromMap(", VIEW)
        body = func(VIEW, "openDetail")
        self.assertIn("focusOnMap(nd);", body)
        self.assertIn("railFor(nd);", body)


if __name__ == "__main__":
    unittest.main()
