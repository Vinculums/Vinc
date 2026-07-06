# -*- coding: utf-8 -*-
"""MCP `vinc_ingest` graph fragment → 임베디드 스토어 MERGE.

[[vinc.mcp#§5]] 의 `{nodes, edges, owner}` fragment 를 받아 **실제 임베디드 스키마**
(`store/embedded_schema.py`)에 이미 선언된 라벨·컬럼·관계로만 MERGE 한다.

D11 계약(및 [[vinc.mcp.skeleton#§9]] 초안)이 가정한 `record` 노드 타입·자유
`props{}` 확장·`inherits_from` 관계는 현재 스키마에 대응 컬럼/테이블이 없다.
[[vinc.c.dataplane#§2]] 의 불변("투기적 열 금지 — 열 명세의 SSoT 는 엔진 SET 전수
조사")을 지키기 위해 스키마를 넓히는 대신 **계약을 실제 스키마에 맞춘다** — 미지원
type/필드는 전체 실패가 아니라 개별 스킵 + `warnings` 로 알린다(D11 §5 "금지" 조항과
동형). 문서 쪽 갱신은 [[vinc.mcp.skeleton#§9]] 델타 참조.

MERGE 판별(created vs matched)은 MERGE 전에 MATCH 로 존재를 먼저 확인한다 — Kùzu
드라이버가 create/match 여부를 별도 반환하지 않기 때문(원본 엔진들은 이 신호를 아예
쓰지 않았다 — D11 이 신규로 요구하는 집계다).
"""
from __future__ import annotations

from typing import Any

# node.type → (label, id 컬럼, 세팅 가능한 D11 필드). record 는 대응 노드 테이블이
# 없어 미지원(warnings). Domain 의 PK 는 실제로 `name`(D11 이 가정하는 `domain_id`
# 아님) — Bridge 의 CROSSES 관계가 이미 이 컬럼에 의존한다(bridges_to_neo4j.py).
_NODE_SPECS: dict[str, dict[str, Any]] = {
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

# edge.type → (rel 라벨, 허용된 (from_type, to_type) 집합) — store/embedded_schema.py
# _REL_TABLES 에 실제 선언된 조합만. inherits_from 은 대응 관계 테이블이 없어 미지원.
_EDGE_SPECS: dict[str, dict[str, Any]] = {
    "relates_to": {"rel": "RELATED_TO", "pairs": {("concept", "concept")}},
    "prerequisite_of": {"rel": "PREREQUISITE_OF", "pairs": {("concept", "concept")}},
    "derives_from": {"rel": "DERIVES_FROM", "pairs": {("concept", "concept")}},
    "connects_via": {"rel": "CONNECTS", "pairs": {("bridge", "concept")}},
}

DEFAULT_OWNER = "tenant:local"


def _merge_node(session, ntype: Any, node: dict, *, owner: str,
                 warnings: list[str]) -> tuple[str, bool] | None:
    """단일 노드 MERGE. 반환 (id, was_existing) 또는 스킵 시 None(경고는 누적)."""
    nid = node.get("id")
    title = node.get("title")
    spec = _NODE_SPECS.get(ntype)

    if spec is None:
        warnings.append(f"node.type not supported in current schema: {ntype!r} (id={nid!r})")
        return None
    if not nid or not isinstance(nid, str):
        warnings.append(f"node missing/invalid id (type={ntype})")
        return None
    if not title or not isinstance(title, str):
        warnings.append(f"node.title required: id={nid}")
        return None

    label, id_col, settable = spec["label"], spec["id_col"], spec["settable"]

    if node.get("domain") is not None and "domain" not in settable:
        warnings.append(f"node.domain not supported for type={ntype} (id={nid})")
    if node.get("tags") and "tags" not in settable:
        warnings.append(f"node.tags not supported for type={ntype} (id={nid})")
    if node.get("props"):
        warnings.append(f"node.props not persisted — no schema-free column for type={ntype} (id={nid})")

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


def _merge_edge(session, edge: dict, node_types: dict[str, Any], warnings: list[str]) -> bool:
    efrom, eto, etype = edge.get("from"), edge.get("to"), edge.get("type")
    spec = _EDGE_SPECS.get(etype)
    if spec is None:
        warnings.append(f"edge.type not supported in current schema: {etype!r}")
        return False

    ft, tt = node_types.get(efrom), node_types.get(eto)
    if ft is None or tt is None:
        warnings.append(f"edge endpoint not declared in fragment.nodes: {efrom!r} -> {eto!r}")
        return False
    if (ft, tt) not in spec["pairs"]:
        warnings.append(f"edge.type {etype!r} unsupported for node-type pair ({ft}, {tt})")
        return False

    from_spec, to_spec = _NODE_SPECS[ft], _NODE_SPECS[tt]
    session.run(
        f"MATCH (a:{from_spec['label']} {{{from_spec['id_col']}: $from_id}}), "
        f"(b:{to_spec['label']} {{{to_spec['id_col']}: $to_id}}) "
        f"MERGE (a)-[:{spec['rel']}]->(b)",
        from_id=efrom, to_id=eto,
    )
    return True


def merge_fragment(session, fragment: dict) -> dict:
    """[[vinc.mcp#§5]] fragment 를 MERGE. 반환은 D11 §4 계약과 1:1 —
    `merged.nodes.{created,matched}` · `merged.edges.created` · `warnings`."""
    warnings: list[str] = []
    owner = fragment.get("owner") or DEFAULT_OWNER
    node_types: dict[str, Any] = {}
    created = matched = edges_created = 0

    for node in fragment.get("nodes") or []:
        res = _merge_node(session, node.get("type"), node, owner=owner, warnings=warnings)
        if res is None:
            continue
        nid, was_existing = res
        node_types[nid] = node.get("type")
        if was_existing:
            matched += 1
        else:
            created += 1

    for edge in fragment.get("edges") or []:
        if _merge_edge(session, edge, node_types, warnings):
            edges_created += 1

    return {
        "merged": {"nodes": {"created": created, "matched": matched},
                   "edges": {"created": edges_created}},
        "warnings": warnings,
    }


def ingest_fragment(store, fragment: dict) -> dict:
    """사이드카 잡 큐 진입점(`api/server.py` `VincApp.write_ingest`). writer 세션으로
    MERGE. store 미도달(드라이버 미설치) 시 강등(무 크래시, [[vinc.c.dataplane#§1]])."""
    if store is None:
        return {"executed": False,
                "merged": {"nodes": {"created": 0, "matched": 0}, "edges": {"created": 0}},
                "warnings": ["no reachable store"]}
    with store.session() as session:
        result = merge_fragment(session, fragment)
    result["executed"] = True
    return result
