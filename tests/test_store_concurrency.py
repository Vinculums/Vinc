# -*- coding: utf-8 -*-
"""스토어 동시성 계약(R4 · §11 P2) — 2026-07-31 실측으로 확정한 것들을 못박는다.

배경: `store/embedded_backend.py` 는 "Kùzu 계보는 MVCC 로 writer 진행 중 reader
스냅샷 일관을 보장한다(P2 착수 시 실측 확정 — 불성립 시 reader 직렬화로 폴백)"를
**미검증 가정**으로 오래 달고 있었다. 측정 결과:

  · MVCC 는 성립한다 — 라이터가 쉬지 않고 써도 조회 지연이 거의 그대로다.
    따라서 예고된 "reader 직렬화" 폴백은 필요 없고, 도입하면 그게 P2 게이트 위반이다.
  · 그러나 **쓰기 트랜잭션은 하나뿐이다** — 리더 커넥션이 DDL(지연 인덱스 빌드)을
    돌리는 동안 라이터의 평범한 MERGE 조차 거절된다. 읽기 경로는 쓰기를 하면 안 된다.

이 파일은 그 두 결론이 코드에서 유지되는지 검사한다.
"""
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class StoreConcurrencyTests(unittest.TestCase):
    def setUp(self):
        from store.config import load_config
        from store.backend import open_store
        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ.pop("MEMORY_DATA_DIR", None)
        self.store = open_store(load_config({"data_dir": os.path.join(self._tmp, "store")}))
        self.assertIsNotNone(self.store, "임베디드 스토어 open 실패")
        self.store.provision(dim=4)
        self.app = VincApp(store=self.store, auth=SessionAuth("t"))

    def tearDown(self):
        try:
            self.app.close()
        except Exception:
            pass
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_chunks(self, n=120):
        for i in range(n):
            self.store.run(
                "MERGE (c:Chunk {chunk_id:$i}) SET c.text=$t, c.owner_id='u', c.doc_id='d'",
                {"i": f"ch{i}", "t": f"본문 {i} alpha beta"})

    # ── 커넥션 수명 ────────────────────────────────────────────────────────
    def test_reader_close_actually_closes_the_driver_connection(self):
        """참조만 버리면 해제가 아니다 — 드라이버 `close()` 를 실제로 부른다.

        드라이버 Connection 에는 `__del__` 이 없어(실측), 참조를 놓는 것만으로는
        언제 풀릴지 알 수 없다. 요청마다 커넥션을 만드는 구조라 그 누수는 부하에
        비례해 쌓인다."""
        rv = self.store.reader_view()
        conn = rv._conn
        self.assertTrue(hasattr(conn, "close"), "드라이버가 close() 를 제공하지 않는다")
        rv.run("MATCH (c:Chunk) RETURN count(c) AS n")
        rv.close()
        # 닫힌 커넥션은 더 못 쓴다 — 이게 '진짜 닫혔다'의 증거다.
        with self.assertRaises(Exception):
            conn.execute("MATCH (c:Chunk) RETURN count(c)")

    def test_close_is_idempotent(self):
        rv = self.store.reader_view()
        rv.close()
        rv.close()   # 두 번 닫아도 터지지 않는다(_read 의 finally 와 중복 호출 대비)

    # ── R4: 읽기 경로는 쓰기를 하지 않는다 ─────────────────────────────────
    def test_lazy_index_build_runs_on_the_job_worker(self):
        """지연 인덱스 빌드(T3b)는 DDL = 쓰기다 — 잡 워커에서 돌아야 한다(R4)."""
        self._seed_chunks()
        self.store.drop_search_indexes()
        seen = {}
        orig = self.store.ensure_search_indexes

        def spy(*a, **kw):
            seen["thread"] = threading.current_thread().name
            return orig(*a, **kw)

        self.store.ensure_search_indexes = spy
        try:
            rv = self.app._reader()
            rv.ensure_search_indexes(dim=4)
            rv.close()
        finally:
            self.store.ensure_search_indexes = orig
        self.assertEqual(seen.get("thread"), "vinc-job-worker",
                         "읽기 경로가 워커 밖에서 DDL 을 실행했다 — R4 위반")

    def test_writer_survives_a_concurrent_lazy_index_build(self):
        """리더가 인덱스를 만드는 동안 라이터의 **평범한 저장**이 살아야 한다.

        고치기 전에는 여기서 `Cannot start a new write transaction` 이 났다(실측):
        엔진이 쓰기 트랜잭션을 하나만 허용하는데 리더가 그걸 붙들고 있었기 때문이다."""
        self._seed_chunks()
        self.store.drop_search_indexes()
        res = {}
        started = threading.Event()

        def reader_side():
            v = self.app._reader()
            started.set()
            try:
                v.ensure_search_indexes(dim=4)
                res["reader"] = "ok"
            except Exception as exc:
                res["reader"] = f"{type(exc).__name__}: {exc}"
            v.close()

        def writer_side():
            # ⚠ BaseException 까지 잡는다 — 스레드에서 새어 나간 예외는 조용히
            # 사라지고 결과 dict 만 비어, "왜 None 인가"로 시간을 잡아먹는다.
            try:
                if not started.wait(5):
                    res["writer"] = "reader 가 시작 신호를 안 줬다"
                    return
                time.sleep(0.02)      # 리더 빌드 한복판을 노린다
                self.app.jobs.run_sync("probe.write", lambda: (
                    self.store.run("MERGE (c:Concept {concept_id:'concept:probe'}) "
                                   "SET c.owner_id='u'"), {"ok": 1})[1], timeout=60)
                res["writer"] = "ok"
            except BaseException as exc:   # noqa: BLE001 — 진단 목적
                res["writer"] = f"{type(exc).__name__}: {exc}"

        ts = [threading.Thread(target=reader_side), threading.Thread(target=writer_side)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=90)
        self.assertEqual(res.get("reader"), "ok", f"리더 빌드 실패: {res.get('reader')}")
        self.assertEqual(res.get("writer"), "ok",
                         f"지연 빌드 중 쓰기가 깨졌다(회귀): {res.get('writer')}")

    # ── §11 P2 게이트: 잡 실행 중 조회 응답성 ──────────────────────────────
    def test_reads_stay_responsive_while_writes_run(self):
        """MVCC 실측 확정 — 라이터가 쉬지 않고 써도 조회는 막히지 않는다.

        이 테스트는 "reader 직렬화 폴백"을 누가 도입하면 곧바로 깨진다(그게 목적이다:
        폴백은 필요 없고, 넣으면 P2 게이트를 스스로 어기는 일이다)."""
        self._seed_chunks(40)
        stop = threading.Event()

        def busy_writer():
            """⚠ 쓰기는 **반드시 잡 큐로** 보낸다(R4). 처음엔 이 테스트가 평범한
            스레드에서 `store.run` 을 직접 불렀는데, 그건 검사하려는 불변식을 테스트가
            먼저 어기는 것이고 실제로 드라이버가 네이티브 크래시를 냈다(전체 스위트
            실행 시 access violation). 운영 경로와 같은 모양으로 쓴다."""
            i = 0
            while not stop.is_set():
                try:
                    self.app.jobs.run_sync("probe.busy", lambda i=i: (
                        self.store.run("MERGE (c:Concept {concept_id:$i}) SET c.owner_id='u'",
                                       {"i": f"concept:b{i}"}), {"ok": 1})[1], timeout=30)
                except Exception:
                    return
                i += 1

        bt = threading.Thread(target=busy_writer, daemon=True)
        bt.start()
        time.sleep(0.2)
        lat = []
        try:
            for _ in range(25):
                t0 = time.perf_counter()
                r = self.app._reader()
                r.run("MATCH (c:Concept) RETURN count(c) AS n")
                r.close()
                lat.append((time.perf_counter() - t0) * 1000)
        finally:
            stop.set()
            bt.join(timeout=5)
        # 넉넉한 상한 — 회귀(직렬화 도입)는 이 값을 자릿수로 넘긴다. 실측 중앙 1.5ms.
        self.assertLess(statistics.median(lat), 200.0,
                        f"쓰기 중 조회가 막힌다 — P2 게이트 위반(중앙 {statistics.median(lat):.1f}ms)")

    # ── 진단 정직성 ────────────────────────────────────────────────────────
    def test_transaction_conflict_is_not_reported_as_missing_extension(self):
        """확장 로드 실패의 **사유를 지어내지 않는다**.

        다른 커넥션이 쓰기 트랜잭션을 쥐면 LOAD 가 거절되는데, 그걸 "확장 미가용 —
        오프라인 배포 시 번들 필요" 로 감싸면 운영자를 패키징 문제로 보낸다(실측으로
        실제 그 문구가 떴다)."""
        class _Conn:
            def execute(self, *_a, **_k):
                raise RuntimeError(
                    "Cannot start a new write transaction in the system. "
                    "Only one write transaction at a time is allowed")

        with self.assertRaises(RuntimeError) as ctx:
            self.store._load_ext("vector", _Conn())
        msg = str(ctx.exception)
        self.assertIn("transaction", msg.lower())
        self.assertNotIn("번들", msg, "트랜잭션 충돌을 확장 부재로 오진했다")


if __name__ == "__main__":
    unittest.main()
