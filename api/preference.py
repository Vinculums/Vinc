# -*- coding: utf-8 -*-
"""Preference-capture surface — the *write* side that feeds rank="preference".

``api/query.py`` stays read-only (/recall /search /decide). The owner signals it
ranks on — pin, tags, access frequency — are produced here. Each builder is a
pure ``QueryPlan``; ``execute`` (reused from query) runs it when a DB is
reachable and otherwise returns the plan, so these stay testable without Neo4j.

    python -m api.cli pin   --episode <id> [--off]
    python -m api.cli tag   --episode <id> --tags a,b
    python -m api.cli touch --episode <id> --now 2026-06-30T12:00:00   # record a recall hit
"""
from __future__ import annotations

from .query import QueryPlan, execute


def pin_plan(episode_id: str, *, pinned: bool = True) -> QueryPlan:
    """Pin (or unpin) an Episode — the strongest preference signal."""
    cypher = (
        "MATCH (e:Episode {episode_id: $id}) SET e.pinned = $pinned "
        "RETURN e.episode_id AS id, e.pinned AS pinned"
    )
    return QueryPlan("pin", cypher, {"id": episode_id, "pinned": bool(pinned)},
                     note="preference signal: pin")


def tag_plan(episode_id: str, tags: list[str]) -> QueryPlan:
    """Add owner tags to an Episode (set-union; never duplicates, never removes).

    임베디드(Kùzu 계보) 정합(§6-2): 리스트 컴프리헨션(`[t IN $x WHERE …]`)은 kuzu
    파서가 거부하므로 `list_distinct(list_cat(...))` 로 합집합을 만든다 — 기존 태그
    순서 유지 + 신규만 추가(중복 제거). 실측 확인."""
    cypher = (
        "MATCH (e:Episode {episode_id: $id}) "
        "SET e.tags = list_distinct(list_cat(coalesce(e.tags, []), $tags)) "
        "RETURN e.episode_id AS id, e.tags AS tags"
    )
    return QueryPlan("tag", cypher, {"id": episode_id, "tags": list(tags)},
                     note="preference signal: tags")


def touch_plan(episode_id: str, *, now: str | None = None) -> QueryPlan:
    """Record a recall hit: increment ``access_count`` (the frequency signal) and,
    when a caller-supplied ISO timestamp ``now`` is given, stamp ``last_access``.
    ``now`` is injected (no wall clock here) so the plan stays deterministic."""
    sets = ["e.access_count = coalesce(e.access_count, 0) + 1"]
    params: dict = {"id": episode_id}
    if now:
        sets.append("e.last_access = $now")
        params["now"] = now
    cypher = (
        "MATCH (e:Episode {episode_id: $id}) "
        f"SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.access_count AS access_count, e.last_access AS last_access"
    )
    return QueryPlan("touch", cypher, params, note="preference signal: access frequency")


# ── Public entry points ──────────────────────────────────────────────────────

def pin(episode_id: str, *, pinned: bool = True, cfg=None) -> dict:
    return execute(pin_plan(episode_id, pinned=pinned), cfg)


def tag(episode_id: str, tags: list[str], *, cfg=None) -> dict:
    return execute(tag_plan(episode_id, tags), cfg)


def touch(episode_id: str, *, now: str | None = None, cfg=None) -> dict:
    return execute(touch_plan(episode_id, now=now), cfg)
