"""소유한 역할의 계약과 협업 엣지만 읽는다. 렌더링은 MCP 계층이 맡는다."""
from __future__ import annotations

from engines.domain_hierarchy import normalize
from store import pkns
from store.backend import open_store
from store.config import load_config

from . import query as q, role_contract


def _role(rid: str, domain: str) -> bool:
    d = normalize(domain)
    return role_contract.is_role_id(rid) and any(
        d == root or (d.startswith(root + "/") and d.count("/") == root.count("/") + 1)
        for root in role_contract.TOPICS)


def read(role: str | None = None, *, owner: str | None = None, store=None, cfg=None) -> dict:
    """슬러그 또는 정확한 id 한 개. 미지정이면 계약 전문 없는 로스터를 돌려준다."""
    base = {"kind": "role" if role else "roles", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
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
        result = q.execute(q.QueryPlan("roles", "MATCH (n:Concept) WHERE n.owner_id = $owner "
            "AND starts_with(n.concept_id, $prefix) "
            "RETURN n.concept_id AS id, n.title AS title, n.domain AS domain, "
            "n.one_liner AS mission", {"owner": owner, "prefix": pkns.key(owner, role_contract.ID_PREFIX)}), cfg, store=store)
        if not result["executed"]:
            return {**base, "error": result.get("error"), "note": result.get("note", "")}
        rows = [{**row, "id": pkns.strip(row["id"])} for row in result["rows"]]
        rows = sorted((row for row in rows if _role(row["id"], row["domain"])), key=lambda r: r["id"])
        if not rid:
            return {**base, "executed": True, "roles": rows, "count": len(rows)}
        indexed = {row["id"]: row for row in rows}
        if rid not in indexed:
            return {**base, "executed": True, "found": False}
        node = q.node(rid, owner=owner, cfg=cfg, store=store)
        if not node.get("executed") or not node.get("found"):
            return {**base, **{k: v for k, v in node.items() if k != "kind"}}
        edges = q.node_edges(rid, owner=owner, limit=5000, cfg=cfg, store=store)
        if not edges.get("executed"):
            return {**base, "error": edges.get("error"), "note": edges.get("note", "")}
        if edges.get("edges_truncated"):
            return {**base, "error": "role relation read exceeded the edge limit"}
        relations = [e for e in edges["edges"] if e["from"] in indexed and e["to"] in indexed
                     and e["type"] in ("PREREQUISITE_OF", "RELATED_TO")]
        peers = sorted({e["from"] for e in relations} | {e["to"] for e in relations})
        return {**base, "executed": True, "found": True, "node": node["node"],
                "relations": relations, "peers": [indexed[n] for n in peers]}
    finally:
        if owns:
            store.close()
