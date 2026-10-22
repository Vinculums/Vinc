# -*- coding: utf-8 -*-
"""개념 병합이 기록 부착을 (기록, 타입) 쌍마다 하나씩 남기는가 — 실엔진.

쓰기 경로는 한 기록이 한 대상에 타입별로 따로 붙게 저장한다(ATTACHES_TO 의 MERGE
패턴에 type 이 있다). 병합의 재지향도 같은 단위로 판정해야, keep 에 다른 타입 부착이
이미 있을 때 drop 의 부착이 사라지거나 그 엣지 위에 덮어써지지 않는다.
PK 네임스페이싱 두 모드(0·1)를 같은 테스트로 돈다.
"""
from __future__ import annotations

import os

import pytest

from api import fragment, query
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER = "user:merge-attach-owner"
KEEP, DROP, OTHER = "concept:ma-keep", "concept:ma-drop", "concept:ma-other"
R1, R2, R3 = "record:ma-r1", "record:ma-r2", "record:ma-r3"


@pytest.fixture(params=["0", "1"])
def store(request, tmp_path):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _attach_types(st, rec: str, cid: str) -> list:
    rows = st.run("MATCH (r:Record)-[a:ATTACHES_TO]->(c:Concept) "
                  "WHERE r.record_id = $r AND c.concept_id = $c RETURN a.type AS t",
                  {"r": pkns.key(OWNER, rec), "c": pkns.key(OWNER, cid)})
    return sorted(str(row.get("t")) for row in rows)


def test_merge_keeps_one_attachment_per_record_and_type(store):
    nodes = [{"id": KEEP, "type": "concept", "title": "Keep"},
             {"id": DROP, "type": "concept", "title": "Drop"},
             {"id": OTHER, "type": "concept", "title": "Other"},
             {"id": R1, "type": "record", "title": "R1"},
             {"id": R2, "type": "record", "title": "R2"},
             {"id": R3, "type": "record", "title": "R3"}]
    edges = [
        # R1: keep 에 relates_to 가 이미 있고, drop 에는 같은 타입과 다른 타입이 있다.
        {"from": R1, "to": KEEP, "type": "relates_to"},
        {"from": R1, "to": DROP, "type": "relates_to"},
        {"from": R1, "to": DROP, "type": "derives_from"},
        # R2: keep 에는 없고 drop 에만 두 타입이 있다.
        {"from": R2, "to": DROP, "type": "relates_to"},
        {"from": R2, "to": DROP, "type": "prerequisite_of"},
        # R3: 다른 개념에만 붙어 병합과 무관하다.
        {"from": R3, "to": OTHER, "type": "inherits_from"},
    ]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges},
                                  store=store)
    assert out["executed"] and out["merged"]["edges"]["created"] == len(edges), out
    assert _attach_types(store, R1, DROP) == ["derives_from", "relates_to"]

    res = query.merge_nodes(KEEP, DROP, owner=OWNER, store=store)
    assert res.get("found") and not res.get("error"), res

    assert _attach_types(store, R1, KEEP) == ["derives_from", "relates_to"]
    assert _attach_types(store, R2, KEEP) == ["prerequisite_of", "relates_to"]
    assert _attach_types(store, R3, OTHER) == ["inherits_from"]
    # 같은 (기록, 타입) 이 keep 에 이미 있던 R1 의 relates_to 하나만 건너뛴다.
    assert (res["repointed"], res["skipped"]) == (3, 1), res
    assert not store.run("MATCH (c:Concept) WHERE c.concept_id = $id RETURN c.concept_id",
                         {"id": pkns.key(OWNER, DROP)})
