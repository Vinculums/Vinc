# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)의 보호되는 키 하나하나를 전권 키가 닿는 쓰기 입구마다 꾸며 보내고,
허브가 지금 무엇을 하는지 표로 적는다.

이웃 테스트(test_run_state_entrances.py)는 꾸민 승인 한 벌을 입구마다 보낸다. 여기서는 키 하나씩 보낸다:
`store/run_state.py` 의 `STATE_KEYS`(질문 쪽과 답 쪽), `ROLE_KEYS`, 사람이 읽는 카드(`CARD_KEYS` 와 제목).
입구는 MCP `vinc_ingest`, REST `POST /v1/fragments`, 웹 프록시 `ingest-fragment`, 기기 동기화
`POST /sync/push` 이고 모두 전권 키로 부른다. 쓰는 모양은 둘이다: 키 하나만 실은 `props` 와 `props_patch`.
동기화는 기기가 들고 있던 저장된 행에서 키 하나만 바꿔 보낸다.

각 칸은 입구가 무엇이라 답했는가를 적고(`refused by <규칙>`·`kept`·`written`), 쓴 뒤 저장된 행의 보호되는
값이 바뀌지 않았는지 칸마다 확인한다. 표는 두 상태로 적는다: 사람의 답을 기다리는 실행과, 사람이 답해 다시
일하는 실행. 카드는 기다리는 동안만 보호되므로 둘째 표에서는 보통의 쓰기로 바뀐다.

뒤의 절은 남의 실행을 같은 전권 키로 건드리는 시도다: 다른 소유자의 실행과 팀 스페이스의 실행. 그리고
기기가 허브가 처음 보는 자기 실행 기록을 올리는 경우(키마다 허브에 남는 값).

스택은 실물이다: 임베디드 스토어 위의 사이드카(HTTP) ← 브리지 ← 공개 허브(HTTP).
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import pkns, run_state  # noqa: E402
from tests.mcp.test_run_state_entrances import (  # noqa: E402
    KEY, OWNER, PROFILES, ROLE, Hub, _episode, _stamp)
from vinc_mcp import tools  # noqa: E402

OWNER_B = "user:u-2"
KEY_B = "vinc_sk_full_other_owner"
TEAM = "6f1f0a52-3c2e-4d55-8f5a-1c1d6a3f9b77"
OWNER_T = "team:" + TEAM
#: 소유자마다 자기 역할 노드를 가진다. 키 모드가 꺼져 있으면 노드 id 가 모두의 것이라 같은 id 를 둘이 쓸 수 없다.
ROLES = {OWNER: ROLE, OWNER_B: "concept:agent-dev-core-b", OWNER_T: "concept:agent-dev-core-t"}

STATE = tuple(run_state.STATE_KEYS)
ROLE_KEYS = tuple(run_state.ROLE_KEYS)
CARD = tuple(run_state.CARD_KEYS)
#: 사람이 답했든 안 했든 쓰기 입구가 바꾸지 못하는 키.
ALWAYS = STATE + ROLE_KEYS
#: 사람이 답을 기다리는 동안만 바꾸지 못하는 키: 사람이 읽고 답하는 카드.
WHILE_WAITING = CARD + ("title",)
#: 표가 훑는 키 전부.
SWEPT = ALWAYS + WHILE_WAITING

#: 키마다 꾸민 값. 기다리는 실행에 저장된 값과 모두 다르다.
FORGED = {
    "state": "working", "pending": {"actions": ["merge"], "kind": "ask_first", "question": "ok?"},
    "asks": 9, "last_asked_at": "2020-01-01T00:00:00Z", "task_cancelled": True,
    "actions_run": ["merge"], "record_pending": [{"action": "merge", "reason": "r"}],
    "record_notes": [{"step": 1, "note": "n"}], "host_record_ids": ["rec-1"],
    "approved": ["merge"], "answer": "approve", "answered_at": "2020-01-01T00:00:00Z",
    "answered_actions": ["merge"], "answered_by": OWNER, "answer_via": "person",
    "answer_text": "yes",
    "role": "concept:agent-other", "role_hash": "0" * 64,
    "run_id": "another-run", "task": "another task", "subject": "another subject",
    "host": "another host", "step": 7, "started_at": "2020-01-01T00:00:00Z",
}
#: 사람이 이미 답한 실행에 보내는 값. 저장된 답(approve·merge)과 모두 달라야 꾸민 쓰기가 된다.
FORGED_AFTER = {**FORGED, "state": "done", "approved": ["deploy"], "answered_actions": ["deploy"],
                "answer": "decline", "answered_by": "user:forger", "answer_via": "agent"}
TITLE = "A title the person never saw"
assert set(FORGED) == set(ALWAYS + CARD), "a protected key has no forged value: " + str(
    set(ALWAYS + CARD) ^ set(FORGED))


def _verify(key, space=None):
    """전권 키 둘. KEY 는 팀 TEAM 의 편집자이기도 하다(회원 키는 개인 주소와 팀 주소에서 모두 통한다)."""
    if space:
        if key == KEY and space == TEAM:
            return {"owner": OWNER_T, "scope": "full", "plan": "pro", "role": "editor",
                    "member": OWNER}
        return None
    return {KEY: {"owner": OWNER, "scope": "full", "plan": "pro"},
            KEY_B: {"owner": OWNER_B, "scope": "full", "plan": "pro"}}.get(key)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """허브 하나를 모듈이 같이 쓴다. 실행마다 id 가 달라 서로 닿지 않는다."""
    mp = pytest.MonkeyPatch()
    for name, value in (("MEMORY_STORE_BACKEND", "embedded"), ("MEMORY_EMBEDDING_MODEL", "local-en"),
                        ("VINC_DURABLE_ACK", "off"), ("VINC_TOPIC_CONVERSION", "0"),
                        ("VINC_STORE_SNAPSHOT", "")):
        mp.setenv(name, value)
    hub = Hub(tmp_path_factory.mktemp("every-entrance") / "store")
    hub.app.verify_key = _verify
    props = dict(PROFILES[ROLE])
    props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [], "reads": [],
                  "act_policy": {"notify": "ask_first"}})
    for owner, role_id in ROLES.items():
        role = {"id": role_id, "type": "concept", "title": "Agent role",
                "domain": "vinc/agent/roles", "props": props}
        seeded = tools.call("vinc_ingest", {"nodes": [role]}, hub.bridge, forced_owner=owner)
        assert seeded.get("executed"), seeded
    try:
        yield hub
    finally:
        hub.close()
        mp.undo()


def _post(hub: Hub, path: str, body: dict, key: str = KEY) -> tuple[int, dict]:
    req = urllib.request.Request(hub.base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        response = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read() or b"{}")


def _tool(hub: Hub, name: str, key: str = KEY, space: str | None = None, **arguments) -> dict:
    path = f"/t/{space}/mcp" if space else "/mcp"
    status, reply = _post(hub, path, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}}, key)
    assert status == 200, (status, reply)
    result = reply["result"]
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def _row(hub: Hub, owner: str, eid: str) -> dict | None:
    """그 소유자의 저장된 실행 행(제목과 props). 없으면 None."""
    got = hub.bridge.request("GET", "/runs", params={"owner": owner, "run": eid, "limit": 1,
                                                     "stored": "1"})
    assert got.get("executed"), got
    runs = got.get("runs") or []
    return {"title": runs[0].get("title"), "props": dict(runs[0]["props"])} if runs else None


def _waiting(hub: Hub, owner: str) -> list[str]:
    got = hub.bridge.request("GET", "/runs/waiting", params={"owner": owner})
    return sorted(item["id"] for item in got["runs"])


def _snapshot(row: dict | None) -> dict | None:
    """보호되는 값만: 각 키의 값(없으면 None)과 제목."""
    if row is None:
        return None
    return {**{k: row["props"].get(k) for k in ALWAYS + CARD}, "title": row["title"]}


def _waiting_run(hub: Hub, run_id: str, *, owner: str = OWNER, space: str | None = None,
                 key: str = KEY) -> str:
    """사람만 답하는 merge 대기에 든 실행 하나를 만든다. episode id 를 돌려준다."""
    run = _tool(hub, "vinc_run_start", key, space, role=ROLES[owner], task="Review the change.",
                run_id=run_id)
    assert run.get("executed") and "run" in run, run
    out = _tool(hub, "vinc_run_submit", key, space, run=run["run"], output="Ready to merge.",
                person_only_requests=[{"action": "merge", "reason": "run merge for PR 12"}])
    assert out["state"] == "waiting" and out["decision"]["kind"] == "person_only", out
    assert run["run"] in _waiting(hub, owner)
    return run["run"]


def _answer(hub: Hub, eid: str) -> None:
    """사람이 merge 를 승인한다(로그인한 세션만 쓰는 길). 실행은 다시 일하는 중이 된다."""
    asks = int(_row(hub, OWNER, eid)["props"]["asks"])
    out = hub.proxy("run-answer", {"run": eid, "ask": asks, "answer": "approve"}, auth="session")
    assert out["executed"] and out["state"] == "working", out
    assert _row(hub, OWNER, eid)["props"]["approved"] == ["merge"]


# ── 입구: 전권 키로 노드 하나를 보내고 입구의 답을 한 줄로 줄인다 ───────────────────────────────

def _guard_of(note: str) -> str:
    """거절한 문장이 어느 규칙의 것인가: 허브의 도구 입구(`run_state.writes`)냐, 사이드카의 쓰기냐."""
    if "belong to the run protocol" in note or "protocol state changes only through" in note:
        return "tool-entrance"
    if "A whole props would replace" in note:
        return "whole-props"
    if "waits for a person's answer" in note:
        return "card"
    return "other: " + note[:60]


def _verdict_of(out: dict) -> str:
    if out.get("executed") is False:
        if out.get("error") != run_state.CODE:
            return "rejected: " + str(out.get("error"))
        return "refused by " + _guard_of(str(out.get("note")))
    return "written"


def rest_fragments(hub, node, *, key=KEY, space=None):
    path = "/v1/fragments" + (f"?space={space}" if space else "")
    status, out = _post(hub, path, {"nodes": [node]}, key)
    if status == 403 and out["error"]["code"] == run_state.CODE:
        return "refused by " + _guard_of(out["error"]["message"])
    return f"status {status}" if status != 200 else _verdict_of(out)


def mcp_ingest(hub, node, *, key=KEY, space=None):
    return _verdict_of(_tool(hub, "vinc_ingest", key, space, nodes=[node]))


def proxy_fragment(hub, node, *, key=KEY, space=None):
    return _verdict_of(hub.proxy("ingest-fragment", {"nodes": [node], "edges": []}))


def sync_push(hub, node, *, key=KEY, space=None):
    """기기의 push. 응답이 허브의 값을 남긴 실행을 이름으로 알리면 `kept`."""
    bundle = {"nodes": [{**node, "_rev": 1, "_updated_at": _stamp()}], "edges": []}
    status, out = _post(hub, "/sync/push", {"bundle": bundle}, key)
    assert status == 200 and out["executed"] is True, (status, out)
    return "kept" if out.get(run_state.CODE) else "written"


INGEST = (("rest-fragments", rest_fragments), ("mcp-ingest", mcp_ingest),
          ("proxy-ingest-fragment", proxy_fragment))
FORMS = ("props", "patch")


def _node(hub: Hub, eid: str, key: str, form: str, forged: dict) -> dict:
    """키 하나를 꾸민 노드. `props`: 그 키만 싣는다. `patch`: `props_patch` 로 싣는다(읽은 rev 와 함께)."""
    node = _episode(eid, {})
    del node["props"]
    if key == "title":
        node["title"] = TITLE
    elif form == "props":
        node["props"] = {key: forged[key]}
    else:
        node["props_patch"] = {key: forged[key]}
        node["expected_rev"] = _tool(hub, "vinc_node", node_id=eid, raw=True)["node"]["rev"]
    return node


def _device_node(hub: Hub, owner: str, eid: str, key: str, forged: dict) -> dict:
    """기기가 들고 있던 저장된 행에서 키 하나만 꾸민 노드."""
    row = _row(hub, owner, eid)
    props, title = dict(row["props"]), row["title"]
    if key == "title":
        title = TITLE
    else:
        props[key] = forged[key]
    return {**_episode(eid, props), "title": title}


def _sweep(hub: Hub, key: str, *, run_id: str, answered: bool) -> dict:
    """키 하나를 입구마다 보내고 칸마다 입구의 답을 모은다. 보호되는 값은 칸마다 확인한다."""
    eid = _waiting_run(hub, run_id)
    if answered:
        _answer(hub, eid)
    forged = FORGED_AFTER if answered else FORGED
    guarded = ALWAYS if answered else SWEPT
    before = _snapshot(_row(hub, OWNER, eid))
    cells = {}

    def unchanged(cell):
        now = _snapshot(_row(hub, OWNER, eid))
        moved = sorted(k for k in before if k in guarded and now[k] != before[k])
        assert not moved, (cell, key, moved)

    for name, enter in INGEST:
        for form in FORMS:
            cells[f"{name}/{form}"] = enter(hub, _node(hub, eid, key, form, forged))
            unchanged(f"{name}/{form}")
    cells["sync-push"] = sync_push(hub, _device_node(hub, OWNER, eid, key, forged))
    unchanged("sync-push")
    return cells


def _grouped(table: dict) -> dict:
    """{키: {칸: 답}} → {칸: {답: [키]}}. 키는 정렬해 둔다."""
    out = {}
    for key, cells in table.items():
        for cell, verdict in cells.items():
            out.setdefault(cell, {}).setdefault(verdict, []).append(key)
    return {cell: {v: sorted(ks) for v, ks in verdicts.items()} for cell, verdicts in out.items()}


def _expected(props: dict, patch: dict, sync: dict) -> dict:
    """세 입구는 같은 규칙을 지나므로 칸 이름만 달리 적는다."""
    cells = {}
    for name, _ in INGEST:
        cells[f"{name}/props"] = {v: sorted(ks) for v, ks in props.items()}
        cells[f"{name}/patch"] = {v: sorted(ks) for v, ks in patch.items()}
    cells["sync-push"] = {v: sorted(ks) for v, ks in sync.items()}
    return cells


#: 기다리는 실행: 상태·역할·카드가 어느 입구로도 안 바뀐다. 도구 입구는 상태 키를 이름으로 거절하고,
#: 통째의 props 는 상태 없이 오면 사이드카의 쓰기가 거절하며(역할과 카드), 카드는 제목까지 사이드카가 지킨다.
WAITING = _expected(
    props={"refused by tool-entrance": STATE, "refused by whole-props": ROLE_KEYS + CARD,
           "refused by card": ("title",)},
    patch={"refused by tool-entrance": ALWAYS, "refused by card": WHILE_WAITING},
    sync={"kept": SWEPT})
#: 사람이 답해 다시 일하는 실행: 상태와 역할은 여전히 안 바뀌고, 카드는 보통의 쓰기로 바뀐다.
ANSWERED = _expected(
    props={"refused by tool-entrance": STATE, "refused by whole-props": ROLE_KEYS + CARD,
           "written": ("title",)},
    patch={"refused by tool-entrance": ALWAYS, "written": WHILE_WAITING},
    sync={"kept": ALWAYS, "written": WHILE_WAITING})


@pytest.mark.parametrize("answered", [False, True], ids=["waiting", "answered"])
def test_no_protected_key_changes_through_any_entrance(world, answered):
    table = {key: _sweep(world, key, run_id=f"every-{'a' if answered else 'w'}-{key}",
                         answered=answered) for key in SWEPT}
    assert _grouped(table) == (ANSWERED if answered else WAITING)


# ── 남의 실행: 다른 소유자와 팀 스페이스 ─────────────────────────────────────────────────────

def _approved_forgery(run_id: str) -> dict:
    """사람이 승인한 모양으로 꾸민 실행 기록 한 벌: 답 키와 질문 키를 모두 싣는다."""
    return {**{k: FORGED[k] for k in STATE if k != "pending"}, "run_id": run_id, "role": ROLE,
            "task": "Review the change.", "outcome": "waiting", "step": 1, "state": "working"}


def _attempts(hub: Hub, eid: str, run_id: str, *, space: str | None = None) -> dict:
    """전권 키 KEY 로 그 실행 id 에 꾸민 승인을 입구마다 보낸다. 팀 주소에는 동기화 길이 없다."""
    node = _episode(eid, _approved_forgery(run_id))
    out = {"rest-fragments": rest_fragments(hub, node, space=space),
           "mcp-ingest": mcp_ingest(hub, node, space=space)}
    if space is None:
        out["sync-push"] = sync_push(hub, node)
    return out


def test_another_owners_run_is_untouched_by_a_full_key(world):
    eid = _waiting_run(world, "other-owner", owner=OWNER_B, key=KEY_B)
    before = _snapshot(_row(world, OWNER_B, eid))
    assert _attempts(world, eid, "other-owner") == {
        "rest-fragments": "refused by tool-entrance", "mcp-ingest": "refused by tool-entrance",
        "sync-push": "kept"}
    # 남의 실행은 그대로 대기 중이고 승인함에 남는다.
    assert _snapshot(_row(world, OWNER_B, eid)) == before
    assert eid in _waiting(world, OWNER_B) and before["state"] == "waiting"
    # push 가 받는 쪽에서 무엇을 남겼나. 키마다 소유자가 갈리면(켠 모드) 보낸 사람의 소유로 새 행이
    # 생기되 질문 쪽만 받는다. 꺼진 모드는 id 가 모두의 것이라 남의 행에 쓰이지 않고 새 행도 없다.
    mine = _row(world, OWNER, eid)
    if pkns.enabled():
        assert mine["props"]["state"] == "working" and mine["props"]["approved"] == []
        assert not {"answer", "answered_by", "answered_at", "answer_via"} & set(mine["props"])
    else:
        assert mine is None
    assert eid not in _waiting(world, OWNER)
    # 남의 실행은 이 소유자의 읽기에도 보이지 않는다.
    seen = _tool(world, "vinc_runs", run=eid)
    assert [r for r in seen.get("runs") or [] if r["props"].get("approved")] == []


def test_a_team_run_is_untouched_by_a_member_key_in_the_personal_graph_and_in_the_team(world):
    eid = _waiting_run(world, "other-team", owner=OWNER_T, space=TEAM)
    before = _snapshot(_row(world, OWNER_T, eid))
    # 같은 키가 개인 그래프로 보내는 시도: 팀의 행에는 닿지 않는다.
    assert _attempts(world, eid, "other-team") == {
        "rest-fragments": "refused by tool-entrance", "mcp-ingest": "refused by tool-entrance",
        "sync-push": "kept"}
    # 팀 주소로 보내는 시도: 팀의 편집자도 같은 도구 입구를 지난다.
    assert _attempts(world, eid, "other-team", space=TEAM) == {
        "rest-fragments": "refused by tool-entrance", "mcp-ingest": "refused by tool-entrance"}
    assert _snapshot(_row(world, OWNER_T, eid)) == before
    assert eid in _waiting(world, OWNER_T) and before["state"] == "waiting"
    assert eid not in _waiting(world, OWNER)
    # 동기화는 개인 그래프만 잇는다: 팀 주소의 push 길은 없다.
    status, _ = _post(world, f"/t/{TEAM}/sync/push", {"bundle": {"nodes": [], "edges": []}})
    assert status == 404


# ── 기기가 허브가 처음 보는 자기 실행 기록을 올린다 ──────────────────────────────────────────

#: 카드 키 가운데 실행 id(`run_id`)는 꾸민 값이 id 와 어긋나므로 이 절의 표에서 뺀다.
UNSEEN_KEYS = tuple(k for k in ALWAYS + CARD if k != "run_id")


def _unseen_sweep(hub: Hub, key: str, run_id: str) -> str:
    """허브가 처음 보는 실행 기록에 키 하나를 싣어 push 한 뒤, 그 키가 허브에 어떻게 남았나."""
    eid = run_state.RUN_PREFIX + run_id
    props = {"run_id": run_id, "role": ROLE, "task": "Review the change.", "outcome": "waiting",
             "step": 1, "host": "device-cli 1.0", key: FORGED[key]}
    status, out = _post(hub, "/sync/push", {"bundle": {"nodes": [{
        **_episode(eid, props), "_rev": 1, "_updated_at": _stamp()}], "edges": []}})
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out
    stored = _row(hub, OWNER, eid)["props"]
    if key not in stored:
        return "dropped"
    return "kept as sent" if stored[key] == FORGED[key] else "kept reshaped"


#: 허브가 처음 보는 실행: 답 쪽 키는 하나도 남지 않고 승인은 빈 목록이다. 질문 쪽과 역할과 카드는 기기의
#: 값이 그대로 남는다(기기의 실행을 허브에서 이어 가려면 질문이 건너와야 한다). 대기(`pending`)만 허브가
#: 사람만 답하는 대기로 다시 만든다.
UNSEEN = {
    "dropped": sorted(set(run_state.ANSWER_KEYS) - {"approved"}),
    "kept as sent": sorted((set(run_state.QUESTION_KEYS) - {"pending"} | set(ROLE_KEYS)
                            | set(CARD)) - {"run_id"}),
    "kept reshaped": ["approved", "pending"],
}


def test_a_device_run_the_hub_never_saw_keeps_its_question_and_loses_its_answers(world):
    grouped = {}
    for key in UNSEEN_KEYS:
        grouped.setdefault(_unseen_sweep(world, key, f"every-unseen-{key}"), []).append(key)
    assert {verdict: sorted(keys) for verdict, keys in grouped.items()} == UNSEEN
