"""삭제·병합 대상의 제한된 미리보기. 상태가 바뀌면 확인한 적용을 거절한다."""
from __future__ import annotations

import hashlib

from api import docstore, query
from store import canon, pkns
from store.querybase import DOC_MIRROR_PREFIX, doc_id_of, mirror_id
from store.embedded_schema import node_table_specs, rel_table_specs

MAX_ROWS = 200
MAX_BYTES = 48000


class PreviewRefused(ValueError):
    pass


_canonical = canon.canonical_json


def _node(store, owner, ident):
    result = query.execute(query.node_plan(ident, owner=owner), store=store)
    if not result.get("executed") or result.get("rows") is None:
        raise PreviewRefused("preview_read_unavailable")
    if not result.get("rows"):
        raise PreviewRefused("preview_target_unavailable")
    return result["rows"][0]


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


def _stray(store, owner, ident, stray):
    # 사본 행만 지운다. 묘비·벡터·동기화 base 는 살아 있는 행의 것이라 남는다.
    table = query._SEED_TABLES.get(stray) if isinstance(stray, str) else None
    if table is None or table == query._SEED_TABLES[ident.partition(":")[0]]:
        raise PreviewRefused("preview_kind_not_supported")
    label, pk = table
    rows = _rows(store, label, f"n.{pk}=$id AND n.owner_id=$owner",
                 {"id": pkns.key(owner, ident), "owner": owner})
    if not rows:
        raise PreviewRefused("preview_target_unavailable")
    edges = _incident(store, owner, [ident], label_override=label)
    return {"deleted_nodes": [{**row, pk: pkns.strip(row[pk])} for row in rows],
            "deleted_edges": edges, "retained_documents": [], "deleted_shadows": {},
            "stray_type": stray, "node": 1, "edge_count": len(edges), "complete": True,
            "edges": [{"from": e["source"], "to": e["target"], "type": e["type"],
                       "props": e["props"]} for e in edges]}


#: 응답이 다음 할 일을 말한다. 도구 설명은 짧은 요약이라 미리보기 → 적용 절차를 싣지 않는다.
#: 해시(preview_digest)는 `preview` 내용으로만 계산하므로 이 문장들은 해시에 들지 않는다.
#: 근거: [[decision:mcp-tool-descriptions-are-short-summaries-with-a-docs-link]]
NEXT_STEP = ("Nothing was changed. Show this preview to the person; to apply it, repeat the same call "
             "without dry_run and with this preview_digest.")
HINTS = {
    "preview_conflict": "Nothing was changed: the target changed after your preview. Run the call again "
                        "with dry_run:true and show the new preview before applying.",
    "preview_incomplete": "Nothing was changed: a complete preview would exceed 200 rows or the size "
                          "budget, so it was refused rather than cut. Narrow the target.",
    "preview_target_unavailable": "Nothing was changed: the target does not exist in this graph or "
                                  "cannot be read here. Check the id.",
    "preview_apply_not_supported": "Nothing was changed: drop_id has relations this merge cannot carry "
                                   "over (unsupported_edges in the preview).",
    "preview_kind_not_supported": "Nothing was changed: this kind of target cannot be changed with "
                                  "this tool.",
    "preview_read_unavailable": "Nothing was changed: the store could not be read completely. Try the "
                                "preview again shortly.",
    "preview_boundary_unavailable": "Nothing was changed: the store could not be read completely. Try "
                                    "the preview again shortly.",
    "preview_owner_required": "Nothing was changed: this call has no owner.",
}


def _guided(result):
    if result.get("executed") and result.get("preview_complete"):
        return {**result, "next_step": NEXT_STEP}
    hint = HINTS.get(result.get("error"))
    return {**result, "hint": hint} if hint else result


def preview(store, *, owner, operation, arguments):
    return _guided(_preview_body(store, owner=owner, operation=operation, arguments=arguments))


def _preview_body(store, *, owner, operation, arguments):
    base = {"kind": operation, "executed": False, "dry_run": True, "preview_complete": False}
    if not owner:
        return {**base, "error": "preview_owner_required"}
    try:
        details = {}
        if operation == "delete":
            ident = arguments["node"]
            if pkns.SEP in ident:
                # 물리 키의 구분자가 든 id 는 어떤 노드의 id 도 아니다. 읽기가 그것을 물리 키로 읽지 않게 거른다.
                raise PreviewRefused("preview_target_unavailable")
            if ident.startswith("domain:") or ident.partition(":")[0] not in query._SEED_TABLES:
                raise PreviewRefused("preview_kind_not_supported")
            if arguments.get("stray_type") is not None:
                details = _stray(store, owner, ident, arguments["stray_type"])
                payload = _canonical({"owner": owner, "operation": operation, "preview": details})
                if len(payload.encode("utf-8")) > MAX_BYTES:
                    raise PreviewRefused("preview_incomplete")
                return {**base, "executed": True, "preview_complete": True, "preview": details,
                        "preview_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
            target = _node(store, owner, ident)
            details = {"deleted_nodes": [target], "deleted_edges": _incident(store, owner, [ident]),
                       "retained_documents": [], "deleted_shadows": _shadows(store, owner, ident)}
            details.update(node=1, edges=[{"from": e["source"], "to": e["target"], "type": e["type"],
                "props": e["props"]} for e in details["deleted_edges"]],
                edge_count=len(details["deleted_edges"]), complete=True)
            if ident.startswith(DOC_MIRROR_PREFIX):
                retained = docstore.get_doc(store, doc_id_of(ident), owner=owner)
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
                "retained_mirror": None}
            mirror = query.execute(query.node_plan(mirror_id(ident), owner=owner), store=store)
            if not mirror.get("executed") or mirror.get("rows") is None:
                raise PreviewRefused("preview_read_unavailable")
            details["retained_mirror"] = mirror["rows"][0] if mirror["rows"] else None
        elif operation == "delete_edge":
            source, target = arguments["from"], arguments["to"]
            _node(store, owner, source)
            _node(store, owner, target)
            if source.startswith("domain:") or target.startswith("domain:"):
                raise PreviewRefused("preview_kind_not_supported")
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
            def supported(edge):
                pair = (edge["source_kind"], edge["target_kind"])
                return (edge["type"] in query._CC_RELS and pair == ("Concept", "Concept")
                    or edge["type"] == "ATTACHES_TO" and pair == ("Record", "Concept")
                    or edge["type"] == "CONNECTS" and pair == ("Bridge", "Concept")
                    or edge["type"] == "IN_DOMAIN" and pair == ("Concept", "Domain"))
            unsupported = [e for e in edges if drop in (e["source"], e["target"]) and not supported(e)]
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
        return {"executed": False, "error": "preview_conflict", "status": "conflict",
                "hint": HINTS["preview_conflict"]}
    if current["preview"].get("apply_supported") is False:
        return {"executed": False, "error": "preview_apply_not_supported",
                "hint": HINTS["preview_apply_not_supported"]}
    return apply()
