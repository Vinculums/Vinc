"""v23 — 공개 위키 쪽(개념·사건·기록·결정) 끝점 쌍. 실엔진으로 본다.

커뮤니티 소유자에서는 위키 관계 네 종이 페이지 노드 넷 어느 쌍에나 서고, ABOUT 은 사건·기록
페이지도 받는다. 개인 그래프의 쓰기 어휘와 군집 투영은 그대로여야 한다.
"""
from __future__ import annotations

import itertools

import pytest

from api import fragment, query
from store import embedded_schema as eschema
from store.backend import open_store
from store.config import StoreConfig

A = "community:0191f0aa-1111-7abc-8def-0123456789ab"
TALK = "episode:talk-0191f0aa-3333-7abc-8def-0123456789ab"
AGENT = "concept:agentv-0191f0aa-2222-7abc-8def-0123456789ab-1"
PAGES = ["concept:ether", "episode:michelson-morley", "record:null-result", "decision:adopt-relativity"]
WIKI = ("relates_to", "prerequisite_of", "derives_from", "inherits_from")


def _props(kind):
    # 반영기가 보내는 모양(kind:"wiki" 와 정체 상자)을 흉내 낸다.
    return {"kind": "wiki", "topic": "physics", "sources": [], "links": [],
            "identity": {"kind": kind, "qid": "Q1", "when": {"start": "1887-07"}}}


def _nodes():
    # 필드 집합은 `fragment._KEPT_FIELDS` 그대로다. 하나라도 어긋나면 경고가 난다.
    return [
        {"id": "concept:ether", "type": "concept", "title": "Ether", "domain": "physics",
         "one_liner": "A medium once assumed for light.", "content_time": "1800",
         "props": _props("concept")},
        {"id": "episode:michelson-morley", "type": "episode", "title": "Michelson-Morley",
         "domain": "physics", "summary": "Interferometer run.", "date": "1887-07",
         "props": _props("event")},
        {"id": "record:null-result", "type": "record", "title": "Null result",
         "domain": "physics", "content_time": "1887", "props": _props("result")},
        {"id": "decision:adopt-relativity", "type": "decision", "title": "Adopt relativity",
         "domain": "physics", "props": _props("decision")},
        {"id": AGENT, "type": "concept", "title": "Agent v1", "domain": "physics/agents",
         "one_liner": "Shared agent.", "props": {"kind": "agent"}},
        {"id": TALK, "type": "episode", "title": "Talk", "domain": "physics/talk",
         "summary": "A thread.", "props": {"kind": "talk"}},
    ]


def _edges():
    out = [{"from": a, "to": b, "type": t, "evidence": "linked on the page"}
           for t in WIKI for a, b in itertools.permutations(PAGES, 2)]
    for page in PAGES:
        out.append({"from": TALK, "to": page, "type": "about", "evidence": "thread url"})
        out.append({"from": AGENT, "to": page, "type": "relates_to"})
    return out


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    s = open_store(StoreConfig(data_dir=str(tmp_path / "db")))
    assert s is not None, "real embedded driver required"
    s.provision(dim=8)
    yield s
    s.close()


def _ingest(store, owner, nodes, edges):
    # 사이드카 `/ingest/fragment` 와 같은 인자다(`api.server.write_ingest`).
    return fragment.merge_fragment({"owner": owner, "nodes": nodes, "edges": edges},
                                   store=store, read_back=True, authored=True)


def test_community_typed_pages_take_every_wiki_pair_without_warnings(store):
    out = _ingest(store, A, _nodes(), _edges())
    assert out["warnings"] == [] and "partial" not in out, out["warnings"]
    edges = out["merged"]["edges"]
    # relates_to 의 역방향 6건은 같은 무방향 관계라 새로 만들지 않는다.
    assert edges["created"] + edges.get("skipped_duplicate", 0) == len(_edges())
    assert edges.get("skipped_duplicate") == 6
    # 기록 출발 관계도 rel 표에 선다(ATTACHES_TO 가 아니다).
    rows = store.run("MATCH (a:Record)-[r:DERIVES_FROM]->(b:Episode) RETURN count(r) AS c")
    assert int(rows[0]["c"]) == 1
    assert int(store.run("MATCH (:Record)-[r:ATTACHES_TO]->() RETURN count(r) AS c")[0]["c"]) == 0
    # 다시 보내도 경고 없이 같은 결과다(반영기는 재시도한다).
    again = _ingest(store, A, _nodes(), _edges())
    assert again["warnings"] == [], again["warnings"]


def test_personal_graph_keeps_its_pair_vocabulary(store):
    out = _ingest(store, "user:p", _nodes(), _edges())
    joined = "\n".join(out["warnings"])
    assert "edge_disallowed_pair" in joined and "concept→episode" in joined
    assert "has no 'about' edge" in joined   # 개인 그래프의 결정 고아 경고는 그대로
    rows = store.run("MATCH (a:Concept)-[r:RELATED_TO]->(b:Episode) RETURN count(r) AS c")
    assert int(rows[0]["c"]) == 0


def test_graph_reads_typed_relations_only_in_the_community(store):
    _ingest(store, A, _nodes(), _edges())
    g = query.graph(owner=A, seed="episode:michelson-morley", depth=1, store=store)
    got = {(e["source"], e["type"], e["target"]) for e in g["edges"]}
    assert ("record:null-result", "DERIVES_FROM", "episode:michelson-morley") in got
    assert ("episode:michelson-morley", "INHERITS_FROM", "concept:ether") in got
    assert (TALK, "ABOUT", "episode:michelson-morley") in got
    assert (AGENT, "RELATED_TO", "episode:michelson-morley") in got
    kinds = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds["record:null-result"] == "record" and kinds["decision:adopt-relativity"] == "decision"
    # 개인 소유자의 조회는 v23 갈래를 타지 않는다(같은 플랜, 같은 갈래 수).
    personal = query.decision_relation_rows_plan(owner="user:p", ids=["x"]).cypher
    community = query.decision_relation_rows_plan(owner=A, ids=["x"]).cypher
    assert "RELATED_TO" not in personal and "(b:Episode)" not in personal
    assert "RELATED_TO" in community and "(b:Episode)" in community


def test_kind_change_deletes_the_old_node_and_relations_follow(store):
    _ingest(store, A, _nodes(), _edges())
    gone = query.delete_edge("record:null-result", "derives_from", "episode:michelson-morley",
                             owner=A, store=store)
    assert gone["found"] and gone["deleted"] == {"edges": 1}, gone
    rel = query.delete_edge("concept:ether", "relates_to", "record:null-result", owner=A, store=store)
    assert rel["found"], rel
    # 종류가 바뀌면(개념 → 사건) 옛 노드를 지우고 새 노드를 쓴다.
    old = query.delete_node("concept:ether", owner=A, store=store)
    assert old["deleted"]["node"] == 1, old
    node = dict(_nodes()[0], id="episode:ether", type="episode", summary="Now an event.",
                date="1887")
    node.pop("one_liner"), node.pop("content_time")
    out = _ingest(store, A, [node, _nodes()[2]],
                  [{"from": "episode:ether", "to": "record:null-result", "type": "relates_to"}])
    assert out["warnings"] == [], out["warnings"]


def test_communities_projection_stays_on_concepts(store):
    _ingest(store, A, _nodes(), _edges())
    nodes, edges = query._algo_graph(store, A, ("RELATED_TO",), query._LOUVAIN_LABELS)
    assert set(nodes) == {"concept:ether", AGENT}
    assert all(a.startswith("concept:") and b.startswith("concept:") for a, b in edges)


def test_v22_store_opens_the_page_pairs_on_upgrade(tmp_path, monkeypatch):
    """v22 모양으로 만든 스토어가 기동 마이그레이션(ALTER ADD FROM/TO)으로 새 쌍을 얻는다."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    old = []
    for lbl, src, dst, props in eschema._REL_TABLES:
        if lbl in eschema._V22_PAIRS:
            src = ("Episode", "Decision") if lbl == "ABOUT" else (
                "Concept" if lbl == "RELATED_TO" else eschema._SEMANTIC_LABELS)
            dst = "Concept" if lbl == "RELATED_TO" else eschema._SEMANTIC_LABELS
        old.append((lbl, src, dst, props))
    cfg = StoreConfig(data_dir=str(tmp_path / "db"))
    with monkeypatch.context() as m:
        m.setattr(eschema, "_REL_TABLES", old)
        s = open_store(cfg)
        s.provision(dim=8)
        s.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = '22'")
        s.run("CREATE (:Record {record_id: 'record:x', owner_id: 'u'})")
        s.run("CREATE (:Episode {episode_id: 'episode:y', owner_id: 'u'})")
        with pytest.raises(Exception):
            s.run("MATCH (a:Record), (b:Episode) MERGE (a)-[:DERIVES_FROM]->(b)")
        s.close()
    s = open_store(cfg)
    try:
        s.provision(dim=8)
        s.run("MATCH (a:Record), (b:Episode) MERGE (a)-[:DERIVES_FROM]->(b)")
        s.run("MATCH (a:Record), (b:Episode) MERGE (b)-[:ABOUT]->(a)")
        assert int(s.run("MATCH (:Record)-[r:DERIVES_FROM]->(:Episode) RETURN count(r) AS c")[0]["c"]) == 1
        v = s.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' RETURN m.value AS v")
        assert v[0]["v"] == str(eschema.SCHEMA_VERSION)
    finally:
        s.close()
