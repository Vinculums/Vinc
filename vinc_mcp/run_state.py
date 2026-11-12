# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)의 프로토콜 상태를 지키는 규칙 한 벌.

대기·승인·답(`run_protocol.STATE_KEYS`)은 실행 프로토콜만 쓴다. 허브의 쓰기 입구가 이 모듈 하나를
같이 쓴다: MCP·REST 의 `vinc_ingest`(tools.py), 웹 프록시의 `ingest-fragment`·`ingest-pack`
(http_server.proxy_op)은 그런 쓰기를 거절하고, 기기 동기화 `/sync/push`(http_server._sync)는
거절하지 않고 허브가 저장한 값을 남긴다. 실행 기록이 든 묶음도 실패시키지 않는다.
"""
from __future__ import annotations

import contextlib
import json

from . import run_protocol

#: 막힌 쓰기와 동기화의 경고가 같이 쓰는 코드.
CODE = "run_state_protected"
#: 경고 문장에 이름을 싣는 실행 수의 상한. 전체 목록은 응답의 같은 이름 필드에 있다.
_NAMED_MAX = 20
_MISSING = object()


def run_id(nid) -> str | None:
    """id 가 실행 기록을 가리키면 스토어가 쓰는 모양(앞뒤 공백을 벗긴 id)을, 아니면 None.

    fragment 병합은 id 의 앞뒤 공백을 벗기고 쓴다. 벗기지 않고 견주면 `" episode:run-x"` 가
    이 검사를 지나 같은 노드에 닿는다. 대소문자는 바꾸지 않는다: 스토어도 바꾸지 않아 다른 노드다."""
    if not isinstance(nid, str):
        return None
    text = nid.strip()
    return text if text.startswith(run_protocol.RUN_PREFIX) else None


def props_of(value) -> dict:
    """노드에 실린 props 를 dict 로 읽는다. 동기화 적용처럼 JSON 문자열도 풀고, 못 읽으면 빈 dict."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else {}
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def writes(nodes) -> list[str]:
    """실행 기록의 프로토콜 상태 필드를 고치려는 노드 id 들(공백을 벗긴 id).

    도구로 `state`·`approved`·`answer` 를 바꿀 수 있으면 에이전트 자격이 사람만 답하는 대기를
    스스로 풀 수 있다. host·trace_ref 같은 나머지 필드는 그대로 `props_patch` 로 더한다."""
    blocked = []
    for node in nodes if isinstance(nodes, list) else []:
        rid = run_id(node.get("id")) if isinstance(node, dict) else None
        if rid is None:
            continue
        keys = set()
        for field in ("props", "props_patch"):
            keys |= set(props_of(node.get(field)))
        if keys & set(run_protocol.STATE_KEYS):
            blocked.append(rid)
    return blocked


def keep_stored(nodes, stored: dict) -> tuple[list, list[str]]:
    """동기화 묶음의 실행 기록마다 props 의 프로토콜 상태를 허브가 저장한 값으로 바꾼다.

    `stored` 는 공백을 벗긴 실행 id → 허브에 저장된 props(처음 보는 실행이면 None)다. props 는
    쓸 때 통째로 바뀌므로 저장된 상태 키를 그대로 실어 보낸다. 처음 보는 실행은 상태 키 없이 간다.
    props 를 싣지 않은 노드는 동기화가 그 필드를 말하지 않은 것으로 읽으므로 그대로 둔다.
    돌려주는 것은 (바꾼 노드 목록, 보낸 값과 저장된 값이 달랐던 실행 id)."""
    out, withheld = [], []
    for node in nodes if isinstance(nodes, list) else []:
        rid = run_id(node.get("id")) if isinstance(node, dict) else None
        if rid is None or "props" not in node:
            out.append(node)
            continue
        pushed = props_of(node.get("props"))
        kept = {k: v for k, v in props_of(stored.get(rid)).items()
                if k in run_protocol.STATE_KEYS}
        props = {k: v for k, v in pushed.items() if k not in run_protocol.STATE_KEYS}
        props.update(kept)
        if any(pushed.get(k, _MISSING) != kept.get(k, _MISSING) for k in run_protocol.STATE_KEYS):
            withheld.append(rid)
        out.append({**node, "props": props})
    return out, sorted(set(withheld))


def _locks(ids):
    """실행마다의 프로토콜 잠금을 겹치지 않게, 늘 같은 순서로(교착 방지) 고른다."""
    return sorted({run_protocol._lock(rid) for rid in ids}, key=run_protocol._LOCKS.index)


def push(bridge, owner: str, bundle) -> dict:
    """기기가 보낸 동기화 묶음을 허브 스토어에 적용한다. 실행 기록의 프로토콜 상태는 허브의 값이다.

    읽기부터 적용까지 그 실행들의 프로토콜 잠금을 쥔다. 그 사이에 사람이 답하면 방금 읽은 옛 상태가
    답을 덮는다. 저장된 상태를 못 읽으면 예외로 끝낸다: 모르는 채 적용하면 허브의 상태를 지운다."""
    bundle = bundle if isinstance(bundle, dict) else {}
    nodes = bundle.get("nodes")
    ids = sorted({rid for node in nodes if isinstance(node, dict) and "props" in node
                  and (rid := run_id(node.get("id")))} if isinstance(nodes, list) else set())
    with contextlib.ExitStack() as held:
        for lock in _locks(ids):
            held.enter_context(lock)
        stored = {}
        for rid in ids:
            got = run_protocol._load(bridge, owner, rid)
            if isinstance(got, dict) and "_error" in got:
                raise RuntimeError(f"stored run unreadable: {rid}")
            stored[rid] = got
        if ids:
            nodes, withheld = keep_stored(nodes, stored)
            bundle = {**bundle, "nodes": nodes}
        else:
            withheld = []
        out = bridge.request("POST", "/sync/apply-delta", body={"owner": owner, "bundle": bundle})
    if withheld and isinstance(out, dict):
        named = ", ".join(withheld[:_NAMED_MAX])
        more = f" and {len(withheld) - _NAMED_MAX} more" if len(withheld) > _NAMED_MAX else ""
        out[CODE] = withheld
        out["warnings"] = list(out.get("warnings") or []) + [
            f"{CODE}: kept the hub's protocol state ({', '.join(run_protocol.STATE_KEYS[:3])}, ...) "
            f"on {len(withheld)} run record(s): {named}{more}. A run's waiting, approval and answer "
            f"change only through the run protocol on the hub."]
    return out
