# -*- coding: utf-8 -*-
"""`_node_semantic_arm` 의 계약 — 응답 dict 를 직접 고치지 않고 `patch` 로만 돌려준다.

검색 응답 전체는 `test_search_golden.py` 가 고정한다. 거기에 없는 자리를 이 파일이 지킨다:
제목 팔이 먼저 적은 `node_error` 를 의미 팔이 덮지 않는 것, `base` 가 읽기 전용인 것,
`patch` 키의 순서(`node_semantic` → `node_domain_error` → `node_error`)가 응답 키 순서가 되는
것, 도메인 조회 실패가 표식으로 남는 것. 스토어는 가짜라 엔진 없이 돈다.
"""
from __future__ import annotations

import copy
import os
import unittest

from vinc_rag import retrieval as R

NEAR = [1.0, 0.0, 0.0, 0.0]


class FakeStore:
    """미러 질의만 흉내 낸다. `hits` 는 거리 오름차순 목록, `fail` 이 있으면 질의가 던진다.
    `run` 은 도메인 조회 자리 — `domains_fail` 이면 라벨마다 던진다."""

    def __init__(self, hits=(), *, fail: Exception | None = None, domains_fail: bool = False):
        self.hits = list(hits)
        self.fail = fail
        self.domains_fail = domains_fail
        self.calls = 0

    def node_vector_search(self, vec, k, owner=None, **kw):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [dict(h) for h in self.hits]

    def run(self, query, params=None):
        if self.domains_fail:
            raise RuntimeError("domain read blocked")
        return []


def hit(node_id: str, distance: float) -> dict:
    return {"id": node_id, "title": node_id, "kind": "concept", "distance": distance}


def arm(store, base=None, **over):
    """부르는 쪽(`_search`)과 같은 인자로 팔을 돌린다. 시점 필터는 끈다(`historical`)."""
    args = dict(continuation=False, owner="user:t", query_model=None, _wide=False, _nk=5, _kw={},
                _scope=None, historical=True, mode=None, known_at=None, _until=None, _vstats={},
                base=base if base is not None else {})
    args.update(over)
    return R._node_semantic_arm(store, args.pop("query_embedding", NEAR), **args)


class EnvIsolated(unittest.TestCase):
    def setUp(self):
        self._prev = {k: os.environ.pop(k, None) for k in ("VINC_NODE_SEMANTIC_ARM", "VINC_NODE_SEM_RANK")}

    def tearDown(self):
        for k, v in self._prev.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class WhenTheArmDoesNotRun(EnvIsolated):
    def test_no_embedding_returns_nothing_and_does_not_touch_the_store(self):
        store = FakeStore([hit("concept:a", 0.1)])
        vec_hits, lead, decisive, patch = arm(store, query_embedding=None)
        self.assertEqual((vec_hits, lead, decisive, patch), ([], None, None, {}))
        self.assertEqual(store.calls, 0)

    def test_a_continuation_page_returns_nothing_and_does_not_touch_the_store(self):
        store = FakeStore([hit("concept:a", 0.1)])
        vec_hits, lead, decisive, patch = arm(store, continuation=True)
        self.assertEqual((vec_hits, lead, decisive, patch), ([], None, None, {}))
        self.assertEqual(store.calls, 0)

    def test_the_switch_off_only_says_so(self):
        os.environ["VINC_NODE_SEMANTIC_ARM"] = "off"
        store = FakeStore([hit("concept:a", 0.1)])
        vec_hits, lead, decisive, patch = arm(store)
        self.assertEqual((vec_hits, lead, decisive), ([], None, None))
        self.assertEqual(list(patch), ["node_semantic"])
        self.assertEqual(patch["node_semantic"]["state"], "disabled")
        self.assertEqual(store.calls, 0)


class WhenTheArmRuns(EnvIsolated):
    def test_hits_lead_and_decisive_come_from_the_raw_list(self):
        gap = R._SEM_GAP + 0.05
        store = FakeStore([hit("concept:a", 0.10), hit("concept:b", 0.10 + gap), hit("concept:c", 0.9)])
        vec_hits, lead, decisive, patch = arm(store)
        self.assertEqual([h["id"] for h in vec_hits], ["concept:a", "concept:b", "concept:c"])
        self.assertTrue(all(h["matched_in"] == "semantic" for h in vec_hits))
        self.assertEqual(lead, ("concept:a", round(gap, 6)))
        self.assertEqual(decisive, "concept:a")
        self.assertEqual(list(patch), ["node_semantic"])
        self.assertEqual(patch["node_semantic"]["state"], "complete")

    def test_a_close_pair_leads_but_is_not_decisive(self):
        store = FakeStore([hit("concept:a", 0.10), hit("concept:b", 0.10 + R._SEM_GAP / 2)])
        _, lead, decisive, _ = arm(store)
        self.assertEqual(lead[0], "concept:a")
        self.assertIsNone(decisive)

    def test_the_rank_switch_keeps_the_lead_but_drops_decisive(self):
        os.environ["VINC_NODE_SEM_RANK"] = "off"
        store = FakeStore([hit("concept:a", 0.10), hit("concept:b", 0.9)])
        _, lead, decisive, _ = arm(store)
        self.assertEqual(lead[0], "concept:a")
        self.assertIsNone(decisive)

    def test_a_domain_lookup_failure_leaves_a_mark_after_node_semantic(self):
        store = FakeStore([hit("concept:a", 0.1)], domains_fail=True)
        vec_hits, _, _, patch = arm(store)
        self.assertEqual(list(patch), ["node_semantic", "node_domain_error"])
        self.assertTrue(patch["node_domain_error"].startswith("domain lookup failed: "))
        self.assertNotIn("domain", vec_hits[0])


class WhenTheQueryFails(EnvIsolated):
    def test_the_failure_is_a_patch_node_error_and_a_degraded_arm(self):
        store = FakeStore(fail=RuntimeError("boom"))
        vec_hits, lead, decisive, patch = arm(store)
        self.assertEqual((vec_hits, lead, decisive), ([], None, None))
        self.assertEqual(list(patch), ["node_semantic", "node_error"])
        self.assertEqual(patch["node_error"], "RuntimeError: boom")
        self.assertEqual(patch["node_semantic"]["state"], "degraded")
        self.assertEqual(patch["node_semantic"]["error"], "node_semantic_failed")

    def test_a_known_code_is_kept_as_the_arm_error(self):
        store = FakeStore(fail=RuntimeError("node_vector_query_failed"))
        _, _, _, patch = arm(store)
        self.assertEqual(patch["node_semantic"]["error"], "node_vector_query_failed")

    def test_a_node_error_the_title_arm_wrote_first_is_not_overwritten(self):
        base = {"node_error": "ValueError: title arm"}
        before = copy.deepcopy(base)
        store = FakeStore(fail=RuntimeError("boom"))
        _, _, _, patch = arm(store, base=base)
        self.assertNotIn("node_error", patch)
        self.assertEqual(patch["node_semantic"]["state"], "degraded")
        base.update(patch)
        self.assertEqual(base["node_error"], "ValueError: title arm")
        self.assertEqual(list(base), ["node_error", "node_semantic"])
        self.assertNotEqual(base, before)

    def test_base_is_read_only(self):
        base = {"kind": "search", "rows": []}
        before = copy.deepcopy(base)
        arm(FakeStore(fail=RuntimeError("boom")), base=base)
        arm(FakeStore([hit("concept:a", 0.1)]), base=base)
        self.assertEqual(base, before)


if __name__ == "__main__":
    unittest.main()
