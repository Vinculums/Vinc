"""Decision relations agree across owner-scoped graph and context reads."""
from __future__ import annotations

import pytest

from api import fragment as frag
from api import query as q
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER = "user:vin433-reader"
FOREIGN = "team:vin433-other-space"
SRC = {"Concept": "concept:source", "Decision": "decision:source", "Episode": "episode:source"}
DST = {"Concept": "concept:target", "Decision": "decision:target"}
RELS = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")
PAIRS = [(s, d) for s in ("Concept", "Decision") for d in ("Concept", "Decision")]


@pytest.fixture(scope="module", params=["0", "1"])
def reads(request, tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VINC_PK_NAMESPACED", request.param)
        mp.setenv("MEMORY_STORE_BACKEND", "embedded")
        cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("vin433") / "db")})
        st = open_store(cfg)
        assert st is not None
        try:
            st.provision(dim=cfg.embedding_dim)
            nodes = [
                {"id": nid, "type": label.lower(), "title": f"{label} {side}",
                 "domain": "vinc/inside/leaf" if side == "source" else "vinc/outside",
                 "props": {"note": f"Synthetic {side}"}, "tags": ["fixture"]}
                for side, ids in (("source", SRC), ("target", DST))
                for label, nid in ids.items()
            ]
            edges = [
                {"from": SRC[s], "to": DST[d], "type": rel.lower(),
                 "evidence": f"Synthetic {s} {rel} {d}",
                 "confidence": 0.0 if rel == "INHERITS_FROM" else 0.73}
                for rel in RELS for s, d in PAIRS
            ] + [
                {"from": SRC[s], "to": DST[d], "type": "about",
                 "evidence": f"Synthetic {s} ABOUT {d}", "confidence": 0.61}
                for s in ("Decision", "Episode") for d in ("Concept", "Decision")
            ] + [{"from": SRC["Concept"], "to": DST["Concept"], "type": "relates_to"},
                 {"from": DST["Decision"], "to": SRC["Concept"], "type": "about"}]
            merged = frag.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges}, store=st)
            assert merged["executed"] and not merged.get("warnings"), merged
            for label, nid in (("Concept", SRC["Concept"]), ("Decision", DST["Decision"])):
                pk = "concept_id" if label == "Concept" else "decision_id"
                st.run(f"MATCH (n:{label} {{{pk}:$id}}) "
                       "MERGE (ch:Chunk {chunk_id:$chunk}) "
                       "SET ch.owner_id=$owner, ch.doc_id=$doc, ch.text=$text, ch.chunk_index=0 "
                       "MERGE (ch)-[:MENTIONS {method:'title'}]->(n)",
                       {"id": pkns.key(OWNER, nid), "chunk": pkns.key(OWNER, f"chunk:{label}"),
                        "owner": OWNER, "doc": f"synthetic-{label}", "text": f"Synthetic {label} source text"})
            yield st, cfg, {
                "full": q.graph(owner=OWNER, cfg=cfg, store=st),
                "scoped": q.graph(owner=OWNER, domains=["vinc/inside"], cfg=cfg, store=st),
                "outside": q.graph(owner=OWNER, domain="vinc/unrelated", cfg=cfg, store=st),
                "target_scope": q.graph(owner=OWNER, domain="vinc/outside", cfg=cfg, store=st),
            }
        finally:
            st.close()


@pytest.mark.parametrize("rel", RELS)
@pytest.mark.parametrize("src_label,dst_label", PAIRS)
def test_semantic_relations_preserve_direction_and_metadata(reads, rel, src_label, dst_label):
    st, cfg, graphs = reads
    src, dst = SRC[src_label], DST[dst_label]
    evidence = f"Synthetic {src_label} {rel} {dst_label}"
    confidence = 0.0 if rel == "INHERITS_FROM" else 0.73
    node = q.node_context(owner=OWNER, node=src, with_evidence=True, cfg=cfg, store=st)
    assert node["executed"], node
    link = next(e for e in node["links"]["items"] if e["id"] == dst and e["type"] == rel and e["dir"] == "out")
    assert link["evidence_text"] == evidence and link["confidence"] == confidence
    incoming = q.node_context(owner=OWNER, node=dst, with_evidence=True, cfg=cfg, store=st)
    back = next(e for e in incoming["links"]["items"]
                if e["id"] == src and e["type"] == rel and e["dir"] == "in")
    assert back["evidence_text"] == evidence and back["confidence"] == confidence
    lenses = [graphs["full"], graphs["scoped"]]
    if "Decision" in (src_label, dst_label):
        lenses.append(graphs["target_scope"])
    for graph in lenses:
        assert graph["executed"], graph
        edges = [e for e in graph["edges"] if (e["source"], e["target"], e["type"]) == (src, dst, rel)]
        assert len(edges) == 1, (src, dst, rel, graph["edges"])
        assert edges[0]["evidence"] == evidence and edges[0]["confidence"] == confidence
        assert edges[0]["cross_domain"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        for nid, label, side in ((src, src_label, "source"), (dst, dst_label, "target")):
            assert nodes[nid]["kind"] == label.lower()
            assert nodes[nid]["label"] == f"{label} {side}"
            assert nodes[nid]["props"]["note"] == f"Synthetic {side}"
            assert nodes[nid]["rev"] > 0 and nodes[nid]["updated_at"]
    assert not graphs["outside"]["edges"]
    ctx = q.edge_context(owner=OWNER, src=src, dst=dst, etype=rel, cfg=cfg, store=st)
    assert ctx["executed"], ctx
    assert ctx["evidence"] == evidence and ctx["confidence"] == confidence
    assert ctx["src"]["title"] == f"{src_label} source"
    assert ctx["dst"]["title"] == f"{dst_label} target"
    reverse = q.edge_context(owner=OWNER, src=dst, dst=src, etype=rel, cfg=cfg, store=st)
    assert not reverse["executed"] and "unknown edge" in reverse["note"], reverse


@pytest.mark.parametrize("src_label", ["Decision", "Episode"])
@pytest.mark.parametrize("dst_label", ["Concept", "Decision"])
def test_about_relations_and_context(reads, src_label, dst_label):
    st, cfg, graphs = reads
    src, dst = SRC[src_label], DST[dst_label]
    node = q.node_context(owner=OWNER, node=src, with_evidence=True, cfg=cfg, store=st)
    forward = next(e for e in node["links"]["items"]
                   if e["id"] == dst and e["type"] == "ABOUT" and e["dir"] == "out")
    assert forward["evidence_text"] == f"Synthetic {src_label} ABOUT {dst_label}"
    incoming = q.node_context(owner=OWNER, node=dst, with_evidence=True, cfg=cfg, store=st)
    direction = "att" if src_label == "Episode" else "in"
    assert any(e["id"] == src and e["type"] == "ABOUT" and e["dir"] == direction
               for e in incoming["links"]["items"])
    for graph in (graphs["full"], graphs["scoped"], graphs["target_scope"]):
        found = [e for e in graph["edges"] if (e["source"], e["target"], e["type"]) == (src, dst, "ABOUT")]
        assert len(found) == 1, graph["edges"]
        assert found[0]["evidence"] == f"Synthetic {src_label} ABOUT {dst_label}"
        assert found[0]["confidence"] == 0.61
    ctx = q.edge_context(owner=OWNER, src=src, dst=dst, etype="ABOUT", cfg=cfg, store=st)
    assert ctx["executed"] and ctx["confidence"] == 0.61, ctx
    assert ctx["evidence"] == f"Synthetic {src_label} ABOUT {dst_label}"


def test_related_to_stays_undirected_and_empty_evidence_is_known(reads):
    st, cfg, _ = reads
    ctx = q.edge_context(owner=OWNER, src=DST["Concept"], dst=SRC["Concept"], etype="RELATED_TO", cfg=cfg, store=st)
    assert ctx["executed"] and ctx["evidence"] == "" and ctx["confidence"] == 0.0, ctx


def test_excerpt_follows_the_endpoint_label(reads):
    st, cfg, _ = reads
    ctx = q.edge_context(owner=OWNER, src=SRC["Concept"], dst=DST["Decision"], etype="DERIVES_FROM", cfg=cfg, store=st)
    assert ctx["executed"], ctx
    assert ctx["src"]["excerpt"]["doc_id"] == "synthetic-Concept"
    assert ctx["dst"]["excerpt"]["doc_id"] == "synthetic-Decision"
    episode = q.edge_context(owner=OWNER, src=SRC["Episode"], dst=DST["Decision"], etype="ABOUT", cfg=cfg, store=st)
    assert episode["src"]["excerpt"] is None


def test_unknown_and_foreign_edges_do_not_disclose_metadata(reads):
    st, cfg, _ = reads
    st.run("MERGE (d:Decision {decision_id:$id}) SET d.owner_id=$owner, d.title='Private fixture', d.domain='vinc/inside/leaf'",
           {"id": pkns.key(FOREIGN, "decision:foreign"), "owner": FOREIGN})
    for reverse in (False, True):
        pattern = "(foreign)-[r:DERIVES_FROM]->(ours)" if reverse else "(ours)-[r:DERIVES_FROM]->(foreign)"
        st.run("MATCH (ours:Decision {decision_id:$ours}), (foreign:Decision {decision_id:$foreign}) "
               f"MERGE {pattern} SET r.evidence='Private edge fixture', r.confidence=0.99",
               {"ours": pkns.key(OWNER, SRC["Decision"]), "foreign": pkns.key(FOREIGN, "decision:foreign")})
    probes = [(SRC["Decision"], "decision:foreign", "DERIVES_FROM"),
              ("decision:foreign", SRC["Decision"], "DERIVES_FROM"),
              ("decision:missing", DST["Decision"], "DERIVES_FROM"),
              (SRC["Decision"], DST["Decision"], "RELATED_TO"),
              (SRC["Concept"], DST["Decision"], "ABOUT"),
              (SRC["Decision"], DST["Decision"], "DERIVES_FROM]->(x) //")]
    for src, dst, rel in probes:
        ctx = q.edge_context(owner=OWNER, src=src, dst=dst, etype=rel, cfg=cfg, store=st)
        assert not ctx["executed"] and "evidence" not in ctx, ctx
        assert ctx["src"] == {"id": src} and ctx["dst"] == {"id": dst}
    denied = q.edge_context(owner=FOREIGN, src=SRC["Decision"], dst=DST["Decision"], etype="DERIVES_FROM", cfg=cfg, store=st)
    assert not denied["executed"] and "evidence" not in denied
    for kwargs in ({}, {"domain": "vinc/inside"}):
        graph = q.graph(owner=OWNER, cfg=cfg, store=st, **kwargs)
        assert graph["executed"], graph
        assert "decision:foreign" not in {n["id"] for n in graph["nodes"]}
        assert "Private edge fixture" not in str(graph)
    if pkns.enabled():
        merged = frag.merge_fragment({"owner": FOREIGN, "nodes": [
            {"id": SRC["Decision"], "type": "decision", "title": "Other space source"},
            {"id": DST["Decision"], "type": "decision", "title": "Other space target"}],
            "edges": [{"from": SRC["Decision"], "to": DST["Decision"], "type": "derives_from",
                       "evidence": "Other space evidence"}]}, store=st)
        assert merged["executed"], merged
        ctx = q.edge_context(owner=OWNER, src=SRC["Decision"], dst=DST["Decision"], etype="DERIVES_FROM", cfg=cfg, store=st)
        assert ctx["evidence"] == "Synthetic Decision DERIVES_FROM Decision"


@pytest.mark.parametrize("seed_label", ["Concept", "Decision"])
def test_seeded_relations_are_joined_once(reads, seed_label):
    st, cfg, _ = reads
    graph = q.graph(owner=OWNER, seed=SRC[seed_label], depth=1, cfg=cfg, store=st)
    assert graph["executed"], graph
    triples = [(e["source"], e["target"], e["type"]) for e in graph["edges"]]
    assert len(triples) == len(set(triples)), triples
    for dst_label in ("Concept", "Decision"):
        for rel in RELS:
            assert (SRC[seed_label], DST[dst_label], rel) in triples
    if seed_label == "Decision":
        assert (SRC["Decision"], DST["Decision"], "ABOUT") in triples
        assert (SRC["Episode"], DST["Decision"], "ABOUT") not in triples
    for node in graph["nodes"]:
        assert node["degree"] == sum(node["id"] in (e["source"], e["target"])
                                     for e in graph["edges"])


def test_returns_to_keeps_its_existing_surface_policy(reads):
    st, cfg, _ = reads
    src, dst = "concept:agent-vin433-reviewer", "concept:agent-vin433-author"
    nodes = [{"id": nid, "type": "concept", "title": title, "domain": "vinc/agent/roles",
              "props": {"mission": "Synthetic review", "tier": "verify", "rung": "fast",
                        "never": ["publish"], "person_only": [
                            {"action": "publish", "reason": "Synthetic policy"}]}}
             for nid, title in ((src, "Fixture reviewer"), (dst, "Fixture author"))]
    merged = frag.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": [
        {"from": src, "to": dst, "type": "returns_to",
         "evidence": "Synthetic loop", "confidence": 0.0}]}, store=st)
    assert merged["executed"] and not merged.get("warnings"), merged
    node = q.node_context(owner=OWNER, node=src, with_evidence=True, cfg=cfg, store=st)
    link = next(e for e in node["links"]["items"] if e["type"] == "RETURNS_TO")
    assert link["evidence_text"] == "Synthetic loop" and link["confidence"] == 0.0
    for kwargs in ({}, {"seed": src, "depth": 1}):
        graph = q.graph(owner=OWNER, cfg=cfg, store=st, **kwargs)
        edge = next(e for e in graph["edges"] if e["type"] == "RETURNS_TO")
        assert edge["confidence"] == 0.0 and edge["evidence"] == "Synthetic loop"
    context = q.edge_context(owner=OWNER, src=src, dst=dst, etype="RETURNS_TO", cfg=cfg, store=st)
    assert not context["executed"] and context["error"] == "unsupported edge type: RETURNS_TO"


def test_seed_join_preserves_concept_budget_and_does_not_expand_again(reads, tmp_path):
    _, _, _ = reads  # Reuse the fixture's PK mode, but keep this graph isolated.
    cfg = load_config({"data_dir": str(tmp_path / "budget-db")})
    st = open_store(cfg)
    assert st is not None
    try:
        st.provision(dim=cfg.embedding_dim)
        nodes = [
            {"id": f"concept:{name}", "type": "concept", "title": name,
             "domain": f"budget/{name}"} for name in ("root", "near", "far", "hidden")
        ] + [{"id": "decision:joined", "type": "decision", "title": "joined", "domain": "budget/decision"}]
        edges = [
            {"from": "concept:root", "to": "concept:near", "type": "relates_to"},
            {"from": "concept:near", "to": "concept:far", "type": "relates_to"},
            {"from": "concept:near", "to": "decision:joined", "type": "derives_from"},
            {"from": "decision:joined", "to": "concept:hidden", "type": "derives_from"},
            {"from": "decision:joined", "to": "concept:near", "type": "about"},
        ]
        result = frag.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges}, store=st)
        assert result["executed"] and not result.get("warnings"), result
        graph = q.graph(owner=OWNER, seed="concept:root", depth=2, max_nodes=1, cfg=cfg, store=st)
        assert graph["executed"] and graph["depth"] == 1, graph
        assert graph["depth_budget"] == {"requested_depth": 2, "served_depth": 1,
                                         "reach_at_requested": 2, "reach_served": 1, "max_nodes": 1}
        assert {n["id"] for n in graph["nodes"]} == {"concept:root", "concept:near", "decision:joined"}
        triples = [(e["source"], e["target"], e["type"]) for e in graph["edges"]]
        assert set(triples) == {("concept:root", "concept:near", "RELATED_TO"),
                                ("concept:near", "decision:joined", "DERIVES_FROM"),
                                ("decision:joined", "concept:near", "ABOUT")}
        assert len(triples) == 3
    finally:
        st.close()


@pytest.mark.parametrize("src_label,dst_label", PAIRS)
def test_existing_semantic_supersedes_stays_visible(reads, src_label, dst_label):
    st, cfg, _ = reads
    src, dst = SRC[src_label], DST[dst_label]
    sp, dp = q._SEMANTIC_PK[src_label], q._SEMANTIC_PK[dst_label]
    st.run(f"MATCH (a:{src_label}), (b:{dst_label}) "
           f"WHERE a.{sp}=$src AND b.{dp}=$dst "
           "MERGE (a)-[r:SUPERSEDES]->(b) SET r.evidence='Synthetic supersession', r.confidence=0.0",
           {"src": pkns.key(OWNER, src), "dst": pkns.key(OWNER, dst)})
    for node_id, other, direction in ((src, dst, "out"), (dst, src, "in")):
        node = q.node_context(owner=OWNER, node=node_id, with_evidence=True, cfg=cfg, store=st)
        link = next(e for e in node["links"]["items"]
                    if e["id"] == other and e["type"] == "SUPERSEDES" and e["dir"] == direction)
        assert link["evidence_text"] == "Synthetic supersession" and link["confidence"] == 0.0
    for kwargs in ({}, {"seed": src, "depth": 1}):
        graph = q.graph(owner=OWNER, cfg=cfg, store=st, **kwargs)
        found = [e for e in graph["edges"] if (e["source"], e["target"], e["type"]) == (src, dst, "SUPERSEDES")]
        assert len(found) == 1 and found[0]["confidence"] == 0.0, graph
    ctx = q.edge_context(owner=OWNER, src=src, dst=dst, etype="SUPERSEDES", cfg=cfg, store=st)
    assert not ctx["executed"] and ctx["error"] == "unsupported edge type: SUPERSEDES"
