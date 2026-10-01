# -*- coding: utf-8 -*-
"""JobQueue — 단일 워커 직렬화(R4)·동기/비동기·오류 격리·드레인 종료(설계 §11 P2)."""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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

    # ── 워커 내구성(2026-07-31 실측 사고) ──────────────────────────────────
    # `except Exception` 은 잡 격리를 약속만 하고 못 지켰다: SystemExit 은
    # BaseException 이라 그대로 빠져나가 워커 스레드를 죽였고, 그 뒤 모든 쓰기가
    # 큐에 쌓인 채 영구 무응답이 됐다. 아래 셋이 그 재발을 막는다.

    def test_system_exit_does_not_kill_worker(self):
        """SystemExit 을 던지는 잡 — 잡만 실패하고 워커는 산다.

        실제 경로: 임베딩 스택이 없는 기기에서 `/doc/reembed` 잡이 라이브러리의
        sys.exit() 를 밟았다."""
        def suicidal():
            raise SystemExit(1)
        with self.assertRaises(JobError):
            self.jq.run_sync("t", suicidal)
        self.assertTrue(self.jq.worker_alive(), "SystemExit 이 워커를 죽였다")
        self.assertEqual(self.jq.run_sync("t", lambda: {"alive": 1}), {"alive": 1})

    def test_keyboard_interrupt_does_not_kill_worker(self):
        """SystemExit 만의 특례가 아니다 — BaseException 전반을 잡는지 본다."""
        def interrupted():
            raise KeyboardInterrupt()
        with self.assertRaises(JobError):
            self.jq.run_sync("t", interrupted)
        self.assertTrue(self.jq.worker_alive())
        self.assertEqual(self.jq.run_sync("t", lambda: {"alive": 1}), {"alive": 1})

    def test_worker_revives_if_it_ever_dies(self):
        """방어가 뚫려 워커가 죽더라도 프로세스가 벽돌이 되지 않는다(자가 복구).

        ⚠ 되살리기는 **단일 워커 불변식**(R4)을 깨면 안 된다 — 되살아난 뒤에도
        잡이 겹쳐 실행되지 않는지 함께 확인한다."""
        # 워커를 강제로 정지시킨다(sentinel 로 루프 종료 — 죽은 상태 재현).
        self.jq._q.put(None)
        for _ in range(50):
            if not self.jq.worker_alive():
                break
            threading.Event().wait(0.02)
        self.assertFalse(self.jq.worker_alive(), "워커가 멈추지 않아 이 테스트가 무의미하다")

        overlap = {"max": 0, "cur": 0}
        lock = threading.Lock()

        def work():
            with lock:
                overlap["cur"] += 1
                overlap["max"] = max(overlap["max"], overlap["cur"])
            threading.Event().wait(0.03)
            with lock:
                overlap["cur"] -= 1
            return {"ok": 1}

        jobs = [self.jq.submit("t", work) for _ in range(4)]
        for j in jobs:
            self.assertTrue(j._done.wait(timeout=5.0), "되살아난 워커가 잡을 안 돌린다")
        self.assertTrue(self.jq.worker_alive())
        self.assertEqual(overlap["max"], 1, "되살리기가 워커를 둘로 만들었다(R4 위반)")

    def test_run_sync_times_out_honestly(self):
        """무한 대기를 두지 않는다 — 막히면 정직한 실패로 끝난다."""
        gate = threading.Event()
        try:
            with self.assertRaises(JobError) as ctx:
                self.jq.run_sync("t", lambda: gate.wait(30) or {"ok": 1}, timeout=0.2)
            self.assertIn("0.2", str(ctx.exception))
        finally:
            gate.set()   # 워커가 잡을 마치고 다음 테스트로 넘어가게 풀어 준다

    def test_default_timeout_is_finite(self):
        from api.jobs import DEFAULT_SYNC_TIMEOUT
        self.assertIsNotNone(DEFAULT_SYNC_TIMEOUT)
        self.assertGreater(DEFAULT_SYNC_TIMEOUT, 0)

    def test_shutdown_drains_pending(self):
        jq = JobQueue()
        seen = []
        for i in range(3):
            jq.submit("t", lambda i=i: seen.append(i) or {"i": i})
        jq.shutdown(drain=True)     # 남은 잡 모두 처리 후 종료
        self.assertEqual(sorted(seen), [0, 1, 2])


class BeforeDoneTests(unittest.TestCase):
    def test_completion_waits_for_required_hook(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def before_done(job, result):
            self.assertEqual(job.status, RUNNING)
            self.assertEqual(result, {"written": True})
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test hook was not released")

        jq = JobQueue(on_before_done=before_done)
        self.addCleanup(jq.shutdown, drain=False)
        job = jq.submit("write", lambda: {"written": True})
        self.assertTrue(entered.wait(2.0))
        self.assertEqual(job.status, RUNNING)
        self.assertIsNone(job.result)
        self.assertFalse(job._done.is_set())
        release.set()
        self.assertTrue(job._done.wait(2.0))
        self.assertEqual(job.status, DONE)
        self.assertEqual(job.result, {"written": True})

    def test_hook_failure_cannot_be_observed_as_success(self):
        mutations = []

        def before_done(_job, _result):
            if len(mutations) == 1:
                raise RuntimeError("persistent promotion failed; commit uncertain")

        jq = JobQueue(on_before_done=before_done)
        self.addCleanup(jq.shutdown, drain=False)
        with self.assertRaisesRegex(JobError, "commit uncertain"):
            jq.run_sync("write", lambda: mutations.append("local") or {"written": True})
        failed = jq.get("job-1")
        self.assertEqual(mutations, ["local"], "failure does not roll back the live store")
        self.assertEqual(failed.status, ERROR)
        self.assertIsNone(failed.result)
        self.assertIn("commit uncertain", failed.error)
        self.assertEqual(jq.run_sync("write", lambda: mutations.append("next") or
                                     {"written": True}), {"written": True})


class _Idle:
    """`on_idle` 기록기 — 호출마다 `wrote` 를 담고 첫 호출에 이벤트를 푼다."""

    def __init__(self):
        self.calls: list[bool] = []
        self.rang = threading.Event()

    def __call__(self, *, wrote: bool):
        self.calls.append(wrote)
        self.rang.set()


class IdleTickTests(unittest.TestCase):
    """유휴 틱 — 잡이 없어도 워커가 깨어 `on_idle` 을 부른다.

    없을 때 무슨 일이 났는가(2026-09-06): 잡 완료가 유일한 틱이라 버스트의 마지막
    쓰기는 **다음 쓰기가 올 때까지** 아무 후처리도 못 밟았다. 허브는 ack 를 준
    문서를 그렇게 잃었다 — 노출 창이 스냅샷 주기가 아니라 트래픽이었다."""

    def _queue(self, idle, **kw) -> JobQueue:
        jq = JobQueue(on_idle=idle, **kw)
        self.addCleanup(jq.shutdown, drain=False)
        return jq

    def test_tick_fires_with_no_job_at_all(self):
        """회귀 본체 — 큐에 아무것도 안 넣었는데 틱이 온다."""
        idle = _Idle()
        self._queue(idle, tick_s=0.01)
        self.assertTrue(idle.rang.wait(5.0), "잡이 없으면 워커가 영영 안 깬다")
        self.assertEqual(idle.calls[0], False, "쓰기가 없었는데 있었다고 말했다")

    def test_a_finished_job_reports_a_write(self):
        """틱을 안 걸면 예전 그대로 — 잡이 끝날 때만, 그리고 그때는 wrote=True."""
        idle = _Idle()
        jq = self._queue(idle)
        jq.run_sync("t", lambda: {"ok": 1})
        self.assertTrue(idle.rang.wait(5.0))
        self.assertEqual(idle.calls, [True])

    def test_a_tick_stops_once_shutdown_has_started(self):
        """종료가 시작되면 틱은 멈춘다.

        `shutdown()` 은 `_q.join()` **앞에서** `_stopping` 을 세우고, 그 뒤 호출부가
        메인 스레드에서 마지막 스냅샷과 store.close() 를 한다(`api/server.py` close).
        그때 워커가 유휴 틱으로 스냅샷(드라이버 close→reopen) 안에 있으면 5초 join
        이 만료되고 같은 경로를 두 스레드가 동시에 밟는다. 틱이 없던 시절에는 잡이
        막 끝난 찰나에만 열리던 창인데, 틱이 그것을 **완전 유휴 허브**에서도 열었다.

        여기서 `_stopping` 을 직접 세우는 것은 `shutdown()` 이 하는 그대로다 — 그
        메서드는 플래그가 이미 서 있으면 되돌아가므로(재진입 가드) 테스트에서 다시
        부를 수 없다. 없음을 재는 것이라 대기가 필요하다: 틱 주기의 30배."""
        second = threading.Event()
        calls = []

        def on_idle(*, wrote):
            calls.append(wrote)
            if len(calls) == 1:
                jq._stopping.set()
            else:
                second.set()

        jq = JobQueue(on_idle=on_idle, tick_s=0.01)
        self.addCleanup(jq._q.put, None)          # 워커 루프 종료(shutdown 은 가드에 막힌다)
        self.assertFalse(second.wait(0.3),
                         "종료가 시작된 뒤에도 유휴 틱이 계속 떴다 — close() 가 "
                         "메인 스레드에서 밟는 close→reopen 과 겹친다")

    def test_ticking_does_not_disturb_job_order_or_draining(self):
        """틱은 대기에 상한을 둘 뿐이다. FIFO 도 드레인 회계도 그대로여야 한다 —
        빈 큐에서 `task_done()` 을 부르면 여기서 ValueError 로 터진다."""
        seen = []
        jq = JobQueue(on_idle=_Idle(), tick_s=0.01)
        for i in range(3):
            jq.submit("t", lambda i=i: seen.append(i) or {"i": i})
        jq.shutdown(drain=True)
        self.assertEqual(seen, [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
