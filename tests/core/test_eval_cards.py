"""역할에 붙은 평가 카드 읽기(api/eval_cards.py): 실엔진 저장, 최신순, 대체, 소유자 격리, 이벤트 제외.

카드를 만드는 SDK(`vinc_client.eval_card`)의 조각을 마지막 시험이 파일 경로로 불러(패키지의
pydantic 의존을 피한다) 같은 스토어에 쓰고 되읽는다. 허브와 SDK 는 서로 import 하지 않으므로 이
시험이 둘의 접두·필드·대체 규칙이 맞는지 잡는다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from api import eval_cards, events, fragment as frag
from store.backend import open_store
from store.config import load_config

OWNER = "user:cards-owner"
OTHER = "user:cards-other"
ROLE = "concept:agent-cards-watcher"
ROLE_PROPS = {"mission": "Be measured.", "tier": "verify", "rung": "fast", "never": ["guess"],
              "person_only": [], "reads": []}
SUITE = {"id": "agent-tasks-test", "hash": "8c" * 32, "tasks": 40}
CELL = {"adapter": "langgraph", "model_family": "claude", "tasks": 40, "runs_per_task": 3,
        "pass_1": {"k": 90, "n": 120, "rate": 0.75}, "pass_k": {"k": 21, "n": 40, "rate": 0.525},
        "violation": None, "fabrication": {"k": 1, "n": 24, "rate": 0.0417},
        "cost_per_success_usd": 0.0412}


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


def ingest(store, nodes, edges=(), owner=OWNER):
    res = frag.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)},
                              store=store)
    assert res.get("executed") is not False, res
    return res


def with_role(store, owner=OWNER):
    ingest(store, [{"id": ROLE, "type": "concept", "title": "Cards watcher", "domain": "agent",
                    "props": ROLE_PROPS}], owner=owner)


def card(cid, day, *, suite=SUITE, cells=(CELL,), extra=None):
    return {"id": cid, "type": "record", "title": f"Eval card {day}", "valid_from": day,
            "domain": "vinc/agent/harness", "tags": ["eval-card"],
            "props": {"claim_kind": "result", "role": ROLE, "suite": suite, "date": day,
                      "harness_version": "agent-tasks-1", "store_fingerprint": "m3-ort130",
                      "results": list(cells), **(extra or {})}}


def attach(cid, role=ROLE):
    return {"from": cid, "to": role, "type": "relates_to", "evidence": "A measured result."}


def read(store, **kw):
    out = eval_cards.list_cards(store, owner=kw.pop("owner", OWNER), role=kw.pop("role", ROLE), **kw)
    assert out["executed"] is True, out
    return out


def test_a_role_with_no_card_has_an_empty_list(store):
    with_role(store)
    assert read(store)["cards"] == [] and read(store)["total"] == 0


def test_cards_come_back_newest_first_with_their_measurement(store):
    with_role(store)
    old, new = "record:eval-card-cards-watcher-2026-10-01-aaaaaaaa", "record:eval-card-cards-watcher-2026-10-26-bbbbbbbb"
    ingest(store, [card(old, "2026-10-01"), card(new, "2026-10-26", extra={
        "frontier": [{"label": "brief only", "success": 0.7, "tokens_in_mean": 9000.0}]})],
        [attach(old), attach(new)])
    out = read(store)
    assert [c["id"] for c in out["cards"]] == [new, old] and out["total"] == 2
    first = out["cards"][0]
    assert first["role"] == ROLE and first["date"] == "2026-10-26" and first["current"] is True
    assert first["suite"] == SUITE and first["results"][0]["pass_1"]["rate"] == 0.75
    assert first["results"][0]["violation"] is None and first["frontier"][0]["label"] == "brief only"
    assert first["harness_version"] == "agent-tasks-1" and first["store_fingerprint"] == "m3-ort130"


def test_a_superseded_card_stays_listed_but_is_no_longer_current(store):
    with_role(store)
    old, new = "record:eval-card-cards-watcher-2026-10-01-aaaaaaaa", "record:eval-card-cards-watcher-2026-10-26-bbbbbbbb"
    ingest(store, [card(old, "2026-10-01")], [attach(old)])
    res = ingest(store, [card(new, "2026-10-26"), {"id": old, "type": "record", "valid_until": "2026-10-26"}],
                 [attach(new), {"from": new, "to": old, "type": "supersedes", "evidence": "Newer."}])
    assert not res.get("warnings"), res.get("warnings")
    out = read(store)
    by_id = {c["id"]: c for c in out["cards"]}
    assert by_id[new]["current"] is True and by_id[old]["current"] is False
    assert by_id[old]["valid_until"] == "2026-10-26"
    # 대체는 나머지를 지우지 않는다: 옛 카드의 측정은 그대로 남는다.
    assert by_id[old]["results"][0]["pass_1"]["k"] == 90


def test_only_eval_cards_attached_to_that_role_are_listed(store):
    with_role(store)
    other_role = "concept:agent-someone-else"
    ingest(store, [{"id": other_role, "type": "concept", "title": "Other", "domain": "agent",
                    "props": ROLE_PROPS}])
    mine, theirs = "record:eval-card-cards-watcher-2026-10-26-bbbbbbbb", "record:eval-card-other-2026-10-26-cccccccc"
    ingest(store, [card(mine, "2026-10-26"), card(theirs, "2026-10-26"),
                   {"id": "record:a-lesson", "type": "record", "title": "A lesson", "domain": "agent"}],
           [attach(mine), attach(theirs, other_role), attach("record:a-lesson")])
    assert [c["id"] for c in read(store)["cards"]] == [mine]
    assert [c["id"] for c in read(store, role="agent-someone-else")["cards"]] == [theirs]


def test_the_limit_truncates_and_owners_do_not_see_each_other(store):
    with_role(store)
    with_role(store, owner=OTHER)
    ids = [f"record:eval-card-cards-watcher-2026-10-{d:02d}-0000000{d % 10}" for d in range(10, 15)]
    ingest(store, [card(i, f"2026-10-{n}") for i, n in zip(ids, range(10, 15))],
           [attach(i) for i in ids])
    out = read(store, limit=2)
    assert len(out["cards"]) == 2 and out["total"] == 5 and out["truncated"] is True
    assert out["cards"][0]["date"] == "2026-10-14"
    assert read(store, owner=OTHER)["cards"] == []


def test_a_short_role_name_reads_the_same_cards_as_the_full_id(store):
    with_role(store)
    cid = "record:eval-card-cards-watcher-2026-10-26-bbbbbbbb"
    ingest(store, [card(cid, "2026-10-26")], [attach(cid)])
    for name in ("cards-watcher", "agent-cards-watcher", ROLE, " cards-watcher "):
        assert [c["id"] for c in read(store, role=name)["cards"]] == [cid]
        assert read(store, role=name)["role"] == ROLE
    assert eval_cards.role_address("") is None and eval_cards.role_address("concept:") is None


def test_bad_input_is_refused(store):
    assert eval_cards.list_cards(store, owner="", role=ROLE)["executed"] is False
    assert eval_cards.list_cards(store, owner=OWNER, role="")["executed"] is False
    assert eval_cards.clamp_limit(None) == eval_cards.DEFAULT_LIMIT
    for bad in (0, 101, "x"):
        with pytest.raises(ValueError):
            eval_cards.clamp_limit(bad)


def test_an_eval_card_attached_to_a_role_is_not_announced_as_a_lesson(store):
    with_role(store)
    start = events.parse_cursor(events.scan(store, owner=OWNER, since=None)["cursor"])
    cid = "record:eval-card-cards-watcher-2026-10-26-bbbbbbbb"
    ingest(store, [card(cid, "2026-10-26"),
                   {"id": "record:lesson-one", "type": "record", "title": "Read the diff first",
                    "domain": "agent"}], [attach(cid), attach("record:lesson-one")])
    out = events.scan(store, owner=OWNER, since=start, kinds=("role.lesson_added",))
    assert [e["data"]["lesson"] for e in out["events"]] == ["record:lesson-one"]
    assert eval_cards.CARD_PREFIX == events.EVAL_CARD_PREFIX


def _sdk():
    path = (Path(__file__).resolve().parents[2] / "integrations" / "client" / "python" / "src"
            / "vinc_client" / "eval_card.py")
    spec = importlib.util.spec_from_file_location("eval_card_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_what_the_sdk_writes_reads_back_and_a_newer_card_supersedes_it_with_no_warning(store):
    sdk = _sdk()
    assert sdk.CARD_PREFIX == eval_cards.CARD_PREFIX == events.EVAL_CARD_PREFIX
    with_role(store)
    first = sdk.build_eval_card_fragment(ROLE, suite=SUITE, results=[{**CELL, "violation": None}],
                                         store_fingerprint="m3-ort130", harness_version="agent-tasks-1",
                                         date="2026-10-01")
    assert not ingest(store, first["nodes"], first["edges"]).get("warnings")
    old_id = first["nodes"][0]["id"]
    assert [c["current"] for c in read(store)["cards"]] == [True]
    second = sdk.build_eval_card_fragment(
        ROLE, suite=SUITE, store_fingerprint="m3-ort130", harness_version="agent-tasks-1",
        date="2026-10-26", supersedes=[old_id],
        results=[{**CELL, "model_family": "gpt", "violation": {"k": 2, "n": 56}}],
        frontier=[{"label": "brief only", "success": 0.7, "tokens_in_mean": 9000}])
    res = ingest(store, second["nodes"], second["edges"])
    assert not res.get("warnings"), res.get("warnings")
    cards = {c["id"]: c for c in read(store)["cards"]}
    new_id = second["nodes"][0]["id"]
    assert cards[new_id]["current"] is True and cards[old_id]["current"] is False
    assert cards[old_id]["valid_until"] == "2026-10-26"
    assert cards[new_id]["results"][0]["violation"] == {"k": 2, "n": 56, "rate": 0.0357}
    assert cards[new_id]["frontier"][0]["success"] == 0.7
    assert cards[old_id]["results"][0]["model_family"] == "claude"
