"""Owner-bound bounded conversion scheduling; no production enablement here.

The native ledger remains authoritative. This coordinator only schedules the next
step after the writer queue has acknowledged the previous one, including hooks.
[[vinc.plan.large-topic-ownership-conversion-2026-10-14]]
"""
from __future__ import annotations

from contextvars import copy_context
from dataclasses import dataclass, field
import threading
import time
from uuid import UUID
from pathlib import Path
import tempfile
from types import SimpleNamespace

from store.topic_transfer import TransferRefused


def bounded_flow(store, durable, *, source, target, topic, run, qualified_indexes=False):
    from .topic_conversion_batches import Batches
    if durable is None:
        raise TransferRefused("conversion_requires_durable_ack")

    def publish(journal):
        durable(SimpleNamespace(kind="topic-conversion.batch"), {})
        return durable.sequence

    return Batches(store, root=durable.root, publisher=publish, run=run,
                   source=source, target=target, topic=topic,
                   qualified_indexes=qualified_indexes)


def bounded_step(store, durable, *, source, target, topic, run, expected, stamp,
                 allow_new=False, qualified_indexes=False, conventions_policy="retain"):
    """One writer-queue step; the caller supplies the qualified execution policy.

    New reservations remain disabled by default. Existing v1/v2 journals belong
    to the legacy executor and are never reinterpreted as bounded manifests.
    """
    from . import topic_conversion as legacy
    from store import topic_batch_manifest as manifest
    from store.topic_manifest import TopicManifest
    from store.scoped_topic_inventory import read_scoped_inventory

    flow = bounded_flow(store, durable, run=run,
                   source=source, target=target, topic=topic,
                   qualified_indexes=qualified_indexes)
    journal = legacy._get(store, run)
    if journal is None:
        if not allow_new:
            raise TransferRefused("conversion_bounded_executor_required")
        # Global indexes are shared. Another unfinished conversion, even one
        # awaiting account activation, cannot start a second maintenance window.
        if any(j.get("status") not in ("active", "cancelled") for j in legacy.journals(store)):
            raise TransferRefused("conversion_execution_busy")
        with tempfile.TemporaryDirectory(prefix="vinc-conversion-prepare-") as directory:
            path = Path(directory) / "sealed.jsonl"
            with TopicManifest(Path(directory) / "inventory.sqlite", deadline=time.monotonic() + 180) as scratch:
                with legacy._inventory_read_transaction(store):
                    inventory = read_scoped_inventory(store, source=source, target=target,
                                                      topic=topic, manifest=scratch)
                    plan = legacy._plan(inventory, source, target, topic, conventions_policy)
                    if not plan.ready:
                        raise TransferRefused("conversion_dependency_blocked")
                    if manifest.authorized_hash(plan.before_hash, conventions_policy) != expected:
                        raise TransferRefused("plan_stale")
                    # 목록 읽기와 봉인은 각자 처리 시간 한도를 갖는다. 근거: [[decision:topic-conversion-phase-deadlines-2026-10-16]]
                    scratch.deadline = time.monotonic() + 180
                    header = manifest.prepare(scratch, plan, run=run, stamp=stamp,
                                              indexes=legacy._index_inventory(store), store=store)
                    scratch.deadline = time.monotonic() + 180
                    sealed = manifest.seal(scratch, path, header)
            flow.initialize(path, sealed["sha256"], expected, conventions_policy=conventions_policy)
        return flow.status()
    legacy._identity(journal, source=source, target=target, topic=topic)
    if journal.get("conventions_policy", "retain") != conventions_policy:
        raise TransferRefused("run_conventions_policy_changed")
    if journal.get("inventory_version") != manifest.PROTOCOL:
        raise TransferRefused("unsupported_conversion_inventory_version")
    if journal.get("expected") != expected:
        raise TransferRefused("run_id_reused")
    if journal["status"] in ("active", "cancelled", "graph_committed"):
        return flow.status()
    return flow.verify() if journal["phase"] == "verifying" else flow.step()


@dataclass
class _Run:
    identity: tuple
    job: object = None
    result: dict = field(default_factory=dict)
    error: str | None = None
    finished: bool = False
    thread: threading.Thread | None = None


class ConversionJobs:
    """One active conversion, yielding the single writer between durable batches.

    A restart requires an authenticated resume from the native ledger. Neither
    status reads nor uncertain failures silently start another mutation.
    """

    def __init__(self, queue, *, work_seconds, limit=64, clock=time.monotonic):
        if work_seconds <= 0 or limit < 1:
            raise ValueError("positive work budget and registry limit required")
        self.queue, self.work_seconds, self.limit, self.clock = queue, work_seconds, limit, clock
        self._runs = {}
        self._lock = threading.RLock()
        self._closing = False

    def _response(self, run, entry):
        if entry.error:
            return {"status": "resume_required", "run": run, "error": entry.error}
        if entry.finished:
            return dict(entry.result)
        return {"status": "pending", "run": run,
                **{key: entry.result[key] for key in ("phase", "batches", "durable_generation")
                   if key in entry.result}}

    def status(self, source, run):
        with self._lock:
            entry = self._runs.get(run)
            if entry is None or entry.identity[0] != source:
                return None
            return self._response(run, entry)

    def start(self, *, source, target, topic, run, expected, advance, conventions_policy="retain"):
        try:
            if str(UUID(run)) != run:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise TransferRefused("invalid_conversion_run") from None
        identity = (source, target, topic, expected, conventions_policy)
        with self._lock:
            if self._closing:
                raise TransferRefused("conversion_service_stopping")
            previous = self._runs.get(run)
            if previous is not None:
                if previous.identity[0] != source:
                    raise TransferRefused("conversion_run_unavailable")
                if previous.identity != identity:
                    raise TransferRefused("run_id_reused")
                if not previous.finished or not previous.error:
                    return self._response(run, previous)
            if any(not entry.finished for entry in self._runs.values()):
                raise TransferRefused("conversion_execution_busy")
            if len(self._runs) >= self.limit and previous is None:
                oldest = next(iter(self._runs))
                del self._runs[oldest]  # All entries are finished; journals are not removed.
            entry = _Run(identity)
            self._runs[run] = entry
            context = copy_context()
            entry.thread = threading.Thread(target=lambda: context.run(self._drive, run, entry, advance),
                                            name="vinc-conversion-coordinator", daemon=True)
            entry.thread.start()
            return self._response(run, entry)

    def _drive(self, run, entry, advance):
        from .topic_conversion_batches import PHASES
        deadline = self.clock() + self.work_seconds
        try:
            while True:
                result = {}

                def work():
                    # The public generic job surface must never contain run IDs,
                    # topic paths, fingerprints or conversion results.
                    nonlocal result
                    try:
                        result = advance()
                    except Exception:
                        raise RuntimeError("conversion_step_failed") from None
                    return {"kind": "conversion-batch", "executed": True}

                with self._lock:
                    if self._closing:
                        entry.error = "conversion_service_stopping"
                        return
                    if self.clock() >= deadline:
                        entry.error = "conversion_work_budget_reached"
                        return
                    entry.job = self.queue.submit("topic-conversion.batch", work)
                entry.job._done.wait()
                with self._lock:
                    if entry.job.status != "done":
                        entry.error = "conversion_step_uncertain"
                        return
                    if result.get("run") != run or result.get("status") not in (
                            "prepared", "active", "cancelled", *PHASES):
                        entry.error = "conversion_step_unverified"
                        return
                    entry.result = dict(result)
                    if result["status"] in ("graph_committed", "active", "cancelled"):
                        return
        except Exception:
            with self._lock:
                entry.error = "conversion_step_uncertain"
        finally:
            with self._lock:
                entry.finished = True

    def close(self):
        # Caller drains its writer queue before closing the store. Holding the
        # same lock as submit prevents any new batch after this boundary.
        with self._lock:
            self._closing = True

    def forget(self, source, run):
        """Drop only a finished cache entry after a native release or cancellation."""
        with self._lock:
            entry = self._runs.get(run)
            if entry is not None and entry.identity[0] == source and entry.finished:
                del self._runs[run]
