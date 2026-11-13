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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _NAME = import_driver()


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

    def test_graph_algo_is_reachable_from_the_read_path(self):
        """구조 진단(WCC·Louvain)이 조회 경로에서 **실제로 돌아야** 한다.

        `_ReaderView` 가 `graph_algo` 를 노출하지 않던 시절, `api.query._run_algo` 의
        `getattr(store, "graph_algo", None)` 이 None 이라 서버 진단이 **상시**
        `algo_unsupported` 로 강등됐다(실측: read_diagnose 가 executed=False,
        components=[] — 같은 데이터에 writer 로 직접 부르면 컴포넌트 3개 정상 검출).
        상태 화면의 "연결 안 된 섬 n개"와 suggest_ties 후보가 배포된 채 죽어 있었다.

        그리고 노출만으로는 부족하다 — project_graph 는 DDL 이라 리더 커넥션에서
        돌리면 라이터를 막는다. **잡 워커에서** 돌아야 한다(R4)."""
        rv = self.app._reader()
        self.assertTrue(hasattr(rv, "graph_algo"),
                        "리더 뷰에 graph_algo 가 없다 — 진단이 상시 강등된다")
        seen = {}
        orig = self.store.graph_algo

        def spy(*a, **kw):
            seen["thread"] = threading.current_thread().name
            return [{"id": "concept:x", "value": 0}]

        self.store.graph_algo = spy
        try:
            rows = rv.graph_algo("weakly_connected_components")
        finally:
            self.store.graph_algo = orig
            rv.close()
        self.assertEqual(seen.get("thread"), "vinc-job-worker",
                         "graph_algo 가 워커 밖에서 DDL 을 실행했다 — R4 위반")
        # 잡 워커는 비-dict 를 {"result": ...} 로 감싼다 — 호출부가 행을 순회하므로
        # 여기서 풀어 주지 않으면 dict 키(str)를 행으로 읽어 터진다(실측 사고).
        self.assertEqual(rows, [{"id": "concept:x", "value": 0}],
                         "잡 결과 포장을 풀지 않아 행 대신 다른 게 나왔다")

    def test_algo_failure_still_names_the_real_reason(self):
        """스토어가 투영을 못 읽으면 그렇게 말해야 한다 — 사유가 뭉개지면 운영자가
        엉뚱한 곳을 본다. 계산이 `api/graph_algo.py` 로 옮겨 온 뒤로 실패가 생기는
        자리는 엔진 확장이 아니라 투영 조회다."""
        import api.query as q

        class _Down:
            def run(self, *a, **kw):
                raise RuntimeError("no reachable store")

        rows, note = q._run_algo(_Down(), "weakly_connected_components")
        self.assertIsNone(rows)
        self.assertIn("no reachable store", note or "",
                      f"실패 사유가 그대로 전달되지 않았다: {note!r}")

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

    # ── 읽기 경로는 LOAD 하지 않는다 ───────────────────────────────────────
    def test_search_does_not_reload_the_extension(self):
        """검색이 `LOAD EXTENSION` 을 재발행하면 안 된다.

        `LOAD` 는 **Database 스코프**라 1회면 충분한데(실측: 로드 뒤 새로 만든
        커넥션도 자기 LOAD 없이 FTS 호출 성공), **이미 로드된 확장을 다시 LOAD 해도
        쓰기 트랜잭션을 요구한다**. 그래서 매 검색이 LOAD 를 보내던 시절엔 라이터가
        도는 동안 검색의 49.3% 가 실패하고 라이터 MERGE 도 26.0% 가 실패했다.
        이 테스트는 그 재발행이 돌아오면 깨진다."""
        self._seed_chunks(20)
        self.app.jobs.run_sync("idx", lambda: self.store.ensure_search_indexes(dim=4),
                               timeout=60)
        loads = []
        orig = self.store._load_ext

        def spy(ext, **kw):
            loads.append((ext, kw.get("reader", False)))
            return orig(ext, **kw)

        self.store._load_ext = spy
        try:
            rv = self.app._reader()
            rv.fulltext_search("alpha", 3)
            rv.close()
        finally:
            self.store._load_ext = orig
        self.assertTrue(loads, "검색이 _load_ext 를 아예 안 불렀다 — 테스트가 낡았다")
        self.assertTrue(all(is_reader for _e, is_reader in loads),
                        "검색이 writer 로 LOAD 를 보냈다 — R4 위반")
        # 캐시 적중이라 엔진 왕복이 없어야 한다.
        self.assertIn("fts", self.store._exts, "부팅 선로드가 캐시를 안 채웠다")

    def test_search_survives_a_busy_writer(self):
        """라이터가 쉬지 않고 쓰는 동안 검색이 살아야 한다.

        고치기 전 실측: 검색 150회 중 74회가 `Cannot start a new write transaction`
        으로 실패(49.3%). 원인은 검색이 보내던 LOAD 였다."""
        self._seed_chunks(40)
        self.app.jobs.run_sync("idx", lambda: self.store.ensure_search_indexes(dim=4),
                               timeout=60)
        stop = threading.Event()

        def busy_writer():
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
        fails = []
        try:
            for _ in range(40):
                r = self.app._reader()
                try:
                    r.fulltext_search("alpha", 3)
                except Exception as exc:
                    fails.append(f"{type(exc).__name__}: {exc}")
                finally:
                    r.close()
        finally:
            stop.set()
            bt.join(timeout=5)
        self.assertEqual(fails, [], f"쓰기 중 검색이 실패했다({len(fails)}/40)")

    # ── 진단 정직성 ────────────────────────────────────────────────────────
    def test_transaction_conflict_is_not_reported_as_missing_extension(self):
        """확장 로드 실패의 **사유를 지어내지 않는다**.

        다른 커넥션이 쓰기 트랜잭션을 쥐면 LOAD 가 거절되는데, 그걸 "확장 미가용,
        오프라인 배포 시 번들 필요" 로 감싸면 운영자를 패키징 문제로 보낸다(실측으로
        실제 그 문구가 떴다)."""
        class _Conn:
            def execute(self, *_a, **_k):
                raise RuntimeError(
                    "Cannot start a new write transaction in the system. "
                    "Only one write transaction at a time is allowed")

        # 캐시를 비우고 writer 커넥션을 가짜로 갈아 끼운다 — 이제 LOAD 는 writer
        # 에서만 일어나므로 conn 인자로 주입하던 예전 방식은 쓸 수 없다.
        saved_conn, saved_exts = self.store._conn, set(self.store._exts)
        self.store._conn, self.store._exts = _Conn(), set()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self.store._load_ext("vector")
        finally:
            self.store._conn, self.store._exts = saved_conn, saved_exts
        msg = str(ctx.exception)
        self.assertIn("transaction", msg.lower())
        self.assertNotIn("번들", msg, "트랜잭션 충돌을 확장 부재로 오진했다")


class ExtLoadDiagnosisTests(unittest.TestCase):
    """드라이버가 **사유를 못 읽는 형태로** 올려도 진단이 무너지지 않는다.

    2026-08-07 실측(ladybug 0.18.0 · 한국어 Windows): `LOAD EXTENSION vector` 가
    실패하면서 드라이버가 원래 예외 대신 `UnicodeDecodeError: 'utf-8' codec can't
    decode byte 0xc1 in position 165` 을 올렸다. 엔진 메시지에 OS 가 만든 cp949
    문구(`지정된 모듈을 찾을 수 없습니다` = 의존 DLL 미해결)가 섞였는데 pybind
    경계가 strict UTF-8 로만 디코드하기 때문이다.

    그 결과 `_load_ext` 의 사유 분류(부분문자열 매칭)가 **통째로 실명(失明)**해
    무관한 실패가 전부 "확장 미가용 — 오프라인 배포 시 번들 필요" 로 떨어졌다.
    운영자를 패키징 문제로 보내는 오진이고, 위 `test_transaction_conflict_...`
    가 막으려던 바로 그 사고가 다른 입구로 되살아난 것이다.

    드라이버가 못 읽어도 원문 바이트는 `UnicodeDecodeError.object` 에 남는다 —
    이 클래스는 그걸 실제로 되살려 쓰는지 고정한다. 드라이버 불요(순수)."""

    #: 실측 원문(cp949) — ladybug 가 UTF-8 로 못 읽고 통째로 버린 바로 그 바이트열.
    _REAL = ("IO exception: Failed to load library: "
             "C:\\Users\\u/.lbdb/extension/0.18.0/win_amd64/vector/libvector.lbug_extension "
             "which is needed by extension: vector.\n"
             "Error: 지정된 모듈을 찾을 수 없습니다.\r\n.")

    @staticmethod
    def _undecodable(text: str) -> UnicodeDecodeError:
        """드라이버가 올리는 것과 같은 예외 — 원문은 cp949 바이트로만 남아 있다."""
        raw = text.encode("cp949")
        bad = raw.index(b"\xc1") if b"\xc1" in raw else 0
        return UnicodeDecodeError("utf-8", raw, bad, bad + 1, "invalid start byte")

    def _backend(self, exc: BaseException):
        from store.embedded_backend import EmbeddedBackend

        class _Conn:
            def execute(self, *_a, **_k):
                raise exc

        class _Cfg:
            data_dir = None

        return EmbeddedBackend(None, _Conn(), "ladybug", _Cfg())

    @staticmethod
    def _korean_windows():
        """한국어 Windows 의 ANSI 코드페이지를 사다리에 꽂는다.

        원문은 cp949 라 그 코덱이 사다리에 있어야만 복원된다. 실기(한국어 Windows)
        에서는 로케일 칸이 이미 cp949 라 그냥 통하지만, **CI 는 Linux · UTF-8** 이고
        `mbcs` 는 Windows 전용 별칭이라 코덱 자체가 없다. `os.name` 만 "nt" 로
        바꿔서는 복원이 안 되고, 그러면 이 테스트가 지키려는 것("사유가 살아남는다")
        이 CI 에서 조용히 검증되지 않는다. 플랫폼 가정을 명시적으로 주입한다."""
        from unittest import mock
        return mock.patch("store.embedded_backend._ANSI_FALLBACKS", ("cp949",))

    def test_decoder_recovers_the_original_message(self):
        """`_driver_error_text` 는 버려진 원문 바이트를 되살린다."""
        from store.embedded_backend import _driver_error_text
        with self._korean_windows():
            got = _driver_error_text(self._undecodable(self._REAL))
            self.assertIn("Failed to load library", got)
            self.assertIn("지정된 모듈을 찾을 수 없습니다", got)
            # 감싸 올린 경우(예외 체인)도 따라간다 — 드라이버 구현이 바뀌어도 유지.
            try:
                try:
                    raise self._undecodable(self._REAL)
                except UnicodeDecodeError as inner:
                    raise RuntimeError("query failed") from inner
            except RuntimeError as outer:
                self.assertIn("지정된 모듈을 찾을 수 없습니다", _driver_error_text(outer))

    def test_undecodable_load_failure_names_the_real_cause(self):
        """읽을 수 없는 예외가 와도 **진짜 사유**를 말한다 — 코덱 문구로 뭉개지 않는다."""
        be = self._backend(self._undecodable(self._REAL))
        with self._korean_windows():
            with self.assertRaises(RuntimeError) as ctx:
                be._load_ext("vector")
        msg = str(ctx.exception)
        self.assertIn("Failed to load library", msg,
                      f"드라이버가 버린 원문을 되살리지 못했다: {msg!r}")
        self.assertIn("지정된 모듈을 찾을 수 없습니다", msg)
        self.assertNotIn("codec can't decode", msg,
                         "코덱 사고 문구가 사유 자리를 차지했다")
        # 파일은 있는데 의존 라이브러리가 없는 것이다 — "번들 필요" 는 틀린 처방이다.
        self.assertNotIn("번들 필요", msg,
                         "의존 라이브러리 미해결을 번들 누락으로 오진했다")
        # 그래도 강등 사유 분류(api.query._classify_chunk_error)는 성립해야 한다.
        self.assertIn("확장 미가용", msg)

    def test_windows_load_failure_names_openssl_and_the_remedy(self):
        """Windows 에서는 **무엇이** 없는지와 **무엇을 하면 되는지**까지 말한다.

        2026-08-07 실측: 확장 3종의 PE 임포트 테이블에서 못 잡히는 비시스템 의존은
        `libssl-3-x64.dll`·`libcrypto-3-x64.dll` 둘뿐이었고(오류 126) 나머지는 전부
        잡혔다. OpenSSL 3 을 PATH 에 올린 직후 `LOAD EXTENSION vector` 가 성공했다.
        사유가 하나로 수렴하므로 처방을 메시지에 박는다 — 여기까지 말하지 않으면
        "의존 라이브러리를 못 찾았다" 는 여전히 다음 사람을 헤매게 한다.

        같은 이유로 셸에 따라 결과가 갈린다: Git for Windows 가 그 두 DLL 을 같은
        이름으로 `mingw64/bin` 에 두므로 Git Bash 는 통과하고 PowerShell 은 실패한다.
        플랫폼 분기라 `os.name` 을 갈아 끼워 양쪽을 다 고정한다(CI 는 Linux)."""
        from unittest import mock
        be = self._backend(self._undecodable(self._REAL))
        with mock.patch("store.embedded_backend.os.name", "nt"):
            with self.assertRaises(RuntimeError) as ctx:
                be._load_ext("vector")
        msg = str(ctx.exception)
        self.assertIn("libssl-3-x64.dll", msg, f"빠진 의존을 이름으로 말하지 않았다: {msg!r}")
        self.assertIn("libcrypto-3-x64.dll", msg)
        self.assertIn("PATH", msg, "처방이 없다 — 사유만으로는 다음 사람이 또 헤맨다")

        with mock.patch("store.embedded_backend.os.name", "posix"):
            with self.assertRaises(RuntimeError) as ctx:
                be._load_ext("vector")
        posix_msg = str(ctx.exception)
        self.assertNotIn("libssl-3-x64.dll", posix_msg,
                         "Windows 전용 처방이 다른 플랫폼에 새어 나갔다")
        self.assertIn("Failed to load library", posix_msg, "사유 자체는 남아야 한다")

    def test_transaction_conflict_survives_an_undecodable_message(self):
        """읽을 수 없는 형태로 와도 **트랜잭션 충돌은 트랜잭션 충돌로** 남는다.

        이게 실명(失明)의 실질 피해다: 부분문자열 분류가 코덱 문구만 보면
        `"transaction" in low` 가 안 걸려, 위 테스트가 지키던 계약이 비 UTF-8
        로케일에서만 조용히 뒤집힌다."""
        be = self._backend(self._undecodable(
            "Cannot start a new write transaction in the system. "
            "Only one write transaction at a time is allowed. 작업을 완료할 수 없습니다."))
        with self.assertRaises(RuntimeError) as ctx:
            be._load_ext("vector")
        msg = str(ctx.exception)
        self.assertIn("transaction", msg.lower())
        self.assertNotIn("번들", msg, "트랜잭션 충돌을 확장 부재로 오진했다")

    def test_bundled_path_failure_is_not_buried_by_the_name_fallback(self):
        """번들 경로가 실패하면 그 사유가 **살아남아야** 한다.

        `_load_ext` 는 번들 경로 → 이름 기반 순으로 시도하는데, 여태 마지막(이름
        기반) 실패만 올렸다. G0 배치(캐시는 없고 번들만 있는 오프라인)에서는 그게
        정확히 반대 방향을 가리킨다: 진짜 사유는 번들 쪽의 의존 라이브러리 실패인데
        이름 기반의 "설치 안 됨" 이 그걸 덮고 "번들 필요" 처방까지 붙는다 — 번들은
        이미 있는데도."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "libvector.lbug_extension").write_bytes(b"stub")
        calls = []

        def execute(_self, cypher, *_a, **_k):
            calls.append(cypher)
            if len(calls) == 1:            # 번들 경로 — 의존 라이브러리 미해결
                raise self._undecodable(self._REAL)
            raise RuntimeError(            # 이름 기반 — 캐시가 없으니 설치 안 됨
                "Binder exception: Extension: vector is an official extension "
                "and has not been installed.")

        old = os.environ.get("VINC_LBUG_EXT_DIR")
        os.environ["VINC_LBUG_EXT_DIR"] = str(d)
        try:
            be = self._backend(RuntimeError("unused"))
            type(be._conn).execute = execute
            with self.assertRaises(RuntimeError) as ctx:
                be._load_ext("vector")
        finally:
            if old is None:
                os.environ.pop("VINC_LBUG_EXT_DIR", None)
            else:
                os.environ["VINC_LBUG_EXT_DIR"] = old
        self.assertEqual(len(calls), 2, "이름 기반 폴백까지 가지 않았다 — 테스트가 낡았다")
        msg = str(ctx.exception)
        self.assertIn("Failed to load library", msg, f"번들 경로 사유가 묻혔다: {msg!r}")
        self.assertIn("has not been installed", msg, "이름 기반 사유도 함께 있어야 한다")
        self.assertNotIn("번들 필요", msg,
                         "번들이 있는데도(그 실패를 본문에 싣고도) 번들을 요구했다")

    def test_genuine_absence_still_asks_for_the_bundle(self):
        """과잉교정 방지 — 진짜로 확장이 없으면 §9·G0 처방이 그대로 나와야 한다."""
        be = self._backend(RuntimeError(
            "Binder exception: The extension vector is neither an official extension, "
            "nor does the extension path: 'vector' exist."))
        with self.assertRaises(RuntimeError) as ctx:
            be._load_ext("vector")
        msg = str(ctx.exception)
        self.assertIn("확장 미가용", msg)
        self.assertIn("번들 필요", msg)


if __name__ == "__main__":
    unittest.main()
