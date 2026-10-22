# -*- coding: utf-8 -*-
"""색인 유지 모드(VIN-236): 적용이 드롭을 건너뛰면 벡터 질의가 반드시 배타로 돈다.

둘은 한 쌍이다. 드롭만 빼고 질의를 그대로 두면 2026-09-30 실측의 세그폴트 자리다
(쓰는 동안 다른 커넥션의 벡터 질의 14/20 사망). 그래서 플래그 하나가 둘을 같이 바꾸고,
측정하지 않은 엔진에서는 플래그가 켜져도 아무것도 바꾸지 않는다.
근거: [[decision:keep-the-chunk-vector-index-behind-a-flag-and-run-each-vector-query-alone-2026-10-03]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import embedded_backend as eb  # noqa: E402

_DRIVER, _NAME = eb._import_driver()
DIM = 4


def _engine() -> str:
    return str(getattr(_DRIVER, "__version__", "") or "")


class FlagTests(unittest.TestCase):
    def tearDown(self):
        eb._keep_vec_engine_ok.cache_clear()

    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {"VINC_KEEP_VECTOR_INDEX": ""}):
            self.assertFalse(eb.keep_vector_index())

    def test_an_unmeasured_engine_keeps_dropping(self):
        with mock.patch.dict(os.environ, {"VINC_KEEP_VECTOR_INDEX": "on"}), \
                mock.patch.object(eb, "_KEEP_VEC_INDEX_ENGINES", ("0.0.0-never",)):
            self.assertFalse(eb.keep_vector_index())


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class KeepIndexTests(unittest.TestCase):
    def setUp(self):
        from api import docstore as ds
        from store.backend import open_store
        from store.config import load_config
        s = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "s")}))
        self.assertIsNotNone(s, eb.EmbeddedBackend.last_open_error)
        self.store = s
        s.provision(dim=DIM)
        try:
            s._load_ext("vector")
            s._load_ext("fts")
        except RuntimeError as exc:
            self.skipTest(f"검색 확장 미가용: {str(exc)[:80]}")
        self.ds = ds
        self._put("d0", [1.0, 0.0, 0.0, 0.0])
        s.ensure_search_indexes(dim=DIM)
        eb._keep_vec_engine_ok.cache_clear()
        patches = [mock.patch.dict(os.environ, {"VINC_KEEP_VECTOR_INDEX": "on"}),
                   mock.patch.object(eb, "_KEEP_VEC_INDEX_ENGINES", (_engine(),))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(eb._keep_vec_engine_ok.cache_clear)

    def tearDown(self):
        self.store.close()

    def _put(self, doc, vec):
        r = self.ds.put_doc(self.store, owner="o", text=f"{doc} body", doc_id=doc,
                            title=doc, link=False)
        self.assertTrue(r.get("executed"), r)
        cid = self.store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id",
                             {"d": doc})[0]["id"]
        r = self.ds.apply_embeddings(self.store, doc, {cid: [vec]})
        self.assertTrue(r.get("executed"), r)
        return cid

    def test_an_apply_keeps_the_index_and_the_new_vector_answers(self):
        s = self.store
        self.assertTrue(eb.keep_vector_index())
        cid = self._put("d1", [0.0, 1.0, 0.0, 0.0])
        self.assertFalse(s._vec_index_dropped)
        self.assertTrue(s._has_index("ChunkVec", s._VEC_INDEX))
        self.assertEqual(s.vector_search([0.0, 1.0, 0.0, 0.0], 1)[0]["id"], cid)

    def test_every_vector_query_runs_alone(self):
        s = self.store
        seen = []
        real = eb._exec_to_dicts

        def spy(conn, cypher, params, **kw):
            if "QUERY_VECTOR_INDEX" in cypher:
                seen.append(eb.DDL_GUARD._owner == threading.get_ident())
            return real(conn, cypher, params, **kw)

        rv = s.reader_view()
        try:
            # 노드 쪽은 작은 미러면 색인을 안 쓴다 — 색인 질의가 혼자 도는지를 보려면 HNSW 로 고정한다.
            with mock.patch.object(eb, "_exec_to_dicts", spy), \
                    mock.patch.object(eb, "_NODE_VEC_EXACT_MAX_ROWS", 0):
                rv.vector_search([1.0, 0.0, 0.0, 0.0], 1)
                with self.assertRaisesRegex(RuntimeError, "^node_vector_query_failed$"):
                    rv.node_vector_search([1.0, 0.0, 0.0, 0.0], 1, owner="o")
        finally:
            rv.close()
        self.assertEqual(seen, [True, True])


if __name__ == "__main__":
    unittest.main()
