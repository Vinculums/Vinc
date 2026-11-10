# -*- coding: utf-8 -*-
"""저장소 적합성 스위트 — 어느 엔진 위에서든 호출부가 보는 동작이 같은가(B3).

계획 `vinc.plan.bring-your-own-database-2026-11-08` §2 B3. 엔진 철자가 아니라 **API 가 돌려주는
것**만 본다: 쓰기 계약(merged·warnings·partial·prefix_mismatch·rev 충돌), 노드 읽기와 소유자
격리, 이웃, 문서 저장·읽기·삭제, 키워드 검색의 모양과 순서, 시간 필터, 같은 쓰기를 다시 보내도
같은 상태. 테스트 몸통은 원시 Cypher 를 쓰지 않는다(엔진마다 달라서다).

백엔드는 `BACKENDS` 에서 고른다. 지금은 임베디드(LadybugDB) 하나다. Neo4j 어댑터(B4)가 생기면
같은 자리에 공장 하나를 더하고, CI 의 Neo4j 컨테이너에서 같은 시험이 돈다.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from api import docstore, fragment, query as q
from store.backend import open_store
from store.config import StoreConfig

OWNER = "user:conformance"
OTHER = "user:conformance-other"


def _embedded():
    store = open_store(StoreConfig(data_dir=str(Path(tempfile.mkdtemp()) / "store"),
                                   buffer_pool_mb=128, max_db_size=1 << 30))
    if store is None:
        pytest.skip("임베디드 드라이버(ladybug) 미설치")
    store.provision(dim=4)
    return store


#: 이름 → 스토어 공장. 공장은 비어 있는 새 스토어를 프로비저닝해서 돌려준다.
BACKENDS = {"embedded": _embedded}


@pytest.fixture(params=sorted(BACKENDS))
def store(request):
    s = BACKENDS[request.param]()
    try:
        yield s
    finally:
        s.close()


def _merge(store, nodes, edges=(), owner=OWNER):
    return fragment.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)},
                                   store=store)


CONCEPT = {"id": "concept:conformance", "type": "concept", "title": "Conformance",
           "domain": "vinc/engineering/store", "one_liner": "Same behaviour on every engine."}
RECORD = {"id": "record:conformance-evidence", "type": "record", "title": "Conformance evidence",
          "props": {"note": "measured"}, "valid_from": "2026-11-01"}
EDGE = {"from": "record:conformance-evidence", "to": "concept:conformance",
        "type": "relates_to", "evidence": "fixture"}


# ── 쓰기 계약 ────────────────────────────────────────────────────────────────
def test_새로_쓰면_만든_수를_세고_다시_쓰면_같은_상태다(store):
    first = _merge(store, [CONCEPT, RECORD], [EDGE])
    assert first["executed"] is True
    assert first["merged"]["nodes"] == {"created": 2, "matched": 0}
    assert first["merged"]["edges"]["created"] == 1
    assert not first.get("partial")
    again = _merge(store, [CONCEPT, RECORD], [EDGE])
    assert again["merged"]["nodes"] == {"created": 0, "matched": 2}
    # ⚠ `merged.edges.created` 는 새 행 수가 아니라 실행한 엣지 MERGE 수다(다시 보내도 1).
    #    그래서 상태로 본다: 엣지는 여전히 하나다.
    assert q.neighbors("concept:conformance", owner=OWNER, store=store)["count"] == 1
    node = q.node("record:conformance-evidence", owner=OWNER, store=store)["node"]
    assert node["props"] == {"note": "measured"}


def test_접두사와_타입이_다르면_그_노드만_빠지고_경고한다(store):
    out = _merge(store, [{"id": "concept:wrong-kind", "type": "episode", "title": "Mismatch"},
                         CONCEPT])
    assert out["merged"]["nodes"]["created"] == 1
    assert any("prefix" in w for w in out["warnings"]), out["warnings"]
    assert q.node("concept:wrong-kind", owner=OWNER, store=store)["found"] is False


def test_기대한_rev_가_다르면_쓰기_전에_거절한다(store):
    _merge(store, [RECORD])
    rev = q.node("record:conformance-evidence", owner=OWNER, store=store)["node"]["rev"]
    stale = dict(RECORD, props=None)
    stale.pop("props")
    out = _merge(store, [dict(stale, props_patch={"extra": 1}, expected_rev=rev + 5)])
    assert out["executed"] is False or out.get("error"), out
    kept = q.node("record:conformance-evidence", owner=OWNER, store=store)["node"]
    assert kept["props"] == {"note": "measured"}
    ok = _merge(store, [dict(stale, props_patch={"extra": 1}, expected_rev=kept["rev"])])
    assert ok["executed"] is True
    merged = q.node("record:conformance-evidence", owner=OWNER, store=store)["node"]["props"]
    assert merged == {"note": "measured", "extra": 1}


# ── 읽기와 소유자 격리 ───────────────────────────────────────────────────────
def test_다른_소유자의_노드는_없는_노드와_같다(store):
    _merge(store, [CONCEPT])
    assert q.node("concept:conformance", owner=OWNER, store=store)["found"] is True
    assert q.node("concept:conformance", owner=OTHER, store=store)["found"] is False


def test_이웃은_관계_종류와_방향을_돌려준다(store):
    _merge(store, [CONCEPT, RECORD], [EDGE])
    out = q.neighbors("concept:conformance", owner=OWNER, store=store)
    assert out["executed"] is True
    rows = [(n["id"], n["rel"], n["dir"]) for n in out["neighbors"]]
    # 기록 → 개념의 관계는 종류를 속성에 담은 ATTACHES_TO 로 저장된다.
    assert ("record:conformance-evidence", "ATTACHES_TO", "in") in rows, rows


# ── 문서 ────────────────────────────────────────────────────────────────────
TEXT = ("# Conformance doc\n\nThe adapter translates standard queries into the engine "
        "spelling.\n\n## Second part\n\nKeyword search must find the word quasar here.")


def test_문서는_저장한_그대로_읽히고_지우면_사라진다(store):
    put = docstore.put_doc(store, owner=OWNER, doc_id="conformance.doc", title="Conformance doc",
                           text=TEXT, topic="vinc/engineering/store")
    assert put["executed"] is True and put["chunks"] >= 1
    got = docstore.get_doc(store, "conformance.doc", owner=OWNER)
    assert got["executed"] is True and got["text"] == TEXT and got["verified"] is True
    assert docstore.get_doc(store, "conformance.doc", owner=OTHER).get("text") in (None, "")
    gone = docstore.delete_doc(store, "conformance.doc", owner=OWNER)
    assert gone["executed"] is True
    assert docstore.get_doc(store, "conformance.doc", owner=OWNER).get("text") in (None, "")
    again = docstore.delete_doc(store, "conformance.doc", owner=OWNER)   # 멱등
    assert again["executed"] is True


def test_같은_문서를_다시_넣으면_건너뛴다(store):
    docstore.put_doc(store, owner=OWNER, doc_id="conformance.doc", text=TEXT)
    again = docstore.put_doc(store, owner=OWNER, doc_id="conformance.doc", text=TEXT)
    assert again["executed"] is True and again["skipped"] is True


# ── 검색 ────────────────────────────────────────────────────────────────────
def test_키워드_검색은_그_낱말이_든_조각을_앞에_낸다(store):
    docstore.put_doc(store, owner=OWNER, doc_id="conformance.doc", title="Conformance doc",
                     text=TEXT)
    docstore.put_doc(store, owner=OWNER, doc_id="conformance.other", title="Other",
                     text="# Other\n\nNothing about the rare word in this one.")
    docstore.put_doc(store, owner=OTHER, doc_id="conformance.foreign", title="Foreign",
                     text="# Foreign\n\nquasar quasar quasar belongs to someone else.")
    out = q.search("quasar", owner=OWNER, store=store, top_k=5)
    assert out["executed"] is True
    docs = [r["doc_id"] for r in out["rows"]]
    assert docs and docs[0] == "conformance.doc", docs
    assert "conformance.foreign" not in docs            # 소유자 격리는 검색에도 선다(I1)
    for row in out["rows"]:
        assert row["id"].startswith(row["doc_id"] + "#")
        assert "quasar" in row["snippet"] or row["doc_id"] != "conformance.doc"
        assert row["arms"]                               # 어느 팔에서 왔는지 늘 싣는다


# ── 시간 ────────────────────────────────────────────────────────────────────
def test_유효기간이_끝난_기록은_기본_검색에서_빠지고_이력으로는_나온다(store):
    _merge(store, [dict(RECORD, id="record:conformance-old", title="Quasar measurement old",
                        valid_from="2026-01-01", valid_until="2026-02-01"),
                   dict(RECORD, id="record:conformance-new", title="Quasar measurement new",
                        valid_from="2026-02-01")])
    now = {h["id"] for h in q.search("Quasar measurement", owner=OWNER, store=store,
                                       top_k=10)["node_hits"]}
    assert "record:conformance-new" in now and "record:conformance-old" not in now, now
    past = {h["id"] for h in q.search("Quasar measurement", owner=OWNER, store=store,
                                        top_k=10, historical=True)["node_hits"]}
    assert "record:conformance-old" in past, past
