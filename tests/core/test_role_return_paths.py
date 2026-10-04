"""Structured role policies and rejection routes survive real storage and transport."""
from __future__ import annotations

import pytest
import json

from api import fragment, query, role_contract, share, sync
from store.backend import open_store
from store.config import load_config
from vinc_mcp import roles

OWNER = "user:role-return-owner"
SRC, DST = "concept:agent-critic", "concept:agent-author"


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    import os
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("role-returns") / "db")})
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


def node(nid):
    return {"id": nid, "type": "concept", "title": "Reviewer" if nid == SRC else "Author",
            "domain": "vinc/agent/roles", "props": {
                "mission": "Review a package", "tier": "verify", "rung": "fast",
                "never": ["merge"], "person_only": [{"action": "merge", "reason": "A person decides"}]}}


def seed(st):
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [node(SRC), node(DST)], "edges": [{
        "from": SRC, "to": DST, "type": "returns_to", "evidence": "Failed checks return to author",
        "confidence": 0.0}]}, store=st)
    assert not out.get("warnings"), out
    return out


def test_structured_person_only_is_visible_without_prose_matching_and_shell_dict_repr(graph):
    st, _ = graph
    seed(st)
    got = query._probe_node(SRC, owner=OWNER, cfg=None, store=st)
    assert json.loads(got["props"])["person_only"] == [{"action": "merge", "reason": "A person decides"}]
    shell = roles.render_shell(roles.build_payload([node(SRC)])["roles"][0])
    assert "- merge: A person decides" in shell
    assert "{'action'" not in shell


def test_returns_are_directed_readable_and_separate_from_forward_handoffs(graph):
    st, _ = graph
    seed(st)
    edge = query.node_edges(SRC, owner=OWNER, type="RETURNS_TO", store=st)["edges"][0]
    assert edge["from"] == SRC and edge["to"] == DST and edge["confidence"] == 0.0
    assert edge["evidence"] == "Failed checks return to author"
    assert query.node_edges(DST, owner=OWNER, dir="out", type="RETURNS_TO", store=st)["count"] == 0
    edges = [{"source": SRC, "target": DST, "type": "RETURNS_TO"}]
    assert roles.works_with(SRC, [node(SRC), node(DST)], edges) == {"returns_to": ["agent-author (Author)"]}
    assert roles.works_with(DST, [node(SRC), node(DST)], edges) == {
        "receives_returns_from": ["agent-critic (Reviewer)"]}
    assert query.node_edges(SRC, owner="user:other-reader", store=st)["found"] is False


def test_returns_reject_missing_evidence_nonroles_self_and_wrong_carriers(graph):
    st, _ = graph
    for src, dst, extra in [(SRC, DST, {}), (SRC, SRC, {"evidence": "same"}),
                             ("concept:ordinary", DST, {"evidence": "not a role"}),
                             ("record:proof", DST, {"evidence": "not a role"})]:
        nodes = [node(SRC), node(DST), {"id": "concept:ordinary", "type": "concept", "title": "Ordinary"},
                 {"id": "record:proof", "type": "record", "title": "Proof"}]
        out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": [{
            "from": src, "to": dst, "type": "returns_to", **extra}]}, store=st)
        assert any("returns_to expects" in warning for warning in out.get("warnings", [])), out
        assert out["merged"]["edges"]["created"] == 0


def test_foreign_endpoint_cannot_receive_a_return_edge(graph):
    st, _ = graph
    foreign = "concept:agent-foreign"
    fragment.merge_fragment({"owner": "user:foreign", "nodes": [node(foreign)], "edges": []}, store=st)
    fragment.merge_fragment({"owner": OWNER, "nodes": [node(SRC)], "edges": []}, store=st)
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [], "edges": [{"from": SRC, "to": foreign,
        "type": "returns_to", "evidence": "forbidden cross-owner"}]}, store=st)
    assert out.get("warnings") and out["merged"]["edges"]["created"] == 0
    assert all(edge["to"] != foreign for edge in query.node_edges(SRC, owner=OWNER, store=st)["edges"])


def test_sync_and_share_preserve_return_evidence_structured_props_and_zero_confidence(graph):
    st, factory = graph
    seed(st)
    bundle = sync.export_delta(owner=OWNER, everything=True, store=st)
    assert bundle["executed"], bundle
    found = next(edge for edge in bundle["edges"] if edge["type"] == "returns_to")
    assert found["evidence"] == "Failed checks return to author" and found["confidence"] == 0.0
    cfg = load_config({"data_dir": str(factory.mktemp("return-recipient") / "db")})
    recipient = open_store(cfg)
    recipient.provision(dim=cfg.embedding_dim)
    try:
        applied = sync.apply_delta(owner=OWNER, bundle=bundle, store=recipient)
        assert applied["executed"] and not applied["warnings"], applied
        edge = query.node_edges(SRC, owner=OWNER, type="RETURNS_TO", store=recipient)["edges"][0]
        assert edge["evidence"] == found["evidence"] and edge["confidence"] == 0.0
    finally:
        recipient.close()
    pack = share.build_share_pack(owner=OWNER, ids=[SRC, DST], store=st)
    assert "fragment" in pack, pack
    export = next(edge for edge in pack["fragment"]["edges"] if edge["type"] == "returns_to")
    assert export["evidence"] == found["evidence"]
    assert export["confidence"] == 0.0


def test_deletion_exports_the_return_tombstone_and_merge_preserves_the_contract(graph):
    st, _ = graph
    seed(st)
    before = sync.export_delta(owner=OWNER, everything=True, store=st)
    refused = query.merge_nodes(SRC, DST, owner=OWNER, store=st)
    assert "explicit review" in refused["error"] and "dropped" not in refused
    assert query._probe_node(DST, owner=OWNER, cfg=None, store=st) is not None
    deleted = query.delete_edge(SRC, "returns_to", DST, owner=OWNER, store=st)
    assert deleted["deleted"] == {"edges": 1}
    delta = sync.export_delta(owner=OWNER, since=before["next_cursor"], everything=True, store=st)
    assert any(t["src"] == SRC and t["dst"] == DST and t["rel"] == "RETURNS_TO"
               for t in delta["edge_tombstones"])


@pytest.mark.parametrize("items", [
    [{"action": "merge", "reason": "person"}, "publish"],
    [{"action": "merge", "reason": " "}], [{"action": "has spaces", "reason": "person"}],
    [{"action": "merge", "reason": "person"}, {"action": "merge", "reason": "duplicate"}],
    [{"action": "merge", "reason": "person", "extra": True}], [42],
])
def test_malformed_structured_person_policy_warns_without_silent_contract_changes(items):
    role = node(SRC)
    role["props"]["person_only"] = items
    assert any("person_only" in message for message in role_contract.check(role, created=True))
