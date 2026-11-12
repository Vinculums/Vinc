# -*- coding: utf-8 -*-
"""동기화 적용(`api/sync.py` `apply_delta`)이 실행 기록의 프로토콜 상태를 받는 스토어의 값으로 남기는가.

실행 기록은 Episode `episode:run-*` 이다. 대기·승인·답(`store/run_state.py` `STATE_KEYS`)은 그 실행을
가진 스토어의 실행 프로토콜만 쓴다. 허브의 push 적용과 기기의 pull 적용이 같은 함수를 지나므로 규칙도
하나다: 들어온 상태 키는 버리고, 받는 스토어에 저장된 상태 키를 남긴다. 처음 보는 실행은 상태 없이 둔다.

`apply_delta` 는 모든 기기의 동기화 경로다. 그래서 실행 기록이 없는 묶음에는 이 규칙이 아무것도 바꾸지
않아야 한다: 규칙을 끈 적용과 저장된 행이 한 글자도 다르지 않고, 스토어 조회 수도 같다.

스토어는 실물(임베디드)이다. 키 모드 둘(`VINC_PK_NAMESPACED=0`·`=1`)에서 같은 결과가 나와야 한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag, revstamp, sync  # noqa: E402
from store import pkns, run_state  # noqa: E402

OWNER = "user:u-1"
RUN = "episode:run-sync-rule"
#: 받는 스토어가 이미 가진 실행의 상태: 사람이 merge 를 승인했고 다음 질문을 기다린다.
OWN_STATE = {"state": "waiting", "approved": ["merge"], "answer": "approve", "answer_via": "person",
             "answered_at": "2026-11-13T00:00:00Z", "answered_by": OWNER, "asks": 2,
             "pending": {"actions": ["deploy"], "kind": "person_only", "question": "Deploy?"}}
#: 다른 쪽이 보낸 상태: 승인을 지우고 다른 답을 꾸몄다.
FORGED_STATE = {"state": "working", "approved": ["merge", "deploy"], "answer": "approve",
                "answer_via": "person", "answered_by": "user:someone", "asks": 9}


@pytest.fixture
def open_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "0")
    monkeypatch.setenv("VINC_STORE_SNAPSHOT", "")
    from store.backend import open_store as _open
    from store.config import load_config
    opened = []

    def make(name: str):
        cfg = load_config({"data_dir": str(tmp_path / name / "store")})
        store = _open(cfg)
        assert store is not None, "embedded store did not open"
        store.provision(dim=cfg.embedding_dim)
        opened.append(store)
        return store

    yield make
    for store in opened:
        store.close()


def _bundle(*nodes, edges=(), tombstones=(), edge_tombstones=()) -> dict:
    return {"nodes": list(nodes), "edges": list(edges), "tombstones": list(tombstones),
            "edge_tombstones": list(edge_tombstones)}


def _run_node(props, *, nid: str = RUN, rev: int = 1, ua: str = "2026-11-13T00:00:01.000Z",
              **extra) -> dict:
    node = {"id": nid, "type": "episode", "title": "Run", "date": "2026-11-13",
            "domain": "vinc/agent/harness", "tags": ["agent-run"], "_rev": rev, "_updated_at": ua}
    if props is not None:
        node["props"] = props
    node.update(extra)
    return node


def _stored_props(store, nid: str = RUN) -> dict:
    rows = store.run("MATCH (e:Episode) WHERE e.episode_id = $id RETURN e.props AS p",
                     {"id": pkns.key(OWNER, nid)})
    assert len(rows) == 1, rows
    return run_state.props_of(rows[0]["p"])


def _own_run(store, extra: dict | None = None) -> dict:
    """받는 스토어에 상태가 든 실행 하나를 쓴다(그 스토어의 실행 프로토콜이 쓴 것처럼)."""
    props = {"run_id": "sync-rule", "role": "concept:agent-x", "host": "laptop", **OWN_STATE,
             **(extra or {})}
    wrote = frag.merge_fragment({"owner": OWNER, "nodes": [_run_node(props)]}, store=store)
    assert wrote.get("executed"), wrote
    return props


def _state(props: dict) -> dict:
    return {k: props[k] for k in run_state.STATE_KEYS if k in props}


# ── 실행 기록: 받는 스토어의 상태가 남는다 ────────────────────────────────────

@pytest.mark.parametrize("remote_newer", [True, False], ids=["remote-newer", "local-newer"])
def test_a_store_keeps_its_own_run_state_when_a_bundle_brings_another(open_store, remote_newer):
    store = open_store("receiver")
    own = _own_run(store)
    ua = "2099-01-01T00:00:00.000Z" if remote_newer else "2000-01-01T00:00:00.000Z"
    incoming = {"run_id": "sync-rule", "role": "concept:agent-x", "host": "device 2.0",
                **FORGED_STATE}
    out = sync.apply_delta(owner=OWNER, bundle=_bundle(_run_node(incoming, rev=7, ua=ua)),
                           store=store)
    assert out["executed"] is True and out[run_state.CODE] == [RUN], out
    assert any(w.startswith(run_state.CODE + ":") for w in out["warnings"]), out
    after = _stored_props(store)
    assert _state(after) == _state(own)
    # 상태가 아닌 필드는 보통의 3-way 규칙을 따른다: 더 새로운 쪽이 이긴다.
    assert after["host"] == ("device 2.0" if remote_newer else "laptop")


def test_a_bundle_that_carries_the_same_state_reports_nothing(open_store):
    store = open_store("receiver")
    own = _own_run(store)
    out = sync.apply_delta(owner=OWNER, bundle=_bundle(_run_node({**own, "host": "device"},
                                                                  ua="2099-01-01T00:00:00.000Z")),
                           store=store)
    assert run_state.CODE not in out, out
    assert not any(w.startswith(run_state.CODE) for w in out["warnings"]), out
    assert _stored_props(store)["host"] == "device"


def test_a_run_the_store_never_saw_is_stored_without_state(open_store):
    store = open_store("receiver")
    incoming = {"run_id": "sync-rule", "role": "concept:agent-x", **FORGED_STATE}
    out = sync.apply_delta(owner=OWNER, bundle=_bundle(_run_node(incoming)), store=store)
    assert out["applied"] == 1 and out[run_state.CODE] == [RUN], out
    after = _stored_props(store)
    assert _state(after) == {} and after["role"] == "concept:agent-x"


def test_a_run_without_props_in_the_bundle_leaves_the_stored_props_alone(open_store):
    store = open_store("receiver")
    own = _own_run(store)
    out = sync.apply_delta(owner=OWNER, bundle=_bundle(_run_node(None, title="Renamed",
                                                                 ua="2099-01-01T00:00:00.000Z")),
                           store=store)
    assert run_state.CODE not in out, out
    assert _stored_props(store) == own


@pytest.mark.parametrize("spell", [" {}", "{} ", "\t{}\n", " {}"],
                         ids=["lead-space", "trail-space", "tab-newline", "nbsp"])
def test_a_spelled_run_id_lands_on_the_stored_run_and_keeps_its_state(open_store, spell):
    store = open_store("receiver")
    own = _own_run(store)
    node = _run_node(json.dumps(FORGED_STATE), nid=spell.format(RUN), ua="2099-01-01T00:00:00.000Z")
    out = sync.apply_delta(owner=OWNER, bundle=_bundle(node), store=store)
    assert out[run_state.CODE] == [RUN], out
    assert _state(_stored_props(store)) == _state(own)


# ── 실행 기록이 없는 묶음: 규칙을 끈 적용과 같다 ──────────────────────────────

def _frozen_clock(monkeypatch):
    """두 스토어가 같은 시각을 찍게 HLC 의 물리 시각을 고정한다. 단조 전진(+1)은 그대로다."""
    def hlc_now(store):
        last = int(revstamp._meta_get(store, "last_hlc", "0"))
        cur = max(1_800_000_000_000, last + 1)
        revstamp._meta_set(store, "last_hlc", cur)
        return revstamp._fmt(cur)
    monkeypatch.setattr(revstamp, "hlc_now", hlc_now)


def _dump(store) -> dict:
    """스토어의 모든 노드·관계 표의 모든 행. 표마다 정렬한 JSON 목록이다."""
    out = {}
    for table in store.run("CALL show_tables() RETURN *"):
        name, kind = table["name"], str(table.get("type") or "").upper()
        query = (f"MATCH (x:{name}) RETURN x" if kind == "NODE"
                 else f"MATCH ()-[x:{name}]->() RETURN x")
        out[name] = sorted(json.dumps(r["x"], sort_keys=True, default=str)
                           for r in store.run(query))
    return out


def _ordinary_steps():
    """기기 동기화가 실제로 나르는 모양들: 새 노드, 편집 충돌, 늦은 필드가 빠진 노드, 공백 id,
    다른 타입, 지원하지 않는 타입, 관계, 묘비, 관계 묘비. 실행 기록은 없다(`episode:` 는 있다)."""
    first = _bundle(
        {"id": "concept:a", "type": "concept", "title": "A", "domain": "work/x", "tags": ["t"],
         "props": {"k": 1}, "one_liner": "a", "_rev": 1, "_updated_at": "2026-11-13T00:00:01.000Z",
         "_created": "2026-11-01T00:00:00.000Z"},
        {"id": "episode:2026-11-13-standup", "type": "episode", "title": "Standup",
         "date": "2026-11-13", "domain": "work/x", "summary": "s", "props": {"who": ["a"]},
         "_rev": 1, "_updated_at": "2026-11-13T00:00:01.000Z"},
        {"id": "decision:d", "type": "decision", "title": "D", "domain": "work/x",
         "props": {"status": "accepted"}, "_rev": 1, "_updated_at": "2026-11-13T00:00:01.000Z"},
        {"id": "record:r", "type": "record", "title": "R", "domain": "work/x", "props": {"n": 2},
         "_rev": 1, "_updated_at": "2026-11-13T00:00:01.000Z"},
        {"id": "concept:b", "type": "concept", "title": "B", "domain": "work/x",
         "_rev": 1, "_updated_at": "2026-11-13T00:00:01.000Z"},
        edges=[{"from": "concept:a", "to": "concept:b", "type": "relates_to"},
               {"from": "episode:2026-11-13-standup", "to": "concept:a", "type": "about"}])
    second = _bundle(
        {"id": "concept:a", "type": "concept", "title": "A2", "domain": "work/x", "tags": ["t"],
         "props": {"k": 2}, "one_liner": "a", "_rev": 2, "_updated_at": "2026-11-13T00:00:03.000Z"},
        {"id": "episode:2026-11-13-standup", "type": "episode", "title": "Standup",
         "date": "2026-11-13", "domain": "work/x", "summary": "s2", "_rev": 2,
         "_updated_at": "2026-11-13T00:00:03.000Z"},
        {"id": " concept:spaced", "type": "concept", "title": "Spaced", "domain": "work/x",
         "_rev": 1, "_updated_at": "2026-11-13T00:00:03.000Z"},
        {"id": "concept:typed", "type": "episode", "title": "Wrong type", "domain": "work/x"},
        {"id": "bridge:x", "type": "bridge", "title": "Unsupported"},
        tombstones=[{"node_id": "record:r", "label": "Record", "deleted_at": "2026-11-13T00:00:04.000Z",
                     "rev": 3}],
        edge_tombstones=[{"src": "concept:a", "rel": "RELATED_TO", "dst": "concept:b",
                          "deleted_at": "2026-11-13T00:00:04.000Z"}])
    return first, second


def _replay(store, apply, monkeypatch) -> tuple[dict, list[int], list[dict]]:
    """두 묶음을 `apply` 로 차례로 적용하고 (저장된 행 전부, 적용마다 스토어 호출 수, 결과)를 돌려준다."""
    calls = [0]
    real_run = store.run

    def counted(*args, **kwargs):
        calls[0] += 1
        return real_run(*args, **kwargs)

    first, second = _ordinary_steps()
    reads, outs = [], []
    with monkeypatch.context() as m:
        m.setattr(store, "run", counted)
        outs.append(apply(owner=OWNER, bundle=first, store=store))
        reads.append(calls[0])
        # 받는 쪽의 로컬 편집 하나: 다음 묶음과 같은 필드를 고쳐 충돌을 만든다.
        frag.merge_fragment({"owner": OWNER, "nodes": [{"id": "concept:a", "type": "concept",
                                                        "title": "A local"}]}, store=store)
        calls[0] = 0
        outs.append(apply(owner=OWNER, bundle=second, store=store))
        reads.append(calls[0])
    return _dump(store), reads, outs


def test_an_ordinary_bundle_never_opens_the_rule_and_applies_as_with_the_rule_removed(
        open_store, monkeypatch):
    _frozen_clock(monkeypatch)
    opened = []
    real_keep = run_state.keep_own

    def keep_own(*args):
        opened.append(args)
        return real_keep(*args)

    with monkeypatch.context() as m:
        m.setattr(sync.run_state, "keep_own", keep_own)
        dump, reads, outs = _replay(open_store("with-rule"), sync.apply_delta, monkeypatch)
    assert opened == []
    with monkeypatch.context() as m:
        m.setattr(sync.run_state, "run_id", lambda nid: None)
        removed = _replay(open_store("rule-removed"), sync.apply_delta, monkeypatch)
    assert dump == removed[0]
    # 노드마다 읽기를 더하지 않는다: 스토어 호출 수가 같다.
    assert reads == removed[1]
    assert outs == removed[2]
    assert all(run_state.CODE not in out for out in outs)
    # 대조가 빈 것끼리의 비교가 아니다: 묶음이 실제로 쓰였고 지웠다.
    assert outs[0]["applied"] == 5 and outs[1]["deleted"] == 1, outs
    assert any("concept:a" in row for row in dump["Concept"])
