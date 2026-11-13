# -*- coding: utf-8 -*-
"""잘못된 모양의 실행 기록이 승인함과 실행 읽기를 멈추지 않는다.

실행 기록(`episode:run-*`)의 props 는 동기화 업로드와 `vinc_ingest` 의 `props_patch` 로도 들어온다.
그 값이 수가 아니거나 목록이 아니어도(예: `asks: "x"`, `step: "x"`, `pending.checks: 5`) 읽는 쪽은
예외로 멈추지 않고, 같은 소유자의 다른 대기는 그대로 보이며, 잘못된 기록 자신도 사람이 보고 닫을
수 있게 보인다. 읽는 쪽은 모두 `store/run_state.py` 의 `readable` 한 벌을 지난다.

같은 스택(임베디드 스토어 위의 사이드카 ← 브리지 ← 공개 허브)으로 동기화가 받는 질문의 시간 상한,
무엇을 묻는지 없는 대기, 질문 문장이 없는 대기, 상태가 없는 실행 기록도 본다.

역할이나 run_id 를 읽을 수 없는 대기도 사람의 거절이 닫는다. 그런 대기의 승인은 견줄 정책이 없어
프로토콜 오류로 거절되고 아무것도 승인하지 않는다. outcome 이 없거나 사용량이 유한한 수가 아니어도
사람의 승인은 결과로 돌아오고, 승인이 저장된 뒤에는 예외가 나지 않는다. 모든 이름과 모양의 조합은
`test_run_record_property.py` 가 본다.
"""
from __future__ import annotations

import datetime
import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import run_state  # noqa: E402
from vinc_mcp import modern, run_notify, run_protocol, run_tasks, tools  # noqa: E402
from test_run_state_entrances import (  # noqa: E402,F401
    KEY, OWNER, PLAIN, PROFILES, ROLE, Hub, _episode, _push, _stamp, _waiting_run, hub)

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


# ── 6. 역할이나 run_id 를 읽을 수 없는 대기도 사람이 거절해 닫는다 ─────────────

_DROP = object()
#: (쓰는 길, 키, 값). unseen 은 허브가 처음 보는 실행의 동기화 업로드, patch 는 대기 중인 실행에
#: `vinc_ingest` 의 `props_patch`. 역할 값은 역할 하나를 가리키지 않는 모양을 하나씩 든다. 가진 실행의
#: 역할은 `props_patch` 가 거절하고 동기화가 남기므로(`run_state.ROLE_KEYS`) 그 모양은 처음 보는 실행으로만 온다.
_NO_ROLE_OR_RUN_ID = {
    "unseen-no-run_id": ("unseen", "run_id", _DROP),
    "unseen-no-role": ("unseen", "role", _DROP),
    "patch-run_id-null": ("patch", "run_id", None),
    "unseen-role-null": ("unseen", "role", None),
    "unseen-role-text": ("unseen", "role", "x"),
    "unseen-role-number": ("unseen", "role", 5),
    "unseen-role-dict": ("unseen", "role", {"a": 1}),
    "unseen-role-list": ("unseen", "role", ["x"]),
    "unseen-role-unknown": ("unseen", "role", "concept:agent-nobody"),
}
TASKS = {"extensions": {run_tasks.EXTENSION: {}}}


def _patch(hub: Hub, eid: str, **fields) -> None:
    """`vinc_ingest` 의 `props_patch` 로 보호되지 않는 키를 바꾼다."""
    node = hub.tool("vinc_node", node_id=eid, raw=True)["node"]
    out = hub.tool("vinc_ingest", nodes=[{"id": eid, "type": "episode", "title": node["title"],
                                          "props_patch": fields, "expected_rev": node["rev"]}])
    assert out.get("executed") is True and out.get("error") is None, out


def _malformed(hub: Hub, case: str, run_id: str) -> str:
    """`_NO_ROLE_OR_RUN_ID` 의 기록 하나를 만든다. 대기 중이고 승인함에 보인다."""
    how, key, value = _NO_ROLE_OR_RUN_ID[case]
    if how == "unseen":
        eid, props = _unseen(run_id)
        if value is _DROP:
            props.pop(key)
        else:
            props[key] = value
        _pushed(hub, eid, props)
    else:
        eid = _waiting_run(hub, run_id)
        _patch(hub, eid, **{key: value})
    assert hub.stored(eid)["state"] == "waiting"
    return eid


def _role_unreadable(case: str) -> bool:
    return _NO_ROLE_OR_RUN_ID[case][1] == "role"


def _task_rpc(hub: Hub, method: str, eid: str, ask: int) -> dict:
    """`/mcp` 의 Tasks 메서드 한 번(모던 세대, Tasks 확장을 선언한 클라이언트). JSON-RPC 응답 그대로."""
    version = modern.MODERN_VERSIONS[0]
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {
        "taskId": run_tasks.task_id(eid, ask),
        "_meta": {modern.META_VERSION: version, modern.META_CAPABILITIES: TASKS}}}
    req = urllib.request.Request(hub.base + "/mcp", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY,
                                          **modern.request_headers(body)})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


@pytest.mark.parametrize("case", list(_NO_ROLE_OR_RUN_ID))
def test_a_person_declines_a_waiting_run_whose_role_or_run_id_cannot_be_read(hub, case):
    eid = _malformed(hub, case, f"integ-decline-{case}")
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    out = hub.proxy("run-answer", {"run": eid, "ask": item["ask"], "answer": "decline"},
                    auth="session")
    assert out.get("executed") is True and out["state"] == "done", out
    assert out["outcome"] == "blocked" and out["run_id"], out
    stored = hub.stored(eid)
    assert stored["state"] == "done" and stored["approved"] == [], stored
    assert eid not in hub.waiting_ids()


def test_a_person_declines_a_run_whose_run_id_is_too_long_for_a_title(hub):
    """제목은 run_id 모양이 아닌 값을 싣지 않는다. 너무 긴 제목의 노드는 저장이 건너뛴다."""
    eid, props = _unseen("integ-long-run-id")
    props["run_id"] = "A" * 100_000
    _pushed(hub, eid, props)
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "decline"}, auth="session")
    assert out.get("executed") is True and out["state"] == "done", out
    assert hub.stored(eid)["state"] == "done" and eid not in hub.waiting_ids()


def test_a_save_the_store_skipped_is_reported_as_not_saved():
    """저장이 노드를 건너뛰었다는 경고는 성공이 아니다: 사람은 반영되지 않은 답을 보지 않는다."""
    eid = run_protocol.episode_id("integ-skipped")

    class _Skips:
        def request(self, method, path, params=None, body=None, timeout=60):
            return {"executed": True, "warnings": [
                f"node.props too large — skipped (id={eid}, >512KB serialized)"]}

    props = {"run_id": "integ-skipped", "role": ROLE, "state": "done", "outcome": "blocked"}
    out = run_protocol._saved(_Skips(), OWNER, eid, props)
    assert out["executed"] is False and out["error"] == "run_not_saved", out
    assert "skipped" in out["detail"]


@pytest.mark.parametrize("case", list(_NO_ROLE_OR_RUN_ID))
def test_a_person_approve_needs_a_role_the_graph_has_and_otherwise_approves_nothing(hub, case):
    eid = _malformed(hub, case, f"integ-approve-{case}")
    item = next(i for i in hub.proxy("runs-waiting", {})["runs"] if i["id"] == eid)
    out = hub.proxy("run-answer", {"run": eid, "ask": item["ask"], "answer": "approve"},
                    auth="session")
    stored = hub.stored(eid)
    if not _role_unreadable(case):
        assert out.get("executed") is True and out["state"] == "working", out
        assert stored["approved"] == item["decision"]["actions"] == ["merge"]
        return
    # 역할이 없으면 승인을 견줄 정책이 없다: 거절되고 아무것도 승인되지 않으며 카드는 남는다.
    assert out.get("executed") is False and out["error"] == "invalid_run_state", out
    assert "decline" in out["note"].lower(), out
    assert stored["state"] == "waiting" and stored.get("approved") == [], stored
    assert "answer" not in stored and eid in hub.waiting_ids()
    closed = hub.proxy("run-answer", {"run": eid, "ask": item["ask"], "answer": "decline"},
                       auth="session")
    assert closed.get("executed") is True and closed["state"] == "done", closed


@pytest.mark.parametrize("case", list(_NO_ROLE_OR_RUN_ID))
def test_the_agent_tools_and_tasks_answer_a_protocol_result_on_such_a_run(hub, case):
    run_id = f"integ-agent-{case}"
    eid = _malformed(hub, case, run_id)
    start = _tool(hub, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    if _role_unreadable(case):
        # 역할 id 모양의 다른 역할이면 그 run_id 는 다른 역할의 것이다.
        error = "run_id_in_use" if case == "unseen-role-unknown" else "invalid_run_state"
        assert start["executed"] is False and start["error"] == error, start
    else:
        assert start["executed"] is True and start["state"] == "waiting", start
        assert start["run_id"], start
    for args in ({"output": PLAIN}, {"answer": "approve"}):
        out = _tool(hub, "vinc_run_submit", run=eid, **args)
        assert out["executed"] is False and out["error"], out
    for method in ("tasks/get", "tasks/cancel"):
        reply = _task_rpc(hub, method, eid, 1)
        assert "result" in reply, reply
    stored = hub.stored(eid)
    assert stored["state"] == "waiting" and stored["approved"] == [] and "answer" not in stored
    assert eid in hub.waiting_ids()


# ── 7. outcome 이 없는 대기는 열린 실행의 outcome 으로 읽는다 ──────────────────

@pytest.mark.parametrize("how", ["unseen", "patch"])
def test_a_waiting_run_without_an_outcome_is_approved_and_resumed(hub, how):
    run_id = f"integ-no-outcome-{how}"
    if how == "unseen":
        eid, props = _unseen(run_id)
        props.pop("outcome")
        _pushed(hub, eid, props)
    else:
        eid = _waiting_run(hub, run_id)
        _patch(hub, eid, outcome=None)
    start = _tool(hub, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    assert start["executed"] is True and start["outcome"] == run_protocol.OPEN_OUTCOME, start
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is True and out["outcome"] == run_protocol.OPEN_OUTCOME, out
    assert hub.stored(eid)["approved"] == ["merge"]
    resumed = _tool(hub, "vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    assert resumed["executed"] is True and resumed["state"] == "working", resumed
    done = _tool(hub, "vinc_run_submit", run=eid, output=PLAIN)
    assert done["executed"] is True and done["state"] == "done", done


# ── 8. 수가 아닌 사용량(NaN·무한)은 없는 것으로 읽고, 저장된 승인은 결과로 돌아온다 ──

BUDGET_ROLE = "concept:agent-integ-budget"


def _role(hub: Hub, role: str, **extra) -> None:
    props = dict(PROFILES[ROLE])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [], "reads": [],
                  "act_policy": {"notify": "ask_first"}, **extra})
    out = tools.call("vinc_ingest", {"nodes": [{"id": role, "type": "concept",
                                                "title": "Agent role", "domain": "vinc/agent/roles",
                                                "props": props}]}, hub.bridge, forced_owner=OWNER)
    assert out.get("executed"), out


def _held_wait(hub: Hub, role: str, run_id: str) -> str:
    run = hub.tool("vinc_run_start", role=role, task="Review the change.", run_id=run_id)
    out = hub.tool("vinc_run_submit", run=run["run"], output="Ready to merge once approved.",
                   person_only_requests=[{"action": "merge", "reason": "run merge for PR 12"}])
    assert out["state"] == "waiting", out
    return run["run"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")], ids=repr)
def test_a_non_finite_usage_reads_as_none_and_a_person_approval_returns_its_result(hub, value):
    _role(hub, BUDGET_ROLE, budget={"tool_calls": 10, "model_calls": 80})
    label = repr(value).strip("-")
    working = _tool(hub, "vinc_run_start", role=BUDGET_ROLE, task="t", run_id=f"integ-used-{label}")
    _patch(hub, working["run"], used={"tool_calls": value})
    resumed = _tool(hub, "vinc_run_start", role=BUDGET_ROLE, task="t", run_id=f"integ-used-{label}")
    assert resumed["executed"] is True and resumed["budget_left"]["tool_calls"] == 10, resumed

    eid = _held_wait(hub, BUDGET_ROLE, f"integ-used-wait-{label}")
    _patch(hub, eid, used={"tool_calls": value})
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is True and out["state"] == "working", out
    assert hub.stored(eid)["approved"] == ["merge"]


def test_an_answer_whose_view_cannot_be_built_stores_nothing(hub, monkeypatch):
    """승인이 저장된 뒤에는 예외가 나지 않는다: 보기를 먼저 만들고 그다음 저장한다."""
    eid = _waiting_run(hub, "integ-view-first")

    def broken(*args, **kwargs):
        raise RuntimeError("view")

    monkeypatch.setattr(run_protocol, "_budget_left", broken)
    with pytest.raises(RuntimeError):
        hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    stored = hub.stored(eid)
    assert stored["state"] == "waiting" and stored["approved"] == [] and "answer" not in stored
    monkeypatch.undo()
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is True and hub.stored(eid)["approved"] == ["merge"], out


# ── 9. 상태가 없는 기록에도 지난 카드와 tasks/get 은 결과로 답한다 ────────────

def test_a_stale_card_and_tasks_get_on_a_record_without_state_answer_a_result(hub):
    eid, props = _unseen("integ-stateless-card")
    props.pop("state")
    _pushed(hub, eid, props)
    assert "state" not in hub.stored(eid)
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is False and out["error"] == "already_answered", out
    reply = _task_rpc(hub, "tasks/get", eid, 1)
    assert "result" in reply, reply
    assert hub.stored(eid).get("approved") == []


# ── 10. 능력 표지의 id 가 문자열이 아니어도 다음 제출은 결과로 답한다 ──────────

def test_capability_flags_with_an_unhashable_id_leave_the_next_submit_a_result(hub):
    role = "concept:agent-integ-caps"
    _role(hub, role, capabilities={"deny": ["github.repo.delete"]})
    run = _tool(hub, "vinc_run_start", role=role, task="t", run_id="integ-caps")
    _patch(hub, run["run"], capability_flags=[{"id": ["x"], "flag": "denied"},
                                              {"id": {"a": 1}, "flag": {"b": 2}}, "x"])
    out = _tool(hub, "vinc_run_submit", run=run["run"], output=PLAIN,
                capabilities_used=["github.repo.delete"])
    assert out["executed"] is True and out["status"] == "blocked", out
    assert out["capability_flags"][-1]["id"] == "github.repo.delete", out
