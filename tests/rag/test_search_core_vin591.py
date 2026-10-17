# -*- coding: utf-8 -*-
"""VIN-591 검색 코어 결함의 회귀 시험. 실엔진 스토어 하나를 PK 네임스페이싱 0·1 로 두 번 돈다.

시험마다 소유자와 문서 id 를 새로 짓는다. 모든 조회가 소유자로 걸리므로 시험끼리 섞이지
않는다(`_DocMeta.doc_id` 는 소유자로 나뉘지 않아 문서 id 도 새로 짓는다).
"""
from __future__ import annotations

import json
import os
from itertools import count

import pytest

from api import docstore, fragment, node_mirror, query
from store import embedded_backend as eb
from store import search_temporal as temporal
from store.backend import open_store
from store.config import load_config
from vinc_mcp import tools
from vinc_rag import retrieval

DIM = 4
CASES = count()


@pytest.fixture(scope="module", params=["0", "1"])
def st(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("vin591") / "db")})
    store = open_store(cfg)
    assert store is not None, eb.EmbeddedBackend.last_open_error
    try:
        store.provision(dim=DIM)
        yield store
    finally:
        store.close()
        for k, value in old.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value


def owner() -> str:
    return f"user:vin591-{next(CASES)}"


def ingest(st, who, nodes, edges=()):
    """노드는 200개씩 넣는다. 엣지는 양 끝 노드와 같은 호출에 실어야 해석된다."""
    for i in range(0, len(nodes), 200):
        res = fragment.merge_fragment({"owner": who, "nodes": nodes[i:i + 200]}, store=st)
        assert res["executed"] and not res.get("error"), res
    if edges:
        ends = {e[k] for e in edges for k in ("from", "to")}
        res = fragment.merge_fragment({"owner": who, "edges": list(edges),
                                       "nodes": [n for n in nodes if n["id"] in ends]}, store=st)
        assert res["executed"] and not res.get("error"), res


def record(nid, title, *, vf="2000", vu=None, props=None):
    return {"id": f"record:{nid}", "type": "record", "title": title,
            "domain": "vinc/synthetic", "props": props or {}, "valid_from": vf,
            "valid_until": vu}


def unit(*xs):
    v = list(xs) + [0.0] * (DIM - len(xs))
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def mirror(st, who, vectors: dict[str, list[float]]):
    """그 소유자의 노드 미러(NodeVec)를 손으로 만든 벡터로 채운다. 모델은 부르지 않는다."""
    rows, _ = node_mirror.collect(st, owner=who)
    rows = [{**r, "emb": vectors[r["id"]]} for r in rows if r["id"] in vectors]
    assert {r["id"] for r in rows} == set(vectors), rows
    node_mirror.replace(st, rows, "test")


def ids(hits):
    return [h["id"] for h in hits]


# ── B3: 은퇴한 문서의 미러는 그 청크처럼 감춘다 ─────────────────────────────────────

def test_b3_retired_document_mirror_is_hidden_like_its_chunks(st):
    who, n = owner(), next(CASES)
    retired, live = f"vin591.retired.{n}", f"vin591.live.{n}"
    docstore.put_doc(st, owner=who, doc_id=retired, title="Retired gateway spec",
                     text="Retired gateway spec body text.", valid_from="2000",
                     valid_until="2001", topic="vinc/synthetic")
    docstore.put_doc(st, owner=who, doc_id=live, title="Current gateway spec",
                     text="Current gateway spec body text.", valid_from="2000",
                     topic="vinc/synthetic")
    res = query.search("gateway spec", owner=who, store=st, top_k=10)
    assert "concept:doc." + retired not in ids(res["node_hits"]), res["node_hits"]
    assert "concept:doc." + live in ids(res["node_hits"]), res["node_hits"]
    assert all(r["doc_id"] != retired for r in res["rows"])
    assert res["validity_filtered"] >= 1
    history = query.search("gateway spec", owner=who, store=st, top_k=10, historical=True)
    assert "concept:doc." + retired in ids(history["node_hits"])
    past = query.search("gateway spec", owner=who, store=st, top_k=10, as_of="2000")
    assert "concept:doc." + retired in ids(past["node_hits"])
    brief = query.brief("Retired gateway spec", owner=who, store=st)
    assert (brief.get("node") or {}).get("id") != "concept:doc." + retired, brief


def test_b3_retired_mirror_cannot_return_through_the_semantic_arm(st):
    who, n = owner(), next(CASES)
    retired, live = f"vin591.sem-retired.{n}", f"vin591.sem-live.{n}"
    # 미러 개념은 주제를 적을 때 생긴다.
    docstore.put_doc(st, owner=who, doc_id=retired, title="Obsolete runbook",
                     text="Obsolete runbook body.", valid_from="2000", valid_until="2001",
                     topic="vinc/synthetic")
    docstore.put_doc(st, owner=who, doc_id=live, title="Living runbook",
                     text="Living runbook body.", valid_from="2000", topic="vinc/synthetic")
    mirror(st, who, {"concept:doc." + retired: unit(1, 0), "concept:doc." + live: unit(0.8, 0.6)})
    # 제목과 한 낱말도 안 겹치는 질의다. 의미 팔만 닿는다.
    res = query.search("zzqx", owner=who, store=st, top_k=5, query_embedding=unit(1, 0))
    assert "node_error" not in res, res.get("node_error")
    assert "concept:doc." + retired not in ids(res["node_hits"]), res["node_hits"]
    assert "concept:doc." + live in ids(res["node_hits"]), res["node_hits"]
    assert res["validity_filtered"] >= 1
    history = query.search("zzqx", owner=who, store=st, top_k=5, query_embedding=unit(1, 0),
                           historical=True)
    assert "concept:doc." + retired in ids(history["node_hits"])


# ── B4: 후보가 256 을 넘어도 대체 판정을 포기하지 않는다 ──────────────────────────

def test_b4_supersession_holds_with_more_than_256_candidates(st):
    who = owner()
    plain = [record(f"plain-{i:04d}", f"Lineage marker plain {i}",
                    props={"entity": "service", "predicate": "release", "scope": f"s{i}",
                           "value": f"v{i}"}) for i in range(300)]
    claim = {"entity": "service", "predicate": "release", "scope": "production"}
    old = record("lineage-old", "Lineage marker special old", props={**claim, "value": "a"})
    # 후속은 질의어와 한 낱말도 안 겹친다. 후보가 아니라 계보를 따라 읽혀야 한다.
    new = record("successor-b", "Replacement only", vf="2002", props={**claim, "value": "b"})
    ingest(st, who, plain + [old, new],
           [{"from": "record:successor-b", "to": "record:lineage-old", "type": "supersedes",
             "evidence": "Synthetic ruling."}])
    cur = query.search("Lineage marker special", owner=who, store=st, mode="current", top_k=5)
    assert "record:lineage-old" not in ids(cur["node_hits"]), cur["node_hits"]
    assert cur["node_window"]["lexical_candidates"] > 256
    assert len(cur["node_hits"]) == 5
    assert not any(h["temporal"].get("supersession_incomplete") for h in cur["node_hits"])
    history = query.search("Lineage marker special", owner=who, store=st, mode="historical",
                           top_k=5)
    hit = next(h for h in history["node_hits"] if h["id"] == "record:lineage-old")
    assert hit["temporal"]["claim_status"] == "superseded", hit["temporal"]


# ── B6: current 는 시간 메타를 못 읽으면 닫힌다 ──────────────────────────────────

def test_b6_current_mode_fails_closed_when_node_metadata_fails(st, monkeypatch):
    who = owner()
    ingest(st, who, [record(f"fo-{i:02d}", f"Failclosed marker {i}") for i in range(12)]
                    + [record("fo-retired", "Failclosed marker retired", vu="2001")])
    ok = query.search("Failclosed marker", owner=who, store=st, mode="current", top_k=5)
    assert len(ok["node_hits"]) == 5
    assert "record:fo-retired" not in ids(ok["node_hits"])

    def boom(*a, **kw):
        raise RuntimeError("synthetic metadata failure")

    monkeypatch.setattr(temporal, "node_sources", boom)
    bad = query.search("Failclosed marker", owner=who, store=st, mode="current", top_k=5)
    assert bad["node_hits"] == [], bad["node_hits"]
    assert bad["node_error"] and bad["partial"]


def test_b6_current_mode_withholds_chunks_when_document_metadata_fails(st, monkeypatch):
    who, n = owner(), next(CASES)
    for name, vu in (("old", "2001"), ("live", None)):
        docstore.put_doc(st, owner=who, doc_id=f"vin591.fc-{name}.{n}", title=f"Shutter {name}",
                         text="Shutter synthetic passage " * 10, valid_from="2000",
                         valid_until=vu, link=False)
    ok = query.search("Shutter", owner=who, store=st, mode="current", top_k=10)
    assert {r["doc_id"] for r in ok["rows"]} == {f"vin591.fc-live.{n}"}

    def boom(*a, **kw):
        raise RuntimeError("synthetic document metadata failure")

    monkeypatch.setattr(temporal, "doc_sources", boom)
    bad = query.search("Shutter", owner=who, store=st, mode="current", top_k=10)
    assert bad["rows"] == [] and bad["partial"] and bad["temporal_error"], bad


def test_b6_general_mode_withholds_chunks_when_document_titles_fail(st, monkeypatch):
    who, n = owner(), next(CASES)
    for name, vu in (("old", "2001"), ("live", None)):
        docstore.put_doc(st, owner=who, doc_id=f"vin591.ft-{name}.{n}", title=f"Louver {name}",
                         text="Louver synthetic passage " * 10, valid_from="2000",
                         valid_until=vu, link=False)
    ok = query.search("Louver", owner=who, store=st, top_k=10)
    assert {r["doc_id"] for r in ok["rows"]} == {f"vin591.ft-live.{n}"}

    def boom(*a, **kw):
        raise RuntimeError("synthetic title failure")

    monkeypatch.setattr(retrieval, "_attach_doc_titles", boom)
    bad = query.search("Louver", owner=who, store=st, top_k=10)
    assert bad["rows"] == [] and bad["partial"] and bad["temporal_error"], bad
    # 은퇴 여부와 상관없는 historical 은 그대로 답한다.
    history = query.search("Louver", owner=who, store=st, top_k=10, historical=True)
    assert len(history["rows"]) == 2


# ── B8: known_at 은 페이지를 자르기 전에 건다 ────────────────────────────────────

def test_b8_known_at_pages_continue_after_the_shrinker(st):
    who, n = owner(), next(CASES)
    for i in range(12):
        born = "2000-01-01T00:00:00Z" if i % 2 == 0 else "2005-01-01T00:00:00Z"
        docstore.put_doc(st, owner=who, doc_id=f"vin591.page{i:02d}.{n}", created_at=born,
                         link=False, text=f"Pagemark synthetic page body {'x' * 600} {i}.")
    first = query.search("Pagemark", owner=who, store=st, top_k=4, known_at="2001")
    assert len(first["rows"]) == 4 and first["has_more"], first
    assert all(int(r["doc_id"][len("vin591.page"):].split(".")[0]) % 2 == 0
               for r in first["rows"])
    assert first["known_at_filtered"] >= 6        # 청크 6 + 그 문서들의 미러
    one = dict(first, rows=first["rows"][:1])
    budget = len(json.dumps(one, ensure_ascii=False)) + 200
    shrunk = tools._shrink_search_result(json.loads(json.dumps(first)), budget)
    assert len(shrunk["rows"]) < 4
    nxt = query.search("Pagemark", owner=who, store=st, top_k=4, known_at="2001",
                       cursor=shrunk["next_cursor"])
    assert not nxt.get("error"), nxt.get("error")
    assert nxt["rows"][0]["id"] == first["rows"][len(shrunk["rows"])]["id"]
    rest = query.search("Pagemark", owner=who, store=st, top_k=4, known_at="2001",
                        cursor=first["next_cursor"])
    assert not rest.get("error") and len(rest["rows"]) == 2
    seen = ids(first["rows"]) + ids(rest["rows"])
    assert len(set(seen)) == 6


def test_b8_known_at_fills_node_hits_before_the_cut(st):
    who = owner()
    # 제목 토큰을 더 맞히는 쪽이 컷오프 뒤에 생긴 노드다. 자른 뒤에 거르면 목록이 빈다.
    late = [record(f"late-{i}", f"Cutoff marker alpha {i}") for i in range(4)]
    early = [{**record(f"early-{i}", f"Cutoff marker {i}"), "created": "2000-01-01T00:00:00Z"}
             for i in range(4)]
    ingest(st, who, late + early)
    res = query.search("Cutoff marker alpha", owner=who, store=st, top_k=3, known_at="2001")
    assert len(res["node_hits"]) == 3, res["node_hits"]
    assert all(h["id"].startswith("record:early-") for h in res["node_hits"])
    assert res["known_at_filtered"] >= 4


# ── B10: props 는 값으로만 맞춘다 ───────────────────────────────────────────────

def test_b10_props_keys_do_not_match_query_tokens(st):
    who = owner()
    nodes = [{"id": f"concept:topic-{i}", "type": "concept", "title": f"Unrelated topic {i}",
              "domain": "vinc/synthetic", "props": {"note": "nothing about the query here"}}
             for i in range(5)]
    nodes.append({"id": "concept:pricing", "type": "concept", "title": "Pricing",
                  "domain": "vinc/synthetic"})
    nodes.append({"id": "concept:tiers", "type": "concept", "title": "Tiers",
                  "domain": "vinc/synthetic", "props": {"scope": "monthly note tiers"}})
    nodes.append(record("claim-kestrel", "Release claim",
                        props={"entity": "gateway", "predicate": "release",
                               "scope": "production", "value": "kestrel"}))
    ingest(st, who, nodes)
    res = query.search("pricing note", owner=who, store=st, top_k=10)
    got = {h["id"]: h["matched_in"] for h in res["node_hits"]}
    assert got == {"concept:pricing": "title", "concept:tiers": "props"}, got
    assert res["node_window"]["lexical_candidates"] == 2
    # 예약 주장 키는 값으로는 찾히고, 키 이름으로는 안 찾힌다.
    by_value = query.search("kestrel", owner=who, store=st, top_k=5)
    assert ids(by_value["node_hits"]) == ["record:claim-kestrel"]
    assert by_value["node_hits"][0]["matched_in"] == "props"
    by_key = query.search("predicate", owner=who, store=st, top_k=5)
    assert "record:claim-kestrel" not in ids(by_key["node_hits"])


def test_b10_props_values_text_strips_keys_only():
    raw = json.dumps({"note": 'say "pricing" now', 'od"d': ["x", 2026], "nest": {"k": "deep"}},
                     ensure_ascii=False, sort_keys=True)
    text = retrieval._props_values_text(raw)
    assert "pricing" in text and "deep" in text and "2026" in text
    assert "note" not in text and "nest" not in text and '"k"' not in text
    assert retrieval._props_values_text({"scope": "Prod"}) == '{  "prod"}'


# ── D16: 도메인을 못 읽으면 빈 문자열이 아니라 비운다 ────────────────────────────

def test_d16_failed_domain_lookup_leaves_the_domain_absent(st, monkeypatch):
    who = owner()
    ingest(st, who, [{"id": "concept:orbit", "type": "concept", "title": "orbit",
                      "domain": "vinc/engineering/search"}])
    mirror(st, who, {"concept:orbit": unit(1, 0)})
    res = query.search("zzqx", owner=who, store=st, top_k=5, query_embedding=unit(1, 0))
    row = next(h for h in res["node_hits"] if h["id"] == "concept:orbit")
    assert row["domain"] == "vinc/engineering/search"
    real = st.run

    def run(cypher, params=None):
        if "RETURN n.concept_id AS id, n.domain AS domain" in cypher:
            raise RuntimeError("synthetic domain failure")
        return real(cypher, params)

    monkeypatch.setattr(st, "run", run)
    bad = query.search("zzqx", owner=who, store=st, top_k=5, query_embedding=unit(1, 0))
    row = next(h for h in bad["node_hits"] if h["id"] == "concept:orbit")
    assert "domain" not in row, row
    assert "Concept" in bad["node_domain_error"]
    failures: list = []
    assert retrieval._domains_for_ids(st, ["concept:orbit"], owner=who, failures=failures) == {}
    assert failures == ["Concept"]


# ── D17: 색인 유지 플래그는 형제 플래그와 같은 참 값을 받는다 ─────────────────────

@pytest.mark.parametrize("value,expect", [("1", True), ("true", True), ("yes", True),
                                          ("YES", True), ("on", True), ("no", False),
                                          ("off", False), ("", False)])
def test_d17_keep_vector_index_accepts_the_sibling_truthy_set(monkeypatch, value, expect):
    eb._keep_vec_engine_ok.cache_clear()
    monkeypatch.setenv("VINC_KEEP_VECTOR_INDEX", value)
    monkeypatch.setattr(eb, "_KEEP_VEC_INDEX_ENGINES",
                        (str(getattr(eb._import_driver()[0], "__version__", "") or ""),))
    try:
        assert eb.keep_vector_index() is expect
    finally:
        eb._keep_vec_engine_ok.cache_clear()


# ── D18: 날짜만 적은 오늘 시작은 current 에서도 오늘 성립한다 ─────────────────────

def test_d18_date_only_start_of_today_holds_in_current_mode(st, monkeypatch):
    who = owner()
    ingest(st, who, [record("kst-today", "Dawnmark today", vf="2026-10-16"),
                     record("kst-later", "Dawnmark later", vf="2026-10-18")])
    mirror(st, who, {"record:kst-today": unit(1, 0), "record:kst-later": unit(0.9, 0.1)})
    real = temporal.dt.datetime

    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            # KST 2026-10-16 05:00 이다. UTC 로는 아직 10월 15일이다.
            return cls(2026, 10, 15, 20, tzinfo=temporal.dt.timezone.utc)

    monkeypatch.setattr(temporal.dt, "datetime", Clock)
    general = query.search("Dawnmark", owner=who, store=st, top_k=5)
    current = query.search("Dawnmark", owner=who, store=st, top_k=5, mode="current")
    assert "record:kst-today" in ids(general["node_hits"])
    assert ids(current["node_hits"]) == ["record:kst-today"], current["node_hits"]
    assert current["node_hits"][0]["temporal"]["validity"] == "current"
    # 의미 팔만 닿는 질의도 같은 규칙이다.
    sem = query.search("zzqx", owner=who, store=st, top_k=5, mode="current",
                       query_embedding=unit(1, 0))
    assert ids(sem["node_hits"]) == ["record:kst-today"], sem["node_hits"]


def test_d18_metadata_reads_a_date_only_start_at_the_earliest_zone():
    assert temporal.metadata({"valid_from": "2026-10-16"},
                             now="2026-10-15T20:00:00Z")["validity"] == "current"
    assert temporal.metadata({"valid_from": "2026-10-16"},
                             now="2026-10-15T09:59:00Z")["validity"] == "future"
    # 시각까지 적은 시작은 그대로 읽는다.
    assert temporal.metadata({"valid_from": "2026-10-16T00:00:00Z"},
                             now="2026-10-15T20:00:00Z")["validity"] == "future"


# ── top_k 0 은 1 로 맞춘다 ───────────────────────────────────────────────────────

def test_top_k_zero_is_clamped_to_one(st):
    who = owner()
    ingest(st, who, [record(f"clamp-{i}", f"Clampmark {i}") for i in range(4)])
    for mode in (None, "current"):
        res = query.search("Clampmark", owner=who, store=st, top_k=0, mode=mode)
        assert len(res["node_hits"]) == 1, (mode, res["node_hits"])
        assert res["node_window"]["limit"] == 1


def test_b3_b6_a_store_before_document_validity_keeps_mirrors_and_rows(st, monkeypatch):
    """v22 이전 스토어(이관 전 고정 평가 스토어)에는 문서 구간 열이 없다. 적은 적이 없으니
    거를 것도 없다: 미러와 행을 그대로 낸다. 그 밖의 실패는 위 시험들처럼 닫힌다."""
    who, n = owner(), next(CASES)
    doc = f"vin591.legacy.{n}"
    docstore.put_doc(st, owner=who, doc_id=doc, title="Legacy ledger",
                     text="Legacy ledger body text.", link=False)
    real = st.run

    def run(cypher, params=None):
        if "_DocMeta" in cypher and "valid_from" in cypher:
            raise RuntimeError("Binder exception: Cannot find property valid_from for d.")
        return real(cypher, params)

    monkeypatch.setattr(st, "run", run)
    res = query.search("Legacy ledger", owner=who, store=st, top_k=5)
    assert [r["doc_id"] for r in res["rows"]] == [doc], res
    assert res["partial"]
    assert retrieval._mirror_validity(st, ["concept:doc." + doc], owner=who) == {}


def test_b3_near_nodes_hint_skips_a_retired_mirror(st):
    # VIN-614: 놓친 id 의 "혹시 이것" 힌트도 검색과 같이 은퇴한 문서의 미러를 뺀다.
    who, n = owner(), next(CASES)
    retired, live = f"vin614.retired.{n}", f"vin614.live.{n}"
    docstore.put_doc(st, owner=who, doc_id=retired, title="Retired turbine spec",
                     text="Retired turbine spec body text.", valid_from="2000",
                     valid_until="2001", topic="vinc/synthetic")
    docstore.put_doc(st, owner=who, doc_id=live, title="Current turbine spec",
                     text="Current turbine spec body text.", valid_from="2000",
                     topic="vinc/synthetic")
    hints = [h["id"] for h in query.near_nodes(st, "concept:turbine", owner=who, limit=5)]
    assert "concept:doc." + live in hints, hints
    assert "concept:doc." + retired not in hints, hints
