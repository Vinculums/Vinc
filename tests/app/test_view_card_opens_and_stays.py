"""보기 카드는 렌즈 버튼 하나이고, 패널은 열린 동안 닫히지 않는다.

소스 잠금이다. 여기서는 패널 동작과 카드 상태가 옛 모양으로 돌아가지 않게 붙잡는다:

- `show()` 의 기본 닫기
- 바깥 누르기의 예외(렌즈 버튼 자리)
- 닫기 버튼이 없던 것
- 관점 세그가 카드에서 미끄러져 나오던 것(보기는 3D 하나라 고를 관점이 없다)

근거: [[decision:the-graph-whole-view-is-the-infra-layout-in-vinc-orbs-2026-10-28]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app" / "src" / "app.css").read_text(encoding="utf-8")
VIEW = (ROOT / "app" / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
COPY = (ROOT / "app" / "src" / "copy.ts").read_text(encoding="utf-8")
TOPBAR = (ROOT / "app" / "src" / "topbar.ts").read_text(encoding="utf-8")
INDEX = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
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
    def test_show_no_longer_closes_the_panel(self):
        body = _code(_fn(VIEW, "async function show("))
        self.assertNotIn("closePanel(", body)
        # 열린 채로 층이 바뀌면 새 옵션으로 다시 그린다.
        self.assertIn("openPanel({ focus: false })", body)

    def test_outside_press_keeps_the_lens_button(self):
        body = _fn(VIEW, "function openPanel(")
        # 버튼은 show() 마다 새로 그려지므로 노드가 아니라 그 자리(슬롯)를 예외로 쥔다.
        self.assertIn("void closePanel({ outside: true }),\n      legend);", body)
        self.assertIn("panel.contains(t)", COMPONENTS)


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


class TheCardIsTheLensButton(unittest.TestCase):
    def test_no_viewpoint_segment(self):
        self.assertNotIn("tb-viewpoints", INDEX)
        self.assertNotIn("tb-viewpoints", CSS)
        self.assertNotIn("viewpointsSlot", TOPBAR)
        self.assertNotIn("data-view=", VIEW)

    def test_the_card_does_not_grow(self):
        self.assertNotIn("setCardOpen", VIEW)
        self.assertNotIn("#tb-viewctl[data-open]", CSS)

    def test_the_button_names_no_viewpoint(self):
        self.assertNotIn("lens-vp", VIEW)
        self.assertNotIn("lens-vp", CSS)


if __name__ == "__main__":
    unittest.main()
