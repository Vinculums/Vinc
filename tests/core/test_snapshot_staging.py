# -*- coding: utf-8 -*-
"""영속 스냅샷 쓰기는 **스테이징을 거친다**(2026-08-09).

두 결함을 막는다. 둘 다 `VINC_STORE_LOCAL=on` 의 기본 구성에서 실제로 도달한다.

**① 영속본 위에 직접 덮어쓰기.** 작업본과 스냅샷의 basename 이 같으면
`store.snapshot(dest_dir)` 이 목적지에 바로 떨어져서, 예전 코드는 rename 분기를
아예 타지 않고 수십 MB 를 영속본 위에 그대로 썼다. 복사 도중 죽으면 **유일한
영속본이 찢어진다.** 2026-08-09 라이브 구성이 정확히 그 경우였다
(work=/var/lib/vinc/db.recovered5 · snapshot=/data/db.recovered5).

**② 낡은 사이드카 잔존.** `_parts` 는 `.wal` 을 선택으로 둔다. 새 스냅샷에 `.wal`
이 없고(직전 CHECKPOINT 가 소진) 목적지에 **옛 `.wal`** 이 남아 있으면, 예전 코드는
그걸 그대로 뒀다 → 새 본 파일 + 옛 WAL 이 짝이 되어 다음 복원이 어긋난 재생을 한다.
이 프로젝트가 네 번 겪은 손상의 양상과 같은 모양이다.

스토어는 스텁이다 — 여기서 검사하는 것은 **`save_snapshot` 의 파일 조작**이지
엔진이 아니다(엔진 왕복은 라이브에서 내용으로 확인했다).
"""
from __future__ import annotations

import os
import tempfile
import unittest

from store import persistence


class _Store:
    """`snapshot(dest_dir)` 계약만 흉내 낸다 — dest_dir 에 원본 basename 으로 떨군다."""

    def __init__(self, name: str, *, body: bytes = b"NEW-DB", wal: bytes | None = b"NEW-WAL",
                 boom: bool = False):
        self.name, self.body, self.wal, self.boom = name, body, wal, boom
        self.calls = 0

    def snapshot(self, dest_dir: str) -> str:
        self.calls += 1
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, self.name)
        with open(dest, "wb") as fh:
            fh.write(self.body)
        if self.wal is not None:
            with open(dest + ".wal", "wb") as fh:
                fh.write(self.wal)
        if self.boom:                      # 복사는 끝났고 자리로 옮기기 전에 죽는다
            raise RuntimeError("죽었다 — 옮기기 전")
        return dest


def _persistent(tmp: str, name: str, body: bytes, wal: bytes | None):
    """이미 자리에 있는 영속본을 만든다."""
    p = os.path.join(tmp, name)
    with open(p, "wb") as fh:
        fh.write(body)
    if wal is not None:
        with open(p + ".wal", "wb") as fh:
            fh.write(wal)
    return p


class StagingTest(unittest.TestCase):

    def test_same_basename_still_goes_through_staging(self):
        """회귀 ①의 본체 — 이름이 같아도 목적지에 **직접** 쓰지 않는다.

        스텁이 `snapshot(dest_dir)` 을 받은 그 dest_dir 이 목적지 디렉터리면
        직접 쓴 것이다. 스테이징을 거치면 그 안의 하위 디렉터리여야 한다."""
        seen = {}

        class _Spy(_Store):
            def snapshot(self, dest_dir):
                seen["dir"] = dest_dir
                return super().snapshot(dest_dir)

        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD-DB", b"OLD-WAL")
            self.assertEqual(persistence.save_snapshot(_Spy("db"), snap_path), "ok")
            self.assertNotEqual(os.path.abspath(seen["dir"]), os.path.abspath(tmp),
                                "목적지 디렉터리에 직접 떨궜다 — 영속본을 덮어쓰는 경로")
            self.assertIn(persistence.STAGE_PREFIX, seen["dir"])
            with open(snap_path, "rb") as fh:
                self.assertEqual(fh.read(), b"NEW-DB")

    def test_crash_before_move_leaves_the_old_snapshot_intact(self):
        """스테이징의 존재 이유 — 옮기기 전에 죽으면 **기존 영속본이 산다.**

        예전 코드는 이 순간 목적지가 반쯤 덮인 상태였다."""
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD-DB", b"OLD-WAL")
            state = persistence.save_snapshot(_Store("db", boom=True), snap_path)
            self.assertTrue(state.startswith("error"), state)
            with open(snap_path, "rb") as fh:
                self.assertEqual(fh.read(), b"OLD-DB", "영속본이 훼손됐다")
            with open(snap_path + ".wal", "rb") as fh:
                self.assertEqual(fh.read(), b"OLD-WAL")

    def test_stale_sidecar_is_removed(self):
        """회귀 ② — 새 스냅샷에 `.wal` 이 없으면 목적지의 **옛 것을 지운다.**

        남기면 새 본 파일 + 옛 WAL 조합이 되어 다음 복원이 어긋난 재생을 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD-DB", b"OLD-WAL")
            self.assertEqual(
                persistence.save_snapshot(_Store("db", wal=None), snap_path), "ok")
            with open(snap_path, "rb") as fh:
                self.assertEqual(fh.read(), b"NEW-DB")
            self.assertFalse(os.path.exists(snap_path + ".wal"),
                             "옛 .wal 이 새 본 파일과 짝이 됐다")

    def test_staging_is_cleaned_up(self):
        """찌꺼기를 남기지 않는다 — 영속 볼륨에 60MB 짜리가 쌓이면 안 된다."""
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD", b"OLD")
            persistence.save_snapshot(_Store("db"), snap_path)
            leftovers = [n for n in os.listdir(tmp) if n.startswith(persistence.STAGE_PREFIX)]
            self.assertEqual(leftovers, [], f"스테이징이 남았다: {leftovers}")

    def test_previous_crash_leftover_does_not_block(self):
        """앞선 급사가 남긴 스테이징이 있어도 다음 스냅샷이 돈다."""
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD", b"OLD")
            stale = os.path.join(tmp, persistence.STAGE_PREFIX + "db")
            os.makedirs(stale, exist_ok=True)
            with open(os.path.join(stale, "db"), "wb") as fh:
                fh.write(b"GARBAGE")
            self.assertEqual(persistence.save_snapshot(_Store("db"), snap_path), "ok")
            with open(snap_path, "rb") as fh:
                self.assertEqual(fh.read(), b"NEW-DB")

    def test_producing_nothing_is_an_error_not_a_silent_ok(self):
        """스냅샷이 아무것도 안 냈는데 'ok' 를 돌려주면 **안 도는 백업**이 된다."""
        class _Empty:
            def snapshot(self, dest_dir):
                os.makedirs(dest_dir, exist_ok=True)
                return os.path.join(dest_dir, "db")      # 파일을 안 만든다
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = _persistent(tmp, "db", b"OLD", None)
            self.assertTrue(
                persistence.save_snapshot(_Empty(), snap_path).startswith("error"))
            with open(snap_path, "rb") as fh:
                self.assertEqual(fh.read(), b"OLD")


if __name__ == "__main__":
    unittest.main()
