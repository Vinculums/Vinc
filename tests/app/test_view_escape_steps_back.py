"""그래프 표면의 Esc 는 가장 나중에 뜬 것부터 하나씩 접는다: 떠 있는 카드, 그다음 상세 열, 그다음 지나온 층.

소스 잠금이다. 이 핸들러는 `renderView` 의 닫힌 함수 안(`view.ts` 의 `onKey`)이라 mini-dom 에 올릴 수 없다.
핸들러를 별도 모듈로 떼어 내면 그 모듈을 그려 보는 테스트로 바꾼다.

보기 카드가 렌즈 버튼 하나이고 관점 세그먼트 · 보기 패널이 없다는 것은 소스 글자가 아니라 화면이 말한다:
머리 마크업은 `app/tests/doc-options.test.mjs` 의 "no view card, viewpoint segment or lens button" 이,
나침반 머리의 단추가 시간 · 범례 · 전체 셋뿐이라는 것은 `app/tests/view-compass-whole.test.mjs` 의
"the header gains one control" 이 잰다.

근거: [[decision:the-graph-is-one-2d-compass-lens-2026-11-06]]
"""
from __future__ import annotations

import re
import unittest

from _src import braced, read

VIEW = read("view")


def _code(body: str) -> str:
    """주석을 걷어 낸 코드 — 주석이 다른 동작을 설명하느라 그 이름을 적어도 잡지 않게."""
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in body.splitlines())


class EscapeFoldsWhatIsOpen(unittest.TestCase):
    def test_escape_folds_the_newest_first(self):
        body = _code(braced(VIEW, "const onKey = (e: KeyboardEvent) =>"))
        card, rail, level = body.find("closeFloatPanel()"), body.find("closeRail()"), body.find("trail.pop()")
        self.assertTrue(0 <= card < rail < level, (card, rail, level))


if __name__ == "__main__":
    unittest.main()
