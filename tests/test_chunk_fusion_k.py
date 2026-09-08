# -*- coding: utf-8 -*-
"""청크 융합만 k=20 을 쓰고, 노드 융합은 안 쓴다.

왜 이 파일이 있나: `rrf_fuse` 의 k 는 **여태 아무도 안 넘겼다.** 그래서 60 은 측정된
적 없는 기본값이었고, 176문서 스토어의 두 질의 셋에서 20 이 양쪽 다 유의하게 낫다.
그 값을 넣는 것은 한 줄이지만, 같은 파일에 융합 호출부가 **둘** 있고 다른 하나는
제목·그래프 팔을 섞는다 — 그 팔들은 재 본 적이 없다. 한 줄을 두 곳에 붙이는 회귀가
조용하다: 검색은 계속 답하고 순위만 달라진다.

근거: [[record:rrf-k-20-reproduces-on-the-human-control-2026-09-08]]
"""
from __future__ import annotations

import inspect
import re
import unittest

from api import query as q


class ChunkFusionK(unittest.TestCase):
    def test_the_constant_is_twenty_and_lives_beside_the_pool_cap(self):
        self.assertEqual(q.CHUNK_RRF_K, 20)
        self.assertEqual(q.SEARCH_POOL_CAP, 200)

    def test_chunk_arms_passes_it_and_node_fusion_does_not(self):
        chunk = inspect.getsource(q._chunk_arms)
        self.assertIn("rrf_fuse(arms, k=CHUNK_RRF_K", chunk)
        # 파일 전체에서 rrf_fuse 호출을 세고, k 를 넘기는 것이 **하나뿐**인지 본다.
        src = inspect.getsource(q)
        calls = re.findall(r"rrf_fuse\([^)]*\)", src)
        with_k = [c for c in calls if "k=CHUNK_RRF_K" in c]
        self.assertEqual(len(with_k), 1, f"k 를 넘기는 호출이 하나가 아니다: {with_k}")
        self.assertGreaterEqual(len(calls), 2, "호출부가 둘인 전제가 깨졌다")

    def test_the_default_is_untouched(self):
        """`rrf_fuse` 자체의 기본값은 60 그대로다 — 안 잰 호출부의 동작을 바꾸지 않는다."""
        self.assertEqual(inspect.signature(q.rrf_fuse).parameters["k"].default, 60)

    def test_smaller_k_actually_reorders(self):
        """상수를 넣고도 순서가 안 바뀌면 아무것도 안 한 것이다.

        A 는 한 팔에서 1위·다른 팔에서 12위, B 는 양쪽 중위(5·6위)다. k 가 작으면
        상위 순위의 기여가 커서 A 가, 크면 평탄해져 두 팔 모두 준수한 B 가 이긴다.
        20 과 60 사이에서 뒤집히는 배치는 좁다 — 손으로 짚지 말고 찾은 것이다.
        채움 id 는 팔마다 다르게 둔다: 같은 id 를 양쪽에 두면 그것이 두 팔 모두
        상위라 A·B 를 제치고 1위가 된다(처음에 그렇게 짜서 틀렸다)."""
        def arm(*ids):
            return [{"id": i} for i in ids]

        a = arm("A", "a1", "a2", "a3", "B", "a4", "a5", "a6", "a7", "a8", "a9", "a10")
        b = arm("b1", "b2", "b3", "b4", "b5", "B", "b6", "b7", "b8", "b9", "b10", "A")
        # A 는 (0, 11), B 는 (4, 5).
        self.assertEqual(q.rrf_fuse([a, b], k=60)[0]["id"], "B")
        self.assertEqual(q.rrf_fuse([a, b], k=q.CHUNK_RRF_K)[0]["id"], "A")


if __name__ == "__main__":
    unittest.main()
