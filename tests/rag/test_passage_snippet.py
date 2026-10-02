# -*- coding: utf-8 -*-
"""발췌는 질의어가 모인 자리를 보여 준다(P2).

근거: [[vinc.research.korean-retrieval-2026-10-01 §7]] — 청크 첫 240자는 근거 구간을 18.6% 만
보여 줬다. 지키는 것:
  · 1,200자 지점의 맞는 문장이 발췌에 들고, 발췌는 `…` 로 시작한다.
  · 맞는 게 없으면 오늘 발췌와 바이트까지 같다(처음 240자, 접두 없음).
  · 짧은 청크는 그대로다. `truncated` 의 뜻(더 있는가)은 바뀌지 않는다.
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store.lexical import _tokens, find_tokens, lex_tokens  # noqa: E402
from vinc_rag.retrieval import SNIPPET_CHARS, _summary_row  # noqa: E402

_FILL = "오늘은 별다른 이야기 없이 지나가는 날이라 시장도 조용합니다. " * 40
_TARGET = "12월에 베팅하는 건 하수라서 실제 승부는 10월과 11월에 납니다."


def _row(text):
    return {"id": "d#00000", "text": text, "score": 1.0}


class SnippetTests(unittest.TestCase):
    def test_맞는_문장이_멀리_있어도_발췌에_든다(self):
        text = _FILL[:1200] + _TARGET + " " + _FILL[:600]
        self.assertGreater(text.index(_TARGET), 1100)
        out = _summary_row(_row(text), lex_tokens("하수 베팅 승부"))
        self.assertTrue(out["snippet"].startswith("…"), out["snippet"][:40])
        self.assertIn("승부는", out["snippet"])
        self.assertIn("하수라서", out["snippet"])
        self.assertLessEqual(len(out["snippet"]), SNIPPET_CHARS + 1)
        self.assertTrue(out["truncated"])

    def test_맞는_게_없으면_오늘_발췌_그대로다(self):
        text = _FILL[:1200] + _TARGET
        flat = " ".join(text.split())
        for q in (None, set(), lex_tokens("quarterly rebalancing")):
            out = _summary_row(_row(text), q)
            self.assertEqual(out["snippet"], flat[:SNIPPET_CHARS])

    def test_짧은_청크는_그대로다(self):
        out = _summary_row(_row(_TARGET), lex_tokens("승부"))
        self.assertEqual(out["snippet"], _TARGET)
        self.assertFalse(out["truncated"])

    def test_처음에_모였으면_접두가_없다(self):
        text = _TARGET + " " + _FILL
        out = _summary_row(_row(text), lex_tokens("하수 승부"))
        self.assertFalse(out["snippet"].startswith("…"))

    def test_찾은_토큰이_색인_토큰과_같다(self):
        # find_tokens 는 글을 토큰화하지 않고 찾는다. 결과가 lex_text 의 토큰과 갈라지면
        # 발췌가 색인이 맞춘 자리와 다른 곳을 보여 준다.
        for text in ("가나 승부는 Quarter끝 a-b 가", "승부 승부는, 하수라서 Q3 q3x 끝", "İstanbul 승부",
                     _FILL[:300] + _TARGET):
            toks = set(_tokens(text))
            got = find_tokens(text, toks)
            for pos, tok in got:
                seg = text[pos:pos + len(tok)]
                self.assertIn(tok, (seg, seg.lower()), "오프셋이 원문 자리와 어긋났다")
            if len(text.lower()) == len(text):
                self.assertEqual(sorted(t for _, t in got), sorted(_tokens(text)), text)

    def test_20행_페이지가_빠르다(self):
        text = _FILL[:1200] + _TARGET + " " + _FILL[:600]
        q = lex_tokens("하수 베팅 승부 시장")
        t = time.perf_counter()
        for _ in range(20):
            _summary_row(_row(text), q)
        self.assertLess(time.perf_counter() - t, 0.05, "20행 발췌가 50ms 를 넘는다")


if __name__ == "__main__":
    unittest.main()
