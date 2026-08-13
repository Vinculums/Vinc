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


class GenerationSwap(_Enabled):
    """교체 도중 죽어도 **온전한 세대가 최소 하나** 공유에 남는가.

    2026-08-12 에 이것이 없어서 허브의 영속본이 통째로 사라졌다. 스테이징은 이미
    있었지만 마지막 `os.replace(stage, dest)` 가 SMB 에서 "목적지를 지우고 rename"
    으로 구현되면 그 사이에 죽었을 때 남는 것이 없다. 사흘치를 트랜스크립트에서
    되살려야 했다 — 그 사고를 코드로 고정한다.
    """

    def _save(self, payload: bytes) -> str:
        Path(self.live).write_bytes(payload)
        return persistence.save_snapshot(_FakeStore(self.live))

    def test_previous_generation_is_kept_after_a_swap(self):
        self.assertEqual(self._save(b"GEN1"), "ok")
        self.assertFalse(os.path.exists(self.snap + persistence.PREV_SUFFIX),
                         "첫 세대에는 물릴 이전 세대가 없다")
        self.assertEqual(self._save(b"GEN2"), "ok")
        self.assertEqual(Path(self.snap).read_bytes(), b"GEN2")
        self.assertEqual(Path(self.snap + persistence.PREV_SUFFIX).read_bytes(), b"GEN1",
                         "직전 세대가 남아 있어야 교체 중 급사를 견딘다")

    def test_only_one_previous_generation_is_kept(self):
        for p in (b"GEN1", b"GEN2", b"GEN3"):
            self.assertEqual(self._save(p), "ok")
        self.assertEqual(Path(self.snap).read_bytes(), b"GEN3")
        self.assertEqual(Path(self.snap + persistence.PREV_SUFFIX).read_bytes(), b"GEN2",
                         "한 세대만 든다 — 공유가 무한히 자라면 안 된다")

    def test_crash_between_retire_and_move_still_restores(self):
        """세대 교대(rename) 직후, 새 세대가 자리에 앉기 **전에** 죽은 상태."""
        self.assertEqual(self._save(b"GOOD"), "ok")
        persistence._retire_generation(self.snap)          # ← 여기서 프로세스가 죽었다
        self.assertFalse(os.path.exists(self.snap), "자리는 비어 있다(그게 이 상태다)")

        state = persistence.restore_if_missing(self.live + "2")
        self.assertEqual(state, "restored-previous", state)
        self.assertEqual(Path(self.live + "2").read_bytes(), b"GOOD",
                         "직전 세대에서 되살아나야 한다")
        self.assertIsNone(persistence.restore_state_is_fatal(state),
                          "이 상태는 기동을 막으면 안 된다 — 데이터는 온전하다")

    def test_a_failed_snapshot_does_not_disturb_the_live_generation(self):
        self.assertEqual(self._save(b"GOOD"), "ok")
        state = persistence.save_snapshot(_BoomStore())
        self.assertTrue(state.startswith("error"), state)
        self.assertEqual(Path(self.snap).read_bytes(), b"GOOD",
                         "실패한 스냅샷이 멀쩡한 영속본을 밀어내면 안 된다")

    def test_prev_carries_no_orphan_wal(self):
        """`.prev` 는 체크포인트 본만 든다 — 짝 없는 WAL 이 다음 세대와 엮이면
        복원이 어긋난 재생을 한다(2026-08-04·08-07·08-09 에 쓴 복구 수법의 근거)."""
        Path(self.live).write_bytes(b"GEN1")
        Path(self.live + ".wal").write_bytes(b"WAL1")
        self.assertEqual(persistence.save_snapshot(_FakeStore(self.live)), "ok")
        self.assertTrue(os.path.exists(self.snap + ".wal"))

        Path(self.live).write_bytes(b"GEN2")
        os.remove(self.live + ".wal")                      # 체크포인트로 WAL 이 사라진 상태
        self.assertEqual(persistence.save_snapshot(_FakeStore(self.live)), "ok")
        self.assertFalse(os.path.exists(self.snap + ".wal"), "낡은 WAL 이 자리에 남았다")
        self.assertFalse(os.path.exists(self.snap + persistence.PREV_SUFFIX + ".wal"),
                         ".prev 는 WAL 을 물지 않는다")
        self.assertEqual(Path(self.snap + persistence.PREV_SUFFIX).read_bytes(), b"GEN1")

    def test_restore_prefers_the_current_generation(self):
        self.assertEqual(self._save(b"GEN1"), "ok")
        self.assertEqual(self._save(b"GEN2"), "ok")
        self.assertEqual(persistence.restore_if_missing(self.live + "3"), "restored")
        self.assertEqual(Path(self.live + "3").read_bytes(), b"GEN2",
                         "자리에 온전한 세대가 있으면 그것을 쓴다")
