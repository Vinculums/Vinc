# -*- coding: utf-8 -*-
"""토픽 이동 시험의 도우미: 합성 스토어용 한 트랜잭션 실행기와 고장 주입.

`apply_rehearsal` 은 계정·울타리·영속 확인 없이 계획 하나를 한 트랜잭션으로 적용하는 합성 실행기다. 운영에서
부르는 곳은 없다. `injected_failure(point)` 는 `store.topic_transfer.fault_point` 의 그 자리에서 원래 문장의
RuntimeError 를 던지게 한다(`nodes`·`edges`·`delete`·`journal`, 또는 None 이면 아무것도 안 건다).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from store import topic_transfer
from store.canon import canonical_json_ascii_v1 as canonical
from store.topic_transfer import (Plan, TransferRefused, fingerprint, read_snapshot, transformed_snapshot,
                                  write_difference)

#: 고장 주입 자리와 그 자리가 던지는 문장.
MESSAGES = {"nodes": "injected_after_nodes", "edges": "injected_after_edges",
            "delete": "injected_after_delete", "journal": "injected_before_commit"}


@contextmanager
def injected_failure(point):
    """`point` 자리에서 실패시킨다. 나오면 갈고리를 원래대로 둔다."""
    if point is None:
        yield
        return
    message = MESSAGES[point]

    def hook(at):
        if at == point:
            raise RuntimeError(message)

    previous, topic_transfer._FAULT_HOOK = topic_transfer._FAULT_HOOK, hook
    try:
        yield
    finally:
        topic_transfer._FAULT_HOOK = previous


def apply_rehearsal(store, plan: Plan, *, run_id: str, stamp: str, fail_at=None) -> str:
    """Private, unindexed synthetic executor. Not connected to REST, MCP or account.

    Production use requires the account reservation, write fences, all-writer move
    barriers, durable ACK, index/cache hooks and engine crash qualification.
    """
    _require_synthetic(store)
    if not run_id or len(run_id) > 120:
        raise TransferRefused("invalid_run_id")
    if store.search_indexes():
        raise TransferRefused("indexed_store_not_supported_in_rehearsal")
    key = "topic-transfer:run:" + run_id
    args = canonical([plan.source, plan.target, plan.topic, plan.before_hash])
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
        with injected_failure(fail_at):
            write_difference(store, before, after)
        journal = canonical({"args": args, "after_hash": fingerprint(after), "status": "committed"})
        store.run("CREATE (m:_SyncMeta {key:$key, value:$value})", {"key": key, "value": journal}, raw=True)
        if fail_at == "journal":
            raise RuntimeError(MESSAGES["journal"])
        store.run("COMMIT", raw=True)
        return "committed"
    except BaseException:
        store.run("ROLLBACK", raw=True)
        raise


def _require_synthetic(store):
    # 실험용 표식이 없는 일반 저장소에는 쓰기 트랜잭션을 열지 않는다.
    marker = store.run("MATCH (m:_SyncMeta) WHERE m.key='topic-transfer:synthetic' RETURN m.value AS value",
                       raw=True)
    if marker != [{"value": "tests-only"}]:
        raise TransferRefused("synthetic_store_required")
