# -*- coding: utf-8 -*-
"""표면이 셸(상단 바 · 좌측 트리 · 저절로 새로 고침)에 거는 손잡이는 한 문(`surface-binding.ts`)으로만 걸고,
라우터(`main.ts route`)가 표면을 갈아 끼울 때마다 그 문을 한 번에 닫는다. 그래서 라우트가 바뀌면 걸린 것이 전부 풀린다.
문이 무엇을 어떤 순서로 풀는지는 노드 시험(`app/tests/home-list.test.mjs`)이 실제 모듈로 돌려 본다. 여기서는 앱의
어느 모듈도 그 문을 건너 setter 를 직접 부르지 않는지, 라우터가 문을 닫는지를 본다."""
from __future__ import annotations

import re
import unittest

from _src import SRC, func, read

SETTERS = ("setViewHooks", "setSearchFilter", "setTopbarTools", "setLnbNavigator", "setLnbTopicPicker",
           "setLnbScopeClearer", "setLnbDocOpener", "setLnbGraphSource", "setLiveRefresher")
# setter 를 정의하는 셸 모듈과 문 자신만 그 이름을 부른다.
OWNERS = {"topbar.ts", "lnb.ts", "live.ts", "surface-binding.ts"}


class OneDoorTests(unittest.TestCase):
    def test_표면은_setter_를_직접_부르지_않는다(self):
        hits = []
        for p in sorted(SRC.rglob("*.ts")):
            if p.name in OWNERS:
                continue
            text = p.read_text(encoding="utf-8")
            for name in SETTERS:
                for m in re.finditer(rf"\b{name}\(", text):
                    hits.append(f"{p.relative_to(SRC)}:{text.count(chr(10), 0, m.start()) + 1} {name}")
        self.assertEqual(hits, [], "표면은 bindSurface 로만 건다: 그래야 라우터의 unbindSurface 가 그것을 푼다")

    def test_문은_아홉_setter_를_모두_안다(self):
        binding = read("surface-binding")
        for name in SETTERS:
            self.assertIn(f": {name},", binding, name)

    def test_라우터가_표면을_갈아_끼울_때마다_문을_닫는다(self):
        route = func(read("main"), "route")
        self.assertIn("unbindSurface();", route)
        # 새 표면을 그리기 전에 닫는다(새 표면이 거는 것을 지우지 않게).
        self.assertLess(route.index("unbindSurface();"), route.index("await renderView(surfaceEl, resumeView)"))


if __name__ == "__main__":
    unittest.main()
