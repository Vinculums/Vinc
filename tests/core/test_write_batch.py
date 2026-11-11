# -*- coding: utf-8 -*-
"""쓰기 묶음(`store.write_batch`) — fragment 하나와 문서 하나가 트랜잭션 하나로 들어간다.

잠그는 것:
  - 결과는 한 문장씩 쓸 때(`VINC_FRAGMENT_BATCH=off`)와 같다: 노드·엣지 수, `IN_DOMAIN` 고리(도메인을
    옮겨도 하나), 시계·카운터(`_SyncMeta`)가 저장된 행의 최대값과 같고 단조다.
  - 도중에 예외가 나면 아무 행도 남지 않는다(묶기 전에는 앞의 노드가 남았다).
  - 안의 문장이 실패해 호출부가 삼키면(노드 하나만 건너뛰기 계약) 묶음을 버리고 한 문장씩 다시 써서
    같은 결과가 된다. 깨진 뒤의 문장은 엔진에 가지 않는다.
  - Neo4j: 묶음이 열린 동안 다른 스레드의 읽기는 커밋 전 행을 보지 않는다.
Neo4j 쪽은 `VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다. ⚠ 그 데이터베이스를 비운다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore, fragment  # noqa: E402
from store import write_batch  # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import StoreConfig  # noqa: E402

OWNER = "user:batch"
URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
needs_neo4j = pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")


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
    s.provision(dim=4)
    return s


ENGINES = [pytest.param(_embedded, id="embedded"),
           pytest.param(_neo4j, id="neo4j", marks=needs_neo4j)]


def _fragment(tag: str, domain: str = "vinc/batch/a", n: int = 6) -> dict:
    nodes = [{"id": f"concept:{tag}-{i}", "type": "concept", "title": f"{tag} {i}", "domain": domain}
             for i in range(n)]
    edges = [{"from": f"concept:{tag}-{i}", "to": f"concept:{tag}-0", "type": "relates_to"}
             for i in range(1, n)]
    return {"owner": OWNER, "nodes": nodes, "edges": edges}


def _count(s, tag: str) -> int:
    rows = s.run("MATCH (n:Concept) WHERE n.owner_id = $o RETURN n.concept_id AS id", {"o": OWNER})
    return sum(1 for r in rows if str(r["id"]).startswith(f"concept:{tag}-"))


def _links(s) -> dict[str, list[str]]:
    rows = s.run("MATCH (n:Concept)-[:IN_DOMAIN]->(d:Domain) WHERE n.owner_id = $o "
                 "RETURN n.concept_id AS id, d.name AS d", {"o": OWNER})
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["id"]), []).append(r["d"])
    return out


def _meta(s, key: str) -> str:
    rows = s.run("MATCH (m:_SyncMeta) WHERE m.key = $k RETURN m.value AS v", {"k": key})
    return str(rows[0]["v"]) if rows else ""


@pytest.mark.parametrize("make", ENGINES)
def test_묶어도_결과와_시계는_한_문장씩_쓸_때와_같다(make, monkeypatch):
    s = make()
    try:
        out = fragment.merge_fragment(_fragment("on"), store=s)
        monkeypatch.setenv("VINC_FRAGMENT_BATCH", "off")
        off = fragment.merge_fragment(_fragment("off"), store=s)
        monkeypatch.delenv("VINC_FRAGMENT_BATCH")
        assert out["merged"] == off["merged"] == {"nodes": {"created": 6, "matched": 0},
                                                   "edges": {"created": 5}}
        links = _links(s)
        assert all(links[f"concept:on-{i}"] == ["vinc/batch/a"] for i in range(6))
        # 도메인을 옮기면 고리는 새 것 하나다.
        moved = fragment.merge_fragment(_fragment("on", domain="vinc/batch/b"), store=s)
        assert moved["merged"]["nodes"]["matched"] == 6
        links = _links(s)
        assert all(links[f"concept:on-{i}"] == ["vinc/batch/b"] for i in range(6))
        # 묶음이 메모리에서 센 시계·카운터는 커밋 때 한 번 쓰이고, 저장된 행의 최대값과 같다.
        rows = s.run("MATCH (n:Concept) WHERE n.owner_id = $o RETURN n.updated_at AS u, n.sync_seq AS q",
                     {"o": OWNER})
        assert int(_meta(s, f"seq:{OWNER}")) == max(int(r["q"]) for r in rows)
        seqs = sorted(int(r["q"]) for r in rows)
        assert len(set(seqs)) == len(seqs)      # 카운터는 겹치지 않는다
        assert write_batch.current(s) is None
    finally:
        s.close()


@pytest.mark.parametrize("make", ENGINES)
def test_도중에_예외가_나면_아무_행도_남지_않는다(make, monkeypatch):
    s = make()
    try:
        def boom(*a, **k):
            raise RuntimeError("edge writer exploded")
        monkeypatch.setattr(fragment, "_merge_edge", boom)
        with pytest.raises(RuntimeError, match="edge writer exploded"):
            fragment.merge_fragment(_fragment("gone"), store=s)
        assert _count(s, "gone") == 0           # 노드 MERGE 까지 되돌려졌다
        assert write_batch.current(s) is None
        monkeypatch.undo()
        ok = fragment.merge_fragment(_fragment("back"), store=s)
        assert ok["merged"]["nodes"]["created"] == 6 and _count(s, "back") == 6
    finally:
        s.close()


@pytest.mark.parametrize("make", ENGINES)
def test_삼킨_실패는_묶음을_버리고_한_문장씩_다시_쓴다(make, capsys):
    s = make()
    real = s.run
    state = {"failed": 0, "after": 0}

    def flaky(query, params=None, **kw):
        # 묶음 안에서 노드 하나의 MERGE 를 한 번 실패시킨다. `_merge_node` 는 그 노드만 건너뛰고
        # 이어 가는데, 트랜잭션은 이미 되돌려졌으므로 묶음은 버려져야 한다.
        batch = write_batch.current(s)
        if batch is not None and batch.broken is not None:
            state["after"] += 1
        if batch is not None and not state["failed"] and "MERGE (n:Concept" in query \
                and (params or {}).get("id", "").endswith("flaky-3"):
            state["failed"] += 1
            exc = RuntimeError("Binder exception: simulated")
            batch.fail(exc)
            raise exc
        return real(query, params, **kw)

    s.run = flaky
    try:
        out = fragment.merge_fragment(_fragment("flaky"), store=s)
        assert state["failed"] == 1
        # 다시 쓴 결과는 실패가 없었던 것과 같다(두 번째 시도에서는 실패를 일으키지 않는다).
        assert out["merged"] == {"nodes": {"created": 6, "matched": 0}, "edges": {"created": 5}}, out
        assert _count(s, "flaky") == 6
        assert "fell back to one statement at a time" in capsys.readouterr().err
    finally:
        s.run = real
        s.close()


def test_깨진_묶음_뒤의_문장은_엔진에_가지_않는다():
    s = _embedded()
    try:
        with s.write_batch() as batch:
            s.run("MERGE (n:Concept {concept_id: $id}) SET n.title = 't', n.owner_id = $o",
                  {"id": "concept:half", "o": OWNER})
            with pytest.raises(Exception):
                s.run("MATCH (n:NoSuchTable) RETURN n")
            assert batch.broken is not None
            with pytest.raises(write_batch.BatchBroken):
                s.run("MERGE (n:Concept {concept_id: $id}) SET n.title = 't', n.owner_id = $o",
                      {"id": "concept:after", "o": OWNER})
        assert s.run("MATCH (n:Concept) WHERE n.concept_id IN ['concept:half', 'concept:after'] "
                     "RETURN count(n) AS c")[0]["c"] == 0
    finally:
        s.close()


def test_문서도_트랜잭션_하나로_들어간다():
    s = _embedded()
    calls = {"begin": 0}
    real = s.write_batch

    def counting():
        calls["begin"] += 1
        return real()

    s.write_batch = counting
    try:
        out = docstore.put_doc(s, owner=OWNER, doc_id="batch.doc", title="Batch",
                               text="First paragraph.\n\nSecond paragraph.")
        assert out["executed"] and calls["begin"] == 1, out
        assert docstore.get_doc(s, "batch.doc", owner=OWNER).get("text")
    finally:
        s.write_batch = real
        s.close()


@needs_neo4j
def test_neo4j_묶음_중_다른_스레드는_커밋_전_행을_보지_않는다():
    s = _neo4j()
    try:
        seen = {}
        with s.write_batch():
            s.run("MERGE (n:Concept {concept_id: $id}) SET n.title = 't', n.owner_id = $o",
                  {"id": "concept:iso", "o": OWNER})
            assert _count(s, "iso") == 0 or True   # 같은 스레드는 자기 쓰기를 본다(아래에서 확인)

            def other():
                seen["rows"] = s.run("MATCH (n:Concept {concept_id: 'concept:iso'}) RETURN count(n) AS c")
            t = threading.Thread(target=other)
            t.start()
            t.join(10)
            mine = s.run("MATCH (n:Concept {concept_id: 'concept:iso'}) RETURN count(n) AS c")
        assert mine[0]["c"] == 1
        assert seen["rows"][0]["c"] == 0
        assert s.run("MATCH (n:Concept {concept_id: 'concept:iso'}) RETURN count(n) AS c")[0]["c"] == 1
    finally:
        s.close()


def test_큰_fragment_는_묶지_않는다(monkeypatch):
    s = _embedded()
    opened = {"n": 0}
    real = s.write_batch

    def counting():
        opened["n"] += 1
        return real()

    s.write_batch = counting
    try:
        monkeypatch.setattr(fragment, "BATCH_MAX_ITEMS", 5)
        out = fragment.merge_fragment(_fragment("big", n=6), store=s)   # 노드 6 + 엣지 5
        assert out["merged"]["nodes"]["created"] == 6 and opened["n"] == 0
        out = fragment.merge_fragment(_fragment("small", n=2), store=s)  # 노드 2 + 엣지 1
        assert out["merged"]["nodes"]["created"] == 2 and opened["n"] == 1
    finally:
        s.write_batch = real
        s.close()


def test_쓰지_않은_카운터는_돌려준다():
    from api import revstamp
    s = _embedded()
    try:
        with s.write_batch():
            a = revstamp.next_seq(s, OWNER)
            b = revstamp.next_seq(s, OWNER)
            revstamp.give_back_seq(s, OWNER, a)       # 뒤에 b 가 나갔으므로 되돌리지 않는다
            assert revstamp.next_seq(s, OWNER) == b + 1
            c = revstamp.next_seq(s, OWNER)
            revstamp.give_back_seq(s, OWNER, c)
            assert revstamp.next_seq(s, OWNER) == c
        assert _meta(s, f"seq:{OWNER}") == str(c)
    finally:
        s.close()
