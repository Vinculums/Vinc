# -*- coding: utf-8 -*-
"""MCP graph fragment MERGE — `vinc_ingest` 의 서버측 계약(D11 §5) 구현.

상류(사용자의 Claude)가 저작한 fragment(nodes+edges)를 **id 정확 매칭**으로만
MERGE 한다 — 유사도·의미 판단은 하지 않는다(하이브리드 c 백스톱, D11 §3).
enum 밖 노드/엣지 타입은 warnings 로 반환하고 스킵한다(무단 확장 금지, §5).

지원(스키마 §5-2 확장, 2026-07-10):
- 노드 5종: record | concept | domain | bridge | episode  (+ 자유 props{} → JSON 문자열 열)
- 엣지: Concept→Concept 4종(relates_to·prerequisite_of·derives_from·inherits_from),
  connects_via(Bridge→Concept → CONNECTS), Record→Concept 는 ATTACHES_TO{type}로
  원 타입을 보존해 수용(임베디드 rel 테이블의 FROM/TO 고정 제약 우회).
"""
from __future__ import annotations

import json

_NODE_MAP = {   # fragment type → (label, pk column)
    "record": ("Record", "record_id"),
    "concept": ("Concept", "concept_id"),
    "domain": ("Domain", "name"),
    "bridge": ("Bridge", "bridge_id"),
    "episode": ("Episode", "episode_id"),
}

_CC_EDGES = {   # Concept→Concept fragment 엣지 → rel 테이블
    "relates_to": "RELATED_TO",
    "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM",
    "inherits_from": "INHERITS_FROM",
}
_EDGE_TYPES = set(_CC_EDGES) | {"connects_via"}

DEFAULT_OWNER = "tenant:local"


def _merge_node(store, node: dict, owner: str, warnings: list[str],
                *, owner_explicit: bool = False) -> str | None:
    """단일 노드 MERGE. 반환 = 'created' | 'matched' | None(스킵).

    owner_id 스탬핑은 **생성 시 또는 fragment.owner 명시 시**에만 한다 — owner 없는
    재-MERGE 가 기존 노드의 소유를 기본값으로 조용히 덮어쓰면 스코프 필터(§1 불변)가
    깨진다."""
    ntype = (node.get("type") or "").strip()
    nid = (node.get("id") or "").strip()
    if ntype not in _NODE_MAP:
        warnings.append(f"unsupported node type '{ntype}' — skipped ({nid or '?'})")
        return None
    if not nid:
        warnings.append(f"node without id — skipped (type={ntype})")
        return None
    label, pk = _NODE_MAP[ntype]

    exists = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN count(n) AS c",
                       {"id": nid})
    matched = bool(exists and exists[0].get("c"))

    sets, params = [], {"id": nid}
    def _set(col, val):
        sets.append(f"n.{col} = ${col}")
        params[col] = val

    if label != "Domain":
        _set("title", node.get("title") or nid)
        if node.get("domain") is not None:
            _set("domain", str(node["domain"]))
        if node.get("tags") is not None:
            _set("tags", [str(t) for t in (node.get("tags") or [])])
        if node.get("props") is not None:
            _set("props", json.dumps(node["props"], ensure_ascii=False, sort_keys=True))
        if label == "Episode":
            # share pack(D19)의 레코드 노드 — 콘텐츠 필드만(선호 신호는 팩에 없음).
            if node.get("date") is not None:
                _set("date", str(node["date"]))
            if node.get("summary") is not None:
                _set("summary", str(node["summary"]))
        if label in ("Record", "Concept", "Episode") and (owner_explicit or not matched):
            _set("owner_id", owner)
        if label == "Record":
            _set("tenant_id", "tenant:local")
    elif node.get("props") is not None:
        warnings.append(f"domain node '{nid}' — props{{}} 미보존(열 없음)")

    set_clause = (" SET " + ", ".join(sets)) if sets else ""
    store.run(f"MERGE (n:{label} {{{pk}: $id}}){set_clause}", params)
    return "matched" if matched else "created"


def _node_types(fragment_nodes: list[dict]) -> dict[str, str]:
    return {(n.get("id") or "").strip(): (n.get("type") or "").strip()
            for n in fragment_nodes}


def _merge_edge(store, edge: dict, types: dict[str, str], warnings: list[str]) -> bool:
    etype = (edge.get("type") or "").strip()
    src, dst = (edge.get("from") or "").strip(), (edge.get("to") or "").strip()
    if etype not in _EDGE_TYPES:
        warnings.append(f"unsupported edge type '{etype}' — skipped ({src}→{dst})")
        return False
    st, dt = types.get(src, ""), types.get(dst, "")

    if etype == "connects_via":
        if (st, dt) != ("bridge", "concept"):
            warnings.append(f"connects_via expects bridge→concept — skipped ({st}→{dt})")
            return False
        store.run(
            "MATCH (a:Bridge), (b:Concept) WHERE a.bridge_id = $s AND b.concept_id = $d "
            "MERGE (a)-[:CONNECTS]->(b)", {"s": src, "d": dst})
        return True
    if (st, dt) == ("concept", "concept"):
        rel = _CC_EDGES[etype]
        store.run(
            "MATCH (a:Concept), (b:Concept) WHERE a.concept_id = $s AND b.concept_id = $d "
            f"MERGE (a)-[:{rel}]->(b)", {"s": src, "d": dst})
        return True
    if (st, dt) == ("record", "concept"):
        # Record 참여 엣지 — ATTACHES_TO{type}로 원 타입 보존(스키마 §5-2 주석).
        store.run(
            "MATCH (a:Record), (b:Concept) WHERE a.record_id = $s AND b.concept_id = $d "
            "MERGE (a)-[r:ATTACHES_TO]->(b) SET r.type = $t",
            {"s": src, "d": dst, "t": etype})
        return True
    warnings.append(f"edge '{etype}' between {st or '?'}→{dt or '?'} 미지원 — skipped")
    return False


def merge_fragment(fragment: dict, *, store) -> dict:
    """fragment 전체 MERGE. 반환 = D11 §4 계약:
    ``{merged:{nodes:{created,matched}, edges:{created}}, warnings:[…]}``."""
    warnings: list[str] = []
    created = matched = e_created = 0
    if store is None:
        return {"merged": None, "warnings": ["no reachable store"], "executed": False}
    nodes = list(fragment.get("nodes") or [])
    edges = list(fragment.get("edges") or [])
    owner_explicit = bool((fragment.get("owner") or "").strip())
    owner = (fragment.get("owner") or "").strip() or DEFAULT_OWNER
    for n in nodes:
        r = _merge_node(store, n, owner, warnings, owner_explicit=owner_explicit)
        created += (r == "created")
        matched += (r == "matched")
    types = _node_types(nodes)
    for e in edges:
        e_created += bool(_merge_edge(store, e, types, warnings))
    return {"merged": {"nodes": {"created": created, "matched": matched},
                       "edges": {"created": e_created}},
            "warnings": warnings, "executed": True}
