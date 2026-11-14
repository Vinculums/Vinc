# -*- coding: utf-8 -*-
"""청크로 나눠 적용한 허브의 결과는 번들 통째로 적용한 결과와 같다 — 청크를 어떻게 나눠도.

기기 스토어 하나와 허브 스토어 하나. 허브에 먼저 그래프를 올리고 기기와 두 번 맞춘 뒤, 허브 쪽에서 다른 기기가 한 일(고치기·
엣지 삭제·노드 삭제·되살리기)과 기기 쪽의 일(고치기·엣지 삭제와 다시 만들기·노드 삭제·엣지 추가·새 노드·삭제 뒤 같은 id 로
다시 만들기)을 무작위로 겹친다. 그 기기의 델타를 허브 스냅샷 셋에 적용한다: 통째로, 청크 순서대로, 청크를 거꾸로.
청크 크기(250바이트부터 무제한)와 개수 상한(1부터 1000)은 시드마다 바꾼다.

- 통째로와 청크 순서대로는 노드(제목)·엣지·노드 삭제 표식·엣지 삭제 표식이 같다. 삭제 뒤에 같은 id 로 다시 만든 노드(번들에 노드와
  표식이 함께 든다)가 든 시드를 일부러 넣었다.
- 거꾸로 적용한 쪽은 어떤 시드에서는 달라진다. 비교가 순서 어긋남을 실제로 잡는다는 대조다.
- 결정적인 경우 하나: 삭제 뒤에 다시 만든 노드가 허브의 엣지를 지키는가(표식 1,000개 경계에서 갈리던 경우를 줄여 둔다).
"""
from __future__ import annotations

import contextlib
import json
import random
import shutil
from pathlib import Path

import pytest

from api import graph_edit, revstamp, sync
from api import fragment as frag
from api import query as q
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import import_driver
from vinc_mcp import syncclient

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

A = "user:layout-dev"
DOM = "layout"
T0 = 1_800_000_000_000
SECOND = 1000
N = 14

#: (시드, 삭제 뒤 같은 id 로 다시 만든 노드가 번들에 든가). 든 시드는 표식과 노드가 갈리는 배치에서 통째로와 달라지던 것이다.
SEEDS = [(0, True), (3, True), (8, True), (13, True), (21, True), (1, False), (15, False)]
#: 청크를 거꾸로 적용하면 허브가 달라지는 시드.
CONTROL_SEEDS = [2, 7]


class Clocks:
    """허브와 기기가 같이 쓰는 벽시계. `tick` 이 앞으로 민다."""

    def __init__(self):
        self.hub = T0

    def __call__(self):
        return self.hub


class World:
    pass


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clocks = Clocks()
    monkeypatch.setattr(revstamp, "_wall_ms", clocks)
    w = World()
    w.tmp, w.clocks, w.opened = tmp_path, clocks, []

    def make(name):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        w.opened.append(st)
        return cfg, st
    w.hub_cfg, w.hub = make("hub")
    w.dev_cfg, w.dev = make("dev")
    sync.set_selected_domains(w.dev, [DOM])

    def post(base, path, token, payload, *, timeout):
        body = json.loads(json.dumps(payload))
        if path == "/sync/push":
            out = sync.apply_upload(owner=A, bundle=body["bundle"], store=w.hub)
        elif path == "/sync/pull":
            out = revstamp.with_clock(sync.export_delta(owner=A, since=int(body["since"]),
                                                        domains=[DOM], store=w.hub))
        else:
            raise AssertionError(path)
        return json.loads(json.dumps(out, default=str))
    monkeypatch.setattr(syncclient, "_post", post)
    w.remote = sync.RemoteHTTP("vinc_sk_test", "http://hub.test")
    try:
        yield w
    finally:
        for st in w.opened:
            with contextlib.suppress(Exception):
                st.close()


def tick(w, n=1):
    w.clocks.hub += n * SECOND


def nid(i):
    return f"concept:o{i:02d}"


def write(st, node_id, title, edges=(), extra=()):
    nodes = [{"id": node_id, "type": "concept", "title": title, "domain": DOM}, *extra]
    assert frag.merge_fragment({"owner": A, "nodes": nodes, "edges": list(edges)}, store=st)["executed"]


def rel(a, b):
    return {"from": nid(a), "to": nid(b), "type": "relates_to"}


def end(i, title):
    return {"id": nid(i), "type": "concept", "title": title, "domain": DOM}


def settle(w):
    tick(w)
    assert sync.run(w.dev, None, owner=A, remote=w.remote)["executed"]


def build(w, rnd):
    """허브에 노드 N개와 엣지 N개를 올리고 기기와 두 번 맞춘다. 엣지 쌍을 돌려준다."""
    tick(w)
    nodes = [{"id": nid(i), "type": "concept", "title": f"t{i} " + "x" * rnd.randint(0, 40), "domain": DOM}
             for i in range(N)]
    pairs = set()
    while len(pairs) < N:
        a, b = rnd.sample(range(N), 2)
        pairs.add((min(a, b), max(a, b)))
    assert frag.merge_fragment({"owner": A, "nodes": nodes, "edges": [rel(a, b) for a, b in sorted(pairs)]},
                               store=w.hub)["executed"]
    settle(w)
    settle(w)
    return sorted(pairs)


def hub_side_ops(w, rnd, pairs):
    """이 기기가 마지막으로 맞춘 뒤 다른 기기가 허브에 한 일."""
    tick(w)
    for _ in range(rnd.randint(0, 4)):
        kind = rnd.choice(["edit", "deledge", "delnode", "readd"])
        if kind == "edit":
            i = rnd.randrange(N)
            write(w.hub, nid(i), f"hub-edit {i} {rnd.random()}")
        elif kind == "deledge" and pairs:
            a, b = rnd.choice(pairs)
            q.delete_edge(nid(a), "relates_to", nid(b), owner=A, store=w.hub)
        elif kind == "delnode":
            graph_edit.delete_node(nid(rnd.randrange(N)), owner=A, cfg=w.hub_cfg, store=w.hub)
        elif kind == "readd" and pairs:
            a, b = rnd.choice(pairs)
            write(w.hub, nid(a), f"re {a}", edges=[rel(a, b)], extra=[end(b, f"re {b}")])
        tick(w)


def title_of(st, i):
    rows = st.run("MATCH (c:Concept) WHERE c.owner_id=$o AND c.concept_id=$id RETURN c.title AS t",
                  {"o": A, "id": pkns.key(A, nid(i))})
    return rows[0]["t"] if rows else None


def device_ops(w, rnd, pairs):
    tick(w)
    for _ in range(rnd.randint(2, 10)):
        kind = rnd.choice(["edit", "edit", "deledge", "delnode", "addedge", "newnode", "recreate", "undo"])
        tick(w)
        if kind == "edit":
            i = rnd.randrange(N)
            write(w.dev, nid(i), f"dev-edit {i} {rnd.random()}")
        elif kind == "deledge" and pairs:
            a, b = rnd.choice(pairs)
            q.delete_edge(nid(a), "relates_to", nid(b), owner=A, store=w.dev)
        elif kind == "delnode":
            graph_edit.delete_node(nid(rnd.randrange(N)), owner=A, cfg=w.dev_cfg, store=w.dev)
        elif kind == "addedge":
            a, b = rnd.sample(range(N), 2)
            write(w.dev, nid(a), f"add {a}", edges=[rel(a, b)], extra=[end(b, f"add {b}")])
        elif kind == "newnode":
            i, a = N + rnd.randrange(5), rnd.randrange(N)
            write(w.dev, nid(i), f"new {i}", edges=[rel(i, a)], extra=[end(a, f"newend {a}")])
        elif kind == "recreate" and pairs:
            a, b = rnd.choice(pairs)
            q.delete_edge(nid(a), "relates_to", nid(b), owner=A, store=w.dev)
            tick(w)
            write(w.dev, nid(a), f"rec {a}", edges=[rel(a, b)], extra=[end(b, f"rec {b}")])
        elif kind == "undo":
            i = rnd.randrange(N)
            title = title_of(w.dev, i)
            if title is not None:
                graph_edit.delete_node(nid(i), owner=A, cfg=w.dev_cfg, store=w.dev)
                tick(w)
                write(w.dev, nid(i), title)


def fingerprint(st):
    """허브가 지닌 노드(제목)·엣지·노드 삭제 표식·엣지 삭제 표식."""
    nodes = {r["id"]: r["t"] for r in st.run(
        "MATCH (c:Concept) WHERE c.owner_id=$o RETURN c.concept_id AS id, c.title AS t", {"o": A})}
    edges = sorted((r["s"], r["d"]) for r in st.run(
        "MATCH (a:Concept)-[r:RELATED_TO]->(b:Concept) WHERE a.owner_id=$o AND b.owner_id=$o "
        "RETURN a.concept_id AS s, b.concept_id AS d", {"o": A}))
    tombs = sorted((r["id"], r["da"]) for r in st.run(
        "MATCH (t:_Tombstone) WHERE t.owner_id=$o RETURN t.node_id AS id, t.deleted_at AS da", {"o": A}))
    etombs = sorted((r["s"], r["rel"], r["d"], r["da"]) for r in st.run(
        "MATCH (t:_EdgeTombstone) WHERE t.owner_id=$o RETURN t.src AS s, t.rel AS rel, t.dst AS d, "
        "t.deleted_at AS da", {"o": A}))
    return {"nodes": nodes, "edges": edges, "tombs": tombs, "etombs": etombs}


def apply_chunks(st, chunks):
    for ch in chunks:
        out = sync.apply_upload(owner=A, bundle=json.loads(json.dumps({**ch, **syncclient.HANDLES})), store=st)
        assert out["executed"], out


def clone_hubs(w, k):
    """허브를 닫고 파일을 k번 복사해 다시 연다. [원본, 사본1..사본k]."""
    src = Path(w.hub_cfg.data_dir)
    w.hub.close()
    first = open_store(load_config({"data_dir": str(src)}))
    w.opened.append(first)
    w.hub = first
    out = [first]
    for j in range(k):
        dst = w.tmp / f"hubcopy{j}"
        shutil.copy2(src, dst)
        for ext in (".wal", ".shadow"):
            sib = Path(str(src) + ext)
            if sib.exists():
                shutil.copy2(sib, Path(str(dst) + ext))
        st = open_store(load_config({"data_dir": str(dst)}))
        w.opened.append(st)
        out.append(st)
    return out


def scenario(w, monkeypatch, seed):
    """기기 델타 하나와 그것의 청크들. 청크 크기와 개수 상한은 시드가 정한다."""
    rnd = random.Random(seed)
    pairs = build(w, rnd)
    hub_side_ops(w, rnd, pairs)
    tick(w)
    sync.pull_apply(w.dev, None, owner=A, remote=w.remote)
    device_ops(w, rnd, pairs)
    bundle = json.loads(json.dumps(sync.export_delta(
        owner=A, since=sync.get_cursor(w.dev, "push"), domains=[DOM], store=w.dev)))
    assert bundle["executed"]
    limit = rnd.choice([250, 400, 700, 1200, 2500, 10**9])
    monkeypatch.setattr(sync, "CHUNK_ITEMS", rnd.choice([1, 2, 3, 7, 1000]))
    return bundle, sync.chunk_bundle(bundle, limit=limit)


def shape(chunks):
    return [(len(c["nodes"]), len(c["edges"]), len(c.get("tombstones") or []), len(c.get("edge_tombstones") or []))
            for c in chunks]


def overlap_of(bundle):
    return sorted({t["node_id"] for t in bundle["tombstones"]} & {n["id"] for n in bundle["nodes"]})


@pytest.mark.parametrize("seed,recreated", SEEDS)
def test_chunks_leave_the_hub_as_the_whole_bundle_does(world, monkeypatch, seed, recreated):
    w = world
    bundle, chunks = scenario(w, monkeypatch, seed)
    assert bool(overlap_of(bundle)) is recreated, "시드의 종류가 바뀌었다: 목록을 다시 고른다"
    whole, chunked = clone_hubs(w, 1)
    tick(w)
    apply_chunks(whole, [bundle])
    apply_chunks(chunked, chunks)
    a, b = fingerprint(whole), fingerprint(chunked)
    assert a == b, {"seed": seed, "shape": shape(chunks),
                    "only_whole": {k: [x for x in a[k] if x not in b[k]] for k in ("edges", "tombs", "etombs")},
                    "only_chunked": {k: [x for x in b[k] if x not in a[k]] for k in ("edges", "tombs", "etombs")}}


@pytest.mark.parametrize("seed", CONTROL_SEEDS)
def test_reversed_chunks_leave_a_different_hub(world, monkeypatch, seed):
    """대조: 청크를 거꾸로 적용하면 허브가 달라지는 시드다. 그렇지 않으면 위 비교는 순서 어긋남을 못 본다."""
    w = world
    bundle, chunks = scenario(w, monkeypatch, seed)
    whole, backwards = clone_hubs(w, 1)
    tick(w)
    apply_chunks(whole, [bundle])
    apply_chunks(backwards, list(reversed(chunks)))
    assert fingerprint(whole) != fingerprint(backwards)


# ── 삭제 뒤 같은 id 로 다시 만든 노드 ──────────────────────────────────────────
def edges_of(st, node_id):
    return sorted(r["o"] for r in st.run(
        "MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.owner_id=$o AND a.concept_id=$id "
        "RETURN b.concept_id AS o", {"o": A, "id": pkns.key(A, node_id)}))


def test_a_recreated_node_keeps_its_hub_edges_wherever_the_markers_are_cut(world, monkeypatch):
    """허브의 phoenix 에 엣지 둘. 기기가 phoenix 를 지우고 다시 만든 뒤 다른 노드를 여럿 지운다.
    표식이 개수 상한 경계에서 갈리면 phoenix 의 표식이 먼저 들어가 허브의 옛 엣지를 지웠다."""
    w = world
    tick(w)
    others = [f"concept:fill{i:03d}" for i in range(9)]
    nodes = [{"id": f"concept:keep{i}", "type": "concept", "title": f"keep{i}", "domain": DOM} for i in range(2)]
    nodes += [{"id": "concept:phoenix", "type": "concept", "title": "phoenix v1", "domain": DOM}]
    nodes += [{"id": o, "type": "concept", "title": o, "domain": DOM} for o in others]
    assert frag.merge_fragment({"owner": A, "nodes": nodes, "edges": [
        {"from": "concept:phoenix", "to": "concept:keep0", "type": "relates_to"},
        {"from": "concept:phoenix", "to": "concept:keep1", "type": "relates_to"}]}, store=w.hub)["executed"]
    settle(w)
    settle(w)
    assert edges_of(w.dev, "concept:phoenix") == ["concept:keep0", "concept:keep1"]
    tick(w)
    graph_edit.delete_node("concept:phoenix", owner=A, cfg=w.dev_cfg, store=w.dev)
    tick(w, 3)
    write(w.dev, "concept:phoenix", "phoenix v2")
    for o in others:
        graph_edit.delete_node(o, owner=A, cfg=w.dev_cfg, store=w.dev)
    bundle = json.loads(json.dumps(sync.export_delta(
        owner=A, since=sync.get_cursor(w.dev, "push"), domains=[DOM], store=w.dev)))
    assert [n["id"] for n in bundle["nodes"]] == ["concept:phoenix"] and len(bundle["tombstones"]) == 10
    monkeypatch.setattr(sync, "CHUNK_ITEMS", 3)
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) >= 3
    whole, chunked = clone_hubs(w, 1)
    tick(w)
    apply_chunks(whole, [bundle])
    apply_chunks(chunked, chunks)
    assert edges_of(whole, "concept:phoenix") == ["concept:keep0", "concept:keep1"]
    assert edges_of(chunked, "concept:phoenix") == ["concept:keep0", "concept:keep1"]
    assert fingerprint(whole) == fingerprint(chunked)
