"""뷰 머리: 내용 카드의 윗줄에 경로 · 검색 · 문서 | 그래프, 맨 끝에 보기 설정과 범례.

소스 잠금이다. 배치 자체는 헤드리스 브라우저 실측으로 확인했고(창 1440 · 1024 · 800 · 600 ·
390, 문서 · 문서 면 · 그래프), 여기서는 그 배치가 조용히 옛 자리로 돌아가지 않게 구조만 붙잡는다:

- 머리가 다시 떠 있으면 본문이 그 밑으로 지나가고, 아래 것들이 또 비켜설 값을 재야 한다.
- 머리와 본문이 칼럼을 따로 가지면 경로와 제목의 세로선이 갈라진다.
- 전환이 표면마다 다른 자리에 서면 누른 자리에서 손이 빗나간다.
- 보기 설정과 범례는 캔버스 안이 아니라 머리 맨 끝 한 자리다. 그래프는 렌즈, 문서 면은 읽기 설정.

근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]]
근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
VIEW = (ROOT / "app" / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
TOPBAR = (ROOT / "app" / "src" / "topbar.ts").read_text(encoding="utf-8")
HOME = (ROOT / "app" / "src" / "surfaces" / "home.ts").read_text(encoding="utf-8")
DOCOPTS = (ROOT / "app" / "src" / "doc-options.ts").read_text(encoding="utf-8")


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


class HeaderOrder(unittest.TestCase):
    def test_lead_then_search_then_switch_then_options(self):
        lead = HEADER.index('id="tb-lead"')
        find = HEADER.index('id="tb-find"')
        switch = HEADER.index('id="tb-switch"')
        lens = HEADER.index('<div id="tb-lens"></div>')
        docopts = HEADER.index('<div id="tb-docopts"></div>')
        self.assertLess(lead, find)
        self.assertLess(find, switch)
        self.assertLess(switch, lens)
        self.assertLess(lens, docopts)
        trail = _block(HEADER, '<div class="tb-trail">', '<div id="tb-right">')
        self.assertIn('id="tb-lens"', trail)
        self.assertIn('id="tb-docopts"', trail)

    def test_the_switch_has_two_segments(self):
        sw = _block(HEADER, 'id="tb-switch"', "</div>")
        self.assertIn('id="tb-to-docs"', sw)
        self.assertIn('id="tb-to-graph"', sw)

    def test_no_view_card_floats_in_the_canvas(self):
        self.assertNotIn("tb-viewctl", HTML)
        self.assertNotIn("tb-viewctl", CSS)
        self.assertNotIn(".tb-card", VIEW)
        self.assertNotIn("tb-viewpoints", HTML)

    def test_document_tree_does_not_hold_the_segment(self):
        lnb = _block(HTML, '<aside id="lnb"', "</aside>")
        self.assertNotIn("tb-viewpoints", lnb)


class HeaderIsTheTopOfTheCard(unittest.TestCase):
    def test_header_and_surface_are_two_rows_of_one_card(self):
        self.assertIn("row-gap: 0", _rule("#shell"))
        self.assertIn("grid-row: 1", _rule("#topbar"))
        self.assertIn("grid-row: 2", _rule("#surface"))
        self.assertIn("grid-row: 2", _rule("#docpane-slot"))
        self.assertIn("border-top: 0", _rule("#surface"))

    def test_header_height_is_the_lnb_top_row_formula(self):
        # 테두리 1 + 8 + 컨트롤 36 + 8 + 아래 선 1 — LNB 맨 위 줄과 같은 식.
        self.assertIn("--head-h: calc(var(--ctl-h) + var(--space-2, .5rem) * 2 + 2px)", CSS)
        bars = re.findall(r"(?m)^#topbar \{[^}]*\}", CSS)
        self.assertTrue(any("height: var(--head-h)" in b for b in bars))
        self.assertTrue(any("background: hsl(var(--card))" in b for b in bars))
        self.assertTrue(all("box-shadow" not in b for b in bars), "머리가 다시 떠 있다")

    def test_nothing_steps_around_the_header_any_more(self):
        self.assertIn("--chrome-top: 0px;", CSS)
        self.assertNotIn("function trackChromeTop()", TOPBAR)
        self.assertIn("top: var(--shell-gap); bottom: var(--shell-gap); right: var(--shell-gap);", _rule("#docpane"))


class OneColumn(unittest.TestCase):
    def test_header_and_page_share_the_column(self):
        col = "max-width: calc(var(--page-col) + var(--page-inset) * 2)"
        self.assertIn(col, _rule(".tb-col"))
        self.assertIn(col, _rule(".home"))

    def test_the_header_leaves_room_for_the_body_scrollbar(self):
        bars = re.findall(r"(?m)^#topbar \{[^}]*\}", CSS)
        self.assertTrue(any("var(--sb-w, 0px)" in b for b in bars))
        self.assertIn("function trackGutter()", TOPBAR)

    def test_search_is_as_wide_as_the_reading_panel(self):
        self.assertIn("width: var(--aside-w)", _rule(".tb-find-open"))
        self.assertIn("var(--aside-w)", _rule(".home-read-grid"))

    def test_the_breadcrumb_lives_in_the_header(self):
        self.assertIn("setTopbarLead(", HOME)
        self.assertIn("export function setTopbarLead(", TOPBAR)


class HeaderEnd(unittest.TestCase):
    def test_empty_slots_take_no_room(self):
        self.assertIn("#tb-lens:empty, #tb-docopts:empty, #tb-docopts[hidden] { display: none; }", CSS)

    def test_lens_renders_into_the_header_slot(self):
        self.assertIn('lensSlot() ?? fallbackSlot("view-legend")', VIEW)
        self.assertIn('return $("tb-lens")', TOPBAR)

    def test_the_lens_panel_keeps_its_own_width(self):
        self.assertIn("width: var(--lens-panel-w)", CSS[CSS.index("#view-lens-panel, #doc-opts-panel {"):])

    def test_panel_anchors_to_the_lens_button(self):
        body = _block(VIEW, "function positionPanel()", "function openPanel(")
        self.assertIn("const r = lensBtn?.getBoundingClientRect();", body)
        self.assertIn("const left = Math.min(r.right - w, rightEdge - w - m);", body)

    def test_documents_get_their_own_options(self):
        self.assertIn("mountDocOptions();", TOPBAR)
        self.assertIn('document.getElementById("tb-docopts")', DOCOPTS)
        # 그래프에서는 숨는다: 한 자리에 버튼이 둘 서지 않는다.
        self.assertIn("slot.hidden = !onDocs();", DOCOPTS)
        for k in ("text", "width", "lines"):
            self.assertIn(f'data-doc-{k}', CSS)

    def test_document_text_reads_the_reading_options(self):
        self.assertIn("font-size: var(--doc-fs, 1rem); line-height: var(--doc-lh, 1.65);", CSS)
        self.assertIn(".doc-render .cm-r-h1 { font-size: 1.5em; }", CSS)

    def test_view_tools_corner_is_untouched(self):
        self.assertIn("right: calc(var(--rail-w, 0px) + var(--space-6, 1.5rem));", _rule("#view-tools"))


class NarrowHeaderFolds(unittest.TestCase):
    def test_the_header_is_its_own_query_container(self):
        self.assertIn("container: topbar / inline-size", CSS)

    def test_switch_names_fold_before_search_does(self):
        mid = _at("@container topbar (max-width: 760px)")
        self.assertIn(".tb-seg-name { display: none; }", mid)
        self.assertRegex(mid, r"\.tb-find-open \{[^}]*width: auto")
        narrow = _at("@container topbar (max-width: 480px)")
        self.assertIn(".tb-find-label { display: none; }", narrow)

    def test_no_viewport_wrap_left_behind(self):
        self.assertNotIn("@media (max-width: 520px)", CSS)
        self.assertNotIn("@media (max-width: 720px)", CSS)


if __name__ == "__main__":
    unittest.main()
