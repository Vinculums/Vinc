"""상단 바는 카드 두 장: 검색 카드 + 보기 카드(관점 세그 + 렌즈)(2026-09-26).

소스 잠금이다. 배치 자체는 헤드리스 브라우저 실측으로 확인했고(창 1440 · 1100 · 1044 ·
1000 · 900 · 812 · 700 · 390), 여기서는 그 배치가 조용히 옛 자리로 돌아가지 않게 구조만
붙잡는다:

- 세그가 문서 트리 안에 있으면 트리를 접을 때 관점 손잡이도 사라진다.
- 검색과 보기 손잡이는 하는 일이 다른 컴포넌트다. 한 면에 섞으면 다시 한 덩어리로 읽힌다.
- 줄바꿈 기준이 창 폭이면 트리가 열린 창(1000)에서 입력이 147px 까지 눌린다.

근거: [[decision:the-top-bar-is-a-search-card-and-a-view-card-2026-09-26]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
VIEW = (ROOT / "app" / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
TOPBAR = (ROOT / "app" / "src" / "topbar.ts").read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a:src.index(end, a)]


def _rule(selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{[^}]*\}", CSS)
    assert m, f"{selector} 규칙이 없다"
    return m.group(0)


def _at(prelude: str) -> str:
    """`@media …` / `@container …` 블록의 본문(중괄호 짝을 센다)."""
    a = CSS.index(prelude)
    i = CSS.index("{", a)
    depth = 0
    for j in range(i, len(CSS)):
        if CSS[j] == "{":
            depth += 1
        elif CSS[j] == "}":
            depth -= 1
            if depth == 0:
                return CSS[i + 1:j]
    raise AssertionError(f"{prelude} 가 닫히지 않는다")


HEADER = _block(HTML, '<header id="topbar">', "</header>")


class TwoCards(unittest.TestCase):
    def test_search_card_then_view_card(self):
        find = HEADER.index('class="tb-card" id="tb-find"')
        view = HEADER.index('class="tb-card" id="tb-viewctl"')
        self.assertLess(find, HEADER.index('id="tb-search"'))
        self.assertLess(HEADER.index('id="tb-search"'), view)

    def test_view_card_holds_viewpoints_then_lens(self):
        card = HEADER[HEADER.index('id="tb-viewctl"'):]
        self.assertLess(card.index('id="tb-viewpoints"'), card.index('id="tb-lens"'))

    def test_document_tree_no_longer_holds_the_segment(self):
        lnb = _block(HTML, '<aside id="lnb"', "</aside>")
        self.assertNotIn("tb-viewpoints", lnb)

    def test_the_bar_is_a_row_and_the_cards_carry_the_surface(self):
        bars = re.findall(r"(?m)^#topbar \{[^}]*\}", CSS)
        self.assertGreaterEqual(len(bars), 2)
        for bar in bars:
            self.assertNotIn("background", bar)
            self.assertNotIn("box-shadow", bar)
        card = _rule(".tb-card")
        self.assertIn("background: hsl(var(--card))", card)
        self.assertIn("box-shadow: var(--shadow-elev-3)", card)
        # 부유 크롬의 유리도 카드가 받는다.
        self.assertRegex(CSS, r"\.tb-card, \.palette-card, #view-detail")

    def test_empty_view_card_hides(self):
        self.assertIn("#tb-viewctl:has(> #tb-viewpoints:empty):has(> #tb-lens:empty)", CSS)


class Lens(unittest.TestCase):
    def test_lens_renders_into_the_top_bar_slot(self):
        self.assertIn('lensSlot() ?? fallbackSlot("view-legend")', VIEW)
        self.assertIn('return $("tb-lens")', TOPBAR)

    def test_view_tools_corner_is_untouched(self):
        self.assertIn("right: calc(var(--rail-w, 0px) + var(--space-6, 1.5rem));", _rule("#view-tools"))

    def test_view_card_is_exactly_as_wide_as_the_lens_panel(self):
        # 패널이 카드 바로 아래에서 열린다 — 양 끝이 같은 세로선이어야 한다(발주자 지시).
        card = _rule("#tb-viewctl")
        self.assertIn("flex: 0 0 var(--lens-panel-w)", card)
        self.assertIn("width: var(--lens-panel-w)", card)
        self.assertIn("width: var(--lens-panel-w)", CSS[CSS.index("#view-lens-panel {"):])

    def test_panel_anchors_to_the_view_card(self):
        body = _block(VIEW, "function positionPanel()", "function openPanel()")
        self.assertIn('lensBtn?.closest<HTMLElement>(".tb-card")', body)
        self.assertIn("r.right - w", body)


class NarrowBarWraps(unittest.TestCase):
    def test_the_bar_is_its_own_query_container(self):
        self.assertIn("container: topbar / inline-size", CSS)

    def test_wraps_by_bar_width_and_view_card_takes_the_second_row(self):
        m = _at("@container topbar (max-width: 700px)")
        self.assertRegex(m, r"#tb-find \{[^}]*flex: 1 1 100%")
        # 넓은 창: 둘째 줄에서도 패널 폭 그대로(오른쪽 끝). 좁은 창(패널이 전폭 판): 줄 전폭.
        self.assertIn("#tb-viewctl { margin-left: auto; }", m)
        narrow = m[m.index("@media (max-width: 900px)"):]
        self.assertIn("#tb-viewctl { flex: 1 1 100%; width: auto; margin-left: 0; }", narrow)
        self.assertIn("#tb-viewpoints .seg .chip { flex: 1 1 0", narrow)

    def test_no_viewport_wrap_left_behind(self):
        self.assertNotIn("@media (max-width: 520px)", CSS)
        self.assertNotIn("@media (max-width: 720px)", CSS)

    def test_ceiling_tracker_still_follows_the_measured_bar(self):
        # 두 줄이 된 바 아래로 문서 열·인캔버스 크롬이 비켜서는 것은 이 측정기 몫이다.
        self.assertIn("function trackChromeTop()", TOPBAR)
        self.assertIn("new ResizeObserver(apply).observe(bar)", TOPBAR)


if __name__ == "__main__":
    unittest.main()
