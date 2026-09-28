"""보기 카드가 렌즈 버튼으로 열리고, 열린 동안은 닫히지 않는다(2026-09-28).

소스 잠금이다. 동작 자체는 브라우저에서 실측했다(창 1024 · 800 · 390): 관점 전환 · 옵션 ·
탭 · 기간 · 날짜 선택을 거쳐도 패널이 열려 있고, 닫기 버튼 · 렌즈 버튼 · 바깥 누르기 ·
Esc 넷으로만 닫히며, 열린 카드와 패널의 양 끝이 같은 세로선(680 · 1008)에 서고 검색
입력은 한 픽셀도 움직이지 않았다. 여기서는 그 동작을 만든 자리가 조용히 옛 모양으로
돌아가지 않게 붙잡는다:

- 관점을 바꾸면 패널을 닫던 두 줄(`applyViewpoint`)과 `show()` 의 기본 닫기
- 바깥 누르기의 예외가 렌즈 버튼 하나뿐이라 관점 칩을 누르는 것도 "바깥"이던 것
- 닫기 버튼이 없던 것
- 닫힌 카드에서 관점 세그가 보이던 것(D65 의 상시 노출)

근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §1
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
VIEW = (ROOT / "app" / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
COPY = (ROOT / "app" / "src" / "copy.ts").read_text(encoding="utf-8")
TOPBAR = (ROOT / "app" / "src" / "topbar.ts").read_text(encoding="utf-8")
COMPONENTS = (ROOT / "app" / "src" / "components.ts").read_text(encoding="utf-8")


def _fn(src: str, head: str) -> str:
    """`head` 로 시작하는 함수 본문(줄 끝에 선 첫 여는 중괄호부터 짝을 센다)."""
    a = src.index(head)
    i = re.search(r"\{\s*\n", src[a:]).start() + a
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[a:j + 1]
    raise AssertionError(f"{head} 가 닫히지 않는다")


def _code(body: str) -> str:
    """주석을 걷어 낸 코드 — 주석이 옛 동작을 설명하느라 그 이름을 적어도 잡지 않게."""
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in body.splitlines())


class NothingInsideClosesThePanel(unittest.TestCase):
    def test_choosing_a_viewpoint_keeps_the_panel(self):
        body = _code(_fn(VIEW, "async function applyViewpoint("))
        self.assertNotIn("closePanel(", body)

    def test_show_no_longer_closes_the_panel(self):
        body = _code(_fn(VIEW, "async function show("))
        self.assertNotIn("closePanel(", body)
        # 열린 채로 층·관점이 바뀌면 새 옵션으로 다시 그린다.
        self.assertIn("openPanel({ focus: false })", body)

    def test_outside_press_keeps_the_whole_card_and_the_picker(self):
        body = _fn(VIEW, "function openPanel(")
        self.assertIn("() => viewCard() ?? lensBtn, viewpointsBar", body)
        self.assertIn('document.getElementById("date-range-pop")', body)
        # 함수로 준 예외는 누를 때마다 다시 찾는다(버튼은 show() 마다 새로 그려진다).
        self.assertIn('typeof k === "function" ? k() : k', COMPONENTS)


class TheFourWaysOut(unittest.TestCase):
    def test_close_button_in_the_panel_head(self):
        self.assertIn("data-lensclose", VIEW)
        self.assertIn('querySelector<HTMLElement>("[data-lensclose]")', VIEW)
        self.assertIn('close: "Close",', COPY)
        self.assertIn('close: "닫기",', COPY)

    def test_lens_button_toggles_and_reopens_mid_close(self):
        self.assertIn("if (viewPanel.hidden || panelClosing) openPanel(); else void closePanel();", VIEW)

    def test_escape_still_closes(self):
        self.assertIn("if (!viewPanel.hidden) { void closePanel(); return; }", VIEW)

    def test_an_outside_close_does_not_take_focus(self):
        body = _fn(VIEW, "async function closePanel(")
        self.assertIn("if (!opts?.outside", body)
        self.assertIn("void closePanel({ outside: true })", VIEW)


class TwoStatesOfTheCard(unittest.TestCase):
    def test_closed_card_hides_the_segment(self):
        self.assertIn("#tb-viewctl:not([data-open]) > #tb-viewpoints { display: none; }", CSS)

    def test_open_card_overlays_instead_of_pushing(self):
        m = re.search(r"(?m)^#tb-viewctl\[data-open\] \{[^}]*\}", CSS)
        self.assertTrue(m)
        # 바깥 폭 = 닫힌 폭(음수 왼쪽 여백 + 패널 폭) — 검색 입력이 흔들리지 않는다(Q1.1).
        self.assertIn(
            "margin-left: calc(var(--vc-closed-w, var(--lens-panel-w)) - var(--lens-panel-w))",
            m.group(0))

    def test_closed_button_names_the_viewpoint(self):
        self.assertIn('class="lens-vp">${esc(vp)}</span>', VIEW)
        self.assertIn("#tb-viewctl:not([data-open]) .lens-vp {", CSS)

    def test_motion_is_short_and_honours_reduced_motion(self):
        body = _fn(VIEW, "async function setCardOpen(")
        self.assertIn("duration: open ? 200 : 160", body)
        self.assertIn("prefers-reduced-motion: reduce", body)

    def test_narrow_window_moves_the_segment_into_the_sheet_head(self):
        self.assertIn('const head = `<div class="lens-vprow">${segHTML(shown)}</div>`;', VIEW)
        a = CSS.index("#tb-viewctl[data-open] { width: auto; margin-left: 0; }")
        block = CSS[a:a + 900]
        self.assertIn("#tb-viewctl > #tb-viewpoints { display: none; }", block)
        self.assertIn(".lens-vprow { display: block;", block)

    def test_leaving_the_view_folds_the_card(self):
        self.assertIn('$("tb-viewctl")?.removeAttribute("data-open")', TOPBAR)


if __name__ == "__main__":
    unittest.main()
