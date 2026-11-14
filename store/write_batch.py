# -*- coding: utf-8 -*-
"""쓰기 묶음: fragment 하나·문서 하나를 트랜잭션 하나로 커밋한다.

문장마다 자동 커밋하면 커밋마다 WAL 을 디스크에 내리고, 노드 하나·엣지 하나가 문장 열댓 개라
하드디스크에서는 그 대기가 쓰기 시간 전부가 된다. 묶으면 커밋이 한 번이다.

어댑터가 `write_batch()` 로 트랜잭션을 열고, 이 모듈의 :class:`Batch` 를 그 스레드에 건다.
묶음 안에서는:

- 같은 스레드의 `store.run` 이 그 트랜잭션으로 간다(다른 스레드의 읽기는 끼어들지 않는다).
- `_SyncMeta` 의 시계·카운터(`last_hlc`·`seq:<owner>`)는 메모리에서 세고 커밋 직전에 한 번 쓴다
  (`api.revstamp`).
- 도메인 사슬(`Domain`·`PARENT_OF`)은 한 번 만든 것을 다시 만들지 않는다
  (`store.domain_hierarchy`).

⚠ 트랜잭션 안의 문장이 하나라도 실패하면 엔진이 트랜잭션 전체를 되돌린다. 그 실패를 호출부가
삼키고 이어 가면, 뒤의 문장은 트랜잭션 밖에서 하나씩 커밋되어 반쪽 상태가 남는다. 그래서 실패한
문장은 묶음을 `broken` 으로 표시하고, 그 뒤의 문장은 엔진에 보내지 않고 :class:`BatchBroken` 을
던진다. 끝에서 묶음은 되돌려지고 호출부(`api.fragment.merge_fragment`)가 한 문장씩 다시 쓴다.
"""
from __future__ import annotations

import os
import sys
import threading

from .backend import flag, operation
from .envflag import truthy


class BatchBroken(RuntimeError):
    """묶음 안의 문장이 실패해 트랜잭션이 이미 되돌려졌다. 묶음 밖에서 다시 써야 한다."""


class Batch:
    """한 트랜잭션 동안의 상태. 어댑터가 만들고 그 스레드에 건다."""

    def __init__(self):
        self.broken: Exception | None = None
        #: `_SyncMeta` 키 → 값(문자열). `dirty` 에 든 키만 커밋 직전에 쓴다.
        self.meta: dict[str, str] = {}
        self.dirty: set[str] = set()
        #: 이 묶음에서 이미 MERGE 한 도메인 이름과 (부모, 자식) 쌍.
        self.domains: set[str] = set()
        self.domain_links: set[tuple[str, str]] = set()
        #: 커밋 직전에 맞출 `IN_DOMAIN` 고리: (라벨, PK 열, 노드 키, 정규화된 도메인 또는 "").
        #: 같은 노드가 두 번 오면 마지막 것이 이긴다(한 문장씩 쓸 때와 같다).
        self.links_pending: list[tuple[str, str, str, str]] = []
        #: 묶음 동안만 유효한 읽기 결과(호출부가 키를 정한다). 되돌려지면 같이 버려진다.
        self.memo: dict = {}
        self.statements = 0

    def check(self) -> None:
        if self.broken is not None:
            raise BatchBroken(f"batch already failed: {type(self.broken).__name__}: {self.broken}")

    def fail(self, exc: Exception) -> None:
        if self.broken is None:
            self.broken = exc


class BatchSlot:
    """어댑터마다 하나. 스레드마다 지금 열린 묶음을 든다."""

    def __init__(self):
        self._local = threading.local()

    def get(self) -> Batch | None:
        return getattr(self._local, "batch", None)

    def set(self, batch: Batch | None) -> None:
        self._local.batch = batch


def current(store) -> Batch | None:
    """``store`` 에 이 스레드의 열린 묶음이 있으면 그것. 세션 래퍼·가짜 스토어는 None."""
    getter = operation(store, "current_batch")
    if getter is None:
        return None
    try:
        return getter()
    except Exception:   # noqa: BLE001 — 묶음은 최적화다. 못 읽으면 없는 것으로 본다
        return None


def flush_domain_links(batch: Batch, run) -> None:
    """모아 둔 `IN_DOMAIN` 고리를 라벨마다 두 문장으로 맞춘다: 옛 고리를 지우고 새 고리를 단다.
    한 문장씩 쓸 때(`store.domain_hierarchy.link_node`)와 결과가 같다: 노드마다 도메인 하나."""
    last: dict[tuple[str, str], tuple[str, str]] = {}
    for label, pk, key, norm in batch.links_pending:
        last[(label, key)] = (pk, norm)
    batch.links_pending.clear()
    by_label: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (label, key), (pk, norm) in last.items():
        by_label.setdefault((label, pk), []).append((key, norm))
    for (label, pk), rows in sorted(by_label.items()):
        run(f"UNWIND $ids AS k MATCH (n:{label} {{{pk}: k}})-[r:IN_DOMAIN]->(:Domain) DELETE r",
            {"ids": [k for k, _ in rows]})
        linked = [{"k": k, "d": d} for k, d in rows if d]
        if linked:
            run(f"UNWIND $rows AS row MATCH (n:{label} {{{pk}: row.k}}) "
                "MATCH (d:Domain {name: row.d}) MERGE (n)-[:IN_DOMAIN]->(d)", {"rows": linked})


def flush(store, batch: Batch, run) -> None:
    """커밋 직전 한 번: 모아 둔 도메인 고리와 시계·카운터를 쓴다."""
    flush_domain_links(batch, run)
    flush_meta(store, batch, run)


def flush_meta(store, batch: Batch, run) -> None:
    """커밋 직전: 묶음이 센 `_SyncMeta` 값을 키마다 한 번 쓴다. ``run`` 은 트랜잭션 안의 실행기."""
    for key in sorted(batch.dirty):
        run("MERGE (m:_SyncMeta {key: $k}) SET m.value = $v", {"k": key, "v": batch.meta[key]})
    batch.dirty.clear()


def run_batched(store, fn, *, what: str):
    """``fn()`` 을 쓰기 묶음 하나로 돌린다. 안의 문장이 실패해 묶음이 깨졌으면 되돌리고 ``fn()`` 을
    한 문장씩 다시 돌린다(결과는 묶기 전과 같다). 반환은 ``(결과, 첫 시도 결과 또는 None)``:
    두 번째 값은 다시 돌렸을 때만 있다.

    묶지 않는 경우: 어댑터에 `write_batch` 가 없거나(가짜 스토어·세션 래퍼), 이미 묶음 안이거나,
    호출부가 직접 연 트랜잭션 안이거나(`in_manual_transaction`), 읽기 전용이거나,
    `VINC_FRAGMENT_BATCH=off`."""
    opener = operation(store, "write_batch")
    manual = operation(store, "in_manual_transaction")
    # 읽기 전용으로 연 어댑터는 묶지 않는다: 이유를 말하는 오류(`StoreReadOnly`)는 묶음 밖의 `run` 이 만든다.
    if opener is None or current(store) is not None or (manual is not None and manual()) \
            or flag(store, "read_only") is True \
            or not truthy(os.environ.get("VINC_FRAGMENT_BATCH"), default=True):
        return fn(), None
    seen: list[Batch] = []
    try:
        with opener() as batch:
            seen.append(batch)
            first = fn()
    except Exception as exc:
        if not seen:
            # 트랜잭션을 열지도 못했다(아무것도 쓰지 않았다). 묶지 않고 한 문장씩 쓴다.
            print(f"[write_batch] {what}: could not open a batch, writing one statement at a time: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr, flush=True)
            return fn(), None
        # 문장 실패로 깨진 묶음이면 그 뒤에 무엇이 던져졌든(`BatchBroken` 포함) 묶음 밖에서 다시 쓴다.
        # 깨지지 않았는데 던졌으면 진짜 실패다: 트랜잭션은 되돌려졌고 아무것도 남지 않는다.
        if seen[0].broken is None:
            raise
        batch, first = seen[0], None
    if batch.broken is None:
        return first, None
    print(f"[write_batch] {what}: batched write fell back to one statement at a time: "
          f"{type(batch.broken).__name__}: {str(batch.broken)[:200]}", file=sys.stderr, flush=True)
    return fn(), first
