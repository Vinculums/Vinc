# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)의 프로토콜 상태를 지키는 규칙 한 벌.

대기·승인·답(`STATE_KEYS`)은 그 실행을 가진 스토어의 실행 프로토콜만 쓴다. 쓰기 입구가 모두
이 모듈을 같이 쓴다:

- 도구 쓰기(MCP·REST `vinc_ingest`, 웹 프록시 `ingest-fragment`·`ingest-pack`)는 `writes` 로
  그런 쓰기를 찾아 거절한다(`vinc_mcp/tools.py`, `vinc_mcp/http_server.py`).
- 동기화 적용(`api/sync.py` `apply_delta`)은 묶음을 거절하지 않고 `keep_own` 으로 받는 스토어의
  값을 남긴다. 허브는 push 에서, 기기는 pull 에서 같은 함수를 지난다.

``store/`` 는 맨 아래 층이라(``scripts/gate_ownership.py`` 의 ``LAYER_BANS``) ``api`` 와
``vinc_mcp`` 가 둘 다 이것을 가져다 쓴다.
"""
from __future__ import annotations

import json

RUN_PREFIX = "episode:run-"
#: 실행 기록의 프로토콜 상태 필드. 도구 쓰기로는 바꾸지 못하고, 동기화로는 건너가지 않는다.
STATE_KEYS = ("state", "pending", "approved", "asks", "last_asked_at", "answer", "answered_at",
              "answered_actions", "answered_by", "answer_via", "answer_text", "task_cancelled")
#: 막힌 쓰기의 오류 코드이자 동기화 결과가 남긴 실행을 싣는 필드 이름.
CODE = "run_state_protected"
#: 경고 문장에 이름을 싣는 실행 수의 상한. 전체 목록은 결과의 같은 이름 필드에 있다.
_NAMED_MAX = 20
_MISSING = object()


def run_id(nid) -> str | None:
    """id 가 실행 기록을 가리키면 스토어가 쓰는 모양(앞뒤 공백을 벗긴 id)을, 아니면 None.

    fragment 병합은 id 의 앞뒤 공백을 벗기고 쓴다. 벗기지 않고 견주면 `" episode:run-x"` 가
    이 검사를 지나 같은 노드에 닿는다. 대소문자는 바꾸지 않는다: 스토어도 바꾸지 않아 다른 노드다."""
    if not isinstance(nid, str):
        return None
    text = nid.strip()
    return text if text.startswith(RUN_PREFIX) else None


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
        if keys & set(STATE_KEYS):
            blocked.append(rid)
    return blocked


def keep_own(incoming, own) -> tuple[dict, bool]:
    """동기화로 들어온 실행 기록의 props 에서 프로토콜 상태만 받는 스토어의 값으로 바꾼다.

    `own` 은 받는 스토어에 저장된 props(처음 보는 실행이면 None)다. 들어온 상태 키는 버리고
    저장된 상태 키를 그대로 싣는다: props 는 쓸 때 통째로 바뀌므로 빼기만 하면 저장된 상태가
    지워진다. 처음 보는 실행은 상태 키 없이 간다. 나머지 키는 보통의 동기화 규칙을 따른다.
    돌려주는 것은 (바꾼 props, 들어온 상태가 남긴 상태와 달랐는가)."""
    incoming, own = props_of(incoming), props_of(own)
    kept = {k: v for k, v in own.items() if k in STATE_KEYS}
    props = {k: v for k, v in incoming.items() if k not in STATE_KEYS}
    props.update(kept)
    differed = any(incoming.get(k, _MISSING) != kept.get(k, _MISSING) for k in STATE_KEYS)
    return props, differed


def warning(ids: list[str]) -> str:
    """상태를 남긴 실행들을 알리는 경고 한 줄. 결과의 `CODE` 필드에 전체 목록이 함께 간다."""
    named = ", ".join(ids[:_NAMED_MAX])
    more = f" and {len(ids) - _NAMED_MAX} more" if len(ids) > _NAMED_MAX else ""
    return (f"{CODE}: kept this store's protocol state ({', '.join(STATE_KEYS[:3])}, ...) "
            f"on {len(ids)} run record(s): {named}{more}. A run's waiting, approval and answer "
            f"change only through the run protocol of the store that records them.")
