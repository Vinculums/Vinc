# -*- coding: utf-8 -*-
"""저장 벡터 런타임 도장 캐시(VIN-178) — 검색마다 ChunkVec·NodeVec 을 훑지 않고, 벡터가 바뀌면 다시 훑는다.

잠그는 것:
  1. 같은 소유자의 두 번째 검색은(요청마다 새 리더 뷰여도) 도장을 다시 훑지 않는다.
  2. 다른 런타임의 벡터가 들어오면 바로 다음 검색이 불일치를 말하고, 그 벡터를 지우면 경고가 걷힌다.
  3. 세대를 올리는 것은 벡터 표를 바꾼 문장뿐이다(읽기는 안 올린다 — 올리면 캐시가 매번 빗나간다).
  4. 스토어 하나가 기억하는 소유자 수에는 상한이 있다.
  5. 캐시는 소유자별이다: 한 소유자의 답이 다른 소유자(또는 무스코프)에게 나가지 않는다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds  # noqa: E402
from api import query as q  # noqa: E402
from store import embedded_backend as eb  # noqa: E402

_DRIVER, _NAME = eb._import_driver()
_SCAN = "RETURN DISTINCT v.encoder_runtime"
_EMB = [1.0, 0.0, 0.0, 0.0]


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class StampCacheE2E(unittest.TestCase):
    OWNER = "user:stamp-cache"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        # 모든 커넥션(라이터·리더)이 지나는 한 점에서 도장 훑기 문장을 센다.
        self.scans = 0
        real = eb._Conn.execute

        def counting(conn, query, parameters=None):
            if isinstance(query, str) and _SCAN in query:
                self.scans += 1
            return real(conn, query, parameters)
        patcher = mock.patch.object(eb._Conn, "execute", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _doc(self, text: str, runtime: str) -> str:
        d = ds.put_doc(self.store, owner=self.OWNER, text=text, title=text[:12])
        cids = [r["id"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id", {"d": d["doc_id"]})]
        ds.apply_embeddings(self.store, d["doc_id"], {c: _EMB for c in cids},
                            encoder_runtime=runtime)
        return d["doc_id"]

    def _search(self, runtime: str) -> dict:
        """허브 모양 — 요청마다 새 리더 뷰로 묻는다."""
        view = self.store.reader_view()
        try:
            return q.search("alpha", query_embedding=_EMB, owner=self.OWNER,
                            query_runtime=runtime, store=view)
        finally:
            view.close()

    def test_second_search_does_not_rescan_the_stamps(self):
        self._doc("alpha body text", "rt-a")
        first = self._search("rt-a")
        self.assertNotIn("encoder_runtime_warning", first)
        self.assertEqual(self.scans, 2)          # 두 표를 한 번씩
        second = self._search("rt-a")
        self.assertNotIn("encoder_runtime_warning", second)
        self.assertEqual(self.scans, 2)          # 캐시가 답했다

    def test_a_mixed_stamp_write_shows_on_the_next_search_and_a_delete_clears_it(self):
        self._doc("alpha body text", "rt-a")
        self.assertNotIn("encoder_runtime_warning", self._search("rt-a"))
        other = self._doc("alpha other body", "rt-b")  # 다른 런타임의 벡터가 들어왔다
        res = self._search("rt-a")
        self.assertTrue(res.get("encoder_runtime_warning"))
        self.assertIn("mismatch", res["vector_note"])
        self.assertIn("rt-b", res["vector_note"])
        self.assertNotIn("error", res)           # REST v1 은 참인 error 를 500 으로 바꾼다
        ds.delete_doc(self.store, other, owner=self.OWNER)
        self.assertNotIn("encoder_runtime_warning", self._search("rt-a"))

    def test_only_statements_that_change_a_vector_table_move_the_generation(self):
        self._doc("alpha body text", "rt-a")
        reads = ("MATCH (v:ChunkVec) WHERE v.owner_id = $o RETURN DISTINCT v.encoder_runtime AS r",
                 "MATCH (n:NodeVec) WHERE n.owner_id = $o RETURN count(n) AS c")
        for stmt in reads:
            gen = eb.vector_generation()
            self.store.run(stmt, {"o": self.OWNER})
            self.assertEqual(eb.vector_generation(), gen, stmt)
        # 소유자 접기(`api.owner_fold`)와 purge 의 모양 — 쓰기 경로에 손대지 않아도 잡힌다.
        writes = ("MATCH (n:ChunkVec) WHERE n.owner_id = $o SET n.owner_id = $o",
                  "MATCH (n:NodeVec) WHERE n.owner_id = $o DETACH DELETE n")
        for stmt in writes:
            gen = eb.vector_generation()
            self.store.run(stmt, {"o": self.OWNER})
            self.assertGreater(eb.vector_generation(), gen, stmt)


class StampCacheBound(unittest.TestCase):
    def test_one_store_remembers_a_bounded_number_of_owners(self):
        class _Store:
            runtime_stamps: dict = {}

            def run(self, *_a, **_k):
                return [{"runtime": "rt-a"}]
        store = _Store()
        for i in range(q._RUNTIME_STAMP_OWNERS + 10):
            self.assertEqual(q._stored_runtimes(store, f"user:{i}"), {"rt-a"})
        self.assertLessEqual(len(store.runtime_stamps), q._RUNTIME_STAMP_OWNERS)

    def test_one_owner_never_gets_another_owners_cached_stamps(self):
        class _Store:
            runtime_stamps: dict = {}

            def run(self, _query, params=None):
                return [{"runtime": f"rt-{(params or {}).get('owner', 'all')}"}]
        store = _Store()
        self.assertEqual(q._stored_runtimes(store, "user:a"), {"rt-user:a"})
        self.assertEqual(q._stored_runtimes(store, "user:b"), {"rt-user:b"})
        self.assertEqual(q._stored_runtimes(store, None), {"rt-all"})
        self.assertEqual(q._stored_runtimes(store, "user:a"), {"rt-user:a"})   # 캐시 적중


if __name__ == "__main__":
    unittest.main()
