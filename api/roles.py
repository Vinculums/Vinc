"""소유한 역할의 계약과 협업 엣지만 읽는다. 렌더링은 MCP 계층이 맡는다."""
from __future__ import annotations

from store import pkns
from store.backend import open_store
from store.config import load_config

from . import query as q, role_contract


def _summaries(rows: list[dict]) -> dict:
    out = {}
    for row in rows:
        nid = pkns.strip(row["id"])
        props = q._props_to_obj(row.get("props"))
        if role_contract.is_role(nid, row.get("domain"), props):
            out[nid] = {**row, "id": nid, "mission": row.get("mission") or props.get("mission") or "",
                        "props": {k: props[k] for k in ("job_title", "team", "team_role") if k in props}}
    return out


_COLUMNS = ("RETURN n.concept_id AS id, n.title AS title, n.domain AS domain, "
            "n.one_liner AS mission, n.props AS props")


def read(role: str | None = None, *, owner: str | None = None, store=None, cfg=None,
         limit: int | None = None, offset: int = 0, roster: bool = False) -> dict:
    """슬러그 또는 정확한 id 한 개. 미지정이면 계약 전문 없는 로스터를 돌려준다."""
    base = {"kind": "role" if role else "roles", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    if roster and limit is None:
        limit = 100
    if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            or limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                                     or not 1 <= limit <= (100 if roster else 1000))):
        return {**base, "error": "invalid role pagination"}
    rid = str(role or "").strip()
    if rid and not rid.startswith("concept:"):
        rid = "concept:" + rid
    if rid and not role_contract.is_role_id(rid):
        return {**base, "executed": True, "found": False}
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        return {**base, "note": "no reachable store"}
    try:
        params = {"owner": owner, "prefix": pkns.key(owner, role_contract.ID_PREFIX)}
        guard = "n.concept_id = $id" if rid else "starts_with(n.concept_id, $prefix)"
        tail = " ORDER BY n.concept_id"
        if rid:
            params["id"] = pkns.key(owner, rid)
        elif limit is not None:
            params.update(offset=offset, fetch=limit + 1)
            tail += " SKIP $offset LIMIT $fetch"
        result = q.execute(q.QueryPlan("roles", "MATCH (n:Concept) WHERE n.owner_id = $owner AND "
            + guard + " " + _COLUMNS + tail, params), cfg, store=store)
        if not result["executed"]:
            return {**base, "error": result.get("error"), "note": result.get("note", "")}
        candidates = result["rows"]
        more = not rid and limit is not None and len(candidates) > limit
        if not rid and limit is not None:
            candidates = candidates[:limit]
        indexed = _summaries(candidates)
        rows = sorted(indexed.values(), key=lambda r: r["id"])
        if rid and rid not in indexed:
            return {**base, "executed": True, "found": False}
        if not rid:
            page = rows if limit is not None else rows[offset:]
            out = {**base, "executed": True, "roles": page, "count": len(page)}
            if limit is not None:
                # 자격이 없는 후보도 페이지 위치를 차지한다. 빈 페이지 뒤에도 계속 읽는다.
                out["next_offset"] = offset + len(candidates) if more else None
            if not roster or not page:
                return {**out, **({"relations": []} if roster else {})}
            edges = q.node_edges(nodes=[r["id"] for r in page], owner=owner,
                                 limit=5000, cfg=cfg, store=store)
        else:
            node = q.node(rid, owner=owner, cfg=cfg, store=store)
            if not node.get("executed") or not node.get("found"):
                return {**base, **{k: v for k, v in node.items() if k != "kind"}}
            edges = q.node_edges(rid, owner=owner, limit=5000, cfg=cfg, store=store)
        if not edges.get("executed"):
            return {**base, "error": edges.get("error"), "note": edges.get("note", "")}
        if edges.get("edges_truncated"):
            return {**base, "error": "role relation read exceeded the edge limit"}
        peer_ids = {e[k] for e in edges["edges"] for k in ("from", "to")
                    if role_contract.is_role_id(e[k]) and e[k] not in indexed}
        if peer_ids:
            peers_result = q.execute(q.QueryPlan("roles", "MATCH (n:Concept) WHERE "
                "n.owner_id = $owner AND n.concept_id IN $ids " + _COLUMNS,
                {"owner": owner, "ids": [pkns.key(owner, n) for n in sorted(peer_ids)]}), cfg, store=store)
            if not peers_result["executed"]:
                return {**base, "error": peers_result.get("error"), "note": peers_result.get("note", "")}
            indexed.update(_summaries(peers_result["rows"]))
        relations = [e for e in edges["edges"] if e["from"] in indexed and e["to"] in indexed
                     and e["type"] in ("PREREQUISITE_OF", "RELATED_TO", "RETURNS_TO")]
        if not rid:
            return {**out, "relations": relations}
        peers = sorted({e["from"] for e in relations} | {e["to"] for e in relations})
        return {**base, "executed": True, "found": True, "node": node["node"],
                "relations": relations, "peers": [indexed[n] for n in peers]}
    finally:
        if owns:
            store.close()
