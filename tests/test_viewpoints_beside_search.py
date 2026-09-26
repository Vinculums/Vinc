"""관점 세그는 검색 바로 오른쪽, 렌즈 버튼은 지도 오른쪽 위(2026-09-26).

소스 잠금이다. 배치 자체는 브라우저 실측으로 확인했고(1440 · 720 · 390), 여기서는
그 배치가 조용히 옛 자리로 돌아가지 않게 구조만 붙잡는다:

- 세그가 문서 트리 안에 있으면 트리를 접을 때 관점 손잡이도 사라진다.
- 렌즈가 상단 바에 남으면 세그와 자리를 다투어 검색이 두 번 줄어든다.
- 좁은 창에서 줄이 갈리지 않으면 입력이 66px 까지 눌린다(창 520 실측).

근거: [[decision:viewpoints-sit-beside-search-and-the-lens-floats-on-the-view-2026-09-26]]
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
    m = re.search(re.escape(selector) + r" \{[^}]*\}", CSS)
    assert m, f"{selector} 규칙이 없다"
    return m.group(0)


def _media(query: str) -> str:
    """`@media (query) { ... }` 의 본문(중괄호 짝을 센다)."""
    a = CSS.index(f"@media ({query})")
    i = CSS.index("{", a)
    depth = 0
    for j in range(i, len(CSS)):
        if CSS[j] == "{":
            depth += 1
        elif CSS[j] == "}":
            depth -= 1
            if depth == 0:
                return CSS[i + 1:j]
    raise AssertionError(f"@media ({query}) 가 닫히지 않는다")


class ViewpointsInTheBar(unittest.TestCase):
    def test_segment_sits_in_the_top_bar_after_search(self):
        header = _block(HTML, '<header id="topbar">', "</header>")
        self.assertIn('id="tb-viewpoints"', header)
        self.assertLess(header.index('id="tb-search"'), header.index('id="tb-viewpoints"'))

    def test_document_tree_no_longer_holds_the_segment(self):
        lnb = _block(HTML, '<aside id="lnb"', "</aside>")
        self.assertNotIn("tb-viewpoints", lnb)

    def test_segment_keeps_its_own_width_and_search_takes_the_rest(self):
        rule = _rule("#tb-viewpoints")
        self.assertIn("flex: 0 0 auto", rule)


class LensFloatsOnTheView(unittest.TestCase):
    def test_top_bar_has_no_lens_slot(self):
        self.assertNotIn('id="tb-lens"', HTML)
        self.assertNotIn("lensSlot", TOPBAR)
        self.assertNotIn("lensSlot", VIEW)

    def test_lens_button_renders_into_the_view_tools_corner(self):
        self.assertIn("const legend = tools;", VIEW)
        self.assertIn('<div id="view-tools"></div>', VIEW)

    def test_lens_corner_lines_up_with_the_bar_inner_edge(self):
        rule = _rule("#view-tools")
        self.assertIn("var(--shell-gap)", rule)
        self.assertIn("var(--space-3", rule)

    def test_panel_flips_to_the_button_right_edge(self):
        body = _block(VIEW, "function positionPanel()", "function openPanel()")
        self.assertIn("r.right - w", body)


class NarrowWindowWraps(unittest.TestCase):
    def test_bar_wraps_at_720_and_segment_takes_the_second_row(self):
        m = _media("max-width: 720px")
        self.assertIn("flex-wrap: wrap", m)
        self.assertIn("height: auto", m)
        self.assertRegex(m, r"#tb-search \{[^}]*flex: 1 1 100%")
        self.assertRegex(m, r"#tb-viewpoints \{[^}]*flex: 1 1 100%")

    def test_old_520_wrap_is_gone(self):
        self.assertNotIn("@media (max-width: 520px)", CSS)

    def test_ceiling_tracker_still_follows_the_measured_bar(self):
        # 두 줄이 된 바 아래로 렌즈 버튼·문서 열이 비켜서는 것은 이 측정기 몫이다.
        self.assertIn("function trackChromeTop()", TOPBAR)
        self.assertIn("new ResizeObserver(apply).observe(bar)", TOPBAR)


if __name__ == "__main__":
    unittest.main()
