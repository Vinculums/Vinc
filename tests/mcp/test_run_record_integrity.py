# -*- coding: utf-8 -*-
"""잘못된 모양의 실행 기록이 승인함과 실행 읽기를 멈추지 않는다.

실행 기록(`episode:run-*`)의 props 는 동기화 업로드와 `vinc_ingest` 의 `props_patch` 로도 들어온다.
그 값이 수가 아니거나 목록이 아니어도(예: `asks: "x"`, `step: "x"`, `pending.checks: 5`) 읽는 쪽은
예외로 멈추지 않고, 같은 소유자의 다른 대기는 그대로 보이며, 잘못된 기록 자신도 사람이 보고 닫을
수 있게 보인다. 읽는 쪽은 모두 `store/run_state.py` 의 `readable` 한 벌을 지난다.

같은 스택(임베디드 스토어 위의 사이드카 ← 브리지 ← 공개 허브)으로 동기화가 받는 질문의 시간 상한,
무엇을 묻는지 없는 대기, 질문 문장이 없는 대기, 상태가 없는 실행 기록도 본다.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import run_state  # noqa: E402
from vinc_mcp import run_notify, run_protocol  # noqa: E402
from test_run_state_entrances import (  # noqa: E402,F401
    OWNER, PLAIN, ROLE, Hub, _episode, _push, _stamp, _waiting_run, hub)

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(text: str) -> datetime.datetime:
    return datetime.datetime.strptime(text, _FMT).replace(tzinfo=datetime.timezone.utc)


def _unseen(run_id: str, **fields) -> tuple[str, dict]:
    """허브가 처음 보는, 대기 중인 실행 하나의 props. `fields` 가 덮는다."""
    now = _stamp(0)
    props = {"run_id": run_id, "role": ROLE, "task": "Review the change.", "outcome": "waiting",
             "step": 1, "state": "waiting", "asks": 1, "last_asked_at": now,
             "pending": {"actions": ["merge"], "kind": "person_only", "asked_at": now,
                         "requests": [{"action": "merge", "reason": "r", "kind": "person_only"}]}}
    props.update(fields)
    return run_protocol.episode_id(run_id), props


def _pushed(hub: Hub, eid: str, props: dict) -> None:
    status, out = _push(hub, _episode(eid, props))
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out


def _patch_step(hub: Hub, eid: str, value) -> None:
    """`vinc_ingest` 의 `props_patch` 로 `step` 만 바꾼다(프로토콜 상태가 아니라 받아들여진다)."""
    node = hub.tool("vinc_node", node_id=eid, raw=True)["node"]
    out = hub.tool("vinc_ingest", nodes=[{"id": eid, "type": "episode", "title": node["title"],
                                          "props_patch": {"step": value},
                                          "expected_rev": node["rev"]}])
    assert out.get("executed") is True and out.get("error") is None, out
    assert hub.tool("vinc_node", node_id=eid, raw=True)["node"]["props"]["step"] == value


def _tool(hub: Hub, name: str, **arguments) -> dict:
    """`hub.tool` 과 같되, 도구 결과 대신 JSON-RPC 오류가 오면 그 오류를 보이며 실패한다."""
    status, reply = hub.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}})
    assert status == 200 and "result" in reply, reply
    result = reply["result"]
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def _events_cursor(hub: Hub) -> str:
    out = hub.tool("vinc_events", kinds=["run.waiting"])
    assert out.get("executed") is True, out
    return out["cursor"]


# ── 1. 잘못된 모양의 기록 하나가 승인함 전체를 가리지 않는다 ──────────────────

def test_one_malformed_run_record_of_each_kind_hides_no_waiting_run(hub):
    cursor = _events_cursor(hub)
    good = [_waiting_run(hub, f"integ-good-{i}") for i in range(3)]
    asks_eid, asks_props = _unseen("integ-bad-asks", asks="x")
    _pushed(hub, asks_eid, asks_props)
    checks_eid, checks_props = _unseen("integ-bad-checks")
    checks_props["pending"]["checks"] = 5
    _pushed(hub, checks_eid, checks_props)
    step_eid = _waiting_run(hub, "integ-bad-step")
    _patch_step(hub, step_eid, "x")
    bad = [asks_eid, checks_eid, step_eid]

    # 승인함(웹 프록시 runs-waiting): 일곱 실행이 모두 보이고 잘못된 값은 안전한 값으로 읽힌다.
    listed = hub.proxy("runs-waiting", {})
    assert listed.get("executed") is True, listed
    items = {item["id"]: item for item in listed["runs"]}
    assert set(good + bad) <= set(items), sorted(items)
    assert items[asks_eid]["ask"] == 1 and items[step_eid]["step"] == 0
    assert items[checks_eid]["decision"]["checks"] == []
    for eid in good + bad:
        assert items[eid]["decision"]["actions"] == ["merge"], items[eid]
        assert items[eid]["decision"]["question"], items[eid]

    # vinc_runs: 목록과 실행 하나 읽기가 모두 된다.
    runs = hub.tool("vinc_runs")
    assert runs.get("executed") is True, runs
    assert set(good + bad) <= {r["id"] for r in runs["runs"]}
    for eid in bad:
        one = hub.tool("vinc_runs", run=eid)
        assert [r["id"] for r in one["runs"]] == [eid], one
        assert one["runs"][0]["props"]["state"] == "waiting"

    # 호스트가 실행을 다시 열어도 대기 보기가 나온다(-32602 가 아니다).
    for eid, run_id in ((asks_eid, "integ-bad-asks"), (checks_eid, "integ-bad-checks"),
                        (step_eid, "integ-bad-step")):
        view = _tool(hub, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
        assert view.get("executed") is True and view["state"] == "waiting", view
        assert view["decision"]["kind"] == "person_only"

    # 알림 본문: 저장된 그대로의 잘못된 props 로도 만들어진다.
    for eid in good + bad:
        raw = hub.tool("vinc_node", node_id=eid, raw=True)["node"]["props"]
        body = run_notify.payload(OWNER, eid, raw)
        assert body is not None and body["run"] == eid and body["ask"] >= 1, body

    # 이벤트 피드: 대기에 든 실행 일곱이 모두 나온다.
    feed = hub.tool("vinc_events", since=cursor, kinds=["run.waiting"])
    assert feed.get("executed") is True, feed
    assert set(good + bad) <= {e["data"]["run"] for e in feed["events"]}, feed["events"]


def test_a_person_can_close_a_malformed_waiting_run_from_the_approvals_list(hub):
    eid, props = _unseen("integ-close-bad", asks="x")
    props["pending"]["checks"] = "not a list"
    _pushed(hub, eid, props)
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    out = hub.proxy("run-answer", {"run": eid, "ask": item["ask"], "answer": "decline"},
                    auth="session")
    assert out.get("executed") is True and out["state"] == "done", out
    assert out["outcome"] == "blocked"
    assert eid not in hub.waiting_ids()
    assert hub.stored(eid)["approved"] == []


# ── 2. 동기화로 온 질문은 프로토콜의 시간 상한을 넘겨 열려 있지 않다 ─────────

@pytest.mark.parametrize("expires", ["2999-01-01T00:00:00Z", "never", None])
def test_a_synced_question_expires_within_the_protocol_limit_of_the_hub_clock(hub, expires):
    run_id = f"integ-expiry-{expires}"
    eid, props = _unseen(run_id)
    props["pending"]["asked_at"] = "2999-01-01T00:00:00Z"
    if expires is not None:
        props["pending"]["expires_at"] = expires
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    _pushed(hub, eid, props)
    after = datetime.datetime.now(datetime.timezone.utc)
    pending = hub.stored(eid)["pending"]
    asked, until = _parse(pending["asked_at"]), _parse(pending["expires_at"])
    assert before <= asked <= after, pending
    limit = datetime.timedelta(seconds=run_protocol.EXPIRES_MAX_S)
    assert before + limit <= until <= after + limit, pending
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    assert item["expires_at"] == pending["expires_at"] and item["expired"] is False


def test_a_synced_question_that_expires_sooner_keeps_its_expiry(hub):
    eid, props = _unseen("integ-expiry-sooner")
    soon = (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=5)).strftime(_FMT)
    props["pending"]["expires_at"] = soon
    _pushed(hub, eid, props)
    assert hub.stored(eid)["pending"]["expires_at"] == soon


# ── 3. 무엇을 묻는지 없는 대기도 사람이 보고 닫을 수 있다 ────────────────────

@pytest.mark.parametrize("pending", [None, 5, "merge", ["merge"]])
def test_a_synced_waiting_run_without_a_pending_dict_reaches_the_approvals_list(hub, pending):
    run_id = f"integ-nopending-{type(pending).__name__}"
    eid, props = _unseen(run_id)
    if pending is None:
        props.pop("pending")
    else:
        props["pending"] = pending
    _pushed(hub, eid, props)
    stored = hub.stored(eid)
    assert stored["state"] == "waiting" and stored["approved"] == []
    assert stored["pending"]["actions"] == [] and stored["pending"]["kind"] == "person_only"
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    assert item["decision"]["actions"] == [] and item["decision"]["question"], item
    # 에이전트는 답하지 못한다. 사람이 승인해도 승인되는 행동이 없다.
    agent = hub.tool("vinc_run_submit", run=eid, answer="approve")
    assert agent["executed"] is False and agent["error"] == "person_answer_required", agent
    out = hub.proxy("run-answer", {"run": eid, "ask": item["ask"], "answer": "approve"},
                    auth="session")
    assert out.get("executed") is True, out
    assert hub.stored(eid)["approved"] == []


# ── 4. 질문 문장이 없는 대기는 저장된 행동과 검사로 질문을 만든다 ─────────────

def test_a_check_only_question_from_a_device_reads_as_a_question_on_every_surface(hub):
    eid, props = _unseen("integ-check-only")
    props["pending"] = {"actions": [], "checks": ["person_only"], "kind": "person_only",
                        "asked_at": _stamp(0), "question": "Approve to merge PR 12.",
                        "requests": []}
    _pushed(hub, eid, props)
    expected = "A gate asks a person to decide: person_only."
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    assert item["decision"]["question"] == expected, item
    assert item["decision"]["checks"] == ["person_only"]
    view = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                    run_id="integ-check-only")
    assert view["question"] == expected and view["decision"]["question"] == expected, view
    assert "PR 12" not in json.dumps(item) and "PR 12" not in json.dumps(view)


def test_a_question_built_on_the_hub_names_the_stored_actions_not_the_sender_text(hub):
    eid, props = _unseen("integ-question-actions")
    props["pending"]["question"] = "May the agent read the docs?"
    _pushed(hub, eid, props)
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    assert item["decision"]["question"] == "A person decides before the role goes on: merge."
    view = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                    run_id="integ-question-actions")
    assert view["question"].startswith("A person decides before the role goes on: merge")


# ── 5. 상태가 없는 실행 기록은 프로토콜 오류로 답한다 ────────────────────────

def test_start_and_submit_on_a_run_record_without_state_refuse_with_invalid_run_state(hub):
    run_id = "integ-stateless"
    eid = run_protocol.episode_id(run_id)
    props = {"run_id": run_id, "role": ROLE, "task": "Review the change.", "outcome": "waiting",
             "step": 1, "host": "device-cli 1.0"}
    _pushed(hub, eid, props)
    assert "state" not in hub.stored(eid)
    start = _tool(hub, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    assert start["executed"] is False and start["error"] == "invalid_run_state", start
    for args in ({"output": PLAIN}, {"answer": "approve"}):
        out = _tool(hub, "vinc_run_submit", run=eid, **args)
        assert out["executed"] is False and out["error"] == "invalid_run_state", out
    assert "state" not in hub.stored(eid)
    assert eid not in hub.waiting_ids()
