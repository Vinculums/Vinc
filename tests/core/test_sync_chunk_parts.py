# -*- coding: utf-8 -*-
"""`chunk_bundle` 와 `_chunk_docs` 는 번들의 어느 부분도 한 요청에 몰아 싣지 않는다.

허브는 4MB 를 넘는 본문을 413 으로 거절한다. 삭제 표식만 있거나 엣지만 있는 번들, 노드와 함께 가는 삭제 표식이 한 요청에
몰려 상한을 넘으면 기기는 매 동기화마다 멈춘다. 여기서 잠그는 것:

- 삭제 표식 4만 개, 엣지 삭제 표식만, 엣지만, 섞인 번들이 각각 상한 안의 여러 청크로 나뉘고 하나도 빠지지 않는다.
- 엣지는 끝점 노드가 번들에 있으면 그 노드와 같은 청크에 간다.
- 삭제 표식은 노드를 쓰는 어떤 청크보다 앞서거나 그 첫 청크에 간다: 허브는 엣지 삭제를 끝점이 이 청크를 적용하기
  전의 스탬프로 가리므로, 삭제 뒤에 고친 끝점이 먼저 들어가면 그 삭제를 잃는다.
- 문서: 삭제 표식도 나뉘고, 혼자서 상한을 넘는 문서는 어느 조각에도 실리지 않으며 이름과 크기로 알려진다.
"""
from __future__ import annotations

import json

import pytest

from api import docstore as ds
from api import query as q
from api import sync
from store.embedded_backend import import_driver
from tests.core.test_sync_edge_marker_after_edits import (  # noqa: F401  (모듈 픽스처와 헬퍼)
    A, SECOND, edges, run, seed, w, write)
from vinc_mcp import hub_http, hubhttp, syncclient

HUB_LIMIT = hub_http.MAX_BODY


def wire(chunk: dict) -> int:
    """클라이언트가 이 노드 청크를 보낼 때의 본문 바이트 수(`syncclient.push` 와 같은 직렬화)."""
    return len(hubhttp.encode_json({"bundle": {**chunk, **syncclient.HANDLES}}))


def tomb(i: int) -> dict:
    return {"node_id": f"concept:topic-{i:05d}-a-slug-of-typical-length", "label": "Concept",
            "deleted_at": "2026-11-14T00:00:00.000Z", "rev": 3, "_seq": i}


def etomb(i: int) -> dict:
    return {"src": f"concept:topic-{i:05d}-a-slug-of-typical-length", "rel": "RELATED_TO",
            "dst": f"concept:other-{i:05d}-a-slug-of-typical-length",
            "deleted_at": "2026-11-14T00:00:00.000Z", "_seq": i}


def node(i: int, pad: int = 0) -> dict:
    return {"id": f"concept:n{i:04d}", "type": "concept", "title": f"n{i}", "domain": "d",
            "props": {"note": "x" * pad}, "_rev": 1, "_updated_at": "2026-11-14T00:00:00.000Z", "_seq": i}


def edge(i: int, j: int, evidence: str = "") -> dict:
    e = {"from": f"concept:n{i:04d}", "to": f"concept:n{j:04d}", "type": "relates_to"}
    if evidence:
        e["evidence"] = evidence
    return e


def carried(chunks: list[dict], key: str) -> list:
    return [x for ch in chunks for x in ch.get(key) or []]


# ── 삭제 표식만 있는 번들 ──────────────────────────────────────────────────────
def test_forty_thousand_delete_markers_split_into_chunks_under_the_hub_limit():
    tombs = [tomb(i) for i in range(40_000)]
    whole = wire({"nodes": [], "edges": [], "tombstones": tombs})
    assert whole > HUB_LIMIT, "이 번들은 통째로는 허브가 거절하는 크기여야 한다"
    chunks = sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": tombs})
    assert len(chunks) >= 40, len(chunks)
    for ch in chunks:
        assert wire(ch) <= HUB_LIMIT
        assert len(ch["tombstones"]) <= sync.CHUNK_ITEMS
        assert ch["nodes"] == [] and ch["edges"] == []
    assert carried(chunks, "tombstones") == tombs, "표식이 빠지거나 겹치거나 순서가 바뀌었다"


def test_edge_delete_markers_only_split_too():
    etombs = [etomb(i) for i in range(25_000)]
    chunks = sync.chunk_bundle({"nodes": [], "edges": [], "edge_tombstones": etombs})
    assert len(chunks) >= 25
    assert all(wire(ch) <= HUB_LIMIT and len(ch["edge_tombstones"]) <= sync.CHUNK_ITEMS for ch in chunks)
    assert carried(chunks, "edge_tombstones") == etombs


def test_node_and_edge_markers_share_chunks_without_crossing_the_limits(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 50)
    tombs, etombs = [tomb(i) for i in range(70)], [etomb(i) for i in range(70)]
    chunks = sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": tombs, "edge_tombstones": etombs},
                               limit=4000)
    assert len(chunks) >= 3
    for ch in chunks:
        assert len(json.dumps(ch["tombstones"] + ch["edge_tombstones"])) <= 4000 + 64
        assert len(ch["tombstones"]) + len(ch["edge_tombstones"]) <= 50
    assert carried(chunks, "tombstones") == tombs and carried(chunks, "edge_tombstones") == etombs


def test_the_item_cap_bounds_a_chunk_even_when_the_bytes_would_allow_more():
    """3MB 청크 하나에 표식이 2만 개 넘게 든다. 허브가 항목 하나에 수 ms 를 쓰므로 개수로도 끊는다."""
    tombs = [tomb(i) for i in range(sync.CHUNK_ITEMS * 3)]
    assert sum(len(json.dumps(t)) for t in tombs) < sync.CHUNK_BYTES
    chunks = sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": tombs})
    assert [len(c["tombstones"]) for c in chunks] == [sync.CHUNK_ITEMS] * 3


# ── 엣지만 있는 번들 ───────────────────────────────────────────────────────────
def test_edges_without_their_endpoint_nodes_split_by_size_and_count():
    evidence = "근거 문장. " * 40
    edges_ = [edge(i, i + 1, evidence) for i in range(3000)]
    whole = wire({"nodes": [], "edges": edges_})
    chunks = sync.chunk_bundle({"nodes": [], "edges": edges_}, limit=whole // 4)
    assert len(chunks) >= 4
    assert all(ch["nodes"] == [] and len(ch["edges"]) <= sync.CHUNK_ITEMS for ch in chunks)
    assert all(wire(ch) <= whole // 4 + 200 for ch in chunks)
    assert carried(chunks, "edges") == edges_


def test_forty_thousand_edges_without_nodes_fit_the_hub_limit():
    edges_ = [edge(i, i + 1) for i in range(40_000)]
    chunks = sync.chunk_bundle({"nodes": [], "edges": edges_})
    assert len(chunks) >= 40
    assert all(wire(ch) <= HUB_LIMIT and len(ch["edges"]) <= sync.CHUNK_ITEMS for ch in chunks)
    assert carried(chunks, "edges") == edges_


# ── 섞인 번들 ──────────────────────────────────────────────────────────────────
def mixed(n_nodes=30, pad=300):
    nodes = [node(i, pad) for i in range(n_nodes)]
    # 가까운 끝점(같은 청크에 들기 쉽다)과 먼 끝점(갈라진다)을 섞는다.
    edges_ = [edge(i, i + 1) for i in range(n_nodes - 1)] + [edge(0, n_nodes - 1), edge(2, n_nodes - 3)]
    return {"nodes": nodes, "edges": edges_,
            "tombstones": [tomb(i) for i in range(120)], "edge_tombstones": [etomb(i) for i in range(120)]}


def test_a_mixed_bundle_keeps_every_part_once_and_every_chunk_under_the_limit(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 40)
    bundle, limit = mixed(), 3000
    chunks = sync.chunk_bundle(bundle, limit=limit)
    assert len(chunks) > 5
    for ch in chunks:
        assert len(json.dumps([ch["nodes"], ch["edges"], ch["tombstones"] if "tombstones" in ch else [],
                               ch.get("edge_tombstones", [])], ensure_ascii=False)) <= limit + 128
        have = {n["id"] for n in ch["nodes"]}
        for e in ch["edges"]:
            assert e["from"] in have and e["to"] in have, "엣지 끝점이 청크에 없다"
    assert carried(chunks, "tombstones") == bundle["tombstones"]
    assert carried(chunks, "edge_tombstones") == bundle["edge_tombstones"]
    key = lambda e: (e["from"], e["type"], e["to"])   # noqa: E731
    assert sorted(map(key, carried(chunks, "edges"))) == sorted(map(key, bundle["edges"]))
    assert {n["id"] for n in carried(chunks, "nodes")} == {n["id"] for n in bundle["nodes"]}


def test_delete_markers_come_before_every_chunk_that_writes_nodes_or_ride_the_first(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 40)
    chunks = sync.chunk_bundle(mixed(), limit=3000)
    first_with_nodes = next(i for i, ch in enumerate(chunks) if ch["nodes"])
    last_with_markers = max(i for i, ch in enumerate(chunks)
                            if ch.get("tombstones") or ch.get("edge_tombstones"))
    assert last_with_markers <= first_with_nodes


def test_markers_ride_the_first_chunk_when_they_fit():
    chunks = sync.chunk_bundle({"nodes": [node(0), node(1)], "edges": [edge(0, 1)],
                                "tombstones": [tomb(0)], "edge_tombstones": [etomb(0)]})
    assert len(chunks) == 1
    assert chunks[0]["tombstones"] == [tomb(0)] and chunks[0]["edge_tombstones"] == [etomb(0)]


def test_an_empty_bundle_has_no_chunks():
    assert sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": [], "edge_tombstones": []}) == []


# ── 순서: 삭제 뒤에 고친 끝점 ──────────────────────────────────────────────────
@pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")
@pytest.mark.parametrize("markers_first", [True, False], ids=["as-chunked", "markers-last-control"])
def test_an_edge_delete_survives_when_the_markers_reach_the_hub_before_the_edited_endpoints(w, markers_first):
    """지운 기기가 엣지를 지운 뒤 두 끝을 고쳤다. 표식 청크가 끝점 청크보다 앞서면 허브가 엣지를 지운다.

    대조(markers-last-control)는 같은 청크를 거꾸로 적용한다: 끝점이 먼저 들어가 허브의 끝점 스탬프가 삭제보다
    늦어지면 허브는 그 엣지를 삭제 뒤에 다시 만든 것으로 읽고 지우지 않는다. 이 순서 규칙이 있는 까닭이다."""
    seed(w)
    run(w, w.dev)
    w.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=w.dev).get("executed")
    w.clocks.hub += SECOND
    write(w.dev, "s edited after the edge deletion", "concept:s")
    write(w.dev, "d edited after the edge deletion", "concept:d")

    bundle = sync.export_delta(owner=A, since=sync.get_cursor(w.dev, "push"), domains=["marker"], store=w.dev)
    assert {n["id"] for n in bundle["nodes"]} == {"concept:s", "concept:d"}
    assert len(bundle["edge_tombstones"]) == 1 and bundle["edges"] == []
    # 노드 하나가 겨우 드는 상한: 두 끝점은 따로 가고, 표식은 첫 노드 청크에 합치지 못해 앞 청크가 된다.
    limit = max(sync._json_bytes(n) for n in bundle["nodes"]) + 10
    chunks = sync.chunk_bundle(bundle, limit=limit)
    assert [bool(c["nodes"]) for c in chunks] == [False, True, True], chunks
    assert chunks[0]["edge_tombstones"] == bundle["edge_tombstones"]

    assert edges(w.hub) == 1
    with w.clocks.at_hub():
        for ch in chunks if markers_first else reversed(chunks):
            assert sync.apply_upload(owner=A, bundle={**ch, **syncclient.HANDLES}, store=w.hub)["executed"]
    assert edges(w.hub) == (0 if markers_first else 1)


# ── 문서 ───────────────────────────────────────────────────────────────────────
def doc(doc_id: str, text: str) -> dict:
    return {"doc_id": doc_id, "title": "t", "origin": "", "content_hash": "h", "updated_at": "2026-11-14T00:00:00.000Z",
            "text": text}


def test_doc_chunks_measure_the_json_that_is_sent():
    """줄바꿈과 따옴표는 JSON 에서 두 바이트가 된다. 글자 수만 세면 조각이 상한을 넘는다."""
    docs = [doc(f"d{i}", '"\n' * 200_000) for i in range(6)]           # 글자 40만 개, 본문 80만 바이트
    chunks = sync._chunk_docs({"docs": docs, "tombstones": []}, max_bytes=1_500_000)
    assert len(chunks) == 6
    for ch in chunks:
        assert syncclient.docs_push_bytes(ch) <= 1_500_000 + 200


def test_doc_delete_markers_split_by_size_and_count_and_follow_the_documents():
    tombs = [{"doc_id": f"doc:gone-{i:05d}", "deleted_at": "2026-11-14T00:00:00.000Z"} for i in range(45_000)]
    chunks = sync._chunk_docs({"docs": [doc("d1", "본문")], "tombstones": tombs})
    assert len(chunks) >= 45
    assert [d["doc_id"] for d in chunks[0]["docs"]] == ["d1"]
    assert all(c["docs"] == [] for c in chunks[1:])
    assert all(syncclient.docs_push_bytes(c) <= HUB_LIMIT and len(c["tombstones"]) <= sync.CHUNK_ITEMS
               for c in chunks)
    assert [t for c in chunks for t in c["tombstones"]] == tombs


def test_a_document_over_the_hub_limit_is_named_and_left_out_of_every_chunk():
    big = doc("doc:big", "가" * 1_500_000)                            # 글자 하나가 3바이트: 본문 4.5MB
    small = doc("doc:small", "작은 문서")
    payload = {"docs": [small, big, doc("doc:also", "다른 문서")], "tombstones": []}
    found = sync.too_large_docs(payload)
    assert [t["doc_id"] for t in found] == ["doc:big"]
    size = syncclient.docs_push_bytes({"docs": [big], "tombstones": []})
    assert found[0]["bytes"] == size > HUB_LIMIT and found[0]["limit"] == HUB_LIMIT
    assert "doc:big" in found[0]["error"] and f"{size:,}" in found[0]["error"] and f"{HUB_LIMIT:,}" in found[0]["error"]
    chunks = sync._chunk_docs(payload)
    assert sorted(d["doc_id"] for c in chunks for d in c["docs"]) == ["doc:also", "doc:small"]


def test_a_document_exactly_at_the_limit_is_sendable(monkeypatch):
    monkeypatch.setattr(hub_http, "MAX_BODY", 2000)
    d = doc("doc:fit", "a")
    pad = 2000 - syncclient.docs_push_bytes({"docs": [d], "tombstones": []})
    d["text"] = "a" * (1 + pad)
    assert syncclient.docs_push_bytes({"docs": [d], "tombstones": []}) == 2000
    assert sync.too_large_docs({"docs": [d]}) == []
    d["text"] += "a"
    assert [t["doc_id"] for t in sync.too_large_docs({"docs": [d]})] == ["doc:fit"]


def test_nothing_to_send_when_the_only_document_is_too_large(monkeypatch):
    monkeypatch.setattr(hub_http, "MAX_BODY", 500)
    assert sync._chunk_docs({"docs": [doc("doc:big", "x" * 600)], "tombstones": []}) == []


# ── 모드 전환(push_everything)은 보내지 못한 문서를 빠졌다고 센다 ───────────────
@pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")
def test_push_everything_counts_a_too_large_document_as_missing(w, monkeypatch):
    for doc_id, text in (("doc:small", "작은 문서. " * 5), ("doc:big", "큰 문서. " * 400)):
        ds.put_doc(w.dev, owner=A, doc_id=doc_id, title=doc_id, text=text)
    monkeypatch.setattr(hub_http, "MAX_BODY", 3000)

    class Remote:
        def __init__(self):
            self.docs = []

        def push(self, chunks):
            return {"executed": True}

        def docs_push(self, payload):
            self.docs += [d["doc_id"] for d in payload["docs"]]
            return {"applied": len(payload["docs"]), "updated": 0, "skipped": 0}

    remote = Remote()
    rep = sync.push_everything(w.dev, None, owners=[A], remote=remote)
    assert remote.docs == ["doc:small"]
    assert rep["missing"] == 1, rep
    assert [t["doc_id"] for t in rep["too_large"]] == ["doc:big"]
    assert any("doc:big" in msg for msg in rep["warnings"])
