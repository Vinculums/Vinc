# -*- coding: utf-8 -*-
"""리랭커 계기의 **순수 함수**가 맞는가 · scripts/reranker.py · scripts/eval_rerank.py.

왜 순수 함수를 테스트하나: 리랭커 채택 판정은 이 도구의 숫자로 내린다. 재정렬이 안정
정렬이 아니거나 게이트가 반대로 켜지면 "리랭커가 이겼다/졌다"가 통째로 뒤집히는데,
그 오류는 그럴듯한 표로 나와 눈으로는 안 잡힌다. 모델·스토어 없이 도는 부분만 검사한다
(그 둘이 없는 CI 에서도 **skip 되면 안 된다**: 조용한 skip 이 이 리포의 상습 함정이다).
"""
from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class RerankRowsTests(unittest.TestCase):
    def setUp(self):
        self.m = _load("reranker")

    def test_점수_내림차순으로_다시_세우고_원래_순위를_남긴다(self):
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        out = self.m.rerank_rows(rows, [0.1, 0.9, 0.5])
        self.assertEqual([r["id"] for r in out], ["b", "c", "a"])
        self.assertEqual([r["prior_rank"] for r in out], [2, 3, 1])
        self.assertEqual(out[0]["rerank_score"], 0.9)

    def test_동점은_들어온_순서를_지킨다(self):
        """안정 정렬이 아니면 동점에서 RRF 순위가 뒤집히고, 그 뒤집힘이 "리랭커 효과"로
        읽힌다. 동점은 리랭커가 판단을 안 한 것이므로 앞 단계의 순서가 남아야 한다."""
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        out = self.m.rerank_rows(rows, [0.5, 0.5, 0.5])
        self.assertEqual([r["id"] for r in out], ["a", "b", "c"])

    def test_입력_행을_바꾸지_않는다(self):
        rows = [{"id": "a"}, {"id": "b"}]
        self.m.rerank_rows(rows, [0.0, 1.0])
        self.assertEqual(rows, [{"id": "a"}, {"id": "b"}])

    def test_길이가_다르면_세운다(self):
        with self.assertRaises(ValueError):
            self.m.rerank_rows([{"id": "a"}], [0.1, 0.2])

    def test_top_n_은_재정렬_뒤에_자른다(self):
        rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        out = self.m.rerank_rows(rows, [0.1, 0.9, 0.5], top_n=2)
        self.assertEqual([r["id"] for r in out], ["b", "c"])


class MaxPTests(unittest.TestCase):
    def setUp(self):
        self.m = _load("reranker")

    def test_조각_최대를_쓰고_조각_없는_청크는_맨_뒤다(self):
        out = self.m.maxp([[0.1, 0.7, 0.3], [], [0.2]])
        self.assertEqual(out[0], 0.7)
        self.assertEqual(out[2], 0.2)
        self.assertTrue(math.isinf(out[1]) and out[1] < 0)


class GateTests(unittest.TestCase):
    """게이트는 "리랭커를 **돌릴지**"를 답한다(True = 돌린다). 방향을 헷갈리면 적응형
    조건이 정확히 반대의 질의에서 리랭커를 켠다."""

    def setUp(self):
        self.m = _load("reranker")
        self.both_first = 2.0 / (self.m.RRF_K + 1)

    def test_always_는_행이_둘_이상이면_켠다(self):
        self.assertTrue(self.m.gate([{"rrf_score": 0.1}, {"rrf_score": 0.05}], "always"))

    def test_행이_둘_미만이면_어떤_정책도_끈다(self):
        self.assertFalse(self.m.gate([{"rrf_score": 0.1}], "always"))
        self.assertFalse(self.m.gate([], "top-agree"))

    def test_top_agree_는_두_팔이_같은_1위를_놓았을_때_끈다(self):
        rows = [{"rrf_score": self.both_first}, {"rrf_score": 0.02}]
        self.assertFalse(self.m.gate(rows, "top-agree"))

    def test_top_agree_는_한_팔만_1위로_놓았을_때_켠다(self):
        rows = [{"rrf_score": 1.0 / (self.m.RRF_K + 1)}, {"rrf_score": 0.01}]
        self.assertTrue(self.m.gate(rows, "top-agree"))

    def test_margin_은_간격이_좁을_때만_켠다(self):
        close = [{"rrf_score": 0.030}, {"rrf_score": 0.029}]
        far = [{"rrf_score": 0.030}, {"rrf_score": 0.010}]
        self.assertTrue(self.m.gate(close, "margin:0.2"))
        self.assertFalse(self.m.gate(far, "margin:0.2"))

    def test_모르는_정책은_세운다(self):
        with self.assertRaises(ValueError):
            self.m.gate([{"rrf_score": 0.1}, {"rrf_score": 0.05}], "random")


class WeightedRrfTests(unittest.TestCase):
    def setUp(self):
        self.r = _load("reranker")
        self.e = _load("eval_rerank")

    def test_가중치가_전부_1이면_원본_rrf_와_같은_순서다(self):
        from api.query import rrf_fuse
        kw = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        vec = [{"id": "c"}, {"id": "a"}, {"id": "d"}]
        mine = [r["id"] for r in self.e.weighted_rrf([kw, vec], [1.0, 1.0])]
        ref = rrf_fuse([[dict(r) for r in kw], [dict(r) for r in vec]])
        ref.sort(key=lambda r: (-r["rrf_score"], r["id"]))     # search() 의 안정 정렬
        self.assertEqual(mine, [r["id"] for r in ref])

    def test_벡터_가중치_0이면_fts_순서다(self):
        kw = [{"id": "a"}, {"id": "b"}]
        vec = [{"id": "b"}, {"id": "a"}]
        out = [r["id"] for r in self.e.weighted_rrf([kw, vec], [1.0, 0.0])]
        self.assertEqual(out, ["a", "b"])


class ScoreExtTests(unittest.TestCase):
    def setUp(self):
        self.e = _load("eval_rerank")

    def test_호출_수_대리_지표는_순위에서_나온다(self):
        """정답이 3위면 세 번째 vinc_doc_get 에서 해석된다: R@1 = 0, R@3 = 1, rank = 3."""
        s = self.e.score_ext(["x", "y", "d1"], "d1", 10)
        self.assertEqual((s["r1"], s["r3"], s["rank"]), (0.0, 1.0, 3))
        self.assertAlmostEqual(s["ndcg"], 1 / math.log2(4))     # eval_search 의 값 그대로

    def test_없으면_rank_0_이고_평균_순위에서_빠진다(self):
        s = self.e.score_ext(["x"], "d1", 10)
        self.assertEqual(s["rank"], 0)
        m = self.e.summarize([s, self.e.score_ext(["d1"], "d1", 10)])
        self.assertEqual(m["mean_rank"], 1.0)
        self.assertEqual(m["recall"], 0.5)

    def test_부트스트랩은_같은_값이면_delta_0_이고_ci_가_0을_품는다(self):
        b = self.e.paired_bootstrap([0.5, 0.2, 0.9], [0.5, 0.2, 0.9], n=200)
        self.assertEqual(b["delta"], 0.0)
        self.assertLessEqual(b["ci95"][0], 0.0)
        self.assertGreaterEqual(b["ci95"][1], 0.0)


if __name__ == "__main__":
    unittest.main()
