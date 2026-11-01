"""Directed incident edges are complete, typed and scoped in the real engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import query as q
from engines import domain_hierarchy as dh
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER = "user:edge-reader"
FOREIGN = "team:foreign-space"


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    import os
    old = {k: os.environ.get(k) for k in ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND")}
    os.environ["VINC_PK_NAMESPACED"] = request.param
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("directed-edges") / "db")})
    st = open_store(cfg)
    try:
        st.provision(dim=cfg.embedding_dim)
        yield st
    finally:
        st.close()
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def put(st, label, nid, owner=OWNER):
    from store.embedded_schema import node_table_specs
    pk = node_table_specs()[label]["pk"]
    st.run(f"MERGE (n:{label} {{{pk}: $id}}) SET n.owner_id = $owner",
           {"id": pkns.key(owner, nid), "owner": owner})


def edge(st, src, src_id, rel, dst, dst_id, *, owner=OWNER,
         dst_owner=OWNER, evidence=None, confidence=None, subtype=None):
    from store.embedded_schema import node_table_specs
    specs = node_table_specs()
    params = {"a": pkns.key(owner, src_id), "b": pkns.key(dst_owner, dst_id)}
    sets = []
    for key, value in (("evidence", evidence), ("confidence", confidence), ("type", subtype)):
        if value is not None:
            sets.append(f"r.{key} = ${key}")
            params[key] = value
    st.run(f"MATCH (a:{src} {{{specs[src]['pk']}:$a}}), "
           f"(b:{dst} {{{specs[dst]['pk']}:$b}}) MERGE (a)-[r:{rel}]->(b)"
           + (" SET " + ", ".join(sets) if sets else ""), params)


def test_all_carriers_and_actual_incident_relations(graph):
    st = graph
    for label, nid in (("Concept", "concept:hub"), ("Concept", "concept:peer"),
                       ("Decision", "decision:choice"), ("Record", "record:proof"),
                       ("Episode", "episode:run"), ("Bridge", "bridge:crossing"),
                       ("Entity", "custom:a"), ("Entity", "custom:b")):
        put(st, label, nid)
    edge(st, "Concept", "concept:hub", "PREREQUISITE_OF", "Decision", "decision:choice",
         evidence="Gate before release", confidence=0.85)
    edge(st, "Decision", "decision:choice", "DERIVES_FROM", "Concept", "concept:hub")
    edge(st, "Concept", "concept:hub", "RELATED_TO", "Concept", "concept:peer")
    edge(st, "Record", "record:proof", "ATTACHES_TO", "Concept", "concept:hub",
         evidence="Measured sample", confidence=0.0, subtype="supports")
    edge(st, "Episode", "episode:run", "ABOUT", "Concept", "concept:hub")
    edge(st, "Bridge", "bridge:crossing", "CONNECTS", "Concept", "concept:hub")
    edge(st, "Entity", "custom:a", "TYPED", "Entity", "custom:b", subtype="depends_on")
    dh.ensure_chain(st, "shared/ours/leaf")
    st.run("MATCH (n:Concept {concept_id:$id}), (d:Domain {name:'shared/ours/leaf'}) "
           "MERGE (n)-[:IN_DOMAIN]->(d)", {"id": pkns.key(OWNER, "concept:hub")})
    all_edges = q.node_edges("concept:hub", owner=OWNER, store=st)
    assert all_edges["executed"], all_edges
    assert all_edges["found"] and all_edges["count"] == 7
    assert {e["type"] for e in all_edges["edges"]} == {
        "PREREQUISITE_OF", "DERIVES_FROM", "RELATED_TO", "ATTACHES_TO", "ABOUT", "CONNECTS", "IN_DOMAIN"}
    structural = next(e for e in all_edges["edges"] if e["type"] == "IN_DOMAIN")
    assert structural == {"from": "concept:hub", "to": "domain:shared/ours/leaf",
                          "type": "IN_DOMAIN", "evidence": None, "confidence": None, "derived": True}
    prereq = q.node_edges("decision:choice", owner=OWNER, dir="in", type="PREREQUISITE_OF", store=st)
    assert prereq["edges"][0]["from"] == "concept:hub"
    assert prereq["edges"][0]["evidence"] == "Gate before release"
    assert prereq["edges"][0]["confidence"] == 0.85
    assert q.node_edges("decision:choice", owner=OWNER, dir="out", type="PREREQUISITE_OF", store=st)["count"] == 0
    # VIN-894 — 문서화된 소문자 이름을 대소문자 무관하게 받는다(물리 라벨은 대문자라
    # 그대로는 어느 라벨과도 안 맞아 0건이 나왔다).
    for name in ("prerequisite_of", "Prerequisite_Of", "PREREQUISITE_OF"):
        same = q.node_edges("decision:choice", owner=OWNER, dir="in", type=name, store=st)
        assert same["count"] == 1 and same["edges"][0]["from"] == "concept:hub", (name, same)
    # `relates_to` → `RELATED_TO`: 대문자 폴백만으로는 못 맞추는 맵 경로.
    rel = q.node_edges("concept:hub", owner=OWNER, type="relates_to", store=st)
    assert rel["count"] == 1 and {e["type"] for e in rel["edges"]} == {"RELATED_TO"}, rel
    # 라벨이 아닌 저작 서브타입은 원문 그대로 걸러 라벨 정규화에 휘둘리지 않는다.
    sub = q.node_edges("concept:hub", owner=OWNER, type="supports", store=st)
    assert sub["count"] == 1 and sub["edges"][0]["type"] == "ATTACHES_TO", sub
    for nid in ("record:proof", "episode:run", "bridge:crossing", "custom:a"):
        got = q.node_edges(nid, owner=OWNER, dir="out", store=st)
        assert got["found"] and got["count"] == 1, got
    typed = q.node_edges("custom:b", owner=OWNER, dir="in", type="depends_on", store=st)
    assert typed["edges"][0]["type"] == "TYPED"
    assert typed["edges"][0]["subtype"] == "depends_on"
    assert typed["edges"][0]["confidence"] is None
    attached = q.node_edges("record:proof", owner=OWNER, type="ATTACHES_TO", store=st)
    assert attached["edges"][0]["confidence"] == 0.0
    batch = q.node_edges(nodes=["concept:hub", "record:proof", "concept:hub"], owner=OWNER, store=st)
    assert batch["count"] == 7  # Incoming and outgoing selections do not duplicate the same edge.
    cut = q.node_edges("concept:hub", owner=OWNER, limit=2, store=st)
    assert cut["count"] == 7 and len(cut["edges"]) == 2 and cut["edges_truncated"]


def test_owner_and_global_domain_tree_never_disclose_foreign_subtrees(graph):
    st = graph
    put(st, "Concept", "concept:foreign", FOREIGN)
    put(st, "Concept", "concept:visible")
    put(st, "Concept", "concept:isolated")
    # Deliberately create a cross-owner edge to prove both endpoints are checked.
    edge(st, "Concept", "concept:visible", "RELATED_TO", "Concept", "concept:foreign", dst_owner=FOREIGN)
    dh.ensure_chain(st, "root/ours/leaf")
    dh.ensure_chain(st, "root/foreign/secret")
    for nid, owner, domain in (("concept:visible", OWNER, "root/ours/leaf"),
                               ("concept:foreign", FOREIGN, "root/foreign/secret")):
        st.run("MATCH (n:Concept {concept_id:$id}), (d:Domain {name:$domain}) "
               "MERGE (n)-[:IN_DOMAIN]->(d)", {"id": pkns.key(owner, nid), "domain": domain})
    out = q.node_edges("domain:root", owner=OWNER, store=st)
    assert out["executed"] and out["found"], out
    assert out["edges"] == [{"from": "domain:root", "to": "domain:root/ours", "type": "PARENT_OF",
                             "evidence": None, "confidence": None, "derived": True}]
    assert not q.node_edges("domain:root/foreign", owner=OWNER, store=st)["found"]
    assert q.node_edges("concept:visible", owner=OWNER, type="RELATED_TO", store=st)["count"] == 0
    assert not q.node_edges("concept:foreign", owner=OWNER, store=st)["found"]
    assert not q.node_edges("concept:absent", owner=OWNER, store=st)["found"]
    isolated = q.node_edges("concept:isolated", owner=OWNER, store=st)
    assert isolated["found"] and isolated["count"] == 0
    mixed = q.node_edges(nodes=["concept:visible", "concept:foreign", "concept:absent"], owner=OWNER, store=st)
    assert mixed["missing_nodes"] == ["concept:foreign", "concept:absent"]
    assert len(mixed["edges"]) == 1
    assert q.node_edges("concept:foreign", owner=FOREIGN, type="RELATED_TO", store=st)["count"] == 0


def test_original_exp05_dag_in_one_batch_without_graph_payload(graph):
    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "agent_lab_exp05_dag.json").read_text("utf-8"))
    for nid in fixture["roster"]:
        put(graph, "Concept", nid)
    for src, dst, evidence in fixture["edges"]:
        edge(graph, "Concept", src, "PREREQUISITE_OF", "Concept", dst, evidence=evidence)
    got = q.node_edges(nodes=fixture["roster"], owner=OWNER, type="PREREQUISITE_OF", dir="out", store=graph)
    assert got["executed"] and not got["missing_nodes"], got
    assert got["count"] == len(fixture["edges"]) == 27
    assert sorted((e["from"], e["to"], e["evidence"]) for e in got["edges"]) == sorted(tuple(e) for e in fixture["edges"])
    assert "graph" not in got and not got["edges_truncated"]


def test_invalid_queries_do_not_open_a_store(monkeypatch):
    monkeypatch.setattr(q, "open_store", lambda cfg: pytest.fail("invalid read opened store"))
    for args in ({}, {"node": "concept:x", "nodes": ["concept:x"]}, {"nodes": []},
                 {"nodes": ["concept:x"] * 101}, {"nodes": [42]}, {"node": "physical\x1fid"},
                 {"node": "concept:x", "dir": "wrong"}, {"node": "concept:x", "limit": 5001}):
        got = q.node_edges(owner=OWNER, **args)
        assert not got["executed"] and "invalid" in got["error"]
