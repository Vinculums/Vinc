from __future__ import annotations

from . import pkns


def blocked_sync_references(store, *, owner: str, payload: dict) -> list[str]:
    """Permanent sync move barriers beat timestamps, including future offline edits.

    This is an ingress fence, not an owner alias or a forwarding permission.
    Other write ingresses need qualification before enabling transfers.
    """
    refs = set()
    fields = {"nodes": ("id",), "edges": ("from", "to"),
              "tombstones": ("node_id", "doc_id"), "edge_tombstones": ("src", "dst"),
              "docs": ("doc_id",)}
    for group, names in fields.items():
        for row in payload.get(group) or []:
            for name in names:
                value = row.get(name)
                if isinstance(value, str) and value:
                    refs.add(pkns.strip(value))
    if not refs:
        return []
    prefix = "topic-transfer:moved:" + owner + ":"
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key STARTS WITH $prefix RETURN m.key AS key",
                     {"prefix": prefix})
    moved = {r["key"][len(prefix):].split(":", 1)[1] for r in rows
             if ":" in r["key"][len(prefix):]}
    return sorted(refs & moved)
