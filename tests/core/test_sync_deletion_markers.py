"""삭제가 기기와 허브 사이를 한 번 건너가고 거기서 멈춘다. 오케스트레이터(`sync.run`)를 여러 번 돌려 본다.

기기는 실제 동기화 클라이언트(`vinc_mcp/syncclient`)로 보내고, 허브의 네 끝점은 두 번째 스토어 위에서 사이드카
라우트가 부르는 함수로 답한다(본문은 JSON 으로 오가고, pull 답에는 허브 벽시계 `clock` 이 실린다). 기기는 둘이다.
오간 삭제 표식은 끝점마다 센다.

- 받은 새 노드도 받는 쪽 시계를 민다: 느린 기기가 막 받은 노드를 지우면 그 삭제가 허브에 남고, 허브가 빠른 기기에서
  막 받은 노드를 지우면 그 삭제가 기기에 남는다.
- 받는 쪽이 이미 같은 삭제를 알면 표식에 새 순번을 매기지 않는다. 그래서 표식이 동기화마다 오가지 않는다. 지운 것을
  다시 만든 뒤의 삭제는 그대로 건너간다.
- 허브가 받은 문서 삭제도 표식을 남긴다. 그래서 다른 기기가 그 삭제를 받는다.
- 한 스토어의 시계(`last_hlc`)는 그 스토어의 소유자들이 함께 쓴다. 다른 소유자의 업로드는 그 시계를 허브 벽시계 +
  `revstamp.MAX_SKEW_MS` 까지만 민다. 한 소유자 안의 인과 순서는 그대로다.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from api import docstore as ds
from api import docsync, revstamp, sync
from api import fragment as frag
from api import query as q
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import import_driver
from vinc_mcp import syncclient

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

A = "user:marker-dev"
B = "user:marker-other"
T0 = 1_800_000_000_000
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
DAY = 24 * HOUR
SKEW = "clock_skew"


class _Clocks:
    """허브의 벽시계와 기기의 어긋남. 허브 끝점 안에서는 허브 시각, 밖에서는 기기 시각이다."""

    def __init__(self):
        self.hub = T0
        self.offset = 0
        self.side = "device"
        self.sends_clock = True

    def __call__(self) -> int:
        return self.hub + (0 if self.side == "hub" else self.offset)

    @contextlib.contextmanager
    def at_hub(self):
        before, self.side = self.side, "hub"
        try:
            yield
        finally:
            self.side = before


def _markers(path: str, body: dict, out: dict) -> tuple[int, int]:
    """한 요청이 허브로 올린 삭제 표식 수와 허브가 내려 준 삭제 표식 수(노드·엣지·문서)."""
    if path == "/sync/push":
        bundle = body.get("bundle") or {}
        return len(bundle.get("tombstones") or []) + len(bundle.get("edge_tombstones") or []), 0
    if path == "/sync/pull":
        return 0, len(out.get("tombstones") or []) + len(out.get("edge_tombstones") or [])
    if path == "/sync/docs-push":
        return len((body.get("payload") or {}).get("tombstones") or []), 0
    return 0, len(out.get("tombstones") or [])


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clocks = _Clocks()
    monkeypatch.setattr(revstamp, "_wall_ms", clocks)
    opened = []
    traffic: list[tuple[str, int, int]] = []

    def make(name):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        opened.append(st)
        return st

    hub, dev, dev2 = make("hub"), make("device"), make("device2")

    def post(base, path, token, payload, *, timeout):
        body = json.loads(json.dumps(payload))
        with clocks.at_hub():
            if path == "/sync/push":
                out = sync.apply_upload(owner=A, bundle=body["bundle"], store=hub)
            elif path == "/sync/pull":
                out = revstamp.with_clock(sync.export_delta(
                    owner=A, since=int(body["since"]), domains=["marker"], store=hub))
            elif path == "/sync/docs-push":
                out = docsync.import_upload(hub, owner=A, payload=body["payload"])
            elif path == "/sync/docs-pull":
                out = revstamp.with_clock(docsync.export_docs(hub, owner=A, since=int(body["since"])))
            else:
                raise AssertionError(path)
        out = json.loads(json.dumps(out, default=str))
        if not clocks.sends_clock:
            out.pop("clock", None)            # pull 답에 자기 시계를 싣지 않는 허브
        traffic.append((path, *_markers(path, body, out)))
        return out

    monkeypatch.setattr(syncclient, "_post", post)
    for st in (dev, dev2):
        sync.set_selected_domains(st, ["marker"])
    try:
        yield hub, dev, dev2, clocks, sync.RemoteHTTP("vinc_sk_test", "http://hub.test"), traffic
    finally:
        for st in opened:
            st.close()


def _write(store, title, nid="concept:n", owner=A):
    res = frag.merge_fragment({"owner": owner, "nodes": [
        {"id": nid, "type": "concept", "title": title, "domain": "marker"}], "edges": []}, store=store)
    assert res["executed"], res


def _row(store, nid="concept:n", owner=A):
    rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
                     "RETURN c.title AS title, c.updated_at AS ua",
                     {"id": pkns.key(owner, nid), "o": owner})
    return rows[0] if rows else None


def _edges(store):
    rows = store.run("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.concept_id = $s "
                     "AND b.concept_id = $d RETURN count(r) AS c",
                     {"s": pkns.key(A, "concept:s"), "d": pkns.key(A, "concept:d")})
    return int(rows[0]["c"]) if rows else 0


def _doc(store, did):
    rows = store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.content_hash AS h", {"id": did})
    return rows[0]["h"] if rows else None


def _put_doc(store, did, text):
    assert ds.put_doc(store, owner=A, text=text, doc_id=did, title="Note",
                      origin="marker-note").get("executed")


def _run(dev, remote, clocks, offset=None):
    """기기 한 대의 동기화 한 번. `offset` 이 있으면 그 기기의 어긋남으로 돈다."""
    before = clocks.offset
    if offset is not None:
        clocks.offset = offset
    try:
        out = sync.run(dev, None, owner=A, remote=remote)
    finally:
        clocks.offset = before
    assert out["executed"], out
    assert SKEW not in out["push"] and SKEW not in out["docs_push"], out
    return out


def _seed_hub(hub, clocks, did):
    with clocks.at_hub():
        res = frag.merge_fragment({"owner": A, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": "marker"}
            for nid in ("concept:n", "concept:s", "concept:d")],
            "edges": [{"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=hub)
        assert res["executed"], res
        _put_doc(hub, did, "a note on the hub")


# ── 받은 새 노드도 받는 쪽 시계를 민다 ────────────────────────────────────────
@pytest.mark.parametrize("behind", [2 * HOUR, 26 * HOUR, 400 * DAY], ids=["2h", "26h", "400d"])
def test_a_slow_device_that_deletes_a_node_it_received_as_new_deletes_it_on_the_hub(world, behind):
    hub, dev, _dev2, clocks, remote, _traffic = world
    clocks.offset = -behind
    clocks.hub += SECOND
    with clocks.at_hub():
        _write(hub, "made on the hub", nid="concept:fresh")
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    got = _row(dev, "concept:fresh")
    assert got is not None and got["title"] == "made on the hub"
    clock = revstamp._fmt(int(revstamp.meta_get(dev, "last_hlc", "0")))
    clocks.hub += SECOND
    assert q.delete_node("concept:fresh", owner=A, store=dev).get("executed")
    clocks.hub += SECOND
    out = _run(dev, remote, clocks)
    assert _row(hub, "concept:fresh") is None, out
    for _ in range(2):
        clocks.hub += SECOND
        _run(dev, remote, clocks)
    assert _row(hub, "concept:fresh") is None and _row(dev, "concept:fresh") is None
    # 받은 노드의 스탬프까지 기기 시계가 따라갔었다. 그래서 그 뒤의 삭제가 노드 스탬프 뒤에 찍혔다.
    assert clock >= got["ua"]


def test_a_node_a_fast_device_made_and_the_hub_then_deleted_is_deleted_on_the_device(world):
    """허브 쪽도 같다. 상한 안에서 빠른 기기가 만든 노드를 허브가 처음 받고 곧 지우면, 그 삭제가 노드 스탬프 뒤에 찍힌다."""
    hub, dev, _dev2, clocks, remote, _traffic = world
    clocks.offset = 4 * MINUTE
    _run(dev, remote, clocks)
    _write(dev, "made on a fast device", nid="concept:fresh")
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    got = _row(hub, "concept:fresh")
    assert got is not None and got["title"] == "made on a fast device"
    clocks.hub += SECOND
    with clocks.at_hub():
        assert q.delete_node("concept:fresh", owner=A, store=hub).get("executed")
    clocks.hub += SECOND
    out = _run(dev, remote, clocks)
    assert _row(dev, "concept:fresh") is None, out
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    assert _row(hub, "concept:fresh") is None and _row(dev, "concept:fresh") is None


@pytest.mark.parametrize("sends_clock", [True, False], ids=["hub-sends-its-clock", "hub-sends-no-clock"])
def test_a_new_node_stamped_far_ahead_does_not_hold_the_device_clock(world, sends_clock):
    """새로 받은 노드도 기존 노드와 같은 한도로 따라간다. 허브 시계가 적혀 있으면 먼 스탬프는 따라가지 않고, 적힌 허브
    시계가 없으면 따라간 시계를 다음 쓰기가 벽시계로 되돌린다. 어느 쪽이든 기기의 다음 편집은 허브에 닿는다.
    (받은 먼 노드 자체는 기기가 다시 올리고 허브가 받지 않는다. 그것은 이 규칙과 무관하게 그렇다.)"""
    hub, dev, _dev2, clocks, remote, _traffic = world
    clocks.sends_clock = sends_clock
    _run(dev, remote, clocks)
    now = clocks.hub
    clocks.hub = revstamp._parse_ms("2999-01-01T00:00:00.000Z")
    with clocks.at_hub():
        _write(hub, "far", nid="concept:far")
    clocks.hub = now + SECOND
    first = sync.run(dev, None, owner=A, remote=remote)
    assert _row(dev, "concept:far")["title"] == "far"
    assert first["push"].get(SKEW, []) in ([], ["concept:far"]), first
    clocks.hub += SECOND
    _write(dev, "after the far node", nid="concept:after")
    assert _row(dev, "concept:after")["ua"] <= revstamp._fmt(clocks() + revstamp.MAX_SKEW_MS)
    clocks.hub += SECOND
    out = sync.run(dev, None, owner=A, remote=remote)
    assert out["push"].get(SKEW, []) in ([], ["concept:far"]), out
    assert _row(hub, "concept:after")["title"] == "after the far node"


# ── 표식은 한 번 건너가고 멈춘다 ──────────────────────────────────────────────
def _per_sync(traffic, start):
    """`start` 뒤의 요청을 (올린 표식, 내려온 표식) 합으로."""
    return sum(t[1] for t in traffic[start:]), sum(t[2] for t in traffic[start:])


@pytest.mark.parametrize("offset", [0, -2 * HOUR], ids=["in-step", "2h-slow"])
def test_deletion_markers_cross_once_and_then_stop(world, offset):
    """기기가 노드 하나·엣지 하나·문서 하나를 지운 뒤 여섯 번 동기화한다. 첫 동기화에 표식 셋이 허브로 가고, 둘째
    동기화에 허브의 표식 셋이 기기로 온다(허브는 누가 보냈는지 모른다). 그 뒤로는 어느 쪽으로도 표식이 오가지 않는다."""
    hub, dev, _dev2, clocks, remote, traffic = world
    clocks.offset = offset
    did = ds.doc_id_for_origin(A, "marker-note")
    _seed_hub(hub, clocks, did)
    _run(dev, remote, clocks)
    assert _row(dev) is not None and _edges(dev) == 1 and _doc(dev, did) is not None
    clocks.hub += SECOND
    assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    assert ds.delete_doc(dev, did, owner=A).get("executed")
    counts = []
    for _ in range(6):
        clocks.hub += SECOND
        start = len(traffic)
        _run(dev, remote, clocks)
        counts.append(_per_sync(traffic, start))
    assert _row(hub) is None and _edges(hub) == 0 and _doc(hub, did) is None
    assert _row(dev) is None and _edges(dev) == 0 and _doc(dev, did) is None
    assert counts == [(3, 0), (0, 3), (0, 0), (0, 0), (0, 0), (0, 0)], counts


def test_a_second_device_learns_each_deletion_and_echoes_it_once(world):
    """둘째 기기는 허브의 표식을 처음 받으므로 자기 순번으로 적고 한 번 되돌려 보낸다. 허브는 그 삭제를 이미 알아
    순번을 매기지 않으므로 거기서 멈춘다."""
    hub, dev, dev2, clocks, remote, traffic = world
    did = ds.doc_id_for_origin(A, "marker-note")
    _seed_hub(hub, clocks, did)
    _run(dev, remote, clocks)
    _run(dev2, remote, clocks)
    clocks.hub += SECOND
    assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    counts = []
    for _ in range(4):
        clocks.hub += SECOND
        start = len(traffic)
        _run(dev2, remote, clocks)
        counts.append(_per_sync(traffic, start))
        _run(dev, remote, clocks)
    assert _row(dev2) is None and _edges(dev2) == 0
    assert counts[0] == (2, 2) and counts[1:] == [(0, 0)] * 3, counts


def test_a_deletion_made_after_the_node_and_edge_are_made_again_still_crosses(world):
    """표식에 순번을 덜 매겨도 진짜 삭제는 잃지 않는다. 첫 기기가 지운 노드와 엣지를 둘째 기기가 다시 만들고(그 뒤로
    표식이 오가며 다시 만든 엣지를 지우지 않는다), 두 기기가 그것을 다시 지운다.

    다시 만든 엣지는 첫 기기에 오지 않는다: 허브는 끝점의 필드가 그대로면 끝점을 다시 적지 않고, 엣지는 양끝이 같은
    묶음에 있을 때만 나간다. 그래서 둘째 삭제는 엣지를 가진 둘째 기기가 한다."""
    hub, dev, dev2, clocks, remote, _traffic = world
    did = ds.doc_id_for_origin(A, "marker-note")
    _seed_hub(hub, clocks, did)
    for st in (dev, dev2):
        _run(st, remote, clocks)
    clocks.hub += SECOND
    assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    for st in (dev, dev2, dev):
        clocks.hub += SECOND
        _run(st, remote, clocks)
    assert [_row(st) for st in (hub, dev, dev2)] == [None] * 3
    assert [_edges(st) for st in (hub, dev, dev2)] == [0, 0, 0]
    clocks.hub += SECOND
    _write(dev2, "made again")
    assert frag.merge_fragment({"owner": A, "nodes": [], "edges": [
        {"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=dev2)["executed"]
    for st in (dev2, dev, dev2, dev):
        clocks.hub += SECOND
        _run(st, remote, clocks)
    assert [(_row(st) or {}).get("title") for st in (hub, dev, dev2)] == ["made again"] * 3
    assert [_edges(st) for st in (hub, dev2)] == [1, 1]
    clocks.hub += SECOND
    assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev2).get("executed")
    for st in (dev, dev2, dev, dev2):
        clocks.hub += SECOND
        _run(st, remote, clocks)
    assert [_row(st) for st in (hub, dev, dev2)] == [None] * 3
    assert [_edges(st) for st in (hub, dev, dev2)] == [0, 0, 0]


# ── 허브가 받은 문서 삭제도 표식을 남긴다 ─────────────────────────────────────
def _doc_marker(store, did):
    rows = store.run("MATCH (t:_DocTombstone) WHERE t.doc_id = $id "
                     "RETURN t.owner_id AS o, t.deleted_at AS da", {"id": did})
    return rows[0] if rows else None


def test_a_document_deleted_on_one_device_is_deleted_on_the_other(world):
    hub, dev, dev2, clocks, remote, _traffic = world
    did = ds.doc_id_for_origin(A, "marker-note")
    _put_doc(dev, did, "written on the first device")
    _run(dev, remote, clocks)
    clocks.hub += SECOND
    _run(dev2, remote, clocks)
    assert _doc(dev2, did) == _doc(hub, did) == _doc(dev, did) is not None
    stale = docsync.export_docs(dev, owner=A)
    clocks.hub += SECOND
    assert ds.delete_doc(dev, did, owner=A).get("executed")
    gone_at = _doc_marker(dev, did)["da"]
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    assert _doc(hub, did) is None
    clocks.hub += SECOND
    out = _run(dev2, remote, clocks)
    assert _doc(dev2, did) is None, out
    assert out["docs_pull"]["deleted"] == 1, out
    # 허브의 표식은 원래 삭제 시각을 지닌다.
    assert _doc_marker(hub, did) == {"o": A, "da": gone_at}
    # 삭제 전의 사본을 다시 올려도 허브에 되살아나지 않는다(허브의 표식이 그 사본보다 새것이다).
    clocks.hub += SECOND
    with clocks.at_hub():
        again = docsync.import_upload(hub, owner=A, payload={
            **stale, revstamp.HANDLES: [revstamp.CLOCK_SKEW]})
    assert again["applied"] == 0 and again["skipped"] == 1, again
    for st in (dev, dev2):
        clocks.hub += SECOND
        _run(st, remote, clocks)
    assert [_doc(st, did) for st in (hub, dev, dev2)] == [None] * 3


@pytest.mark.parametrize("behind", [0, 26 * HOUR], ids=["in-step", "26h-slow"])
def test_a_document_made_again_after_its_deletion_reaches_every_store(world, behind):
    """허브의 문서 표식이 더 늦은 재생성을 막지 않는다. 느린 기기도 받은 삭제의 시각까지 시계가 따라가 그 뒤에 찍는다."""
    hub, dev, dev2, clocks, remote, _traffic = world
    did = ds.doc_id_for_origin(A, "marker-note")
    _put_doc(dev, did, "first text")
    _run(dev, remote, clocks)
    _run(dev2, remote, clocks, offset=-behind)
    clocks.hub += SECOND
    assert ds.delete_doc(dev, did, owner=A).get("executed")
    clocks.hub += SECOND
    _run(dev, remote, clocks)
    clocks.hub += SECOND
    _run(dev2, remote, clocks, offset=-behind)
    assert _doc(dev2, did) is None
    clocks.hub += SECOND
    clocks.offset = -behind
    _put_doc(dev2, did, "made again on the second device")
    clocks.offset = 0
    for st, off in ((dev2, -behind), (dev, 0), (dev2, -behind)):
        clocks.hub += SECOND
        _run(st, remote, clocks, offset=off)
    want = ds._sha("made again on the second device")
    assert [_doc(st, did) for st in (hub, dev, dev2)] == [want] * 3


def test_a_document_deletion_names_no_document_of_another_owner(world):
    """허브는 문서 표식을 이 소유자의 문서를 지웠을 때만 남긴다. 남의 문서 id 에 표식을 남기면 그 소유자의 다음 업로드가
    막힌다(`_DocTombstone` 의 키는 `doc_id` 하나다)."""
    hub, _dev, _dev2, clocks, _remote, _traffic = world
    did = "shared-looking-id"
    with clocks.at_hub():
        assert ds.put_doc(hub, owner=B, text="the other owner's note", doc_id=did,
                          title="Other").get("executed")
        out = docsync.import_upload(hub, owner=A, payload={
            "docs": [], "tombstones": [{"doc_id": did, "deleted_at": revstamp._fmt(clocks.hub)}],
            revstamp.HANDLES: [revstamp.CLOCK_SKEW]})
        assert out["deleted"] == 0, out
        assert _doc(hub, did) is not None and _doc_marker(hub, did) is None
        # 아무도 갖지 않은 id 의 삭제도 표식을 남기지 않는다(나중에 그 id 를 쓰는 소유자를 막지 않게).
        out = docsync.import_upload(hub, owner=A, payload={
            "docs": [], "tombstones": [{"doc_id": "nobody-has-it", "deleted_at": revstamp._fmt(clocks.hub)}],
            revstamp.HANDLES: [revstamp.CLOCK_SKEW]})
        assert out["deleted"] == 0 and _doc_marker(hub, "nobody-has-it") is None, out


# ── 한 스토어의 시계는 소유자들이 함께 쓴다 ──────────────────────────────────
def test_another_owners_upload_moves_the_shared_hub_clock_only_up_to_the_skew_bound(world, monkeypatch):
    """허브 스토어 하나에 소유자 둘. 시계(`last_hlc`)는 하나고 순번(`seq:<owner>`)은 소유자마다다.

    A 의 빠른 기기가 허용 스큐 끝에 찍힌 편집을 올리면 허브 시계가 거기까지 가고, 그 뒤 B 의 허브 쪽 쓰기가 그 뒤에
    찍힌다. 그보다 먼 편집은 받지 않아 시계를 밀지 못한다. B 의 스탬프는 한 시계에서 나오므로 B 의 순서는 그대로고,
    B 의 기기가 그 허브 쓰기를 받은 뒤의 편집은 그 뒤에 찍혀 허브에서 이긴다. 스탬프를 비교해 쓰는 곳(병합·삭제
    가드·문서 LWW)은 그 소유자의 행에만 쓴다."""
    hub, dev, _dev2, clocks, _remote, _traffic = world
    remote_b = sync.RemoteHTTP("vinc_sk_test_b", "http://hub.test")

    def post_b(base, path, token, payload, *, timeout):
        body = json.loads(json.dumps(payload))
        with clocks.at_hub():
            if path == "/sync/push":
                got = sync.apply_upload(owner=B, bundle=body["bundle"], store=hub)
            elif path == "/sync/pull":
                got = revstamp.with_clock(sync.export_delta(
                    owner=B, since=int(body["since"]), domains=["marker"], store=hub))
            elif path == "/sync/docs-push":
                got = docsync.import_upload(hub, owner=B, payload=body["payload"])
            else:
                got = revstamp.with_clock(docsync.export_docs(hub, owner=B, since=int(body["since"])))
        return json.loads(json.dumps(got, default=str))

    monkeypatch.setattr(syncclient, "_post", post_b)
    with clocks.at_hub():
        _write(hub, "A v0", nid="concept:a")
        _write(hub, "B v0", nid="concept:b", owner=B)
        b_first = _row(hub, "concept:b", owner=B)["ua"]
    # B 의 기기가 먼저 그 노드를 받아 둔다.
    clocks.hub += SECOND
    assert sync.run(dev, None, owner=B, remote=remote_b)["executed"]
    assert _row(dev, "concept:b", owner=B)["title"] == "B v0"
    with clocks.at_hub():
        edge = revstamp._fmt(clocks.hub + revstamp.MAX_SKEW_MS)
        far = revstamp._fmt(clocks.hub + revstamp.MAX_SKEW_MS + MINUTE)
        out = sync.apply_upload(owner=A, store=hub, bundle={
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A at the edge", "domain": "marker",
                       "tags": [], "props": {}, "_rev": 5, "_updated_at": edge},
                      {"id": "concept:a2", "type": "concept", "title": "A too far", "domain": "marker",
                       "tags": [], "props": {}, "_rev": 1, "_updated_at": far}],
            "edges": [], revstamp.HANDLES: [revstamp.CLOCK_SKEW]})
        assert out[SKEW] == ["concept:a2"], out
        # 받은 편집의 스탬프까지 갔다(병합 쓰기가 그 뒤 몇 밀리초를 더 쓴다). 먼 편집은 시계를 밀지 않았다.
        assert 0 <= int(revstamp.meta_get(hub, "last_hlc", "0")) - revstamp._parse_ms(edge) < 10
        clocks.hub += SECOND
        _write(hub, "B on the hub", nid="concept:b", owner=B)
        b_hub = _row(hub, "concept:b", owner=B)["ua"]
    assert b_first < edge < b_hub <= revstamp._fmt(revstamp._parse_ms(edge) + 10)
    # B 의 기기(시계가 맞다)가 그 허브 쓰기를 받은 뒤에 고친 것이 허브에서 이긴다.
    clocks.hub += SECOND
    assert sync.run(dev, None, owner=B, remote=remote_b)["executed"]
    assert _row(dev, "concept:b", owner=B)["title"] == "B on the hub"
    clocks.hub += SECOND
    _write(dev, "B on the device", nid="concept:b", owner=B)
    assert _row(dev, "concept:b", owner=B)["ua"] > b_hub
    clocks.hub += SECOND
    out = sync.run(dev, None, owner=B, remote=remote_b)
    assert SKEW not in out["push"], out
    assert _row(hub, "concept:b", owner=B)["title"] == "B on the device"
