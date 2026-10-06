"""작은 노드 미러는 HNSW 대신 정확 코사인으로 훑는다. 소유자 범위와 결과 순위를 지킨다.

고정 코퍼스: 다수 소유자 512행 · 소수 소유자 64행 · 1,024차원 · 시드 400. 질의의 전역 이웃은
전부 다수 소유자 것이라, HNSW 경로에서는 소수 소유자가 정확 폴백을 밟는다.
근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]"""
import math
import random

import pytest

from store import embedded_backend as eb, request_timing as timing

DIM, MAJOR, MINOR = 1024, 512, 64
MAJOR_OWNER, MINOR_OWNER = "user:major", "user:minority"


def _corpus():
    rng = random.Random(400)
    query = [rng.gauss(0, 1) for _ in range(DIM)]
    rows = []
    for i in range(MAJOR):        # 질의 방향 + 잡음: 전역 상위가 전부 여기서 나온다
        rows.append({"id": f"concept:major{i}", "kind": "concept", "owner": MAJOR_OWNER,
                     "title": f"Major {i}", "text": f"Major {i}",
                     "emb": [q + rng.gauss(0, .3) for q in query]})
    for i in range(MINOR):        # 질의와 거의 직교
        rows.append({"id": f"concept:minor{i}", "kind": "concept", "owner": MINOR_OWNER,
                     "title": f"Minor {i}", "text": f"Minor {i}",
                     "emb": [rng.gauss(0, 1) for _ in range(DIM)]})
    return query, rows


def _exact_top(query, rows, owner, k=5):
    """엔진과 독립으로 계산한 정확 코사인 상위 k."""
    qn = math.sqrt(sum(x * x for x in query))
    def cos(e):
        return sum(a * b for a, b in zip(query, e)) / (qn * math.sqrt(sum(x * x for x in e)))
    mine = [r for r in rows if r["owner"] == owner]
    return [r["id"] for r in sorted(mine, key=lambda r: -cos(r["emb"]))[:k]]


@pytest.fixture(scope="module")
def fixed(tmp_path_factory):
    from api import node_mirror
    from store.backend import open_store
    from store.config import load_config
    mp = pytest.MonkeyPatch()
    mp.setenv("VINC_KEEP_VECTOR_INDEX", "off")
    store = open_store(load_config({"data_dir": str(tmp_path_factory.mktemp("nv") / "db"),
                                    "buffer_pool_mb": 128}))
    assert store is not None, eb.EmbeddedBackend.last_open_error
    store.provision(dim=DIM)
    query, rows = _corpus()
    assert node_mirror.replace(store, rows, "fixture")["index_ok"]
    try:
        yield store, query, rows
    finally:
        store.close()
        mp.undo()


def _search(store, query, owner):
    reader = store.reader_view()
    try:
        with timing.capture() as collected:
            hits = reader.node_vector_search(query, 5, owner=owner)
        return [h["id"] for h in hits], collected.values
    finally:
        reader.close()


def test_below_threshold_both_owners_get_the_exact_top_five(fixed):
    store, query, rows = fixed
    for owner in (MINOR_OWNER, MAJOR_OWNER):
        ids, phases = _search(store, query, owner)
        assert ids == _exact_top(query, rows, owner)
        assert all(i.startswith("concept:minor" if owner == MINOR_OWNER else "concept:major")
                   for i in ids)
        assert "node_vector_exact" in phases
        assert "node_vector_index" not in phases and "node_vector_fallback" not in phases
        assert not any(p.endswith("_error") for p in phases)


def test_above_threshold_hnsw_runs_and_the_minority_fallback_stays_exact(fixed, monkeypatch):
    store, query, rows = fixed
    monkeypatch.setattr(eb, "_NODE_VEC_EXACT_MAX_ROWS", 1)
    ids, phases = _search(store, query, MINOR_OWNER)
    assert ids == _exact_top(query, rows, MINOR_OWNER)
    assert "node_vector_index" in phases and "node_vector_fallback" in phases
    assert "node_vector_exact" not in phases
    ids, phases = _search(store, query, MAJOR_OWNER)
    assert len(ids) == 5 and all(i.startswith("concept:major") for i in ids)
    assert "node_vector_index" in phases and "node_vector_exact" not in phases


def test_ownerless_exact_scan_keeps_the_return_shape(fixed):
    store, query, rows = fixed
    reader = store.reader_view()
    try:
        hits = reader.node_vector_search(query, 3)
    finally:
        reader.close()
    assert [h["id"] for h in hits] == _exact_top(query, rows, MAJOR_OWNER, k=3)
    assert set(hits[0]) == {"id", "kind", "title", "text", "distance"}


def test_index_answers_still_asks_the_index_not_the_exact_scan(fixed):
    """작은 미러의 검색은 색인 없이도 답한다 — 그래서 색인 확인은 색인을 직접 물어야 한다."""
    from api import node_mirror
    store, query, rows = fixed
    assert node_mirror.index_answers(store)
    store.drop_node_vector_index()
    try:
        assert not node_mirror.index_answers(store)
        assert store.node_vector_search(query, 5, owner=MINOR_OWNER) \
            and _search(store, query, MINOR_OWNER)[0] == _exact_top(query, rows, MINOR_OWNER)
    finally:
        assert store.create_node_vector_index() == ""
    assert node_mirror.index_answers(store)
