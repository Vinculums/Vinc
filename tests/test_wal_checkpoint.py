# -*- coding: utf-8 -*-
"""WAL 이 무한정 자라지 않는가 — 2026-08-08 데이터 유실 사고의 재발 방지.

무슨 일이 있었나. Kùzu 계보는 **close 시점에만** WAL 을 본 파일에 반영한다. 그런데
사이드카는 장수 프로세스라 절대 close 하지 않으므로 WAL 이 끝없이 자란다. 실측:
DB 파일 mtime 06:38 · WAL mtime 11:56(11.9MB) — 그 5시간 반의 모든 쓰기가 WAL 에만
있었다. 사이드카가 죽자 그 WAL 은 **재생 불가**였고(네이티브 크래시 0xC0000409,
로컬 재현 확인) 5시간 반이 통째로 사라졌다.

그래서 잡 큐가 N개마다 CHECKPOINT 를 건다. 여기서 지키는 것 둘:

1. 체크포인트가 실제로 **WAL 을 줄인다**(호출됐다가 아니라 효과가 있다).
2. 체크포인트가 실패해도 **잡은 성공한다** — 보조 기능이 본 기능을 죽이면 안 된다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.jobs import JobQueue                                # noqa: E402
from store.embedded_backend import _import_driver            # noqa: E402

_DRIVER, _NAME = _import_driver()


class CheckpointHook(unittest.TestCase):
    """드라이버 없이도 도는 계약 검사."""

    def test_fires_every_n_jobs(self):
        hits = []
        q = JobQueue(checkpoint=lambda: hits.append(1), checkpoint_every=3)
        for _ in range(7):
            q.run_sync("t", lambda: {"ok": 1})
        self.assertEqual(len(hits), 2, "7잡 · 주기 3 이면 2회여야 한다")

    def test_job_survives_a_failing_checkpoint(self):
        def boom():
            raise RuntimeError("checkpoint 실패")
        q = JobQueue(checkpoint=boom, checkpoint_every=1)
        self.assertEqual(q.run_sync("t", lambda: {"ok": 2}), {"ok": 2})

    def test_zero_disables(self):
        hits = []
        q = JobQueue(checkpoint=lambda: hits.append(1), checkpoint_every=0)
        for _ in range(5):
            q.run_sync("t", lambda: {"ok": 1})
        self.assertEqual(hits, [], "0 이면 꺼져야 한다")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버 미설치")
class CheckpointShrinksWal(unittest.TestCase):
    """실엔진 — CHECKPOINT 가 정말 WAL 을 본 파일에 반영하는가."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     "MEMORY_EMBEDDING_MODEL": "local-en"}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _wal_size(self, data_dir: str) -> int:
        wal = Path(data_dir + ".wal")
        return wal.stat().st_size if wal.exists() else 0

    def test_checkpoint_reduces_the_wal(self):
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        data_dir = cfg.data_dir

        with store.session() as s:
            for i in range(300):          # WAL 이 눈에 띄게 자랄 만큼
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='x'", id=f"n{i}")
        grown = self._wal_size(data_dir)
        self.assertGreater(grown, 0, "쓰기 뒤에는 WAL 이 있어야 한다 "
                                     "(없다면 이 엔진은 WAL 을 안 쓰고 이 사고도 불가능하다)")

        store.run("CHECKPOINT")
        after = self._wal_size(data_dir)
        store.close()
        self.assertLess(after, grown,
                        f"CHECKPOINT 뒤 WAL 이 줄지 않았다 ({grown} → {after}) — "
                        f"그러면 주기 체크포인트가 사고를 못 막는다")
