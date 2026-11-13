# -*- coding: utf-8 -*-
"""노드는 어떤 상태에서도 제 색 그대로다.

정본: [[decision:a-node-keeps-its-own-colour-in-every-state]].

같은 노드가 어떤 때는 진한 색, 어떤 때는 옅은 틴트로 보였다. 틴트 팔레트가 따로
있던 것이 아니라 한 가지 색이 상태에 따라 배경 쪽으로 섞였다 — hover·점등의
물러남(배경색 혼합), 거리 안개,
그리고 주제 하나로 새로 지은 색상표(노드 카드). 이제 물러나는 것은 엣지와 라벨뿐이다.

앱에는 JS 테스트 러너가 없어(`app/package.json` 에 `test` 없음) 이 리포의 관례대로
소스를 대조한다(`test_view_url.py` · `test_lnb_tint.py` 와 같은 자리)."""
from __future__ import annotations

import unittest

from _src import read


class CardTests(unittest.TestCase):
    def test_노드_카드는_지도와_같은_색상표를_읽는다(self):
        src = read("view")
        self.assertNotIn("tintMap([nd.domain]", src)
        # 표는 지도(나침반)가 짓는 그 한 벌이다 — 카드가 따로 지으면 넣는 집합이 갈려 순위가 밀린다.
        self.assertIn("compass?.colour(nd.domain.trim())", src)
        self.assertNotIn("tintMap(", src)

    def test_범례가_노드_물러남을_가르치지_않는다(self):
        self.assertNotIn("tierNode", read("copy"))
        self.assertNotIn("tierNode", read("view"))


if __name__ == "__main__":
    unittest.main()
