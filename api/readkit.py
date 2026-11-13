# -*- coding: utf-8 -*-
"""읽기 화면(이벤트·실행·에이전트 쓰기·평가 카드)이 같이 쓰는 작은 도구.

모델도 상태도 없다. 조회 하나를 실행해 행을 돌려주고, 실패를 한 가지 예외로 바꾸고,
저장된 props 와 제목을 같은 규칙으로 읽고, `limit` 인자를 한 규칙으로 검사한다.
`api/events.py` 는 사설 이름(`_run`·`_props`·`_title`·`_StoreFailed`·`_superseded_by`)을
이 모듈의 이름으로 그대로 가리키므로 두 이름은 같은 객체다.
"""
from __future__ import annotations

import json

from store import pkns

from . import query as q

#: 읽기 화면이 돌려주는 제목의 최대 길이(글자).
TITLE_CAP = 200


class StoreFailed(RuntimeError):
    """조회가 실행되지 않았다. 메시지는 스토어가 준 오류 그대로다."""


def run_plan(plan: q.QueryPlan, store) -> list[dict]:
    """조회 하나를 실행해 행 목록을 돌려준다. 실행되지 않았으면 `StoreFailed`."""
    res = q.execute(plan, None, store=store)
    if not res.get("executed"):
        raise StoreFailed(res.get("error") or res.get("note") or "store query failed")
    return list(res.get("rows") or [])


def props(raw) -> dict:
    """저장된 props(dict 또는 JSON 문자열)를 dict 로. 비었거나 깨졌거나 dict 가 아니면 빈 dict."""
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def title(value) -> str:
    """제목을 문자열로, `TITLE_CAP` 글자까지."""
    return str(value or "")[:TITLE_CAP]


def clamp_limit(value, *, default: int, maximum: int) -> int:
    """`limit` 인자 검사. 비었으면 `default`, 1..`maximum` 밖이거나 정수가 아니면 ValueError.

    오류 문구(`invalid_limit: ...`)는 클라이언트가 읽으므로 바꾸지 않는다."""
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_limit: expected an integer") from None
    if not 1 <= n <= maximum:
        raise ValueError(f"invalid_limit: expected 1..{maximum}")
    return n


def superseded_by(store, owner: str, label: str, pk: str, ids: list[str]) -> dict[str, str]:
    """`ids` 중 같은 라벨의 다른 노드가 `SUPERSEDES` 로 가리키는 것 → 그 새 노드 id.

    한 노드를 여럿이 대체하면 처음 읽힌 하나만 남는다. id 는 소유자 접두를 벗긴 형태다."""
    if not ids:
        return {}
    rows = run_plan(q.QueryPlan(
        f"events_supersedes_{label}",
        f"MATCH (a:{label})-[:SUPERSEDES]->(b:{label}) WHERE b.{pk} IN $ids "
        f"AND a.owner_id = $owner AND b.owner_id = $owner RETURN a.{pk} AS by, b.{pk} AS id",
        {"ids": pkns.keys(owner, ids), "owner": owner}), store)
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(pkns.strip(r["id"]), pkns.strip(r["by"]))
    return out
