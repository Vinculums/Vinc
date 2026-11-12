"""시계가 틀린 기기와 허브가 오케스트레이터(`sync.run`)로 여러 번 동기화해도 같은 사본에 닿는다.

기기는 실제 동기화 클라이언트(`vinc_mcp/syncclient`)로 보내고, 허브의 네 끝점은 두 번째 스토어 위에서
`vinc_mcp/http_server._sync` 가 사이드카로 넘기는 것과 같은 함수로 답한다(본문은 JSON 으로 오간다). 허브는
기기를 믿지 않고(`apply_delta` 기본값), 기기는 자기 허브를 믿는다(`pull_apply`). 두 스토어의 벽시계는 따로
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
from store.embedded_backend import _import_driver
from vinc_mcp import syncclient

pytestmark = pytest.mark.skipif(_import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

A = "user:clock-dev"
T0 = 1_800_000_000_000
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
SKEW = "clock_skew"


class _Clocks:
    """허브의 벽시계와 기기의 어긋남. 허브 끝점 안에서는 허브 시각, 밖에서는 기기 시각이다."""

    def __init__(self):
        self.hub = T0
        self.offset = 0
        self.side = "device"

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
        with clocks.at_hub():
            if path == "/sync/push":
                out = sync.apply_delta(owner=A, bundle=body["bundle"], store=hub)
            elif path == "/sync/pull":
                out = sync.export_delta(owner=A, since=int(body["since"]), domains=["clock"],
                                        store=hub)
            elif path == "/sync/docs-push":
                out = docsync.import_docs(hub, owner=A, payload=body["payload"])
            elif path == "/sync/docs-pull":
                out = docsync.export_docs(hub, owner=A, since=int(body["since"]))
            else:
                raise AssertionError(path)
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
    assert int(revstamp._meta_get(dev, "last_hlc", "0")) >= _ms(_row(hub)["ua"])
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
