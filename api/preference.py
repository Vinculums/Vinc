# -*- coding: utf-8 -*-
"""Preference-capture surface — the *write* side that feeds rank="preference".

``api/query.py`` stays read-only (/recall /search /decide). The owner signals it
ranks on — pin, tags, access frequency — are produced here. Each builder is a
pure ``QueryPlan``; ``execute`` (reused from query) runs it when a DB is
reachable and otherwise returns the plan, so these stay testable without a store.

    python -m api.cli pin   --episode <id> [--off]
    python -m api.cli tag   --episode <id> --tags a,b
    python -m api.cli touch --episode <id> --now 2026-06-30T12:00:00   # record a recall hit
"""
from __future__ import annotations

from . import revstamp
from store import pkns
from store.config import load_config
from . import query as _query
from .query import QueryPlan, execute


def _stamp_sets(stamp: dict | None, params: dict) -> list[str]:
    """동기화 스탬프(S0)를 SET 절·params 에 얹는다. stamp=None 이면 무영향 —
    플랜은 순수 유지(스토어 없이 빌드·테스트 가능)."""
    if not stamp:
        return []
    params.update({"__ua": stamp["updated_at"], "__rev": stamp["rev"],
                   "__seq": stamp["sync_seq"]})
    return ["e.updated_at = $__ua", "e.rev = $__rev", "e.sync_seq = $__seq"]


def _ep_scope(owner: str | None, params: dict) -> str:
    """SEC-3: owner 주어지면 Episode MATCH 에 owner_id 술어를 더한다 — 강제 owner
    경로(원격/웹)에서 남의 episode_id 를 건드리면 MATCH 0건 → no-op(교차테넌트 차단).
    owner=None(로컬)이면 빈 문자열 → 현행 단일유저 동작."""
    if not owner:
        return ""
    params["owner"] = owner
    return " AND e.owner_id = $owner"


def pin_plan(episode_id: str, *, pinned: bool = True, stamp: dict | None = None,
             owner: str | None = None) -> QueryPlan:
    """Pin (or unpin) an Episode — the strongest preference signal.

    ``stamp`` 는 호출부(pin)가 **실제 변경일 때만** 만들어 넘긴다 — pinned 는 추적필드라
    동기화 대상이지만, 같은 값 재-pin 이 델타를 만들면 안 된다."""
    # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 근거: [[vinc.plan.node-pk-namespacing]] §7
    params: dict = {"id": pkns.key(owner, episode_id), "pinned": bool(pinned)}
    scope = _ep_scope(owner, params)
    sets = ["e.pinned = $pinned"] + _stamp_sets(stamp, params)
    cypher = (
        f"MATCH (e:Episode) WHERE e.episode_id = $id{scope} SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.pinned AS pinned"
    )
    return QueryPlan("pin", cypher, params, note="preference signal: pin")


def tag_union(current: list[str] | None, tags: list[str]) -> list[str]:
    """기존 태그 순서를 지키고 새 것만 뒤에 붙인 합집합(중복 제거)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in list(current or []) + list(tags):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def tag_plan(episode_id: str, tags: list[str], *, current: list[str] | None = None,
             stamp: dict | None = None, owner: str | None = None) -> QueryPlan:
    """Add owner tags to an Episode (set-union; never duplicates, never removes).

    합집합은 파이썬에서 만든다(`tag_union`). 엔진마다 철자가 달라서다: ladybug 는 리스트
    컴프리헨션을 거부하고 `list_distinct(list_cat(...))` 만 있고, Neo4j 에는 그 둘이 없다.
    ⚠ ``current`` 는 **지금 저장된 태그**여야 한다 — 비우면 기존 태그를 덮어쓴다. 그래서
    실행은 `tag()` 를 거친다: 같은 쓰기 잡 안에서 현재 값을 읽고 쓰므로(단일 writer)
    사이에 끼어드는 쓰기가 없다."""
    params: dict = {"id": pkns.key(owner, episode_id), "tags": tag_union(current, tags)}
    scope = _ep_scope(owner, params)
    sets = ["e.tags = $tags"] + _stamp_sets(stamp, params)
    cypher = (
        f"MATCH (e:Episode) WHERE e.episode_id = $id{scope} SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.tags AS tags"
    )
    return QueryPlan("tag", cypher, params, note="preference signal: tags")


def touch_plan(episode_id: str, *, now: str | None = None,
               owner: str | None = None) -> QueryPlan:
    """Record a recall hit: increment ``access_count`` (the frequency signal) and,
    when a caller-supplied ISO timestamp ``now`` is given, stamp ``last_access``.
    ``now`` is injected (no wall clock here) so the plan stays deterministic."""
    sets = ["e.access_count = coalesce(e.access_count, 0) + 1"]
    params: dict = {"id": pkns.key(owner, episode_id)}
    scope = _ep_scope(owner, params)
    if now:
        sets.append("e.last_access = $now")
        params["now"] = now
    cypher = (
        f"MATCH (e:Episode) WHERE e.episode_id = $id{scope} "
        f"SET {', '.join(sets)} "
        "RETURN e.episode_id AS id, e.access_count AS access_count, e.last_access AS last_access"
    )
    return QueryPlan("touch", cypher, params, note="preference signal: access frequency")


# ── Public entry points ──────────────────────────────────────────────────────

# ``store`` 주입(사이드카 §11 P2): 쓰기는 잡 워커의 단일 writer Connection 으로
# 직렬화된다(R4). 워커가 자신이 소유한 writer 스토어를 넘기면 execute 는 그것을
# 재사용하고 닫지 않는다(수명은 서버 소유). 미주입 시 종전대로 호출당 open→close.

def _current(store, episode_id: str, field: str, owner: str | None = None) -> dict | None:
    """스탬핑 판정용 현재 행({field, rev, owner}). 스토어 없거나 노드 없으면 None.

    SEC-3: owner 주어지면 소유 일치 행만 반환 → 강제 owner 경로에서 남의 episode 를
    조회해 스탬프 판정에 쓰지 않는다(fail-closed: 미소유면 None → plan 이 no-op)."""
    if store is None:
        return None
    params = {"id": pkns.key(owner, episode_id)}
    scope = _ep_scope(owner, params)
    rows = store.run(
        f"MATCH (e:Episode) WHERE e.episode_id = $id{scope} "
        f"RETURN e.{field} AS val, e.rev AS rev, e.owner_id AS owner", params)
    return rows[0] if rows else None


def pin(episode_id: str, *, pinned: bool = True, owner: str | None = None,
        cfg=None, store=None) -> dict:
    # 같은 값 재-pin 은 변경이 아니다 → 무스탬프(가짜 델타 방지, S0).
    cur = _current(store, episode_id, "pinned", owner)
    stamp = (revstamp.make_stamp(store, cur)
             if cur and bool(cur.get("val")) != bool(pinned) else None)
    return execute(pin_plan(episode_id, pinned=pinned, stamp=stamp, owner=owner), cfg, store=store)


def tag(episode_id: str, tags: list[str], *, owner: str | None = None,
        cfg=None, store=None) -> dict:
    # 합집합을 파이썬에서 만들므로 현재 태그를 반드시 읽는다. 스토어를 안 받았으면(CLI)
    # 여기서 열고 닫는다 — 안 읽고 쓰면 기존 태그가 사라진다.
    owns = store is None
    if owns:
        store = _query.open_store(cfg or load_config())   # execute 와 같은 여는 자리(테스트 대역도 여기)
    try:
        cur = _current(store, episode_id, "tags", owner)
        # 이미 전부 달린 태그면 합집합이 no-op → 무스탬프.
        changed = bool(cur) and not set(tags) <= set(cur.get("val") or [])
        stamp = revstamp.make_stamp(store, cur) if changed else None
        plan = tag_plan(episode_id, tags, current=(cur or {}).get("val") or [],
                        stamp=stamp, owner=owner)
        return execute(plan, cfg, store=store)
    finally:
        if owns and store is not None:
            store.close()


def touch(episode_id: str, *, now: str | None = None, owner: str | None = None,
          cfg=None, store=None) -> dict:
    # access_count/last_access 는 **비추적**(revstamp.TRACKED 참조) — 동기화 대상이
    # 아니므로 스탬프하지 않는다. recall 마다 델타가 생기면 동기화가 무의미해진다.
    return execute(touch_plan(episode_id, now=now, owner=owner), cfg, store=store)
