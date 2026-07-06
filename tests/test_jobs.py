# -*- coding: utf-8 -*-
"""JobQueue — 단일 워커 직렬화(R4)·동기/비동기·오류 격리·드레인 종료(설계 §11 P2)."""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.jobs import JobQueue, JobError, DONE, ERROR, RUNNING  # noqa: E402


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.jq = JobQueue()

    def tearDown(self):
        self.jq.shutdown(drain=False)

    def test_run_sync_returns_result(self):
        res = self.jq.run_sync("t", lambda: {"ok": 1})
        self.assertEqual(res, {"ok": 1})

    def test_run_sync_wraps_non_dict(self):
        res = self.jq.run_sync("t", lambda: 42)
        self.assertEqual(res, {"result": 42})

    def test_submit_is_async_then_completes(self):
        job = self.jq.submit("t", lambda: {"done": True})
        job._done.wait(timeout=2.0)
        got = self.jq.get(job.id)
        self.assertEqual(got.status, DONE)
        self.assertEqual(got.result, {"done": True})
        self.assertEqual(got.to_dict()["job_id"], job.id)

    def test_error_isolated_and_raised_in_sync(self):
        def boom():
            raise ValueError("nope")
        with self.assertRaises(JobError):
            self.jq.run_sync("t", boom)
        # 워커는 살아있어야 한다(오류 격리) — 다음 잡 정상 처리
        self.assertEqual(self.jq.run_sync("t", lambda: {"alive": 1}), {"alive": 1})

    def test_error_status_recorded(self):
        job = self.jq.submit("t", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        job._done.wait(timeout=2.0)
        got = self.jq.get(job.id)
        self.assertEqual(got.status, ERROR)
        self.assertIn("RuntimeError", got.error)

    def test_single_worker_serializes_writes(self):
        """두 잡이 동시에 실행되지 않음(R4 — 유일 writer). 겹침 감지 카운터."""
        overlap = {"max": 0, "cur": 0}
        lock = threading.Lock()
        gate = threading.Event()

        def work():
            with lock:
                overlap["cur"] += 1
                overlap["max"] = max(overlap["max"], overlap["cur"])
            gate.wait(0.05)   # 겹칠 기회를 준다
            with lock:
                overlap["cur"] -= 1
            return {"ok": 1}

        jobs = [self.jq.submit("t", work) for _ in range(5)]
        for j in jobs:
            j._done.wait(timeout=3.0)
        self.assertEqual(overlap["max"], 1, "잡이 동시 실행됨 — 단일 워커 직렬화 위반(R4)")

    def test_get_unknown_job_is_none(self):
        self.assertIsNone(self.jq.get("job-999"))

    def test_shutdown_drains_pending(self):
        jq = JobQueue()
        seen = []
        for i in range(3):
            jq.submit("t", lambda i=i: seen.append(i) or {"i": i})
        jq.shutdown(drain=True)     # 남은 잡 모두 처리 후 종료
        self.assertEqual(sorted(seen), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
