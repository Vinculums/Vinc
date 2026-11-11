# -*- coding: utf-8 -*-
"""저장 위치 이동의 복사(`store.migrate.copy_store`) — 원본의 키가 대상에 전부 오고, 다시 돌려도 같다.

잠그는 것:
  - 노드·엣지·문서(청크·메타)가 키 그대로 오고, 옮긴 뒤 API 로 읽히는 것이 원본과 같다.
  - 같은 복사를 다시 돌리면 아무것도 늘지 않는다(MERGE, 멱등).
  - 대상에서 행 하나가 빠지면 `verify_copy` 가 그것을 센다(부르는 쪽이 위치를 바꾸지 않을 근거).
  - 원본에는 아무것도 쓰지 않는다.
임베디드 → 임베디드는 늘 돌고, 임베디드 → Neo4j → 임베디드 왕복은 `VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다.
⚠ 그 데이터베이스를 비운다. 테스트 전용 서버만 가리킬 것.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore, fragment, query as q  # noqa: E402
from store import migrate  # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import StoreConfig  # noqa: E402

OWNER = "user:copy"
URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
TEXT = "Moving a graph keeps every node, edge and document.\n\nThe second paragraph is a second chunk."


def _embedded():
    s = open_store(StoreConfig(data_dir=str(Path(tempfile.mkdtemp()) / "store"),
                               buffer_pool_mb=128, max_db_size=1 << 30))
    if s is None:
        pytest.skip("임베디드 드라이버(ladybug) 미설치")
    s.provision(dim=4)
    return s


def _neo4j():
    from store.neo4j_backend import Neo4jBackend
    s = Neo4jBackend(URI, user=os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j"),
                     password=os.environ["VINC_CONFORMANCE_NEO4J_PASSWORD"], heartbeat=False)
    s.run("MATCH (n) WHERE NOT n:_VincWriterLease CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
    return s


def _fill(s):
    nodes = [{"id": "concept:copy", "type": "concept", "title": "Copy", "domain": "vinc/engineering/store"},
             {"id": "record:copy-evidence", "type": "record", "title": "Copy evidence",
              "props": {"note": "kept"}, "valid_from": "2026-11-01"}]
    nodes += [{"id": f"concept:copy-{i}", "type": "concept", "title": f"Copy {i}"} for i in range(7)]
    edges = [{"from": "record:copy-evidence", "to": "concept:copy", "type": "relates_to"}]
    edges += [{"from": f"concept:copy-{i}", "to": "concept:copy", "type": "relates_to"} for i in range(7)]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": edges}, store=s)
    assert out["executed"], out
    put = docstore.put_doc(s, owner=OWNER, doc_id="copy.doc", title="Copy doc", text=TEXT,
                           topic="vinc/engineering/store")
    assert put["executed"], put


def _seen(s):
    node = q.node("record:copy-evidence", owner=OWNER, store=s)["node"]
    return {"props": node["props"], "title": node["title"],
            "neighbors": q.neighbors("concept:copy", owner=OWNER, store=s)["count"],
            "doc": docstore.get_doc(s, "copy.doc", owner=OWNER).get("text")}


def _round(src, dst):
    before = migrate.export_store(src)
    rep = migrate.copy_store(src, dst, dim=4, batch=3)     # 작은 묶음으로 경계를 지난다
    assert rep["missing"] == 0, rep
    assert migrate.export_store(src) == before              # 원본은 그대로다
    assert _seen(dst) == _seen(src)
    counts = {k: len(v) for k, v in migrate.export_store(dst)["nodes"].items()}
    edges = len(migrate.export_store(dst)["edges"])
    again = migrate.copy_store(src, dst, dim=4, batch=3)
    assert again["missing"] == 0
    after = migrate.export_store(dst)
    assert {k: len(v) for k, v in after["nodes"].items()} == counts and len(after["edges"]) == edges  # 멱등
    return rep


def test_임베디드에서_임베디드로():
    src, dst = _embedded(), _embedded()
    try:
        _fill(src)
        rep = _round(src, dst)
        assert rep["tables"]["Concept"] == {"source": 9, "found": 9}   # 문서 미러 개념 하나 포함
        assert sum(t["source"] for t in rep["edges"].values()) >= 8
    finally:
        src.close()
        dst.close()


def test_대상에서_빠진_행을_센다():
    src, dst = _embedded(), _embedded()
    try:
        _fill(src)
        migrate.copy_store(src, dst, dim=4)
        dst.run("MATCH (n:Concept) WHERE n.title = 'Copy 3' DETACH DELETE n")
        from store import pkns
        raw = pkns.enabled()      # 덤프와 대조는 같은 키로(물리 키면 둘 다 물리 키)
        rep = migrate.verify_copy(migrate.export_store(src, raw=raw), dst, raw=raw)
        assert rep["tables"]["Concept"] == {"source": 9, "found": 8}
        assert rep["missing"] >= 2                     # 노드 하나와 그 엣지 하나
    finally:
        src.close()
        dst.close()


@pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")
def test_임베디드에서_Neo4j_로_갔다가_돌아온다():
    src, db, back = _embedded(), _neo4j(), _embedded()
    try:
        _fill(src)
        _round(src, db)
        _round(db, back)
        a, b = migrate.export_store(src), migrate.export_store(back)
        assert {k: len(v) for k, v in a["nodes"].items()} == {k: len(v) for k, v in b["nodes"].items()}
        assert len(a["edges"]) == len(b["edges"])
    finally:
        src.close()
        db.close()
        back.close()
