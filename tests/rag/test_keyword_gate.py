# -*- coding: utf-8 -*-
"""키워드 팔 관문 — 질의를 못 덮는 키워드 목록은 융합에서 뺀다(v21).

근거: [[vinc.research.korean-retrieval-2026-10-01 §4]]. 한국어 질의로 영어 문서를 찾을 때 두 글자
묶음 키워드 팔은 엉뚱한 한국어 행을 내고, RRF 가 그것으로 의미 팔의 정답을 밀어냈다.

지키는 것:
  · 의미 팔이 행을 냈고 키워드 상위 20행의 최고 덮음이 0.5 미만이면 키워드 목록을 비운다.
  · 의미 팔이 없거나 비었으면(인코더 없는 데스크톱·실패) 절대 비우지 않는다.
  · 행에 `lex` 가 없으면(이관 전 스토어) 판단하지 않는다 — 오늘처럼 답한다.
  · `lex` 는 응답 행에 나가지 않는다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store.lexical import lex_text  # noqa: E402
from vinc_rag import retrieval as r  # noqa: E402

_KO_NOISE = [{"id": f"ko#{i:05d}", "text": t, "lex": lex_text(t), "score": 1.0 / (i + 1)}
             for i, t in enumerate(["오늘 시장은 조용했다", "판화 전시를 보러 갔다", "이름을 바꾼 화가"])]
_EN_SEM = [{"id": "en#00000", "text": "Hokusai renamed himself", "distance": 0.3}]


class _Store:
    scoped_chunk_search = True

    def __init__(self, kw, vec):
        self._kw, self._vec = kw, vec

    def fulltext_search(self, text, top_k, filters=None):
        return [dict(x) for x in self._kw]

    def vector_search(self, emb, top_k, filters=None):
        return [dict(x) for x in self._vec]


class GateTests(unittest.TestCase):
    def test_덮지_못하는_키워드_목록은_빠진다(self):
        rows, _, ran, _, gated = r._chunk_arms(_Store(_KO_NOISE, _EN_SEM),
                                               "그 판화가는 왜 평생 이름을 바꿨나", [0.0], 200, None)
        self.assertTrue(gated)
        self.assertEqual([x["id"] for x in rows], ["en#00000"])
        self.assertEqual(ran, ["keyword", "semantic"], "두 팔은 돌았다고 말한다")
        self.assertEqual(rows[0]["arms"], ["semantic"])

    def test_덮는_키워드_목록은_남는다(self):
        rows, _, _, _, gated = r._chunk_arms(_Store(_KO_NOISE, _EN_SEM), "판화 전시", [0.0], 200, None)
        self.assertFalse(gated)
        self.assertIn("ko#00001", [x["id"] for x in rows])

    def test_의미_팔이_없으면_빼지_않는다(self):
        rows, _, ran, _, gated = r._chunk_arms(_Store(_KO_NOISE, []),
                                               "그 판화가는 왜 평생 이름을 바꿨나", None, 200, None)
        self.assertFalse(gated)
        self.assertEqual(ran, ["keyword"])

        self.assertTrue(rows)

    def test_의미_팔이_비었으면_빼지_않는다(self):
        _, _, _, _, gated = r._chunk_arms(_Store(_KO_NOISE, []),
                                          "그 판화가는 왜 평생 이름을 바꿨나", [0.0], 200, None)
        self.assertFalse(gated)

    def test_이관_전_행이면_판단하지_않는다(self):
        legacy = [{k: v for k, v in x.items() if k != "lex"} for x in _KO_NOISE]
        _, _, _, _, gated = r._chunk_arms(_Store(legacy, _EN_SEM),
                                          "그 판화가는 왜 평생 이름을 바꿨나", [0.0], 200, None)
        self.assertFalse(gated)

    def test_lex_는_응답에_나가지_않는다(self):
        rows, *_ = r._chunk_arms(_Store(_KO_NOISE, _EN_SEM), "판화 전시", [0.0], 200, None)
        self.assertTrue(rows)
        self.assertTrue(all("lex" not in x for x in rows))

    def test_검색_봉투가_관문을_말한다(self):
        res = r.search("그 판화가는 왜 평생 이름을 바꿨나", query_embedding=[0.0], top_k=10,
                       store=_StoreFull(_KO_NOISE, _EN_SEM))
        self.assertTrue(res.get("keyword_gated"), res)
        res2 = r.search("판화 전시", query_embedding=[0.0], top_k=10,
                        store=_StoreFull(_KO_NOISE, _EN_SEM))
        self.assertNotIn("keyword_gated", res2)


class _StoreFull(_Store):
    """`search()` 가 부르는 나머지 표면 — 노드 팔·제목 조회는 빈 답."""
    driver_name = "fake"

    def run(self, cypher, params=None):
        return []

    def reader_view(self):
        return self

class GateBarTests(unittest.TestCase):
    """문턱 0.5 와 창 20행을 값으로 못 박는다. 위 테스트는 덮음 0 과 1 만 써서 문턱이 0.3 이든
    0.9 든 통과한다. 두 값은 잰 자리라 바꾸려면 다시 재야 한다."""
    _Q = "alpha beta gamma delta"
    _SEM = [{"id": "s#00000", "text": "x", "distance": 0.3}]

    @staticmethod
    def _kw(*lexes):
        return [{"id": f"k#{i:05d}", "text": t, "lex": t} for i, t in enumerate(lexes)]

    def test_문턱은_절반이고_창은_스무_행이다(self):
        self.assertEqual((r.KEYWORD_GATE, r.GATE_TOP), (0.5, 20))

    def test_정확히_절반을_덮으면_남고_그_아래면_빠진다(self):
        self.assertFalse(r._keyword_gate(self._Q, [self._kw("alpha beta"), self._SEM]))
        self.assertTrue(r._keyword_gate(self._Q, [self._kw("alpha"), self._SEM]))

    def test_스무_행_안의_덮는_행만_본다(self):
        noise = ["zeta"] * 20
        inside = noise[:19] + [self._Q] + noise[19:]
        outside = noise + [self._Q]
        self.assertFalse(r._keyword_gate(self._Q, [self._kw(*inside), self._SEM]))
        self.assertTrue(r._keyword_gate(self._Q, [self._kw(*outside), self._SEM]))


if __name__ == "__main__":
    unittest.main()
