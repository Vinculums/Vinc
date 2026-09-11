# -*- coding: utf-8 -*-
"""PR 제목 계약의 회귀 시험.

스쿼시 머지에서 PR 제목이 곧 main 의 커밋 제목이므로, 이 게이트가 실제로 무는지가
`git log` 의 품질이다. 통과하는 것만 재면 게이트를 지워도 초록이라, 여기서는 **떨어져야
하는 것이 떨어지는지**를 함께 잠근다.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import gate_pr_title as G                      # noqa: E402

GOOD = [
    "fix(share): unchecked documents stay behind",
    "feat(search): say when the meaning arm is missing",
    "ci(release): pin the interpreter the container can run",
    "fix: the last write is written down on a schedule",
    "refactor(app)!: drop the packs route",
    "chore(graph): refresh the citation manifest",
    "fix(mcp): MCP handshake reports the product version",
]

BAD = {
    "제목이 한국어다": "fix(share): 체크를 끈 문서는 나가지 않는다",
    "접두가 없다": "unchecked documents stay behind",
    "모르는 타입": "improve(share): unchecked documents stay behind",
    "콜론 뒤 공백이 없다": "fix(share):unchecked documents stay behind",
    "em-dash": "fix(share): unchecked documents stay behind — measured",
    "en-dash": "fix(share): unchecked documents stay behind – measured",
    "마침표로 끝난다": "fix(share): unchecked documents stay behind.",
    "subject 가 대문자로 시작한다": "fix(share): Unchecked documents stay behind",
    "빈 제목": "   ",
    "너무 길다": "fix(share): " + "x" * 100,
    "대문자 타입": "Fix(share): unchecked documents stay behind",
}


class Contract(unittest.TestCase):
    def test_통과해야_하는_제목들(self):
        for t in GOOD:
            with self.subTest(title=t):
                self.assertEqual(G.check(t), [], t)

    def test_떨어져야_하는_제목들(self):
        for why, t in BAD.items():
            with self.subTest(why=why):
                self.assertNotEqual(G.check(t), [], f"{why}: {t!r} 가 통과했다")

    def test_약어로_시작하는_subject_는_통과한다(self):
        """대문자 규칙이 약어를 잡으면 사람이 게이트를 우회하려고 제목을 망친다."""
        self.assertEqual(G.check("fix(mcp): MCP announces the product version"), [])
        self.assertEqual(G.check("ci: CI runs the whole suite"), [])

    def test_이_리포의_타입_목록이_실제_이력을_담는다(self):
        """실측 상위 열둘이 목록에 있어야 한다 — 빠지면 정상 PR 이 떨어진다."""
        for t in ("feat", "fix", "ci", "chore", "design", "docs", "release",
                  "test", "infra", "agents", "refactor", "security"):
            self.assertIn(t, G.TYPES)


class Cli(unittest.TestCase):
    def test_인자와_환경변수_양쪽에서_읽는다(self):
        from unittest import mock
        self.assertEqual(G.main(["x", GOOD[0]]), 0)
        with mock.patch.dict(os.environ, {"PR_TITLE": GOOD[0]}):
            self.assertEqual(G.main(["x"]), 0)
        with mock.patch.dict(os.environ, {"PR_TITLE": BAD["제목이 한국어다"]}):
            self.assertEqual(G.main(["x"]), 1)

    def test_제목을_못_받으면_2_다(self):
        """0 으로 떨어지면 제목을 못 넘긴 워크플로가 조용히 통과한다."""
        from unittest import mock
        with mock.patch.dict(os.environ, {"PR_TITLE": ""}):
            self.assertEqual(G.main(["x"]), 2)


if __name__ == "__main__":
    unittest.main()
