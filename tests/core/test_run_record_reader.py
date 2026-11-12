# -*- coding: utf-8 -*-
"""실행 기록 props 를 읽는 한 벌(`store/run_state.py` `readable`)과 동기화가 받는 질문의 모양.

- `readable` 은 어떤 모양의 props 에서도 예외 없이 읽는 쪽이 그대로 변환해도 되는 사본을 낸다.
  대기 중인 실행은 dict 인 대기와 1 이상의 질문 번호를 갖는다.
- 믿지 않는 쪽이 보낸 처음 보는 실행의 질문(`questions_only`)은 받는 스토어의 시계로 프로토콜의
  시간 상한 안에서 만료되고, 무엇을 묻는지 없는 대기는 승인되는 행동이 없는 사람만의 대기가 된다.
  믿는 쪽에서 온 것(`keep_own(trusted=True)`)은 그대로다.
- 대기의 질문 문장이 비면 `run_protocol.decision` 이 저장된 행동과 검사로 만든다.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import run_state  # noqa: E402
from vinc_mcp import run_notify, run_protocol  # noqa: E402

NOW = datetime.datetime(2026, 11, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)
STAMP = "2026-11-13T12:00:00Z"
LIMIT = "2026-11-14T00:00:00Z"
WAITING = {"run_id": "r", "role": "concept:agent-maker", "outcome": "waiting", "state": "waiting",
           "step": 2, "asks": 1, "pending": {"actions": ["merge"], "checks": [], "question": "Q",
                                            "asked_at": STAMP, "kind": "person_only"}}

_ODD = [None, True, False, -3, 2.5, float("inf"), float("nan"), "x", "", "  ", "²", "9" * 5000,
        [], {}, [1, "a"], {"a": 1}]


@pytest.mark.parametrize("value", _ODD, ids=repr)
def test_count_reads_any_value_as_a_non_negative_int(value):
    n = run_state.count(value)
    assert isinstance(n, int) and not isinstance(n, bool) and n >= 0


@pytest.mark.parametrize("value,expected", [(3, 3), (3.0, 3), ("4", 4), (" 5 ", 5), ("x", 0),
                                            (-1, 0), (True, 0), (None, 0)])
def test_count_keeps_readable_numbers(value, expected):
    assert run_state.count(value) == expected


@pytest.mark.parametrize("field", ["step", "asks", "pending", "checks", "actions", "requests",
                                   "question", "cited_ids", "capability_flags",
                                   "capabilities_used", "used", "approved",
                                   "answered_actions"])
@pytest.mark.parametrize("value", _ODD, ids=repr)
def test_every_reader_takes_any_value_of_a_run_field(field, value):
    props = json.loads(json.dumps(WAITING))
    if field in ("checks", "actions", "requests", "question"):
        props["pending"][field] = value
    else:
        props[field] = value
    copy = repr(props)
    read = run_state.readable(props)
    assert repr(props) == copy
    assert isinstance(read["pending"], dict) and read["asks"] >= 1
    assert isinstance(read["step"], int)
    view = run_protocol.decision(read["pending"])
    assert view["question"] and isinstance(view["checks"], list)
    # 원본 props 로도 결정 보기와 알림 본문을 만든다.
    run_protocol.decision(props["pending"] if isinstance(props.get("pending"), dict) else {})
    assert run_notify.payload("user:u-1", "episode:run-r", props)["ask"] >= 1


def test_readable_leaves_a_well_formed_run_as_it_is():
    assert run_state.readable(WAITING) == WAITING
    done = {**WAITING, "state": "done", "outcome": "success"}
    done.pop("pending")
    done.pop("asks")
    assert run_state.readable(done) == done


def test_a_waiting_run_reads_with_a_pending_and_a_question_number():
    read = run_state.readable({**WAITING, "asks": "x", "pending": 5})
    assert read["pending"] == {} and read["asks"] == 1
    assert run_state.readable({"state": "working", "pending": 5})["pending"] == 5


# ── 동기화가 받는 질문 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("sent", ["2999-01-01T00:00:00Z", "never", "", None, 7])
def test_an_untrusted_question_expires_within_the_protocol_limit(sent):
    pending = {**WAITING["pending"]}
    if sent is not None:
        pending["expires_at"] = sent
    kept = run_state.questions_only({**WAITING, "pending": pending}, now=NOW)
    assert kept["pending"]["expires_at"] == LIMIT
    assert kept["pending"]["asked_at"] == STAMP


def test_an_untrusted_question_keeps_an_earlier_expiry_and_cuts_a_future_ask():
    pending = {**WAITING["pending"], "expires_at": "2026-11-13T12:30:00Z",
               "asked_at": "2999-01-01T00:00:00Z"}
    kept = run_state.questions_only({**WAITING, "pending": pending}, now=NOW)
    assert kept["pending"]["expires_at"] == "2026-11-13T12:30:00Z"
    assert kept["pending"]["asked_at"] == STAMP


def test_the_limit_is_the_protocol_limit():
    assert run_protocol.EXPIRES_MAX_S == run_state.EXPIRES_MAX_S == 43200


def test_a_trusted_pull_takes_the_question_as_it_is():
    pending = {**WAITING["pending"], "expires_at": "2999-01-01T00:00:00Z",
               "asked_at": "2999-01-01T00:00:00Z"}
    props, differed = run_state.keep_own({**WAITING, "pending": pending}, None, trusted=True)
    assert props["pending"] == pending and differed is False


@pytest.mark.parametrize("pending", ["absent", None, 5, "merge", ["merge"]])
def test_an_untrusted_waiting_run_without_a_pending_dict_waits_for_a_person_on_nothing(pending):
    props = {**WAITING}
    if pending == "absent":
        props.pop("pending")
    else:
        props["pending"] = pending
    kept = run_state.questions_only(props, now=NOW)
    assert kept["state"] == "waiting" and kept["approved"] == []
    assert kept["pending"] == {"actions": [], "kind": "person_only", "requests": [],
                               "asked_at": STAMP, "expires_at": LIMIT}


def test_an_untrusted_working_run_without_a_pending_stays_without_one():
    kept = run_state.questions_only({**WAITING, "state": "working", "pending": 5}, now=NOW)
    assert "pending" not in kept


# ── 질문 문장 ─────────────────────────────────────────────────────────────

def test_a_pending_without_a_question_gets_one_from_its_actions_and_checks():
    assert run_protocol.decision({"actions": ["merge"], "checks": ["x"]})["question"] == \
        "A person decides before the role goes on: merge."
    assert run_protocol.decision({"actions": [], "checks": ["person_only"]})["question"] == \
        "A gate asks a person to decide: person_only."
    policy = {"person_only": [{"action": "merge", "reason": "merge"}]}
    assert run_protocol.decision({"actions": ["merge"]}, policy)["question"] == \
        "A person decides before the role goes on: merge (merge)."


def test_a_pending_with_neither_actions_nor_checks_says_so():
    question = run_protocol.decision({})["question"]
    assert question == run_protocol.NO_QUESTION


def test_a_stored_question_is_kept():
    assert run_protocol.decision({"actions": ["merge"], "question": "Q"})["question"] == "Q"
