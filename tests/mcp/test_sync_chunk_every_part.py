# -*- coding: utf-8 -*-
"""기기는 번들의 어느 부분도 한 요청에 몰아 보내지 않고, 보낼 수 없는 문서 하나가 동기화를 멈추지 않는다.

`test_sync_over_limit.py` 와 같은 스택이다: 임베디드 스토어 위의 사이드카 ← 브리지 ← 공개 허브(HTTP), 기기 스토어
하나. 기기는 실제 클라이언트(`syncclient`, `sync.push_local`/`push_docs`)로 보낸다. 상한은 작게 줄여서(허브와
클라이언트가 같은 값을 읽는다) 같은 일이 몇백 항목으로 일어나게 한다. 큰 값의 분할은 `tests/core/test_sync_chunk_parts.py`.

- 삭제 표식만, 엣지 삭제 표식만, 엣지만, 섞인 번들이 여러 청크로 올라가고 허브에 전부 적용된다.
- 청크 하나가 받아들여지지 않으면(413, 500) 커서도 base 도 움직이지 않고, 다시 보내면 전부 올라간 뒤에야 움직인다.
- 혼자서 상한을 넘는 문서는 이름과 크기를 단 경고로 건너뛰고 큐에 남는다. 나머지는 올라가고 동기화는 끝까지 간다.
"""
from __future__ import annotations

import json

import pytest

from api import docstore as ds
from api import docsync, graph_edit, sync
from api import fragment as frag
from store.embedded_backend import import_driver
from tests.mcp.test_sync_over_limit import (  # noqa: F401  (모듈 픽스처와 헬퍼)
    DOMAIN, KEY, OWNER, STAMP, World, _node, world)
from vinc_mcp import http_server, hub_http, hubhttp, syncclient

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

APPLY = ("POST", "/sync/apply-delta")
DOCS_APPLY = ("POST", "/sync/docs-apply")


def shrink(monkeypatch, body: int, items: int = 50) -> None:
    """허브의 본문 상한과 클라이언트가 읽는 같은 값, 청크 상한을 줄인다."""
    monkeypatch.setattr(http_server, "MAX_BODY", body)
    monkeypatch.setattr(hub_http, "MAX_BODY", body)
    monkeypatch.setattr(sync, "CHUNK_BYTES", body * 3 // 4)
    monkeypatch.setattr(sync, "CHUNK_ITEMS", items, raising=False)


def wire(chunk: dict) -> int:
    return len(hubhttp.encode_json({"bundle": {**chunk, **syncclient.HANDLES}}))


def put_nodes(world, ids, pad: int = 0, store=None) -> None:
    store = store or world.dev
    for lo in range(0, len(ids), 100):
        nodes = [{"id": nid, "type": "concept", "title": nid, "domain": DOMAIN,
                  "props": {"note": "x" * pad} if pad else {}} for nid in ids[lo:lo + 100]]
        res = frag.merge_fragment({"owner": OWNER, "nodes": nodes, "edges": []}, store=store)
        assert res["executed"], res


def hub_nodes(world) -> set[str]:
    """허브에 있는 이 소유자의 개념 id(스토어가 돌려주는 논리 id)."""
    rows = world.hub.run("MATCH (c:Concept) WHERE c.owner_id = $o RETURN c.concept_id AS id", {"o": OWNER})
    return {r["id"] for r in rows}


def hub_edge_pairs(world) -> set[frozenset]:
    rows = world.hub.run("MATCH (a:Concept)-[r:RELATED_TO]->(b:Concept) WHERE a.owner_id = $o "
                         "RETURN a.concept_id AS s, b.concept_id AS d", {"o": OWNER})
    return {frozenset((r["s"], r["d"])) for r in rows}


def hub_markers(world) -> int:
    return int(world.hub.run("MATCH (t:_Tombstone) WHERE t.owner_id = $o RETURN count(t) AS c",
                             {"o": OWNER})[0]["c"])


def requests(world, route) -> int:
    return sum(1 for r in world.reached if r == route)


def has_base(world, nid: str) -> bool:
    """`push_local` 가 쓰는 base 행이 있는가. 그 길은 base 를 소유자 없이 적고 병합은 소유자로 읽으므로 둘 다 본다."""
    return any(sync.get_base(world.dev, nid, owner) is not None for owner in ("", OWNER))


def sync_cursor(world) -> int:
    return sync.get_cursor(world.dev, "push")


def push(world):
    return sync.push_local(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)


def export(world, since=None):
    return sync.export_delta(owner=OWNER, since=sync_cursor(world) if since is None else since,
                             cfg=world.dev_cfg, store=world.dev)


# ── 삭제 표식만 ────────────────────────────────────────────────────────────────
def test_a_delete_only_bundle_over_the_limit_goes_up_in_several_chunks_and_every_delete_lands(world, monkeypatch):
    ids = [f"concept:gone-{i:04d}" for i in range(240)]
    put_nodes(world, ids)
    assert push(world)["sent"] == 240 and len(hub_nodes(world)) == 240
    for nid in ids:
        assert graph_edit.delete_node(nid, owner=OWNER, cfg=world.dev_cfg, store=world.dev)["found"]

    shrink(monkeypatch, 8192)
    bundle = export(world)
    assert bundle["nodes"] == [] and len(bundle["tombstones"]) == 240
    assert wire(bundle) > http_server.MAX_BODY, "통째로는 허브가 거절하는 크기여야 한다"
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 4 and all(wire(ch) <= http_server.MAX_BODY for ch in chunks)

    world.reached.clear()
    done = push(world)
    assert done["deletes"] == 240 and done["remote"]["chunks"] == len(chunks), done
    assert requests(world, APPLY) == len(chunks)
    assert hub_nodes(world) == set(), "허브에 지운 노드가 남았다"
    assert hub_markers(world) == 240
    assert sync_cursor(world) == bundle["next_cursor"]


# ── 엣지 삭제 표식만 ───────────────────────────────────────────────────────────
def test_an_edge_delete_only_bundle_goes_up_in_several_chunks_and_every_edge_is_gone(world, monkeypatch):
    ids = [f"concept:c{i:03d}" for i in range(121)]
    put_nodes(world, ids)
    res = frag.merge_fragment({"owner": OWNER, "nodes": [], "edges": [
        {"from": ids[i], "to": ids[i + 1], "type": "relates_to"} for i in range(120)]}, store=world.dev)
    assert res["executed"], res
    push(world)
    assert len(hub_edge_pairs(world)) == 120
    for i in range(120):
        assert graph_edit.delete_edge(ids[i], "relates_to", ids[i + 1], owner=OWNER,
                                      cfg=world.dev_cfg, store=world.dev)["executed"]

    shrink(monkeypatch, 8192)
    # 엣지 삭제가 끝점에 새 순번을 매겨 노드가 델타에 실릴 수 있다. 표식만 보려고 끝점은 이미 올라간 것으로 둔다.
    since = sync_cursor(world)
    bundle = export(world, since)
    assert len(bundle["edge_tombstones"]) == 120 and bundle["edges"] == []
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 3 and all(wire(ch) <= http_server.MAX_BODY for ch in chunks)
    assert [e for ch in chunks for e in ch["edge_tombstones"]] == bundle["edge_tombstones"]

    done = push(world)
    assert done["edge_deletes"] == 120, done
    assert hub_edge_pairs(world) == set(), "허브에 지운 엣지가 남았다"
    assert sync_cursor(world) == bundle["next_cursor"]


# ── 엣지만 ─────────────────────────────────────────────────────────────────────
def test_an_edge_only_bundle_over_the_limit_goes_up_in_several_chunks_and_every_edge_lands(world, monkeypatch):
    """기기의 델타는 끝점을 함께 싣지만, 청커는 끝점 없는 엣지 번들도 받는다: 허브에 끝점이 이미 있으면 올라간다."""
    ids = [f"concept:e{i:03d}" for i in range(40)]
    put_nodes(world, ids)
    push(world)
    pairs = [(ids[i], ids[j]) for i in range(40) for j in range(i + 1, 40)][:300]
    edges_ = [{"from": s, "to": d, "type": "relates_to", "evidence": "근거 문장. " * 4} for s, d in pairs]

    shrink(monkeypatch, 8192)
    bundle = {"nodes": [], "edges": edges_}
    assert wire(bundle) > http_server.MAX_BODY
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 4 and all(wire(ch) <= http_server.MAX_BODY and ch["nodes"] == [] for ch in chunks)

    world.reached.clear()
    out = syncclient.push(chunks, token=KEY, base=world.base)
    assert out["chunks"] == len(chunks) and out["edges"] == 300, out
    assert requests(world, APPLY) == len(chunks)
    assert hub_edge_pairs(world) == {frozenset(p) for p in pairs}


# ── 섞인 번들 ──────────────────────────────────────────────────────────────────
def test_a_mixed_bundle_lands_whole_through_the_real_client(world, monkeypatch):
    old = [f"concept:old{i:03d}" for i in range(60)]
    keep = [f"concept:keep{i:03d}" for i in range(40)]
    put_nodes(world, old + keep, pad=200)
    frag.merge_fragment({"owner": OWNER, "nodes": [], "edges": [
        {"from": keep[i], "to": keep[i + 1], "type": "relates_to"} for i in range(39)]}, store=world.dev)
    push(world)
    assert hub_nodes(world) == set(old + keep)

    for nid in old:
        graph_edit.delete_node(nid, owner=OWNER, cfg=world.dev_cfg, store=world.dev)
    for i in range(0, 39, 2):                                  # 엣지 절반은 지우고
        graph_edit.delete_edge(keep[i], "relates_to", keep[i + 1], owner=OWNER, cfg=world.dev_cfg, store=world.dev)
    fresh = [f"concept:new{i:03d}" for i in range(50)]         # 새 노드와 그 사이 엣지를 더한다
    put_nodes(world, fresh, pad=300)
    frag.merge_fragment({"owner": OWNER, "nodes": [], "edges": [
        {"from": fresh[i], "to": fresh[i + 1], "type": "relates_to"} for i in range(49)]}, store=world.dev)

    shrink(monkeypatch, 12288, items=40)
    bundle = export(world)
    assert bundle["nodes"] and bundle["edges"] and bundle["tombstones"] and bundle["edge_tombstones"]
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 6 and all(wire(ch) <= http_server.MAX_BODY for ch in chunks)
    for ch in chunks:
        have = {n["id"] for n in ch["nodes"]}
        assert all(e["from"] in have and e["to"] in have for e in ch["edges"]), "엣지 끝점이 청크에 없다"
    first_nodes = next(i for i, ch in enumerate(chunks) if ch["nodes"])
    assert all(not (ch.get("tombstones") or ch.get("edge_tombstones")) for ch in chunks[first_nodes + 1:])

    done = push(world)
    assert done["remote"]["chunks"] == len(chunks) and done["deletes"] == 60, done
    assert hub_nodes(world) == set(keep + fresh)
    # 보존 엣지 19개(지운 20개 빼고) + 새 엣지 49개
    assert len(hub_edge_pairs(world)) == (39 - 20) + 49
    assert sync_cursor(world) == bundle["next_cursor"]


# ── 청크 하나가 받아들여지지 않으면 커서는 그대로다 ─────────────────────────────
class Fault:
    """`/sync/push` 나 `/sync/docs-push` 의 n 번째 요청을 허브가 보낸 것처럼 실패시킨다. 요청마다 커서를 적는다."""

    def __init__(self, monkeypatch, world, *, status: int, nth: int = 2, path: str = "/sync/push",
                 cursor_key: str = "push"):
        self.calls = self.armed = 0
        self.status, self.nth, self.path, self.armed = status, nth, path, True
        self.cursor_at_each_call: list[int] = []
        real = hubhttp.post_json

        def post_json(url, **kw):
            if url.endswith(self.path):
                self.calls += 1
                self.cursor_at_each_call.append(sync.get_cursor(world.dev, cursor_key))
                if self.armed and self.calls == self.nth:
                    raise hubhttp.HubHTTPError(url, self.status, body=json.dumps({"error": "injected"}))
            return real(url, **kw)

        monkeypatch.setattr(hubhttp, "post_json", post_json)


@pytest.mark.parametrize("status", [413, 500])
def test_a_chunk_the_hub_does_not_accept_stops_the_cursor_and_the_bases(world, monkeypatch, status):
    gone = [f"concept:gone{i:03d}" for i in range(30)]
    put_nodes(world, gone)
    push(world)
    for nid in gone:
        graph_edit.delete_node(nid, owner=OWNER, cfg=world.dev_cfg, store=world.dev)
    fresh = [f"concept:fresh{i:03d}" for i in range(40)]
    put_nodes(world, fresh, pad=1500)

    shrink(monkeypatch, 16384)
    before = sync_cursor(world)
    bundle = export(world)
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 4, "둘째 청크 뒤에도 청크가 남아야 한다"
    fault = Fault(monkeypatch, world, status=status)
    with pytest.raises(syncclient.SyncError) as err:
        push(world)
    assert f"({status})" in str(err.value) and "injected" in str(err.value), str(err.value)
    assert fault.calls == 2, "둘째 청크에서 멈추고 셋째를 보내지 않는다"
    assert fault.cursor_at_each_call == [before, before]
    assert sync_cursor(world) == before
    assert not any(has_base(world, nid) for nid in fresh), "받아들여지지 않은 노드의 base 가 옮겨졌다"
    again = export(world)
    assert [n["id"] for n in again["nodes"]] == [n["id"] for n in bundle["nodes"]]
    assert len(again["tombstones"]) == 30, "받아들여지지 않은 묶음의 삭제 표식이 사라졌다"
    assert len(hub_nodes(world)) < 40, "허브에 이미 전부 올라가 있으면 이 테스트는 아무것도 보지 않는다"

    fault.armed = False                                      # 허브가 돌아왔다: 같은 묶음이 처음부터 올라간다
    done = push(world)
    assert done["remote"]["chunks"] == len(chunks), done
    assert sync_cursor(world) == bundle["next_cursor"] > before
    assert hub_nodes(world) == set(fresh)
    assert all(has_base(world, nid) for nid in fresh)


@pytest.mark.parametrize("status", [413, 500])
def test_a_document_chunk_the_hub_does_not_accept_stops_the_docs_cursor(world, monkeypatch, status):
    shrink(monkeypatch, 6000)
    ids = [f"doc:chunked-{i}" for i in range(4)]
    for doc_id in ids:
        ds.put_doc(world.dev, owner=OWNER, doc_id=doc_id, title=doc_id, text=f"{doc_id} " + "본문 " * 300)
    before = sync.get_cursor(world.dev, "docs-push")
    payload = docsync.export_docs(world.dev, owner=OWNER, since=before)
    assert len(sync._chunk_docs(payload)) >= 3
    fault = Fault(monkeypatch, world, status=status, path="/sync/docs-push", cursor_key="docs-push")
    with pytest.raises(syncclient.SyncError) as err:
        sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert f"({status})" in str(err.value)
    assert fault.cursor_at_each_call == [before, before] and sync.get_cursor(world.dev, "docs-push") == before
    assert len(docsync.export_docs(world.dev, owner=OWNER, since=before)["docs"]) == 4

    fault.armed = False
    done = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert done["sent"] == 4 and sync.get_cursor(world.dev, "docs-push") == payload["cursor"] > before
    assert len(docsync.export_docs(world.dev, owner=OWNER, since=payload["cursor"])["docs"]) == 0


# ── 혼자서 상한을 넘는 문서 ────────────────────────────────────────────────────
def hub_docs(world) -> set[str]:
    return {r["id"] for r in world.hub.run("MATCH (d:_DocMeta) WHERE d.owner_id = $o RETURN d.doc_id AS id",
                                            {"o": OWNER})}


def queued(world) -> list[str]:
    return [d["doc_id"] for d in docsync.export_docs(
        world.dev, owner=OWNER, since=sync.get_cursor(world.dev, "docs-push"))["docs"]]


def test_a_document_over_the_limit_is_skipped_by_name_while_the_rest_syncs_and_it_stays_queued(world, monkeypatch):
    shrink(monkeypatch, 6000)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:small-1", title="Small one", text="첫째. " * 20)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:huge", title="Huge", text="큰 문서. " * 700)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:small-2", title="Small two", text="둘째. " * 20)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:gone", title="Gone", text="곧 지운다. ")
    assert ds.delete_doc(world.dev, "doc:gone", owner=OWNER)["executed"]
    huge = next(d for d in docsync.export_docs(world.dev, owner=OWNER, since=0)["docs"] if d["doc_id"] == "doc:huge")
    size = syncclient.docs_push_bytes({"docs": [huge], "tombstones": []})
    assert size > http_server.MAX_BODY

    out = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert out["executed"] is True and out["sent"] == 2 and out["deletes"] == 1, out
    [report] = out["too_large"]
    assert (report["doc_id"], report["bytes"], report["limit"]) == ("doc:huge", size, 6000), report
    assert "doc:huge" in report["error"] and f"{size:,}" in report["error"] and "6,000" in report["error"]
    assert report["error"] in out["warnings"]
    assert hub_docs(world) == {"doc:small-1", "doc:small-2"}, "나머지 문서가 올라가야 한다"
    assert queued(world) == ["doc:huge"], "보내지 못한 문서가 큐에서 빠졌다"
    assert sync.get_cursor(world.dev, "docs-push") > 0

    again = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)       # 매번 같은 답이다
    assert again["sent"] == 0 or again["too_large"][0]["doc_id"] == "doc:huge"
    assert [t["doc_id"] for t in again["too_large"]] == ["doc:huge"] and queued(world) == ["doc:huge"]

    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:huge", title="Huge", text="줄였다. " * 20)   # 줄이면 올라간다
    done = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert done["sent"] == 1 and "too_large" not in done, done
    assert hub_docs(world) == {"doc:small-1", "doc:small-2", "doc:huge"} and queued(world) == []


def test_the_whole_sync_runs_to_the_end_with_an_unsendable_document(world, monkeypatch):
    """문서 국면의 보낼 수 없는 문서가 노드 국면이나 다음 문서 국면을 막지 않는다."""
    shrink(monkeypatch, 8192)
    put_nodes(world, [f"concept:s{i}" for i in range(5)])
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:huge", title="Huge", text="큰 문서. " * 900)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:ok", title="Fine", text="괜찮은 문서. ")

    out = sync.run(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert out["push"]["sent"] == 5 and out["docs_push"]["executed"] is True, out
    assert [t["doc_id"] for t in out["docs_push"]["too_large"]] == ["doc:huge"]
    assert len(hub_nodes(world)) == 5 and hub_docs(world) == {"doc:ok"}
    assert queued(world) == ["doc:huge"]

    put_nodes(world, ["concept:later"])                       # 다음 동기화도 노드를 보낸다
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:ok-2", title="Fine too", text="또 괜찮은 문서. ")
    out = sync.run(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert out["push"]["sent"] >= 1 and out["docs_push"]["sent"] == 1, out
    assert "concept:later" in hub_nodes(world)
    assert hub_docs(world) == {"doc:ok", "doc:ok-2"} and queued(world) == ["doc:huge"]


def test_the_document_limit_is_the_hubs_limit_to_the_byte(world, monkeypatch):
    shrink(monkeypatch, 6000)
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:edge", title="Edge", text="a" * 1000)

    def body_size() -> int:
        d = next(x for x in docsync.export_docs(world.dev, owner=OWNER, since=0)["docs"] if x["doc_id"] == "doc:edge")
        return syncclient.docs_push_bytes({"docs": [d], "tombstones": []})

    grow = 1000 + 6000 - body_size()
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:edge", title="Edge", text="a" * grow)
    assert body_size() == 6000
    out = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert out["sent"] == 1 and "too_large" not in out and hub_docs(world) == {"doc:edge"}, out

    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:edge", title="Edge", text="a" * (grow + 1))
    assert body_size() == 6001
    out = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert out["sent"] == 0 and [t["doc_id"] for t in out["too_large"]] == ["doc:edge"], out
