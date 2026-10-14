"""Account-authorized ownership conversion, serialized on the sidecar writer.

The account reserves a fresh inaccessible team without a default pack. The graph
commit retains durable source/target fences until the account confirms activation.
An uncertain response is resumed by run id; it is never repeated as a new move.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from contextvars import ContextVar
from dataclasses import replace
from uuid import UUID

from store.embedded_backend import DDL_GUARD
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_manifest import TopicManifest, ManifestLimit
from store.topic_transfer import (TransferRefused, fingerprint, read_snapshot,
                                 plan_transfer, transformed_snapshot, _write_difference, check_inventory_budget,
                                 _canonical, _REFERENCE)

request_owner = ContextVar("conversion_request_owner", default=None)
request_payload = ContextVar("conversion_request_payload", default=None)
PREFIX = "topic-conversion:"
PREFLIGHT_TARGET = "team:00000000-0000-0000-0000-000000000000"
SCOPED_INVENTORY_VERSION = 2


def _inventory(store, source, target, topic, *, after=False, verify_shape=True):
    return read_scoped_inventory(store, source=source, target=target, topic=topic,
                                 after=after, verify_shape=verify_shape)


def _plan(inventory, source, target, topic):
    plan = plan_transfer(inventory.snapshot, source=source, target=target, topic=topic)
    if inventory.blockers:
        plan = replace(plan, blockers=tuple(sorted(set(plan.blockers) | set(inventory.blockers))))
    return plan


def _scope_response_safe(snapshot, source):
    # 전역 문서 키에 다른 소유자의 행이 붙어 있으면 그 크기나 해시도 공개하지 않는다.
    return not any(row.get("owner_id") not in (None, source)
                   for group in ("nodes", "meta") for rows in snapshot[group].values() for row in rows)


def _document_scope_summary(snapshot, plan, source):
    """이미 증명한 소유자 범위 안에서 문서 식별자와 메타데이터 상태를 구분한다."""
    metas={row['doc_id']:row for row in snapshot['meta']['_DocMeta'] if row['doc_id'] in plan.documents}
    return {'identities':len(plan.documents),'owned_metadata':sum(row.get('owner_id')==source for row in metas.values()),
            'missing_metadata':sum(doc not in metas for doc in plan.documents),
            'unassigned_metadata':sum(row.get('owner_id') in (None,'') for row in metas.values())}


def _missing_document_evidence(snapshot, plan, source):
    """같은 조회에서 본 본문·벡터·삭제 표지만 집계하고 복구 가능성은 추정하지 않는다."""
    identities=set(plan.documents)
    metas={row['doc_id'] for row in snapshot['meta']['_DocMeta']}
    missing=identities-metas
    chunks={row['doc_id'] for row in snapshot['nodes']['Chunk']}
    vectors={row['doc_id'] for row in snapshot['nodes']['ChunkVec']}
    tombstones={row['doc_id'] for row in snapshot['meta']['_DocTombstone'] if row.get('owner_id')==source}
    unassigned={row['doc_id'] for row in snapshot['meta']['_DocTombstone'] if row.get('owner_id') in (None,'')}
    owned={row['doc_id'] for row in snapshot['meta']['_DocMeta'] if row.get('owner_id')==source}
    return {'missing_with_chunks':len(missing & chunks),'missing_with_vectors':len(missing & vectors),
            'missing_with_owned_tombstone':len(missing & tombstones),
            'missing_with_unassigned_tombstone':len(missing & unassigned),
            'missing_without_payload_with_owned_tombstone':len((missing-chunks-vectors) & tombstones),
            'missing_without_payload_or_owned_tombstone':len(missing-chunks-vectors-tombstones),
            'live_metadata_with_owned_tombstone':len(owned & tombstones)}


def _committed_snapshot(store, source, target, topic, journal):
    if journal.get("inventory_version", 1) == 1:
        return read_snapshot(store, vocabulary_owner=source)
    if journal["inventory_version"] != SCOPED_INVENTORY_VERSION:
        raise TransferRefused("unsupported_conversion_inventory_version")
    return _inventory(store, source, target, topic, after=True).snapshot


def journals(store):
    # Only JSON values are selected; the request reader has no raw-key API.
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key STARTS WITH $prefix RETURN m.value AS value",
                     {"prefix": PREFIX})
    return [json.loads(r["value"]) for r in rows]


def fenced(store, owner):
    return any(r["status"] not in ("active", "cancelled") and (owner is None or owner in (r["source"], r["target"]))
               for r in journals(store))


def check_read(store, owner):
    if fenced(store, owner):
        raise TransferRefused("space_conversion_pending")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def check_write(store, owner, payload=None):
    """Checked at execution, including jobs enqueued before the graph commit."""
    check_read(store, owner)
    if not owner or not payload:
        return
    from store.ownership_moves import blocked_sync_references
    ids = list(_strings(payload))
    ids.extend(ref for value in tuple(ids) for ref in _REFERENCE.findall(value))
    moved = blocked_sync_references(store, owner=owner,
                                   payload={"nodes": [{"id": s} for s in ids]})
    if moved:
        raise TransferRefused("reference_moved_to_team")
    for run in journals(store):
        if run["status"] != "cancelled" and run["source"] == owner and any(s == run["topic"] or s.startswith(run["topic"] + "/")
                                           for s in ids):
            raise TransferRefused("topic_moved_to_team")


def scoped_hash(snapshot, source, target):
    """Ignore other tenants' changes and this protocol's own journal rows."""
    owned = set()
    out = {"schema_version": snapshot["schema_version"], "nodes": {}, "meta": {}, "edges": []}
    from store.embedded_schema import node_table_specs, meta_table_specs
    specs = {**node_table_specs(), **meta_table_specs()}
    for group in ("nodes", "meta"):
        for label, rows in snapshot[group].items():
            kept = []
            for row in rows:
                key = row[specs[label]["pk"]]
                belongs = row.get("owner_id") in (source, target) or (isinstance(key, str) and
                    any(key.startswith(owner + "\x1f") for owner in (source, target)))
                if label in ("Domain", "_SchemaMeta"):
                    belongs = True
                if label == "_SyncMeta":
                    belongs = not key.startswith(PREFIX) and (source in key or target in key)
                if belongs:
                    kept.append(row)
                    owned.add((label, key))
            out[group][label] = kept
    out["edges"] = [e for e in snapshot["edges"]
                    if (e["from_label"], e["from"]) in owned or (e["to_label"], e["to"]) in owned]
    return fingerprint(out)


def preflight(store, source, topic):
    check_read(store, source)
    try:
        inventory = _inventory(store, source, PREFLIGHT_TARGET, topic)
        snap = inventory.snapshot
        plan = _plan(inventory, source, PREFLIGHT_TARGET, topic)
        if not _scope_response_safe(snap, source):
            return {"ready": False, "scope": "topic", "topic": topic,
                    "counts_known": False, "counts": None, "documents": None,
                    "blockers": sorted({"conversion_scope_inconsistent"} | {b.split(":", 1)[0] for b in plan.blockers})}
    except TransferRefused as exc:
        if isinstance(exc, ManifestLimit) and exc.reason in ("retained_payload", "retained_rows", "retained_bytes"):
            return inventory_diagnostics(store, source, topic)
        # An incomplete inventory cannot establish the scope. Zero here would
        # falsely say the topic is empty, while a reservation must stay blocked.
        return {"ready": False, "scope": "topic", "topic": topic,
                "counts_known": False, "counts": None, "documents": None,
                "blockers": [str(exc).split(":", 1)[0]]}
    counts = {}
    for label, _ in plan.selected:
        counts[label] = counts.get(label, 0) + 1
    blockers = {b.split(":", 1)[0] for b in plan.blockers}
    projected_rows = projected_bytes = None
    try:
        _index_inventory(store)
        if plan.ready:
            preview = transformed_snapshot(snap, plan, stamp="9999-12-31T23:59:59.999Z")
            audit = _audit(snap, plan)
            projected_rows = (len(preview["edges"]) + sum(len(rows) for group in ("nodes", "meta")
                                                        for rows in preview[group].values()) + 1)
            projected_bytes = len(_canonical(preview).encode()) + 4096 + 3 * len(json.dumps(audit).encode())
            _conversion_budget(preview, audit)
    except TransferRefused as exc:
        blockers.add(str(exc).split(":", 1)[0])
    # Boundary identities can belong to another tenant: expose codes only.
    return {"ready": not blockers, "scope": "topic", "topic": topic,
            "fingerprint": scoped_hash(snap, source, PREFLIGHT_TARGET),
            "counts_known": True, "counts": counts, "documents": len(plan.documents),
            "inventory_rows": inventory.retained_rows, "inventory_bytes": inventory.retained_bytes,
            "inspected_rows": inventory.inspected_rows,
            "projected_rows": projected_rows, "projected_bytes": projected_bytes,
            "blockers": sorted(blockers)}


def inventory_diagnostics(store, source, topic):
    """Read the complete bounded closure without authorizing the legacy executor."""
    check_read(store, source)
    # 로컬 임시 목록만 만들며, 공유 디스크나 그래프에 payload를 쓰지 않는다.
    temporary_root = Path(tempfile.gettempdir())
    if str(temporary_root).startswith(("\\\\", "//")):
        return {"ready": False, "scope": "topic", "topic": topic,
                "counts_known": False, "counts": None, "documents": None,
                "blockers": ["conversion_local_scratch_required"]}
    deadline = time.monotonic() + 180
    try:
        with _inventory_read_transaction(store), tempfile.TemporaryDirectory(prefix="vinc-inventory-", dir=temporary_root) as directory:
            with TopicManifest(Path(directory) / "inventory.sqlite", deadline=deadline) as manifest:
                inventory = read_scoped_inventory(store, source=source, target=PREFLIGHT_TARGET,
                                                   topic=topic, manifest=manifest)
                plan = _plan(inventory, source, PREFLIGHT_TARGET, topic)
                if not _scope_response_safe(inventory.snapshot, source):
                    return {"ready": False, "scope": "topic", "topic": topic,
                            "counts_known": False, "counts": None, "documents": None,
                            "blockers": sorted({"conversion_scope_inconsistent"} | {b.split(":", 1)[0] for b in plan.blockers})}
                counts = {}
                for label, _ in plan.selected:
                    counts[label] = counts.get(label, 0) + 1
                blockers = {b.split(":", 1)[0] for b in plan.blockers}
                # 큰 목록을 읽었다고 기존 단일 트랜잭션 실행까지 허용하지 않는다.
                blockers.add("conversion_bounded_executor_required")
                return {"ready": False, "scope": "topic", "topic": topic,
                        "counts_known": True, "counts": counts, "documents": len(plan.documents),
                        "document_ownership": _document_scope_summary(inventory.snapshot,plan,source),
                        "document_missing_evidence": _missing_document_evidence(inventory.snapshot,plan,source),
                        "inventory_rows": inventory.retained_rows, "inventory_bytes": inventory.retained_bytes,
                        "inspected_rows": inventory.inspected_rows, "inventory_hash": plan.before_hash,
                        "inventory_version": 3, "diagnostic_only": True,
                        "blockers": sorted(blockers)}
    except (TransferRefused, OSError, sqlite3.Error) as exc:
        code = str(exc).split(":", 1)[0] if isinstance(exc, TransferRefused) else "conversion_scratch_unavailable"
        result = {"ready": False, "scope": "topic", "topic": topic, "counts_known": False,
                  "counts": None, "documents": None, "blockers": [code]}
        if isinstance(exc, ManifestLimit):
            result["limit"] = {"reason": exc.reason, **exc.details}
        return result


@contextmanager
def _inventory_read_transaction(store):
    # 여러 페이지가 같은 DB 스냅샷을 읽게 하며 그래프에는 쓰지 않는다.
    store.run("BEGIN TRANSACTION READ ONLY", raw=True)
    try:
        yield
    finally:
        try:
            store.run("ROLLBACK", raw=True)
        except RuntimeError as exc:
            # 엔진이 실패한 읽기 트랜잭션을 이미 중단했을 수 있다.
            if "No active transaction" not in str(exc):
                raise


def _get(store, run):
    UUID(run)
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key=$key RETURN m.value AS value",
                     {"key": PREFIX + run}, raw=True)
    return json.loads(rows[0]["value"]) if rows else None


def _save(store, run, journal):
    store.run("MERGE (m:_SyncMeta {key:$key}) SET m.value=$value",
              {"key": PREFIX + run, "value": json.dumps(journal, sort_keys=True)}, raw=True)
    store._topic_conversion_guard = True


def _identity(journal, source, target, topic):
    if (journal["source"], journal["target"], journal["topic"]) != (source, target, topic):
        raise TransferRefused("run_id_reused")


def _indexes(store, inventory):
    """Restore exactly the canonical indexes present before the operation."""
    for table in inventory:
        store._ensure_index(table)


def _audit(snapshot, plan):
    from store.embedded_schema import node_table_specs, meta_table_specs
    from store import pkns
    specs = {**node_table_specs(), **meta_table_specs()}
    selected = set(plan.selected)
    return [{"label": label, "id": pkns.strip(row[specs[label]["pk"]]),
             "owner_id": row.get("owner_id"),
             **{field: row[field] for field in ("rev", "sync_seq", "updated_at") if field in row}}
            for group in ("nodes", "meta") for label, rows in snapshot[group].items()
            for row in rows if (label, row[specs[label]["pk"]]) in selected]


def _conversion_budget(after, audit):
    # Leave room for a journal row and its escaped audit JSON before creating a
    # team or publishing any graph mutation. Never inspect a truncated snapshot.
    check_inventory_budget(after, extra_rows=1,
                           extra_bytes=4096 + 3 * len(json.dumps(audit).encode()))


def _index_inventory(store):
    if any(getattr(store, attr, False) for attr in
           ("vector_index_remnant", "fts_index_remnant", "node_vector_index_remnant")):
        raise TransferRefused("search_index_repair_required")
    known = {("Chunk", store._FTS_INDEX), ("ChunkVec", store._VEC_INDEX),
             ("NodeVec", store._NODE_VEC_INDEX)}
    inventory = []
    for row in store.run("CALL show_indexes() RETURN *", raw=True):
        if row["index_name"] == "_PK":
            continue
        pair = (row["table_name"], row["index_name"])
        if pair not in known:
            raise TransferRefused("unsupported_search_index")
        if pair[0] not in inventory:
            inventory.append(pair[0])
    return inventory


def stage(store, *, source, target, topic, run, expected, stamp, fail_at=None):
    UUID(run)
    with DDL_GUARD.exclusive():
        previous = _get(store, run)
        if previous:
            _identity(previous, source, target, topic)
            if previous["status"] == "cancelled":
                return {"status": "cancelled", "run": run}
            if previous["expected"] != expected:
                raise TransferRefused("run_id_reused")
            if previous["status"] == "active":
                return {"status": "active", "run": run}
            if previous["status"] == "graph_committed":
                _indexes(store, previous["indexes"])
                snap = _committed_snapshot(store, source, target, topic, previous)
                if scoped_hash(snap, source, target) != previous["after_hash"]:
                    raise TransferRefused("committed_payload_changed")
                return {"status": "graph_committed", "run": run}
        else:
            check_read(store, source)
            check_read(store, target)
        legacy = bool(previous and previous.get("inventory_version", 1) == 1)
        def checked_before():
            if legacy:
                snapshot = read_snapshot(store, vocabulary_owner=source)
                return snapshot, plan_transfer(snapshot, source=source, target=target, topic=topic)
            current = _inventory(store, source, target, topic)
            return current.snapshot, _plan(current, source, target, topic)
        try:
            before, plan = checked_before()
        except TransferRefused as scoped_error:
            if previous:
                raise
            # An already-reserved run from the prior full-inventory protocol
            # can still stage if its old fingerprint is exactly current.
            old = read_snapshot(store, vocabulary_owner=source)
            if scoped_hash(old, source, PREFLIGHT_TARGET) != expected:
                raise scoped_error
            legacy = True
            before, plan = old, plan_transfer(old, source=source, target=target, topic=topic)
        if scoped_hash(before, source, PREFLIGHT_TARGET) != expected:
            if previous:
                raise TransferRefused("plan_stale")
            old = read_snapshot(store, vocabulary_owner=source)
            if scoped_hash(old, source, PREFLIGHT_TARGET) != expected:
                raise TransferRefused("plan_stale")
            legacy = True
            before, plan = old, plan_transfer(old, source=source, target=target, topic=topic)
        after = transformed_snapshot(before, plan, stamp=stamp)
        audit = _audit(before, plan)
        _conversion_budget(after, audit)
        index_inventory = list(previous["indexes"]) if previous else []
        for table in _index_inventory(store):
            if table not in index_inventory:
                index_inventory.append(table)
        # Persist intent and fences BEFORE DDL. A process killed between index
        # drops and the ownership transaction can restore the exact inventory.
        if previous is None:
            _save(store, run, {"source": source, "target": target, "topic": topic,
                              "expected": expected, "indexes": index_inventory, "status": "prepared",
                              "inventory_version": 1 if legacy else SCOPED_INVENTORY_VERSION})
            before, plan = checked_before()
            after = transformed_snapshot(before, plan, stamp=stamp)
        # Keep the crash-sensitive HNSW mutation guard. All readers are excluded.
        if "ChunkVec" in index_inventory:
            store.drop_vector_index()
        if "NodeVec" in index_inventory:
            store.drop_node_vector_index()
        if "Chunk" in index_inventory:
            store._load_ext("fts")
            store._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{store._FTS_INDEX}')", "conversion FTS")
        store.run("BEGIN TRANSACTION", raw=True)
        try:
            _write_difference(store, before, after, fail_at=fail_at, vocabulary_owner=source,
                              verify_snapshot=(None if legacy else
                                  # The shape was checked again after intent was saved,
                                  # before DDL and this transaction began.
                                  lambda: _inventory(store, source, target, topic,
                                                     after=True, verify_shape=False).snapshot))
            _save(store, run, {"source": source, "target": target, "topic": topic,
                              "expected": expected, "after_hash": scoped_hash(after, source, target),
                              "indexes": index_inventory, "prior_sync_metadata": audit, "status": "graph_committed",
                              "inventory_version": 1 if legacy else SCOPED_INVENTORY_VERSION})
            if fail_at == "journal":
                raise RuntimeError("injected_before_commit")
            store.run("COMMIT", raw=True)
        except BaseException:
            store.run("ROLLBACK", raw=True)
            _indexes(store, index_inventory)
            raise
        _indexes(store, index_inventory)
        store._topic_conversion_guard = True
        return {"status": "graph_committed", "run": run}


def release(store, *, source, target, topic, run):
    """Only an account assertion after verified activation may release fences."""
    journal = _get(store, run)
    if not journal:
        raise TransferRefused("run_not_committed")
    _identity(journal, source, target, topic)
    if journal["status"] not in ("graph_committed", "active"):
        raise TransferRefused("run_not_committed")
    if journal["status"] != "active":
        snap = _committed_snapshot(store, source, target, topic, journal)
        if scoped_hash(snap, source, target) != journal["after_hash"]:
            raise TransferRefused("committed_payload_changed")
        journal["status"] = "active"
        _save(store, run, journal)
    return {"status": "active", "run": run}


def cancel(store, *, source, target, topic, run):
    """Serial cancellation barrier invalidates every outstanding stage capability."""
    with DDL_GUARD.exclusive():
        journal = _get(store, run)
        if journal:
            _identity(journal, source, target, topic)
            if journal["status"] not in ("prepared", "cancelled"):
                raise TransferRefused("conversion_resume_required")
            _indexes(store, journal["indexes"])
        else:
            # Validate the same source/target/topic contract without selecting data.
            if not source.startswith("user:") or not target.startswith("team:") or not topic:
                raise TransferRefused("invalid_conversion_scope")
            UUID(target[5:])
            journal = {"source": source, "target": target, "topic": topic, "indexes": [], "expected": None}
        journal["status"] = "cancelled"
        _save(store, run, journal)
        return {"status": "cancelled", "run": run}


def status(store, *, source, target, topic, run):
    journal = _get(store, run)
    if journal:
        _identity(journal, source, target, topic)
    return {"run": run, "status": journal["status"] if journal else "prepared"}
