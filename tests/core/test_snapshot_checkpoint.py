# -*- coding: utf-8 -*-
"""스냅샷의 CHECKPOINT 는 **혼자** 돌고, 끝나지 않은 체크포인트는 복사하지 않는다.

  1  수동 CHECKPOINT 가 다른 커넥션의 읽기와 겹치면 엔진이 네이티브로 죽는다(segfault·
     double free). 허브는 스냅샷마다 그걸 공유 가드 아래서 돌렸고, 죽은 작업본을 다시
     열어 영속본까지 밀었다(2026-09-27 쓰기 거절 "invalid local node offset").
     회귀를 막는 것은 **가드를 쥐었는가**를 보는 결정적 테스트다. 크래시 자체는 확률이라
     스위트에서 재현하지 않는다 — 이 백엔드 경로로는 고치기 전 방식도 로컬에서 9번 다
     살았다(드라이버를 직접 부른 재현은 12번 중 5번 죽었다). 자식 프로세스 테스트는
     가드가 **읽기를 굶기거나 깨뜨리지 않는지**를 본다.
  2  `.wal.checkpoint`(얼린 WAL)·`.shadow` 가 남아 있으면 본 파일 + `.wal` 만으로는
     온전한 세대가 아니다. 그 상태로는 영속본을 건드리지 않고 `error:` 를 돌려준다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import embedded_backend as eb  # noqa: E402
from store import persistence  # noqa: E402

_DRIVER, _NAME = eb._import_driver()


def _open(data_dir: str):
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config({"data_dir": data_dir}))
    assert s is not None, eb.EmbeddedBackend.last_open_error
    s.provision(dim=4)
    return s


# 자식: 라이터가 쓰기 묶음과 체크포인트를 번갈아 돌리는 동안 리더 여섯이 각자 커넥션으로
# 읽는다 — 허브의 잡 스레드와 HTTP 스레드 모양 그대로다. argv[3] 은 체크포인트 방식:
# `alone` 은 스냅샷이 쓰는 길, `shared` 는 고치기 전의 길(손으로 대조할 때만).
# 리눅스에서는 코어 덤프를 끈다 — 세그폴트 한 번에 덤프가 수 GB 다.
_CHILD = r"""
import os, random, sys, threading, time
if sys.platform.startswith("linux"):
    import ctypes
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
mode, seconds = sys.argv[3], float(sys.argv[4])
rows = int(sys.argv[5]) if len(sys.argv) > 5 else 4000
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=4)
for a in range(0, rows, 500):
    s.run("UNWIND range($a, $b) AS k MERGE (c:Concept {concept_id: 'c' + string(k)}) "
          "SET c.title = 'title ' + string(k), c.owner_id = 'user:a'", {"a": a, "b": a + 499})
stop = time.time() + seconds
errs = []

def reader(seed):
    rv = s.reader_view()
    rnd = random.Random(seed)
    try:
        while time.time() < stop:
            k = rnd.randrange(rows)
            if k % 2:
                rv.run("MATCH (c:Concept {concept_id: $id}) RETURN c.title", {"id": f"c{k}"})
            else:
                rv.run("MATCH (c:Concept) WHERE c.title CONTAINS $t "
                       "RETURN c.concept_id, c.title LIMIT 200", {"t": str(k % 97)})
    except Exception as e:
        errs.append(f"{type(e).__name__}: {e}"[:200])
    finally:
        rv.close()

ts = [threading.Thread(target=reader, args=(i,)) for i in range(6)]
for t in ts:
    t.start()
n, ckpts = rows, 0
while time.time() < stop:
    for _ in range(20):
        n += 1
        s.run("MERGE (c:Concept {concept_id: $id}) SET c.title = $t", {"id": f"w{n}", "t": "x" * 200})
        # 있는 행도 고친다 — 체크포인트가 리더가 훑는 열 조각을 다시 쓰게 된다.
        s.run("MATCH (c:Concept {concept_id: $id}) SET c.title = $t",
              {"id": f"c{random.randrange(rows)}", "t": "y" * 150})
    if mode == "alone":
        s._checkpoint_alone()
    else:
        s.run("CHECKPOINT")
    ckpts += 1
for t in ts:
    t.join()
print("checkpoints", ckpts, flush=True)
for e in errs[:3]:
    print("reader error", e, flush=True)
s.close()
print("survived", flush=True)
"""


def run_child(data_dir: str, mode: str, seconds: float, rows: int = 4000) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _CHILD, str(ROOT), data_dir, mode, str(seconds),
                           str(rows)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class CheckpointAloneTests(unittest.TestCase):
    """1 — 체크포인트와 읽기가 겹치지 않는다."""

    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "store")

    def test_snapshot_checkpoints_with_the_store_held_alone(self):
        s = _open(self.dir)
        seen: list[bool] = []
        real = s.run

        def spy(query, params=None, **kw):
            if query == "CHECKPOINT":
                seen.append(eb.DDL_GUARD._owner == threading.get_ident())
            return real(query, params, **kw)

        s.run = spy
        try:
            s.snapshot(tempfile.mkdtemp())
        finally:
            s.run = real
            s.close()
        self.assertTrue(seen, "스냅샷이 CHECKPOINT 를 돌리지 않았다")
        self.assertTrue(all(seen), "CHECKPOINT 가 배타 가드 없이 돌았다 — 읽기와 겹치면 엔진이 죽는다")

    def test_reads_on_other_connections_survive_snapshot_checkpoints(self):
        r = run_child(self.dir, "alone", 8.0)
        self.assertEqual(r.returncode, 0,
                         f"rc={r.returncode} (0x{r.returncode & 0xFFFFFFFF:08X})\n"
                         f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        lines = r.stdout.splitlines()
        self.assertIn("survived", lines)
        self.assertFalse([ln for ln in lines if ln.startswith("reader error")], lines)
        ckpts = [int(ln.split()[1]) for ln in lines if ln.startswith("checkpoints ")]
        self.assertTrue(ckpts and ckpts[0] > 0, lines)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class UnfinishedCheckpointTests(unittest.TestCase):
    """2 — 짝 파일이 남은 작업본은 영속본이 되지 않는다."""

    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "store")
        self.snap = os.path.join(tempfile.mkdtemp(), "store")

    def test_a_leftover_companion_blocks_the_copy_and_keeps_the_old_generation(self):
        s = _open(self.dir)
        # 체크포인트는 여기서 돌리지 않는다 — 엔진이 WAL 을 돌려 짝 파일 자리를 덮거나
        # 지울 수 있다. 보려는 것은 파일 판정이다.
        s._checkpoint_alone = lambda: None
        try:
            for suffix in (".wal.checkpoint", ".shadow"):
                with self.subTest(suffix=suffix):
                    with open(self.snap, "wb") as f:
                        f.write(b"OLD")
                    left = self.dir + suffix
                    with open(left, "wb") as f:
                        f.write(b"x")
                    try:
                        state = persistence.save_snapshot(s, snapshot_path=self.snap)
                    finally:
                        os.remove(left)
                    self.assertTrue(state.startswith("error:"), state)
                    self.assertIn("unfinished checkpoint", state)
                    with open(self.snap, "rb") as f:
                        self.assertEqual(f.read(), b"OLD", "옛 세대가 바뀌었다")
                    self.assertFalse(os.path.exists(self.snap + persistence.PREV_SUFFIX),
                                     "옛 세대를 .prev 로 물렸다")
            # 짝 파일이 없으면 평소대로 된다.
            self.assertEqual(persistence.save_snapshot(s, snapshot_path=self.snap), "ok")
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
