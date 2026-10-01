# -*- coding: utf-8 -*-
"""답변 가능성 게이트 — 주제를 찾은 것과 질문에 답하는 것은 다르다.

남은 고장의 모양은 하나였다: 질문이 노드 제목을 그대로 인용하고 그 **속성**을 묻는다
("…는 문장은 누가 썼나"). 제목이 통째로 맞으니 토큰 여유가 넘쳐 확신 문턱을 쉽게
넘는다. 랭킹으로는 못 잡는다 — 순위 문제가 아니기 때문이다.

이 게이트가 보는 것: 질문에서 확정 노드의 제목이 **안 먹은 부분**이, 검색이 가져온
어디에도 없는가. 없다면 그 노드가 아니라 **어느 노드도** 질문의 그 부분을 못 다룬다.

근거: [[record:answerability-is-a-residual-coverage-question-2026-09-10]]
"""
from __future__ import annotations

import os
import unittest

from api import query as q


def _res(*, titles=(), snippets=()):
    """search 가 돌려준 것의 최소 모양 — 후보 제목과 청크 발췌만 본다."""
    return {"node_hits": [{"title": t} for t in titles],
            "rows": [{"snippet": s} for s in snippets]}


class FlagDefault(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.pop("VINC_BRIEF_ANSWERABILITY", None)

    def tearDown(self):
        os.environ.pop("VINC_BRIEF_ANSWERABILITY", None)
        if self._prev is not None:
            os.environ["VINC_BRIEF_ANSWERABILITY"] = self._prev

    def test_the_gate_is_off_unless_asked_for(self):
        """문턱을 확신 18건 위에서 골랐고 답 불가 21문항 중 15문항을 이 세션에 저작했다.
        시험지 절반이 이 신호를 편들 수 있으므로 기본값은 꺼진 채로 둔다."""
        self.assertFalse(q._brief_answerability_on())

    def test_a_false_string_does_not_turn_it_on(self):
        for v in ("", "off", "false", "0", "no"):
            os.environ["VINC_BRIEF_ANSWERABILITY"] = v
            self.assertFalse(q._brief_answerability_on(), v)

    def test_the_usual_truthy_spellings_turn_it_on(self):
        for v in ("on", "1", "true", "yes", " ON "):
            os.environ["VINC_BRIEF_ANSWERABILITY"] = v
            self.assertTrue(q._brief_answerability_on(), v)


class OrphanResidual(unittest.TestCase):
    def test_a_question_the_title_fully_covers_has_no_residual(self):
        """제목이 질문을 다 먹으면 물은 것이 곧 그 노드다 — 막을 이유가 없다."""
        self.assertEqual(
            q._brief_orphan_residual("alpha core", "Alpha Core", _res()), 0.0)

    def test_a_residual_the_corpus_knows_is_not_orphaned(self):
        """남은 부분이 다른 후보나 발췌에 있으면, 답이 그래프 안에 있을 수 있다."""
        r = q._brief_orphan_residual(
            "alpha core deployment rollback", "Alpha Core",
            _res(titles=["deployment notes"], snippets=["a rollback takes ten seconds"]))
        self.assertEqual(r, 0.0)

    def test_a_residual_nothing_has_seen_is_orphaned(self):
        """이 게이트가 잡는 자리다 — 물은 속성이 코퍼스 어디에도 없다."""
        r = q._brief_orphan_residual(
            "alpha core who wrote this", "Alpha Core",
            _res(titles=["beta edge"], snippets=["something unrelated"]))
        self.assertEqual(r, 1.0)

    def test_an_empty_query_does_not_divide_by_zero(self):
        self.assertEqual(q._brief_orphan_residual("", "Alpha", _res()), 0.0)

    def test_the_threshold_is_where_it_was_measured(self):
        """이 값을 옮기려면 다시 재야 한다 — 0.34 는 맞는 확신을 셋 잃었고
        0.67 은 하나 잃는다(둘 다 답 불가 확정은 0 이다)."""
        self.assertEqual(q._ORPHAN_BLOCK, 0.67)


if __name__ == "__main__":
    unittest.main()
