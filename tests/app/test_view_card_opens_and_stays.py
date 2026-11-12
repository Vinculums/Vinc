"""보기 카드는 렌즈 버튼 하나이고, 패널은 열린 동안 닫히지 않는다.

소스 잠금이다. 여기서는 패널 동작과 카드 상태가 옛 모양으로 돌아가지 않게 붙잡는다:

- `show()` 의 기본 닫기
- 바깥 누르기의 예외(렌즈 버튼 자리)
- 닫기 버튼이 없던 것
- 관점 세그가 카드에서 미끄러져 나오던 것(보기는 2D 나침반 렌즈 하나라 고를 관점이 없다)

근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
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


class TheLensPanelIsGone(unittest.TestCase):
    """그래프의 보기 옵션 · 범례 패널은 3D 렌더러와 함께 걷었다. 손잡이는 나침반 머리의 시간 · 범례 · 전체다."""

    def test_no_lens_panel_in_the_view(self):
        code = _code(VIEW)
        for gone in ("view-lens-panel", "function openPanel(", "function closePanel(", "viewPanel"):
            self.assertNotIn(gone, code, gone)

    def test_escape_still_folds_what_is_open(self):
        # Esc 는 가장 나중에 뜬 것부터 접는다: 카드, 그다음 상세 열, 그다음 지나온 층
        body = _code(_fn(VIEW, "const onKey = (e: KeyboardEvent) =>"))
        self.assertIn("closeFloatPanel()", body)
        self.assertIn("closeRail()", body)
        self.assertIn("trail.pop()", body)


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
