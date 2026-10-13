"""Synthetic-only tests for temporal metadata, explicit modes and claim safety."""
import json

import pytest

from api import docstore, fragment, query
from store.backend import open_store
from store.config import load_config
from store import search_temporal as temporal, pkns
from vinc_rag import retrieval
from vinc_mcp import tools

OWNER = "user:synthetic-temporal"


def test_later_today_successor_does_not_retire_current_evidence(st, monkeypatch):
    real_datetime = temporal.dt.datetime
    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 13, 12, tzinfo=temporal.dt.timezone.utc)
    monkeypatch.setattr(temporal.dt, "datetime", Clock)
    ingest(st, [node("today-old", vf="2026-10-13"),
                node("today-new", vf="2026-10-13", value="b")],
           [{"from": "record:today-new", "to": "record:today-old",
             "type": "supersedes", "evidence": "Synthetic later ruling"}])
    # Record ingestion accepts date granularity; test preexisting timestamp metadata
    # directly in this isolated synthetic store without changing the write contract.
    from store import pkns
    for suffix, hour in (("old", "00"), ("new", "20")):
        st.run("MATCH (n:Record) WHERE n.record_id=$id SET n.valid_from=$value",
               {"id": pkns.key(OWNER, "record:today-" + suffix),
                "value": "2026-10-13T" + hour + ":00:00Z"})
    result = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert [h["id"] for h in result["node_hits"]] == ["record:today-old"], json.dumps(result)
    assert result["node_hits"][0]["temporal"]["claim_status"] == "candidate"
    assert query.brief("record:today-new", owner=OWNER, store=st, mode="current")["status"] == "none"


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_document_mirror_lineage_reaches_chunks_and_get(st, detail):
    for suffix, vf in (("old", "2000"), ("new", "2002")):
        docstore.put_doc(st, owner=OWNER, doc_id="synthetic." + suffix,
                         text="Marker synthetic passage " * 20, title="Marker " + suffix, valid_from=vf)
    nodes = [{"id": "concept:doc.synthetic." + suffix, "type": "concept",
              "title": "Marker " + suffix, "props": {"entity": "service", "predicate": "release",
                "scope": "synthetic", "value": suffix}} for suffix in ("old", "new")]
    ingest(st, nodes, [{"from": nodes[1]["id"], "to": nodes[0]["id"],
                       "type": "supersedes", "evidence": "Synthetic replacement"}])
    history = query.search("Marker", owner=OWNER, store=st, mode="historical", detail=detail, top_k=20)
    old = next(r for r in history["rows"] if r["doc_id"] == "synthetic.old")
    assert old["temporal"]["claim_status"] == "superseded"
    current = query.search("Marker", owner=OWNER, store=st, mode="current", detail=detail, top_k=20)
    assert {r["doc_id"] for r in current["rows"]} == {"synthetic.new"}
    direct = docstore.get_doc(st, "synthetic.old", owner=OWNER)
    assert direct["verified"] and direct["temporal"]["supersession"] == old["temporal"]["supersession"]
    context = query.node_context(owner=OWNER, node="concept:doc.synthetic.old", store=st)
    assert context["executed"]
    assert context["temporal"]["supersession"] == old["temporal"]["supersession"]
    from unittest.mock import patch
    with patch.object(st, "run", wraps=st.run) as run:
        exact = query.node("concept:doc.synthetic.old", owner=OWNER, store=st)
        assert run.call_count == 1
    assert all(exact["node"]["temporal"][k] == context["temporal"][k] for k in temporal.FIELDS)
    assert exact["node"]["temporal"]["claim_status"] == "unassessed"
    for excerpt in context.get("excerpts") or []:
        if excerpt.get("doc_id") == "synthetic.old":
            assert excerpt["temporal"]["supersession"] == old["temporal"]["supersession"]


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_current_page_limit_preserves_observed_document_conflict(st, detail):
    for suffix in ("a", "b"):
        docstore.put_doc(st, owner=OWNER, doc_id="synthetic.conflict." + suffix,
                         text="Marker synthetic conflicting passage " * 20,
                         title="Marker conflict " + suffix, valid_from="2000")
    ingest(st, [{"id": "concept:doc.synthetic.conflict." + suffix, "type": "concept",
                 "title": "Marker conflict " + suffix,
                 "props": {"entity": "service", "predicate": "release",
                           "scope": "synthetic", "value": suffix}} for suffix in ("a", "b")])
    result = query.search("Marker", owner=OWNER, store=st, mode="current", detail=detail, top_k=1)
    assert len(result["rows"]) == 1 and result["has_more"]
    assert result["rows"][0]["temporal"]["claim_status"] == "conflict"
    assert result["rows"][0]["temporal"]["conflicts"]
    assert "_current_doc_temporal" not in result


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    cfg = load_config({"data_dir": str(tmp_path / "synthetic-db")})
    store = open_store(cfg)
    assert store is not None
    store.provision(dim=cfg.embedding_dim)
    yield store
    store.close()


def node(nid, *, value="a", vf="2000", vu=None, scope="production", kind="record"):
    p = {"entity": "service", "predicate": "release", "scope": scope, "value": value}
    out = {"id": f"{kind}:{nid}", "type": kind, "title": f"Marker {nid}",
           "domain": "vinc/synthetic", "props": p}
    if kind == "decision":
        p.update(valid_from=vf, valid_until=vu)
    else:
        out.update(valid_from=vf, valid_until=vu)
    return out


def ingest(st, nodes, edges=(), owner=OWNER):
    res = fragment.merge_fragment({"owner": owner, "nodes": nodes, "edges": list(edges)}, store=st)
    assert res["executed"], res
    assert not res.get("error"), res
    return res


@pytest.mark.parametrize("vf,vu,now,state", [
    (None, None, "2026-10-13", "unknown"),
    ("2026-10-13", "2026-10-14", "2026-10-13", "current"),
    ("2026-10-13", "2026-10-14", "2026-10-14", "historical"),
    ("2026-10-14", None, "2026-10-13", "future"),
    ("2026", "2027", "2027", "historical"),
    ("2026-10-13T09:00:00+09:00", None, "2026-10-13T00:00:00Z", "current"),
    ("2026-10-13T00:00:00Z", "2026-10-13T09:00:00+09:00", "2026-10-13", "unknown"),
    ("broken", None, "2026-10-13", "unknown"),
])
def test_half_open_dates_and_timezones(vf, vu, now, state):
    got = temporal.metadata({"valid_from": vf, "valid_until": vu}, now=now)
    assert got["validity"] == state
    assert got["updated_at"] is None


@pytest.mark.parametrize("kind", ["record", "decision"])
def test_modes_and_unknown_keep_legacy_behavior(st, kind):
    ingest(st, [node("old", vu="2001", kind=kind), node("future", vf="2099", kind=kind),
                node("current", kind=kind), node("unknown", vf=None, kind=kind)])
    legacy = query.search("Marker", store=st, owner=OWNER, top_k=20)
    general = query.search("Marker", store=st, owner=OWNER, top_k=20, mode="general")
    assert [h["id"] for h in legacy["node_hits"]] == [h["id"] for h in general["node_hits"]]
    assert f"{kind}:future" in {h["id"] for h in legacy["node_hits"]}
    current = query.search("Marker", store=st, owner=OWNER, top_k=20, mode="current")
    assert {h["id"] for h in current["node_hits"]} == {f"{kind}:current", f"{kind}:unknown"}
    unknown = next(h for h in current["node_hits"] if h["id"].endswith("unknown"))
    assert unknown["temporal"]["validity"] == "unknown"
    assert unknown["temporal"]["claim_status"] == "date_unknown"
    history = query.search("Marker", store=st, owner=OWNER, top_k=20, mode="historical")
    assert len(history["node_hits"]) == 4
    assert all("created_at" in h["temporal"] for h in history["node_hits"])
    past = query.search("Marker", store=st, owner=OWNER, top_k=20, mode="historical", as_of="2000")
    assert f"{kind}:old" in {h["id"] for h in past["node_hits"]}
    assert f"{kind}:future" not in {h["id"] for h in past["node_hits"]}


@pytest.mark.parametrize("kw", [{"mode": "bad"}, {"mode": "current", "historical": True},
                               {"mode": "current", "as_of": "2020"}, {"known_at": "bad"}])
def test_explicit_invalid_requests_error(st, kw):
    res = query.search("Marker", store=st, owner=OWNER, **kw)
    assert res["error"].startswith("invalid_")
    assert not res["executed"]


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_document_metadata_and_revision_edit(st, detail):
    body = "# Marker\n\nSynthetic only. " * 60
    docstore.put_doc(st, owner=OWNER, doc_id="synthetic.old", text=body, title="Marker old", valid_from="2000")
    before = docstore.get_doc(st, "synthetic.old", owner=OWNER)
    docstore.put_doc(st, owner=OWNER, doc_id="synthetic.old", text=body + "Edited.", title="Marker old")
    after = docstore.get_doc(st, "synthetic.old", owner=OWNER)
    assert before["created_at"] == after["created_at"]
    assert after["temporal"]["valid_from"] == "2000"
    assert after["text"] == body + "Edited."
    docstore.put_doc(st, owner=OWNER, doc_id="synthetic.new", text="Marker newer unrelated.", title="Marker new")
    docstore.put_doc(st, owner=OWNER, doc_id="synthetic.expired", text="Marker expired.", valid_until="2001")
    result = query.search("Marker", owner=OWNER, store=st, detail=detail, mode="historical", top_k=20)
    row = next(r for r in result["rows"] if r["doc_id"] == "synthetic.old")
    assert row["temporal"]["created_at"] == after["temporal"]["created_at"]
    assert row["temporal"]["updated_at"] == after["temporal"]["updated_at"]
    assert row["temporal"]["validity"] == "current"
    assert row["temporal"]["supersession"] == []
    current = query.search("Marker", owner=OWNER, store=st, detail=detail, mode="current", top_k=20)
    assert all(r["doc_id"] != "synthetic.expired" for r in current["rows"])
    assert docstore.get_doc(st, "synthetic.expired", owner=OWNER)["verified"]


@pytest.mark.parametrize("kind", ["record", "decision"])
def test_explicit_supersession_and_conflicts(st, kind):
    ingest(st, [node("old", value="a", kind=kind), node("new", value="b", vf="2002", kind=kind)],
           [{"from": f"{kind}:new", "to": f"{kind}:old", "type": "supersedes", "evidence": "Synthetic ruling."}])
    history = query.search("Marker", owner=OWNER, store=st, mode="historical", top_k=20)
    old = next(h for h in history["node_hits"] if h["id"] == f"{kind}:old")
    assert old["temporal"]["claim_status"] == "superseded", history
    current = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert f"{kind}:old" not in {h["id"] for h in current["node_hits"]}, current
    ingest(st, [node("conflict", value="c", kind=kind)])
    result = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert all(h["temporal"]["claim_status"] == "conflict" for h in result["node_hits"]), result
    assert all(h["temporal"]["conflicts"] for h in result["node_hits"])


def test_pointer_without_evidence_and_different_scope_do_not_retire(st):
    old = node("old")
    old["props"]["superseded_by"] = "record:new"
    ingest(st, [old, node("new", vf="2002", scope="elsewhere")])
    result = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert len(result["node_hits"]) == 2
    assert all(h["temporal"]["claim_status"] != "superseded" for h in result["node_hits"])


def test_cycle_keeps_candidates(st):
    ingest(st, [node("a"), node("b", value="b")], [
        {"from": "record:a", "to": "record:b", "type": "supersedes", "evidence": "Synthetic a."},
        {"from": "record:b", "to": "record:a", "type": "supersedes", "evidence": "Synthetic b."}])
    result = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert len(result["node_hits"]) == 2
    assert all(h["temporal"]["claim_status"] == "cycle" for h in result["node_hits"]), result


def test_owner_and_namespaced_scope_isolation(st, monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    ingest(st, [node("same", value="a")], owner=OWNER)
    ingest(st, [node("same", value="b", vu="2001")], owner="team:synthetic-other")
    result = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert len(result["node_hits"]) == 1
    assert result["node_hits"][0]["temporal"]["validity"] == "current"
    assert result["node_hits"][0]["temporal"]["conflicts"] == []


def test_node_detail_brief_and_semantic_metadata(st, monkeypatch):
    ingest(st, [node("selected")])
    context = query.node_context(owner=OWNER, node="record:selected", store=st)
    brief = query.brief("record:selected", owner=OWNER, store=st)
    assert context["temporal"] == brief["node"]["temporal"]
    class SemanticStore:
        def run(self, cypher, params=None):
            return st.run(cypher, params)
        def fulltext_search(self, *args, **kw):
            return []
        def vector_search(self, *args, **kw):
            return []
        def node_vector_search(self, *args, **kw):
            return [{"id": "record:selected", "title": "Marker selected", "kind": "record", "distance": .2}]
    result = query.search("unlexical", owner=OWNER, store=SemanticStore(), query_embedding=[.1], mode="historical")
    assert result["node_hits"][0]["temporal"] == context["temporal"], result
    assert result["node_hits"][0]["matched_in"] == "semantic"


def test_current_relevance_before_same_claim_recency():
    sources = {"a": node("a"), "b": node("b"), "c": node("c", scope="other")}
    hits = [{"id": "a", "kind": "record", "score": 10, "valid_from": "2000"},
            {"id": "b", "kind": "record", "score": 10, "valid_from": "2002"},
            {"id": "c", "kind": "record", "score": 1, "valid_from": "2025", "updated_at": "2099"}]
    for h in hits:
        temporal.stamp(h)
    temporal.current_order(hits, sources)
    assert [h["id"] for h in hits] == ["b", "a", "c"]


def test_cursor_binds_modes_historical_known_asof_and_owner(st):
    for i in range(3):
        docstore.put_doc(st, owner=OWNER, doc_id=f"page{i}", text=f"Marker synthetic page{i}.", valid_from="2000")
    first = query.search("Marker", owner=OWNER, store=st, top_k=1, mode="historical")
    assert first["next_cursor"]
    second = query.search("Marker", owner=OWNER, store=st, top_k=1, mode="historical", cursor=first["next_cursor"])
    assert second["node_hits"] == []
    assert first["rows"][0]["id"] != second["rows"][0]["id"]
    for change in ({"mode": "current"}, {"mode": "general"}, {"historical": True},
                   {"known_at": "2026"}, {"as_of": "2000"}, {"owner": "team:other"}):
        res = query.search("Marker", store=st, top_k=1, cursor=first["next_cursor"],
                           **{**{"mode": "historical", "owner": OWNER}, **change})
        assert res["error"].startswith("invalid_cursor"), res


def test_known_at_is_creation_cutoff_not_revision_restore(st):
    docstore.put_doc(st, owner=OWNER, doc_id="known", text="Marker current revised text.")
    created = docstore.get_doc(st, "known", owner=OWNER)["created_at"]
    current = query.search("Marker", owner=OWNER, store=st, known_at="2099", mode="historical", detail="full")
    assert "current revised text" in current["rows"][0]["text"]
    assert current["rows"][0]["temporal"]["created_at"] == created
    past = query.search("Marker", owner=OWNER, store=st, known_at="2000", mode="historical")
    assert past["rows"] == []
    assert past["known_at_filtered"] >= 1


def test_mcp_mode_and_openapi_contract():
    class Bridge:
        owner = OWNER
        def request(self, method, path, params):
            return params
    result = tools._t_search({"query": "Marker", "mode": "current", "known_at": "2026"}, Bridge())
    assert result["mode"] == "current" and result["owner"] == OWNER
    from vinc_mcp.openapi_v1 import generate, document
    assert generate() == document()
    props = document()["paths"]["/v1/search"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert props["mode"]["enum"] == ["general", "current", "historical"]


def test_current_brief_abstains_on_conflict_and_missing_dates(st):
    ingest(st, [node("a"), node("b", value="b"), node("unknown", vf=None)])
    conflict = query.brief("Marker a", owner=OWNER, store=st, mode="current")
    assert conflict["status"] == "choose", conflict
    assert any(c["temporal"]["claim_status"] == "conflict" for c in conflict["candidates"])
    unknown = query.brief("record:unknown", owner=OWNER, store=st, mode="current")
    assert unknown["status"] == "choose", unknown
    assert query.brief("record:unknown", owner=OWNER, store=st)["status"] == "resolved"


def test_successor_not_in_title_candidates_still_has_metadata(st):
    old, new = node("old"), node("new", value="b", vf="2002")
    old["title"], new["title"] = "Specific old marker", "Replacement only"
    ingest(st, [old, new], [{"from": "record:new", "to": "record:old", "type": "supersedes", "evidence": "Synthetic ruling."}])
    history = query.search("Specific", owner=OWNER, store=st, mode="historical")
    assert history["node_hits"][0]["temporal"]["claim_status"] == "superseded"
    assert query.search("Specific", owner=OWNER, store=st, mode="current")["node_hits"] == []
    current = query.search("Replacement", owner=OWNER, store=st, mode="current")
    assert current["node_hits"][0]["temporal"]["supersedes"][0]["old"] == "record:old"


def test_no_evidence_and_disjoint_periods_cannot_supersede_or_conflict(st):
    ingest(st, [node("old", vu="2001"), node("new", value="b", vf="2002")])
    st.run("MATCH (a:Record), (b:Record) WHERE a.record_id = $new AND b.record_id = $old "
           "MERGE (a)-[e:SUPERSEDES]->(b) SET e.evidence = ''",
         {"new": pkns.key(OWNER, "record:new"), "old": pkns.key(OWNER, "record:old")})
    history = query.search("Marker", owner=OWNER, store=st, mode="historical", as_of="2000")
    old = history["node_hits"][0]
    assert old["temporal"]["claim_status"] != "superseded"
    assert old["temporal"]["supersession"][0]["status"] == "unsupported"
    # 넓은 기간 조회에서도 서로 겹치지 않는 주장을 충돌로 만들지 않는다.
    res = query.search("Marker", owner=OWNER, store=st, mode="historical")
    assert all(h["temporal"]["conflicts"] == [] for h in res["node_hits"])


def test_metadata_read_failure_and_existing_degraded_warning_are_explicit(st, monkeypatch):
    ingest(st, [node("one")])
    monkeypatch.setattr(temporal, "node_sources", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic metadata failure")))
    res = query.search("Marker", owner=OWNER, store=st, mode="historical")
    assert res["partial"] and "synthetic metadata failure" in res["temporal_error"]
    assert res["node_hits"][0]["temporal"]["validity"] == "unknown"


def test_document_mirror_uses_document_interval_without_expiring_whole_old_doc(st):
    docstore.put_doc(st, owner=OWNER, doc_id="mirror-old", title="Marker old", text="Marker old text.", valid_from="2000", topic="vinc/synthetic")
    docstore.put_doc(st, owner=OWNER, doc_id="mirror-new", title="Marker new", text="Marker new text.", valid_from="2002", topic="vinc/synthetic")
    # 새 문서의 존재 자체는 이전 문서의 유효 종료 증거가 아니다.
    res = query.search("Marker", owner=OWNER, store=st, mode="current", top_k=20)
    assert {r["doc_id"] for r in res["rows"]} == {"mirror-old", "mirror-new"}
    mirrors = [h for h in res["node_hits"] if h["id"].startswith("concept:doc.")]
    assert mirrors and all(h["temporal"]["valid_from"] is not None for h in mirrors)


def test_temporal_metadata_never_enters_embedding_inputs(st):
    from api import node_mirror
    from vinc_rag.embed.worker import prefix
    body = "# Marker\n\nOriginal synthetic body without metadata."
    docstore.put_doc(st, owner=OWNER, doc_id="embedding-input", title="Marker source", text=body,
                    topic="vinc/synthetic", valid_from="1991", valid_until="1992")
    ingest(st, [node("embedding-source", vf="1993", vu="1994")])
    pending_before = [r["text"] for r in docstore.pending_embed_chunks(st)]
    mirrors_before, _ = node_mirror.collect(st, OWNER)
    original = docstore.get_doc(st, "embedding-input", owner=OWNER)
    docstore.put_doc(st, owner=OWNER, doc_id="embedding-input", title="Marker source", text=body,
                    valid_from="1995", valid_until="2099", superseded_by="synthetic-next")
    for mode in (None, "general", "current", "historical"):
        query.search("Marker", owner=OWNER, store=st, mode=mode, top_k=20)
    current = docstore.get_doc(st, "embedding-input", owner=OWNER)
    mirrors_after, _ = node_mirror.collect(st, OWNER)
    assert current["text"] == original["text"] == body
    assert current["content_hash"] == original["content_hash"]
    assert [r["text"] for r in docstore.pending_embed_chunks(st)] == pending_before
    assert mirrors_after == mirrors_before
    have = {node_mirror.vec_id(r["owner"], r["id"]): (r["text"], "synthetic-model", "synthetic-runtime") for r in mirrors_before}
    assert node_mirror.plan(mirrors_after, have, "synthetic-model", "synthetic-runtime") == ([], [])
    assert all(prefix(text, "passage", "BAAI/bge-m3") == text for text in pending_before)
    assert all("1991" not in r["text"] and "1995" not in r["text"] for r in mirrors_after)


@pytest.mark.parametrize("known,expected", [("2020", ("2020-01-01", "2021-01-01")),
    ("2020-02", ("2020-02-01", "2020-03-01")), ("2020-02-29", ("2020-02-29", "2020-03-01"))])
def test_known_at_keeps_existing_period_response(st, known, expected):
    result = query.search("Marker", owner=OWNER, store=st, known_at=known)
    scope = result["time_scope"]["known_at"]
    assert (scope["from"], scope["until"]) == expected
    assert scope["value"] == known


def test_dense_conflicts_parse_each_source_once_per_pass(monkeypatch):
    sources, hits = {}, []
    for i in range(100):
        nid = f"record:synthetic-{i}"
        source = {"kind": "record", "valid_from": "2000", "props_raw": json.dumps({
            "entity": "same", "predicate": "release", "scope": "synthetic", "value": i % 2})}
        sources[nid] = source
        hits.append({"id": nid, "kind": "record", "temporal": temporal.metadata(source)})
    parse = temporal._props_to_obj
    calls = []
    def tracked(raw):
        calls.append(raw)
        return parse(raw)
    monkeypatch.setattr(temporal, "_props_to_obj", tracked)
    temporal.annotate_claims(hits, sources, [])
    assert all(len(h["temporal"]["conflicts"]) == 50 for h in hits)
    assert all(h["temporal"]["claim_status"] == "conflict" for h in hits)
    assert len(calls) <= 2 * len(hits)


def test_native_semantic_metadata_is_owner_isolated(st, monkeypatch):
    from api import node_mirror
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    other = "team:synthetic-semantic-other"
    ingest(st, [node("shared-semantic", vf="2000")], owner=OWNER)
    ingest(st, [node("shared-semantic", vf="2000", vu="2001", value="foreign")], owner=other)
    vector = [1.0] + [0.0] * (load_config().embedding_dim - 1)
    rows = [{"vid": node_mirror.vec_id(owner, "record:shared-semantic"), "owner": owner,
             "node": "record:shared-semantic", "embedding": vector} for owner in (OWNER, other)]
    st.run("UNWIND $rows AS r CREATE (:NodeVec {vec_id:r.vid, node_id:r.node, owner_id:r.owner, "
           "kind:'record', title:'Marker shared semantic', text:'Marker shared semantic', "
           "model:'synthetic', embedding:r.embedding})", {"rows": rows})
    note = st.create_node_vector_index()
    assert not note, note
    result = query.search("paraphrase-unlexical", owner=OWNER, store=st,
                          query_embedding=vector, mode="current")
    assert len(result["node_hits"]) == 1, result
    hit = result["node_hits"][0]
    assert hit["matched_in"] == "semantic"
    assert hit["temporal"]["validity"] == "current"
    assert hit["temporal"]["valid_until"] is None
    assert hit["temporal"]["conflicts"] == []
    past = query.search("paraphrase-unlexical", owner=other, store=st,
                        query_embedding=vector, mode="historical")
    assert past["node_hits"][0]["temporal"]["validity"] == "historical", past


def test_invalid_interval_cannot_be_supersession_evidence():
    props = json.dumps({"entity": "synthetic", "predicate": "release", "scope": "same", "value": "a"})
    sources = {"record:old": {"kind": "record", "props_raw": props, "valid_from": "2099", "valid_until": "2000"},
               "record:new": {"kind": "record", "props_raw": props, "valid_from": "1990"}}
    hits = [{"id": nid, "kind": "record", "temporal": temporal.metadata(source)} for nid, source in sources.items()]
    retired = temporal.annotate_claims(hits, sources, [{"new": "record:new", "old": "record:old", "evidence": "synthetic ruling"}])
    assert retired == set()
    old = hits[0]["temporal"]
    assert old["validity"] == "unknown" and old["interval_invalid"]
    assert not old["interval_known"] and old["claim_status"] != "superseded"
    assert old["supersession"][0]["status"] == "uncomparable"


def test_explicit_period_uses_utc_offsets_and_half_open_end(st):
    ingest(st, [node("offset", vf="2026-10-12")])
    st.run("MATCH (n:Record) WHERE n.record_id=$id SET n.valid_from=$vf, n.valid_until=$vu",
           {"id": pkns.key(OWNER, "record:offset"),
            "vf": "2026-10-13T00:30:00+09:00", "vu": "2026-10-13T09:00:00+09:00"})
    before = query.search("Marker", owner=OWNER, store=st, mode="historical", as_of="2026-10-12")
    assert [h["id"] for h in before["node_hits"]] == ["record:offset"]
    assert before["node_hits"][0]["temporal"]["holds_in_scope"] is True
    at_end = query.search("Marker", owner=OWNER, store=st, mode="historical", as_of="2026-10-13")
    assert at_end["node_hits"] == []
    assert query.brief("record:offset", owner=OWNER, store=st, mode="historical",
                       as_of="2026-10-12")["status"] == "resolved"
    assert query.brief("record:offset", owner=OWNER, store=st, mode="historical",
                       as_of="2026-10-13")["status"] == "none"
