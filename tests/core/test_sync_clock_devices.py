"""시계가 틀린 기기와 허브가 오케스트레이터(`sync.run`)로 여러 번 동기화해도 같은 사본에 닿는다.

기기는 실제 동기화 클라이언트(`vinc_mcp/syncclient`)로 보내고, 허브의 네 끝점은 두 번째 스토어 위에서
`vinc_mcp/http_server._sync` 가 사이드카로 넘기는 것과 같은 함수로 답한다(본문은 JSON 으로 오가고, pull 답에는
사이드카 라우트처럼 허브 벽시계 `clock` 이 실린다). 허브는
기기를 믿지 않고(`apply_upload`), 기기는 자기 허브를 믿는다(`pull_apply`). 두 스토어의 벽시계는 따로
고정한다: 허브 시각과 기기의 어긋남.

- 느린 기기(2시간): 허브 편집을 본 뒤의 편집이 허브에서 이긴다.
- 빠른 기기(6분·1시간): 허브는 그 편집을 받지 않고 `clock_skew` 로 알린다. 기기는 base 를 옮기지 않고 다시
  보낸다. 허브 시계가 따라잡은 뒤의 동기화에서 들어가고, 그 뒤 허브의 편집이 기기에 온다.
- 상한 안(2분·4분 59초·정확히 5분)은 첫 동기화에 들어간다.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os

import pytest

from api import docstore as ds
from api import docsync, revstamp, sync
from api import fragment as frag
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import import_driver
from vinc_mcp import syncclient

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

A = "user:clock-dev"
T0 = 1_800_000_000_000
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
SKEW = "clock_skew"


class _Clocks:
    """허브의 벽시계와 기기의 어긋남. 허브 끝점 안에서는 허브 시각, 밖에서는 기기 시각이다.
    `declares` 가 False 면 업로드에서 `clock_skew` 를 안다는 표시를 뺀다(데스크톱 0.9.x 가 보내는 모양).
    `reply_clock` 이 있으면 pull 답의 `clock` 을 그 값으로 바꾼다(틀린 시계를 보내는 허브)."""

    def __init__(self):
        self.hub = T0
        self.offset = 0
        self.side = "device"
        self.declares = True
        self.reply_clock = None

    def __call__(self) -> int:
        return self.hub + (0 if self.side == "hub" else self.offset)

    @contextlib.contextmanager
    def at_hub(self):
        before, self.side = self.side, "hub"
        try:
            yield
        finally:
            self.side = before


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clocks = _Clocks()
    monkeypatch.setattr(revstamp, "_wall_ms", clocks)
    opened = []

    def make(name):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        opened.append(st)
        return st

    hub, dev = make("hub"), make("device")

    def post(base, path, token, payload, *, timeout):
        body = json.loads(json.dumps(payload))
        if not clocks.declares:
            for part in ("bundle", "payload"):
                if part in body:
                    body[part].pop(revstamp.HANDLES, None)
        with clocks.at_hub():
            if path == "/sync/push":
                out = sync.apply_upload(owner=A, bundle=body["bundle"], store=hub)
            elif path == "/sync/pull":
                out = revstamp.with_clock(sync.export_delta(
                    owner=A, since=int(body["since"]), domains=["clock"], store=hub))
            elif path == "/sync/docs-push":
                out = docsync.import_upload(hub, owner=A, payload=body["payload"])
            elif path == "/sync/docs-pull":
                out = revstamp.with_clock(docsync.export_docs(hub, owner=A, since=int(body["since"])))
            else:
                raise AssertionError(path)
        if clocks.reply_clock is not None and path in ("/sync/pull", "/sync/docs-pull"):
            out = {**out, "clock": clocks.reply_clock}
        return json.loads(json.dumps(out, default=str))

    monkeypatch.setattr(syncclient, "_post", post)
    sync.set_selected_domains(dev, ["clock"])
    try:
        yield hub, dev, clocks, sync.RemoteHTTP("vinc_sk_test", "http://hub.test")
    finally:
        for st in opened:
            st.close()


def _write(store, title, nid="concept:n"):
    res = frag.merge_fragment({"owner": A, "nodes": [
        {"id": nid, "type": "concept", "title": title, "domain": "clock"}], "edges": []}, store=store)
    assert res["executed"], res


def _hub_write(hub, clocks, title, nid="concept:n"):
    with clocks.at_hub():
        _write(hub, title, nid)


def _row(store, nid="concept:n"):
    rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
                     "RETURN c.title AS title, c.updated_at AS ua",
                     {"id": pkns.key(A, nid), "o": A})
    return rows[0] if rows else None


def _run(dev, remote):
    out = sync.run(dev, None, owner=A, remote=remote)
    assert out["executed"], out
    return out


def _ms(stamp: str) -> int:
    head, _, frac = stamp.rstrip("Z").partition(".")
    dt = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000) + int(frac or 0)


def _skew_warnings(push: dict) -> list[str]:
    return [w for w in push.get("warnings") or [] if "clock" in w]


# ── B1: 느린 기기 ─────────────────────────────────────────────────────────────
def test_a_device_two_hours_slow_wins_on_the_hub_with_an_edit_made_after_it_pulled(world):
    hub, dev, clocks, remote = world
    clocks.offset = -2 * HOUR
    _hub_write(hub, clocks, "v0")
    _run(dev, remote)
    clocks.hub += SECOND
    _hub_write(hub, clocks, "v1-hub")
    clocks.hub += SECOND
    pulled = _run(dev, remote)
    assert _row(dev)["title"] == "v1-hub", pulled
    # 기기 시계는 허브 스탬프까지 따라갔다(자르지 않았다). 그래서 다음 편집이 그 뒤에 찍힌다.
    assert int(revstamp.meta_get(dev, "last_hlc", "0")) >= _ms(_row(hub)["ua"])
    clocks.hub += SECOND
    _write(dev, "v2-device")
    assert _row(dev)["ua"] > _row(hub)["ua"]
    clocks.hub += SECOND
    out = _run(dev, remote)
    assert _row(hub)["title"] == "v2-device", out
    assert SKEW not in out["push"], out


# ── B2: 빠른 기기 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ahead", [6 * MINUTE, HOUR], ids=["6m", "1h"])
def test_a_fast_device_edit_is_refused_then_stored_once_the_hub_clock_catches_up(world, ahead):
    hub, dev, clocks, remote = world
    clocks.offset = ahead
    _hub_write(hub, clocks, "v0")
    _run(dev, remote)
    hub_v0 = _row(hub)
    clocks.hub += SECOND
    _write(dev, "dev-edit")
    sent = _row(dev)["ua"]
    clocks.hub += SECOND
    first = _run(dev, remote)
    # 허브는 저장하지 않았고, 기기는 base 를 옮기지 않았다(다음 동기화에 다시 보낸다).
    assert _row(hub) == hub_v0, first
    assert sync.get_base(dev, "concept:n", A)["fields"]["title"] == "v0"
    assert first["push"].get(SKEW) == ["concept:n"], first
    assert _skew_warnings(first["push"]) == [revstamp.clock_skew_warning(1)], first
    # 허브 시계 + 허용 스큐가 그 스탬프를 지난 뒤의 동기화에서 들어간다.
    clocks.hub += ahead - 5 * MINUTE + SECOND
    second = _run(dev, remote)
    assert SKEW not in second["push"], second
    assert _row(hub)["title"] == "dev-edit", second
    # 그 뒤의 허브 편집은 기기 편집의 스탬프 뒤에 찍히고 기기에 온다. 두 쪽이 같은 사본을 가진다.
    # (받는 쪽이 병합한 쓰기는 자기 HLC 로 찍는다. 스탬프 문자열이 아니라 내용과 순서를 본다.)
    clocks.hub += 30 * SECOND
    _hub_write(hub, clocks, "hub-edit")
    assert _row(hub)["ua"] > sent
    clocks.hub += SECOND
    third = _run(dev, remote)
    assert _row(dev)["title"] == "hub-edit" == _row(hub)["title"], third


@pytest.mark.parametrize("ahead", [2 * MINUTE, 4 * MINUTE + 59 * SECOND, 5 * MINUTE],
                         ids=["2m", "4m59s", "exactly-the-ceiling"])
def test_a_device_ahead_by_no_more_than_the_bound_syncs_at_once(world, ahead):
    hub, dev, clocks, remote = world
    clocks.offset = ahead
    _hub_write(hub, clocks, "v0")
    _run(dev, remote)
    _write(dev, "dev-edit")
    # 기기 시계는 pull 안의 쓰기로 몇 밀리초 앞서 있다. 허브 시각을 그만큼 옮겨 어긋남을 정확히 `ahead` 로 둔다.
    clocks.hub = _ms(_row(dev)["ua"]) - ahead
    out = _run(dev, remote)
    assert SKEW not in out["push"], out
    assert _row(hub)["title"] == "dev-edit"
    clocks.hub += 30 * SECOND
    _hub_write(hub, clocks, "hub-edit")
    _run(dev, remote)
    assert _row(dev)["title"] == "hub-edit" == _row(hub)["title"]


# ── 문서도 같은 길을 간다 ─────────────────────────────────────────────────────
def _doc_ua(store, did):
    rows = store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.updated_at AS ua", {"id": did})
    return rows[0]["ua"] if rows else None


def test_a_fast_device_document_is_refused_then_stored_once_the_hub_clock_catches_up(world):
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    _run(dev, remote)
    did = ds.doc_id_for_origin(A, "clock-note")
    wrote = ds.put_doc(dev, owner=A, text="A note written on a fast device.", doc_id=did,
                       title="Note", origin="clock-note")
    assert wrote.get("executed"), wrote
    first = _run(dev, remote)
    assert _doc_ua(hub, did) is None, first
    assert first["docs_push"].get(SKEW) == [did], first
    assert _skew_warnings(first["docs_push"]) == [revstamp.clock_skew_warning(1)], first
    clocks.hub += MINUTE + SECOND
    second = _run(dev, remote)
    assert SKEW not in second["docs_push"], second
    assert _doc_ua(hub, did) == _doc_ua(dev, did)


# ── 갱신하지 않은 앱이 읽는 것 ────────────────────────────────────────────────
def test_an_upload_whose_items_are_all_refused_still_answers_executed(world):
    """옛 클라이언트(`syncclient.push`)는 `executed` 가 없으면 동기화를 멈춘다. 모두 거절돼도 답은 실행됨이다."""
    hub, dev, clocks, remote = world
    with clocks.at_hub():
        out = sync.apply_delta(owner=A, store=hub, bundle={"nodes": [
            {"id": "concept:x", "type": "concept", "title": "X", "domain": "clock", "tags": [],
             "props": {}, "_rev": 1, "_updated_at": revstamp._fmt(T0 + HOUR)}], "edges": []})
    assert out["executed"] is True and out["applied"] == 0 and out["skipped_stale"] == 0, out
    assert out[SKEW] == ["concept:x"], out


def test_a_mode_switch_upload_counts_refused_items_as_missing(world):
    """로컬 모드에서 계정으로 옮길 때 받지 않은 항목은 `missing` 에 든다. 앱은 그 수가 0 이 아니면 모드를 바꾸지 않는다."""
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    _write(dev, "on a fast device")
    did = ds.doc_id_for_origin(A, "move-note")
    assert ds.put_doc(dev, owner=A, text="moved", doc_id=did, title="Moved",
                      origin="move-note").get("executed")
    rep = sync.push_everything(dev, None, owners=[A], remote=remote)
    assert rep["missing"] == 2, rep
    assert rep[SKEW] == ["concept:n", did], rep
    assert _row(hub) is None and _doc_ua(hub, did) is None


# ── 삭제도 다시 보낸다 ────────────────────────────────────────────────────────
def _edges(store):
    rows = store.run("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.concept_id = $s "
                     "AND b.concept_id = $d RETURN count(r) AS c",
                     {"s": pkns.key(A, "concept:s"), "d": pkns.key(A, "concept:d")})
    return int(rows[0]["c"]) if rows else 0


def test_a_fast_device_deletion_is_refused_then_applied_once_the_hub_clock_catches_up(world):
    from api import query as q
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    with clocks.at_hub():
        res = frag.merge_fragment({"owner": A, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": "clock"}
            for nid in ("concept:n", "concept:s", "concept:d")],
            "edges": [{"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=hub)
        assert res["executed"], res
    _run(dev, remote)
    assert _edges(dev) == 1 and _row(dev) is not None
    clocks.hub += SECOND
    assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    clocks.hub += SECOND
    first = _run(dev, remote)
    assert _row(hub) is not None and _edges(hub) == 1, first
    assert set(first["push"].get(SKEW) or []) >= {
        "concept:n", sync.edge_ref("concept:s", "RELATED_TO", "concept:d")}, first
    clocks.hub += MINUTE + SECOND
    second = _run(dev, remote)
    assert SKEW not in second["push"], second
    assert _row(hub) is None and _edges(hub) == 0, second


# ── 하루 넘게 느린 기기: 허브를 따라간 시계는 되돌리지 않는다 ─────────────────────
DAY = 24 * HOUR
KINDS = ["edit", "delete", "edge_add", "edge_delete_with_node_edit", "doc_edit", "doc_delete"]


def _doc_hash(store, did):
    rows = store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.content_hash AS h", {"id": did})
    return rows[0]["h"] if rows else None


def _put_doc(store, did, text):
    assert ds.put_doc(store, owner=A, text=text, doc_id=did, title="Note",
                      origin="slow-note").get("executed")


def _both(hub, dev, did):
    """두 쪽이 같은 사본을 가졌는지 보는 값: 노드 제목·엣지 수·문서 내용."""
    return [(_row(st)["title"] if _row(st) else None, _edges(st), _doc_hash(st, did))
            for st in (hub, dev)]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("behind", [26 * HOUR, 3 * DAY, 400 * DAY], ids=["26h", "3d", "400d"])
def test_a_device_more_than_a_day_slow_keeps_every_change_it_makes_after_following_its_hub(
        world, behind, kind):
    from api import query as q
    hub, dev, clocks, remote = world
    clocks.offset = -behind
    did = ds.doc_id_for_origin(A, "slow-note")
    with clocks.at_hub():
        res = frag.merge_fragment({"owner": A, "nodes": [
            {"id": nid, "type": "concept", "title": "v0" if nid == "concept:n" else nid,
             "domain": "clock"} for nid in ("concept:n", "concept:s", "concept:d")],
            "edges": ([{"from": "concept:s", "to": "concept:d", "type": "relates_to"}]
                      if kind == "edge_delete_with_node_edit" else [])}, store=hub)
        assert res["executed"], res
        _put_doc(hub, did, "doc v0")
    _run(dev, remote)
    clocks.hub += SECOND
    with clocks.at_hub():
        _write(hub, "v1-hub")
        _put_doc(hub, did, "doc v1-hub")
    clocks.hub += SECOND
    _run(dev, remote)
    assert _row(dev)["title"] == "v1-hub" and _doc_hash(dev, did) == _doc_hash(hub, did)
    clocks.hub += SECOND
    if kind == "edit":
        _write(dev, "v2-device")
    elif kind == "delete":
        assert q.delete_node("concept:n", owner=A, store=dev).get("executed")
    elif kind == "edge_add":
        assert frag.merge_fragment({"owner": A, "nodes": [], "edges": [
            {"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=dev)["executed"]
    elif kind == "edge_delete_with_node_edit":
        assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
        _write(dev, "v2-device")
    elif kind == "doc_edit":
        _put_doc(dev, did, "doc v2-device")
    elif kind == "doc_delete":
        assert ds.delete_doc(dev, did, owner=A).get("executed")
    want = _both(hub, dev, did)[1]
    clocks.hub += SECOND
    out = _run(dev, remote)
    # 첫 동기화에 허브가 기기의 변경을 그대로 가진다. 거절도 경고도 없다.
    assert _both(hub, dev, did)[0] == want, out
    assert SKEW not in out["push"] and SKEW not in out["docs_push"], out
    # 그 뒤의 허브 편집도 기기에 온다. 두 쪽이 같은 사본에 닿는다.
    clocks.hub += 30 * SECOND
    with clocks.at_hub():
        if kind in ("edit", "edge_delete_with_node_edit"):
            _write(hub, "v3-hub")
        if kind == "doc_edit":
            _put_doc(hub, did, "doc v3-hub")
    for _ in range(2):
        clocks.hub += SECOND
        _run(dev, remote)
    hub_side, dev_side = _both(hub, dev, did)
    assert hub_side == dev_side
    if kind in ("edit", "edge_delete_with_node_edit"):
        assert hub_side[0] == "v3-hub"


@pytest.mark.parametrize("back", [25 * HOUR, 3 * DAY], ids=["25h", "3d"])
def test_local_stamp_order_holds_when_the_wall_clock_of_a_device_that_followed_its_hub_steps_back(
        world, back):
    hub, dev, clocks, remote = world
    _hub_write(hub, clocks, "v0")
    clocks.hub += SECOND
    _run(dev, remote)
    _write(dev, "before the step", nid="concept:a")
    before = _row(dev, "concept:a")["ua"]
    clocks.offset = -back
    _write(dev, "after the step", nid="concept:b")
    assert _row(dev, "concept:b")["ua"] > before
    _write(dev, "edited after the step", nid="concept:a")
    assert _row(dev, "concept:a")["ua"] > before


# ── 엣지 삭제만 한 동기화도 올라간다 ────────────────────────────────────────────
def _hub_with_an_edge(hub, clocks):
    with clocks.at_hub():
        res = frag.merge_fragment({"owner": A, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": "clock"}
            for nid in ("concept:s", "concept:d")],
            "edges": [{"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=hub)
        assert res["executed"], res


def test_an_edge_deletion_made_on_its_own_reaches_the_hub(world):
    from api import query as q
    hub, dev, clocks, remote = world
    _hub_with_an_edge(hub, clocks)
    _run(dev, remote)
    assert _edges(dev) == 1
    clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    clocks.hub += SECOND
    out = _run(dev, remote)
    assert _edges(hub) == 0, out
    assert out["push"]["edge_deletes"] == 1 and SKEW not in out["push"], out
    clocks.hub += SECOND
    again = _run(dev, remote)
    assert _edges(hub) == 0 == _edges(dev), again


def test_a_refused_edge_deletion_made_on_its_own_is_sent_again(world):
    from api import query as q
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    _hub_with_an_edge(hub, clocks)
    _run(dev, remote)
    clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=dev).get("executed")
    clocks.hub += SECOND
    first = _run(dev, remote)
    assert _edges(hub) == 1, first
    assert first["push"].get(SKEW) == [sync.edge_ref("concept:s", "RELATED_TO", "concept:d")], first
    clocks.hub += MINUTE + SECOND
    second = _run(dev, remote)
    assert SKEW not in second["push"], second
    assert _edges(hub) == 0, second


# ── `clock_skew` 를 모르는 클라이언트: 청크째 실패해 다시 보낸다 ─────────────────
def test_a_client_that_does_not_declare_clock_skew_gets_a_sync_error_and_sends_again(world):
    """`clock_skew` 를 모르는 클라이언트는 그 답을 읽지 않는다. 허브가 청크를 실패시키면 동기화 오류를 보이고 base·커서를 그대로 둔다."""
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    clocks.declares = False
    _hub_write(hub, clocks, "v0")
    _hub_write(hub, clocks, "other", nid="concept:o")
    _run(dev, remote)
    hub_v0 = _row(hub)
    clocks.hub += SECOND
    _write(dev, "dev-edit")
    _write(dev, "other-dev", nid="concept:o")
    cursor = sync.get_cursor(dev, "push")
    clocks.hub += SECOND
    with pytest.raises(syncclient.SyncError, match="minutes ahead"):
        sync.run(dev, None, owner=A, remote=remote)
    # 청크의 어느 것도 적용되지 않았고, 기기는 합의된 것으로 적지 않았다.
    assert _row(hub) == hub_v0 and _row(hub, "concept:o")["title"] == "other"
    assert sync.get_base(dev, "concept:n", A)["fields"]["title"] == "v0"
    assert sync.get_cursor(dev, "push") == cursor
    # 허브 시계가 따라잡은 뒤의 동기화에서 둘 다 들어간다.
    clocks.hub += MINUTE + SECOND
    out = _run(dev, remote)
    assert _row(hub)["title"] == "dev-edit" and _row(hub, "concept:o")["title"] == "other-dev", out
    # 그 뒤의 허브 편집은 기기에 온다. 기기가 받은 편집을 병합해 쓴 행은 기기 시계로 찍혀, 허브 시계가 따라잡을
    # 때까지 그 동기화는 오류로 끝난다(아무것도 잃지 않는다). 그 뒤에는 두 쪽이 같다.
    clocks.hub += 30 * SECOND
    _hub_write(hub, clocks, "hub-edit")
    clocks.hub += SECOND
    with pytest.raises(syncclient.SyncError, match="minutes ahead"):
        sync.run(dev, None, owner=A, remote=remote)
    assert _row(dev)["title"] == "hub-edit"
    clocks.hub += MINUTE + SECOND
    _run(dev, remote)
    assert _row(dev)["title"] == "hub-edit" == _row(hub)["title"]


def test_a_client_that_does_not_declare_clock_skew_keeps_its_document_cursor(world):
    hub, dev, clocks, remote = world
    clocks.offset = 6 * MINUTE
    clocks.declares = False
    _run(dev, remote)
    did = ds.doc_id_for_origin(A, "old-app-note")
    assert ds.put_doc(dev, owner=A, text="written on an old app", doc_id=did, title="Note",
                      origin="old-app-note").get("executed")
    cursor = sync.get_cursor(dev, "docs-push")
    first = _run(dev, remote)
    assert first["docs_push"]["executed"] is False and "minutes ahead" in first["docs_push"]["error"]
    assert _doc_ua(hub, did) is None and sync.get_cursor(dev, "docs-push") == cursor
    clocks.hub += MINUTE + SECOND
    second = _run(dev, remote)
    assert second["docs_push"]["executed"] is True, second
    assert _doc_ua(hub, did) == _doc_ua(dev, did)



# ── 먼 시계나 먼 항목을 보내는 허브: 기기는 붙잡히지 않는다 ─────────────────────────
Y2999 = "2999-01-01T00:00:00.000Z"


@pytest.mark.parametrize("reply_clock, item", [
    (Y2999, Y2999),
    ("9999-12-31T23:59:59.999Z", "9999-12-31T23:59:59.900Z"),
    (None, Y2999),
], ids=["clock-and-item-2999", "clock-and-item-9999", "honest-clock-and-a-2999-item"])
def test_a_far_hub_reply_does_not_hold_the_device(world, reply_clock, item):
    """허브가 기기가 가진 노드를 먼 시각에 고치고, pull 답의 `clock` 도 그 시각이다(`None` 이면 맞는 시계).
    기기의 다음 쓰기 150 번은 모두 되고 기기 벽시계 + `MAX_SKEW_MS` 안에 찍힌다. 허브가 계속 같은 답을 보내도
    다음 동기화에서 모두 허브에 닿는다."""
    hub, dev, clocks, remote = world
    _hub_write(hub, clocks, "v0")
    clocks.hub += SECOND
    _run(dev, remote)
    honest = int(revstamp.meta_get(dev, revstamp.TRUSTED_CLOCK, "0"))
    assert honest == clocks.hub
    now = clocks.hub
    clocks.hub = _ms(item)
    _hub_write(hub, clocks, "far")
    clocks.hub = now + SECOND
    assert _row(hub)["ua"] >= item
    clocks.reply_clock = reply_clock
    _run(dev, remote)
    assert _row(dev)["title"] == "far"
    clocks.hub += SECOND
    top = revstamp._fmt(clocks() + revstamp.MAX_SKEW_MS)
    late = []
    for i in range(150):
        _write(dev, f"w{i}", nid=f"concept:w{i}")
        if _row(dev, f"concept:w{i}")["ua"] > top:
            late.append(i)
    assert late == []
    clocks.hub += SECOND
    out = _run(dev, remote)
    assert SKEW not in out["push"], out
    assert [i for i in range(150) if (_row(hub, f"concept:w{i}") or {}).get("title") != f"w{i}"] == []
    assert int(revstamp.meta_get(dev, revstamp.TRUSTED_CLOCK, "0")) <= clocks.hub


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("behind", [DAY, 365 * DAY, (3 * 365 + 1) * DAY], ids=["1d", "1y", "3y"])
def test_a_device_a_day_a_year_or_three_years_slow_still_follows_its_hub(world, behind, kind):
    """허브 시계를 적는 폭(`revstamp.TRUSTED_AHEAD_MS`) 안이면 위의 느린 기기 시나리오가 그대로 선다."""
    test_a_device_more_than_a_day_slow_keeps_every_change_it_makes_after_following_its_hub(
        world, behind, kind)
