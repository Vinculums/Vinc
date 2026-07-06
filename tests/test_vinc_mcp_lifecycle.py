# -*- coding: utf-8 -*-
"""vinc_mcp/lifecycle.py — 실 서브프로세스(`python -m api.cli serve`)로 attach/spawn/
release 왕복을 검증한다(§3 결정 2). mcp SDK·드라이버 불요 — 사이드카는 store=None
으로도 기동·health 응답하므로 이 환경(kuzu 미설치)에서도 실제로 돈다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import lifecycle as lc  # noqa: E402


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lc._pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


class LifecycleRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._handles = []

    def tearDown(self):
        # 테스트 실패로 release 가 못 불린 경우에도 프로세스가 남지 않게 정리.
        for pid in self._handles:
            if lc._pid_alive(pid):
                os.kill(pid, 9)

    def test_first_call_spawns_second_call_attaches(self):
        h1 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())
        self._handles.append(h1.pid)
        self.assertTrue(h1.spawned_by_us)
        self.assertTrue(lc._health_ok(h1.host, h1.port))

        h2 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())
        self.assertFalse(h2.spawned_by_us)
        self.assertEqual(h2.pid, h1.pid)
        self.assertEqual(h2.port, h1.port)

        state = lc._read_state(lc._lock_path(self.tmp))
        self.assertEqual(state["refcount"], 2)

        lc.release(self.tmp, h2)   # refcount 2 -> 1, 아직 종료 안 함
        self.assertTrue(lc._pid_alive(h1.pid))
        self.assertEqual(lc._read_state(lc._lock_path(self.tmp))["refcount"], 1)

        lc.release(self.tmp, h1)   # refcount 1 -> 0, 종료
        self.assertTrue(_wait_gone(h1.pid), "사이드카가 refcount 0 에서 종료되지 않음")
        self.assertIsNone(lc._read_state(lc._lock_path(self.tmp)))

    def test_release_terminates_regardless_of_which_handle_hits_zero(self):
        # 마지막으로 release() 를 부른 쪽이 spawner 가 아니어도(attacher) 종료돼야 한다
        # (§3 "종료 순서 무관" — lockfile 의 pid 로 종료하지, handle.spawned_by_us 로
        # 게이트하지 않음).
        h1 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())
        self._handles.append(h1.pid)
        h2 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())

        lc.release(self.tmp, h1)   # spawner 가 먼저 나감: refcount 2 -> 1
        self.assertTrue(lc._pid_alive(h1.pid))
        lc.release(self.tmp, h2)   # attacher(spawned_by_us=False) 가 마지막: 0 -> 종료
        self.assertTrue(_wait_gone(h1.pid))

    def test_dead_pid_in_lockfile_triggers_respawn(self):
        h1 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())
        self._handles.append(h1.pid)
        os.kill(h1.pid, 9)
        self.assertTrue(_wait_gone(h1.pid))

        h2 = lc.attach_or_spawn(self.tmp, parent_pid=os.getpid())
        self._handles.append(h2.pid)
        self.assertTrue(h2.spawned_by_us)
        self.assertNotEqual(h2.pid, h1.pid)
        lc.release(self.tmp, h2)
        self.assertTrue(_wait_gone(h2.pid))


if __name__ == "__main__":
    unittest.main()
