# -*- coding: utf-8 -*-
"""쓰기 대기 집계(`store.write_waits`)와 그것을 부르는 쓰기 큐(`api.jobs.JobQueue`).

잠그는 것:
- 대기 시간은 기다리는 동안 워커가 돌린 일의 겹친 시간으로 나뉘어 (기다린 종류, 앞에서
  돈 종류) 쌍에 붙는다. 잡 사이 후처리 틱도 앞에서 돈 일로 센다.
- `run_sync`(누가 붙잡고 기다림)와 `submit`(폴링)을 가르고, 오류·시간 초과를 센다.
- 종류·쌍은 상한을 넘으면 `other` 로 접힌다(메모리가 묶인다).
- 한 시간에 한 번만 로그 한 줄을 내고, 그 줄은 JSON 이다.
- 실제 큐에서 긴 잡 뒤에 넣은 짧은 잡이 그 쌍으로 잡힌다.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import write_waits as W  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.lines: list[str] = []
        self.w = W.WriteWaits(self.clock, log=self.lines.append)

    def test_대기는_앞에서_돈_일로_나뉜다(self):
        w, c = self.w, self.clock
        embed, save = object(), object()
        w.enqueued(embed, "embed.apply", sync=False)
        w.started(embed)                    # t=0 에 시작
        c.t = 0.2
        w.enqueued(save, "doc.put", sync=True)
        c.t = 1.0
        w.finished(embed, ok=True)          # 저장은 0.8 초를 임베딩 뒤에서 기다렸다
        with w.blocking():                  # 그다음 스냅샷 틱이 0.3 초
            c.t = 1.3
        w.started(save)
        c.t = 1.35
        w.finished(save, ok=False)
        snap = w.snapshot()
        pairs = {(b["waiting"], b["behind"]): b["total_ms"] for b in snap["blockers"]}
        self.assertAlmostEqual(pairs[("doc.put", "embed.apply")], 800.0, places=3)
        self.assertAlmostEqual(pairs[("doc.put", W.IDLE_TICK)], 300.0, places=3)
        put = snap["kinds"]["doc.put"]
        self.assertEqual((put["sync"], put["polled"], put["errors"]), (1, 0, 1))
        self.assertAlmostEqual(put["wait"]["max_ms"], 1100.0, places=3)   # 둘의 합
        self.assertEqual(put["wait"]["p95_le_ms"], 2000.0)                 # 구간 상한
        self.assertEqual(snap["kinds"]["embed.apply"]["polled"], 1)
        self.assertEqual(snap["queue"], {"waiting_now": 0, "running": None})

    def test_기다리는_잡이_없으면_틱은_세지_않는다(self):
        with self.w.blocking():
            self.clock.t = 5.0
        self.assertNotIn(W.IDLE_TICK, self.w.snapshot()["kinds"])

    def test_시간_초과와_store_busy_와_DDL_보유를_센다(self):
        self.w.timed_out("doc.put")
        self.w.store_busy("read")
        self.w.store_busy("read")
        token = W.current_kind.set("embed.apply")
        try:
            self.w.ddl_held(548.0)
        finally:
            W.current_kind.reset(token)
        self.w.ddl_held(12.0)               # 큐 밖에서 잡은 배타
        snap = self.w.snapshot()
        self.assertEqual(snap["kinds"]["doc.put"]["timeouts"], 1)
        self.assertEqual(snap["store_busy"], {"read": 2})
        self.assertEqual(snap["ddl_exclusive_hold"]["embed.apply"]["max_ms"], 548.0)
        self.assertIn(W.OUTSIDE_QUEUE, snap["ddl_exclusive_hold"])

    def test_종류가_넘치면_other_로_접힌다(self):
        for i in range(W.MAX_KINDS + 10):
            t = object()
            self.w.enqueued(t, f"k{i}", sync=False)
            self.w.started(t)
            self.w.finished(t, ok=True)
        kinds = self.w.snapshot()["kinds"]
        self.assertLessEqual(len(kinds), W.MAX_KINDS + 1)
        self.assertIn(W.OTHER, kinds)

    def test_로그는_한_시간에_한_줄이다(self):
        self.assertFalse(self.w.maybe_log())
        self.clock.t = W.LOG_EVERY_S + 1
        self.assertTrue(self.w.maybe_log())
        self.assertFalse(self.w.maybe_log())
        self.assertTrue(self.w.maybe_log(force=True))
        self.assertEqual(len(self.lines), 2)
        prefix, body = self.lines[0].split(" ", 1)
        self.assertEqual(prefix, W.LOG_PREFIX)
        self.assertIn("kinds", json.loads(body))

    def test_로그는_켜기_전에는_내지_않는다(self):
        quiet = W.WriteWaits(self.clock)
        self.clock.t = W.LOG_EVERY_S * 3
        self.assertFalse(quiet.maybe_log(force=True))   # CLI 의 짧은 큐: 출력 계약을 지킨다
        quiet.enable_log(self.lines.append)
        self.assertTrue(quiet.maybe_log(force=True))


class QueueTests(unittest.TestCase):
    def test_긴_잡_뒤에_넣은_저장이_그_쌍으로_잡힌다(self):
        from api.jobs import JobQueue
        waits = W.WriteWaits(log=lambda line: None)
        q = JobQueue(waits=waits)
        try:
            gate = threading.Event()
            started = threading.Event()

            def long_job():
                started.set()
                gate.wait(5)
                return {}

            q.submit("embed.apply", long_job)
            self.assertTrue(started.wait(5))
            timer = threading.Timer(0.15, gate.set)
            timer.start()
            q.run_sync("doc.put", lambda: {"ok": True}, timeout=5)
            timer.join()
        finally:
            q.shutdown()
        snap = waits.snapshot()
        pair = next(b for b in snap["blockers"]
                    if (b["waiting"], b["behind"]) == ("doc.put", "embed.apply"))
        self.assertGreaterEqual(pair["total_ms"], 100.0)
        self.assertEqual(snap["kinds"]["doc.put"]["sync"], 1)
        self.assertEqual(snap["kinds"]["embed.apply"]["polled"], 1)

    def test_잡_안의_DDL_배타는_그_잡의_종류로_센다(self):
        from api.jobs import JobQueue
        waits = W.WriteWaits(log=lambda line: None)
        q = JobQueue(waits=waits)
        try:
            q.run_sync("search.index", lambda: (waits.ddl_held(5.0), {})[1], timeout=5)
        finally:
            q.shutdown()
        self.assertIn("search.index", waits.snapshot()["ddl_exclusive_hold"])


if __name__ == "__main__":
    unittest.main()
