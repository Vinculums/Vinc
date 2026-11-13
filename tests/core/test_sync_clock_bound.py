"""믿지 않는 보낸 쪽(허브가 받는 기기 업로드)의 스탬프가 받는 쪽 벽시계 + `MAX_SKEW_MS` 보다 뒤면 그 항목을 받지 않는다.

허브 한 스토어가 여러 소유자를 들고 시계는 그 스토어에 하나다. 한 소유자의 묶음이 시계를 미래로 밀면 모든
소유자의 다음 쓰기가 그 미래로 찍히거나(2999) 아예 찍히지 못한다(9999 다음 해는 날짜가 아니다). 그래서 허브는
상한을 넘은 노드·엣지 삭제·노드 삭제·문서·문서 삭제를 저장하지 않고 결과 `clock_skew` 에 이름을 남긴다. 자르지
않는다: 자르면 한 편집이 두 스탬프를 가진 두 사본이 된다. 기기가 자기 허브에서 받는 pull 은 믿는 길이라 스탬프를
그대로 받는다. 모든 테스트는 벽시계(`revstamp._wall_ms`)를 고정한다. 두 키 모드에서 같은 단정이 선다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from api import docstore as ds
from api import docsync, revstamp, runs, sync
from api import fragment as frag
from store import pkns, run_state
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import _import_driver
from vinc_mcp import tools
from vinc_mcp.bridge import Bridge

pytestmark = pytest.mark.skipif(_import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

ROOT = Path(__file__).resolve().parents[2]
A = "user:clock-a"
B = "user:clock-b"
ROLE = "concept:agent-dev-core"
PROFILE = next(p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"] if p["id"] == ROLE)
PROPS = {**PROFILE, "mission": "m", "tier": "execute", "rung": "strong", "never": ["merge"]}

#: 고정한 벽시계. 실제 날짜와 무관하게 모든 비교가 이 값을 기준으로 선다.
T0 = 1_800_000_000_000
MINUTE = 60 * 1000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
Y9999 = "9999-12-31T23:59:59.999Z"
Y2999 = "2999-01-01T00:00:00.000Z"
Y9999_MS = 253402300799999
Y2999_MS = 32472144000000
SKEW = "clock_skew"


class _Wall:
    def __init__(self, ms: int):
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


@pytest.fixture(autouse=True)
def wall(monkeypatch):
    w = _Wall(T0)
    monkeypatch.setattr(revstamp, "_wall_ms", w)
    return w


@pytest.fixture()
def store(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    opened = []

    def make(name: str = "db"):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        opened.append(st)
        return st

    try:
        yield make
    finally:
        for st in opened:
            st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _StoreBridge(Bridge):
    """`vinc_run_start` 가 부르는 사이드카 경로 중 실행 시작에 필요한 셋만 이 스토어로 잇는다."""

    def __init__(self, store, owner):
        super().__init__(base="http://127.0.0.1:1", token="t", owner=owner)
        self.store = store

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Core dev",
                             "domain": "vinc/agent/roles", "props": PROPS}}
        if path == "/runs":
            return runs.list_runs(self.store, owner=params["owner"], run=params.get("run"),
                                  cited=params.get("cited"), role=params.get("role"),
                                  limit=params.get("limit") or 50)
        if path == "/ingest/fragment":
            return frag.merge_fragment(dict(body), store=self.store)
        return {"executed": False, "error": "unexpected path " + path}


def _at(ms: int) -> str:
    return revstamp._fmt(ms)


def _top() -> str:
    return revstamp._fmt(revstamp.ceiling_ms())


def _write(store, owner, nid, title="Plain"):
    res = frag.merge_fragment({"owner": owner, "nodes": [
        {"id": nid, "type": "concept", "title": title, "domain": "clock"}], "edges": []}, store=store)
    assert res["executed"], res
    return res


def _pushed(nid, ua, title="Pushed", rev=1):
    return {"id": nid, "type": "concept", "title": title, "domain": "clock", "tags": [],
            "props": {}, "_rev": rev, "_updated_at": ua}


def _push(store, owner, *nodes, edges=(), tombstones=(), edge_tombstones=(), trusted=None):
    kw = {} if trusted is None else {"trusted_sender": trusted}
    return sync.apply_delta(owner=owner, store=store, bundle={
        "nodes": list(nodes), "edges": list(edges), "tombstones": list(tombstones),
        "edge_tombstones": list(edge_tombstones)}, **kw)


def _row(store, owner, nid):
    rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
                     "RETURN c.updated_at AS ua, c.title AS title",
                     {"id": pkns.key(owner, nid), "o": owner})
    return rows[0] if rows else None


def _ua(store, owner, nid):
    row = _row(store, owner, nid)
    return row["ua"] if row else None


def _last(store) -> int:
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key = 'last_hlc' RETURN m.value AS v")
    return int(rows[0]["v"])


def _poison(store, ms: int) -> None:
    store.run("MERGE (m:_SyncMeta {key: 'last_hlc'}) SET m.value = $v", {"v": str(ms)})


def _edge_tombstones(store):
    return store.run("MATCH (t:_EdgeTombstone) RETURN t.deleted_at AS d")


def _doc(store, did):
    rows = store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.updated_at AS ua, "
                     "d.title AS title", {"id": did})
    return rows[0] if rows else None


def _docs_payload(*docs, tombstones=()):
    return {"docs": [{"doc_id": did, "title": text, "origin": "", "content_hash": ds._sha(text),
                      "updated_at": ua, "text": text} for did, ua, text in docs],
            "tombstones": [{"doc_id": did, "deleted_at": da} for did, da in tombstones]}


# ── 1. 원래 결함: 먼 스탬프가 스토어 시계를 끌고 가지 못한다 ────────────────────
@pytest.mark.parametrize("far", [Y2999, Y9999], ids=["y2999", "y9999"])
def test_a_far_stamp_in_an_upload_is_refused_and_another_owner_writes_at_the_wall_clock(store, far):
    st = store()
    _write(st, A, "concept:target")
    _write(st, B, "concept:mine")
    out = _push(st, A, _pushed("concept:target", far, title="Far"),
                _pushed("concept:fresh", far, title="Far new"),
                _pushed("concept:plain", _at(T0), title="Plain new"))
    assert out["executed"], out
    # 먼 노드는 저장되지 않는다(자르지도 않는다). 나머지는 적용된다.
    assert _row(st, A, "concept:target")["title"] == "Plain"
    assert _row(st, A, "concept:fresh") is None
    assert _row(st, A, "concept:plain")["title"] == "Plain new"
    assert out["applied"] == 1, out
    assert out.get(SKEW) == ["concept:target", "concept:fresh"], out
    assert "clamped_stamps" not in out
    assert [w for w in out["warnings"] if "clock" in w] == [revstamp.clock_skew_warning(2)], out
    assert _last(st) <= revstamp.ceiling_ms()
    # 다른 소유자의 다음 쓰기는 벽시계에 찍힌다(같은 밀리초 안의 단조 전진만 더해진다).
    _write(st, B, "concept:mine", title="B edits")
    assert _at(T0) <= _ua(st, B, "concept:mine") < _at(T0 + 1000)


def test_a_far_stamp_in_an_upload_does_not_stop_a_run_start(store):
    st = store()
    _write(st, A, "concept:target")
    _push(st, A, _pushed("concept:target", Y9999, title="Plain"))
    opened = tools.call("vinc_run_start", {"role": ROLE, "task": "Review it.", "run_id": "clock-1"},
                        _StoreBridge(st, A))
    assert opened.get("executed") and opened.get("state") == "working", opened


# ── 2. 받지 않는 항목: 노드 삭제·엣지 삭제·형식이 깨진 스탬프 ───────────────────
def test_a_far_node_tombstone_is_refused_and_the_node_stays(store):
    st = store()
    _write(st, A, "concept:kept", title="Kept")
    out = _push(st, A, tombstones=[{"node_id": "concept:kept", "label": "Concept",
                                    "deleted_at": Y9999, "rev": 2}])
    assert out["deleted"] == 0, out
    assert _row(st, A, "concept:kept")["title"] == "Kept"
    assert sync.get_tombstone(st, "concept:kept", A) is None
    assert out.get(SKEW) == ["concept:kept"], out
    assert _last(st) <= revstamp.ceiling_ms()


def test_a_far_edge_tombstone_is_refused_and_the_edge_can_be_written_again(store, wall):
    st = store()
    _write(st, A, "concept:s")
    _write(st, A, "concept:d")
    out = _push(st, A, edge_tombstones=[{"src": "concept:s", "rel": "RELATED_TO",
                                         "dst": "concept:d", "deleted_at": Y9999}])
    assert _edge_tombstones(st) == [], _edge_tombstones(st)
    # 30일 뒤 그 소유자가 끝점을 고치고 엣지를 다시 보낸다.
    wall.ms = T0 + 30 * DAY
    again = _push(st, A, _pushed("concept:s", _at(wall.ms), title="S2", rev=3),
                  _pushed("concept:d", _at(wall.ms), title="D2", rev=3),
                  edges=[{"from": "concept:s", "to": "concept:d", "type": "relates_to"}])
    assert again["edges"] == 1, again
    assert out.get(SKEW) == [sync.edge_ref("concept:s", "RELATED_TO", "concept:d")], out


def test_an_edge_tombstone_within_the_bound_still_deletes(store):
    st = store()
    _write(st, A, "concept:s")
    _write(st, A, "concept:d")
    _push(st, A, _pushed("concept:s", _at(T0 + 1), rev=2), _pushed("concept:d", _at(T0 + 1), rev=2),
          edges=[{"from": "concept:s", "to": "concept:d", "type": "relates_to"}])
    out = _push(st, A, edge_tombstones=[{"src": "concept:s", "rel": "RELATED_TO",
                                         "dst": "concept:d", "deleted_at": _at(T0 + 4 * MINUTE)}])
    assert SKEW not in out and out.get("edges_deleted") == 1, out
    assert [r["d"] for r in _edge_tombstones(st)] == [_at(T0 + 4 * MINUTE)]


@pytest.mark.parametrize("bad", [253402300799999, ["9999"], {"t": "9999"}], ids=["int", "list", "dict"])
def test_a_stamp_that_is_not_a_string_is_refused_and_the_apply_does_not_raise(store, bad):
    st = store()
    _write(st, A, "concept:t", title="T0")
    _write(st, A, "concept:s")
    out = _push(st, A, _pushed("concept:t", bad, title="Bad"), _pushed("concept:new", bad),
                _pushed("concept:ok", _at(T0), title="Ok"),
                tombstones=[{"node_id": "concept:s", "label": "Concept", "deleted_at": bad, "rev": 2}],
                edge_tombstones=[{"src": "concept:s", "rel": "RELATED_TO", "dst": "concept:t",
                                  "deleted_at": bad}])
    assert out[SKEW] == ["concept:t", "concept:new", "concept:s",
                         sync.edge_ref("concept:s", "RELATED_TO", "concept:t")], out
    assert out["applied"] == 1 and _row(st, A, "concept:ok")["title"] == "Ok"
    assert _row(st, A, "concept:t")["title"] == "T0" and _row(st, A, "concept:new") is None
    assert _row(st, A, "concept:s") is not None
    did = ds.doc_id_for_origin(A, "bad-note")
    res = docsync.import_docs(st, owner=A, payload={
        "docs": [{"doc_id": did, "title": "", "origin": "", "content_hash": ds._sha("x"),
                  "updated_at": bad, "text": "x"}],
        "tombstones": [{"doc_id": "doc:none", "deleted_at": bad}]})
    assert res[SKEW] == [did, "doc:none"] and res["applied"] == 0, res
    assert _doc(st, did) is None


@pytest.mark.parametrize("ahead", [2 * MINUTE, 4 * MINUTE + 59_000, 5 * MINUTE],
                         ids=["2m", "4m59s", "exactly-the-ceiling"])
def test_a_stamp_up_to_the_ceiling_is_accepted_as_sent(store, ahead):
    st = store()
    _write(st, A, "concept:target")
    out = _push(st, A, _pushed("concept:target", _at(T0 + ahead), title="Fast device"),
                _pushed("concept:new", _at(T0 + ahead), title="New"))
    assert SKEW not in out, out
    assert _row(st, A, "concept:target")["title"] == "Fast device"
    assert _ua(st, A, "concept:new") == _at(T0 + ahead)
    # 시계는 그 스탬프까지 전진하고 다음 로컬 쓰기는 그 뒤로 찍힌다.
    assert _last(st) >= T0 + ahead
    _write(st, A, "concept:after")
    assert _ua(st, A, "concept:after") > _at(T0 + ahead)


def test_one_millisecond_past_the_ceiling_is_refused(store):
    st = store()
    out = _push(st, A, _pushed("concept:new", _at(T0 + 5 * MINUTE + 1)))
    assert _row(st, A, "concept:new") is None
    assert out.get(SKEW) == ["concept:new"], out


# ── 3. 문서와 문서 삭제 ───────────────────────────────────────────────────────
def test_a_far_document_and_a_far_document_deletion_are_refused(store, wall):
    st = store()
    kept = ds.doc_id_for_origin(A, "kept-note")
    far = ds.doc_id_for_origin(A, "far-note")
    first = docsync.import_docs(st, owner=A, payload=_docs_payload((kept, _at(T0), "kept")))
    assert first["applied"] == 1, first
    res = docsync.import_docs(st, owner=A, payload=_docs_payload(
        (far, Y9999, "far"), tombstones=[(kept, Y9999)]))
    assert _doc(st, far) is None and _doc(st, kept)["ua"] == _at(T0)
    assert res["applied"] == 0 and res["deleted"] == 0, res
    assert res.get(SKEW) == [far, kept], res
    assert res["warnings"] == [revstamp.clock_skew_warning(2)], res
    assert _last(st) <= revstamp.ceiling_ms()
    # 한 시간 뒤 정직한 편집은 둘 다 들어간다.
    wall.ms = T0 + HOUR
    later = docsync.import_docs(st, owner=A, payload=_docs_payload(
        (far, _at(wall.ms), "honest new"), (kept, _at(wall.ms), "honest edit")))
    assert SKEW not in later and later["applied"] == 1 and later["updated"] == 1, later
    assert _doc(st, far)["ua"] == _at(wall.ms) and _doc(st, kept)["ua"] == _at(wall.ms)


# ── 4. 믿는 길: 기기가 자기 허브에서 받는 pull ──────────────────────────────────
def test_a_trusted_pull_keeps_stamps_beyond_this_clock(store):
    """기기 시계가 2시간 느리다. 허브의 스탬프를 그대로 받고 시계도 거기까지 따라간다."""
    st = store()
    ahead = _at(T0 + 2 * HOUR)
    out = _push(st, A, _pushed("concept:hub", ahead, title="From hub"), trusted=True,
                tombstones=[{"node_id": "concept:gone", "label": "Concept", "deleted_at": ahead,
                             "rev": 2}],
                edge_tombstones=[{"src": "concept:hub", "rel": "RELATED_TO", "dst": "concept:x",
                                  "deleted_at": ahead}])
    assert SKEW not in out, out
    assert _ua(st, A, "concept:hub") == ahead
    assert [r["d"] for r in _edge_tombstones(st)] == [ahead]
    assert _last(st) >= T0 + 2 * HOUR
    _write(st, A, "concept:hub", title="Device edit")
    assert _ua(st, A, "concept:hub") > ahead
    did = ds.doc_id_for_origin(A, "hub-note")
    res = docsync.import_docs(st, owner=A, payload=_docs_payload((did, ahead, "hub")),
                              trusted_sender=True)
    assert SKEW not in res and _doc(st, did)["ua"] == ahead, res


# ── 5. 시계 되돌림: 오염된 시계는 돌아오고, 정상 범위의 후퇴는 순서를 지킨다 ────────
@pytest.mark.parametrize("poisoned", [Y9999_MS, Y2999_MS], ids=["y9999", "y2999"])
def test_a_poisoned_clock_comes_back_on_the_next_write(store, poisoned):
    st = store()
    _poison(st, poisoned)
    _write(st, A, "concept:first")
    first = _ua(st, A, "concept:first")
    assert first <= _top()
    assert _last(st) <= revstamp.ceiling_ms()
    _write(st, B, "concept:second")
    assert first < _ua(st, B, "concept:second") <= _top()


@pytest.mark.parametrize("step", [11 * MINUTE, 2 * HOUR, 23 * HOUR], ids=["11m", "2h", "23h"])
def test_local_stamp_order_holds_when_the_wall_clock_steps_back(store, wall, step):
    st = store()
    wall.ms = T0 + step
    _write(st, A, "concept:a", title="fast")
    before = _ua(st, A, "concept:a")
    wall.ms = T0
    _write(st, A, "concept:b", title="after correction")
    assert _ua(st, A, "concept:b") > before
    _write(st, A, "concept:a", title="edited after correction")
    assert _ua(st, A, "concept:a") > before


def _trusted_pull(store, owner, clock, *nodes):
    """기기가 자기 허브에서 받는 pull 한 쪽. `clock` 은 허브가 답에 실은 자기 벽시계다."""
    return sync.apply_delta(owner=owner, store=store, trusted_sender=True, bundle={
        "nodes": list(nodes), "edges": [], "tombstones": [], "edge_tombstones": [],
        "clock": clock})


@pytest.mark.parametrize("mark", [None, T0 + 2 * HOUR], ids=["no-hub-clock", "hub-clock-below"])
def test_a_clock_poisoned_by_2999_comes_back_with_or_without_a_hub_clock_below_it(store, mark):
    st = store()
    if mark is not None:
        assert _trusted_pull(st, A, _at(mark))["executed"]
    _poison(st, Y2999_MS)
    _write(st, A, "concept:first")
    assert _ua(st, A, "concept:first") <= _top()
    assert _last(st) <= revstamp.ceiling_ms()


def test_a_2999_item_from_the_hub_does_not_hold_the_device_clock(store):
    """허브에 남은 먼 스탬프 하나를 받아도 기기의 다음 쓰기는 벽시계 근처에 찍힌다(허브 시계가 기준이다)."""
    st = store()
    out = _trusted_pull(st, A, _at(T0), _pushed("concept:old", Y2999, title="Kept from before"))
    assert out["executed"] and _ua(st, A, "concept:old") == Y2999, out
    _write(st, A, "concept:next")
    assert _at(T0) <= _ua(st, A, "concept:next") <= _top()


def test_an_untrusted_upload_cannot_hold_a_poisoned_clock(store):
    """허브는 믿는 출처가 없다. 업로드가 실은 시계 값은 읽지 않으므로 오염된 시계는 다음 쓰기에서 돌아온다."""
    st = store()
    _poison(st, Y2999_MS)
    out = sync.apply_delta(owner=A, store=st, bundle={
        "nodes": [], "edges": [], "tombstones": [], "edge_tombstones": [], "clock": Y2999})
    assert out["executed"], out
    docsync.import_docs(st, owner=A, payload={"docs": [], "tombstones": [], "clock": Y2999})
    _write(st, B, "concept:mine")
    assert _ua(st, B, "concept:mine") <= _top()


@pytest.mark.parametrize("behind", [26 * HOUR, 400 * DAY], ids=["26h", "400d"])
def test_a_device_that_followed_its_hub_keeps_stamping_after_the_hub(store, behind):
    st = store()
    hub_now = T0 + behind
    _write(st, A, "concept:n", title="Device copy")
    out = _trusted_pull(st, A, _at(hub_now), _pushed("concept:n", _at(hub_now), title="From hub"))
    assert out["merged"] == 1 and _last(st) >= hub_now, out
    _write(st, A, "concept:n", title="Device edit")
    assert _ua(st, A, "concept:n") > _at(hub_now)


def test_the_upload_scan_names_what_apply_refuses(store):
    """청크째 실패를 정하는 검사(`skewed_upload_items`·`skewed_upload_docs`)는 적용이 거절하는 것과 같은 것을 같은 순서로 꼽는다."""
    st = store()
    _write(st, A, "concept:kept")
    bundle = {"nodes": [_pushed("concept:far", Y2999), _pushed("concept:near", _at(T0)),
                        {**_pushed("decision:wrong", Y2999), "type": "concept"},
                        {**_pushed("concept:odd", Y2999), "type": "nonesuch"},
                        _pushed("concept:int", 5)],
              "edges": [],
              "tombstones": [{"node_id": "concept:kept", "label": "Concept", "deleted_at": Y9999,
                              "rev": 1}, {"node_id": "", "label": "Concept", "deleted_at": Y9999}],
              "edge_tombstones": [{"src": "concept:a", "rel": "RELATED_TO", "dst": "concept:b",
                                   "deleted_at": Y2999},
                                  {"src": "concept:a", "rel": "NOT_A_REL", "dst": "concept:b",
                                   "deleted_at": Y2999}]}
    named = sync.skewed_upload_items(bundle)
    assert named == sync.apply_delta(owner=A, store=st, bundle=bundle)[SKEW]
    assert named == ["concept:far", "concept:int", "concept:kept",
                     sync.edge_ref("concept:a", "RELATED_TO", "concept:b")]
    payload = _docs_payload(("doc:far", Y2999, "far"), ("doc:near", _at(T0), "near"),
                            tombstones=[("doc:gone", Y9999)])
    docs = docsync.skewed_upload_docs(payload)
    assert docs == ["doc:far", "doc:gone"] == docsync.import_docs(st, owner=A, payload=payload)[SKEW]


def test_an_undeclared_upload_with_a_far_item_applies_nothing(store):
    st = store()
    bundle = {"nodes": [_pushed("concept:far", Y2999), _pushed("concept:near", _at(T0))],
              "edges": []}
    out = sync.apply_upload(owner=A, store=st, bundle=bundle)
    assert out["executed"] is False and out[SKEW] == ["concept:far"], out
    assert out["note"] == revstamp.clock_skew_warning(1)
    assert _row(st, A, "concept:near") is None
    declared = sync.apply_upload(owner=A, store=st, bundle={**bundle, revstamp.HANDLES: [SKEW]})
    assert declared["executed"] is True and declared[SKEW] == ["concept:far"], declared
    assert _row(st, A, "concept:near")["title"] == "Pushed"
    # 상한을 넘은 항목이 없으면 밝히지 않은 업로드도 그대로 적용된다.
    plain = sync.apply_upload(owner=A, store=st, bundle={"nodes": [
        _pushed("concept:plain", _at(T0))], "edges": []})
    assert plain["executed"] is True and SKEW not in plain, plain
    payload = _docs_payload(("doc:far", Y2999, "far"), ("doc:near", _at(T0), "near"))
    res = docsync.import_upload(st, owner=A, payload=payload)
    assert res["error"] == revstamp.clock_skew_warning(1) and res[SKEW] == ["doc:far"], res
    assert _doc(st, "doc:near") is None
    res = docsync.import_upload(st, owner=A, payload={**payload, revstamp.HANDLES: [SKEW]})
    assert "error" not in res and res[SKEW] == ["doc:far"] and _doc(st, "doc:near"), res


def test_observe_bounds_a_stamp_whose_digits_sort_low_but_read_far():
    """문자열로는 상한 아래로 정렬되지만 밀리초 칸이 길어 읽으면 먼 값도 믿지 않는 길에서는 시계를 밀지 못한다."""
    class _Meta:
        def __init__(self):
            self.v = "0"

        def run(self, cypher, params=None):
            if "SET" in cypher:
                self.v = params["v"]
                return []
            return [{"v": self.v}]

    meta = _Meta()
    stamp = _at(T0)[:-5] + ".999999999Z"
    revstamp.observe_hlc(meta, stamp)
    assert int(meta.v) <= revstamp.ceiling_ms()


# ── 6. 실행 기록 규칙과 한 묶음에서 ──────────────────────────────────────────
RUN_STATE = {"state": "waiting", "approved": ["merge"], "answer": "approve", "answer_via": "person",
             "answered_at": "2026-11-13T00:00:00Z", "answered_by": A, "asks": 2,
             "pending": {"actions": ["deploy"], "kind": "person_only", "question": "Deploy?"}}
FORGED = {"state": "working", "approved": ["merge", "deploy"], "answer": "approve",
          "answer_via": "person", "answered_by": "user:someone", "asks": 9}


def _run_node(nid, props, ua):
    return {"id": nid, "type": "episode", "title": "Run", "date": "2026-11-13",
            "domain": "vinc/agent/harness", "tags": ["agent-run"], "props": props,
            "_rev": 5, "_updated_at": ua}


def _run_props(st, nid):
    rows = st.run("MATCH (e:Episode) WHERE e.episode_id = $id RETURN e.props AS p",
                  {"id": pkns.key(A, nid)})
    return run_state.props_of(rows[0]["p"]) if rows else None


def test_far_runs_are_refused_and_the_run_state_rule_still_reports_what_it_kept(store):
    st = store()
    held_far, unseen_far, held_near = ("episode:run-held-far", "episode:run-unseen-far",
                                       "episode:run-held-near")
    for nid in (held_far, held_near):
        props = {"run_id": nid[len("episode:run-"):], "role": "concept:agent-x", **RUN_STATE}
        wrote = frag.merge_fragment({"owner": A, "nodes": [_run_node(nid, props, _at(T0))]}, store=st)
        assert wrote.get("executed"), wrote
    forged = {"role": "concept:agent-x", **FORGED}
    out = _push(st, A, _run_node(held_far, {**forged, "run_id": "held-far"}, Y9999),
                _run_node(unseen_far, {**forged, "run_id": "unseen-far"}, Y9999),
                _run_node(held_near, {**forged, "run_id": "held-near"}, _at(T0 + MINUTE)))
    assert _run_props(st, unseen_far) is None
    assert out[run_state.CODE] == [held_near], out
    for nid in (held_far, held_near):
        kept = _run_props(st, nid)
        assert {k: kept.get(k) for k in run_state.STATE_KEYS} == \
            {k: RUN_STATE.get(k) for k in run_state.STATE_KEYS}, (nid, kept)
    assert out.get(SKEW) == [held_far, unseen_far], out


# ── 7. 상한 안의 묶음: 믿는 길과 믿지 않는 길이 같은 행을 남긴다 ──────────────────
def _dump(st) -> dict:
    nodes = st.run("MATCH (c:Concept) WHERE c.owner_id = $o RETURN c.concept_id AS id, c.title AS t, "
                   "c.updated_at AS ua, c.rev AS r, c.sync_seq AS s ORDER BY id", {"o": A})
    tombs = st.run("MATCH (t:_Tombstone) RETURN t.node_id AS id, t.deleted_at AS d ORDER BY id")
    etombs = st.run("MATCH (t:_EdgeTombstone) RETURN t.src AS s, t.dst AS d, t.deleted_at AS da "
                    "ORDER BY s")
    return {"nodes": nodes, "tombs": tombs, "etombs": etombs, "last": _last(st)}


def test_a_bundle_with_no_stamp_beyond_the_ceiling_applies_the_same_on_both_paths(store):
    """그 묶음에는 규칙이 아무것도 바꾸지 않는다. origin/main 과의 바이트 대조는 PR 본문에 적은 탐침이 한다."""
    results, dumps = [], []
    for name, trusted in (("untrusted", False), ("trusted", True)):
        st = store(name)
        for i in range(5):
            _write(st, A, f"concept:n{i}", title=f"L{i}")
        res = _push(st, A, _pushed("concept:n0", _at(T0 - 1000), title="R0", rev=2),
                    _pushed("concept:n1", _at(T0 + MINUTE), title="R1"),
                    _pushed("concept:new", _at(T0 - 5000), title="New"),
                    _pushed("concept:n2", _at(T0 - 10 ** 9), title="Old"),
                    edges=[{"from": "concept:n0", "to": "concept:n1", "type": "relates_to"}],
                    tombstones=[{"node_id": "concept:n3", "label": "Concept",
                                 "deleted_at": _at(T0 + 1000), "rev": 3}],
                    edge_tombstones=[{"src": "concept:n0", "rel": "RELATED_TO", "dst": "concept:n4",
                                      "deleted_at": _at(T0)}],
                    trusted=trusted)
        assert SKEW not in res, res
        results.append(res)
        dumps.append(_dump(st))
    assert results[0] == results[1]
    assert dumps[0] == dumps[1]


# ── 8. 믿는 출처가 기기 시계를 끌고 갈 수 있는 폭 ─────────────────────────────────
class _MetaOnly:
    """`_SyncMeta` 키만 아는 가짜 스토어. 시계 함수만 부르는 테스트가 쓴다."""

    def __init__(self, **meta):
        self.meta = {k: str(v) for k, v in meta.items()}

    def run(self, cypher, params=None):
        if "SET" in cypher:
            self.meta[params["k"]] = params["v"]
            return []
        v = self.meta.get(params["k"])
        return [] if v is None else [{"v": v}]


def _trusted_clock(st):
    rows = st.run("MATCH (m:_SyncMeta) WHERE m.key = $k RETURN m.value AS v", {"k": revstamp.TRUSTED_CLOCK})
    return int(rows[0]["v"]) if rows and rows[0]["v"] not in (None, "") else None


def _writes_never_fail(st, count):
    """`count` 번 쓰고, 실패한 쓰기와 상한(벽시계 + `MAX_SKEW_MS`) 뒤에 찍힌 스탬프를 돌려준다."""
    failed, late = [], []
    for i in range(count):
        try:
            _write(st, A, f"concept:w{i}")
        except Exception as exc:          # 9999년 다음 해는 날짜가 아니라 쓰기가 예외로 끝난다
            failed.append((i, repr(exc)[:80]))
            continue
        if _ua(st, A, f"concept:w{i}") > _top():
            late.append(i)
    return failed, late


@pytest.mark.parametrize("earlier", [None, T0], ids=["no-earlier-hub-clock", "earlier-hub-clock"])
def test_a_hub_reply_at_year_9999_never_stops_the_device_writing(store, earlier):
    """허브 답의 `clock` 과 항목 스탬프가 9999년 끝이다. 기기의 150 번 쓰기가 모두 되고 벽시계 근처에 찍힌다."""
    st = store()
    _write(st, A, "concept:n", title="Device copy")
    if earlier is not None:
        assert _trusted_pull(st, A, _at(earlier))["executed"]
    out = _trusted_pull(st, A, Y9999, _pushed("concept:n", "9999-12-31T23:59:59.900Z", title="Far"))
    assert out["executed"], out
    assert _writes_never_fail(st, 150) == ([], [])
    assert _trusted_clock(st) == earlier


def test_stored_clocks_left_at_year_9999_never_stop_the_device_writing(store):
    """저장된 시계와 허브 시계가 이미 9999년 끝에 있는 스토어도 다음 쓰기에서 벽시계로 돌아온다."""
    st = store()
    _poison(st, Y9999_MS - 98)
    st.run("MERGE (m:_SyncMeta {key: $k}) SET m.value = $v",
           {"k": revstamp.TRUSTED_CLOCK, "v": str(Y9999_MS)})
    assert _writes_never_fail(st, 150) == ([], [])


def test_the_next_stamp_resets_to_the_wall_clock_instead_of_passing_year_9999(wall):
    """다음 값이 형식으로 나타낼 수 없으면(9999년 뒤) 벽시계로 되돌린다. 그 앞까지는 순서를 지킨다."""
    wall.ms = Y9999_MS - HOUR
    meta = _MetaOnly(last_hlc=Y9999_MS - 1)
    assert revstamp.hlc_now(meta) == Y9999
    assert revstamp.hlc_now(meta) == _at(Y9999_MS - HOUR)
    assert revstamp.hlc_now(meta) == _at(Y9999_MS - HOUR + 1)


def test_a_hub_clock_more_than_the_trusted_bound_ahead_is_not_recorded(store):
    """허브 시계는 기기 벽시계 + `TRUSTED_AHEAD_MS` 까지 적는다. 그 뒤의 값은 무시하고 앞서 적은 값을 둔다."""
    st = store()
    edge = T0 + revstamp.TRUSTED_AHEAD_MS
    assert _trusted_pull(st, A, _at(edge))["executed"]
    assert _trusted_clock(st) == edge
    assert _trusted_pull(st, A, _at(T0 + HOUR))["executed"]
    assert _trusted_clock(st) == T0 + HOUR
    for far in (_at(edge + 1), Y2999, Y9999):
        assert _trusted_pull(st, A, far)["executed"]
        assert _trusted_clock(st) == T0 + HOUR
    docsync.import_docs(st, owner=A, payload={"docs": [], "tombstones": [], "clock": Y2999},
                        trusted_sender=True)
    assert _trusted_clock(st) == T0 + HOUR


def test_the_trusted_bound_covers_a_device_three_years_slow():
    """세 해(윤일 하나를 포함한 1096일) 느린 기기도 자기 허브를 따라간다."""
    assert revstamp.TRUSTED_AHEAD_MS >= (3 * 365 + 1) * DAY


@pytest.mark.parametrize("hub", [None, T0 + DAY], ids=["hub-at-the-wall", "hub-a-day-ahead"])
def test_a_trusted_stamp_is_followed_up_to_the_hub_clock_plus_the_skew_and_no_further(wall, hub):
    """믿는 길에서 시계가 따라가는 스탬프는 max(벽시계, 적힌 허브 시계) + `MAX_SKEW_MS` 까지다. 그 뒤의 스탬프는
    따라가지 않는다(잘라서 따라가면 기기가 상한 끝에서 찍기 시작해 다음 편집이 허브에서 거절될 수 있다)."""
    top = max(T0, hub or T0) + revstamp.MAX_SKEW_MS
    meta = _MetaOnly(last_hlc=T0, trusted_clock=hub or T0)
    revstamp.observe_hlc(meta, _at(top), trusted=True)
    assert int(meta.meta["last_hlc"]) == top
    meta = _MetaOnly(last_hlc=T0, trusted_clock=hub or T0)
    for far in (_at(top + 1), Y2999, Y9999):
        revstamp.observe_hlc(meta, far, trusted=True)
        assert int(meta.meta["last_hlc"]) == T0, far


def test_the_hub_clock_is_recorded_before_the_pull_applies_nodes(store):
    """한 번 허브 시계를 적은 기기가 400일 앞선 허브의 편집을 받는다. 같은 답의 `clock` 을 적용 전에 적어야 그 편집을
    따라가고, 기기의 다음 편집이 그 뒤에 찍힌다."""
    st = store()
    _write(st, A, "concept:n", title="Device copy")
    assert _trusted_pull(st, A, _at(T0))["executed"]
    ahead = T0 + 400 * DAY
    out = _trusted_pull(st, A, _at(ahead), _pushed("concept:n", _at(ahead), title="From hub"))
    assert out["merged"] == 1 and _last(st) >= ahead, out
    _write(st, A, "concept:n", title="Device edit")
    assert _ua(st, A, "concept:n") > _at(ahead)


def test_the_hub_clock_is_recorded_before_the_pull_applies_documents(store):
    st = store()
    did = ds.doc_id_for_origin(A, "order-note")
    assert ds.put_doc(st, owner=A, text="device", doc_id=did, title="Note",
                      origin="order-note").get("executed")
    assert _trusted_pull(st, A, _at(T0))["executed"]
    ahead = T0 + 400 * DAY
    res = docsync.import_docs(st, owner=A, trusted_sender=True, payload={
        **_docs_payload((did, _at(ahead), "from hub")), "clock": _at(ahead)})
    assert res["updated"] == 1 and _last(st) >= ahead, res
    assert ds.put_doc(st, owner=A, text="device edit", doc_id=did, title="Note",
                      origin="order-note").get("executed")
    assert _doc(st, did)["ua"] > _at(ahead)
