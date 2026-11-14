"""뷰 머리의 배치 값: 머리와 본문이 한 카드의 두 줄이고, 한 칼럼을 나누고, 좁아지면 이름부터 접는다.

소스 잠금이다. 여기 남은 것은 CSS 값(격자 줄 · 높이 식 · 칼럼 계산 · 컨테이너 쿼리)뿐이라 mini-dom 으로는 잴 수 없다
(배치가 없다). 값은 헤드리스 브라우저 실측(창 1440 · 1024 · 800 · 600 · 390, 문서 · 문서 면 · 그래프)으로 정했고,
여기서는 그 값이 조용히 옛 자리로 돌아가지 않게 붙잡는다.

머리의 칸 순서와 자리 · 문서 | 그래프 전환의 두 단추 · 보기 카드와 관점 세그먼트가 없다는 것 · 앞자리와 그래프 도구
칸 채우기 · 본문 스크롤바 폭 비우기 · 읽기 설정 칸은 진짜 마크업(index.html)과 topbar.ts 를 돌리는
`app/tests/doc-options.test.mjs` 의 "뷰 머리" 절이, 문서 홈이 앞자리를 채우는 것은 `app/tests/home-list.test.mjs`
가 잰다. 그래프가 도구 칸에 나침반의 단추를 넘기는 한 줄(`renderView` 의 닫힌 함수 안)만 여기서 글자로 본다.

근거: [[decision:documents-and-graph-share-one-lnb-tree-2026-11-06]]
근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
"""
from __future__ import annotations

import re
import unittest

from _src import css_block, css_rule, read

CSS = read("app.css")
VIEW = read("view")


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

    def test_search_is_as_wide_as_the_reading_panel(self):
        self.assertIn("width: var(--aside-w)", css_rule(CSS, ".tb-find-open"))
        self.assertIn("var(--aside-w)", css_rule(CSS, ".home-read-grid"))


class HeaderEnd(unittest.TestCase):
    def test_empty_slots_take_no_room(self):
        self.assertIn("#tb-graph-tools:empty, #tb-docopts:empty, #tb-docopts[hidden] { display: none; }", CSS)
        self.assertIn("#tb-space:empty { display: none; }", CSS)

    def test_the_graph_hands_the_compass_buttons_to_the_header(self):
        # 시간 · 범례는 나침반이 넘기고 머리가 전환 왼쪽 자리에 옮겨 붙인다(topbar.ts 쪽은 node 테스트가 잰다).
        self.assertIn("topbarTools: cmp.tools", VIEW)

    def test_the_options_panel_keeps_its_own_width(self):
        self.assertIn("width: var(--lens-panel-w)", css_rule(CSS, "#doc-opts-panel"))

    def test_reading_choices_have_their_css_hooks(self):
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
