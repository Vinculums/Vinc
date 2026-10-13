"""삭제·병합 대상의 제한된 미리보기. 상태가 바뀌면 확인한 적용을 거절한다."""
from __future__ import annotations

import hashlib
import json

from api import docstore, query
from store import pkns
from store.embedded_schema import node_table_specs, rel_table_specs

MAX_ROWS = 200
MAX_BYTES = 48000


class PreviewRefused(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _node(store, owner, ident):
    result = query.node(ident, owner=owner, store=store)
    if not result.get("executed"):
        raise PreviewRefused("preview_read_unavailable")
    if not result.get("found"):
        raise PreviewRefused("preview_target_unavailable")
    return result["node"]


def _incident(store, owner, identities, *, label_override=None):
    specs = node_table_specs()
    grouped = {}
    for ident in identities:
        label, _ = query._seed_table(ident)
        label = label_override or label
        grouped.setdefault(label, []).append(ident if label in ("Chunk", "ChunkVec") else pkns.key(owner, ident))
    rows = []
    for spec in rel_table_specs():
        a, b = spec["from"], spec["to"]
        if a not in grouped and b not in grouped:
            continue
        a_owned = "owner_id" in specs[a]["cols"]
        b_owned = "owner_id" in specs[b]["cols"]
        fields = [f"a.{specs[a]['pk']} AS source", f"b.{specs[b]['pk']} AS target",
                  "a.owner_id AS ao" if a_owned else "'' AS ao",
                  "b.owner_id AS bo" if b_owned else "'' AS bo"]
        fields += [f"r.{prop} AS p_{prop}" for prop in spec["props"]]
        parts = []
        params = {}
        if a in grouped:
            parts.append(f"a.{specs[a]['pk']} IN $a")
            params["a"] = grouped[a]
        if b in grouped:
            parts.append(f"b.{specs[b]['pk']} IN $b")
            params["b"] = grouped[b]
        found = store.run(f"MATCH (a:{a})-[r:{spec['label']}]->(b:{b}) WHERE ("
                          + " OR ".join(parts) + ") RETURN " + ", ".join(fields)
                          + f" LIMIT {MAX_ROWS - len(rows) + 1}", params)
        if found is None:
            raise PreviewRefused("preview_read_unavailable")
        for row in found:
            if (a_owned and row["ao"] != owner) or (b_owned and row["bo"] != owner):
                raise PreviewRefused("preview_boundary_unavailable")
            rows.append({"type": spec["label"], "source": row["source"], "target": row["target"],
                         "source_kind": a, "target_kind": b,
                         "props": {prop: row["p_" + prop] for prop in spec["props"]}})
            if len(rows) > MAX_ROWS:
                raise PreviewRefused("preview_incomplete")
    return sorted(rows, key=_canonical)


def _rows(store, table, where, params):
    from store.embedded_schema import meta_table_specs
    spec = {**node_table_specs(), **meta_table_specs()}[table]
    fields = ", ".join(f"n.{col} AS {col}" for col in spec["cols"])
    rows = store.run(f"MATCH (n:{table}) WHERE {where} RETURN {fields} LIMIT 201", params)
    if rows is None:
        raise PreviewRefused("preview_read_unavailable")
    if len(rows) > MAX_ROWS:
        raise PreviewRefused("preview_incomplete")
    return sorted(rows, key=_canonical)


def _fingerprint(rows):
    return {"count": len(rows), "state_hash": hashlib.sha256(_canonical(rows).encode()).hexdigest()}


def _shadows(store, owner, ident):
    from api import node_mirror
    return {
        "node_vectors": _fingerprint(_rows(store, "NodeVec", "n.vec_id=$id",
            {"id": node_mirror.vec_id(owner, ident)})),
        "sync_base": _fingerprint(_rows(store, "_SyncBase", "n.node_id=$id",
            {"id": pkns.key(owner, ident)})),
    }


def preview(store, *, owner, operation, arguments):
    base = {"kind": operation, "executed": False, "dry_run": True, "preview_complete": False}
    if not owner:
        return {**base, "error": "preview_owner_required"}
    try:
        details = {}
        if operation == "delete":
            ident = arguments["node"]
            if ident.startswith("domain:"):
                raise PreviewRefused("preview_kind_not_supported")
            target = _node(store, owner, ident)
            details = {"deleted_nodes": [target], "deleted_edges": _incident(store, owner, [ident]),
                       "retained_documents": [], "deleted_shadows": _shadows(store, owner, ident)}
            details.update(node=1, edges=[{"from": e["source"], "to": e["target"], "type": e["type"],
                "props": e["props"]} for e in details["deleted_edges"]],
                edge_count=len(details["deleted_edges"]), complete=True)
            if ident.startswith("concept:doc."):
                retained = docstore.get_doc(store, ident[len("concept:doc."):], owner=owner)
                if retained.get("executed"):
                    details["retained_documents"] = [{"doc_id": retained["doc_id"],
                        "content_hash": retained.get("content_hash"), "chunks": retained.get("chunks")}]
        elif operation == "doc.delete":
            ident = arguments["id"]
            got = docstore.get_doc(store, ident, owner=owner)
            if not got.get("executed"):
                raise PreviewRefused("preview_target_unavailable")
            if not got.get("verified") and (got.get("text") or got.get("chunks")):
                raise PreviewRefused("preview_read_unavailable")
            params = {"doc": ident, "owner": owner}
            chunks = _rows(store, "Chunk", "n.doc_id=$doc", params)
            vectors = _rows(store, "ChunkVec", "n.doc_id=$doc", params)
            if any(row.get("owner_id") != owner for row in chunks + vectors):
                raise PreviewRefused("preview_boundary_unavailable")
            if chunks is None or len(chunks) > MAX_ROWS or len(chunks) != got.get("chunks"):
                raise PreviewRefused("preview_incomplete")
            details = {"deleted_document": {"doc_id": ident, "title": got.get("title"),
                "content_hash": got.get("content_hash"), "characters": len(got.get("text") or ""),
                "excerpt": (got.get("text") or "")[:160], "chunks": len(chunks)},
                "deleted_edges": _incident(store, owner, [r["chunk_id"] for r in chunks], label_override="Chunk"),
                "deleted_state": {
                    "metadata": _fingerprint(_rows(store, "_DocMeta", "n.doc_id=$doc AND n.owner_id=$owner", params)),
                    "chunks": _fingerprint(chunks),
                    "vectors": _fingerprint(vectors),
                },
                "retained_mirror": query.node("concept:doc." + ident, owner=owner, store=store).get("node")}
        elif operation == "delete_edge":
            source, target = arguments["from"], arguments["to"]
            _node(store, owner, source)
            _node(store, owner, target)
            raw_type = arguments["type"].strip().lower()
            rel = query._FRAGMENT_EDGE_TO_REL.get(raw_type, raw_type.upper())
            subtype = None
            if source.startswith("record:") and raw_type in {
                    "relates_to", "prerequisite_of", "derives_from", "inherits_from"}:
                subtype, rel = raw_type, "ATTACHES_TO"
            if rel not in query.SYNC_EDGE_RELS:
                raise PreviewRefused("preview_kind_not_supported")
            pairs = [(source, target)] + ([(target, source)] if rel == "RELATED_TO" else [])
            edges = [e for e in _incident(store, owner, [source, target])
                     if (e["source"], e["target"]) in pairs and e["type"] == rel
                     and (subtype is None or e["props"].get("type") == subtype)]
            details = {"deleted_edges": edges, "retained_nodes": [source, target]}
        elif operation == "merge_nodes":
            keep, drop = arguments["keep"], arguments["drop"]
            if not keep.startswith("concept:") or not drop.startswith("concept:") or keep == drop:
                raise PreviewRefused("preview_kind_not_supported")
            a, b = _node(store, owner, keep), _node(store, owner, drop)
            edges = _incident(store, owner, [keep, drop])
            # 기존 병합의 재배선 목록 밖인 관계는 잃을 수 있다. 미리보기 적용은 거절한다.
            supported = {"RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM",
                         "ATTACHES_TO", "CONNECTS", "IN_DOMAIN"}
            unsupported = [e for e in edges if drop in (e["source"], e["target"]) and e["type"] not in supported]
            details = {"kept_node": a, "discarded_fields": b, "affected_edges": edges,
                       "unsupported_edges": unsupported, "apply_supported": not unsupported,
                       "discarded_edge_properties": [e for e in edges if drop in (e["source"], e["target"])
                           and any(v is not None for k, v in e["props"].items()
                                   if k not in ({"type"} if e["type"] == "ATTACHES_TO" else
                                                {"role"} if e["type"] == "CONNECTS" else set()))],
                       "merge_policy": "Keep fields win. Self loops and duplicate destinations are skipped. "
                           "Drop topic membership is removed. Rewired edges retain only attachment type or bridge role; "
                           "other edge properties shown here are discarded by the existing merge.",
                       "deleted_shadows": _shadows(store, owner, drop)}
        else:
            raise PreviewRefused("preview_kind_not_supported")
        payload = _canonical({"owner": owner, "operation": operation, "preview": details})
        if len(payload.encode("utf-8")) > MAX_BYTES:
            raise PreviewRefused("preview_incomplete")
        return {**base, "executed": True, "preview_complete": True, "preview": details,
                "preview_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
    except PreviewRefused as exc:
        return {**base, "error": str(exc)}
    except Exception:
        return {**base, "error": "preview_read_unavailable"}


def apply_checked(store, *, owner, operation, arguments, digest, apply):
    current = preview(store, owner=owner, operation=operation, arguments=arguments)
    if not current.get("executed"):
        return {**current, "dry_run": False}
    if current["preview_digest"] != digest:
        return {"executed": False, "error": "preview_conflict", "status": "conflict"}
    if current["preview"].get("apply_supported") is False:
        return {"executed": False, "error": "preview_apply_not_supported"}
    return apply()
