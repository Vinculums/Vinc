# -*- coding: utf-8 -*-
"""작업본 / 영속 스냅샷 분리 — 하드다운 3회의 공통 원인을 없애는 층.

허브의 그래프 DB 가 Azure Files(SMB) 위에서 직접 read-write 로 살았고, 그 계층에서
FTS 손상 2회(2026-08-04·08-07)와 재생 불가 WAL 1회(08-08)가 났다. 이 리포는 같은
교훈을 OAuth SQLite 에서 이미 배웠지만(`vinc_mcp/store.py`) 정작 그래프에는 적용하지
않았다. 여기서 지키는 것:

1. 기능이 **꺼져 있으면 아무것도 하지 않는다** — 기존 배포·데스크탑이 안 바뀐다.
2. 작업본이 있으면 **절대 스냅샷으로 덮지 않는다**(낡은 것이 새것을 밀어버리는 사고).
3. 스냅샷 주기는 **시간** 기준이고, 쓰기가 있었을 때만 뜬다.
4. 전 구간 fail-soft — 스냅샷 실패가 서비스를 죽이지 않는다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import persistence                                   # noqa: E402


class _Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


class _FakeStore:
    """`snapshot(dest_dir)` 계약만 흉내낸다 — 원본 basename 으로 떨군다."""
    def __init__(self, src): self.src = src; self.calls = 0
    def snapshot(self, dest_dir):
        self.calls += 1
        dest = os.path.join(dest_dir, os.path.basename(self.src))
        import shutil
        shutil.copy2(self.src, dest)
        wal = self.src + ".wal"
        if os.path.exists(wal):
            shutil.copy2(wal, dest + ".wal")
        return dest


class _BoomStore:
    def snapshot(self, dest_dir): raise OSError("SMB 가 또 성을 냈다")


class Disabled(unittest.TestCase):
    """VINC_STORE_SNAPSHOT 미설정 = 완전 no-op."""

    def setUp(self):
        self._old = os.environ.pop("VINC_STORE_SNAPSHOT", None)

    def tearDown(self):
        if self._old is not None:
            os.environ["VINC_STORE_SNAPSHOT"] = self._old

    def test_resolve_is_none(self):
        self.assertIsNone(persistence.resolve_snapshot_path())

    def test_restore_is_off(self):
        self.assertEqual(persistence.restore_if_missing("/nonexistent/x"), "off")

    def test_save_is_off(self):
        self.assertEqual(persistence.save_snapshot(_BoomStore()), "off",
                         "꺼져 있으면 스토어를 건드리지도 않아야 한다")


class _Enabled(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.live = os.path.join(self.tmp, "live", "db")
        self.snap = os.path.join(self.tmp, "persist", "db")
        os.makedirs(os.path.dirname(self.live), exist_ok=True)
        os.makedirs(os.path.dirname(self.snap), exist_ok=True)
        self._old = os.environ.get("VINC_STORE_SNAPSHOT")
        os.environ["VINC_STORE_SNAPSHOT"] = self.snap

    def tearDown(self):
        if self._old is None:
            os.environ.pop("VINC_STORE_SNAPSHOT", None)
        else:
            os.environ["VINC_STORE_SNAPSHOT"] = self._old


class Restore(_Enabled):
    def test_restores_db_and_wal(self):
        Path(self.snap).write_bytes(b"DBDBDB")
        Path(self.snap + ".wal").write_bytes(b"WAL")
        self.assertEqual(persistence.restore_if_missing(self.live), "restored")
        self.assertEqual(Path(self.live).read_bytes(), b"DBDBDB")
        self.assertEqual(Path(self.live + ".wal").read_bytes(), b"WAL")

    def test_never_overwrites_an_existing_work_copy(self):
        Path(self.snap).write_bytes(b"OLD-SNAPSHOT")
        Path(self.live).write_bytes(b"NEW-LIVE")
        self.assertEqual(persistence.restore_if_missing(self.live), "present")
        self.assertEqual(Path(self.live).read_bytes(), b"NEW-LIVE",
                         "낡은 스냅샷이 살아 있는 작업본을 밀어버리면 안 된다")

    def test_empty_when_no_snapshot_yet(self):
        self.assertEqual(persistence.restore_if_missing(self.live), "empty")

    def test_missing_wal_is_not_required(self):
        Path(self.snap).write_bytes(b"DB")
        self.assertEqual(persistence.restore_if_missing(self.live), "restored")
        self.assertFalse(os.path.exists(self.live + ".wal"))


class Save(_Enabled):
    def test_copies_to_the_persistent_path(self):
        Path(self.live).write_bytes(b"PAYLOAD")
        self.assertEqual(persistence.save_snapshot(_FakeStore(self.live)), "ok")
        self.assertEqual(Path(self.snap).read_bytes(), b"PAYLOAD")

    def test_failure_is_reported_not_raised(self):
        state = persistence.save_snapshot(_BoomStore())
        self.assertTrue(state.startswith("error"), state)

    def test_backend_without_snapshot_is_off(self):
        self.assertEqual(persistence.save_snapshot(object()), "off")


class Policy(_Enabled):
    def test_time_based_and_dirty_gated(self):
        clock = _Clock()
        p = persistence.SnapshotPolicy(every=300, clock=clock)
        clock.advance(1000)
        self.assertFalse(p.due(), "쓰기가 없었으면 시간이 지나도 안 뜬다")
        p.mark_dirty()
        self.assertTrue(p.due())
        p.done("ok")
        self.assertFalse(p.due(), "직후에는 안 뜬다")
        p.mark_dirty()
        self.assertFalse(p.due(), "주기 전에는 쓰기가 있어도 안 뜬다")
        clock.advance(300)
        self.assertTrue(p.due())

    def test_zero_disables(self):
        p = persistence.SnapshotPolicy(every=0)
        p.mark_dirty()
        self.assertFalse(p.due())
