# -*- coding: utf-8 -*-
"""`rrf_fuse` 의 `weights`·`tie_by_id` 가 두 호출부의 출력을 그대로 지키는지 잠근다.

기준은 인자가 생기기 전의 두 구현을 **그대로** 옮긴 것이다: 프로덕션 `rrf_fuse`(동점은 처음 본
순서, 입력 행 위에 점수를 쓴다)와 평가 하네스의 `weighted_rrf`(동점은 id 오름차순, 복사본을 돌려준다).
동점이 많고 id 가 겹치고 id 가 없는 행이 섞인 무작위 입력에서 둘이 같아야 한다.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rag_eval.rerank import weighted_rrf  # noqa: E402
from vinc_rag.retrieval import rrf_fuse  # noqa: E402


def _old_rrf_fuse(ranked_lists, *, k=60, top_k=None, id_key="id"):
    scores, rows_by_id = {}, {}
    for rows in ranked_lists:
        for rank, row in enumerate(rows):
            rid = row.get(id_key)
            if rid is None:
                continue
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
            rows_by_id.setdefault(rid, row)
    fused = sorted(rows_by_id.values(), key=lambda r: scores[r[id_key]], reverse=True)
    for r in fused:
        r["rrf_score"] = scores[r[id_key]]
    return fused[:top_k] if top_k else fused


def _old_weighted_rrf(ranked_lists, weights, *, k=60, id_key="id"):
    if len(ranked_lists) != len(weights):
        raise ValueError("lists and weights differ in length")
    scores, rows_by_id = {}, {}
    for rows, w in zip(ranked_lists, weights):
        for rank, row in enumerate(rows):
            rid = row.get(id_key)
            if rid is None:
                continue
            scores[rid] = scores.get(rid, 0.0) + float(w) / (k + rank + 1)
            rows_by_id.setdefault(rid, row)
    fused = sorted(rows_by_id.values(), key=lambda r: (-scores[r[id_key]], str(r[id_key])))
    out = []
    for r in fused:
        row = dict(r)
        row["rrf_score"] = scores[r[id_key]]
        out.append(row)
    return out


def _arms(rng: random.Random, n_arms: int):
    """작은 id 풀에서 뽑아 같은 순위·같은 점수의 동점이 흔하게 나온다."""
    pool = [f"d{i}" for i in range(12)] + [3, 7, None]
    arms = []
    for a in range(n_arms):
        rows = []
        for _ in range(rng.randrange(0, 14)):
            rid = rng.choice(pool)
            rows.append({"id": rid, "arm": a, "text": f"t{rng.randrange(100)}",
                         **({"rrf_score": -1.0} if rng.random() < 0.1 else {})})
        arms.append(rows)
    return arms


def _clone(arms):
    return [[dict(r) for r in rows] for rows in arms]


def _shape(rows):
    """값과 키 순서까지 본다 — `dict ==` 는 키 순서를 안 본다."""
    return [list(r.items()) for r in rows]


@pytest.mark.parametrize("seed", range(300))
def test_default_call_is_the_old_product_function(seed):
    rng = random.Random(seed)
    arms = _arms(rng, rng.choice([1, 2, 3]))
    k, top_k = rng.choice([60, 20, 0, 5]), rng.choice([None, 0, 1, 4, 50])
    want = _old_rrf_fuse(_clone(arms), k=k, top_k=top_k)
    got = rrf_fuse(_clone(arms), k=k, top_k=top_k)
    assert _shape(got) == _shape(want)


def test_default_call_still_writes_the_score_on_the_input_rows():
    arms = [[{"id": "a"}, {"id": "b"}], [{"id": "b"}]]
    out = rrf_fuse(arms)
    assert out[0] is arms[0][1] and arms[0][1]["rrf_score"] == out[0]["rrf_score"]


def test_default_ties_keep_the_first_seen_order_not_the_id_order():
    # a 와 b 는 서로 다른 팔의 1위라 점수가 같다. 처음 본 쪽(z)이 앞에 선다.
    out = rrf_fuse([[{"id": "z"}], [{"id": "a"}]])
    assert [r["id"] for r in out] == ["z", "a"]
    out = rrf_fuse([[{"id": "z"}], [{"id": "a"}]], tie_by_id=True)
    assert [r["id"] for r in out] == ["a", "z"]


@pytest.mark.parametrize("seed", range(300))
def test_weighted_rrf_is_the_old_harness_function(seed):
    rng = random.Random(1000 + seed)
    n_arms = rng.choice([1, 2, 3])
    arms = _arms(rng, n_arms)
    weights = [rng.choice([1.0, 1, 0.0, 0.5, 2.0, 0.3]) for _ in range(n_arms)]
    k = rng.choice([60, 20, 0, 5, 100])
    want = _old_weighted_rrf(_clone(arms), weights, k=k)
    before = _clone(arms)
    got = weighted_rrf(arms, weights, k=k)
    assert _shape(got) == _shape(want)
    assert arms == before, "입력 행을 건드리면 같은 팔 목록으로 도는 스윕의 앞 조건이 덮인다"
    assert not any(g is r for g in got for rows in arms for r in rows)


def test_weights_length_mismatch_is_refused_in_both_places():
    with pytest.raises(ValueError, match="lists and weights differ in length"):
        rrf_fuse([[{"id": "a"}]], weights=[1.0, 2.0])
    with pytest.raises(ValueError, match="lists and weights differ in length"):
        weighted_rrf([[{"id": "a"}]], [1.0, 2.0])


def test_id_key_is_passed_through():
    arms = [[{"doc": "x"}, {"doc": "y"}], [{"doc": "y"}]]
    assert [r["doc"] for r in weighted_rrf(arms, [1.0, 1.0], id_key="doc")] == ["y", "x"]
