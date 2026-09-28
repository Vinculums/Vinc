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
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import persistence                                   # noqa: E402
from api.jobs import JobQueue                                   # noqa: E402
from api.server import VincApp                                  # noqa: E402


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


class _FlakyStore(_FakeStore):
    def __init__(self, src):
        super().__init__(src)
        self.attempts = 0

    def snapshot(self, dest_dir):
        self.attempts += 1
        if self.attempts == 1:
            raise OSError("share unavailable")
        return super().snapshot(dest_dir)


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

    def test_the_app_wires_no_tick(self):
        """꺼져 있으면 워커를 깨우지도 않는다 — 1번의 나머지 절반.

        `resolve_snapshot_every()` 는 이 env 와 **무관하게** 300 을 주므로, 주기만
        보고 틱을 걸면 스냅샷이 꺼진 데스크탑 사이드카에서도 워커가 30초마다 깨는
        영구 타이머가 남는다(배터리). 할 일이 없는 깨어남이다."""
        app = VincApp(store=None)
        self.addCleanup(app.jobs.shutdown, drain=False)
        self.assertIsNone(app.jobs._tick_s)

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

    def test_failed_snapshot_retries_without_another_write(self):
        clock = _Clock()
        p = persistence.SnapshotPolicy(every=300, clock=clock)
        p.mark_dirty()
        clock.advance(300)
        self.assertTrue(p.due())
        p.done("error: OSError: share unavailable")
        self.assertTrue(p.detail()["pending_write"])
        self.assertFalse(p.due(), "a failed copy must not retry in a tight loop")
        clock.advance(300)
        self.assertTrue(p.due(), "an idle tick must retry the unpersisted write")
        p.done("ok")
        self.assertFalse(p.detail()["pending_write"])
        self.assertFalse(p.due())

    def test_interval_comes_from_env_and_bad_values_fall_back(self):
        """주기 = **최대 손실 창**이라 이 해석이 곧 데이터 정책이다.

        운영에서 실제로 돌리는 손잡이인데 여태 검증이 없었다(2026-08-19 에 배포
        무음 유실을 줄이려 60초로 내렸다). 오타가 조용히 기본값으로 떨어지는 것도
        의도된 동작이므로 함께 못박는다 — 예외로 기동을 죽이지 않는다."""
        cases = [("60", 60.0), ("", 300.0), ("abc", 300.0), ("0", 0.0), ("  90 ", 90.0)]
        for raw, want in cases:
            with self.subTest(raw=raw):
                os.environ["VINC_STORE_SNAPSHOT_EVERY"] = raw
                self.assertEqual(persistence.resolve_snapshot_every(), want)
        os.environ.pop("VINC_STORE_SNAPSHOT_EVERY", None)
        self.assertEqual(persistence.resolve_snapshot_every(), 300.0, "미설정이면 기본값")


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


class IdleTick(_Enabled):
    """잡이 없어도 뜬다 — 이 파일 머리말 3번의 나머지 절반.

    2026-09-06: 허브가 ack 한 `doc_put`(청크 7개 교체·경고 없음)이 2분 뒤에 없었다.
    스냅샷은 **잡이 끝날 때만** 불렸으므로 버스트의 마지막 쓰기는 다음 쓰기가 올
    때까지 작업 사본에만 있었고, `VINC_STORE_LOCAL` 에서 작업 사본은 컨테이너와 함께
    죽는다. 그러니 손실 창은 주기가 아니라 **트래픽**이었다."""

    def _app(self, clock) -> VincApp:
        Path(self.live).write_bytes(b"BURST")
        app = VincApp(store=_FakeStore(self.live))
        app.jobs.shutdown(drain=False)     # 실물 큐의 틱은 30초 — 여기선 직접 건다
        app._snapshots = persistence.SnapshotPolicy(every=300, clock=clock)
        return app

    def test_a_pending_write_is_snapshotted_with_no_job_in_the_queue(self):
        """회귀 본체 — 잡을 **하나도** 안 넣었는데 밀린 쓰기가 볼륨으로 간다."""
        clock = _Clock()
        app = self._app(clock)
        app._snapshots.mark_dirty()        # 버스트의 마지막 쓰기
        clock.advance(300)                 # 주기가 찼다
        rang = threading.Event()

        def on_idle(*, wrote):
            state = app._maybe_snapshot(wrote=wrote)
            if state == "ok":
                rang.set()
            return state

        jq = JobQueue(on_idle=on_idle, tick_s=0.01)
        self.addCleanup(jq.shutdown, drain=False)
        self.assertTrue(rang.wait(5.0),
                        "잡을 하나도 안 넣었더니 스냅샷이 영영 안 떴다 — 다음 쓰기가 "
                        "올 때까지 마지막 쓰기가 작업 사본에만 있다")
        self.assertEqual(Path(self.snap).read_bytes(), b"BURST")

    def test_reading_alone_pushes_nothing(self):
        """빈 큐 틱은 dirty 를 세우면 안 된다 — 밤새 수십 MB 를 SMB 로 민다."""
        clock = _Clock()
        app = self._app(clock)
        clock.advance(3000)
        self.assertIsNone(app._maybe_snapshot(wrote=False))
        self.assertFalse(app._snapshots.detail()["pending_write"],
                         "읽기만 오간 구간이 쓰기 표시를 세웠다")

    def test_failed_copy_retries_on_idle_without_new_jobs(self):
        clock = _Clock()
        app = self._app(clock)
        app.store = _FlakyStore(self.live)
        app._snapshots.mark_dirty()
        clock.advance(300)
        self.assertTrue(app._maybe_snapshot(wrote=False).startswith("error"))
        self.assertTrue(app._snapshots.detail()["pending_write"])
        self.assertFalse(os.path.exists(self.snap))
        clock.advance(300)
        self.assertEqual(app._maybe_snapshot(wrote=False), "ok")
        self.assertEqual(Path(self.snap).read_bytes(), b"BURST")
        self.assertEqual(app.store.attempts, 2)

    def test_the_app_wires_a_tick_into_its_queue(self):
        """배선 — 실물 사이드카의 큐에 틱이 실제로 걸려 있는가."""
        app = VincApp(store=None)
        self.addCleanup(app.jobs.shutdown, drain=False)
        self.assertEqual(app.jobs._tick_s, app._snapshots.every / 10.0)



class WriterFence(_Enabled):
    """배포 중 되살아난 구 리비전이 **새 리비전 뒤에** 영속본을 덮지 못하는가.

    ACA 는 단일 리비전 모드의 롤링 전환에서 방금 내린 구 리비전을 다시 띄우고, 그것은
    1분쯤 뒤 종료 훅에서 최종 스냅샷을 쓴다. 번호가 큰 리비전이 자격을 가져갔으면
    번호가 작은 쪽의 저장은 영속본을 건드리지 않아야 한다.
    근거: [[record:aca-reboots-the-stopped-hub-revision-during-every-deploy-and-it-writes-a-late-final-snapshot-2026-09-25]]
    """
    OLD, NEW = "vinc-hub--0000230", "vinc-hub--0000231"

    def setUp(self):
        super().setUp()
        self._rev = os.environ.get(persistence.REVISION_ENV)
        self.addCleanup(self._restore_rev)

    def _restore_rev(self):
        if self._rev is None:
            os.environ.pop(persistence.REVISION_ENV, None)
        else:
            os.environ[persistence.REVISION_ENV] = self._rev

    def _as(self, revision):
        os.environ[persistence.REVISION_ENV] = revision

    def _save(self, payload: bytes, store=None) -> str:
        Path(self.live).write_bytes(payload)
        return persistence.save_snapshot(store or _FakeStore(self.live))

    def _marker(self) -> str:
        return Path(self.snap + persistence.WRITER_SUFFIX).read_text(encoding="utf-8").strip()

    def test_off_without_a_revision(self):
        os.environ.pop(persistence.REVISION_ENV, None)
        self.assertEqual(persistence.claim_writer(), "off")
        self.assertEqual(self._save(b"DESKTOP"), "ok")
        self.assertFalse(os.path.exists(self.snap + persistence.WRITER_SUFFIX))

    def test_the_stopped_revision_cannot_overwrite_after_the_new_one_claims(self):
        self._as(self.OLD)
        self.assertEqual(persistence.claim_writer(), "claimed")
        self.assertEqual(self._save(b"FINAL-OF-OLD"), "ok")    # 배포 1단계의 정상 최종 스냅샷
        self._as(self.NEW)
        self.assertEqual(persistence.claim_writer(), "claimed")
        self._as(self.OLD)                                     # 되살아난 구 리비전
        self.assertEqual(persistence.claim_writer(), f"fenced: {self.NEW}")
        self.assertEqual(self._save(b"ZOMBIE"), f"fenced: {self.NEW}")
        self.assertEqual(Path(self.snap).read_bytes(), b"FINAL-OF-OLD")
        self.assertFalse(os.path.exists(self.snap + persistence.PREV_SUFFIX),
                         "막힌 쪽이 세대를 물려서도 안 된다")
        self.assertEqual(self._marker(), self.NEW, "번호가 작은 쪽이 표식을 되돌리면 안 된다")

    def test_the_new_revision_still_writes(self):
        self._as(self.OLD)
        persistence.claim_writer()
        self.assertEqual(self._save(b"GEN1"), "ok")
        self._as(self.NEW)
        persistence.claim_writer()
        self.assertEqual(self._save(b"GEN2"), "ok")
        self.assertEqual(Path(self.snap).read_bytes(), b"GEN2")

    def test_a_claim_during_the_copy_stops_the_swap(self):
        """복사는 수십 초 걸린다. 그 사이에 가져간 자격도 막아야 한다."""
        self._as(self.OLD)
        persistence.claim_writer()
        self.assertEqual(self._save(b"GEN1"), "ok")
        test = self

        class _ClaimMidCopy(_FakeStore):
            def snapshot(self, dest_dir):
                out = super().snapshot(dest_dir)
                test._as(test.NEW)
                persistence.claim_writer()
                test._as(test.OLD)
                return out

        self.assertEqual(self._save(b"ZOMBIE", _ClaimMidCopy(self.live)), f"fenced: {self.NEW}")
        self.assertEqual(Path(self.snap).read_bytes(), b"GEN1")
        self.assertFalse(any(n.startswith(persistence.STAGE_PREFIX)
                             for n in os.listdir(os.path.dirname(self.snap))),
                         "막혀도 스테이징 찌꺼기는 치운다")

    def test_the_same_revision_does_not_rewrite_the_marker(self):
        self._as(self.NEW)
        self.assertEqual(persistence.claim_writer(), "claimed")
        before = os.stat(self.snap + persistence.WRITER_SUFFIX).st_mtime_ns
        os.utime(self.snap + persistence.WRITER_SUFFIX, ns=(before - 10**9, before - 10**9))
        self.assertEqual(persistence.claim_writer(), "claimed")
        self.assertEqual(os.stat(self.snap + persistence.WRITER_SUFFIX).st_mtime_ns,
                         before - 10**9)

    def test_an_unreadable_marker_does_not_fence(self):
        """표식을 못 읽으면 예전처럼 쓴다 — 닫힌 쪽으로 넘어지면 살아 있는 writer 가 멈춘다."""
        Path(self.snap + persistence.WRITER_SUFFIX).write_text("hand-edited", encoding="utf-8")
        self._as(self.OLD)
        self.assertIsNone(persistence.newer_writer())
        self.assertEqual(self._save(b"GEN1"), "ok")
