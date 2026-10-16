# -*- coding: utf-8 -*-
"""노드 발췌의 순수 함수(:mod:`vinc_rag.excerpt`) — 플래그 해석, 순위 복원, 거리 정렬, 창 위치."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_rag import excerpt as x  # noqa: E402


class Flags(unittest.TestCase):
    def test_모르는_값과_빈_값은_꺼짐이다(self):
        for raw in ("", "on", "QUERYX", "1"):
            with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": raw,
                                              "VINC_EXCERPT_WINDOW": raw}):
                self.assertEqual(x.order_mode(), "")
                self.assertEqual(x.window_mode(), "")

    def test_대소문자와_공백은_무시한다(self):
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": " Query ",
                                          "VINC_EXCERPT_WINDOW": "NODE"}):
            self.assertEqual(x.order_mode(), "query")
            self.assertEqual(x.window_mode(), "node")


class Order(unittest.TestCase):
    ROWS = [{"cid": "a", "method": "fts"}, {"cid": "b", "method": "fts"},
            {"cid": "c", "method": "fts"}]

    def test_순위_목록대로_되돌린다(self):
        got = x.by_rank(self.ROWS, ["c", "a", "b"])
        self.assertEqual([r["cid"] for r in got], ["c", "a", "b"])

    def test_목록에_없는_행은_원래_순서로_뒤에_온다(self):
        got = x.by_rank(self.ROWS, ["b"])
        self.assertEqual([r["cid"] for r in got], ["b", "a", "c"])

    def test_거리가_가까운_청크가_먼저다(self):
        got, note = x.by_distance(self.ROWS, {"a": 0.4, "b": 0.1, "c": 0.3})
        self.assertEqual(note, "")
        self.assertEqual([r["cid"] for r in got], ["b", "c", "a"])

    def test_링크_방법_등급이_거리보다_먼저다(self):
        rows = [{"cid": "s", "method": "source"}, {"cid": "t", "method": "title"}]
        got, _ = x.by_distance(rows, {"s": 0.01, "t": 0.9})
        self.assertEqual([r["cid"] for r in got], ["t", "s"])

    def test_거리를_못_잰_청크가_있으면_순서를_바꾸지_않고_사유를_낸다(self):
        got, note = x.by_distance(self.ROWS, {"a": 0.4, "c": 0.3})
        self.assertEqual([r["cid"] for r in got], ["a", "b", "c"])
        self.assertTrue(note.startswith("missing_vectors: 1 of 3"), note)


class Window(unittest.TestCase):
    def test_노드_문장이_뒤에_있으면_그_자리로_간다(self):
        text = ("어제 시장은 크게 흔들렸고 외국인은 팔았다. " * 20
                + "현금 비중은 평소 30% 정도 들고 있는 게 맞다. " + "마무리 문단이다. " * 5)
        toks = x.node_tokens("현금 비중은 평소 30% 정도 들고 있는 게 맞다")
        got = x.focus(text, toks)
        self.assertTrue(got.startswith("…"), got)
        self.assertIn("현금 비중은 평소 30%", got)
        self.assertLessEqual(len(got), x.SNIPPET_CHARS + 1)

    def test_맞는_토큰이_없으면_맨_앞_240자다(self):
        text = "가나다라마바사 " * 60
        self.assertEqual(x.focus(text, x.node_tokens("전혀 다른 말")),
                         " ".join(text.split())[:x.SNIPPET_CHARS])


if __name__ == "__main__":
    unittest.main()
