# -*- coding: utf-8 -*-
"""체크포인트 표식(`db.wal.checkpoint`)이 섀도 파일보다 오래 남은 스토어가 열린다.

ladybug 0.21.2 의 체크포인트는 활성 WAL 을 `db.wal.checkpoint` 로 바꾸고, `db.shadow` 에 바뀐
페이지를 쓰고, 동결 WAL 끝에 CHECKPOINT 레코드를 붙이고, 섀도 페이지를 본 파일에 적용한 뒤
`db.shadow` 를 지우고, 마지막으로 동결 WAL 을 지운다. 마지막 두 삭제 사이에서 죽으면 본 파일과
활성 WAL 은 온전한데 엔진이 섀도 파일을 다시 열려다 `Cannot open file db.shadow` 로 열기를
거절한다(50회 강제 종료 중 1회). 여기서 못 박는 것:

- 그 상태(CHECKPOINT 레코드가 있는 동결 WAL · 섀도 없음)는 표식을 옆으로 옮기고 열리며,
  체크포인트 전·후의 커밋이 한 건도 빠지지 않는다.
- CHECKPOINT 레코드가 **없는** 동결 WAL(이름만 바뀐 채 죽은 것)은 건드리지 않는다. 엔진이
  재생하고 커밋은 남는다. 이것을 옮기면 커밋을 통째로 잃는다.
- 표식과 섀도가 **둘 다** 있으면 아무것도 옮기지 않는다(엔진이 체크포인트를 마저 끝낸다).
근거: [[record:linux-50-forced-kill-reopen-requalification-on-main-2026-11-04]]
"""
from __future__ import annotations

import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import embedded_backend as eb  # noqa: E402
from store.config import load_config  # noqa: E402

try:
    import ladybug as _DRIVER  # noqa: F401
except Exception:   # noqa: BLE001
    _DRIVER = None

#: ladybug 0.21.2 가 동결 WAL 끝에 붙이는 CHECKPOINT 레코드(길이 8바이트 + 타입 + 체크섬).
#: 두 스토어(행 50개·700개)에서 같은 17바이트로 실측됐다 — 레코드에 본문이 없어 상수다.
#: 엔진 판이 바뀌어 포맷이 달라지면 아래 시험이 다른 문구로 실패한다(조용히 통과하지 않는다).
_CHECKPOINT_RECORD = bytes.fromhex("0100000000000000fe773cb3d5173d7b25")

#: 자식 하나가 행 [lo, hi) 를 넣는다. `exit` 면 닫지 않고 죽는다(WAL 이 남는다 — kill -9 와
#: 같은 모양). `checkpoint` 면 CHECKPOINT 뒤 정상 종료(본 파일만 남는다).
_CHILD = """
import os, sys, ladybug
db, lo, hi, how = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
d = ladybug.Database(db, checkpoint_threshold=1 << 30)
c = ladybug.Connection(d)
if lo == 0 and hi > 0:
    c.execute("CREATE NODE TABLE T(id INT64, s STRING, PRIMARY KEY(id))")
for i in range(lo, hi):
    c.execute(f"CREATE (:T {{id:{i}, s:'r{i}'}})")
if how == "checkpoint":
    c.execute("CHECKPOINT"); c.close(); d.close()
else:
    os._exit(0)
"""


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
class StaleCheckpointMarkerTest(unittest.TestCase):

    def setUp(self):
        # 로컬 디스크(리포의 .tmp) — /tmp 는 tmpfs 라 사용자 할당량에 걸린 적이 있다.
        root = Path(__file__).resolve().parents[2] / ".tmp"
        root.mkdir(exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="stale-marker-", dir=str(root))
        self.db = os.path.join(self.dir, "db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rows(self, lo: int, hi: int, how: str) -> None:
        subprocess.run([sys.executable, "-c", _CHILD, self.db, str(lo), str(hi), how],
                       check=True, timeout=120, stdout=subprocess.DEVNULL)

    def _files(self) -> list[str]:
        return sorted(os.listdir(self.dir))

    def _open_and_count(self):
        err = io.StringIO()
        with redirect_stderr(err):
            store = eb.EmbeddedBackend.open(load_config({"data_dir": self.db}))
        self.assertIsNotNone(store, eb.EmbeddedBackend.last_open_error)
        try:
            rows = store.run("MATCH (t:T) RETURN count(*) AS n, count(DISTINCT t.id) AS d")
        finally:
            store.close()
        return rows[0]["n"], rows[0]["d"], err.getvalue()

    def _fabricate_marker_with_checkpoint_record(self) -> None:
        """본 파일(300행) + CHECKPOINT 레코드가 붙은 동결 WAL(같은 300행) + 활성 WAL(100행).
        섀도 페이지가 적용되고 섀도 파일이 지워진 뒤, 동결 WAL 삭제 직전에 죽은 모양이다."""
        self._rows(0, 300, "exit")
        frozen = Path(self.db + ".wal").read_bytes()
        self._rows(0, 0, "checkpoint")          # 재생 뒤 체크포인트 — 본 파일에 300행
        self.assertEqual(self._files(), ["db"])
        self._rows(300, 400, "exit")            # 체크포인트 뒤의 쓰기 — 활성 WAL
        Path(self.db + ".wal.checkpoint").write_bytes(frozen + _CHECKPOINT_RECORD)
        self.assertEqual(self._files(), ["db", "db.wal", "db.wal.checkpoint"])

    def test_the_engine_refuses_the_state_without_help(self):
        self._fabricate_marker_with_checkpoint_record()
        with self.assertRaises(Exception) as cm:
            _DRIVER.Database(self.db, throw_on_wal_replay_failure=False)
        self.assertIn("Cannot open file", str(cm.exception))
        self.assertIn("db.shadow", str(cm.exception))

    def test_marker_that_outlived_its_shadow_is_set_aside_and_every_row_survives(self):
        self._fabricate_marker_with_checkpoint_record()
        n, d, err = self._open_and_count()
        self.assertEqual((n, d), (400, 400))
        self.assertIn("outlived its shadow file", err)
        aside = glob.glob(self.db + ".wal.checkpoint.stale-*")
        self.assertEqual(len(aside), 1, self._files())
        self.assertFalse(os.path.exists(self.db + ".wal.checkpoint"))
        # 두 번째 열기는 표식 없이 평범하게 열린다.
        n, d, err = self._open_and_count()
        self.assertEqual((n, d), (400, 400))
        self.assertEqual(err, "")

    def test_marker_without_a_checkpoint_record_is_left_for_the_engine(self):
        # 이름만 바뀐 채 죽은 동결 WAL — 그 안의 300행은 아직 본 파일에 없다.
        self._rows(0, 300, "exit")
        os.replace(self.db + ".wal", self.db + ".wal.checkpoint")
        n, d, err = self._open_and_count()
        self.assertEqual((n, d), (300, 300))
        self.assertEqual(err, "")
        self.assertEqual(glob.glob(self.db + ".wal.checkpoint.stale-*"), [])

    def test_marker_beside_a_shadow_file_is_not_touched(self):
        Path(self.db + ".wal.checkpoint").write_bytes(b"\x00" * 16)
        Path(self.db + ".shadow").write_bytes(b"\x00" * 16)
        exc = RuntimeError(f"IO exception: Cannot open file {self.db}.shadow: No such file")
        self.assertEqual(eb._stale_checkpoint_marker_note(self.db, exc), "")
        self.assertEqual(self._files(), ["db.shadow", "db.wal.checkpoint"])

    def test_other_open_failures_do_not_move_the_marker(self):
        Path(self.db + ".wal.checkpoint").write_bytes(b"\x00" * 16)
        exc = RuntimeError("Checksum verification failed, the WAL file is corrupted.")
        self.assertEqual(eb._stale_checkpoint_marker_note(self.db, exc), "")
        self.assertEqual(self._files(), ["db.wal.checkpoint"])


if __name__ == "__main__":
    unittest.main()
