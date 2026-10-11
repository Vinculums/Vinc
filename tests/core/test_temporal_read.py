"""시점 읽기 후처리: as_of(유효 시간)와 known_at(그래프 시간)을 그래프 도구에 건다.

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §1
"""
from __future__ import annotations

import copy
import os

import pytest

from api import fragment, query, temporal_read
from store.backend import open_store
from store.config import load_config

OWNER = "user:temporal-owner"
HUB = "concept:point-in-time"


@pytest.fixture(scope="module", params=["0", "1"])
def st(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("temporal") / "db")})
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    nodes = [
        {"id": HUB, "type": "concept", "title": "Point-in-time reads", "created": "2026-08-01"},
        {"id": "record:v1", "type": "record", "title": "not built", "created": "2026-09-28",
         "valid_from": "2026-09-28", "valid_until": "2026-10-07"},
        {"id": "record:v2", "type": "record", "title": "on search and brief",
         "created": "2026-10-11", "valid_from": "2026-10-07"},
        {"id": "decision:old-rule", "type": "decision", "title": "Owner approves",
         "created": "2026-09-01", "props": {"valid_from": "2026-09-01",
                                            "valid_until": "2026-10-11"}},
        {"id": "episode:pr-673", "type": "episode", "title": "PR 673", "date": "2026-10-07"},
    ]
    edges = [{"from": "record:v1", "to": HUB, "type": "derives_from", "evidence": "about it"},
             {"from": "record:v2", "to": HUB, "type": "derives_from", "evidence": "about it"},
             {"from": "decision:old-rule", "to": HUB, "type": "about", "evidence": "about it"},
             {"from": "episode:pr-673", "to": HUB, "type": "about", "evidence": "about it"}]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges}, store=store)
    assert not [w for w in out.get("warnings", []) if "skipped" in w], out
    try:
        yield store
    finally:
        store.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ids(res):
    return {n["id"] for n in res["neighbors"]}


def neighbors(st, **kw):
    res = query.neighbors(HUB, owner=OWNER, store=st)
    return temporal_read.apply("neighbors", res, store=st, owner=OWNER, seed=HUB, **kw)


def test_defaults_change_nothing(st):
    res = query.neighbors(HUB, owner=OWNER, store=st)
    before = copy.deepcopy(res)
    assert temporal_read.apply("neighbors", res, store=st, owner=OWNER) == before


def test_as_of_reads_the_period_from_each_kind_of_interval(st):
    sept = neighbors(st, as_of="2026-09-30")
    assert ids(sept) == {"record:v1", "decision:old-rule"}, sept
    assert sept["validity_filtered"] == 2 and sept["time_scope"]["as_of"]["value"] == "2026-09-30"
    late = neighbors(st, as_of="2026-10-12")
    assert ids(late) == {"record:v2", "episode:pr-673"}, late
    # 반개구간: 10월 7일 하루에는 v1 이 이미 끝났고 v2 와 PR 이 성립한다.
    assert ids(neighbors(st, as_of="2026-10-07")) == {"record:v2", "episode:pr-673",
                                                      "decision:old-rule"}


def test_known_at_hides_what_was_recorded_later_and_counts_unknown_births(st):
    res = neighbors(st, known_at="2026-10-10")
    assert "record:v2" not in ids(res) and "record:v1" in ids(res)
    assert res["known_at_filtered"] == 1
    # 사건은 생성 시각 열이 없다. 감추지 않고 센다.
    assert "episode:pr-673" in ids(res) and res["known_at_unknown"] == 1


def test_graph_drops_edges_of_hidden_nodes_and_keeps_the_seed(st):
    res = query.graph(owner=OWNER, seed=HUB, depth=2, store=st)
    assert {"record:v1", "record:v2"} <= {n["id"] for n in res["nodes"]}, res["nodes"]
    got = temporal_read.apply("graph", res, store=st, owner=OWNER, seed=HUB,
                              as_of="2026-09-30")
    alive = {n["id"] for n in got["nodes"]}
    assert HUB in alive and "record:v1" in alive and "record:v2" not in alive
    assert all(e["source"] in alive and e["target"] in alive for e in got["edges"])


def test_invalid_period_is_an_error_not_today(st):
    res = neighbors(st, known_at="last week")
    assert res["executed"] is False and res["error"].startswith("invalid_time_scope")
