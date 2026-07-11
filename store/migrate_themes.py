# -*- coding: utf-8 -*-
"""마이그레이션 — 전역 고정 enum → 주제별 사용자 저작 어휘 (D11-R2 §7 · IMPL §9).

**그래프는 건드리지 않는다** — `themes.json`(어휘 메타)만 기록한다(어휘 편집이 그래프
데이터를 안 건드린다는 IMPL §3 경계). 오프라인·stdlib·멱등.

    python -m store.migrate_themes --dry-run
    python -m store.migrate_themes --apply

동작:
1. `legacy` 주제 생성 → 구 전역 enum(concept/domain/bridge/episode + relates_to/
   prerequisite_of/derives_from/connects_via)을 `system_draft/proposed` 초안으로 시드
   (지위 강등, 데이터 손실 0).
2. 그래프에 **실재 인스턴스(≥1)가 있는** 레거시 타입만 `legacy` 에서 재가 → `authored`
   (기존 적재분 이관). 미적재 enum 은 proposed 잔류(사용자가 나중에 재분류).
3. `legacy` 를 활성 주제로 지정 → 마이그 후 ingest 가 유효 대상 주제를 갖는다.

주제 미지정 레거시 적재분은 단일 `legacy` 주제에 귀속(D11-R2 §7-2) — 이후 사용자가
새 주제를 만들어 재분류한다(GUI/도구, 후속).
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import StoreConfig, load_config
from .theme_store import ThemeStore, LEGACY_NODE_TYPES, LEGACY_EDGE_TYPES

LEGACY_THEME_ID = "legacy"

# 레거시 타입 → 그래프 실재 여부를 세는 프로브(노드 라벨 / 관계 라벨). 인스턴스가
# 하나라도 있으면 그 타입을 authored 로 승격한다.
_NODE_PROBE = {
    "concept": ("Concept", "concept_id"),
    "domain": ("Domain", "name"),
    "bridge": ("Bridge", "bridge_id"),
    "episode": ("Episode", "episode_id"),
}
_EDGE_PROBE = {
    "relates_to": "RELATED_TO",
    "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM",
    "connects_via": "CONNECTS",
}


def _count(reader, cypher: str) -> int:
    try:
        rec = reader.run(cypher).single()
    except Exception:   # noqa: BLE001 — 미프로비저닝/미구현 방언이면 0 으로 강등(무 크래시)
        return 0
    if rec is None:
        return 0
    try:
        return int(rec["c"])
    except (KeyError, TypeError, ValueError):
        return 0


def _present_types(reader) -> tuple[set[str], set[str]]:
    """그래프에 실재 인스턴스(≥1)가 있는 레거시 노드/엣지 타입 집합."""
    if reader is None:
        return set(), set()
    nodes = {t for t, (label, _) in _NODE_PROBE.items()
             if _count(reader, f"MATCH (n:{label}) RETURN count(n) AS c") > 0}
    edges = {t for t, rel in _EDGE_PROBE.items()
             if _count(reader, f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c") > 0}
    return nodes, edges


def migrate(cfg: StoreConfig, *, dry_run: bool = True) -> dict:
    """themes.json 마이그레이션. dry_run 이면 계획만 반환(파일·그래프 무변경)."""
    themes = ThemeStore(cfg.data_dir)

    reader = None
    store = None
    if not dry_run:
        from .backend import open_store
        store = open_store(cfg)
        reader = store.reader_view() if store is not None else None
    else:
        # dry-run 도 실재 타입을 보고하려면 읽기 전용으로 스토어를 연다(있으면).
        from .backend import open_store
        store = open_store(cfg)
        reader = store.reader_view() if store is not None else None

    try:
        present_nodes, present_edges = _present_types(reader)
    finally:
        if reader is not None:
            reader.close()
        if store is not None:
            store.close()

    ratify_nodes = [t for t in LEGACY_NODE_TYPES if t in present_nodes]
    ratify_edges = [t for t in LEGACY_EDGE_TYPES if t in present_edges]

    plan = {
        "dry_run": dry_run,
        "theme_id": LEGACY_THEME_ID,
        "seed_node_types": list(LEGACY_NODE_TYPES),
        "seed_edge_types": list(LEGACY_EDGE_TYPES),
        "ratify_node_types": ratify_nodes,
        "ratify_edge_types": ratify_edges,
        "graph_touched": False,   # 불변: 어휘 파일만 기록
    }
    if dry_run:
        return plan

    themes.create_theme(LEGACY_THEME_ID, title="Legacy (미분류 이관)")
    for t in ratify_nodes:
        themes.ratify(LEGACY_THEME_ID, t, kind="node")
    for t in ratify_edges:
        themes.ratify(LEGACY_THEME_ID, t, kind="edge")
    themes.activate_theme(LEGACY_THEME_ID)
    plan["applied"] = True
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="전역 enum → 주제 어휘 마이그레이션 (D11-R2 §7 — themes.json 만 기록)")
    ap.add_argument("--apply", action="store_true",
                    help="apply to themes.json (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only, no file/graph change (default behavior; explicit)")
    args = ap.parse_args(argv)

    result = migrate(load_config(), dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
