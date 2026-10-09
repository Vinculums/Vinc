"""A failed post-write promotion must forbid blind retries on both job surfaces."""
from unittest import mock
import threading

import pytest

from api.jobs import JobError, JobQueue
from test_server import _EmptyStore, _app, _req, running_server


def _promotion_failure(_job, _result):
    raise OSError("persistent promotion failed")


def test_sync_and_polled_post_write_failure_preserve_uncertainty():
    mutations = []
    queue = JobQueue(on_before_done=_promotion_failure)
    try:
        with pytest.raises(JobError) as caught:
            queue.run_sync("touch", lambda: mutations.append("hit") or {"access_count": 1})
        assert mutations == ["hit"]
        assert caught.value.write_may_have_applied is True
        assert caught.value.job_id == "job-1"
        status = queue.get("job-1").to_dict()
        assert status["status"] == "error" and status["result"] is None
        assert status["retryable"] is False
        assert status["write_may_have_applied"] is True
    finally:
        queue.shutdown(drain=True)


def test_sidecar_failed_promotion_exposes_reconciliation_job():
    app = _app(store=_EmptyStore())
    app.jobs._on_before_done = _promotion_failure
    with mock.patch("api.server.pref.touch", return_value={"access_count": 1}) as touch:
        with running_server(app) as base:
            status, result = _req(base, "/touch", method="POST", body={"episode": "retry-probe"})
            assert status == 500
            assert result["retryable"] is False
            assert result["write_may_have_applied"] is True
            assert result["job_id"] == "job-1"
            assert touch.call_count == 1


def test_pre_mutation_failure_does_not_claim_a_promotion_failure():
    queue = JobQueue(on_before_done=_promotion_failure)
    try:
        with pytest.raises(JobError) as caught:
            queue.run_sync("invalid", lambda: (_ for _ in ()).throw(ValueError("invalid input")))
        assert caught.value.write_may_have_applied is False
        assert "write_may_have_applied" not in queue.get("job-1").to_dict()
    finally:
        queue.shutdown(drain=True)


def test_wait_timeout_may_finish_after_the_error_and_requires_reconciliation():
    release = threading.Event()
    mutations = []
    queue = JobQueue()
    try:
        with pytest.raises(JobError) as caught:
            queue.run_sync("touch", lambda: release.wait(5) and
                           (mutations.append("hit") or {"access_count": 1}), timeout=0.01)
        assert caught.value.write_may_have_applied is True
        assert caught.value.job_id == "job-1"
        release.set()
        assert queue.get("job-1")._done.wait(5)
        assert queue.get("job-1").to_dict()["result"] == {"access_count": 1}
        assert mutations == ["hit"]
    finally:
        release.set()
        queue.shutdown(drain=True)
