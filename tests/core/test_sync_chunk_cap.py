# -*- coding: utf-8 -*-
"""`chunk_bundle` 의 개수 상한, 노드와 그 삭제 표식, 보낼 수 없는 청크, 문서 크기 한 번.

허브는 항목 하나마다 쓰기를 하고 허브에서 사이드카로 가는 요청은 60초에 끊긴다. 바이트만 보면 3MB 노드 청크 하나가
2만 개를 넘으므로 노드 청크도 개수로 끊는다. 여기서 잠그는 것:

- 노드 청크와 넘침 청크는 `CHUNK_ITEMS` 를 넘지 않는다. 센 것은 노드(다시 실은 끝점 포함)·엣지·표식 전부다.
- 한 청크에 든 엣지의 양끝은 그 청크의 노드다. 상한 때문에 노드가 갈라져도 같은 청크 안에서 갖춰지는 엣지는 넘침으로 가지 않는다.
- 노드 삭제 표식은 번들에 같은 id 의 노드가 있으면 그 노드와 같은 청크에 간다. 개수 상한이 둘을 가르지 않는다.
- 더 쪼갤 수 없어 허브의 본문 상한을 넘는 청크는 `held` 에 이름과 크기로 적히고 돌려주는 청크에 들지 않는다.
- 문서는 한 번의 push 에서 한 번만 인코딩한다.
"""
from __future__ import annotations

import json

import pytest

from api import docsync, sync
from tests.core.test_sync_chunk_parts import doc, edge, node, tomb, wire
from tests.core.test_sync_orchestrator import _MetaStore
from vinc_mcp import hub_http


def items(chunk: dict) -> int:
    return sum(len(chunk.get(k) or []) for k in ("nodes", "edges", "tombstones", "edge_tombstones"))


def ends_inside(chunks: list[dict]) -> bool:
    """모든 청크에서 엣지의 양끝이 그 청크의 노드인가."""
    for ch in chunks:
        have = {n["id"] for n in ch["nodes"]}
        if not all(e["from"] in have and e["to"] in have for e in ch["edges"]):
            return False
    return True


# ── 노드 청크의 개수 상한 ──────────────────────────────────────────────────────
def test_node_chunks_stop_at_the_item_cap(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 100)
    nodes = [node(i) for i in range(450)]
    assert sum(len(json.dumps(n)) for n in nodes) < sync.CHUNK_BYTES, "바이트로는 한 청크에 들어가는 번들이어야 한다"
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": []})
    assert [len(c["nodes"]) for c in chunks] == [100, 100, 100, 100, 50]
    assert [n for c in chunks for n in c["nodes"]] == nodes


def test_edges_that_complete_inside_a_node_chunk_count_toward_its_cap(monkeypatch):
    """줄 모양 그래프: 청크 안에서 갖춰지는 엣지는 같은 청크에 가고, 청크의 노드와 엣지를 합친 수가 상한 안이다."""
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 100)
    nodes = [node(i) for i in range(300)]
    edges_ = [edge(i, i + 1) for i in range(299)]
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": edges_})
    assert all(items(c) <= 100 for c in chunks), [items(c) for c in chunks]
    assert ends_inside(chunks)
    assert sorted(e["from"] + e["to"] for c in chunks for e in c["edges"]) == sorted(e["from"] + e["to"] for e in edges_)
    seen: set[str] = set()
    kept_in_node_chunks = 0
    for c in chunks:
        ids = {n["id"] for n in c["nodes"]}
        if not ids & seen:                  # 노드를 처음 싣는 청크. 다시 싣는 넘침 청크가 아니다.
            kept_in_node_chunks += len(c["edges"])
        seen |= ids
    boundaries = len(chunks)                # 청크 경계를 넘는 엣지는 경계마다 하나다
    assert kept_in_node_chunks >= 299 - boundaries, "청크 안에서 갖춰지는 엣지가 넘침 청크로 밀려났다"


def test_a_spill_chunk_counts_the_resent_endpoints(monkeypatch):
    """끝점이 모두 다른 청크에 있는 엣지: 넘침 청크는 엣지마다 끝점 둘을 다시 싣는다. 그 노드도 상한에 센다."""
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 100)
    nodes = [node(i) for i in range(300)]
    edges_ = [edge(i, i + 150) for i in range(150)]
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": edges_})
    spill = [c for c in chunks if c["edges"]]
    assert len(spill) >= 5
    assert all(items(c) <= 100 for c in chunks), [items(c) for c in chunks]
    assert ends_inside(chunks)
    assert sorted(e["from"] for c in spill for e in c["edges"]) == sorted(e["from"] for e in edges_)


def test_the_item_cap_leaves_the_byte_cap_in_force(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 1000)
    nodes = [node(i, pad=3000) for i in range(60)]
    whole = sum(len(json.dumps(n)) for n in nodes)
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": []}, limit=whole // 4)
    assert len(chunks) >= 4
    assert all(sum(len(json.dumps(n)) for n in c["nodes"]) <= whole // 4 for c in chunks)


# ── 노드와 그 삭제 표식 ────────────────────────────────────────────────────────
def own_tomb(i: int) -> dict:
    return {"node_id": f"concept:n{i:04d}", "label": "Concept", "deleted_at": "2026-11-14T00:00:00.000Z",
            "rev": 2, "_seq": i}


def holder(chunks: list[dict], key: str, ident, field: str) -> list[int]:
    return [i for i, c in enumerate(chunks) if any(x[field] == ident for x in c.get(key) or [])]


def test_a_node_and_its_tombstone_share_a_chunk_at_any_layout(monkeypatch):
    nodes = [node(i, pad=300) for i in range(12)]
    recreated = [3, 4, 9]
    loose = [tomb(i) for i in range(40, 44)]
    bundle = {"nodes": nodes, "edges": [edge(i, i + 1) for i in range(11)],
              "tombstones": [own_tomb(i) for i in recreated] + loose}
    for limit, cap in ((10**9, 1000), (1500, 1000), (700, 1000), (10**9, 3), (900, 2), (250, 1)):
        monkeypatch.setattr(sync, "CHUNK_ITEMS", cap)
        chunks = sync.chunk_bundle(bundle, limit=limit)
        for i in recreated:
            nid = f"concept:n{i:04d}"
            home = holder(chunks, "nodes", nid, "id")
            assert holder(chunks, "tombstones", nid, "node_id") and \
                set(holder(chunks, "tombstones", nid, "node_id")) <= set(home), (limit, cap, i)
            assert len(holder(chunks, "tombstones", nid, "node_id")) == 1, "표식은 한 번만 간다"
        assert sorted(t["node_id"] for c in chunks for t in c.get("tombstones") or []) == \
            sorted(t["node_id"] for t in bundle["tombstones"])
        first_node = next(i for i, c in enumerate(chunks) if c["nodes"])
        for t in loose:
            assert holder(chunks, "tombstones", t["node_id"], "node_id")[0] <= first_node, (limit, cap)


def test_the_item_cap_does_not_split_a_node_from_its_tombstone(monkeypatch):
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 3)
    nodes = [node(i) for i in range(6)]
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": [], "tombstones": [own_tomb(2), own_tomb(3)]})
    assert [[n["id"][-1] for n in c["nodes"]] for c in chunks] == [["0", "1"], ["2"], ["3", "4"], ["5"]]
    assert [[t["node_id"][-1] for t in c.get("tombstones") or []] for c in chunks] == [[], ["2"], ["3"], []]
    assert all(items(c) <= 3 for c in chunks)


def test_a_tombstone_with_no_node_in_the_bundle_stays_a_marker(monkeypatch):
    chunks = sync.chunk_bundle({"nodes": [node(0)], "edges": [], "tombstones": [tomb(7)]})
    assert len(chunks) == 1 and chunks[0]["nodes"] == [node(0)] and chunks[0]["tombstones"] == [tomb(7)]


# ── 더 쪼갤 수 없어 허브의 본문 상한을 넘는 청크 ────────────────────────────────
def test_a_chunk_the_hub_cannot_take_is_named_and_left_out(monkeypatch):
    """각각 상한 아래인 노드 둘이 서로 다른 청크에 있고 그 사이 엣지가 있다. 엣지의 넘침 청크는 둘을 함께 싣는다."""
    monkeypatch.setattr(hub_http, "MAX_BODY", 4000)
    bundle = {"nodes": [node(1, pad=1800), node(2, pad=1800)], "edges": [edge(1, 2)]}
    chunks = sync.chunk_bundle(bundle, limit=2500)
    assert len(chunks) == 3 and wire(chunks[2]) > 4000 >= max(wire(c) for c in chunks[:2])

    held: list[dict] = []
    sendable = sync.chunk_bundle(bundle, limit=2500, held=held)
    assert sendable == chunks[:2]
    assert len(held) == 1
    h = held[0]
    assert h["bytes"] == wire(chunks[2]) and h["limit"] == 4000
    assert h["edges"] == ["concept:n0001 -relates_to-> concept:n0002"]
    assert h["nodes"] == ["concept:n0001", "concept:n0002"]
    assert "concept:n0001 -relates_to-> concept:n0002" in h["error"] and f"{h['bytes']:,}" in h["error"]
    assert "4,000" in h["error"] and "stays queued" in h["error"]


def test_a_chunk_that_fits_the_hub_is_never_held(monkeypatch):
    """`limit` 을 넘어도 허브의 상한 안이면 보낸다(상한 위에 허브와의 여유를 두었다)."""
    monkeypatch.setattr(hub_http, "MAX_BODY", 4000)
    held: list[dict] = []
    chunks = sync.chunk_bundle({"nodes": [node(1, pad=2500)], "edges": []}, limit=1000, held=held)
    assert held == [] and len(chunks) == 1


# ── 문서는 한 번의 push 에서 한 번 인코딩한다 ───────────────────────────────────
def test_each_document_is_encoded_once_per_push(monkeypatch):
    monkeypatch.setattr(hub_http, "MAX_BODY", 6000)
    docs = [doc(f"doc:d{i}", "본문 " * 200) for i in range(5)] + [doc("doc:big", "가" * 4000)]
    payload = {"docs": docs, "tombstones": [], "cursor": 9}
    once = sum(len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for d in docs)
    monkeypatch.setattr(docsync, "export_docs", lambda store, owner, since: payload)
    monkeypatch.setattr(sync, "_send_docs_again", lambda *a, **k: None)
    sent = []
    remote = type("R", (), {"docs_push": lambda self, ch: sent.append(ch) or {}})()

    encoded = []
    real = json.dumps

    def counting(obj, *a, **k):
        out = real(obj, *a, **k)
        encoded.append(len(out.encode("utf-8")))
        return out
    monkeypatch.setattr(json, "dumps", counting)
    out = sync.push_docs(_MetaStore(), None, owner="user:local", remote=remote)
    monkeypatch.setattr(json, "dumps", real)

    assert [t["doc_id"] for t in out["too_large"]] == ["doc:big"]
    assert sorted(d["doc_id"] for ch in sent for d in ch["docs"]) == [f"doc:d{i}" for i in range(5)]
    assert sum(encoded) <= once * 1.1, f"문서가 한 번보다 많이 인코딩됐다: {sum(encoded)} / {once}"
