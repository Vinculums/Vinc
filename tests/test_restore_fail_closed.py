# -*- coding: utf-8 -*-
"""영속 스냅샷 **복원 실패는 기동을 막는다**(2026-08-09 회귀).

실사고. `VINC_STORE_LOCAL=on` 을 켰더니 복원이 이렇게 실패했다:

    vinc: store restore — error: PermissionError: [Errno 13] Permission denied
    {"host": "127.0.0.1", "port": 8787, ..., "driver": "ladybug"}

두 줄 사이에 아무 분기가 없었다 — 상태를 **찍기만 하고** `server.serve()` 로
내려갔고, 스토어가 빈 DB 로 새로 생성됐다. 그리고 다음 주기 스냅샷이 그 빈 DB 를
영속본 위에 덮었다: **60.72MB → 0.00MB.**

`store/persistence.py` 는 "전 구간 fail-soft" 를 원칙으로 적고 있었는데, 그 원칙이
여기서는 데이터 전손 경로였다. 갈래가 필요하다:

  저장 실패 → fail-soft.  작업본에 옳은 데이터가 있고 잃는 건 이번 회차뿐이다.
  복원 실패 → fail-closed. 데이터가 **없다**. 진행하면 빈 그래프를 서빙하면서
              영속본까지 파괴한다. 멈추면 사람이 보고 고친다.

⚠ 이 테스트는 `restore_state_is_fatal` 만 검사하지 않는다 — **`main(["serve", ...])`
로 몰아서** 판정이 실제 기동 경로에 걸려 있는지 본다. 헬퍼만 검사하면 "판정 함수는
멀쩡한데 아무도 안 부르는" 상태를 통과시킨다(그게 정확히 사고 당시의 모양이었다).
"""
from __future__ import annotations

import os
import tempfile
import unittest

from api import cli
from api import server as srv
from store import persistence


class _Recorder:
    """`server.serve` 대역 — 불렸는지만 기록한다. 불리면 스토어가 열린 것이다."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return None


class _Patched:
    """`restore_if_missing` 를 고정 상태로, `server.serve` 를 대역으로 바꾼다."""

    def __init__(self, state: str, *, allow_empty: bool | None = None):
        self.state, self.allow_empty = state, allow_empty
        self.rec = _Recorder()

    def __enter__(self):
        self._restore = persistence.restore_if_missing
        self._serve = srv.serve
        self._env = os.environ.get(persistence.BOOTSTRAP_ENV)
        persistence.restore_if_missing = lambda *_a, **_k: self.state
        srv.serve = self.rec
        if self.allow_empty is None:
            os.environ.pop(persistence.BOOTSTRAP_ENV, None)
        else:
            os.environ[persistence.BOOTSTRAP_ENV] = "1" if self.allow_empty else "0"
        return self.rec

    def __exit__(self, *exc):
        persistence.restore_if_missing = self._restore
        srv.serve = self._serve
        if self._env is None:
            os.environ.pop(persistence.BOOTSTRAP_ENV, None)
        else:
            os.environ[persistence.BOOTSTRAP_ENV] = self._env
        return False


def _serve(state: str, *, allow_empty: bool | None = None):
    """`main(["serve", ...])` 를 그 상태로 돌리고 (종료코드, serve 호출수) 를 낸다."""
    with _Patched(state, allow_empty=allow_empty) as rec:
        with tempfile.TemporaryDirectory() as d:
            code = cli.main(["serve", "--data", os.path.join(d, "db"), "--port", "0"])
    return code, rec.calls


class ServeGateTest(unittest.TestCase):

    def test_restore_error_stops_boot(self):
        """회귀 본체 — 복원이 실패하면 스토어를 **열지 않는다**."""
        code, calls = _serve("error: PermissionError: [Errno 13] Permission denied")
        self.assertEqual(calls, 0, "복원 실패인데 스토어를 열었다 — 빈 그래프 서빙 경로")
        self.assertEqual(code, 1, "실패는 0 이 아닌 종료코드여야 엔트리포인트가 알아챈다")

    def test_empty_stops_boot_unless_explicitly_allowed(self):
        """스냅샷이 안 보이는 것과 '이 배포에 데이터가 없다' 는 다르다.

        마운트가 늦거나 읽기 권한이 없어도 `empty` 가 나오고, 그때 진행하면
        error 갈래와 **결과가 같다**(빈 그래프 → 영속본 덮어쓰기)."""
        code, calls = _serve("empty")
        self.assertEqual((code, calls), (1, 0), "empty 를 그냥 통과시키면 같은 사고가 난다")

        code, calls = _serve("empty", allow_empty=True)
        self.assertEqual(calls, 1, "진짜 첫 기동은 옵트인으로 열려 있어야 한다")

    def test_healthy_states_boot_normally(self):
        """기능 꺼짐 · 작업본 존재 · 복원 성공은 그대로 뜬다."""
        for state in ("off", "present", "restored"):
            with self.subTest(state=state):
                _, calls = _serve(state)
                self.assertEqual(calls, 1, f"{state} 인데 기동을 막았다")


class VerdictTest(unittest.TestCase):
    """판정 함수 자체 — 통과 목록이 **화이트리스트**여야 한다."""

    def test_only_three_states_pass(self):
        for ok in ("off", "present", "restored"):
            self.assertIsNone(persistence.restore_state_is_fatal(ok), ok)

    def test_unknown_state_is_fatal(self):
        """모르는 상태를 통과시키면 안 된다 — 나중에 상태가 하나 늘 때
        조용히 열리는 것이 이 사고의 재발 경로다."""
        for bad in ("error: OSError", "weird", "", "partial"):
            with self.subTest(state=bad):
                self.assertIsNotNone(persistence.restore_state_is_fatal(bad))

    def test_message_says_what_to_do(self):
        """사유만 적고 조치를 안 적으면 새벽에 로그를 보는 사람이 못 고친다."""
        msg = persistence.restore_state_is_fatal("empty")
        self.assertIn(persistence.BOOTSTRAP_ENV, msg)
        msg = persistence.restore_state_is_fatal("error: PermissionError")
        self.assertIn("영속본", msg)


if __name__ == "__main__":
    unittest.main()
