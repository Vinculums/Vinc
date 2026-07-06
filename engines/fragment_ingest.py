# -*- coding: utf-8 -*-
"""MCP `vinc_ingest` graph fragment → 임베디드 스토어 MERGE (D11-R2 · 사용자 저작 타입).

[[vinc.mcp#§5]] 의 `{theme_id, nodes, edges, owner}` fragment 를 받아 MERGE 한다.
**타입은 전역 고정 enum 이 아니라 주제(theme)별 사용자 저작 어휘**로 해석한다
(store/theme_store.py). 어휘 밖 타입은 *스킵되지 않고* `status:proposed,
provenance:ingest_proposed` 로 어휘에 편입되며 응답 `proposed_types` 로 재가를
유도한다(정보 손실 0 — THESIS T1·T3, D11-R2 §3).

**어휘(frame) vs 물리 바인딩(content) 분리가 이 파일의 축이다:**
- *어떤 타입 이름이 존재하는가* = 어휘 → theme_store(그래프 밖 메타).
- *그 타입이 어느 물리 테이블에 묶이는가* = 아래 `_PHYSICAL_*_BINDINGS` — 이는
  **검증 게이트가 아니라 라우팅 표**다. 엔트리 없는 타입은 *reject 가 아니라*
  범용 `Entity`/`TYPED` 캐리어로 exact-match MERGE 된다.

레거시 4노드타입(concept/domain/bridge/episode)·4엣지타입은 실 엔진
(concepts/recall/bridges_ingest)과 api/query.py 가 읽고 쓰는 전용 테이블을 계속
쓴다. 그 밖의 사용자 저작 타입은 `Entity{entity_id,type,title,owner_id,theme_id,tags}`
+ `TYPED{type}(Entity→Entity)` 캐리어로 간다(store/embedded_schema.py).

MERGE 판별(created vs matched)은 MERGE 전에 MATCH 로 존재를 먼저 확인한다 — Kùzu
드라이버가 create/match 여부를 별도 반환하지 않기 때문. **매칭은 exact-match only**
(노드 id / 엣지 (from,type,to)) — 유사도 병합 영구 금지(THESIS T1).
"""
from __future__ import annotations

from typing import Any

# node.type → (label, id 컬럼, 세팅 가능한 필드). **레거시 전용 테이블 라우팅만** —
# 여기 없는 타입은 범용 Entity 캐리어로 간다(reject 아님). Domain 의 PK 는 실제로
# `name`(Bridge 의 CROSSES 관계가 이미 이 컬럼에 의존 — bridges_ingest.py).
_PHYSICAL_NODE_BINDINGS: dict[str, dict[str, Any]] = {
    "concept": {"label": "Concept", "id_col": "concept_id",
                "settable": {"title": "title", "domain": "domain", "tags": "tags"},
                "owner_col": "owner_id"},
    "domain": {"label": "Domain", "id_col": "name",
               "settable": {}, "owner_col": None},
    "bridge": {"label": "Bridge", "id_col": "bridge_id",
               "settable": {"title": "title"}, "owner_col": None},
    "episode": {"label": "Episode", "id_col": "episode_id",
                "settable": {"title": "title", "domain": "domain", "tags": "tags"},
                "owner_col": "owner_id"},
}

# edge.type → (rel 라벨, 허용된 (from_type, to_type) 집합) — 레거시 전용 관계 테이블만.
# 여기 없는(또는 pair 불일치) 엣지는 양 끝이 Entity 면 TYPED 캐리어로, 아니면 warnings.
_PHYSICAL_EDGE_BINDINGS: dict[str, dict[str, Any]] = {
    "relates_to": {"rel": "RELATED_TO", "pairs": {("concept", "concept")}},
    "prerequisite_of": {"rel": "PREREQUISITE_OF", "pairs": {("concept", "concept")}},
    "derives_from": {"rel": "DERIVES_FROM", "pairs": {("concept", "concept")}},
    "connects_via": {"rel": "CONNECTS", "pairs": {("bridge", "concept")}},
}

_ENTITY_LABEL = "Entity"
_ENTITY_ID = "entity_id"
_TYPED_REL = "TYPED"

DEFAULT_OWNER = "tenant:local"


def _fold_in(themes, theme_id, kind: str, name: Any, proposed: list[dict]) -> None:
    """어휘 밖 타입을 주제 어휘에 proposed 로 편입(멱등) + proposed 목록에 누적.
    themes/theme_id 부재 시 무동작(직접 호출 단위테스트·강등 경로 대비)."""
    if themes is None or not theme_id or not isinstance(name, str):
        return
    if themes.resolve_type(theme_id, kind, name) is None:
        newly = themes.propose_type(theme_id, kind, name)
        entry = {"kind": kind, "name": name}
        if newly and entry not in proposed:
            proposed.append(entry)


def _merge_dedicated_node(session, node: dict, spec: dict, *, owner: str,
                          warnings: list[str]) -> tuple[str, bool] | None:
    """레거시 전용 테이블 MERGE(라벨·id컬럼·settable 은 spec 이 정본). 불변 로직."""
    nid, title = node.get("id"), node.get("title")
    label, id_col, settable = spec["label"], spec["id_col"], spec["settable"]

    if node.get("domain") is not None and "domain" not in settable:
        warnings.append(f"node.domain not supported for label {label} (id={nid})")
    if node.get("tags") and "tags" not in settable:
        warnings.append(f"node.tags not supported for label {label} (id={nid})")
    if node.get("props"):
        warnings.append(f"node.props not persisted — no schema-free column for label {label} (id={nid})")

    existing = session.run(f"MATCH (n:{label} {{{id_col}: $id}}) RETURN n.{id_col} AS id", id=nid)
    was_existing = existing.single() is not None

    set_parts: list[str] = []
    params: dict[str, Any] = {"id": nid}
    if "title" in settable:
        set_parts.append(f"n.{settable['title']} = $title")
        params["title"] = title
    if "domain" in settable and node.get("domain") is not None:
        set_parts.append(f"n.{settable['domain']} = $domain")
        params["domain"] = node["domain"]
    if "tags" in settable and node.get("tags") is not None:
        set_parts.append(f"n.{settable['tags']} = $tags")
        params["tags"] = list(node["tags"])
    if spec["owner_col"]:
        set_parts.append(f"n.{spec['owner_col']} = $owner")
        params["owner"] = owner

    cypher = f"MERGE (n:{label} {{{id_col}: $id}})"
    if set_parts:
        cypher += " SET " + ", ".join(set_parts)
    session.run(cypher, **params)
    return nid, was_existing


def _merge_entity_node(session, node: dict, ntype: str, *, owner: str, theme_id: str | None,
                       warnings: list[str]) -> tuple[str, bool]:
    """범용 Entity 캐리어 MERGE — 사용자 저작 타입 노드. exact entity_id 매칭.
    type/theme_id 를 각인(노드 소유 = 주제 귀속). domain 은 승계하지 않음."""
    nid, title = node.get("id"), node.get("title")
    if node.get("domain") is not None:
        warnings.append(f"node.domain not persisted on Entity (id={nid}); fold into tags/theme meta")
    if node.get("props"):
        warnings.append(f"node.props not persisted — no schema-free column on Entity (id={nid})")

    existing = session.run(
        f"MATCH (n:{_ENTITY_LABEL} {{{_ENTITY_ID}: $id}}) RETURN n.{_ENTITY_ID} AS id", id=nid)
    was_existing = existing.single() is not None

    session.run(
        f"MERGE (n:{_ENTITY_LABEL} {{{_ENTITY_ID}: $id}}) "
        f"SET n.type = $type, n.title = $title, n.owner_id = $owner, "
        f"n.theme_id = $theme, n.tags = $tags",
        id=nid, type=ntype, title=title, owner=owner,
        theme=theme_id or "", tags=list(node.get("tags") or []))
    return nid, was_existing


def _merge_node(session, node: dict, *, owner: str, theme_id: str | None,
                warnings: list[str]) -> tuple[str, bool, bool] | None:
    """단일 노드 MERGE. 반환 (id, was_existing, is_entity) 또는 스킵 시 None.
    스킵은 **id/title 형태 오류일 때만** — 어휘 밖 타입은 스킵하지 않는다."""
    nid, ntype, title = node.get("id"), node.get("type"), node.get("title")
    if not nid or not isinstance(nid, str):
        warnings.append(f"node missing/invalid id (type={ntype!r})")
        return None
    if not title or not isinstance(title, str):
        warnings.append(f"node.title required: id={nid}")
        return None

    spec = _PHYSICAL_NODE_BINDINGS.get(ntype)
    if spec is not None:
        res = _merge_dedicated_node(session, node, spec, owner=owner, warnings=warnings)
        return None if res is None else (res[0], res[1], False)
    res = _merge_entity_node(session, node, ntype, owner=owner, theme_id=theme_id, warnings=warnings)
    return res[0], res[1], True


def _merge_edge(session, edge: dict, node_types: dict[str, Any],
                node_is_entity: dict[str, bool], warnings: list[str]) -> bool:
    efrom, eto, etype = edge.get("from"), edge.get("to"), edge.get("type")
    ft, tt = node_types.get(efrom), node_types.get(eto)
    if ft is None or tt is None:
        warnings.append(f"edge endpoint not declared in fragment.nodes: {efrom!r} -> {eto!r}")
        return False

    spec = _PHYSICAL_EDGE_BINDINGS.get(etype)
    if spec is not None and (ft, tt) in spec["pairs"]:
        from_spec, to_spec = _PHYSICAL_NODE_BINDINGS[ft], _PHYSICAL_NODE_BINDINGS[tt]
        session.run(
            f"MATCH (a:{from_spec['label']} {{{from_spec['id_col']}: $from_id}}), "
            f"(b:{to_spec['label']} {{{to_spec['id_col']}: $to_id}}) "
            f"MERGE (a)-[:{spec['rel']}]->(b)",
            from_id=efrom, to_id=eto)
        return True

    # 어휘 밖(또는 pair 불일치) 엣지: 양 끝이 Entity 면 TYPED 캐리어로 exact
    # (from,type,to) MERGE. 아니면 물리 토폴로지 한계(레거시 노드 간 신규 엣지는
    # 조합 폭발 회피 위해 미표현) — warnings(스킵 아님과 다른 사유).
    if node_is_entity.get(efrom) and node_is_entity.get(eto):
        session.run(
            f"MATCH (a:{_ENTITY_LABEL} {{{_ENTITY_ID}: $from_id}}), "
            f"(b:{_ENTITY_LABEL} {{{_ENTITY_ID}: $to_id}}) "
            f"MERGE (a)-[:{_TYPED_REL} {{type: $type}}]->(b)",
            from_id=efrom, to_id=eto, type=etype)
        return True

    warnings.append(
        f"edge.type {etype!r} between physically-bound nodes ({ft}, {tt}) has no "
        f"rel-table binding — topology limitation, not an unknown-type skip")
    return False


def merge_fragment(session, fragment: dict, *, theme_id: str | None = None,
                   themes=None) -> dict:
    """[[vinc.mcp#§5]] fragment 를 MERGE. 어휘 밖 타입은 `themes` 어휘에 proposed 로
    편입(themes+theme_id 제공 시). 반환은 D11-R2 §3 계약 —
    `merged.nodes.{created,matched}` · `merged.edges.created` · `skipped` ·
    `proposed_types` · `warnings`."""
    warnings: list[str] = []
    proposed: list[dict] = []
    owner = fragment.get("owner") or DEFAULT_OWNER
    node_types: dict[str, Any] = {}
    node_is_entity: dict[str, bool] = {}
    created = matched = edges_created = 0

    for node in fragment.get("nodes") or []:
        _fold_in(themes, theme_id, "node", node.get("type"), proposed)
        res = _merge_node(session, node, owner=owner, theme_id=theme_id, warnings=warnings)
        if res is None:
            continue
        nid, was_existing, is_entity = res
        node_types[nid] = node.get("type")
        node_is_entity[nid] = is_entity
        if was_existing:
            matched += 1
        else:
            created += 1

    for edge in fragment.get("edges") or []:
        _fold_in(themes, theme_id, "edge", edge.get("type"), proposed)
        if _merge_edge(session, edge, node_types, node_is_entity, warnings):
            edges_created += 1

    return {
        "merged": {"nodes": {"created": created, "matched": matched},
                   "edges": {"created": edges_created}},
        "skipped": 0,   # 어휘 밖 타입은 편입(proposed)되지 어휘 사유로 스킵되지 않는다
        "proposed_types": proposed,
        "warnings": warnings,
    }


def ingest_fragment(store, fragment: dict, *, theme_id: str | None = None,
                    themes=None) -> dict:
    """사이드카 잡 큐 진입점(`api/server.py` `VincApp.write_ingest`). **theme_id 필수**
    (없으면 reject — D11-R2 §3·IMPL §4). writer 세션으로 MERGE. store 미도달 시 강등."""
    if not theme_id:
        return {"executed": False, "rejected": True,
                "merged": {"nodes": {"created": 0, "matched": 0}, "edges": {"created": 0}},
                "skipped": 0, "proposed_types": [],
                "warnings": ["theme_id required — ingest rejected"]}
    if store is None:
        return {"executed": False,
                "merged": {"nodes": {"created": 0, "matched": 0}, "edges": {"created": 0}},
                "skipped": 0, "proposed_types": [],
                "warnings": ["no reachable store"]}
    with store.session() as session:
        result = merge_fragment(session, fragment, theme_id=theme_id, themes=themes)
    result["executed"] = True
    return result
