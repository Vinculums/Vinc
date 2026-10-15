"""이전 진단의 조회권을 소유자에 묶고 기존 단일 작업 큐에서 실행한다."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from uuid import UUID, uuid4


def _response(topic, state, code, ticket=None):
    return {"ready": False, "scope": "topic", "topic": topic, "counts_known": False,
            "counts": None, "documents": None, "blockers": [code], "inventory_state": state,
            **({"inventory_ticket": ticket} if ticket else {})}


@dataclass
class _Entry:
    owner: str
    topic: str
    ticket: str
    job: object = None
    version: str = ""
    result: dict | None = None
    finished: float | None = None
    invalidated: bool = False
    policy: str = "retain"


class InventoryJobs:
    """본문은 보관하지 않는다. 완료 결과도 표식이 바뀌면 실행 허가로 쓰지 않는다."""

    def __init__(self, queue, revision, *, limit=64, active_limit=2, ttl=300, clock=time.monotonic):
        self.queue, self.revision = queue, revision
        self.limit, self.active_limit, self.ttl, self.clock = limit, active_limit, ttl, clock
        self._entries = {}
        self._lock = threading.RLock()

    def _prune(self):
        now = self.clock()
        for ticket, entry in list(self._entries.items()):
            if (entry.finished is not None and entry.job is not None and entry.job.status not in ("queued", "running")
                    and now - entry.finished >= self.ttl):
                del self._entries[ticket]

    def invalidate(self, owner):
        with self._lock:
            for entry in self._entries.values():
                if entry.owner == owner:
                    entry.invalidated = True

    def _status(self, entry):
        if entry.job is None or entry.job.status in ("queued", "running"):
            return _response(entry.topic, "pending", "conversion_inventory_pending", entry.ticket)
        if entry.job.status == "error" or entry.result is None:
            return _response(entry.topic, "failed", "conversion_inventory_failed", entry.ticket)
        version, pending = self.revision(entry.owner)
        if entry.invalidated or pending or entry.version != version:
            return _response(entry.topic, "stale", "conversion_inventory_stale", entry.ticket)
        return {**entry.result, "inventory_state": entry.result.get("inventory_state", "done"),
                "inventory_ticket": entry.ticket}

    def start(self, owner, topic, work, *, policy="retain"):
        with self._lock:
            self._prune()
            active = [e for e in self._entries.values()
                      if e.job is None or e.job.status in ("queued", "running")]
            for entry in self._entries.values():
                if (entry.owner, entry.topic, entry.policy) == (owner, topic, policy):
                    result = self._status(entry)
                    if result["inventory_state"] != "stale":
                        return result
            if any(e.owner == owner for e in active) or len(active) >= self.active_limit:
                return _response(topic, "busy", "conversion_inventory_busy")
            if len(self._entries) >= self.limit:
                done = [e for e in self._entries.values() if e not in active]
                if not done:
                    return _response(topic, "busy", "conversion_inventory_busy")
                oldest = min(done, key=lambda e: e.finished or 0)
                del self._entries[oldest.ticket]
            entry = _Entry(owner, topic, str(uuid4()))
            entry.policy = policy
            self._entries[entry.ticket] = entry

            def run():
                try:
                    version, pending = self.revision(owner)
                    result = work()
                    with self._lock:
                        entry.version = version if not pending else "invalidated"
                        entry.result = result
                    # 일반 잡 조회 표면에는 진단의 토픽, 개수, 지문을 남기지 않는다.
                    return {"executed": True, "kind": "conversion-inventory"}
                except Exception:
                    with self._lock:
                        entry.result = _response(topic, "failed", "conversion_inventory_failed")
                        entry.version = self.revision(owner)[0]
                    return {"executed": False, "kind": "conversion-inventory"}
                finally:
                    with self._lock:
                        entry.finished = self.clock()

            entry.job = self.queue.submit("topic-conversion.preflight", run)
            return _response(topic, "pending", "conversion_inventory_pending", entry.ticket)

    def status(self, owner, ticket):
        try:
            UUID(ticket)
        except (ValueError, TypeError, AttributeError):
            return _response("", "unknown", "conversion_inventory_unknown")
        with self._lock:
            self._prune()
            entry = self._entries.get(ticket)
            if entry is None or entry.owner != owner:
                return _response("", "unknown", "conversion_inventory_unknown")
            return self._status(entry)
