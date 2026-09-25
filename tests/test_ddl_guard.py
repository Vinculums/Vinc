# -*- coding: utf-8 -*-
"""DDL 배타 가드 — 인덱스·스키마 DDL 이 다른 커넥션의 읽기와 겹치지 않는다.

허브가 3~10분마다 네이티브 힙 손상으로 죽던 것(2026-09-24~)의 방어선이다.
근거: [[record:hub-segfault-loop-index-ddl-races-readers-2026-09-25]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.embedded_backend import (DdlGuardTimeout, _DdlGuard,  # noqa: E402
                                    _import_driver)

_DRIVER, _NAME = _import_driver()


class DdlGuardUnitTests(unittest.TestCase):
    """가드 자체의 계약 — 드라이버 없이 돈다."""

    def test_exclusive_excludes_shared(self):
        g = _DdlGuard()
        inside = threading.Event()
        release = threading.Event()
        got_shared = []

        def ddl():
            with g.exclusive():
                inside.set()
                release.wait(5)

        t = threading.Thread(target=ddl)
        t.start()
        inside.wait(5)

        def reader():
            with g.shared(timeout=5):
                got_shared.append(time.monotonic())

        r = threading.Thread(target=reader)
        r.start()
        time.sleep(0.2)
        self.assertEqual(got_shared, [], "배타를 쥔 동안 공유가 들어왔다")
        release.set()
        t.join(5)
        r.join(5)
        self.assertEqual(len(got_shared), 1, "배타가 풀린 뒤에도 공유가 못 들어왔다")

    def test_shared_blocks_exclusive_and_writer_goes_first(self):
        g = _DdlGuard()
        order = []
        hold = threading.Event()
        started = threading.Event()

        def long_reader():
            with g.shared():
                started.set()
                hold.wait(5)
            order.append("reader1-out")

        def ddl():
            with g.exclusive(timeout=5):
                order.append("ddl")

        def late_reader():
            with g.shared(timeout=5):
                order.append("reader2")

        a = threading.Thread(target=long_reader)
        a.start()
        started.wait(5)
        b = threading.Thread(target=ddl)
        b.start()
        time.sleep(0.1)                 # DDL 이 대기열에 선다
        c = threading.Thread(target=late_reader)
        c.start()
        time.sleep(0.1)
        self.assertEqual(order, [], "대기 중인 DDL 을 새 읽기가 앞질렀다(라이터 우선 위반)")
        hold.set()
        for t in (a, b, c):
            t.join(5)
        self.assertLess(order.index("ddl"), order.index("reader2"))

    def test_reentrancy_does_not_deadlock(self):
        g = _DdlGuard()
        with g.exclusive():
            with g.exclusive():
                with g.shared():
                    pass
        with g.shared():
            with g.shared():
                pass
            # 공유를 쥔 채 배타로 승격 — 내려놓고 잡고 되돌려 받는다
            with g.exclusive(timeout=1):
                pass
            self.assertEqual(g._readers, 1)
        self.assertEqual((g._readers, g._owner, g._waiting), (0, None, 0))

    def test_reentrant_shared_is_not_stopped_by_a_waiting_writer(self):
        g = _DdlGuard()
        done = []
        with g.shared():
            g._waiting += 1             # 대기 중인 라이터를 흉내 낸다
            try:
                with g.shared(timeout=0.2):
                    done.append(1)
            finally:
                g._waiting -= 1
        self.assertEqual(done, [1])

    def test_timeouts_raise_instead_of_hanging(self):
        g = _DdlGuard()
        entered = threading.Event()
        release = threading.Event()

        def reader():
            with g.shared():
                entered.set()
                release.wait(5)

        t = threading.Thread(target=reader)
        t.start()
        entered.wait(5)
        t0 = time.monotonic()
        with self.assertRaises(DdlGuardTimeout):
            with g.exclusive(timeout=0.2):
                pass
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual(g._waiting, 0, "실패한 배타가 대기 표시를 남겼다")
        release.set()
        t.join(5)
        self.assertIsInstance(DdlGuardTimeout("x"), TimeoutError)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DdlRacesReadersE2E(unittest.TestCase):
    """실엔진: 리더 스레드들이 도는 동안 인덱스를 드롭·재생성하고 임베딩을 적용한다.

    가드 전에는 이 모양(DDL 과 다른 커넥션의 스캔이 겹침)이 허브를 죽였다.
    확장이 없으면 **실패한다**(skip 하지 않는다) — 이 테스트가 조용히 빠지면 방어선이
    검증 없이 남는다."""

    def setUp(self):
        from store.backend import open_store
        from store.config import load_config
        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(self._tmp, "store")
        self.store = open_store(load_config())
        self.assertIsNotNone(self.store)
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_readers_survive_index_ddl_and_embedding_applies(self):
        from api import docstore as ds
        docs = [ds.put_doc(self.store, owner="user:a", title=f"D{i}",
                           text=f"가드검색어 doc{i} alpha\n\n" + "본문 하나. " * 80,
                           max_chars=200)["doc_id"] for i in range(6)]
        self.store.ensure_search_indexes(dim=4)
        chunk_ids = {d: [r["id"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id=$d RETURN c.chunk_id AS id", {"d": d})]
            for d in docs}

        stop = threading.Event()
        errors: list[str] = []
        reads = [0]

        def reader(seed: int):
            rv = self.store.reader_view()
            try:
                while not stop.is_set():
                    rv.fulltext_search("가드검색어", 5, {"owner_id": "user:a"})
                    try:
                        rv.vector_search([1.0, 0, 0, seed / 10], 5)
                    except Exception as exc:          # 드롭된 틈 — 그건 정상 강등이다
                        if "index" not in str(exc).lower():
                            raise
                    rv.run("MATCH (c:Chunk) RETURN count(c) AS n")
                    reads[0] += 1
            except Exception as exc:
                errors.append(f"reader{seed}: {type(exc).__name__}: {exc}")
            finally:
                rv.close()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        t0 = time.monotonic()
        rounds = 0
        try:
            while rounds < 12 and time.monotonic() - t0 < 12:
                for d in docs:
                    emb = {c: [[1.0, float(rounds), 0, 0]] for c in chunk_ids[d]}
                    r = ds.apply_embeddings(self.store, d, emb)
                    self.assertTrue(r["executed"], r)
                self.store.ensure_search_indexes(dim=4)      # 검색이 부르는 지연 재생성
                if rounds % 4 == 0:
                    self.store.rebuild_fts_index()
                rounds += 1
        finally:
            stop.set()
            for t in threads:
                t.join(20)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(rounds, 3, "DDL 라운드가 충분히 돌지 않았다")
        self.assertGreater(reads[0], 0, "리더가 한 번도 못 읽었다 — 겹침을 검증하지 못했다")
        self.assertLess(time.monotonic() - t0, 25)


if __name__ == "__main__":
    unittest.main()
