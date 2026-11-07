"""VIN-590 — brief·graph·neighbors·edges 가 같은 그래프를 같게 읽고 시간 인자를 지킨다.

감사 2026-10-16 의 읽기 표면 결함마다 회귀 하나씩이다(F2·F2b·F8·F10·F11·F12·F13·F14·F15,
B1·B2·B5·B7, D11·D12·D14·D15). 실엔진 스토어에서 PK 네임스페이싱 0·1 둘 다 돈다.
"""
from __future__ import annotations

import os
from itertools import count

import pytest

from api import docstore, fragment, query, server, temporal_read
from store.backend import open_store
from store.config import load_config

OWNER, OTHER = "user:vin590-reader", "user:vin590-other"
CASES = count()


@pytest.fixture(scope="module", params=["0", "1"])
def st(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("vin590") / "db")})
    store = open_store(cfg)
    try:
        store.provision(dim=cfg.embedding_dim)
        yield store
    finally:
        store.close()
        for k, value in old.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value


def tag() -> str:
    return f"c{next(CASES)}"


def ingest(st, nodes, edges=(), owner=OWNER):
    res = fragment.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)},
                                  store=st, authored=True, read_back=True)
    assert res["executed"] and not res.get("error"), res
    return res


def concept(nid, title, **kw):
    return {"id": nid, "type": "concept", "title": title, "domain": "vinc/test", **kw}


def record(nid, title, **kw):
    return {"id": nid, "type": "record", "title": title, "domain": "vinc/test", **kw}


def brief_links(st, nid, **kw):
    out = query.brief(nid, owner=OWNER, store=st, **kw)
    assert out["status"] == "resolved", out
    return out["links"]


# ── F2 · F2b · F8 · F15 ──────────────────────────────────────────────────────

def test_f2_brief_graph_and_neighbors_count_the_same_relations(st):
    t = tag()
    c, d, r_old, r_new, r_dec = (f"concept:{t}-c", f"decision:{t}-d", f"record:{t}-old",
                                 f"record:{t}-new", f"record:{t}-on-dec")
    b = f"bridge:{t}-b"
    ingest(st, [concept(c, f"Hub {t}"),
                {"id": d, "type": "decision", "title": f"Rule {t}", "domain": "vinc/test"},
                record(r_old, f"Old {t}"), record(r_new, f"New {t}"),
                record(r_dec, f"On decision {t}"),
                {"id": b, "type": "bridge", "title": f"Bridge {t}"}],
           [{"from": d, "type": "about", "to": c, "evidence": "rule about hub"},
            {"from": r_new, "type": "supersedes", "to": r_old, "evidence": "replaced"},
            {"from": r_new, "type": "relates_to", "to": c, "evidence": "new on hub"},
            {"from": r_old, "type": "relates_to", "to": c},
            {"from": r_dec, "type": "derives_from", "to": d, "evidence": "rec->dec",
             "confidence": 0.42},
            {"from": b, "type": "connects_via", "to": d, "role": "out"}])
    for nid in (c, d, r_old, r_new, r_dec):
        links = brief_links(st, nid)
        count_ = query.neighbors(nid, owner=OWNER, store=st)["count"]
        assert links["total"] == count_, (nid, links, count_)
    assert {(i["type"], i["id"], i["dir"]) for i in brief_links(st, r_old)["items"]} == {
        ("ATTACHES_TO", c, "out"), ("SUPERSEDES", r_new, "in")}
    edges = {(e["source"], e["type"], e["target"]): e
             for e in query.graph(owner=OWNER, store=st)["edges"]}
    assert (r_new, "SUPERSEDES", r_old) in edges
    assert (b, "CONNECTS", d) in edges
    # F2b — 부착의 근거·신뢰도가 그래프(그리고 그것을 쓰는 내보내기)까지 간다.
    attach = edges[(r_dec, "ATTACHES_TO", d)]
    assert attach["evidence"] == "rec->dec" and attach["confidence"] == 0.42, attach
    seeded = query.graph(owner=OWNER, seed=r_new, depth=1, store=st)
    assert {(e["source"], e["type"], e["target"]) for e in seeded["edges"]} == {
        (r_new, "SUPERSEDES", r_old), (r_new, "ATTACHES_TO", c)}
    old_node = next(n for n in seeded["nodes"] if n["id"] == r_old)
    assert old_node["kind"] == "record"


def test_f2_record_links_keep_attachments_on_a_store_without_supersedes():
    """옛 스토어(고정 평가 스토어가 그렇다)에는 SUPERSEDES 표가 없다. 그 팔이 죽어도
    부착은 남고, 못 읽은 것은 표식으로 말한다."""
    class Store:
        def run(self, cypher, params=None):
            if "SUPERSEDES" in cypher:
                raise RuntimeError("Binder exception: Table SUPERSEDES does not exist.")
            return [{"id": "concept:a", "title": "A", "domain": "", "type": "ATTACHES_TO",
                     "dir": "out", "ev": "", "conf": None, "valid_from": "", "valid_until": ""}]
    links = query._record_links(Store(), "record:x", owner=OWNER)
    assert links["executed"] and links["total"] == 1, links
    assert links["supersession_unavailable"] is True


def test_f8_unset_confidence_stays_none_and_sorts_after_authored_zero(st):
    t = tag()
    c = f"concept:{t}-c"
    ingest(st, [concept(c, f"Hub {t}"), concept(f"concept:{t}-zero", f"Zero {t}"),
                concept(f"concept:{t}-unset", f"Unset {t}")],
           [{"from": c, "type": "relates_to", "to": f"concept:{t}-unset"},
            {"from": c, "type": "relates_to", "to": f"concept:{t}-zero", "confidence": 0.0}])
    items = brief_links(st, c)["items"]
    assert [(i["id"], i["confidence"]) for i in items] == [
        (f"concept:{t}-zero", 0.0), (f"concept:{t}-unset", None)]
    edges = query.node_edges(c, owner=OWNER, store=st, type="RELATED_TO")["edges"]
    assert {e["to"]: e["confidence"] for e in edges} == {
        f"concept:{t}-zero": 0.0, f"concept:{t}-unset": None}


def test_f15_edges_carry_created_and_role_and_neighbors_carry_direction(st):
    t = tag()
    c, b = f"concept:{t}-c", f"bridge:{t}-b"
    ingest(st, [concept(c, f"Hub {t}"), concept(f"concept:{t}-x", f"X {t}"),
                {"id": b, "type": "bridge", "title": f"Bridge {t}"}],
           [{"from": b, "type": "connects_via", "to": c, "role": "input"},
            {"from": c, "type": "relates_to", "to": f"concept:{t}-x",
             "created": "2025-05-05T00:00:00.000Z"},
            {"from": c, "type": "prerequisite_of", "to": f"concept:{t}-x"},
            {"from": f"concept:{t}-x", "type": "prerequisite_of", "to": c}])
    edge = query.node_edges(b, owner=OWNER, store=st)["edges"][0]
    assert edge["role"] == "input" and edge["created"], edge
    rel = next(e for e in query.node_edges(c, owner=OWNER, store=st, type="RELATED_TO",
                                           dir="out")["edges"])
    assert rel["created"] == "2025-05-05T00:00:00.000Z", rel
    rows = {(r["rel"], r["id"]): r for r in query.neighbors(c, owner=OWNER, store=st)["neighbors"]}
    assert rows[("CONNECTS", b)]["dir"] == "in"
    assert rows[("RELATED_TO", f"concept:{t}-x")]["dir"] == "out"
    # 같은 관계가 양쪽 방향에 다 있으면 한 줄이고 dir 이 both 다(count 의 뜻은 그대로).
    assert rows[("PREREQUISITE_OF", f"concept:{t}-x")]["dir"] == "both"
    assert query.neighbors(b, owner=OWNER, store=st)["neighbors"][0]["dir"] == "out"


# ── F10 · F11 · F12 ─────────────────────────────────────────────────────────

def test_f10_graph_keeps_concept_content_time_on_episode_and_bridge_axes(st):
    t = tag()
    c, e, b = f"concept:{t}-c", f"episode:{t}-e", f"bridge:{t}-b"
    ingest(st, [concept(c, f"Timed {t}", content_time="2025-03"),
                {"id": e, "type": "episode", "title": f"Episode {t}", "domain": "vinc/test",
                 "date": "2026-03-04", "summary": "s"},
                {"id": b, "type": "bridge", "title": f"Bridge {t}"}],
           [{"from": e, "type": "about", "to": c}, {"from": b, "type": "connects_via", "to": c}])
    for seed in (e, b):
        nodes = {n["id"]: n for n in query.graph(owner=OWNER, seed=seed, depth=1,
                                                  store=st)["nodes"]}
        assert nodes[c]["content_ts"] == "2025-03", (seed, nodes[c])


def test_f11_node_context_returns_episode_props(st):
    t = tag()
    e = f"episode:{t}-e"
    ingest(st, [concept(f"concept:{t}-c", f"Topic {t}"),
                {"id": e, "type": "episode", "title": f"Episode {t}", "domain": "vinc/test",
                 "date": "2026-03-04", "summary": "summary text", "props": {"gamma": 3}}],
           [{"from": e, "type": "about", "to": f"concept:{t}-c"}])
    ctx = query.node_context(owner=OWNER, node=e, store=st)
    assert ctx["props"] == {"gamma": 3}, ctx


def test_f12_neighbors_and_brief_open_an_entity(st):
    t = tag()
    a, b = f"theory:{t}-a", f"theory:{t}-b"
    ingest(st, [{"id": a, "type": "theory", "title": f"Theory A {t}"},
                {"id": b, "type": "theory", "title": f"Theory B {t}"}],
           [{"from": a, "type": "cites", "to": b}])
    out = query.neighbors(a, owner=OWNER, store=st)
    assert [(r["rel"], r["id"], r["dir"], r.get("subtype")) for r in out["neighbors"]] == [
        ("TYPED", b, "out", "cites")]
    back = query.neighbors(b, owner=OWNER, store=st)["neighbors"]
    assert [(r["id"], r["dir"]) for r in back] == [(a, "in")]
    got = query.brief(f"what does {a} cite", owner=OWNER, store=st)
    assert got["status"] == "resolved" and got["node"]["id"] == a, got
    assert got["node"]["kind"] == "entity" and got["links"]["total"] == out["count"]
    assert got["links"]["items"][0]["subtype"] == "cites"
    # 실재하지 않는 개방 접두 조각은 "없다"가 아니라 랭킹으로 간다.
    other = query.brief(f"note:{t}-nowhere Theory A {t}", owner=OWNER, store=st)
    assert not other.get("unreadable"), other


# ── F13 · F14 ───────────────────────────────────────────────────────────────

def test_f13_retired_document_mirror_reads_historical_in_brief(st):
    t = tag()
    doc = f"vin590-{t}-old"
    put = docstore.put_doc(st, owner=OWNER, doc_id=doc, text="# Old\n\nRetired text.\n",
                           title=f"Old doc {t}", valid_until="2001-01",
                           superseded_by=f"vin590-{t}-new", topic="vinc/test")
    assert put["executed"], put
    out = query.brief(f"concept:doc.{doc}", owner=OWNER, store=st)
    node = out["node"]
    assert node["validity"] == "historical" and node["claim_validity"] == "historical", node
    assert node["valid_until"] == "2001-01" and node["superseded_by"] == f"vin590-{t}-new"
    assert "historical" in out["validity_note"] and f"vin590-{t}-new" in out["validity_note"]


def test_f14_as_of_hides_later_episodes_on_every_surface_and_filters_brief_links(st):
    t = tag()
    hub, e, rec, dec = (f"concept:{t}-hub", f"episode:{t}-late", f"record:{t}-early",
                        f"decision:{t}-rule")
    ingest(st, [concept(hub, f"Hub {t}"),
                {"id": e, "type": "episode", "title": f"Marker {t} late episode",
                 "domain": "vinc/test", "date": "2026-03-04", "summary": "s"},
                record(rec, f"Marker {t} early record", valid_from="2025-01",
                       valid_until="2026-01"),
                {"id": dec, "type": "decision", "title": f"Rule {t}", "domain": "vinc/test",
                 "props": {"valid_from": "2025-06", "valid_until": "2026-02"}}],
           [{"from": e, "type": "about", "to": hub}, {"from": rec, "type": "relates_to", "to": hub},
            {"from": dec, "type": "about", "to": hub}])
    nb = temporal_read.apply("neighbors", query.neighbors(hub, owner=OWNER, store=st),
                             store=st, owner=OWNER, as_of="2025-12", seed=hub)
    hits = {h["id"] for h in query.search(f"Marker {t}", owner=OWNER, store=st, top_k=20,
                                          as_of="2025-12")["node_hits"]}
    assert e not in {n["id"] for n in nb["neighbors"]} and e not in hits, hits
    assert rec in hits
    links = brief_links(st, hub, as_of="2025-12")
    ids = {i["id"] for i in links["items"]}
    assert ids == {n["id"] for n in nb["neighbors"]} == {rec, dec}, links
    assert links["total"] == nb["count"]
    plain = brief_links(st, hub)
    decision = next(i for i in plain["items"] if i["id"] == dec)
    assert (decision["valid_from"], decision["valid_until"]) == ("2025-06", "2026-02")
    # 사건 직참조도 날짜를 시작으로 읽는다.
    assert query.brief(e, owner=OWNER, store=st, as_of="2025-12")["status"] == "none"
    assert query.brief(e, owner=OWNER, store=st, as_of="2026-06")["status"] == "resolved"


# ── B1 · B2 · B5 · B7 ───────────────────────────────────────────────────────

def test_b1_brief_applies_known_at_to_ranking_and_direct_ids(st):
    t = tag()
    nid = f"record:{t}-zeta"
    ingest(st, [record(nid, f"Zeta {t} rollout plan", valid_from="2000")])
    ranked = query.brief(f"Zeta {t} rollout plan", owner=OWNER, store=st, known_at="2000")
    assert ranked["status"] != "resolved" and ranked["known_at_filtered"] >= 1, ranked
    assert ranked["time_scope"]["known_at"]["value"] == "2000"
    direct = query.brief(nid, owner=OWNER, store=st, known_at="2000")
    assert direct["status"] == "none" and direct["known_at_filtered"] == 1, direct
    assert query.brief(nid, owner=OWNER, store=st, known_at="2999")["status"] == "resolved"
    bad = query.brief(nid, owner=OWNER, store=st, known_at="last week")
    assert bad["error"].startswith("invalid_time_scope"), bad
    # 사이드카는 brief 를 다시 거르지 않는다. 후처리 모듈도 brief 를 받지 않는다.
    assert server._timed("brief", {"known_at": "2000", "owner": OWNER}, st, direct) is direct
    with pytest.raises(ValueError):
        temporal_read.apply("brief", {}, store=st, owner=OWNER, known_at="2000")


def test_b2_brief_excerpts_follow_document_validity(st):
    t = tag()
    c = f"concept:{t}-quasar"
    ingest(st, [concept(c, f"Quasar {t} engine")])
    put = docstore.put_doc(st, owner=OWNER, doc_id=f"vin590-{t}-retired",
                           text=f"The Quasar {t} engine retired design notes. " * 5,
                           title=f"Retired notes {t}", valid_from="2000", valid_until="2001")
    assert put["executed"], put
    default = query.brief(f"Quasar {t} engine", owner=OWNER, store=st)
    assert default["status"] == "resolved" and default["node"]["id"] == c, default
    assert default["excerpts"] == [] and default["validity_filtered"] >= 1, default
    history = query.brief(f"Quasar {t} engine", owner=OWNER, store=st, historical=True)
    assert {e["doc_id"] for e in history["excerpts"]} == {f"vin590-{t}-retired"}, history
    past = query.brief(f"Quasar {t} engine", owner=OWNER, store=st, as_of="2000-06")
    assert {e["doc_id"] for e in past["excerpts"]} == {f"vin590-{t}-retired"}, past
    later = query.brief(f"Quasar {t} engine", owner=OWNER, store=st, as_of="2005")
    assert later["excerpts"] == [], later


def test_b5_current_brief_ignores_unknown_dates_of_other_claims(st):
    t = tag()
    ingest(st, [record(f"record:{t}-window", f"Orion{t} release window", valid_from="2000",
                       props={"entity": f"orion{t}", "predicate": "window", "scope": "prod",
                              "value": "q3"}),
                concept(f"concept:{t}-orion", f"Orion{t}")])
    current = query.brief(f"When is the Orion{t} release window", owner=OWNER, store=st,
                          mode="current")
    assert current["status"] == "resolved", current
    assert current["node"]["id"] == f"record:{t}-window"
    # 같은 주장 키의 후보가 날짜를 모르면 여전히 기권한다.
    ingest(st, [record(f"record:{t}-window-b", f"Orion{t} release window again",
                       props={"entity": f"orion{t}", "predicate": "window", "scope": "prod",
                              "value": "q4"})])
    shaky = query.brief(f"When is the Orion{t} release window", owner=OWNER, store=st,
                        mode="current")
    assert shaky["status"] == "choose", shaky


def test_b7_fts_excerpt_fallback_is_owner_scoped_and_builds_the_index(st):
    t = tag()
    c = f"concept:{t}-nimbus"
    ingest(st, [concept(c, f"Nimbus{t} gateway")])
    docstore.put_doc(st, owner=OWNER, doc_id=f"vin590-{t}-a", link=False, title="A notes",
                     text=("Long background paragraph about unrelated operations. " * 20)
                     + f"The Nimbus{t} gateway routes traffic.")
    for i in range(8):
        docstore.put_doc(st, owner=OTHER, doc_id=f"vin590-{t}-b{i}", link=False,
                         title=f"B {i}", text=f"Nimbus{t} gateway. Nimbus{t} gateway notes.")
    ctx = query.node_context(owner=OWNER, node=c, store=st)
    assert ctx["arm"] == "fts" and not ctx.get("excerpt_error"), ctx
    assert {e["doc_id"] for e in ctx["excerpts"]} == {f"vin590-{t}-a"}, ctx


# ── D11 · D12 · D14 · D15 ───────────────────────────────────────────────────

def test_d11_current_brief_resolves_to_the_survivor_of_a_retired_tie(st):
    t = tag()
    ingest(st, [record(f"record:{t}-one", f"Marker{t} alpha one", valid_from="2000"),
                record(f"record:{t}-two", f"Marker{t} alpha two", valid_from="2000",
                       valid_until="2001")])
    general = query.brief(f"tell me about marker{t} alpha", owner=OWNER, store=st)
    assert general["status"] == "choose", general
    current = query.brief(f"tell me about marker{t} alpha", owner=OWNER, store=st,
                          mode="current")
    assert current["status"] == "resolved", current
    assert current["node"]["id"] == f"record:{t}-one"
    # D15 — current 의 알림은 as_of 를 말하지 않는다.
    assert "as_of" not in str(current.get("validity_note") or ""), current


def test_d12_expose_above_the_pool_is_capped_by_the_search_top_k(st, monkeypatch):
    t = tag()
    n = query._BRIEF_TOP_K + 3
    ingest(st, [record(f"record:{t}-{i}", f"Kappa{t} widget {i}") for i in range(n)])
    monkeypatch.setenv("VINC_BRIEF_EXPOSE", str(n))
    out = query.brief(f"Kappa{t} widget", owner=OWNER, store=st)
    assert out["status"] == "choose" and len(out["candidates"]) == query._BRIEF_TOP_K, out


def _hit(nid, hit=2, title=2, arms=("title",), distance=None, **kw):
    h = {"id": nid, "title": nid, "kind": "record", "hit_tokens": hit, "title_tokens": title,
         "arms": list(arms), **kw}
    if distance is not None:
        h["distance"] = distance
    return h


def test_resolution_reads_only_the_first_five_of_the_pool():
    """6번째부터는 되묻기 목록용이다. 그 자리에 토큰이 더 많은 행이 있어도 확정은 그대로다."""
    hits = [_hit("record:top", hit=4, title=4), _hit("record:b", hit=1, title=1)] \
        + [_hit(f"record:f{i}", hit=1, title=1) for i in range(3)] \
        + [_hit("record:late", hit=4, title=4)]
    status, picked = query._brief_resolve("q", hits[:query._BRIEF_RESOLVE_N])
    assert status == "resolved" and picked[0]["id"] == "record:top"
    # 넓은 풀 전체로 확정했다면 record:late 와 비겨 되묻기였다.
    assert query._brief_resolve("q", hits)[0] == "choose"


def test_the_choose_list_is_in_semantic_order_over_the_whole_pool():
    """확정용 순위는 의미 팔에만 걸린 후보를 맨 뒤로 민다. 목록은 의미 거리 순이다."""
    rows = [_hit("record:t1", hit=2, title=2, arms=("title", "graph")),
            _hit("record:t2", hit=2, title=2, arms=("title",), distance=0.50),
            _hit("record:s1", hit=0, title=0, arms=("semantic",), distance=0.31),
            _hit("record:s2", hit=0, title=0, arms=("semantic",), distance=0.40, sem_decisive=False),
            _hit("record:d", hit=0, title=0, arms=("semantic",), distance=0.45, sem_decisive=True)]
    order = [h["id"] for h in query._brief_list_rank(rows)]
    assert order == ["record:d", "record:s1", "record:s2", "record:t2", "record:t1"]


def test_d14_plan_search_reports_the_fusion_that_runs():
    from vinc_rag import retrieval
    code, plan = server.VincApp.read_plan(None, "search", {})
    assert code == 200
    assert plan["fusion"] == f"RRF(k={retrieval.CHUNK_RRF_K})"
    assert plan["keyword_gate"]["threshold"] == retrieval.KEYWORD_GATE
    assert plan["node_fusion"] == "RRF(k=60)" and "title" in plan["node_arms"]
