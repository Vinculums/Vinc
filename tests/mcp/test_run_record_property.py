# -*- coding: utf-8 -*-
"""실행 기록의 props 하나에 잘못된 모양의 값을 넣어 보는 생성 시험.

읽는 이름은 `store/run_state.py` 가 댄다(`PROPS`, 대기 안의 `PENDING_PROPS`, 사용량의
`run_protocol.USAGE_KEYS`). 이름마다 정해진 잘못된 모양(`SHAPES`)을 두 길로 쓴다.

- (i) 허브가 처음 보는 대기 중인 실행을 동기화 업로드로 보낸다.
- (ii) 허브가 가진 대기 중인 실행에 `vinc_ingest` 의 `props_patch` 로 쓴다(보호되지 않는 키만).
  가진 실행은 프로토콜이 만든 대기 하나의 저장된 props 를 본떠 동기화로 들여온다.

그리고 읽는 쪽과 사람·에이전트의 행동을 모두 부른다(`ACTIONS`). 모든 조합에서 이것이 성립한다.

- 예외가 없다. 답은 결과이거나 코드가 있는 프로토콜 오류다.
- 같은 소유자의 좋은 대기 셋이 늘 보인다.
- 대기 중인 잘못된 실행은 승인함과 `vinc_runs` 에 보이고, 사람의 거절이 그것을 끝낸다.
- 사람이 답하기 전에는 아무것도 승인되거나 답한 것으로 읽히지 않고, 에이전트의 답은 거절된다.
- 사람의 승인은 역할을 읽을 수 있으면 카드에 보인 행동만 승인하고, 못 읽으면 프로토콜 오류로
  거절되어 아무것도 승인하지 않는다. 거절되면 그 실행은 그대로 대기한다.

알려진 한계 하나만 따로 받는다: 결과 상한(`rpc.MAX_RESULT_CHARS`)을 넘는 값이 실린 도구 결과는
`vinc_too_large` 표지가 있는 거절이다. 그 모양(`long-text`)에서만 받는다.
이름 하나에 스토어 하나를 쓴다. 역할 계약은 시험 동안 바뀌지 않으므로 역할마다 한 번 읽어 둔다
(`_fresh_role` 을 기억하는 판으로 바꾼다). 두 키 모드(`VINC_PK_NAMESPACED`)에서 돈다.
"""
from __future__ import annotations

import copy
import json
import os
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
    KEY, OWNER, PLAIN, ROLE, Hub, _episode, _push, _stamp, hub)

_DROP = object()


def _nested(depth: int):
    value = "x"
    for _ in range(depth):
        value = [value, [1]]
    return value


#: 잘못된 모양들. 결과 상한을 넘는 `long-text` 는 따로 쓴다: 그 기록이 같은 스토어의 목록 결과를
#: 상한 위로 올리므로, 다른 모양들의 목록 검사는 그 기록이 들어오기 전에 끝난다.
SHAPES = {
    "missing": _DROP, "null": None, "empty-text": "", "text": "x", "zero": 0, "minus-one": -1,
    "fraction": 1.5, "nan": float("nan"), "inf": float("inf"), "-inf": float("-inf"),
    "true": True, "empty-list": [], "text-list": ["x"], "empty-dict": {}, "dict": {"a": 1},
    "nested-list": _nested(6), "json-object-text": '{"a": 1}', "long-text": "A" * 100_000,
}
#: 기본으로 도는 모양. 전체(`SHAPES`)는 `VINC_RUN_PROPERTY_FULL=1` 일 때 돈다: 전체는 키 모드마다 몇 분이 든다.
#: 모든 이름에 도는 넷은 읽는 쪽이 예외를 내던 모양을 모두 덮는다(없음, null, 글자, 유한하지 않은 수).
#: 나머지는 그 모양이 결함을 낸 이름에만 돈다: dict 는 역할·실행 id·승인·대기·사용량에, 무한대는 사용량에,
#: 결과 상한을 넘는 글은 제목이 되는 run_id 에.
_EVERY = ("missing", "null", "text", "nan")
_ONLY = {
    "dict": ("role", "run_id", "approved", "answered_actions", "pending", "used"),
    "inf": ("used",), "-inf": ("used",), "long-text": ("run_id",),
}
FULL = os.environ.get("VINC_RUN_PROPERTY_FULL") == "1"


def _shapes(path: str) -> list[str]:
    """이 이름에 쓸 모양들. 전체 실행이면 모두, 아니면 기본 부분집합."""
    if FULL:
        return list(SHAPES)
    head = path.split(".")[0]
    return [name for name in SHAPES
            if name in _EVERY or path in _ONLY.get(name, ()) or head in _ONLY.get(name, ())]


PATHS = (list(run_state.PROPS) + [f"pending.{k}" for k in run_state.PENDING_PROPS]
         + [f"used.{k}" for k in run_protocol.USAGE_KEYS])
#: (ii) 의 길로 쓸 수 있는 이름. 프로토콜 상태 키와 역할 키는 `props_patch` 를 거절한다(`run_state.writes`).
HELD_PATHS = [p for p in PATHS
              if p.split(".")[0] not in run_state.STATE_KEYS + run_state.ROLE_KEYS]
ACTIONS = ("runs-waiting", "vinc_runs list", "vinc_runs run", "vinc_run_start",
           "vinc_run_submit output", "vinc_run_submit answer", "tasks/get", "tasks/cancel",
           "events feed", "notification payload", "run-answer decline", "run-answer approve")
_TASKS = {"extensions": {run_tasks.EXTENSION: {}}}
_LIMIT = 200


def _set(props: dict, path: str, value) -> dict:
    """`path`("step" 이나 "pending.kind")에 값을 쓴다. `_DROP` 은 키를 뺀다."""
    head, _, tail = path.partition(".")
    target = props
    if tail:
        inner = props.get(head)
        target = props[head] = dict(inner) if isinstance(inner, dict) else {}
        head = tail
    if value is _DROP:
        target.pop(head, None)
    else:
        target[head] = copy.deepcopy(value)
    return props


def _unseen_props(run_id: str) -> dict:
    """허브가 처음 보는, 대기 중인 실행 하나의 온전한 props."""
    now = _stamp(0)
    return {"run_id": run_id, "role": ROLE, "task": "Review the change.", "outcome": "waiting",
            "step": 1, "state": "waiting", "asks": 1, "last_asked_at": now, "started_at": now,
            "host": "device-cli 1.0", "subject": "PR 12", "used": {"tool_calls": 1},
            "cited_ids": ["concept:x"], "capabilities_used": ["github.pr.read"],
            "capability_flags": [{"id": "github.pr.merge", "flag": "denied", "step": 1}],
            "pending": {"actions": ["merge"], "checks": ["person_only"], "kind": "person_only",
                        "asked_at": now, "expires_at": _stamp(3600),
                        "requests": [{"action": "merge", "reason": "r", "kind": "person_only"}]}}


def _rpc(hub: Hub, method: str, params: dict) -> dict:
    """`/mcp` 에 JSON-RPC 한 번. 모던 세대 메서드(tasks/*)는 그 세대의 헤더를 싣는다."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(hub.base + "/mcp", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY,
                                          **modern.request_headers(body)})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


class _Case:
    """조합 하나(이름 · 모양 · 길)의 기록 둘: 거절해 보는 A 와 승인해 보는 B."""

    def __init__(self, path: str, shape: str, how: str, n: int):
        self.path, self.shape, self.how = path, shape, how
        self.run_ids = [f"prop-{how}-{n}-a", f"prop-{how}-{n}-b"]
        self.eids = [run_protocol.episode_id(r) for r in self.run_ids]

    def __repr__(self) -> str:
        return f"{self.how}:{self.path}={self.shape}"


def _answered(case, name: str, out) -> None:
    """결과이거나 코드가 있는 프로토콜 오류다."""
    assert isinstance(out, dict) and isinstance(out.get("executed"), bool), (case, name, out)
    if out["executed"] is False:
        assert isinstance(out.get("error"), str) and out["error"], (case, name, out)


def _tool(hub: Hub, case, name: str, *, too_large: bool, **arguments) -> dict | None:
    """도구 호출 한 번. 결과 JSON 을 돌려준다. 결과 상한에 걸린 거절이면 None(`too_large` 일 때만)."""
    reply = _rpc(hub, "tools/call", {"name": name, "arguments": arguments})
    assert "result" in reply, (case, name, reply)
    result = reply["result"]
    if (result.get("_meta") or {}).get("vinc_too_large"):
        assert too_large, (case, name, result)
        return None
    out = result.get("structuredContent")
    if not isinstance(out, dict):
        out = json.loads(result["content"][0]["text"])
    _answered(case, name, out)
    return out


def _task(hub: Hub, case, method: str, eid: str, ask: int, waits: bool) -> None:
    reply = _rpc(hub, method, {"taskId": run_tasks.task_id(eid, ask),
                               "_meta": {modern.META_VERSION: modern.MODERN_VERSIONS[0],
                                         modern.META_CAPABILITIES: _TASKS}})
    if "result" in reply:
        return
    # 대기가 아닌 기록에서만, 그 질문 번호의 작업이 없다는 프로토콜 오류를 받는다.
    error = reply.get("error") or {}
    assert not waits and isinstance(error.get("code"), int), (case, method, reply)
    assert error.get("message", "").startswith("Failed to retrieve task"), (case, method, reply)


def _snapshot(hub: Hub, eids) -> dict:
    """저장된 그대로의 실행 기록 props(episode id → props). 실행 프로토콜이 읽는 문과 같다."""
    out = {}
    for eid in eids:
        got = hub.bridge.request("GET", "/runs", params={"owner": OWNER, "run": eid, "limit": 1,
                                                         "stored": "1"})
        assert got.get("executed") and [r["id"] for r in got["runs"]] == [eid], got
        out[eid] = run_state.props_of(got["runs"][0]["props"])
    return out


def _waits(props: dict) -> bool:
    # 만료가 지난 질문은 대기가 아니다: 프로토콜이 읽는 대로(`as_read`) 본다.
    return run_protocol.as_read(run_state.readable(props)).get("state") == "waiting"


def _unanswered(case, props: dict) -> None:
    assert run_state.readable(props).get("approved", []) == [], (case, props)
    assert "answer" not in props and "answered_at" not in props, (case, props)


def _write(hub: Hub, cases: list[_Case], template: dict) -> None:
    """조합들의 기록을 쓴다: 동기화 업로드 한 번, 그다음 가진 실행마다 `props_patch`."""
    nodes = []
    for case in cases:
        for run_id, eid in zip(case.run_ids, case.eids):
            if case.how == "unseen":
                props = _set(_unseen_props(run_id), case.path, SHAPES[case.shape])
            else:
                props = {**copy.deepcopy(template), "run_id": run_id}
            nodes.append(_episode(eid, props))
    status, out = _push(hub, *nodes)
    assert status == 200 and out.get("executed") is True and out["applied"] == len(nodes), out
    patches = []
    for case in cases:
        if case.how != "patch":
            continue
        head, _, tail = case.path.partition(".")
        value = None if SHAPES[case.shape] is _DROP else copy.deepcopy(SHAPES[case.shape])
        for eid in case.eids:
            node = tools.call("vinc_node", {"node_id": eid, "raw": True}, hub.bridge,
                              forced_owner=OWNER)["node"]
            assert _waits(node["props"]), node
            # props_patch 는 병합 패치다: null 은 키를 지우고 dict 는 안으로 합친다.
            patches.append({"id": eid, "type": "episode", "title": node["title"],
                            "props_patch": {head: {tail: value} if tail else value},
                            "expected_rev": node["rev"]})
    if patches:
        # 결과가 쓴 값을 되싣는 일이 있어 도구 결과 상한을 지나지 않는 같은 도구 함수로 쓴다.
        out = tools.call("vinc_ingest", {"nodes": patches}, hub.bridge, forced_owner=OWNER)
        assert out.get("executed") is True and out.get("error") is None, out


def _lists(hub: Hub, cases: list[_Case], goods: list[str], cursor: str, stored: dict,
           too_large: bool) -> dict:
    """목록 읽기 셋(승인함·vinc_runs·이벤트 피드)을 부르고 승인함의 카드들을 돌려준다."""
    listed = hub.proxy("runs-waiting", {"limit": _LIMIT})
    assert listed.get("executed") is True and not listed["truncated"], (cases, listed)
    cards = {item["id"]: item for item in listed["runs"]}
    assert set(goods) <= set(cards), (cases, sorted(cards))
    runs = _tool(hub, cases, "vinc_runs", too_large=too_large, limit=_LIMIT)
    if runs is not None:
        assert runs["executed"] is True and not runs["truncated"], runs
        assert set(goods) <= {r["id"] for r in runs["runs"]}, runs
    feed = _tool(hub, cases, "vinc_events", too_large=too_large, since=cursor,
                 kinds=["run.waiting", "run.answered"], limit=_LIMIT)
    if feed is not None:
        assert feed["executed"] is True, feed
        assert set(goods) <= {e["data"]["run"] for e in feed["events"]}, feed
    for case in cases:
        for eid in case.eids:
            if _waits(stored[eid]):
                assert eid in cards, (case, eid)
                if runs is not None:
                    assert eid in {r["id"] for r in runs["runs"]}, (case, eid)
    return cards


def _agent_side(hub: Hub, case: _Case, cards: dict, stored: dict) -> None:
    """A 에 읽기와 에이전트의 행동을 모두 해 본다."""
    run_id, eid = case.run_ids[0], case.eids[0]
    big = case.shape == "long-text"
    waits = _waits(stored[eid])
    _unanswered(case, stored[eid])
    one = _tool(hub, case, "vinc_runs", too_large=big, run=eid)
    if one is not None:
        assert one["executed"] is True and [r["id"] for r in one["runs"]] == [eid], (case, one)
    _tool(hub, case, "vinc_run_start", too_large=big, role=ROLE, task="Review the change.",
          run_id=run_id)
    _tool(hub, case, "vinc_run_submit", too_large=big, run=eid, output=PLAIN)
    agent = _tool(hub, case, "vinc_run_submit", too_large=big, run=eid, answer="approve")
    if agent is not None and waits:
        assert agent["executed"] is False, (case, agent)
    ask = cards[eid]["ask"] if eid in cards else 1
    _task(hub, case, "tasks/get", eid, ask, waits)
    _task(hub, case, "tasks/cancel", eid, ask, waits)
    body = run_notify.payload(OWNER, eid, stored[eid])
    if waits:
        assert body is not None and body["run"] == eid and body["ask"] >= 1, (case, body)


def _person_side(hub: Hub, case: _Case, cards: dict, before: dict) -> tuple[dict, dict]:
    """사람이 A 를 거절하고 B 를 승인한다. 두 답을 돌려준다."""
    answers = []
    for eid, answer in zip(case.eids, ("decline", "approve")):
        card = cards.get(eid)
        out = hub.proxy("run-answer", {"run": eid, "ask": card["ask"] if card else 1,
                                       "answer": answer}, auth="session")
        _answered(case, "run-answer " + answer, out)
        answers.append(out)
    return answers[0], answers[1]


def _same_contract(props: dict, pinned) -> bool:
    """기록이 연 때의 역할 계약 해시가 지금 계약의 해시와 같은가. 해시가 없거나 null 이면 견줄 것이 없다."""
    return props.get("role_hash") in (None, pinned)


def _check_after(case: _Case, cards: dict, before: dict, after_agent: dict, after: dict,
                 declined: dict, approved: dict, *, pinned: str) -> None:
    a, b = case.eids
    # 에이전트의 행동 뒤에도 A 는 그대로다: 승인도 답도 없고 대기 여부도 같다.
    _unanswered(case, after_agent[a])
    assert _waits(after_agent[a]) == _waits(before[a]), (case, after_agent[a])
    if _waits(before[a]):
        assert declined["executed"] is True and declined["state"] == "done", (case, declined)
        assert after[a]["state"] == "done" and after[a].get("answer") == "decline", (case, after[a])
        assert run_state.readable(after[a]).get("approved", []) == [], (case, after[a])
    else:
        assert declined["executed"] is False, (case, declined)
    if (_waits(before[b]) and run_protocol._role_of(before[b]) == ROLE
            and _same_contract(before[b], pinned)):
        assert approved["executed"] is True and approved["state"] == "working", (case, approved)
        assert after[b]["approved"] == cards[b]["decision"]["actions"], (case, after[b], cards[b])
        return
    # 역할을 못 읽거나, 연 때의 계약과 다르거나, 대기가 아니면 아무것도 승인되지 않고 기록은 그대로 대기한다.
    assert approved["executed"] is False, (case, approved)
    _unanswered(case, after[b])
    assert _waits(after[b]) == _waits(before[b]), (case, after[b])


def _round(hub: Hub, cases: list[_Case], goods: list[str], cursor: str, template: dict) -> None:
    too_large = any(c.shape == "long-text" for c in cases)
    _write(hub, cases, template)
    eids = [eid for case in cases for eid in case.eids]
    before = _snapshot(hub, eids)
    cards = _lists(hub, cases, goods, cursor, before, too_large)
    for case in cases:
        _agent_side(hub, case, cards, before)
    after_agent = _snapshot(hub, eids)
    answers = {repr(c): _person_side(hub, c, cards, before) for c in cases}
    after = _snapshot(hub, eids)
    for case in cases:
        _check_after(case, cards, before, after_agent, after, *answers[repr(case)],
                     pinned=template["role_hash"])
    still = {item["id"] for item in hub.proxy("runs-waiting", {"limit": _LIMIT})["runs"]}
    assert set(goods) <= still and not {c.eids[0] for c in cases} & still, sorted(still)


@pytest.fixture
def remembered_roles(monkeypatch):
    """역할 계약을 역할마다 한 번만 읽는다. 시험 동안 역할 노드는 바뀌지 않는다."""
    seen: dict = {}
    read = run_protocol._fresh_role

    def fresh_role(bridge, owner, role):
        if (owner, role) not in seen:
            seen[(owner, role)] = read(bridge, owner, role)
        return copy.deepcopy(seen[(owner, role)])

    monkeypatch.setattr(run_protocol, "_fresh_role", fresh_role)


def _goods(hub: Hub) -> list[str]:
    goods = []
    for i in range(3):
        run = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                       run_id=f"prop-good-{i}")
        out = hub.tool("vinc_run_submit", run=run["run"], output="Ready to merge once approved.",
                       person_only_requests=[{"action": "merge", "reason": "run merge"}])
        assert out["state"] == "waiting", out
        goods.append(run["run"])
    return goods


@pytest.mark.parametrize("path", PATHS)
def test_every_wrong_shape_of_a_run_prop_leaves_every_run_readable_and_answerable(
        hub, remembered_roles, path):
    cursor = hub.tool("vinc_events", kinds=["run.waiting"])["cursor"]
    goods = _goods(hub)
    # 가진 실행의 본: 프로토콜이 만든 대기의 저장된 props. 동기화가 처음 보는 실행으로 받는다.
    template = _snapshot(hub, goods[:1])[goods[0]]
    hows = ("unseen", "patch") if path in HELD_PATHS else ("unseen",)
    cases, n = [], 0
    for shape in _shapes(path):
        for how in hows:
            if how == "patch" and shape == "missing":
                # 병합 패치의 null 이 키를 지운다: 이 길의 missing 은 null 과 같은 쓰기다.
                continue
            n += 1
            cases.append(_Case(path, shape, how, n))
    _round(hub, [c for c in cases if c.shape != "long-text"], goods, cursor, template)
    _round(hub, [c for c in cases if c.shape == "long-text"], goods, cursor, template)
