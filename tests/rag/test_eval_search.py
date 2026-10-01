# -*- coding: utf-8 -*-
"""검색 평가 도구의 **지표가 맞는가** — scripts/eval_search.py.

왜 지표를 테스트하나: 이 도구의 숫자로 "벡터를 켤 가치가 있나", "모델을 바꿀까" 를
결정한다. 지표가 틀리면 그 결정이 통째로 틀린다. 그런데 지표 오류는 그럴듯한 숫자를
내놓기 때문에 눈으로는 안 잡힌다 — 손으로 계산한 값과 대조해 못박는다.

스토어·임베더 없이 도는 순수 함수만 검사한다(그 둘이 없는 CI 에서도 **skip 되면 안 된다**
— 조용한 skip 이 이 리포의 상습 함정이다).
"""
from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eval_search.py"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_eval_search", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class EvalMetricTests(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_1위면_전부_만점이다(self):
        s = self.m.score(["d1", "d2", "d3"], "d1", 10)
        self.assertEqual(s["recall"], 1.0)
        self.assertEqual(s["mrr"], 1.0)
        self.assertEqual(s["ndcg"], 1.0)          # 1/log2(2) = 1

    def test_순위가_내려가면_mrr_과_ndcg_만_떨어진다(self):
        """Recall 은 '들어 있나'만 보므로 3위여도 1.0 이다. 이 둘을 헷갈리면
        '재현율은 좋은데 왜 체감이 나쁘지' 를 영영 설명 못 한다."""
        s = self.m.score(["x", "y", "d1"], "d1", 10)
        self.assertEqual(s["recall"], 1.0)
        self.assertAlmostEqual(s["mrr"], 1 / 3)
        self.assertAlmostEqual(s["ndcg"], 1 / math.log2(4))    # rank 3 → log2(3+1)

    def test_상위k_밖이면_0이다(self):
        s = self.m.score(["a", "b", "c", "d1"], "d1", 3)       # 정답은 4위
        self.assertEqual((s["recall"], s["mrr"], s["ndcg"]), (0.0, 0.0, 0.0))

    def test_없으면_0이다(self):
        s = self.m.score(["a", "b"], "d1", 10)
        self.assertEqual((s["recall"], s["mrr"], s["ndcg"]), (0.0, 0.0, 0.0))

    def test_언어_분류가_한국어를_영어와_가른다(self):
        """언어를 섞어 재면 CJK 손실이 묻힌다 — 영어는 512 초과 손실이 전체 토큰의
        5.7% 인데 한국어는 82% 다. 분리 집계가 이 도구의 존재 이유 중 하나다."""
        self.assertEqual(self.m.lang_of("양자역학의 해밀토니안"), "ko")
        self.assertEqual(self.m.lang_of("Hamiltonian mechanics"), "en")
        self.assertEqual(self.m.lang_of("量子力学のハミルトニアン"), "cjk")
        # 영문 제목에 한글이 한두 자 섞여도 영어로 남아야 한다(임계 15%).
        self.assertEqual(self.m.lang_of("Quantum Field Theory 개론서 introduction "
                                        "for graduate students in physics"), "en")

    def test_평균이_조건별로_따로_난다(self):
        rows = [{"recall": 1.0, "mrr": 1.0, "ndcg": 1.0},
                {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}]
        self.assertEqual(self.m._mean(rows, "recall"), 0.5)
        self.assertEqual(self.m._mean([], "recall"), 0.0)      # 0 나눗셈 방어


if __name__ == "__main__":
    unittest.main()
