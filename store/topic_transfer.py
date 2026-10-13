from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from . import pkns
from .embedded_schema import (
    SCHEMA_VERSION, meta_table_specs, node_table_specs, rel_table_specs,
)
from .querybase import is_conventions_doc_id


class TransferRefused(ValueError):
    pass


MAX_INVENTORY_ROWS = 5000
MAX_INVENTORY_BYTES = 32 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def fingerprint(snapshot: dict) -> str:
    # 순서만 다른 행과 병렬 관계의 개수를 모두 보존한다.
    normalized = copy.deepcopy(snapshot)
    for group in ("nodes", "meta"):
        for rows in normalized[group].values():
            rows.sort(key=_canonical)
    normalized["edges"].sort(key=_canonical)
    return hashlib.sha256(_canonical(normalized).encode()).hexdigest()


def check_inventory_budget(snapshot, *, extra_rows=0, extra_bytes=0):
    count = len(snapshot["edges"]) + sum(len(rows) for group in ("nodes", "meta")
                                          for rows in snapshot[group].values())
    if count + extra_rows > MAX_INVENTORY_ROWS or len(_canonical(snapshot).encode()) + extra_bytes > MAX_INVENTORY_BYTES:
        raise TransferRefused("conversion_inventory_limit")


def verify_store_shape(store, *, vocabulary_owner: str | None = None) -> None:
    """Validate sidecar and every physical table before any scoped inventory."""
    path = getattr(store, "path", None) or getattr(getattr(store, "_cfg", None), "data_dir", None)
    if not path:
        raise TransferRefused("external_store_state_unchecked")
    # DB 밖 소유자 어휘를 이전하지 못하므로, 파일이 있으면 내용과 무관하게 거절한다.
    sidecar = Path(str(path) + ".themes.json")
    if sidecar.exists():
        if vocabulary_owner is None:
            raise TransferRefused("owner_vocabulary_sidecar_not_supported")
        from .theme_store import owner_theme_id
        try:
            vocabulary = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise TransferRefused("owner_vocabulary_sidecar_not_supported") from None
        # A file shared with other tenants is not itself a source dependency.
        # Unknown shapes and any vocabulary belonging to this source fail closed.
        if not isinstance(vocabulary, dict) or not isinstance(vocabulary.get("themes"), dict) \
                or owner_theme_id(vocabulary_owner) in vocabulary["themes"]:
            raise TransferRefused("owner_vocabulary_sidecar_not_supported")
    specs = {**node_table_specs(), **meta_table_specs()}
    tables = store.run("CALL show_tables() RETURN *", raw=True)
    have = {r["name"] for r in tables}
    expected = set(specs) | {r["label"] for r in rel_table_specs()}
    if have != expected:
        raise TransferRefused("unsupported_schema_tables")
    for label, spec in specs.items():
        cols = {r["name"] for r in store.run(f"CALL table_info('{label}') RETURN *", raw=True)}
        if cols != set(spec["cols"]):
            raise TransferRefused("unsupported_schema_columns:" + label)
    for spec in rel_table_specs():
        label = spec["label"]
        cols = {r["name"] for r in store.run(f"CALL table_info('{label}') RETURN *", raw=True)}
        if cols != set(spec["props"]):
            raise TransferRefused("unsupported_schema_columns:" + label)


def read_snapshot(store, *, vocabulary_owner: str | None = None,
                  max_rows: int = MAX_INVENTORY_ROWS, max_bytes: int = MAX_INVENTORY_BYTES) -> dict:
    """Strict, read-only canonical inventory for synthetic rehearsal."""
    verify_store_shape(store, vocabulary_owner=vocabulary_owner)
    specs = {**node_table_specs(), **meta_table_specs()}
    result = {"schema_version": SCHEMA_VERSION, "nodes": {}, "meta": {}, "edges": []}
    remaining_rows, remaining_bytes = max_rows, max_bytes
    def bounded(q):
        nonlocal remaining_rows, remaining_bytes
        rows = store.run(q + f" LIMIT {remaining_rows + 1}", raw=True)
        remaining_rows -= len(rows)
        remaining_bytes -= len(_canonical(rows).encode())
        if remaining_rows < 0 or remaining_bytes < 0:
            raise TransferRefused("conversion_inventory_limit")
        return rows
    for label, spec in specs.items():
        projection = ", ".join(f"n.{c} AS {c}" for c in spec["cols"])
        group = "meta" if label.startswith("_") else "nodes"
        result[group][label] = bounded(f"MATCH (n:{label}) RETURN {projection}")
    for spec in rel_table_specs():
        label, src, dst = spec["label"], spec["from"], spec["to"]
        fields = [f"a.{specs[src]['pk']} AS src", f"b.{specs[dst]['pk']} AS dst"]
        fields += [f"r.{c} AS {c}" for c in spec["props"]]
        rows = bounded(f"MATCH (a:{src})-[r:{label}]->(b:{dst}) RETURN {', '.join(fields)}")
        for row in rows:
            result["edges"].append({"label": label, "from_label": src, "to_label": dst,
                                    "from": row.pop("src"), "to": row.pop("dst"), "props": row})
    return result


@dataclass(frozen=True)
class Plan:
    source: str
    target: str
    topic: str
    before_hash: str
    selected: tuple[tuple[str, str], ...]
    documents: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def ready(self):
        return not self.blockers


_KINDS = ("Concept", "Record", "Decision", "Episode", "LearnerPattern")
_REFERENCE = re.compile(r"\[\[([^\]\s|]+)(?:[^\]]*)\]\]")
_ATTACHMENTS = {"attachment", "attachments", "file", "files", "blob", "blobs", "asset", "assets"}
_EXTERNAL_RESOURCE = re.compile(r"^(?:[A-Za-z]:[\\/]|(?:file|blob|s3|azure|gs|az|https?):|[/\\])", re.I)
_IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", re.I)


def referenced_ids(value):
    """Logical references covered by the lossless transfer boundary check."""
    if isinstance(value, dict):
        for key, val in value.items():
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            if compact in ("docid", "supersededby", "nodeid", "ref") and isinstance(val, str):
                yield pkns.strip(val)
            yield from referenced_ids(val)
    elif isinstance(value, list):
        for val in value:
            yield from referenced_ids(val)
    elif isinstance(value, str):
        yield from _REFERENCE.findall(value)


def _validate_snapshot(snapshot: dict) -> set[str]:
    expected = {"schema_version", "nodes", "meta", "edges"}
    if set(snapshot) != expected:
        raise TransferRefused("unsupported_snapshot_fields:" + ",".join(sorted(set(snapshot) ^ expected)))
    specs = {**node_table_specs(), **meta_table_specs()}
    if set(snapshot["nodes"]) != set(node_table_specs()) or set(snapshot["meta"]) != set(meta_table_specs()):
        raise TransferRefused("incomplete_inventory")
    blocks = set()
    for label, rows in {**snapshot["nodes"], **snapshot["meta"]}.items():
        if not isinstance(rows, list):
            raise TransferRefused("invalid_inventory_rows:" + label)
        wanted = set(specs[label]["cols"])
        for row in rows:
            if not isinstance(row, dict) or wanted - set(row):
                raise TransferRefused("incomplete_columns:" + label)
            if set(row) - wanted:
                blocks.add("incomplete_columns:" + label)
            if not isinstance(row[specs[label]["pk"]], str):
                raise TransferRefused("invalid_primary_key:" + label)
    relations = {(r["label"], r["from"], r["to"]): set(r["props"]) for r in rel_table_specs()}
    if not isinstance(snapshot["edges"], list):
        raise TransferRefused("invalid_relationship_inventory")
    for edge in snapshot["edges"]:
        if not isinstance(edge, dict) or set(edge) != {"label", "from_label", "to_label", "from", "to", "props"}:
            raise TransferRefused("unsupported_relationship_fields")
        key = (edge["label"], edge["from_label"], edge["to_label"])
        if key not in relations or not isinstance(edge["props"], dict):
            raise TransferRefused("unsupported_relationship_schema")
        if set(edge["props"]) != relations[key]:
            blocks.add("unsupported_relationship_columns:" + edge["label"])
    return blocks


def plan_transfer(snapshot: dict, *, source: str, target: str, topic: str) -> Plan:
    """Select one authored topic tree. Dependencies outside it are blockers, not additions."""
    blocks = _validate_snapshot(snapshot)
    if not source.startswith("user:") or pkns.SEP in source or not source[5:]:
        raise TransferRefused("personal_source_required")
    try:
        if not target.startswith("team:") or str(UUID(target[5:])) != target[5:]:
            raise ValueError
    except ValueError:
        raise TransferRefused("new_team_owner_required") from None
    if not topic or topic != topic.strip("/") or any(p in ("", ".", "..") for p in topic.split("/")):
        raise TransferRefused("explicit_topic_required")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise TransferRefused("unsupported_schema_version")
    specs = {**node_table_specs(), **meta_table_specs()}
    if set(snapshot["nodes"]) != set(node_table_specs()) or set(snapshot["meta"]) != set(meta_table_specs()):
        raise TransferRefused("incomplete_inventory")
    selected = set()
    within = lambda d: isinstance(d, str) and (d == topic or d.startswith(topic + "/"))
    allrows = {**snapshot["nodes"], **snapshot["meta"]}
    for label, rows in allrows.items():
        pk = specs[label]["pk"]
        ids = [r[pk] for r in rows]
        if len(set(ids)) != len(ids):
            blocks.add("duplicate_primary_key:" + label)
        for row in rows:
            if set(row) != set(specs[label]["cols"]):
                blocks.add("incomplete_columns:" + label)
            if row.get("owner_id") == target:
                blocks.add("target_not_empty")
            if row.get("owner_id") != source:
                continue
            if label in _KINDS and within(row.get("domain")):
                selected.add((label, row[pk]))
            if label == "Bridge" and (within(row.get("source_domain")) or within(row.get("target_domain"))):
                if within(row.get("source_domain")) and within(row.get("target_domain")):
                    selected.add((label, row[pk]))
                else:
                    blocks.add("bridge_crosses_topic:" + row[pk])
    if not selected:
        blocks.add("empty_topic")
    # owner별 관리 데이터만 있는 대상도 빈 팀으로 취급하지 않는다.
    for row in snapshot["meta"]["_SyncMeta"]:
        if target in row["key"]:
            blocks.add("target_has_metadata")
    if any(r["owner_id"] == source for r in snapshot["nodes"]["Entity"]):
        blocks.add("entity_topic_membership_not_supported")
    docs = {pkns.strip(nid)[len("concept:doc."):] for label, nid in selected
            if label == "Concept" and pkns.strip(nid).startswith("concept:doc.")}
    metas = {r["doc_id"]: r for r in snapshot["meta"]["_DocMeta"]}
    for doc in docs:
        if any(r["doc_id"] == doc for r in snapshot["meta"]["_DocTombstone"]):
            # This table's physical PK is global doc_id. A live document plus
            # historical tombstone cannot also retain a new source tombstone.
            blocks.add("document_tombstone_conflicts_with_live_doc:" + doc)
        if doc not in metas or metas[doc].get("owner_id") != source:
            blocks.add("document_owner_missing:" + doc)
            continue
        if is_conventions_doc_id(doc) or metas[doc].get("origin") == "vinc:conventions":
            blocks.add("owner_conventions_require_separate_decision:" + doc)
        successor = metas[doc].get("superseded_by")
        if successor and successor not in docs:
            blocks.add("external_document_successor:" + successor)
        chunks = sorted((r for r in snapshot["nodes"]["Chunk"] if r["doc_id"] == doc),
                        key=lambda r: r["chunk_index"])
        text = "".join(r["text"] or "" for r in chunks)
        offset = 0
        for i, chunk in enumerate(chunks):
            if (chunk["owner_id"] != source or chunk["chunk_index"] != i
                    or chunk["char_start"] != offset or chunk["char_end"] != offset + len(chunk["text"] or "")):
                blocks.add("document_chunks_not_intact:" + doc)
            offset = chunk["char_end"]
        if len(chunks) != metas[doc]["chunk_count"] or hashlib.sha256(text.encode()).hexdigest() != metas[doc]["content_hash"]:
            blocks.add("document_integrity_failed:" + doc)
        for label in ("Chunk", "ChunkVec"):
            for row in snapshot["nodes"][label]:
                if row["doc_id"] == doc:
                    if row["owner_id"] != source:
                        blocks.add("shared_document_owner:" + doc)
                    selected.add((label, row[specs[label]["pk"]]))
        selected.add(("_DocMeta", doc))
        chunk_keys = {r["chunk_id"] for r in chunks}
        for vector in snapshot["nodes"]["ChunkVec"]:
            if vector["doc_id"] == doc and vector["chunk_id"] not in chunk_keys:
                blocks.add("chunk_vector_join_mismatch:" + doc)
    logical = {pkns.strip(nid) for _, nid in selected}
    # Logical links have no relationship row. Moving their target must not
    # silently leave a different topic's text or structured references broken.
    for label, rows in allrows.items():
        if label not in _KINDS + ("Bridge", "Chunk", "_DocMeta"):
            continue
        for row in rows:
            if row.get("owner_id") != source:
                continue
            key = row[specs[label]["pk"]]
            props = row.get("props")
            fields = dict(row)
            if props:
                try:
                    fields["props"] = json.loads(props)
                except (TypeError, ValueError):
                    # Unclassified properties outside the scope can hide links.
                    blocks.add("opaque_props:" + key)
            refs = set(referenced_ids(fields))
            if (label, key) in selected:
                for ref in refs - (logical | docs):
                    blocks.add("external_reference:" + ref)
            elif refs & (logical | docs):
                blocks.add("incoming_reference:" + key)
    for edge in snapshot["edges"]:
        a, b = (edge["from_label"], edge["from"]), (edge["to_label"], edge["to"])
        if a not in selected and b not in selected:
            owned = any(r.get("owner_id") == source and r[specs[label]["pk"]] == key
                        for label, key in (a, b) for r in allrows[label])
            if owned and set(referenced_ids(edge["props"])) & (logical | docs):
                blocks.add("incoming_reference:relationship")
    for row in snapshot["nodes"]["NodeVec"]:
        if row["owner_id"] == source and row["node_id"] in logical:
            selected.add(("NodeVec", row["vec_id"]))
            if row["vec_id"] != source + "#" + row["node_id"]:
                blocks.add("unsupported_node_vector_key")
    for edge in snapshot["edges"]:
        a, b = (edge["from_label"], edge["from"]), (edge["to_label"], edge["to"])
        sa, sb = a in selected, b in selected
        if sa != sb:
            other = b if sa else a
            # Domain은 소유자 없는 구조 어휘다. 같은 토픽의 구조 관계만 유지한다.
            if other[0] != "Domain" or not within(other[1]) or edge["label"] not in ("IN_DOMAIN", "CROSSES"):
                blocks.add("boundary_relation:" + edge["label"] + ":" + pkns.strip(other[1]))
        if sa or sb:
            for ref in _REFERENCE.findall(str(edge["props"].get("evidence") or "")):
                if ref not in logical and ref not in docs:
                    blocks.add("external_reference:" + ref)
    for label, nid in tuple(selected):
        row = next(r for r in allrows[label] if r[specs[label]["pk"]] == nid)
        if "theme_id" in row and row["theme_id"]:
            blocks.add("owner_vocabulary_not_supported:" + nid)
        source_file = str(row.get("source_file") or "")
        if _EXTERNAL_RESOURCE.match(source_file) or "/" in source_file or "\\" in source_file:
            blocks.add("external_source_file_not_supported:" + nid)
        if "props" in row and row["props"]:
            try:
                props = json.loads(row["props"])
            except (TypeError, ValueError):
                blocks.add("opaque_props:" + nid)
                props = {}
            def inspect(value):
                if isinstance(value, dict):
                    for key, val in value.items():
                        compact = re.sub(r"[^a-z0-9]", "", key.lower())
                        if (compact in _ATTACHMENTS or compact in (
                                "attachmenturl", "attachmentpath", "asseturl", "bloburi",
                                "filepath", "filepaths", "fileurl")) and val:
                            blocks.add("external_attachments_not_supported:" + nid)
                        if compact in ("vocabulary", "themeid") and val:
                            blocks.add("owner_vocabulary_not_supported:" + nid)
                        if compact in ("graphmd", "conventions") and val:
                            blocks.add("owner_conventions_require_separate_decision:" + nid)
                        if compact in ("docid", "supersededby", "nodeid", "ref") and val and isinstance(val, str):
                            if val not in logical and val not in docs:
                                blocks.add("external_reference:" + val)
                        inspect(val)
                elif isinstance(value, list):
                    for val in value:
                        inspect(val)
                elif isinstance(value, str):
                    if _EXTERNAL_RESOURCE.match(value):
                        blocks.add("external_resource_reference_not_supported:" + nid)
                    for ref in _REFERENCE.findall(value):
                        if ref not in logical and ref not in docs:
                            blocks.add("external_reference:" + ref)
            inspect(props)
        if label == "Chunk":
            if _IMAGE_REFERENCE.search(row["text"] or ""):
                blocks.add("external_attachments_not_supported:" + nid)
            for ref in _REFERENCE.findall(row["text"] or ""):
                if ref not in logical and ref not in docs:
                    blocks.add("external_reference:" + ref)
    for row in snapshot["meta"]["_SyncBase"]:
        if row["node_id"] == source + pkns.SEP + pkns.strip(row["node_id"]) and pkns.strip(row["node_id"]) in logical:
            selected.add(("_SyncBase", row["node_id"]))
    # 이력은 추측해서 버리거나 다른 토픽과 함께 가져오지 않는다.
    source_logical = {pkns.strip(r[specs[label]["pk"]]) for label in _KINDS + ("Bridge",)
                      for r in snapshot["nodes"][label] if r["owner_id"] == source}
    for row in snapshot["meta"]["_MergeRejection"]:
        if row["owner_id"] == source:
            refs = {pkns.strip(row["a_id"] or ""), pkns.strip(row["b_id"] or "")}
            if refs & logical:
                blocks.add("merge_rejection_history_not_supported")
            elif not refs <= source_logical:
                blocks.add("merge_rejection_topic_membership_unknown")
    for label in ("_Tombstone", "_EdgeTombstone", "_DocTombstone"):
        for row in snapshot["meta"][label]:
            if row.get("owner_id") == source:
                refs = {pkns.strip(row.get(k) or "") for k in ("node_id", "src", "dst", "doc_id")}
                if refs & (logical | docs):
                    blocks.add("existing_tombstone_history_not_supported")
                else:
                    blocks.add("unclassified_tombstone_topic_history:" + label)
    for label, nid in selected:
        if label in _KINDS + ("Bridge",) and not nid.startswith(source + pkns.SEP):
            blocks.add("namespaced_node_key_required:" + nid)
    return Plan(source, target, topic, fingerprint(snapshot), tuple(sorted(selected)), tuple(sorted(docs)), tuple(sorted(blocks)))


def transformed_snapshot(snapshot: dict, plan: Plan, *, stamp: str) -> dict:
    """Pure transformation used by the synthetic transaction rehearsal, with no embeddings."""
    if not plan.ready:
        raise TransferRefused(";".join(plan.blockers))
    if fingerprint(snapshot) != plan.before_hash:
        raise TransferRefused("plan_stale")
    out = copy.deepcopy(snapshot)
    specs = {**node_table_specs(), **meta_table_specs()}
    selected, mapping = set(plan.selected), {}
    for label, nid in selected:
        if label == "NodeVec":
            row = next(r for r in out["nodes"][label] if r["vec_id"] == nid)
            new = plan.target + "#" + row["node_id"]
            if nid != plan.source + "#" + row["node_id"]:
                raise TransferRefused("unsupported_node_vector_key")
        else:
            new = plan.target + pkns.SEP + pkns.strip(nid) if nid.startswith(plan.source + pkns.SEP) else nid
        mapping[(label, nid)] = new
    seqs = {r["key"]: int(r["value"]) for r in out["meta"]["_SyncMeta"] if r["key"].startswith("seq:")}
    def seq(owner):
        key = "seq:" + owner
        seqs[key] = seqs.get(key, 0) + 1
        return seqs[key]
    for group in ("nodes", "meta"):
        for label, rows in out[group].items():
            pk = specs[label]["pk"]
            for row in rows:
                old = row[pk]
                if (label, old) not in selected:
                    continue
                row[pk] = mapping[(label, old)]
                if "owner_id" in row:
                    row["owner_id"] = plan.target
                if label == "ChunkVec" and row["chunk_id"].startswith(plan.source + pkns.SEP):
                    row["chunk_id"] = plan.target + pkns.SEP + pkns.strip(row["chunk_id"])
                if "sync_seq" in row:
                    row["sync_seq"] = seq(plan.target)
                    row["updated_at"] = stamp
                    if "rev" in row:
                        row["rev"] = int(row["rev"] or 0) + 1
                if label in _KINDS + ("Bridge",):
                    out["meta"]["_Tombstone"].append({"node_id": old, "label": label,
                        "owner_id": plan.source, "deleted_at": stamp, "rev": row.get("rev") or 1,
                        "sync_seq": seq(plan.source), "origin": "topic-transfer"})
                if label == "_DocMeta":
                    out["meta"]["_DocTombstone"].append({"doc_id": old, "owner_id": plan.source,
                        "deleted_at": stamp, "sync_seq": seq(plan.source)})
                marker = "topic-transfer:moved:" + plan.source + ":" + label + ":" + pkns.strip(old)
                out["meta"]["_SyncMeta"].append({"key": marker, "value": plan.target})
    for edge in out["edges"]:
        edge["from"] = mapping.get((edge["from_label"], edge["from"]), edge["from"])
        edge["to"] = mapping.get((edge["to_label"], edge["to"]), edge["to"])
    for key, value in seqs.items():
        rows = [r for r in out["meta"]["_SyncMeta"] if r["key"] == key]
        if rows:
            rows[0]["value"] = str(value)
        else:
            out["meta"]["_SyncMeta"].append({"key": key, "value": str(value)})
    for label, rows in {**out["nodes"], **out["meta"]}.items():
        pk = specs[label]["pk"]
        if len({r[pk] for r in rows}) != len(rows):
            raise TransferRefused("key_collision:" + label)
    return out


def _write_difference(store, before: dict, after: dict, *, fail_at=None, vocabulary_owner=None,
                      verify_snapshot=None):
    specs = {**node_table_specs(), **meta_table_specs()}
    # 전체 스토어를 지우지 않는다. 키가 바뀌는 선택 행만 새로 만들고 관계를 옮긴다.
    for group in ("nodes", "meta"):
        for label, rows in after[group].items():
            pk = specs[label]["pk"]
            old = {r[pk]: r for r in before[group][label]}
            for row in rows:
                if old.get(row[pk]) == row:
                    continue
                setters = ", ".join(f"n.{c}=${c}" for c in row if c != pk)
                store.run(f"MERGE (n:{label} {{{pk}:${pk}}})" + (" SET " + setters if setters else ""), row, raw=True)
    if fail_at == "nodes":
        raise RuntimeError("injected_after_nodes")
    old_edges = Counter(_canonical(e) for e in before["edges"])
    for edge in after["edges"]:
        encoded = _canonical(edge)
        if old_edges[encoded]:
            old_edges[encoded] -= 1
            continue
        sp, dp = specs[edge["from_label"]]["pk"], specs[edge["to_label"]]["pk"]
        props = ", ".join(f"{c}:${c}" for c in edge["props"])
        store.run(f"MATCH (a:{edge['from_label']}), (b:{edge['to_label']}) "
                  f"WHERE a.{sp}=$src AND b.{dp}=$dst CREATE (a)-[:{edge['label']}"
                  + (" {" + props + "}" if props else "") + "]->(b)",
                  {"src": edge["from"], "dst": edge["to"], **edge["props"]}, raw=True)
    if fail_at == "edges":
        raise RuntimeError("injected_after_edges")
    for group in ("nodes", "meta"):
        for label, rows in before[group].items():
            pk = specs[label]["pk"]
            keep = {r[pk] for r in after[group][label]}
            for row in rows:
                if row[pk] not in keep:
                    store.run(f"MATCH (n:{label}) WHERE n.{pk}=$key DETACH DELETE n", {"key": row[pk]}, raw=True)
    if fail_at == "delete":
        raise RuntimeError("injected_after_delete")
    observed = (verify_snapshot() if verify_snapshot is not None else
                read_snapshot(store, vocabulary_owner=vocabulary_owner))
    if fingerprint(observed) != fingerprint(after):
        raise TransferRefused("payload_verification_failed")


def _apply_rehearsal(store, plan: Plan, *, run_id: str, stamp: str, fail_at=None) -> str:
    """Private, unindexed synthetic executor. Not connected to REST, MCP or account.

    Production use requires the account reservation, write fences, all-writer move
    barriers, durable ACK, index/cache hooks and engine crash qualification.
    """
    _require_synthetic(store)
    if not run_id or len(run_id) > 120:
        raise TransferRefused("invalid_run_id")
    if any(r["index_name"] != "_PK" for r in store.run("CALL show_indexes() RETURN *", raw=True)):
        raise TransferRefused("indexed_store_not_supported_in_rehearsal")
    key = "topic-transfer:run:" + run_id
    args = _canonical([plan.source, plan.target, plan.topic, plan.before_hash])
    store.run("BEGIN TRANSACTION", raw=True)
    try:
        previous = store.run("MATCH (m:_SyncMeta) WHERE m.key=$key RETURN m.value AS value", {"key": key}, raw=True)
        if previous:
            journal = json.loads(previous[0]["value"])
            if journal["args"] != args:
                raise TransferRefused("run_id_reused")
            if journal.get("status") == "cancelled":
                raise TransferRefused("run_cancelled")
            current = read_snapshot(store)
            current["meta"]["_SyncMeta"] = [r for r in current["meta"]["_SyncMeta"] if r["key"] != key]
            if fingerprint(current) != journal["after_hash"]:
                raise TransferRefused("committed_payload_changed")
            store.run("COMMIT", raw=True)
            return "already_committed"
        before = read_snapshot(store)
        after = transformed_snapshot(before, plan, stamp=stamp)
        _write_difference(store, before, after, fail_at=fail_at)
        journal = _canonical({"args": args, "after_hash": fingerprint(after), "status": "committed"})
        store.run("CREATE (m:_SyncMeta {key:$key, value:$value})", {"key": key, "value": journal}, raw=True)
        if fail_at == "journal":
            raise RuntimeError("injected_before_commit")
        store.run("COMMIT", raw=True)
        return "committed"
    except BaseException:
        store.run("ROLLBACK", raw=True)
        raise


def _restore_rehearsal(store, before: dict, plan: Plan, *, run_id: str, stamp: str, fail_at=None):
    """Compensate an isolated synthetic commit before account activation.

    A changed store is refused. This is not a live sync recovery protocol: live
    recovery must publish new monotonic revisions under durable scope fences.
    """
    _require_synthetic(store)
    if any(r["index_name"] != "_PK" for r in store.run("CALL show_indexes() RETURN *", raw=True)):
        raise TransferRefused("indexed_store_not_supported_in_rehearsal")
    expected = transformed_snapshot(before, plan, stamp=stamp)
    key = "topic-transfer:run:" + run_id
    args = _canonical([plan.source, plan.target, plan.topic, plan.before_hash])
    store.run("BEGIN TRANSACTION", raw=True)
    try:
        current = read_snapshot(store)
        journals = [r for r in current["meta"]["_SyncMeta"] if r["key"] == key]
        if not journals or json.loads(journals[0]["value"])["args"] != args:
            raise TransferRefused("run_not_committed")
        without_journal = copy.deepcopy(current)
        without_journal["meta"]["_SyncMeta"] = [r for r in without_journal["meta"]["_SyncMeta"] if r["key"] != key]
        if json.loads(journals[0]["value"]).get("status") == "cancelled":
            if fingerprint(without_journal) != fingerprint(before):
                raise TransferRefused("restored_payload_changed")
            store.run("COMMIT", raw=True)
            return "already_restored"
        if fingerprint(without_journal) != fingerprint(expected):
            raise TransferRefused("compensation_source_changed")
        _write_difference(store, current, before, fail_at=fail_at)
        value = _canonical({"args": args, "status": "cancelled"})
        store.run("CREATE (m:_SyncMeta {key:$key, value:$value})", {"key": key, "value": value}, raw=True)
        store.run("COMMIT", raw=True)
        return "restored"
    except BaseException:
        store.run("ROLLBACK", raw=True)
        raise


def _require_synthetic(store):
    # 실험용 표식이 없는 일반 저장소에는 쓰기 트랜잭션을 열지 않는다.
    marker = store.run("MATCH (m:_SyncMeta) WHERE m.key='topic-transfer:synthetic' RETURN m.value AS value",
                       raw=True)
    if marker != [{"value": "tests-only"}]:
        raise TransferRefused("synthetic_store_required")
