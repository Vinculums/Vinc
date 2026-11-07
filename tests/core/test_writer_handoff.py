# -*- coding: utf-8 -*-
"""허브 writer 인계 — 새 리비전은 이전 writer 의 넘김을 본 **뒤에만** 복원한다.

지키는 것:

1. 넘김 표식은 자격을 가진 리비전만, 마지막 스냅샷이 ok 일 때만 쓴다.
2. 새 리비전은 이전 writer 가 넘길 때까지 기다리고, 넘김을 보면 곧장 진행한다.
3. 나보다 새 리비전이 writer 면 낡은 리비전이다 — 복원도 자격 주장도 하지 않는다.
4. 같은 리비전의 재시작은 기다리지 않는다. 넘긴 뒤 다시 떴으면 제 넘김 표식을 지운다.
5. 리비전 이름이나 스냅샷 경로가 없으면(데스크탑·테스트) 아무것도 하지 않는다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import handoff, persistence                          # noqa: E402

OLD, NEW, NEWER = "vinc-hub--0000446", "vinc-hub--0000447", "vinc-hub--0000448"


class _Clock:
    """`decide()` 의 기다림을 실제로 자지 않고 돌린다. sleep 마다 시계를 민다."""

    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.slept = 0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept += 1
        self.t += s
        if self.on_sleep:
            self.on_sleep(self.slept)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snap = os.path.join(self.tmp.name, "db")
        self.logs: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def _put(self, suffix: str, value: str) -> None:
        with open(self.snap + suffix, "w", encoding="utf-8") as f:
            f.write(value + "\n")

    def _get(self, suffix: str) -> str | None:
        try:
            with open(self.snap + suffix, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return None

    def _decide(self, mine: str, clock: _Clock | None = None, wait_s: float = 10):
        clock = clock or _Clock()
        with mock.patch.dict(os.environ, {persistence.REVISION_ENV: mine}):
            return handoff.decide(self.snap, wait_s=wait_s, poll_s=1, clock=clock.now,
                                  sleep=clock.sleep, log=self.logs.append), clock


class MarkReleased(_Case):
    def test_off_without_a_revision(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(handoff.mark_released(self.snap), "off")
        self.assertIsNone(self._get(handoff.RELEASED_SUFFIX))

    def test_the_writer_releases(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        with mock.patch.dict(os.environ, {persistence.REVISION_ENV: OLD}):
            self.assertEqual(handoff.mark_released(self.snap), "released")
        self.assertEqual(self._get(handoff.RELEASED_SUFFIX), OLD)

    def test_a_revision_that_lost_the_claim_does_not_release(self):
        self._put(persistence.WRITER_SUFFIX, NEW)
        with mock.patch.dict(os.environ, {persistence.REVISION_ENV: OLD}):
            self.assertTrue(handoff.mark_released(self.snap).startswith("skipped"))
        self.assertIsNone(self._get(handoff.RELEASED_SUFFIX))


class Decide(_Case):
    def test_off_without_a_revision(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(handoff.decide(self.snap), "proceed: off")

    def test_no_writer_yet(self):
        verdict, clock = self._decide(NEW)
        self.assertEqual(verdict, "proceed: no writer")
        self.assertEqual(clock.slept, 0)

    def test_own_restart_does_not_wait(self):
        self._put(persistence.WRITER_SUFFIX, NEW)
        verdict, clock = self._decide(NEW)
        self.assertEqual(verdict, "proceed: own restart")
        self.assertEqual(clock.slept, 0)

    def test_a_newer_writer_makes_me_stale(self):
        self._put(persistence.WRITER_SUFFIX, NEWER)
        verdict, _ = self._decide(NEW)
        self.assertEqual(verdict, f"stale: {NEWER}")

    def test_proceeds_at_once_when_the_previous_writer_already_released(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        self._put(handoff.RELEASED_SUFFIX, OLD)
        verdict, clock = self._decide(NEW)
        self.assertEqual(verdict, f"proceed: {OLD} released")
        self.assertEqual(clock.slept, 0)

    def test_waits_for_the_release_and_goes_when_it_lands(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        clock = _Clock(on_sleep=lambda n: n == 3 and self._put(handoff.RELEASED_SUFFIX, OLD))
        verdict, clock = self._decide(NEW, clock)
        self.assertEqual(verdict, f"proceed: {OLD} released")
        self.assertEqual(clock.slept, 3, "넘김을 본 즉시 나가야 한다")

    def test_an_older_release_does_not_count(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        self._put(handoff.RELEASED_SUFFIX, "vinc-hub--0000445")
        verdict, clock = self._decide(NEW, wait_s=5)
        self.assertEqual(verdict, f"proceed: {OLD} did not release")
        self.assertGreaterEqual(clock.t, 5, "상한까지 기다려야 한다")
        self.assertTrue(any("did not release" in m for m in self.logs), "넘기지 못한 것을 크게 남긴다")

    def test_a_newer_claim_during_the_wait_makes_me_stale(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        clock = _Clock(on_sleep=lambda n: n == 2 and self._put(persistence.WRITER_SUFFIX, NEWER))
        verdict, _ = self._decide(NEW, clock)
        self.assertEqual(verdict, f"stale: {NEWER}")

    def test_a_released_revision_that_comes_back_goes_at_once_and_clears_its_release(self):
        self._put(persistence.WRITER_SUFFIX, OLD)
        self._put(handoff.RELEASED_SUFFIX, OLD)
        verdict, clock = self._decide(OLD)
        self.assertEqual(verdict, "proceed: own restart")
        self.assertEqual(clock.slept, 0, "정상 종료 뒤의 재시작이 기다리면 그만큼 다운타임이 는다")
        self.assertIsNone(self._get(handoff.RELEASED_SUFFIX),
                          "넘김을 지워야 다음 새 리비전이 이 리비전의 넘김을 다시 기다린다")

    def test_a_released_revision_that_comes_back_after_a_newer_claim_is_stale(self):
        self._put(persistence.WRITER_SUFFIX, NEW)
        self._put(handoff.RELEASED_SUFFIX, OLD)
        verdict, _ = self._decide(OLD)
        self.assertEqual(verdict, f"stale: {NEW}")


class Replicas(_Case):
    """같은 리비전의 재시작: 플랫폼이 새 레플리카를 먼저 띄우고 옛 것을 나중에 내린다."""
    REV = "vinc-hub--0000452"
    A, B = REV + "@vinc-hub--0000452-aaa", REV + "@vinc-hub--0000452-bbb"

    def _decide_as(self, identity, clock=None, wait_s=10):
        rev, _, rep = identity.partition("@")
        env = {persistence.REVISION_ENV: rev}
        if rep:
            env[persistence.REPLICA_ENV] = rep
        clock = clock or _Clock()
        with mock.patch.dict(os.environ, env):
            if not rep:
                os.environ.pop(persistence.REPLICA_ENV, None)
            return handoff.decide(self.snap, wait_s=wait_s, poll_s=1, clock=clock.now,
                                  sleep=clock.sleep, log=self.logs.append), clock

    def test_another_replica_of_my_revision_is_waited_for(self):
        self._put(persistence.WRITER_SUFFIX, self.A)
        clock = _Clock(on_sleep=lambda n: n == 2 and self._put(handoff.RELEASED_SUFFIX, self.A))
        verdict, clock = self._decide_as(self.B, clock)
        self.assertEqual(verdict, f"proceed: {self.A} released")
        self.assertEqual(clock.slept, 2)

    def test_a_replica_that_already_released_is_followed_at_once(self):
        self._put(persistence.WRITER_SUFFIX, self.A)
        self._put(handoff.RELEASED_SUFFIX, self.A)
        verdict, clock = self._decide_as(self.B)
        self.assertEqual(verdict, f"proceed: {self.A} released")
        self.assertEqual(clock.slept, 0)

    def test_the_same_replica_restarting_in_place_does_not_wait(self):
        self._put(persistence.WRITER_SUFFIX, self.A)
        verdict, clock = self._decide_as(self.A)
        self.assertEqual(verdict, "proceed: own restart")
        self.assertEqual(clock.slept, 0)

    def test_an_old_marker_without_a_replica_reads_as_own_restart(self):
        self._put(persistence.WRITER_SUFFIX, self.REV)
        self._put(handoff.RELEASED_SUFFIX, self.REV)
        verdict, clock = self._decide_as(self.B)
        self.assertEqual(verdict, "proceed: own restart")
        self.assertIsNone(self._get(handoff.RELEASED_SUFFIX))

    def test_a_third_replica_claiming_during_the_wait_makes_me_stale(self):
        third = self.REV + "@vinc-hub--0000452-ccc"
        self._put(persistence.WRITER_SUFFIX, self.A)
        clock = _Clock(on_sleep=lambda n: n == 2 and self._put(persistence.WRITER_SUFFIX, third))
        verdict, _ = self._decide_as(self.B, clock)
        self.assertEqual(verdict, f"stale: {third}")

    def test_only_the_holding_replica_releases(self):
        self._put(persistence.WRITER_SUFFIX, self.B)
        with mock.patch.dict(os.environ, {persistence.REVISION_ENV: self.REV,
                                          persistence.REPLICA_ENV: "vinc-hub--0000452-aaa"}):
            self.assertTrue(handoff.mark_released(self.snap).startswith("skipped"))
        with mock.patch.dict(os.environ, {persistence.REVISION_ENV: self.REV,
                                          persistence.REPLICA_ENV: "vinc-hub--0000452-bbb"}):
            self.assertEqual(handoff.mark_released(self.snap), "released")
        self.assertEqual(self._get(handoff.RELEASED_SUFFIX), self.B)


class Main(_Case):
    def test_exit_codes(self):
        self._put(persistence.WRITER_SUFFIX, NEWER)
        env = {persistence.REVISION_ENV: NEW, "VINC_STORE_SNAPSHOT": self.snap}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(handoff.main(["wait"]), handoff.EXIT_STALE)
        self._put(persistence.WRITER_SUFFIX, NEW)
        with mock.patch.dict(os.environ, env):
            self.assertEqual(handoff.main(["wait"]), handoff.EXIT_PROCEED)
        self.assertEqual(handoff.main([]), 2)


if __name__ == "__main__":
    unittest.main()
