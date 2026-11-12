# -*- coding: utf-8 -*-
"""테마를 바꾸면 열린 지도는 **다시 칠해질 뿐** 다시 그려지지 않는다.

정본: [[decision:a-theme-switch-repaints-the-open-view-in-place]].

주제 색·잉크·안개·조명·환경맵은 마운트 때 그때의 모드로 굳는다. 그래서 설정(또는
OS 가 "auto" 일 때)에서 테마를 뒤집으면 열린 지도가 반대 모드의 값을
들고 남았다 — 다크의 파스텔(명도 .72)이 밝은 배경에서 바래고, 라이트의 짙은 색이 검은
배경에서 탁해졌다. 다시 마운트하면 풀리지만 그 길(`show`)은 읽던 문서 열과 노드 카드를
닫는다. 그래서 색만 갈아 끼우는 길을 따로 둔다.

앱에는 JS 테스트 러너가 없어 이 리포의 관례대로 소스를 대조한다
(`test_node_colour_stays_solid.py` · `test_lnb_tint.py` 와 같은 자리). 실제 색이 새로
연 화면과 같은지는 브라우저에서 수치로 대조했다(PR 본문)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app" / "src"


def _read(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


def _fn(src: str, head: str) -> str:
    """`head` 로 시작하는 함수(또는 화살표) 본문 — **같은 들여쓰기**의 다음 선언 전까지."""
    at = src.index(head)
    indent = at - (src.rfind("\n", 0, at) + 1)
    rest = src[at:]
    nxt = re.search(r"\n" + " " * indent + r"(const|function|let|export|type|g\.on|/\*\*|\(g as any\))",
                    rest[len(head):])
    return rest if not nxt else rest[:len(head) + nxt.start()]


class WiringTests(unittest.TestCase):
    def test_applyTheme_이_지도도_다시_칠한다(self):
        """좌측 트리와 **같은 자리**여야 한다 — 설정 클릭과 OS 추종이 만나는 곳이 거기뿐이다."""
        main = _read("main.ts")
        body = _fn(main, "export function applyTheme() {")
        self.assertIn("repaintLnb()", body)
        self.assertIn("repaintView()", body)

    def test_다시_그리지_않고_다시_칠한다(self):
        """show() 는 문서 열(detail.hidden)과 노드 카드(closeFloatPanel)를 닫는다."""
        body = _fn(_read("surfaces/view.ts"), "function retheme(): void {")
        for bad in ("show(", "route(", "closeFloatPanel", "detail.hidden", "swapSurface"):
            self.assertNotIn(bad, body, bad)
        # 표는 한 벌 — 나침반이 새 모드로 자기 색 표를 다시 짓는다
        self.assertIn("compass?.retheme()", body)
        # 떠 있는 카드의 표식도 같은 표를 따른다.
        self.assertIn(".float-mark[data-domain]", body)
        self.assertIn("compass?.colour(mk.dataset.domain", body)

    def test_같은_모드를_다시_쓰면_아무것도_안_한다(self):
        body = _fn(_read("surfaces/view.ts"), "function retheme(): void {")
        self.assertIn("if (mode === paintedMode) return;", body)

    def test_떠난_표면은_칠하지_않는다(self):
        src = _read("surfaces/view.ts")
        self.assertIn("themeRepaint = () => { if (myGen === liveGen && canvas.isConnected) retheme(); };",
                      src)


if __name__ == "__main__":
    unittest.main()
