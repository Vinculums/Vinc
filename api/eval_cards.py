# -*- coding: utf-8 -*-
"""역할에 붙은 평가 카드(`record:eval-card-*`)를 읽는다. 모델도 상태도 없다.

카드를 만드는 쪽은 SDK(`vinc_client.eval_card`)다. 여기는 저장된 Record 를 되읽어 최신순으로
내고, 어느 카드가 아직 현재인지(`valid_until` 이 비어 있는지)를 말할 뿐이다. 카드 안의 수는
다시 계산하지 않는다 - 만든 사람이 잰 값이다. 서로 다른 지문(묶음 해시, 저장소, 하네스 판)의
카드는 비교하지 않는다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

from store import pkns

from . import events, query as q

CARD_PREFIX = "record:eval-card-"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def clamp_limit(value) -> int:
    if value in (None, ""):
        return DEFAULT_LIMIT
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_limit: expected an integer") from None
    if not 1 <= n <= MAX_LIMIT:
        raise ValueError(f"invalid_limit: expected 1..{MAX_LIMIT}")
    return n


def list_cards(store, *, owner: str, role: str | None, limit: int = DEFAULT_LIMIT) -> dict:
    """역할의 카드를 `date` 가 새로운 것부터. `current` 는 대체되지 않은 카드다."""
    base = {"kind": "eval_cards", "executed": False}
    rid = events.role_id(role)
    if not owner:
        return {**base, "error": "owner is required"}
    if not rid:
        return {**base, "error": "role is required"}
    try:
        rows = events._run(q.QueryPlan(
            "eval_cards",
            "MATCH (r:Record)-[:ATTACHES_TO]->(c:Concept) WHERE r.owner_id = $owner "
            "AND c.owner_id = $owner AND c.concept_id = $role "
            "AND starts_with(r.record_id, $prefix) "
            "RETURN r.record_id AS id, r.title AS title, r.props AS props, "
            "r.valid_from AS valid_from, r.valid_until AS valid_until",
            {"owner": owner, "role": pkns.key(owner, rid),
             "prefix": pkns.key(owner, CARD_PREFIX)}), store)
    except events._StoreFailed as exc:
        return {**base, "error": str(exc)}
    cards = []
    for row in rows:
        props = events._props(row.get("props"))
        until = str(row.get("valid_until") or "")
        cards.append({"id": pkns.strip(row["id"]), "title": str(row.get("title") or ""),
                      "role": rid, "date": str(props.get("date") or row.get("valid_from") or ""),
                      "current": not until, "valid_until": until,
                      "suite": props.get("suite") or {}, "results": props.get("results") or [],
                      "frontier": props.get("frontier") or [],
                      "harness_version": props.get("harness_version") or "",
                      "store_fingerprint": props.get("store_fingerprint") or ""})
    cards.sort(key=lambda c: (c["date"], c["id"]), reverse=True)
    return {"kind": "eval_cards", "executed": True, "role": rid, "cards": cards[:limit],
            "total": len(cards), "truncated": len(cards) > limit}
