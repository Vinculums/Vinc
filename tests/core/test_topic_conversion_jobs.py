"""Conversion scheduling preserves queue acknowledgement and owner isolation."""
import threading
from uuid import uuid4

import pytest

from api.jobs import JobQueue
from api.topic_conversion_jobs import ConversionJobs
from store.topic_transfer import TransferRefused


def request(run=None):
    return dict(source="user:synthetic", target="team:synthetic", topic="synthetic/topic",
                run=run or str(uuid4()), expected="a" * 64)


def finish(coordinator, run):
    coordinator._runs[run].thread.join(5)
    assert not coordinator._runs[run].thread.is_alive()


def test_duplicate_requests_owner_isolation_and_yield_between_batches():
    queue = JobQueue()
    coordinator = ConversionJobs(queue, work_seconds=30)
    args = request()
    entered, release = threading.Event(), threading.Event()
    seen = []

    def advance():
        seen.append("batch")
        if len(seen) == 1:
            entered.set()
            assert release.wait(5)
            return {"run": args["run"], "status": "prepared", "batches": 1}
        return {"run": args["run"], "status": "graph_committed", "batches": 2}

    try:
        assert coordinator.start(**args, advance=advance)["status"] == "pending"
        assert entered.wait(5)
        assert coordinator.start(**args, advance=advance)["status"] == "pending"
        assert coordinator.status("user:other", args["run"]) is None
        with pytest.raises(TransferRefused, match="run_id_reused"):
            coordinator.start(**{**args, "topic": "other"}, advance=advance)
        with pytest.raises(TransferRefused, match="conversion_execution_busy"):
            coordinator.start(**request(), advance=advance)
        unrelated = queue.submit("unrelated", lambda: seen.append("unrelated") or {})
        first = coordinator._runs[args["run"]].job
        release.set()
        finish(coordinator, args["run"])
        assert unrelated._done.is_set()
        assert seen == ["batch", "unrelated", "batch"]
        assert first.result == {"kind": "conversion-batch", "executed": True}
        assert coordinator.status(args["source"], args["run"])["status"] == "graph_committed"
        coordinator.start(**args, advance=advance)
        assert len(seen) == 3
    finally:
        release.set()
        coordinator.close()
        queue.shutdown()


def test_failed_completion_hook_stops_and_only_explicit_resume_advances():
    fail = [True]
    calls = []

    def hook(job, result):
        if fail[0]:
            raise RuntimeError("synthetic publication failure")

    queue = JobQueue(on_before_done=hook)
    coordinator = ConversionJobs(queue, work_seconds=30)
    args = request()

    def advance():
        calls.append(1)
        return {"run": args["run"], "status": "graph_committed"}

    try:
        coordinator.start(**args, advance=advance)
        finish(coordinator, args["run"])
        assert coordinator.status(args["source"], args["run"])["status"] == "resume_required"
        assert len(calls) == 1
        fail[0] = False
        coordinator.start(**args, advance=advance)
        finish(coordinator, args["run"])
        assert coordinator.status(args["source"], args["run"])["status"] == "graph_committed"
        assert len(calls) == 2
    finally:
        coordinator.close()
        queue.shutdown()


def test_shutdown_prevents_next_batch_and_does_not_claim_completion():
    queue = JobQueue()
    coordinator = ConversionJobs(queue, work_seconds=30)
    args = request()
    calls = []

    def advance():
        calls.append(1)
        coordinator.close()
        return {"run": args["run"], "status": "prepared"}

    try:
        coordinator.start(**args, advance=advance)
        finish(coordinator, args["run"])
        assert coordinator.status(args["source"], args["run"])["status"] == "resume_required"
        assert len(calls) == 1
        with pytest.raises(TransferRefused, match="conversion_service_stopping"):
            coordinator.start(**args, advance=advance)
    finally:
        queue.shutdown()


def test_budget_and_unverified_results_never_become_success():
    now = [0]
    queue = JobQueue()
    coordinator = ConversionJobs(queue, work_seconds=10, clock=lambda: now[0])
    args = request()

    def advance():
        now[0] = 11
        return {"run": args["run"], "status": "prepared"}

    try:
        coordinator.start(**args, advance=advance)
        finish(coordinator, args["run"])
        assert coordinator.status(args["source"], args["run"])["error"] == "conversion_work_budget_reached"
        coordinator.start(**args, advance=lambda: {"run": str(uuid4()), "status": "active"})
        finish(coordinator, args["run"])
        assert coordinator.status(args["source"], args["run"])["error"] == "conversion_step_unverified"
    finally:
        coordinator.close()
        queue.shutdown()
