# -*- coding: utf-8 -*-
"""노드 발췌의 창과 폴백 순서(:mod:`vinc_rag.excerpt`) — 순수 함수만."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_rag import excerpt as x  # noqa: E402

LEAD = "어제 시장은 크게 흔들렸고 외국인은 팔았다. " * 20
SENTENCE = "현금 비중은 평소 30% 정도 들고 있는 게 맞다."


class WindowMode(unittest.TestCase):
    def test_기본은_노드_창이고_head_만_비상구다(self):
        for raw, want in (("", "node"), ("node", "node"), ("HEAD", "head"), (" head ", "head"),
                          ("off", "node"), ("1", "node")):
            with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": raw}):
                self.assertEqual(x.window_mode(), want, raw)


class Excerpt(unittest.TestCase):
    def test_노드_문장이_뒤에_있으면_그_자리로_간다(self):
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": ""}):
            got = x.excerpt(LEAD + SENTENCE + " 마무리.", "현금 비중은 평소 30% 정도", SENTENCE)
        self.assertTrue(got.startswith("…"), got)
        self.assertIn("30% 정도 들고", got)
        self.assertLessEqual(len(got), x.SNIPPET_CHARS + 1)

    def test_head_비상구는_맨_앞_240자다(self):
        text = LEAD + SENTENCE
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "head"}):
            got = x.excerpt(text, "현금 비중은 평소 30% 정도", SENTENCE)
        self.assertEqual(got, " ".join(text.split())[:x.SNIPPET_CHARS])

    def test_맞는_토큰이_없으면_맨_앞_240자다(self):
        text = "가나다라마바사 " * 60
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": ""}):
            self.assertEqual(x.excerpt(text, "전혀 다른 말"),
                             " ".join(text.split())[:x.SNIPPET_CHARS])


class ByRank(unittest.TestCase):
    ROWS = [{"cid": "a"}, {"cid": "b"}, {"cid": "c"}]

    def test_순위_목록대로_되돌린다(self):
        self.assertEqual([r["cid"] for r in x.by_rank(self.ROWS, ["c", "a", "b"])],
                         ["c", "a", "b"])

    def test_목록에_없는_행은_원래_순서로_뒤에_온다(self):
        self.assertEqual([r["cid"] for r in x.by_rank(self.ROWS, ["b"])], ["b", "a", "c"])


if __name__ == "__main__":
    unittest.main()
