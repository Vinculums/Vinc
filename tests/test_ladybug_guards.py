# -*- coding: utf-8 -*-
"""ladybug 0.20.4 로 올리며 단 가드 셋 — 계획 캐시(A) · 파이썬 문장 캐시(B) · algo 강등(C).

  A  같은 매개변수 FTS 질의를 한 커넥션에서 두 번 돌리면 두 번째가 **세그폴트**한다
     (0.20.0~0.20.4, 결과가 한 행이라도 있을 때). 백엔드는 커넥션을 열 때마다 엔진의 계획
     캐시를 끈다(`enable_cached_prepared_statement='NONE'`). 세그폴트는 `except` 로 못 받으므로
     재현은 **자식 프로세스**에서 한다 — 회귀가 나면 테스트 실패로 보이지, 스위트가 죽지 않는다.
  B  0.18.1 부터 파이썬 `Connection` 이 매개변수 문장을 캐시하는데, 바인드에 실패한 문장도
     들고 있고 DDL 로 무효화하지 않는다. "색인 없음 → 색인 생성 → 같은 질의" 가 한 커넥션에서
     옛 오류를 되풀이한다(지연 FTS 빌드가 정확히 이 순서다).
  C  0.20.x 의 algo 확장은 NetworKit·Arrow 를 링크하면서 싣지 않는다. 구조 진단은 크래시가
     아니라 **이유를 단 강등**이어야 하고, 호출마다 LOAD(쓰기 트랜잭션)를 되풀이하면 안 된다.

근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
import weakref
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from store import embedded_backend as eb  # noqa: E402

_DRIVER, _NAME = eb._import_driver()


def _plan_switch() -> bool:
    return _DRIVER is not None and eb._plan_cache_switch_exists(_DRIVER)


def _open(data_dir: str):
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config({"data_dir": data_dir}))
    assert s is not None, eb.EmbeddedBackend.last_open_error
    s.provision(dim=4)
    return s


# 자식: 문서 하나를 넣고 **같은 커넥션**에서 같은 FTS 질의를 세 번 돌린다(라이터·리더 둘 다).
# 리눅스에서는 코어 덤프를 끈다 — 세그폴트 한 번에 덤프가 수 GB 다.
_TWICE = r"""
import os, sys
if sys.platform.startswith("linux"):
    import ctypes
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
sys.path.insert(0, sys.argv[1])
from store.backend import open_store
from store.config import load_config
from api import docstore
s = open_store(load_config({"data_dir": sys.argv[2]}))
s.provision(dim=4)
s.build_search_indexes(dim=4)
r = docstore.put_doc(s, owner="user:a", text="정본검색어 alpha\n\n" + "본문 하나. " * 40, title="A")
assert r.get("executed"), r
for i in range(3):
    print("writer", i, len(s.fulltext_search("정본검색어", 5)), flush=True)
rv = s.reader_view()
for i in range(3):
    print("reader", i, len(rv.fulltext_search("정본검색어", 5)), flush=True)
rv.close()
s.close()
print("survived", flush=True)
"""


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PlanCacheGuardTests(unittest.TestCase):
    """A — 엔진 계획 캐시."""

    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "store")

    def _child(self, env_extra: dict | None = None):
        env = dict(os.environ)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-c", _TWICE, str(ROOT), self.dir],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180, env=env)

    @unittest.skipUnless(_plan_switch(), "계획 캐시 스위치가 없는 엔진(0.20.2 이전) — 결함도 없다")
    def test_every_connection_opens_with_the_plan_cache_off(self):
        s = _open(self.dir)
        try:
            q = "CALL current_setting('enable_cached_prepared_statement') RETURN *"
            def scope(rows):
                return [str(v).upper() for r in rows for v in r.values()]
            self.assertEqual(scope(s.run(q)), ["NONE"])
            rv = s.reader_view()
            try:
                self.assertEqual(scope(rv.run(q)), ["NONE"],
                                 "리더 커넥션은 따로 설정해야 한다(커넥션 단위다)")
            finally:
                rv.close()
        finally:
            s.close()

    def test_the_same_fts_query_twice_on_one_connection(self):
        r = self._child()
        self.assertEqual(r.returncode, 0, f"rc={r.returncode}\n{r.stdout}\n{r.stderr[-2000:]}")
        lines = r.stdout.splitlines()
        self.assertIn("survived", lines)
        hits = [ln for ln in lines if ln.startswith(("writer", "reader"))]
        self.assertEqual(len(hits), 6)
        self.assertTrue(all(ln.endswith(" 1") for ln in hits), hits)

    @unittest.skipUnless(sys.platform.startswith("linux") and _plan_switch(),
                         "대조 재현은 리눅스·스위치 있는 엔진에서만(세그폴트를 일부러 낸다)")
    def test_control_the_engine_default_still_crashes(self):
        """대조: 엔진 기본값(`BOTH`)이면 같은 자식이 죽는다. 이 테스트가 **실패하기 시작하면**
        상류가 고친 것이다 — 그때는 가드(`_PLAN_CACHE_SCOPE`)를 걷어 내고 이 테스트도 지운다."""
        r = self._child({"VINC_LBUG_PLAN_CACHE": "BOTH"})
        self.assertNotEqual(r.returncode, 0,
                            "기본 계획 캐시로도 살아남았다 — 상류 수정 여부를 확인하고 가드를 걷을 것")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class StatementCacheGuardTests(unittest.TestCase):
    """B — 파이썬 문장 캐시. 실엔진으로 순서를 그대로 밟는다."""

    def setUp(self):
        self.store = _open(os.path.join(tempfile.mkdtemp(), "store"))
        from api import docstore
        r = docstore.put_doc(self.store, owner="user:a", text="lazybuild zeta body", title="Z")
        self.assertTrue(r.get("executed"), r)

    def tearDown(self):
        self.store.close()

    def test_query_create_index_query_again_on_one_connection(self):
        s = self.store
        s.drop_search_indexes()                          # 색인이 없는 상태에서 시작한다
        with self.assertRaises(Exception) as ctx:
            s.fulltext_search("lazybuild", 5)
        self.assertIn("index", str(ctx.exception).lower())
        s.build_search_indexes(dim=4)                    # 같은 라이터 커넥션의 DDL
        self.assertEqual(len(s.fulltext_search("lazybuild", 5)), 1,
                         "색인을 만든 뒤에도 실패한 바인드를 되풀이했다")

    def test_a_reader_sees_an_index_the_writer_built(self):
        """리더 커넥션이 먼저 실패하고, 라이터가 색인을 세우고, **같은 리더**가 다시 묻는다."""
        s = self.store
        s.drop_search_indexes()
        rv = s.reader_view()
        try:
            with self.assertRaises(Exception):
                rv.fulltext_search("lazybuild", 5)
            s.build_search_indexes(dim=4)
            self.assertEqual(len(rv.fulltext_search("lazybuild", 5)), 1)
        finally:
            rv.close()

    def test_the_lazy_build_path_answers_on_first_search(self):
        """`api.query.search` 의 지연 빌드(색인 없음 → 빌드 → 재시도)가 행을 돌려준다."""
        from api import query as q
        self.store.drop_search_indexes()
        res = q.search("lazybuild", owner="user:a", store=self.store, top_k=5)
        self.assertTrue(res.get("rows"), res)


class _FakeRaw:
    """드라이버 커넥션 흉내 — 캐시와 잠금, 그리고 실패시킬 문장."""

    def __init__(self, fail_on: str = ""):
        self._pybind_implicit_prepared_cache = {("seed", ()): object()}
        self._prepared_cache_lock = threading.RLock()
        self.fail_on = fail_on
        self.seen = []

    def execute(self, query, parameters=None):
        self.seen.append(query)
        if parameters:
            self._pybind_implicit_prepared_cache[(query, tuple(parameters))] = object()
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("Binder exception: nope")
        return "ok"


class ConnWrapperTests(unittest.TestCase):
    """B 가드의 판정만 — 드라이버 없이."""

    def test_a_failed_parameterized_statement_is_not_kept(self):
        raw = _FakeRaw(fail_on="QUERY_FTS_INDEX")
        c = eb._Conn(raw)
        with self.assertRaises(RuntimeError):
            c.execute("CALL QUERY_FTS_INDEX('Chunk','chunk_ft',$q) RETURN node", {"q": "x"})
        self.assertEqual(raw._pybind_implicit_prepared_cache, {})

    def test_ddl_clears_every_connection_of_the_store(self):
        fam = weakref.WeakSet()
        writer, reader = eb._Conn(_FakeRaw(), fam), eb._Conn(_FakeRaw(), fam)
        reader.execute("MATCH (n) WHERE n.id = $id RETURN n", {"id": 1})
        self.assertTrue(reader._raw._pybind_implicit_prepared_cache)
        writer.execute("CALL CREATE_FTS_INDEX('Chunk','chunk_ft',['text'])")
        self.assertEqual(reader._raw._pybind_implicit_prepared_cache, {})
        self.assertEqual(writer._raw._pybind_implicit_prepared_cache, {})

    def test_plain_reads_and_writes_keep_the_cache(self):
        raw = _FakeRaw()
        c = eb._Conn(raw)
        c.execute("MATCH (n) WHERE n.id = $id SET n.x = 1", {"id": 1})
        c.execute("MERGE (n:Concept {concept_id: $id})", {"id": "a"})
        self.assertEqual(len(raw._pybind_implicit_prepared_cache), 3)

    def test_ddl_detector(self):
        for q in ("CALL CREATE_FTS_INDEX('Chunk','chunk_ft',['text'])",
                  "  call drop_vector_index('ChunkVec','chunk_vec')",
                  "CREATE NODE TABLE X(id STRING, PRIMARY KEY(id))",
                  "DROP TABLE ChunkVec", "ALTER TABLE Chunk ADD x STRING",
                  "ALTER TABLE ChunkVecRebuild RENAME TO ChunkVec",
                  "CALL project_graph('g', ['Concept'], ['RELATED_TO'])",
                  "CALL drop_projected_graph('g')", "LOAD EXTENSION '/x/libfts.lbug_extension'"):
            self.assertTrue(eb._DDL_STMT.match(q), q)
        for q in ("MATCH (c:Chunk) RETURN c", "CREATE (:Chunk {chunk_id: 'x'})",
                  "CALL QUERY_FTS_INDEX('Chunk','chunk_ft',$q) RETURN node",
                  "CALL enable_cached_prepared_statement='NONE'", "CHECKPOINT"):
            self.assertIsNone(eb._DDL_STMT.match(q), q)

    def test_attributes_pass_through(self):
        raw = _FakeRaw()
        raw.close = lambda: "closed"
        self.assertEqual(eb._Conn(raw).close(), "closed")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class AlgoDegradeTests(unittest.TestCase):
    """C — algo 확장이 안 올라오는 엔진에서 구조 진단은 이유를 단 강등이다."""

    def setUp(self):
        self.store = _open(os.path.join(tempfile.mkdtemp(), "store"))
        try:
            self.store._load_ext("algo")
        except RuntimeError as exc:
            self.load_error = str(exc)
        else:
            self.skipTest("이 엔진에서는 algo 확장이 올라온다 — 강등 경로가 필요 없다")

    def tearDown(self):
        self.store.close()

    def test_the_reason_names_the_upstream_defect_and_the_lost_feature(self):
        self.assertIn("#857", self.load_error)
        self.assertIn("vinc_diagnose", self.load_error)

    def test_the_failure_is_remembered_not_retried(self):
        seen = []
        real = self.store._conn

        class _Spy:
            def execute(self, q, *a):
                seen.append(q)
                return real.execute(q, *a)

            def __getattr__(self, name):
                return getattr(real, name)

        self.store._conn = _Spy()
        try:
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    self.store.graph_algo("weakly_connected_components")
        finally:
            self.store._conn = real
        self.assertFalse([q for q in seen if "LOAD EXTENSION" in q],
                         "실패가 확정된 확장을 호출마다 다시 LOAD 했다")

    def test_diagnose_answers_without_the_extension(self):
        """🔴 이것이 허브의 상황이었다(2026-09-28): 확장이 안 올라와 세 진단이 전부
        꺼졌다. 계산을 `api/graph_algo.py` 로 옮긴 뒤로는 확장 없이 답해야 한다."""
        from api import fragment as frag
        from api import query as q
        frag.merge_fragment({"owner": "user:a", "nodes": [
            {"id": "concept:a", "type": "concept", "title": "A"},
            {"id": "concept:b", "type": "concept", "title": "B"},
            {"id": "concept:lonely", "type": "concept", "title": "Lonely"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]},
            store=self.store)
        comp = q.component_diagnosis(owner="user:a", store=self.store)
        self.assertTrue(comp["executed"], comp)
        self.assertEqual(comp["orphans"], ["concept:lonely"])
        com = q.communities(owner="user:a", store=self.store)
        self.assertTrue(com["executed"], com)
        self.assertEqual(com["algorithm"], "leiden")
        self.assertEqual([c["members"] for c in com["communities"]],
                         [["concept:a", "concept:b"]])


if __name__ == "__main__":
    unittest.main()
