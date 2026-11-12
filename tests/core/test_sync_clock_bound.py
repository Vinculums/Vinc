"""바깥에서 온 스탬프는 스토어 시계(`last_hlc`)를 받는 쪽 벽시계 + `MAX_SKEW_MS` 너머로 끌고 가지 못한다.

허브 한 스토어가 여러 소유자를 들고 시계는 그 스토어에 하나다. 그래서 한 소유자의 번들이 시계를 미래로
밀면 모든 소유자의 다음 쓰기가 그 미래로 찍히거나(2999) 아예 찍히지 못한다(9999 다음 해는 날짜가 아니다).
상한을 넘은 노드는 상한으로 잘라 적용하고 응답 `clamped_stamps` 에 이름을 남긴다. 이미 오염된 시계는 다음
쓰기나 관측에서 벽시계로 돌아온다. 두 키 모드(`VINC_PK_NAMESPACED`)에서 같은 단정이 선다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from api import docstore as ds
from api import docsync, revstamp, runs, sync
from api import fragment as frag
from store import pkns
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

Y9999 = "9999-12-31T23:59:59.999Z"
Y2999 = "2999-01-01T00:00:00.000Z"
Y9999_MS = 253402300799999
Y2999_MS = 32472144000000


@pytest.fixture()
def store(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st
    finally:
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


def _write(store, owner, nid, title="Plain"):
    res = frag.merge_fragment({"owner": owner, "nodes": [
        {"id": nid, "type": "concept", "title": title, "domain": "clock"}], "edges": []}, store=store)
    assert res["executed"], res
    return res


def _pushed(nid, ua, title="Pushed"):
    return {"id": nid, "type": "concept", "title": title, "domain": "clock", "tags": [],
            "props": {}, "_rev": 1, "_updated_at": ua}


def _push(store, owner, *nodes, tombstones=()):
    return sync.apply_delta(owner=owner, store=store, bundle={
        "nodes": list(nodes), "edges": [], "tombstones": list(tombstones)})


def _ua(store, owner, nid):
    rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
                     "RETURN c.updated_at AS ua", {"id": pkns.key(owner, nid), "o": owner})
    return rows[0]["ua"] if rows else None


def _last(store) -> int:
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key = 'last_hlc' RETURN m.value AS v")
    return int(rows[0]["v"])


def _poison(store, ms: int) -> None:
    store.run("MERGE (m:_SyncMeta {key: 'last_hlc'}) SET m.value = $v", {"v": str(ms)})


def _top() -> str:
    return revstamp._fmt(revstamp.ceiling_ms())


# ── 1. 재현: 상한이 없으면 깨지는 셋 ─────────────────────────────────────────
def test_a_year_9999_stamp_on_an_existing_node_does_not_stop_later_writes(store):
    _write(store, A, "concept:target")
    # 내용이 같아 쓰기 없이 스탬프만 관측되는 번들이다.
    out = _push(store, A, _pushed("concept:target", Y9999, title="Plain"))
    assert out["executed"], out
    _write(store, A, "concept:after")
    assert _ua(store, A, "concept:after") <= _top()


def test_a_year_9999_stamp_on_an_existing_node_does_not_stop_a_run_start(store):
    _write(store, A, "concept:target")
    _push(store, A, _pushed("concept:target", Y9999, title="Plain"))
    opened = tools.call("vinc_run_start", {"role": ROLE, "task": "Review it.", "run_id": "clock-1"},
                        _StoreBridge(store, A))
    assert opened.get("executed") and opened.get("state") == "working", opened


def test_a_year_2999_stamp_does_not_carry_later_local_stamps_into_2999(store):
    _write(store, A, "concept:target")
    _push(store, A, _pushed("concept:target", Y2999))
    _write(store, A, "concept:after")
    assert _ua(store, A, "concept:after") <= _top()
    assert _last(store) <= revstamp.ceiling_ms()


def test_one_owner_push_does_not_move_the_clock_another_owner_writes_with(store):
    """허브 모양: 한 스토어에 두 소유자. 시계는 소유자별이 아니라 스토어에 하나다."""
    _write(store, A, "concept:target")
    _write(store, B, "concept:mine")
    _push(store, A, _pushed("concept:target", Y2999))
    _write(store, B, "concept:mine", title="B edits")
    assert _ua(store, B, "concept:mine") <= _top()


# ── 3. 상한을 넘은 노드: 상한으로 잘라 적용하고 알린다 ────────────────────────
def test_a_far_stamp_is_cut_to_the_ceiling_and_named_in_the_response(store):
    _write(store, A, "concept:target")
    out = _push(store, A, _pushed("concept:target", Y9999, title="Far"),
                _pushed("concept:fresh", Y9999, title="Far new"),
                _pushed("concept:plain", revstamp._fmt(revstamp._wall_ms()), title="Plain new"))
    assert out["executed"], out
    assert out["clamped_stamps"] == ["concept:target", "concept:fresh"]
    # 한 노드 때문에 번들이 실패하지 않는다: 세 노드가 다 반영된다.
    assert out["applied"] == 3, out
    for nid in ("concept:target", "concept:fresh"):
        assert _ua(store, A, nid) <= _top()
    assert _ua(store, A, "concept:plain") < _top()
    assert _last(store) <= revstamp.ceiling_ms()


def test_a_cut_stamp_does_not_win_every_later_deletion(store, monkeypatch):
    """잘리지 않은 9999 노드는 어떤 삭제보다 새것이라 영영 지워지지 않는다."""
    _push(store, A, _pushed("concept:fresh", Y9999))
    later = revstamp._wall_ms() + 3600 * 1000
    monkeypatch.setattr(revstamp, "_wall_ms", lambda: later)
    out = _push(store, A, tombstones=[{"node_id": "concept:fresh", "label": "Concept",
                                       "deleted_at": revstamp._fmt(later), "rev": 2}])
    assert out["deleted"] == 1, out
    assert _ua(store, A, "concept:fresh") is None


def test_a_far_stamp_cannot_resurrect_a_deleted_node(store):
    _write(store, A, "concept:gone")
    _push(store, A, tombstones=[{"node_id": "concept:gone", "label": "Concept",
                                 "deleted_at": revstamp._fmt(revstamp._wall_ms()), "rev": 2}])
    assert _ua(store, A, "concept:gone") is None
    out = _push(store, A, _pushed("concept:gone", Y9999))
    # 잘린 스탬프(지금 + 허용 스큐)는 방금의 삭제보다 새것이라 되살리기는 된다. 다만 그 뒤의
    # 삭제는 이긴다(위 테스트). 9999 를 그대로 믿으면 이후의 삭제가 모두 진다.
    assert out["clamped_stamps"] == ["concept:gone"]
    assert _ua(store, A, "concept:gone") <= _top()


# ── 2. 허용 스큐 안의 기기는 전과 같다 ──────────────────────────────────────
def test_a_device_a_few_minutes_ahead_syncs_as_before(store):
    _write(store, A, "concept:target")
    ahead_ms = revstamp._wall_ms() + 2 * 60 * 1000
    ahead = revstamp._fmt(ahead_ms)
    out = _push(store, A, _pushed("concept:target", ahead, title="From a fast device"))
    assert "clamped_stamps" not in out, out
    assert out["merged"] == 1, out
    # 시계는 빠른 기기의 스탬프까지 그대로 전진하고, 다음 로컬 쓰기는 그 뒤로 찍힌다.
    assert _last(store) >= ahead_ms
    _write(store, A, "concept:after")
    assert _ua(store, A, "concept:after") > ahead
    fresh = _push(store, A, _pushed("concept:new", ahead, title="New from a fast device"))
    assert "clamped_stamps" not in fresh, fresh
    assert _ua(store, A, "concept:new") == ahead


# ── 4. 이미 오염된 스토어는 다음 쓰기에서 돌아온다 ───────────────────────────
@pytest.mark.parametrize("poisoned", [Y9999_MS, Y2999_MS])
def test_a_poisoned_clock_comes_back_on_the_next_write(store, poisoned):
    _poison(store, poisoned)
    _write(store, A, "concept:first")
    first = _ua(store, A, "concept:first")
    assert first <= _top()
    assert _last(store) <= revstamp.ceiling_ms()
    # 되돌린 뒤로는 평소처럼 단조다(되돌림이 반복되지 않는다).
    _write(store, B, "concept:second")
    assert first < _ua(store, B, "concept:second") <= _top()


@pytest.mark.parametrize("poisoned", [Y9999_MS, Y2999_MS])
def test_a_poisoned_clock_comes_back_when_a_push_is_observed(store, poisoned):
    _write(store, A, "concept:target")
    _poison(store, poisoned)
    out = _push(store, A, _pushed("concept:target", revstamp._fmt(revstamp._wall_ms()), "Edit"))
    assert out["executed"], out
    assert _last(store) <= revstamp.ceiling_ms()
    assert _ua(store, A, "concept:target") <= _top()


# ── 5. 문서 동기화도 같은 상한이다 ──────────────────────────────────────────
def test_a_far_document_stamp_is_cut_and_does_not_move_the_clock(store):
    did = ds.doc_id_for_origin(A, "clock-note")
    payload = {"docs": [{"doc_id": did, "title": "", "origin": "", "content_hash": ds._sha("far"),
                         "updated_at": Y9999, "text": "far"}], "tombstones": []}
    res = docsync.import_docs(store, owner=A, payload=payload)
    assert res["applied"] == 1 and res["clamped_stamps"] == [did], res
    assert _last(store) <= revstamp.ceiling_ms()
    rows = store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.updated_at AS ua", {"id": did})
    assert rows[0]["ua"] <= _top()
    _write(store, A, "concept:after")
    assert _ua(store, A, "concept:after") <= _top()


def test_observe_bounds_a_stamp_whose_digits_sort_low_but_read_far():
    """문자열로는 상한 아래로 정렬되지만 밀리초 칸이 길어 읽으면 먼 값도 시계를 밀지 못한다."""
    class _Meta:
        def __init__(self):
            self.v = "0"

        def run(self, cypher, params=None):
            if "SET" in cypher:
                self.v = params["v"]
                return []
            return [{"v": self.v}]

    meta = _Meta()
    now = revstamp._wall_ms()
    stamp = revstamp._fmt(now)[:-5] + ".999999999Z"
    revstamp.observe_hlc(meta, stamp)
    assert int(meta.v) <= revstamp.ceiling_ms()
