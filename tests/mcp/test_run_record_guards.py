# -*- coding: utf-8 -*-
"""실행 기록을 프로토콜 밖에서 지우거나, 통째로 바꾸거나, 다른 역할로 돌리는 길을 막는다.

실행 기록(`episode:run-*`)은 사람에게 묻는 질문과 사람의 답을 싣는다. 입구마다 대기 중인 실행과
끝난 실행을 써 보고, 저장된 행·승인함·호스트가 받는 결과를 함께 단정한다.

- 삭제: 대기 중인 실행은 동기화 업로드의 삭제 표식으로도, `vinc_delete`(MCP·REST·웹 프록시)로도
  지워지지 않는다. 끝난 실행과 만료된 질문은 둘 다 지운다.
- 통째의 props: 프로토콜이 가진 실행 기록에 props 전체를 쓰는 쓰기는 거절되고 `props_patch` 를
  말한다. 프로토콜이 갖지 않은 실행 기록(클라이언트가 쓰는 기록)은 그대로 쓴다.
- 역할: `role`·`role_hash` 는 `props_patch` 로 못 바꾸고, 동기화 업로드는 허브의 값을 남긴다. 역할
  계약이 실행 중에 바뀌면 그 단계는 `role_changed` 로 끝나고 아무것도 승인되지 않는다.
- 결과 상한: run_id·task 가 긴 실행에서도 승인의 답이 상한 안에 들고, 저장된 승인을 말한다.
- 이벤트: 역할이 없는 대기도 `run.waiting` 으로 나간다.

스택은 `test_run_state_entrances.py` 의 것이다(임베디드 스토어 위 사이드카 ← 브리지 ← 공개 허브).
"""
from __future__ import annotations

import datetime
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import run_state  # noqa: E402
from vinc_mcp import run_protocol, tools  # noqa: E402
from test_run_state_entrances import (  # noqa: E402,F401
    KEY, OWNER, PROFILES, ROLE, Hub, _episode, _push, _stamp, _waiting_run, _withheld, hub)
from test_run_record_integrity import _pushed, _unseen  # noqa: E402

LAX = "concept:agent-guard-lax"
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _rest(hub: Hub, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(hub.base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read() or b"{}")


def _mcp(hub: Hub, name: str, **arguments) -> tuple[dict | None, dict]:
    """도구 호출 한 번. (결과 JSON, 원래 결과). 결과 상한에 걸렸으면 결과 JSON 은 None."""
    status, reply = hub.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}})
    assert status == 200 and "result" in reply, reply
    result = reply["result"]
    if (result.get("_meta") or {}).get("vinc_too_large"):
        return None, result
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"], result
    return json.loads(result["content"][0]["text"]), result


def _patch(hub: Hub, eid: str, **fields) -> dict:
    """`vinc_ingest` 의 `props_patch` 한 번(도구 함수로 불러 결과 상한을 지나지 않는다). 결과를 돌려준다."""
    node = tools.call("vinc_node", {"node_id": eid, "raw": True}, hub.bridge,
                      forced_owner=OWNER)["node"]
    return tools.call("vinc_ingest", {"nodes": [{
        "id": eid, "type": "episode", "title": node["title"], "props_patch": fields,
        "expected_rev": node["rev"]}]}, hub.bridge, forced_owner=OWNER)


def _role(hub: Hub, role: str, **changes) -> None:
    """역할 노드를 통째로 쓴다(fixture 와 같은 모양에 `changes` 를 덮는다)."""
    props = dict(PROFILES[ROLE])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [], "reads": [],
                  "act_policy": {"notify": "ask_first"}, **changes})
    out = tools.call("vinc_ingest", {"nodes": [{"id": role, "type": "concept", "title": "Agent role",
                                                "domain": "vinc/agent/roles", "props": props}]},
                     hub.bridge, forced_owner=OWNER)
    assert out.get("executed"), out


def _finished_run(hub: Hub, run_id: str) -> str:
    """사람이 거절해 끝난 실행 하나."""
    eid = _waiting_run(hub, run_id)
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "decline"}, auth="session")
    assert out.get("executed") is True and out["state"] == "done", out
    return eid


def _gone(hub: Hub, eid: str) -> bool:
    got = hub.bridge.request("GET", "/runs", params={"owner": OWNER, "run": eid, "limit": 1,
                                                      "stored": "1"})
    assert got.get("executed"), got
    return got["runs"] == []


def _upload(hub: Hub, *, nodes=(), tombstones=()) -> tuple[int, dict]:
    """기기의 동기화 업로드 한 번(`/sync/push`). 삭제 표식은 지금보다 조금 뒤에 찍는다."""
    bundle = {"nodes": [{**n, "_rev": 1, "_updated_at": _stamp()} for n in nodes], "edges": [],
              "tombstones": [{"node_id": t, "label": "Episode", "deleted_at": _stamp()}
                             for t in tombstones]}
    return hub.post("/sync/push", {"bundle": bundle})


def _ago(seconds: int) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return moment.strftime(_FMT)


# ── 1. 대기 중인 실행은 지워지지 않는다 ───────────────────────────────────────

def test_a_sync_upload_delete_marker_leaves_a_waiting_run_on_the_approvals_list(hub):
    eid = _waiting_run(hub, "guard-tomb")
    before = hub.stored(eid)
    status, out = _upload(hub, tombstones=[eid])
    assert status == 200 and out["executed"] is True and out["deleted"] == 0, out
    _withheld(out, eid)
    assert hub.stored(eid) == before and eid in hub.waiting_ids()


def test_a_delete_marker_sent_with_the_run_itself_leaves_it_waiting(hub):
    eid = _waiting_run(hub, "guard-tomb-recreate")
    before = hub.stored(eid)
    status, out = _upload(hub, nodes=[_episode(eid, before)], tombstones=[eid])
    assert status == 200 and out["executed"] is True and out["deleted"] == 0, out
    _withheld(out, eid)
    assert hub.stored(eid)["state"] == "waiting" and eid in hub.waiting_ids()
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is True and hub.stored(eid)["approved"] == ["merge"], out


@pytest.mark.parametrize("entrance", ["mcp", "rest", "proxy"])
def test_a_tool_delete_refuses_a_waiting_run_and_says_how_to_close_it(hub, entrance):
    eid = _waiting_run(hub, f"guard-delete-{entrance}")
    before = hub.stored(eid)
    if entrance == "mcp":
        out = hub.tool("vinc_delete", node_id=eid)
        assert out["executed"] is False and out["error"] == run_state.CODE, out
        assert "decline" in out["note"].lower() and "nothing was deleted" in out["note"].lower()
    elif entrance == "rest":
        status, out = _rest(hub, "DELETE", "/v1/nodes/" + eid)
        assert status == 403 and out["error"]["code"] == run_state.CODE, out
    else:
        out = hub.proxy("delete", {"node": eid})
        assert out["executed"] is False and out["error"] == run_state.CODE, out
    assert hub.stored(eid) == before and eid in hub.waiting_ids()


def test_a_finished_run_is_deleted_by_its_owner_and_by_a_sync_delete_marker(hub):
    by_tool = _finished_run(hub, "guard-done-tool")
    out = hub.tool("vinc_delete", node_id=by_tool)
    assert out["executed"] is True and out["deleted"]["node"] == 1, out
    assert _gone(hub, by_tool)
    by_sync = _finished_run(hub, "guard-done-sync")
    status, out = _upload(hub, tombstones=[by_sync])
    assert status == 200 and out["deleted"] == 1 and run_state.CODE not in out, out
    assert _gone(hub, by_sync)


def test_a_question_that_expired_can_be_deleted(hub):
    """만료된 질문에는 누구도 답할 수 없다. 읽는 쪽이 끝난 실행으로 읽는 기록은 지울 수 있다."""
    eid, props = _unseen("guard-expired")
    props["pending"].update(asked_at=_ago(7200), expires_at=_ago(3600))
    _pushed(hub, eid, props)
    assert hub.stored(eid)["state"] == "waiting"
    out = hub.tool("vinc_delete", node_id=eid)
    assert out["executed"] is True and out["deleted"]["node"] == 1, out
    assert _gone(hub, eid)


# ── 2. 프로토콜이 가진 실행 기록에 props 를 통째로 쓰지 못한다 ──────────────────

_WHOLE = {"object": {"host": "x"}, "json-text": json.dumps({"host": "x"}), "list": ["x"],
          "number": 5, "empty": {}}


@pytest.mark.parametrize("form", list(_WHOLE))
@pytest.mark.parametrize("state", ["waiting", "done"])
def test_whole_props_on_a_run_the_protocol_keeps_are_refused_and_name_props_patch(hub, state, form):
    run_id = f"guard-whole-{state}-{form}"
    eid = _waiting_run(hub, run_id) if state == "waiting" else _finished_run(hub, run_id)
    before = hub.stored(eid)
    out = hub.tool("vinc_ingest", nodes=[{"id": eid, "type": "episode", "title": "Run",
                                          "props": _WHOLE[form]}])
    assert out["executed"] is False and out["error"] == run_state.CODE, out
    assert "props_patch" in out["note"], out
    assert hub.stored(eid) == before
    assert (eid in hub.waiting_ids()) is (state == "waiting")
    view = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    assert view["executed"] is True and view["state"] == state, view


@pytest.mark.parametrize("entrance", ["rest", "proxy"])
def test_whole_props_through_rest_and_the_web_proxy_are_refused(hub, entrance):
    eid = _waiting_run(hub, f"guard-whole-{entrance}")
    before = hub.stored(eid)
    node = {"id": eid, "type": "episode", "title": "Run", "props": {"host": "x"}}
    if entrance == "rest":
        status, out = _rest(hub, "POST", "/v1/fragments", {"nodes": [node]})
        assert status == 403 and out["error"]["code"] == run_state.CODE, out
        assert "props_patch" in out["error"]["message"], out
    else:
        out = hub.proxy("ingest-fragment", {"nodes": [node], "edges": []})
        assert out["executed"] is False and out["error"] == run_state.CODE, out
    assert hub.stored(eid) == before and eid in hub.waiting_ids()


def test_props_patch_and_records_the_protocol_does_not_keep_are_still_written(hub):
    eid = _waiting_run(hub, "guard-patch-host")
    out = _patch(hub, eid, host="device-cli 2.0")
    assert out.get("executed") is True and out.get("error") is None, out
    stored = hub.stored(eid)
    assert stored["host"] == "device-cli 2.0" and stored["state"] == "waiting"
    # 클라이언트의 실행 기록(`vinc_client.record_run` 의 모양)은 프로토콜 상태가 없다. 다시 쓰면 통째로 바뀐다.
    sdk = run_protocol.episode_id("guard-sdk-run")
    for outcome in ("success", "failure"):
        props = {"run_id": "guard-sdk-run", "role": ROLE, "outcome": outcome, "steps": 3}
        out = hub.tool("vinc_ingest", nodes=[{"id": sdk, "type": "episode", "title": "Run",
                                              "props": props}])
        assert out["executed"] is True and out.get("error") is None, out
        assert hub.stored(sdk) == props


# ── 3. 실행의 역할은 프로토콜 밖에서 바뀌지 않는다 ─────────────────────────────

@pytest.mark.parametrize("patch", [{"role": LAX}, {"role": None}, {"role_hash": "0" * 64},
                                   {"role_hash": None}], ids=["role", "role-null", "hash",
                                                              "hash-null"])
def test_a_props_patch_cannot_change_a_runs_role(hub, patch):
    _role(hub, LAX, person_only=[], never_checks=[])
    run_id = "guard-role-patch-" + "-".join(f"{k}-{v is None}" for k, v in patch.items())
    run = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    before = hub.stored(run["run"])
    out = _patch(hub, run["run"], **patch)
    assert out["executed"] is False and out["error"] == run_state.CODE, out
    assert hub.stored(run["run"]) == before
    # 실행을 연 역할의 정책 그대로다: merge 는 사람만 하는 행동으로 묻는다.
    out = hub.tool("vinc_run_submit", run=run["run"], output="Ready to merge once approved.",
                   person_only_requests=[{"action": "merge", "reason": "run merge for PR 12"}])
    assert out["state"] == "waiting" and out["decision"]["kind"] == "person_only", out


def test_a_sync_upload_keeps_the_role_of_a_run_the_hub_holds(hub):
    _role(hub, LAX, person_only=[], never_checks=[])
    eid = _waiting_run(hub, "guard-role-sync")
    before = hub.stored(eid)
    status, out = _push(hub, _episode(eid, {**before, "role": LAX, "role_hash": "f" * 64}))
    assert status == 200 and out["executed"] is True, out
    _withheld(out, eid)
    after = hub.stored(eid)
    assert after["role"] == ROLE and after["role_hash"] == before["role_hash"], after
    out = hub.proxy("run-answer", {"run": eid, "ask": 1, "answer": "approve"}, auth="session")
    assert out.get("executed") is True and hub.stored(eid)["approved"] == ["merge"], out


def test_a_role_contract_edited_mid_run_ends_the_step_and_approves_nothing(hub):
    working = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                       run_id="guard-edit-working")["run"]
    waiting = _waiting_run(hub, "guard-edit-waiting")
    working_before, waiting_before = hub.stored(working), hub.stored(waiting)
    _role(hub, ROLE, person_only=[], never_checks=[])

    # 에이전트의 단계는 끝나고 아무것도 저장되지 않는다. 다시 열어도 같다.
    out, _ = _mcp(hub, "vinc_run_submit", run=working, output="Ready to merge once approved.",
                  person_only_requests=[{"action": "merge", "reason": "run merge for PR 12"}])
    assert out["executed"] is False and out["error"] == "role_changed", out
    assert "new run" in out["note"], out
    resumed, _ = _mcp(hub, "vinc_run_start", role=ROLE, task="Review the change.",
                      run_id="guard-edit-working")
    assert resumed["executed"] is False and resumed["error"] == "role_changed", resumed
    assert hub.stored(working) == working_before

    # 사람의 승인도 아무것도 승인하지 않는다. 거절은 그 실행을 닫는다.
    out = hub.proxy("run-answer", {"run": waiting, "ask": 1, "answer": "approve"}, auth="session")
    assert out["executed"] is False and out["error"] == "role_changed", out
    assert hub.stored(waiting) == waiting_before and waiting in hub.waiting_ids()
    closed = hub.proxy("run-answer", {"run": waiting, "ask": 1, "answer": "decline"},
                       auth="session")
    assert closed.get("executed") is True and closed["state"] == "done", closed

    # 바뀐 계약으로 여는 새 실행은 그 계약을 따른다.
    fresh = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                     run_id="guard-edit-new")
    assert fresh["executed"] is True and fresh["policy"]["person_only"] == [], fresh


# ── 5. 긴 run_id·task 가 있어도 승인의 답은 결과 상한 안에 든다 ─────────────────

@pytest.mark.parametrize("key", ["run_id", "task"])
def test_an_approve_on_a_run_with_a_long_run_id_or_task_answers_within_the_cap(hub, key):
    run_id = f"guard-long-{key}"
    eid = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)["run"]
    out = hub.tool("vinc_run_submit", run=eid, output="Ready to notify the team once approved.",
                   person_only_requests=[{"action": "notify", "reason": "tell the team"}])
    assert out["state"] == "waiting" and out["decision"]["kind"] == "ask_first", out
    patched = _patch(hub, eid, **{key: "A" * 100_000})
    assert patched.get("executed") is True and patched.get("error") is None, patched
    got, raw = _mcp(hub, "vinc_run_submit", run=eid, answer="approve")
    assert got is not None, raw
    assert got["executed"] is True and got["state"] == "working", got
    assert got["approved"] == ["notify"] and got["run"] == eid, got
    assert got["run_id"] == (eid if key == "run_id" else run_id), got["run_id"][:200]
    assert len(got["task"]) == (run_protocol.TASK_MAX if key == "task" else len("Review the change."))
    assert hub.stored(eid)["approved"] == ["notify"]


# ── 6. 역할이 없는 대기도 이벤트 피드에 나온다 ─────────────────────────────────

@pytest.mark.parametrize("role", ["missing", "null"])
def test_a_waiting_run_without_a_role_is_on_the_events_feed(hub, role):
    cursor = hub.tool("vinc_events", kinds=["run.waiting"])["cursor"]
    eid, props = _unseen(f"guard-events-{role}")
    if role == "missing":
        props.pop("role")
    else:
        props["role"] = None
    _pushed(hub, eid, props)
    assert eid in hub.waiting_ids()
    feed = hub.tool("vinc_events", since=cursor, kinds=["run.waiting"])
    found = [e for e in feed["events"] if e["data"]["run"] == eid]
    assert len(found) == 1, feed
    data = found[0]["data"]
    assert data["role"] == "" and data["ask"] == 1, data
    assert data["title"] == f"Run {eid}: waiting", data
