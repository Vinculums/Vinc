"""작업 ID로 결과를 확인한다. 원문·props를 복제하지 않는 소유 범위 영수증."""
from __future__ import annotations

import hashlib
import json
import datetime as dt
from contextvars import ContextVar
from uuid import UUID

PREFIX = "mutation-receipt:"
MAX_OWNER_RECEIPTS = 5000
MAX_REQUEST_BYTES = 4 * 1024 * 1024
in_transaction = ContextVar("mutation_receipt_transaction", default=False)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _key(owner, operation_id):
    if not isinstance(owner, str) or not owner or owner != owner.strip() or "\x1f" in owner:
        raise ValueError("receipt_owner_required")
    if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id:
        raise ValueError("invalid_operation_id")
    # 기존 owner purge의 ENDS WITH ':'+owner 규칙으로 함께 지워진다.
    return PREFIX + operation_id + ":" + owner


def _load(store, key):
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key=$key RETURN m.value AS value", {"key": key})
    if rows is None:
        raise RuntimeError("receipt_read_unavailable")
    return json.loads(rows[0]["value"]) if rows else None


def _save(store, key, value):
    store.run("MERGE (m:_SyncMeta {key:$key}) SET m.value=$value",
              {"key": key, "value": canonical(value)})


def status(store, *, owner, operation_id):
    base = {"kind": "operation-status", "executed": False}
    try:
        key = _key(owner, operation_id)
    except ValueError:
        return {**base, "error": "invalid_operation_id"}
    try:
        record = _load(store, key)
        if record is None:
            # 기록이 없다는 것으로 쓰지 않았다고 단정하지 않는다.
            return {**base, "executed": True, "found": False, "state": "unknown",
                    "applied": "unknown", "durability": "not_checked"}
        return {**base, "executed": True, "found": True, **record,
                "durability": "not_checked", "historical": True}
    except Exception:
        return {**base, "error": "receipt_read_unavailable", "state": "unknown"}


def _summary(result):
    out = {"executed": result.get("executed") is not False,
           "partial": bool(result.get("partial")), "verification_required": True}
    for field in ("kind", "created", "skipped", "found", "rev", "content_hash", "doc_id", "node_id", "status",
                  "current_rev", "current_content_hash", "rolled_back", "deleted_chunks", "deleted_meta"):
        if field in result and isinstance(result[field], (str, int, bool)):
            if not isinstance(result[field], str) or len(result[field]) <= 2048:
                out[field] = result[field]
    for field in ("deleted", "merged"):
        value = result.get(field)
        if isinstance(value, int):
            out[field] = value
        elif isinstance(value, dict):
            def counts(v):
                if isinstance(v, dict):
                    return {k: counts(x) for k, x in v.items() if isinstance(x, (dict, int))}
                return v
            out[field] = counts(value)
    stored = result.get("stored")
    if isinstance(stored, list):
        out["targets"] = [{k: row[k] for k in ("id", "kind", "rev") if k in row}
                          for row in stored[:256] if isinstance(row, dict)]
        out["stored_truncated"] = len(stored) > 256
        out["target_count"] = len(stored)
    if result.get("error"):
        error = str(result["error"])
        out["error"] = error if error.replace("_", "").isalnum() and len(error) <= 80 else "operation_failed"
    if len(canonical(out).encode("utf-8")) > 40000:
        out.pop("targets", None)
        out["stored_truncated"] = True
    return out


def execute(store, *, owner, operation, arguments, operation_id, apply):
    """기존 단일 writer 안에서 수행한다. 내용과 완료 기록은 같은 DB 트랜잭션이다."""
    base = {"executed": False, "kind": operation}
    try:
        key = _key(owner, operation_id)
    except ValueError:
        return {**base, "error": "invalid_operation_id"}
    try:
        payload = canonical({"operation": operation, "owner": owner, "arguments": arguments})
        if len(payload.encode("utf-8")) > MAX_REQUEST_BYTES:
            return {**base, "error": "receipt_request_too_large"}
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        previous = _load(store, key)
        if previous:
            if previous.get("request_hash") != digest:
                return {**base, "error": "operation_id_conflict", "status": "conflict"}
            if previous.get("state") not in ("committed", "not_applied"):
                return {**base, "error": "operation_outcome_unknown", "write_may_have_applied": True}
            return {**previous["result"], "operation_id": operation_id,
                    "receipt": previous, "replayed": True, "verification_required": True}
        count = store.run("MATCH (m:_SyncMeta) WHERE m.key STARTS WITH $prefix "
                          "AND m.key ENDS WITH $suffix RETURN count(m) AS n",
                          {"prefix": PREFIX, "suffix": ":" + owner})
        if not count or int(count[0]["n"]) >= MAX_OWNER_RECEIPTS:
            return {**base, "error": "receipt_capacity_unavailable"}
    except Exception:
        return {**base, "error": "receipt_read_unavailable"}
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    record = {"operation_id": operation_id, "operation": operation, "request_hash": digest,
              "accepted_at": stamp, "acknowledgement_scope": "graph_commit",
              "state": "accepted", "applied": "unknown"}
    started = False
    token = None
    try:
        # 적용 전 접수 기록을 남긴다. 도중 종료된 접수는 자동으로 다시 실행하지 않는다.
        _save(store, key, record)
        store.run("BEGIN TRANSACTION")
        started = True
        token = in_transaction.set(True)
        result = apply()
        if not isinstance(result, dict):
            raise RuntimeError("mutation_result_unavailable")
        applied = result.get("executed") is True and not result.get("error")
        if not applied:
            store.run("ROLLBACK")
            started = False
            result = {**result, "executed": False, "rolled_back": True}
            for field in ("stored", "deleted", "merged", "applied", "repointed", "dropped"):
                result.pop(field, None)
            record.update(state="not_applied", applied=False, result=_summary(result))
            _save(store, key, record)
        else:
            record.update(state="committed", applied=True, result=_summary(result),
                          committed_at=dt.datetime.now(dt.timezone.utc).isoformat())
            _save(store, key, record)
            store.run("COMMIT")
            started = False
        return {**result, "operation_id": operation_id, "receipt": record}
    except BaseException as exc:
        if started:
            try:
                store.run("ROLLBACK")
            except Exception:
                pass
        if not isinstance(exc, Exception):
            raise
        # COMMIT 응답을 잃었을 수도 있으므로 실패를 미적용으로 추측하지 않는다.
        return {**base, "error": "operation_outcome_unknown", "operation_id": operation_id,
                "write_may_have_applied": True}
    finally:
        if token is not None:
            in_transaction.reset(token)
