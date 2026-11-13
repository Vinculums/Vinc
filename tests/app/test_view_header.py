"""뷰 머리: 내용 카드의 윗줄에 공간 전환 · 경로 · 검색 · 그래프의 시간 · 범례 · 문서 | 그래프, 맨 끝에 읽기 설정.

소스 잠금이다. 배치 자체는 헤드리스 브라우저 실측으로 확인했고(창 1440 · 1024 · 800 · 600 ·
390, 문서 · 문서 면 · 그래프), 여기서는 그 배치가 조용히 옛 자리로 돌아가지 않게 구조만 붙잡는다:

- 머리가 다시 떠 있으면 본문이 그 밑으로 지나가고, 아래 것들이 또 비켜설 값을 재야 한다.
- 머리와 본문이 칼럼을 따로 가지면 경로와 제목의 세로선이 갈라진다.
- 전환이 표면마다 다른 자리에 서면 누른 자리에서 손이 빗나간다.
- 그래프의 시간 · 범례는 캔버스 안이 아니라 머리 뒤 묶음, 전환 바로 왼쪽이다(전환이 어느 면에서나 같은 자리).
  문서 면의 읽기 설정은 맨 끝이다.
- 공간 전환은 머리 맨 앞 칸이다(계정 그래프에서만 채워지고, 비면 자리를 차지하지 않는다).

근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]]
근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
"""
from __future__ import annotations

import re
import unittest

from _src import css_block, css_rule, cut, read

HTML = read("index.html")
CSS = read("app.css")
VIEW = read("view")
TOPBAR = read("topbar")
HOME = read("home")
DOCOPTS = read("doc-options")


HEADER = cut(HTML, '<header id="topbar">', "</header>")


class HeaderOrder(unittest.TestCase):
    def test_space_then_lead_then_search_then_tools_then_switch_then_options(self):
        space = HEADER.index('<div class="tb-space" id="tb-space"></div>')
        lead = HEADER.index('id="tb-lead"')
        find = HEADER.index('id="tb-find"')
        tools = HEADER.index('<div id="tb-graph-tools"></div>')
        switch = HEADER.index('id="tb-switch"')
        docopts = HEADER.index('<div id="tb-docopts"></div>')
        self.assertLess(space, lead)
        self.assertLess(lead, find)
        self.assertLess(find, tools)
        self.assertLess(tools, switch)
        self.assertLess(switch, docopts)
        trail = cut(HEADER, '<div class="tb-trail">', '<div id="tb-right">')
        self.assertIn('id="tb-graph-tools"', trail)
        self.assertIn('id="tb-docopts"', trail)
        self.assertNotIn("tb-lens", HTML)

    def test_the_switch_has_two_segments(self):
        sw = cut(HEADER, 'id="tb-switch"', "</div>")
        self.assertIn('id="tb-to-docs"', sw)
        self.assertIn('id="tb-to-graph"', sw)

    def test_no_view_card_floats_in_the_canvas(self):
        self.assertNotIn("tb-viewctl", HTML)
        self.assertNotIn("tb-viewctl", CSS)
        self.assertNotIn(".tb-card", VIEW)
        self.assertNotIn("tb-viewpoints", HTML)

    def test_document_tree_does_not_hold_the_segment(self):
        lnb = cut(HTML, '<aside id="lnb"', "</aside>")
        self.assertNotIn("tb-viewpoints", lnb)


class HeaderIsTheTopOfTheCard(unittest.TestCase):
    def test_header_and_surface_are_two_rows_of_one_card(self):
        self.assertIn("row-gap: 0", css_rule(CSS, "#shell"))
        self.assertIn("grid-row: 1", css_rule(CSS, "#topbar"))
        self.assertIn("grid-row: 2", css_rule(CSS, "#surface"))
        self.assertIn("grid-row: 2", css_rule(CSS, "#docpane-slot"))
        self.assertIn("border-top: 0", css_rule(CSS, "#surface"))

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
        self.assertIn("top: var(--shell-gap); bottom: var(--shell-gap); right: var(--shell-gap);", css_rule(CSS, "#docpane"))


class OneColumn(unittest.TestCase):
    def test_header_lead_follows_the_column_and_trail_the_card_edge(self):
        # 본문은 칼럼이다. 머리의 앞자리는 그 칼럼의 첫 글자선에(좁으면 본문 여백), 뒤 묶음은 카드 안쪽 끝에 선다.
        # 그래프는 본문이 카드 전체를 써서 앞자리도 카드 안쪽 끝이다
        self.assertIn("max-width: calc(var(--page-col) + var(--page-inset) * 2)", css_rule(CSS, ".home"))
        col = css_rule(CSS, ".tb-col")
        self.assertIn("calc((100cqi - var(--page-col) - var(--page-inset) * 2) / 2 + var(--page-inset))", col)
        self.assertIn("max(var(--page-inset),", col)
        self.assertIn("padding: 0 var(--head-inset)", col)
        self.assertIn("padding-left: var(--head-inset)", css_rule(CSS, '#shell[data-surface="view"] .tb-col'))

    def test_the_header_leaves_room_for_the_body_scrollbar(self):
        bars = re.findall(r"(?m)^#topbar \{[^}]*\}", CSS)
        self.assertTrue(any("var(--sb-w, 0px)" in b for b in bars))
        self.assertIn("function trackGutter()", TOPBAR)

    def test_search_is_as_wide_as_the_reading_panel(self):
        self.assertIn("width: var(--aside-w)", css_rule(CSS, ".tb-find-open"))
        self.assertIn("var(--aside-w)", css_rule(CSS, ".home-read-grid"))

    def test_the_breadcrumb_lives_in_the_header(self):
        self.assertIn("setTopbarLead(", HOME)
        self.assertIn("export function setTopbarLead(", TOPBAR)


class HeaderEnd(unittest.TestCase):
    def test_empty_slots_take_no_room(self):
        self.assertIn("#tb-graph-tools:empty, #tb-docopts:empty, #tb-docopts[hidden] { display: none; }", CSS)
        self.assertIn("#tb-space:empty { display: none; }", CSS)

    def test_graph_tools_stand_in_the_trail_not_the_lead(self):
        # 시간 · 범례는 나침반이 넘기고 머리가 전환 왼쪽 자리에 옮겨 붙인다. 앞자리(경로)에는 붙지 않는다.
        self.assertIn("setTopbarTools(cmp.tools)", VIEW)
        self.assertIn('const slot = $("tb-graph-tools");', TOPBAR)
        self.assertNotIn("setTopbarLeadTools", TOPBAR + VIEW)
        self.assertNotIn("lensSlot", TOPBAR + VIEW)

    def test_the_options_panel_keeps_its_own_width(self):
        self.assertIn("width: var(--lens-panel-w)", css_rule(CSS, "#doc-opts-panel"))

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
        self.assertIn("right: calc(var(--rail-w, 0px) + var(--space-6, 1.5rem));", css_rule(CSS, "#view-tools"))


class NarrowHeaderFolds(unittest.TestCase):
    def test_the_header_is_its_own_query_container(self):
        self.assertIn("container: topbar / inline-size", CSS)

    def test_switch_names_fold_before_search_does(self):
        mid = css_block(CSS, "@container topbar (max-width: 760px)")
        self.assertIn(".tb-seg-name { display: none; }", mid)
        self.assertRegex(mid, r"\.tb-find-open \{[^}]*width: auto")
        narrow = css_block(CSS, "@container topbar (max-width: 480px)")
        self.assertIn(".tb-find-label { display: none; }", narrow)

    def test_no_viewport_wrap_left_behind(self):
        self.assertNotIn("@media (max-width: 520px)", CSS)
        self.assertNotIn("@media (max-width: 720px)", CSS)


if __name__ == "__main__":
    unittest.main()
