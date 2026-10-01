# -*- coding: utf-8 -*-
"""VIN-82 ACK 경계 배선 — 쓰기 잡은 영속 세대를 게시한 뒤에야 DONE·200 이다.

지키는 것:
1. ``VINC_DURABLE_ACK`` 가 꺼져 있으면 훅이 없고 기동 복원도 예전과 같다.
2. 켜져 있으면 쓰기 잡은 세대를 게시한 뒤에야 끝나고, 게시 실패는 ERROR(불확정 커밋)다.
3. 쓰기가 없던 잡은 게시 비용을 물지 않는다.
4. 기동은 스냅샷과 세대 중 더 새롭고 검증되는 쪽으로 복원하고, 깨진 세대는 건너뛴다.
5. 세대 수에 상한이 있다.
6. 실엔진: 쓰기 → ACK → 급사(스냅샷 없음) → 로컬 디스크 소실 → 복원 → 그 쓰기가 있다.

근거: [[vinc.plan.vin82-durable-ack-2026-09-29]]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api.jobs import ERROR, JobError, JobQueue          # noqa: E402
from api.server import VincApp                         # noqa: E402
from store import durable_generation as dg             # noqa: E402
from store import embedded_backend as eb               # noqa: E402
from store import persistence                          # noqa: E402
from store.config import load_config                   # noqa: E402


class _FakeStore:
    """``snapshot(dest_dir)`` 계약만: CHECKPOINT 처럼 WAL 을 본 파일에 접고 사본을 떨군다."""

    def __init__(self, live: str):
        self.live = live
        self.snapshots = 0
        Path(live).write_bytes(b"BASE")

    def write(self, data: bytes) -> dict:
        with open(self.live + ".wal", "ab") as f:      # 커밋 한 건 = WAL 에 덧붙임
            f.write(data)
        return {"ok": True}

    def snapshot(self, dest_dir: str) -> str:
        self.snapshots += 1
        wal = Path(self.live + ".wal")
        if wal.exists():
            Path(self.live).write_bytes(Path(self.live).read_bytes() + wal.read_bytes())
            wal.unlink()
        dest = os.path.join(dest_dir, os.path.basename(self.live))
        shutil.copy2(self.live, dest)
        return dest


class _Env(unittest.TestCase):
    durable = "on"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.live = str(self.tmp / "local" / "db")
        self.snap = str(self.tmp / "share" / "db")
        self.root = persistence.generations_root(self.snap)
        os.makedirs(os.path.dirname(self.live))
        os.makedirs(os.path.dirname(self.snap))
        env = mock.patch.dict(os.environ, {"VINC_STORE_SNAPSHOT": self.snap,
                                           persistence.DURABLE_ENV: self.durable})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(persistence.REVISION_ENV, None)

    def _queue(self, store):
        ack = persistence.DurableAck(lambda: store, self.live)
        jq = JobQueue(on_before_done=ack)
        self.addCleanup(jq.shutdown, drain=False)
        return ack, jq


class Wiring(_Env):
    def test_on_wires_the_hook_into_the_app_queue(self):
        app = VincApp(load_config({"data_dir": self.live}), store=None)
        self.addCleanup(app.jobs.shutdown, drain=False)
        self.assertIsNotNone(app._durable)
        self.assertIs(app.jobs._on_before_done, app._durable)

    def test_off_by_default_wires_nothing(self):
        for env in ({persistence.DURABLE_ENV: ""},
                    {persistence.DURABLE_ENV: "on", "VINC_STORE_SNAPSHOT": ""}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                app = VincApp(load_config({"data_dir": self.live}), store=None)
                self.addCleanup(app.jobs.shutdown, drain=False)
                self.assertIsNone(app._durable)
                self.assertIsNone(app.jobs._on_before_done)


class AckBoundary(_Env):
    def test_first_write_is_a_full_generation_then_wal_only(self):
        store = _FakeStore(self.live)
        ack, jq = self._queue(store)
        jq.run_sync("write", lambda: store.write(b"+one"))
        self.assertEqual((ack.last, ack.sequence), ("full", 1))
        jq.run_sync("write", lambda: store.write(b"+two"))
        self.assertEqual((ack.last, ack.sequence), ("wal", 2))
        self.assertEqual(store.snapshots, 1, "WAL 만 올린 쓰기가 전체 snapshot 을 떴다")
        target = self.tmp / "replacement" / "db"
        self.assertEqual(dg.restore(self.root, target), "restored:2")
        self.assertEqual(target.read_bytes() + Path(f"{target}.wal").read_bytes(),
                         b"BASE+one+two")

    def test_a_job_that_wrote_nothing_publishes_nothing(self):
        store = _FakeStore(self.live)
        ack, jq = self._queue(store)
        jq.run_sync("write", lambda: store.write(b"+one"))
        jq.run_sync("read", lambda: {"rows": []})
        self.assertEqual(ack.last, "unchanged")
        self.assertEqual((dg.latest_sequence(self.root), store.snapshots, ack.publishes),
                         (1, 1, 1))

    def test_a_failed_publish_is_an_error_not_a_done(self):
        store = _FakeStore(self.live)
        ack, jq = self._queue(store)
        with mock.patch.object(dg, "publish", side_effect=OSError("share went away")):
            with self.assertRaisesRegex(JobError, "commit uncertain"):
                jq.run_sync("write", lambda: store.write(b"+one"))
        self.assertEqual(jq.get("job-1").status, ERROR)
        self.assertEqual(ack.failures, 1)
        self.assertEqual(dg.latest_sequence(self.root), 0)
        # 공유가 돌아오면 다음 쓰기가 앞의 로컬 커밋까지 함께 싣는다(WAL 은 누적이다).
        jq.run_sync("write", lambda: store.write(b"+two"))
        target = self.tmp / "replacement" / "db"
        dg.restore(self.root, target)
        self.assertEqual(target.read_bytes(), b"BASE+one+two")

    def test_a_leftover_stage_does_not_block_the_next_write(self):
        store = _FakeStore(self.live)
        ack, jq = self._queue(store)
        jq.run_sync("write", lambda: store.write(b"+one"))
        os.makedirs(os.path.join(self.root, "stage-2"))   # 앞선 시도가 남긴 것
        jq.run_sync("write", lambda: store.write(b"+two"))
        self.assertEqual(ack.sequence, 3)

    def test_generations_are_capped(self):
        store = _FakeStore(self.live)
        _ack, jq = self._queue(store)
        for i in range(6):
            jq.run_sync("write", lambda i=i: store.write(b"+%d" % i))
        names = sorted(os.listdir(self.root))
        # 최신 둘(6·5)과 그 둘이 기대는 본 세대(1)만 남는다.
        self.assertEqual(names, ["commit-1.json", "commit-5.json", "commit-6.json",
                                 "generation-1", "generation-5", "generation-6"])
        target = self.tmp / "replacement" / "db"
        self.assertEqual(dg.restore(self.root, target), "restored:6")


class Fence(_Env):
    def _write(self, marker: str | None = None, *, as_dir=False):
        os.environ[persistence.REVISION_ENV] = "vinc-hub--0000010"
        path = self.snap + persistence.WRITER_SUFFIX
        persistence._rm(path)
        if as_dir:
            os.makedirs(path)
        elif marker is not None:
            Path(path).write_text(marker + "\n", encoding="utf-8")
        store = _FakeStore(self.live)
        _ack, jq = self._queue(store)
        return lambda: jq.run_sync("write", lambda: store.write(b"+one"))

    def tearDown(self):
        os.environ.pop(persistence.REVISION_ENV, None)

    def test_a_newer_revision_fences_the_ack(self):
        with self.assertRaisesRegex(JobError, "fenced: vinc-hub--0000011"):
            self._write("vinc-hub--0000011")()

    def test_an_unreadable_marker_fails_closed(self):
        with self.assertRaisesRegex(JobError, "marker unreadable"):
            self._write(as_dir=True)()
        with self.assertRaisesRegex(JobError, "marker unparsable"):
            self._write("hand-edited")()

    def test_own_or_older_marker_and_no_marker_pass(self):
        for marker in ("vinc-hub--0000010", "vinc-hub--0000009", None):
            with self.subTest(marker=marker):
                self.assertEqual(self._write(marker)(), {"ok": True})


class BootRestore(_Env):
    def _generation(self, seq: int, body: bytes):
        src = self.tmp / f"src-{seq}"
        src.write_bytes(body)
        dg.publish(self.root, src, sequence=seq)

    def _snapshot(self, body: bytes):
        Path(self.snap).write_bytes(body)
        persistence._mark_seen(self.snap)

    def test_generation_newer_than_the_snapshot_wins(self):
        self._snapshot(b"SNAPSHOT")                   # 세대 루트가 없어 표식도 없다 → 0
        self._generation(1, b"GEN-1")
        state = persistence.restore_newest(self.live)
        self.assertEqual(state, "restored-generation:1")
        self.assertIsNone(persistence.restore_state_is_fatal(state))
        self.assertEqual(Path(self.live).read_bytes(), b"GEN-1")

    def test_snapshot_taken_after_the_latest_generation_wins(self):
        self._generation(1, b"GEN-1")
        self._snapshot(b"SNAPSHOT")                   # 세대 1 까지 담았다
        self.assertEqual(persistence.restore_newest(self.live), "restored")
        self.assertEqual(Path(self.live).read_bytes(), b"SNAPSHOT")

    def test_a_broken_latest_generation_is_skipped_and_said(self):
        self._generation(1, b"GEN-1")
        self._generation(2, b"GEN-2")
        Path(self.root, "generation-2", "db").write_bytes(b"CORRUPT")
        state = persistence.restore_newest(self.live)
        self.assertTrue(state.startswith("restored-generation:1; skipped 1 invalid"), state)
        self.assertIsNone(persistence.restore_state_is_fatal(state))
        self.assertEqual(Path(self.live).read_bytes(), b"GEN-1")

    def test_broken_generations_with_no_snapshot_stop_the_boot(self):
        self._generation(1, b"GEN-1")
        Path(self.root, "generation-1", "db").write_bytes(b"CORRUPT")
        with mock.patch.dict(os.environ, {persistence.BOOTSTRAP_ENV: "1"}):
            state = persistence.restore_newest(self.live)
            self.assertTrue(state.startswith("error"), state)
            self.assertIsNotNone(persistence.restore_state_is_fatal(state))
        self.assertFalse(os.path.exists(self.live))

    def test_an_existing_work_copy_is_never_touched(self):
        self._generation(1, b"GEN-1")
        Path(self.live).write_bytes(b"LIVE")
        self.assertEqual(persistence.restore_newest(self.live), "present")
        self.assertEqual(Path(self.live).read_bytes(), b"LIVE")

    def test_off_restores_the_snapshot_as_before(self):
        self._snapshot(b"SNAPSHOT")
        self._generation(1, b"GEN-1")
        with mock.patch.dict(os.environ, {persistence.DURABLE_ENV: ""}):
            self.assertEqual(persistence.restore_newest(self.live), "restored")
        self.assertEqual(Path(self.live).read_bytes(), b"SNAPSHOT")

    def test_snapshot_records_the_generation_it_covers_only_when_one_exists(self):
        store = _FakeStore(self.live)
        self.assertEqual(persistence.save_snapshot(store), "ok")
        self.assertFalse(os.path.exists(self.snap + persistence.SEEN_SUFFIX),
                         "세대 모드를 켠 적 없는 설치에 새 파일이 생겼다")
        self._generation(3, b"GEN-3")
        self.assertEqual(persistence.save_snapshot(store), "ok")
        self.assertEqual(persistence._snapshot_seen(self.snap), 3)


# 자식 프로세스: 쓰기 두 건과 쓰기 없는 잡 하나를 ACK 받은 뒤 부모가 죽일 때까지 산다.
_CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from api.server import VincApp
from store import persistence
from store.backend import open_store
from store.config import load_config

cfg = load_config({"data_dir": sys.argv[2]})
store = open_store(cfg)
store.provision(dim=4)
# 급사 전 마지막 영속 스냅샷. 아래 쓰기는 여기에 없다.
print(json.dumps({"baseline": persistence.save_snapshot(store)}), flush=True)
app = VincApp(cfg, store=store)
seen = []
for i in range(2):
    app.jobs.run_sync("write", lambda i=i: (store.run(
        f"MERGE (c:Concept {{concept_id: 'acked-{i}'}}) "
        "SET c.title = 'acknowledged', c.owner_id = 'user:a'"), {"ok": True})[1])
    seen.append(app._durable.detail() if app._durable else None)
app.jobs.run_sync("noop", lambda: {"ok": True})
seen.append(app._durable.detail() if app._durable else None)
print(json.dumps({"ack": seen}), flush=True)
sys.stdin.read()
'''


@unittest.skipIf(eb._import_driver()[0] is None, "Ladybug driver unavailable")
class KillAfterAck(unittest.TestCase):
    """실엔진 킬 테스트 — 이 패키지의 수용 기준."""

    def _kill_after_ack(self, durable: bool):
        from store.backend import open_store
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        live, snap = tmp / "local" / "db", tmp / "share" / "db"
        snap.parent.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items()
               if k not in (persistence.REVISION_ENV, persistence.DURABLE_ENV)}
        env.update(VINC_STORE_SNAPSHOT=str(snap), VINC_STORE_SNAPSHOT_EVERY="3600",
                   PYTHONIOENCODING="utf-8")
        if durable:
            env[persistence.DURABLE_ENV] = "on"
        with open(tmp / "child.log", "w", encoding="utf-8") as log:
            proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(ROOT), str(live)],
                                    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=log, text=True, encoding="utf-8")
            watchdog = threading.Timer(300, proc.kill)
            watchdog.start()
            try:
                acked = None
                for line in proc.stdout:
                    if line.startswith("{") and "ack" in (msg := json.loads(line)):
                        acked = msg["ack"]
                        break
                proc.kill()                     # 급사: close()·최종 스냅샷이 없다
                proc.wait()
            finally:
                watchdog.cancel()
        self.assertIsNotNone(acked, (tmp / "child.log").read_text(encoding="utf-8"))
        shutil.rmtree(live.parent)              # 컨테이너 로컬 디스크 소실
        with mock.patch.dict(os.environ, {"VINC_STORE_SNAPSHOT": str(snap),
                                          persistence.DURABLE_ENV: "on" if durable else ""}):
            state = persistence.restore_newest(str(live))
        self.assertIsNone(persistence.restore_state_is_fatal(state), state)
        store = open_store(load_config({"data_dir": str(live)}))
        self.assertIsNotNone(store)
        try:
            rows = store.run("MATCH (c:Concept) WHERE c.concept_id STARTS WITH 'acked-' "
                             "RETURN c.concept_id AS id ORDER BY id")
        finally:
            store.close()
        return state, [r["id"] for r in rows], acked, snap

    def test_an_acknowledged_write_survives_a_kill_and_disk_loss(self):
        state, ids, acked, _snap = self._kill_after_ack(durable=True)
        self.assertTrue(state.startswith("restored-generation:"), state)
        self.assertEqual(ids, ["acked-0", "acked-1"])
        # Windows 는 엔진이 본 파일을 배타 잠금해 WAL 만 올리기가 늘 거절된다.
        second = "full" if os.name == "nt" else "wal"
        self.assertEqual([d["last"] for d in acked], ["full", second, "unchanged"])

    def test_off_loses_the_write_exactly_as_before(self):
        state, ids, acked, snap = self._kill_after_ack(durable=False)
        self.assertEqual(state, "restored")
        self.assertEqual(ids, [], "꺼진 모드의 손실 창은 예전 그대로다(마지막 스냅샷 뒤)")
        self.assertEqual(acked, [None, None, None])
        self.assertFalse(os.path.exists(persistence.generations_root(str(snap))))


if __name__ == "__main__":
    unittest.main()
