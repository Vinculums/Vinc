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

from . import revstamp
from .query import QueryPlan, execute


def _stamp_sets(stamp: dict | None, params: dict) -> list[str]:
    """동기화 스탬프(S0)를 SET 절·params 에 얹는다. stamp=None 이면 무영향 —
    플랜은 순수 유지(스토어 없이 빌드·테스트 가능)."""
    if not stamp:
        return []
    params.update({"__ua": stamp["updated_at"], "__rev": stamp["rev"],
                   "__seq": stamp["sync_seq"]})
    return ["e.updated_at = $__ua", "e.rev = $__rev", "e.sync_seq = $__seq"]


def pin_plan(episode_id: str, *, pinned: bool = True, stamp: dict | None = None) -> QueryPlan:
    """Pin (or unpin) an Episode — the strongest preference signal.

    ``stamp`` 는 호출부(pin)가 **실제 변경일 때만** 만들어 넘긴다 — pinned 는 추적필드라
    동기화 대상이지만, 같은 값 재-pin 이 델타를 만들면 안 된다."""
    params: dict = {"id": episode_id, "pinned": bool(pinned)}
    sets = ["e.pinned = $pinned"] + _stamp_sets(stamp, params)
    cypher = (
        f"MATCH (e:Episode {{episode_id: $id}}) SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.pinned AS pinned"
    )
    return QueryPlan("pin", cypher, params, note="preference signal: pin")


def tag_plan(episode_id: str, tags: list[str], *, stamp: dict | None = None) -> QueryPlan:
    """Add owner tags to an Episode (set-union; never duplicates, never removes).

    임베디드(Kùzu 계보) 정합(§6-2): 리스트 컴프리헨션(`[t IN $x WHERE …]`)은 kuzu
    파서가 거부하므로 `list_distinct(list_cat(...))` 로 합집합을 만든다 — 기존 태그
    순서 유지 + 신규만 추가(중복 제거). 실측 확인."""
    params: dict = {"id": episode_id, "tags": list(tags)}
    sets = ["e.tags = list_distinct(list_cat(coalesce(e.tags, []), $tags))"] + \
        _stamp_sets(stamp, params)
    cypher = (
        f"MATCH (e:Episode {{episode_id: $id}}) SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.tags AS tags"
    )
    return QueryPlan("tag", cypher, params, note="preference signal: tags")


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

# ``store`` 주입(사이드카 §11 P2): 쓰기는 잡 워커의 단일 writer Connection 으로
# 직렬화된다(R4). 워커가 자신이 소유한 writer 스토어를 넘기면 execute 는 그것을
# 재사용하고 닫지 않는다(수명은 서버 소유). 미주입 시 종전대로 호출당 open→close.

def _current(store, episode_id: str, field: str) -> dict | None:
    """스탬핑 판정용 현재 행({field, rev, owner}). 스토어 없거나 노드 없으면 None."""
    if store is None:
        return None
    rows = store.run(
        f"MATCH (e:Episode {{episode_id: $id}}) "
        f"RETURN e.{field} AS val, e.rev AS rev, e.owner_id AS owner", {"id": episode_id})
    return rows[0] if rows else None


def pin(episode_id: str, *, pinned: bool = True, cfg=None, store=None) -> dict:
    # 같은 값 재-pin 은 변경이 아니다 → 무스탬프(가짜 델타 방지, S0).
    cur = _current(store, episode_id, "pinned")
    stamp = (revstamp.make_stamp(store, cur)
             if cur and bool(cur.get("val")) != bool(pinned) else None)
    return execute(pin_plan(episode_id, pinned=pinned, stamp=stamp), cfg, store=store)


def tag(episode_id: str, tags: list[str], *, cfg=None, store=None) -> dict:
    # 이미 전부 달린 태그면 합집합이 no-op → 무스탬프.
    cur = _current(store, episode_id, "tags")
    changed = bool(cur) and not set(tags) <= set(cur.get("val") or [])
    stamp = revstamp.make_stamp(store, cur) if changed else None
    return execute(tag_plan(episode_id, tags, stamp=stamp), cfg, store=store)


def touch(episode_id: str, *, now: str | None = None, cfg=None, store=None) -> dict:
    # access_count/last_access 는 **비추적**(revstamp.TRACKED 참조) — 동기화 대상이
    # 아니므로 스탬프하지 않는다. recall 마다 델타가 생기면 동기화가 무의미해진다.
    return execute(touch_plan(episode_id, now=now), cfg, store=store)
