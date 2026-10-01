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

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"


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

    K = 60          # 이 클래스의 풀이 융합된 값. 게이트에 늘 함께 넘긴다.

    def setUp(self):
        self.m = _load("reranker")
        self.both_first = 2.0 / (self.K + 1)

    def test_always_는_행이_둘_이상이면_켠다(self):
        self.assertTrue(self.m.gate([{"rrf_score": 0.1}, {"rrf_score": 0.05}],
                                    "always", k=self.K))

    def test_행이_둘_미만이면_어떤_정책도_끈다(self):
        self.assertFalse(self.m.gate([{"rrf_score": 0.1}], "always", k=self.K))
        self.assertFalse(self.m.gate([], "top-agree", k=self.K))

    def test_top_agree_는_두_팔이_같은_1위를_놓았을_때_끈다(self):
        rows = [{"rrf_score": self.both_first}, {"rrf_score": 0.02}]
        self.assertFalse(self.m.gate(rows, "top-agree", k=self.K))

    def test_top_agree_는_한_팔만_1위로_놓았을_때_켠다(self):
        rows = [{"rrf_score": 1.0 / (self.K + 1)}, {"rrf_score": 0.01}]
        self.assertTrue(self.m.gate(rows, "top-agree", k=self.K))

    def test_k_를_안_주면_부를_수_없다(self):
        """기본값이 있던 동안 프로덕션 k 가 60에서 20으로 바뀌자 게이트가 **75건 중
        0건** 발화했고, 화면에는 `gated` 열이 `rrf` 와 같게만 보여서 조용했다.
        근거: [[record:the-top-agree-gate-never-fired-after-the-k-change-2026-09-09]]"""
        with self.assertRaises(TypeError):
            self.m.gate([{"rrf_score": 0.1}, {"rrf_score": 0.05}], "always")

    def test_풀의_k_가_임계값을_정한다(self):
        """같은 점수라도 k 가 다르면 판정이 뒤집힌다 — 그것이 상수를 못 박으면 안 되는
        이유다. k=20 융합에서 두 팔이 동의한 1위는 2/21 이고, 그 값을 k=60 기준으로
        재면 '동의 안 함'으로 읽혀 언제나 리랭크를 켠다."""
        agreed_at_20 = [{"rrf_score": 2.0 / 21}, {"rrf_score": 1.0 / 21}]
        self.assertFalse(self.m.gate(agreed_at_20, "top-agree", k=20))
        self.assertFalse(self.m.gate(agreed_at_20, "top-agree", k=60),
                         "k=60 기준으로도 0.095 는 2/61 보다 크다 — 이쪽은 항상 끈다")
        one_arm_at_20 = [{"rrf_score": 1.0 / 21}, {"rrf_score": 1.0 / 22}]
        self.assertTrue(self.m.gate(one_arm_at_20, "top-agree", k=20))
        self.assertFalse(self.m.gate(one_arm_at_20, "top-agree", k=60),
                         "k 가 틀리면 켜야 할 질의를 끈다 — 이것이 0건 발화의 정체다")

    def test_margin_은_간격이_좁을_때만_켠다(self):
        close = [{"rrf_score": 0.030}, {"rrf_score": 0.029}]
        far = [{"rrf_score": 0.030}, {"rrf_score": 0.010}]
        self.assertTrue(self.m.gate(close, "margin:0.2", k=self.K))
        self.assertFalse(self.m.gate(far, "margin:0.2", k=self.K))

    def test_모르는_정책은_세운다(self):
        with self.assertRaises(ValueError):
            self.m.gate([{"rrf_score": 0.1}, {"rrf_score": 0.05}], "random", k=self.K)


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


class PickPieceTests(unittest.TestCase):
    """자식 창 — 벡터가 이긴 조각 하나만 채점한다. seq 를 잘못 집으면 `max` 창보다
    나쁜 값을 내면서 "자식 창은 안 된다"로 읽히므로, 경계는 테스트로 못박는다."""

    def setUp(self):
        self.m = _load("reranker")

    def test_seq_가_없거나_범위_밖이면_첫_조각(self):
        pieces = ["a", "b", "c"]
        for seq in (None, -1, 3, 99):
            self.assertEqual(self.m.pick_piece(pieces, seq), "a", seq)

    def test_범위_안이면_그_조각(self):
        pieces = ["a", "b", "c"]
        self.assertEqual(self.m.pick_piece(pieces, 0), "a")
        self.assertEqual(self.m.pick_piece(pieces, 2), "c")

    def test_조각이_없으면_죽는다(self):
        with self.assertRaises(ValueError):
            self.m.pick_piece([], 0)

    def test_child_창은_후보당_쌍_하나만_쓴다(self):
        """`max` 는 조각 수만큼 재고 `child` 는 후보 수만큼 잰다 — 그 차이가 실험의
        전부다. 모델 없이 split·score 만 갈아 끼워 쌍 수를 센다."""
        m = self.m
        pieces = {"A": ["a0", "a1", "a2"], "B": ["b0", "b1"]}
        rr = m.Reranker.__new__(m.Reranker)
        seen = []
        rr.split = lambda t: pieces[t]
        rr.score = lambda q, ps: (seen.extend(ps), [1.0] * len(ps))[1]

        texts = ["A", "B"]
        scores, pairs = m.Reranker.score_texts(rr, "q", texts, window="child",
                                               seqs=[2, None])
        self.assertEqual(pairs, len(texts))
        self.assertEqual(seen, ["a2", "b0"])
        self.assertEqual(len(scores), len(texts))

        seen.clear()
        _, pairs_max = m.Reranker.score_texts(rr, "q", texts, window="max")
        self.assertEqual(pairs_max, 5)          # 조각 수 = 3 + 2
        self.assertGreater(pairs_max, pairs)

    def test_seqs_길이가_다르면_죽는다(self):
        m = self.m
        rr = m.Reranker.__new__(m.Reranker)
        rr.split = lambda t: [t]
        rr.score = lambda q, ps: [0.0] * len(ps)
        with self.assertRaises(ValueError):
            m.Reranker.score_texts(rr, "q", ["A", "B"], window="child", seqs=[0])


class SummarizeRunsTests(unittest.TestCase):
    """반복 측정의 중앙값과 흩어진 정도. 이 계산이 틀리면 잡음이 결론으로 읽힌다 —
    실제로 한 번 그랬다(같은 러너 타입에서 jina 385.2ms 와 693.5ms 를 한 표본씩 재고
    "하드웨어가 다르다"로 읽었다, 2026-09-04)."""

    def setUp(self):
        self.m = _load("reranker")

    def test_중앙값이지_평균이_아니다(self):
        """공유 vCPU 의 잡음은 느려지는 쪽으로만 튄다. 평균은 그 꼬리를 먹는다."""
        r = self.m.summarize_runs([100.0, 110.0, 400.0])
        self.assertEqual(r["ms_per_long_pair"], 110.0)      # 평균이면 203.3
        self.assertEqual(r["ms_per_long_pair_min"], 100.0)
        self.assertEqual(r["ms_per_long_pair_max"], 400.0)

    def test_짝수개는_가운데_둘의_평균(self):
        r = self.m.summarize_runs([100.0, 200.0])
        self.assertEqual(r["ms_per_long_pair"], 150.0)

    def test_스프레드는_중앙값_대비_최대최소_폭이다(self):
        r = self.m.summarize_runs([100.0, 100.0, 200.0])
        self.assertEqual(r["spread_pct"], 100.0)            # (200-100)/100
        self.assertEqual(self.m.summarize_runs([50.0] * 4)["spread_pct"], 0.0)

    def test_한_번만_재면_스프레드가_0_이라_아무_말도_못_한다(self):
        r = self.m.summarize_runs([250.0])
        self.assertEqual(r["reps"], 1)
        self.assertEqual(r["spread_pct"], 0.0)

    def test_빈_입력은_수치를_지어내지_않는다(self):
        r = self.m.summarize_runs([])
        self.assertEqual(r["reps"], 0)
        self.assertIsNone(r["ms_per_long_pair"])


if __name__ == "__main__":
    unittest.main()
