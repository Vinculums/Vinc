# -*- coding: utf-8 -*-
"""동기화·내보내기·공유가 값을 빠짐없이 나르는가(VIN-588) — 실엔진 두 스토어.

한 스토어에 모든 종류의 노드와 근거·신뢰도·출생을 가진 관계를 심고, 번들을 청크로
나눠 새 스토어에 적용한 뒤 **받는 쪽에서 읽어** 값이 같은지 본다. 보내는 쪽 번들만
보면 양쪽이 같이 빈 값을 잃어도 초록이 되므로, 심은 값을 직접 단정한다.
PK 네임스페이싱 두 모드(0·1)를 같은 테스트로 돈다.
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from api import docstore, docsync, export, fragment, query, share, sync
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER = "user:fidelity-owner"
DOMAIN = "vinc/fidelity"

C1, C2 = "concept:fid-c1", "concept:fid-c2"
D0, D1 = "decision:fid-d0", "decision:fid-d1"
R0, R1, R2 = "record:fid-r0", "record:fid-r1", "record:fid-r2"
E1 = "episode:fid-e1"

EPISODE_PROPS = {"gamma": 3, "nested": {"k": "v"}}

#: (from, type, to, evidence, confidence, created) — 근거 열을 가진 관계를 조합마다 하나씩.
EDGES = [
    (C1, "relates_to", C2, "ev relates", 0.7, "2025-05-01T00:00:00.000Z"),
    (C1, "prerequisite_of", C2, "ev prerequisite", 0.6, "2025-05-02T00:00:00.000Z"),
    (C1, "derives_from", C2, "ev derives", 0.5, "2025-05-03T00:00:00.000Z"),
    (C1, "inherits_from", C2, "ev inherits", 0.4, "2025-05-04T00:00:00.000Z"),
    (D1, "derives_from", C1, "ev decision derives", 0.45, "2025-05-05T00:00:00.000Z"),
    (C2, "prerequisite_of", D0, "ev concept to decision", 0.35, "2025-05-06T00:00:00.000Z"),
    (D1, "inherits_from", D0, "ev decision lineage", 0.3, "2025-05-07T00:00:00.000Z"),
    (E1, "about", C1, "ev episode about", 0.85, "2025-05-08T00:00:00.000Z"),
    (E1, "about", D1, "ev episode about decision", 0.25, "2025-05-09T00:00:00.000Z"),
    (D1, "about", C1, "ev decision about", 0.8, "2025-05-10T00:00:00.000Z"),
    (R1, "relates_to", C1, "ev record attaches", 0.55, "2025-05-11T00:00:00.000Z"),
    (R2, "derives_from", D1, "ev record on decision", 0.42, "2025-05-12T00:00:00.000Z"),
    (R1, "supersedes", R0, "ev record supersedes", 0.95, "2025-05-13T00:00:00.000Z"),
    (D1, "supersedes", D0, "ev decision supersedes", 0.9, "2025-05-14T00:00:00.000Z"),
]


def _nodes() -> list[dict]:
    return [
        {"id": C1, "type": "concept", "title": "Fidelity concept one", "domain": DOMAIN,
         "tags": ["t1"], "props": {"alpha": 1}, "one_liner": "C1 in one line",
         "content_time": "2025-03", "created": "2025-01-02T03:04:05.000Z"},
        # props.note 가 있어도 저작한 one_liner 가 이겨야 한다(받는 쪽 승격 금지).
        {"id": C2, "type": "concept", "title": "Fidelity concept two", "domain": DOMAIN,
         "props": {"note": "a note that must not become the one_liner"},
         "one_liner": "C2 in one line"},
        {"id": D0, "type": "decision", "title": "Fidelity decision old", "domain": DOMAIN,
         "props": {"why": "old"}},
        {"id": D1, "type": "decision", "title": "Fidelity decision new", "domain": DOMAIN,
         "props": {"why": "new"}, "created": "2026-02-03T00:00:00.000Z"},
        {"id": R0, "type": "record", "title": "Fidelity record old", "domain": DOMAIN,
         "props": {"value": 1}, "valid_from": "2025-12-01", "valid_until": "2026-03-01"},
        {"id": R1, "type": "record", "title": "Fidelity record new", "domain": DOMAIN,
         "props": {"value": 2}, "content_time": "2026-03", "valid_from": "2026-03-01",
         "valid_until": "2026-12"},
        {"id": R2, "type": "record", "title": "Fidelity record on a decision",
         "domain": DOMAIN, "props": {"value": 3}},
        {"id": E1, "type": "episode", "title": "Fidelity episode", "domain": DOMAIN,
         "tags": ["e"], "props": EPISODE_PROPS, "date": "2026-03-04",
         "summary": "Episode summary text"},
    ]


def _seed(st) -> None:
    edges = [{"from": s, "type": t, "to": d, "evidence": ev, "confidence": conf,
              "created": born} for s, t, d, ev, conf, born in EDGES]
    out = fragment.merge_fragment({"owner": OWNER, "nodes": _nodes(), "edges": edges},
                                  store=st)
    assert out["executed"], out
    # 경고(결정의 유효구간 안내 등)는 있어도 되지만 빠진 노드·관계는 없어야 한다.
    assert out["merged"]["nodes"]["created"] == len(_nodes()), out
    assert out["merged"]["edges"]["created"] == len(EDGES), out
    assert not [w for w in out.get("warnings") or [] if "skipped" in w], out


@pytest.fixture(scope="module", params=["0", "1"])
def stores(request, tmp_path_factory):
    """테스트마다 새 스토어를 여는 공장. 모듈이 끝나면 연 것을 전부 닫는다."""
    import os
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded",
                      MEMORY_EMBEDDING_MODEL="local-en")
    opened = []

    def make(tag: str):
        cfg = load_config({"data_dir": str(tmp_path_factory.mktemp(tag) / "db")})
        st = open_store(cfg)
        assert st is not None, "embedded store did not open"
        st.provision(dim=cfg.embedding_dim)
        opened.append(st)
        return st

    try:
        yield make
    finally:
        for st in opened:
            try:
                st.close()
            except Exception:      # noqa: BLE001 — 정리 실패가 결과를 가리지 않게
                pass
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _ship(src, dst, *, since: int = 0) -> dict:
    """보내는 쪽 번들을 청크로 나눠(=push 와 같은 모양) 받는 쪽에 적용한다."""
    bundle = sync.export_delta(owner=OWNER, since=since, everything=True, store=src)
    assert bundle["executed"], bundle
    for chunk in sync.chunk_bundle(bundle):
        res = sync.apply_delta(owner=OWNER, bundle=chunk, store=dst)
        assert res["executed"], res
        # 저작 안내(대체 구간·고아 결정)는 그대로 나온다. 떨어진 것만 없어야 한다.
        assert not [w for w in res["warnings"] if "skipped" in w], res
    return bundle


def _edge_values(st) -> dict:
    """스토어가 지금 나르는 관계 값. 키는 (from, type, to)."""
    bundle = sync.export_delta(owner=OWNER, everything=True, store=st)
    return {(e["from"], e["type"], e["to"]): (e.get("evidence"), e.get("confidence"),
                                              e.get("created"))
            for e in bundle["edges"]}


def _stored_attach(st, src: str, dl: str, dp: str, dst: str) -> dict:
    """ATTACHES_TO 를 열에서 직접 읽는다(번들이 아니라 저장된 값)."""
    rows = st.run(f"MATCH (a:Record)-[r:ATTACHES_TO]->(b:{dl}) "
                  f"WHERE a.record_id = $s AND b.{dp} = $d "
                  "RETURN r.type AS t, r.evidence AS ev, r.confidence AS conf, "
                  "r.created AS born", {"s": pkns.key(OWNER, src), "d": pkns.key(OWNER, dst)})
    assert len(rows) == 1, rows
    return rows[0]


def _node(st, nid: str) -> dict:
    got = query.node(nid, owner=OWNER, store=st)
    assert got.get("node"), got
    return got["node"]


# ── F3·F4: 두 스토어 왕복 ──────────────────────────────────────────────────────
def test_two_store_round_trip_carries_edge_evidence_and_late_node_fields(stores):
    a, b = stores("fid-src"), stores("fid-peer")
    _seed(a)
    _ship(a, b)

    want = {(s, t, d): (ev, conf, born) for s, t, d, ev, conf, born in EDGES}
    # 보내는 쪽 번들이 심은 값을 나르고(F3), 받는 쪽이 같은 값을 다시 낸다.
    assert {k: v for k, v in _edge_values(a).items() if k in want} == want
    assert {k: v for k, v in _edge_values(b).items() if k in want} == want
    # 번들이 아니라 받는 쪽 **열**에서도 확인한다(기록 부착은 r.type 에 원 타입이 있다).
    got = _stored_attach(b, R1, "Concept", "concept_id", C1)
    assert (got["t"], got["ev"], got["conf"], got["born"]) == (
        "relates_to", "ev record attaches", 0.55, "2025-05-11T00:00:00.000Z")
    got = _stored_attach(b, R2, "Decision", "decision_id", D1)
    assert (got["t"], got["ev"], got["conf"]) == ("derives_from", "ev record on decision", 0.42)

    # F4 — 개념 one_liner 와 사건 props 가 받는 쪽에 그대로 있다.
    assert _node(b, C1)["one_liner"] == "C1 in one line"
    assert _node(b, C2)["one_liner"] == "C2 in one line"      # props.note 승격이 아니다
    assert _node(b, E1)["props"] == EPISODE_PROPS
    # 이미 나르던 값도 그대로다(회귀 방지).
    e1 = _node(b, E1)
    assert (e1["date"], e1["summary"]) == ("2026-03-04", "Episode summary text")
    assert _node(b, C1)["content_time"] == "2025-03"
    assert _node(b, C1)["created"] == "2025-01-02T03:04:05.000Z"


def test_late_field_edits_travel_as_a_delta(stores):
    """props 만 고친 사건, one_liner 만 고친 개념도 변경으로 세져 다음 델타에 실린다."""
    a, b = stores("fid-delta-src"), stores("fid-delta-peer")
    _seed(a)
    first = _ship(a, b)
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": E1, "type": "episode", "props": {"gamma": 4}},
        {"id": C1, "type": "concept", "one_liner": "C1 said again"}]}, store=a)
    assert out["executed"] and not out.get("partial"), out
    delta = _ship(a, b, since=first["next_cursor"])
    assert {n["id"] for n in delta["nodes"]} >= {E1, C1}
    assert _node(b, E1)["props"] == {"gamma": 4}
    assert _node(b, C1)["one_liner"] == "C1 said again"


def test_a_bundle_without_the_new_keys_does_not_erase_them(stores):
    """옛 클라이언트의 번들에는 one_liner·사건 props 키가 없다. 받는 쪽은 그것을 지우기로
    읽으면 안 된다(제목 같은 다른 필드는 그대로 반영한다)."""
    a, b = stores("fid-old-src"), stores("fid-old-peer")
    _seed(a)
    first = _ship(a, b)
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": C1, "type": "concept", "title": "Retitled by an old client"},
        {"id": E1, "type": "episode", "title": "Episode retitled by an old client"}]},
        store=a)
    assert out["executed"], out
    delta = sync.export_delta(owner=OWNER, since=first["next_cursor"], everything=True,
                              store=a)
    for n in delta["nodes"]:
        n.pop("one_liner", None)
        if n["type"] == "episode":
            n.pop("props", None)
    res = sync.apply_delta(owner=OWNER, bundle=delta, store=b)
    assert res["executed"], res
    assert not [w for w in res["warnings"] if "skipped" in w], res
    assert _node(b, C1)["title"] == "Retitled by an old client"
    assert _node(b, C1)["one_liner"] == "C1 in one line"
    assert _node(b, E1)["title"] == "Episode retitled by an old client"
    assert _node(b, E1)["props"] == EPISODE_PROPS

    # 새 노드여도 마찬가지다: 키가 없으면 쓰지 않는다(빈 값으로 만들지 않는다).
    c = stores("fid-old-fresh")
    full = sync.export_delta(owner=OWNER, everything=True, store=a)
    for n in full["nodes"]:
        if n["type"] == "episode":
            n.pop("props", None)
    res = sync.apply_delta(owner=OWNER, bundle=full, store=c)
    assert res["executed"], res
    assert _node(c, E1)["props"] in ({}, None)


def test_an_empty_late_field_does_not_erase_a_value_the_base_never_saw(stores):
    """필드가 동기화되기 전부터 갈라진 값. 옛 동기화가 사건 props 를 안 날라서 한 기기에는
    빈 props 가 남아 있다. base 가 그 키를 모르면 빈 값은 지우기가 아니라 못 받은 것이다:
    그 기기가 나중에 제목을 고쳐 보내도 찬 쪽의 props 가 남아야 하고, 거꾸로 찬 쪽이
    보내면 빈 쪽이 받아야 한다."""
    hub, device = stores("fid-gap-hub"), stores("fid-gap-device")
    base_node = {"id": E1, "type": "episode", "title": "Gap episode", "domain": DOMAIN,
                 "date": "2026-03-04"}
    for st, node in ((hub, {**base_node, "props": EPISODE_PROPS}), (device, base_node)):
        out = fragment.merge_fragment({"owner": OWNER, "nodes": [node]}, store=st)
        assert out["executed"], out
    out = fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": E1, "type": "episode", "title": "Retitled on the device"}]}, store=device)
    assert out["executed"], out
    delta = sync.export_delta(owner=OWNER, everything=True, store=device)
    assert next(n for n in delta["nodes"] if n["id"] == E1)["props"] == {}
    res = sync.apply_delta(owner=OWNER, bundle=delta, store=hub)
    assert res["executed"], res
    assert _node(hub, E1)["title"] == "Retitled on the device"
    assert _node(hub, E1)["props"] == EPISODE_PROPS

    # 같은 빈 번들이 다시 와도(base 가 이제 빈 props 를 안다) 찬 값은 그대로다.
    res = sync.apply_delta(owner=OWNER, bundle=delta, store=hub)
    assert res["executed"], res
    assert _node(hub, E1)["props"] == EPISODE_PROPS

    back = sync.export_delta(owner=OWNER, everything=True, store=hub)
    res = sync.apply_delta(owner=OWNER, bundle=back, store=device)
    assert res["executed"], res
    assert _node(device, E1)["props"] == EPISODE_PROPS


# ── 청크 상한: 근거 문장이 실려도 청크가 상한을 넘지 않는다 ─────────────────────
def test_chunks_count_edge_bytes_and_stay_under_the_limit():
    nodes = [{"id": f"concept:{i}", "type": "concept", "title": "n" * 150, "_seq": i}
             for i in range(8)]
    edges = [{"from": f"concept:{i}", "to": f"concept:{j}", "type": "relates_to",
              "evidence": "e" * 120, "confidence": 0.5}
             for i in range(8) for j in range(8) if i < j]
    limit = 1200
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": edges}, limit=limit)
    carried = []
    for ch in chunks:
        assert len(json.dumps(ch["nodes"] + ch["edges"], ensure_ascii=False).encode("utf-8")) \
            <= limit + 64                       # 리스트 괄호·쉼표만큼의 여유
        have = {n["id"] for n in ch["nodes"]}
        for e in ch["edges"]:
            assert e["from"] in have and e["to"] in have, "엣지 끝점이 청크에 없다"
        carried += ch["edges"]
    key = lambda e: (e["from"], e["type"], e["to"])   # noqa: E731
    assert sorted(map(key, carried)) == sorted(map(key, edges))   # 빠짐도 겹침도 없다


# ── F17: 내보내기 파일 ─────────────────────────────────────────────────────────
def _export_sources(st) -> tuple[dict, dict, dict]:
    """`vinc export` 의 인프로세스 경로와 같은 원천(`export._payload`)."""
    nodes = sync.export_delta(owner=OWNER, since=0, everything=True, store=st)
    export._enrich(nodes, query.graph(owner=OWNER, store=st))
    docs = docsync.export_docs(st, owner=OWNER, since=0, everything=True)
    listed = docstore.list_docs(st, owner=OWNER, detail="full")
    return nodes, docs, listed


def _front(text: str) -> dict:
    head = text.split("---\n", 2)[1]
    return {k: json.loads(v) for k, v in (line.split(": ", 1) for line in head.splitlines())}


def test_export_files_carry_time_fields_episode_props_and_edge_values(stores):
    st = stores("fid-export")
    _seed(st)
    new = docstore.put_doc(st, owner=OWNER, doc_id="fid-doc-new", title="New doc",
                           text="# New\n\nThe current text.\n", topic=DOMAIN)
    assert new["executed"], new
    old = docstore.put_doc(st, owner=OWNER, doc_id="fid-doc-old", title="Old doc",
                           text="# Old\n\nThe retired text.\n", topic=DOMAIN,
                           content_time="2025-06", valid_from="2025-01",
                           valid_until="2026-01", superseded_by="fid-doc-new")
    assert old["executed"], old
    sources = _export_sources(st)

    md = export.build_files(OWNER, "md", *sources)
    c1 = _front(md["nodes/concept/fid-c1.md"].decode("utf-8"))
    assert list(c1)[:8] == ["id", "kind", "title", "domain", "tags", "valid_from",
                            "valid_until", "one_liner"]          # 기존 키와 순서는 그대로
    assert (c1["content_time"], c1["created"], c1["one_liner"]) == (
        "2025-03", "2025-01-02T03:04:05.000Z", "C1 in one line")
    e1_text = md["nodes/episode/fid-e1.md"].decode("utf-8")
    e1 = _front(e1_text)
    assert (e1["date"], e1["summary"]) == ("2026-03-04", "Episode summary text")
    assert json.loads(e1_text.split("```json\n", 1)[1].split("\n```", 1)[0]) == EPISODE_PROPS
    doc = _front(md["documents/fid-doc-old.md"].decode("utf-8"))
    assert list(doc)[:6] == ["doc_id", "title", "topic", "origin", "updated_at",
                             "content_hash"]
    assert (doc["content_time"], doc["valid_from"], doc["valid_until"],
            doc["superseded_by"]) == ("2025-06", "2025-01", "2026-01", "fid-doc-new")
    assert doc["created_at"]
    # 기록 대체의 근거는 graph 읽기에 없는 관계라도 번들에서 그대로 온다.
    r1 = md["nodes/record/fid-r1.md"].decode("utf-8")
    link = "[" * 2 + R0 + "]" * 2              # 인용 게이트가 그래프 id 로 읽지 않게 조립
    assert f"- supersedes -> {link}" in r1 and "ev record supersedes" in r1

    csvs = export.build_files(OWNER, "csv", *sources)
    edges = list(csv.DictReader(io.StringIO(csvs["edges.csv"].decode("utf-8"))))
    assert list(edges[0]) == ["from", "to", "type", "evidence", "confidence", "created"]
    rows = {(e["from"], e["type"], e["to"]): e for e in edges}
    for s, t, d, ev, conf, born in EDGES:
        row = rows[(s, t, d)]
        assert (row["evidence"], float(row["confidence"]), row["created"]) == (ev, conf, born)
    nodes = list(csv.DictReader(io.StringIO(csvs["nodes.csv"].decode("utf-8"))))
    assert list(nodes[0]) == ["id", "type", "title", "domain", "tags", "valid_from",
                              "valid_until", "one_liner", "props", "content_time",
                              "created", "date", "summary"]
    by_id = {n["id"]: n for n in nodes}
    assert json.loads(by_id[E1]["props"]) == EPISODE_PROPS
    assert (by_id[E1]["date"], by_id[E1]["summary"]) == ("2026-03-04", "Episode summary text")
    assert by_id[C1]["created"] == "2025-01-02T03:04:05.000Z"
    docs = list(csv.DictReader(io.StringIO(csvs["documents.csv"].decode("utf-8"))))
    assert list(docs[0]) == ["doc_id", "title", "topic", "origin", "updated_at",
                             "content_hash", "text", "content_time", "created_at",
                             "valid_from", "valid_until", "superseded_by"]
    old_row = next(d for d in docs if d["doc_id"] == "fid-doc-old")
    assert (old_row["valid_until"], old_row["superseded_by"]) == ("2026-01", "fid-doc-new")


# ── F15: 공유 팩의 대체 관계 ───────────────────────────────────────────────────
def test_share_pack_carries_evidenced_supersedes_edges(stores):
    st, peer = stores("fid-share"), stores("fid-share-peer")
    _seed(st)
    pack = share.build_share_pack(owner=OWNER, domain=DOMAIN, store=st)
    assert pack["executed"], pack
    edges = {(e["from"], e["type"], e["to"]): e for e in pack["fragment"]["edges"]}
    for s, d, ev, conf in ((R1, R0, "ev record supersedes", 0.95),
                           (D1, D0, "ev decision supersedes", 0.9)):
        e = edges[(s, "supersedes", d)]
        assert (e["evidence"], e["confidence"]) == (ev, conf)
    res = share.import_share_pack(pack, owner=OWNER, store=peer)
    assert res["executed"], res
    for lbl, pk, s, d, ev in (("Record", "record_id", R1, R0, "ev record supersedes"),
                              ("Decision", "decision_id", D1, D0, "ev decision supersedes")):
        rows = peer.run(f"MATCH (a:{lbl})-[r:SUPERSEDES]->(b:{lbl}) "
                        f"WHERE a.{pk} = $s AND b.{pk} = $d RETURN r.evidence AS ev",
                        {"s": pkns.key(OWNER, s), "d": pkns.key(OWNER, d)})
        assert [r["ev"] for r in rows] == [ev]


def test_share_pack_leaves_out_an_unevidenced_supersedes_and_says_so(stores):
    """쓰기 경로가 근거 없는 대체를 거절하므로, 옛 행에 그런 관계가 있으면 팩은 빼고 말한다."""
    st = stores("fid-share-bare")
    _seed(st)
    st.run("MATCH (a:Record), (b:Record) WHERE a.record_id = $s AND b.record_id = $d "
           "CREATE (a)-[:SUPERSEDES {created: ''}]->(b)",
           {"s": pkns.key(OWNER, R2), "d": pkns.key(OWNER, R0)})
    pack = share.build_share_pack(owner=OWNER, domain=DOMAIN, store=st)
    assert pack["executed"], pack
    assert (R2, "supersedes", R0) not in {(e["from"], e["type"], e["to"])
                                          for e in pack["fragment"]["edges"]}
    assert any(R2 in w and "no evidence" in w for w in pack["manifest"].get("warnings", []))
