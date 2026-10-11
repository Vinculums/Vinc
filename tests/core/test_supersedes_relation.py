"""대체(supersedes) 관계: 같은 종류끼리만, 근거 필수, 구간은 경고만, 동기화·삭제 전파.

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] §2
"""
from __future__ import annotations

import os

import pytest

from api import fragment, query, sync
from store.backend import open_store
from store.config import load_config

OWNER = "user:supersedes-owner"
OLD, NEW = "record:claim-v1", "record:claim-v2"
EVIDENCE = "v2 replaces v1 after the as_of read path landed"


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("supersedes") / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st, tmp_path_factory
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def records(old_until="2026-10-07", new_from="2026-10-07"):
    return [
        {"id": OLD, "type": "record", "title": "Point-in-time reads are not built",
         "domain": "vinc/governance/claim-decay", "valid_from": "2026-09-28",
         "valid_until": old_until},
        {"id": NEW, "type": "record", "title": "as_of is on search and brief",
         "domain": "vinc/governance/claim-decay", "valid_from": new_from},
    ]


def seed(st, **kw):
    return fragment.merge_fragment({"owner": OWNER, "nodes": records(**kw), "edges": [
        {"from": NEW, "to": OLD, "type": "supersedes", "evidence": EVIDENCE}]}, store=st)


def test_record_supersedes_is_stored_directed_with_evidence(graph):
    st, _ = graph
    out = seed(st)
    assert not out.get("warnings"), out
    edges = query.node_edges(NEW, owner=OWNER, type="SUPERSEDES", store=st)["edges"]
    assert [(e["from"], e["to"], e["evidence"]) for e in edges] == [(NEW, OLD, EVIDENCE)]


def test_both_ends_see_the_supersession_in_neighbors(graph):
    st, _ = graph
    seed(st)
    for seed_id, other in ((NEW, OLD), (OLD, NEW)):
        got = query.neighbors(seed_id, owner=OWNER, store=st)
        assert {"rel": "SUPERSEDES", "id": other} in [
            {"rel": n["rel"], "id": n["id"]} for n in got["neighbors"]], got


def test_interval_is_warned_about_never_written(graph):
    st, _ = graph
    out = seed(st, old_until="")
    assert any("has no valid_until" in w for w in out["warnings"]), out
    row = query.node_edges(OLD, owner=OWNER, store=st)
    assert row["count"] >= 1
    seed(st)   # 원상 복구 — 다른 테스트가 같은 모듈 스토어를 쓴다
    out = seed(st, new_from="2026-10-08")
    assert any("differs from" in w for w in out["warnings"]), out
    seed(st)


@pytest.mark.parametrize("edge", [
    {"from": NEW, "to": OLD, "type": "supersedes"},                         # 근거 없음
    {"from": NEW, "to": NEW, "type": "supersedes", "evidence": "self"},     # 자기 자신
    {"from": NEW, "to": "decision:d-old", "type": "supersedes", "evidence": "x"},  # 다른 종류
    {"from": "concept:plain-a", "to": "concept:plain-b", "type": "supersedes",
     "evidence": "not document mirrors"},
])
def test_refuses_without_evidence_self_cross_kind_and_non_mirror_concepts(graph, edge):
    st, _ = graph
    nodes = records() + [
        {"id": "decision:d-old", "type": "decision", "title": "Old decision"},
        {"id": "concept:plain-a", "type": "concept", "title": "Plain A"},
        {"id": "concept:plain-b", "type": "concept", "title": "Plain B"}]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": [edge]}, store=st)
    assert any("supersedes expects" in w for w in out["warnings"]), out


def test_decision_supersedes_reads_the_interval_from_props(graph):
    st, _ = graph
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": "decision:approve-owner-only", "type": "decision", "title": "Owner approves",
         "props": {"valid_from": "2026-09-01"}},
        {"id": "decision:approve-anyone", "type": "decision", "title": "Anyone approves",
         "props": {"valid_from": "2026-10-11"}}], "edges": [
        {"from": "decision:approve-anyone", "to": "decision:approve-owner-only",
         "type": "supersedes", "evidence": "the owner widened approval"}]}, store=st)
    assert any("has no valid_until" in w for w in out["warnings"]), out
    got = query.neighbors("decision:approve-owner-only", owner=OWNER, store=st)
    assert "decision:approve-anyone" in [n["id"] for n in got["neighbors"]], got


def test_sync_carries_the_edge_with_evidence_and_its_deletion(graph):
    st, factory = graph
    seed(st)
    bundle = sync.export_delta(owner=OWNER, everything=True, store=st)
    found = [e for e in bundle["edges"] if e["type"] == "supersedes" and e["from"] == NEW]
    assert found and found[0]["evidence"] == EVIDENCE, bundle["edges"]
    cfg = load_config({"data_dir": str(factory.mktemp("supersedes-recipient") / "db")})
    recipient = open_store(cfg)
    recipient.provision(dim=cfg.embedding_dim)
    try:
        applied = sync.apply_delta(owner=OWNER, bundle=bundle, store=recipient)
        assert applied["executed"], applied
        assert not [w for w in applied.get("warnings", []) if "skipped" in w], applied
        got = query.node_edges(NEW, owner=OWNER, type="SUPERSEDES", store=recipient)
        assert got["count"] == 1 and got["edges"][0]["evidence"] == EVIDENCE
    finally:
        recipient.close()
    deleted = query.delete_edge(NEW, "supersedes", OLD, owner=OWNER, store=st)
    assert deleted["deleted"] == {"edges": 1}, deleted
    delta = sync.export_delta(owner=OWNER, since=bundle["next_cursor"], everything=True, store=st)
    assert any(t["src"] == NEW and t["dst"] == OLD and t["rel"] == "SUPERSEDES"
               for t in delta["edge_tombstones"])
