# -*- coding: utf-8 -*-
"""색인 드롭의 멱등은 '색인 없음' 하나만 흡수한다(RV-34).

드롭이 다른 이유로 실패하면 색인은 걸린 채로 남는다. 그걸 '없음'으로 읽고 넘어가면 부르는
쪽이 벡터를 넣고, 그게 세그폴트 자리다(`drop_vector_index` 주석의 실측). 실패는 실엔진으로
만든다: 다른 커넥션이 쓰기 트랜잭션을 쥐고 있으면 엔진이 드롭을 거절한다.
근거: [[vinc.audit.rag-code-review-2026-09-29]] (RV-34)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import embedded_backend as eb  # noqa: E402

_DRIVER, _NAME = eb.import_driver()
DIM = 4


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class IndexDropTests(unittest.TestCase):
    def setUp(self):
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
        for i in range(3):
            e = [1.0, 2.0, 3.0, float(i)]
            s.run("CREATE (:ChunkVec {vec_id: $v, chunk_id: 'c', doc_id: 'd', owner_id: 'o', "
                  "seq: $i, embedding: $e})", {"v": f"c#{i}", "i": i, "e": e})
            s.run("CREATE (:NodeVec {vec_id: $v, node_id: $v, kind: 'concept', owner_id: 'o', "
                  "title: 't', text: 't', model: 'm', embedding: $e})",
                  {"v": f"concept:n{i}", "e": e})
        s.ensure_search_indexes(dim=DIM)
        self.assertEqual(s.create_node_vector_index(), "")

    def tearDown(self):
        self.store.close()

    @contextmanager
    def _write_txn_held(self):
        other = eb._open_connection(_DRIVER, self.store._db)
        other.execute("BEGIN TRANSACTION")
        other.execute("CREATE (:Concept {concept_id: 'concept:hold', owner_id: 'o'})")
        try:
            yield
        finally:
            other.execute("ROLLBACK")
            other.close()

    def _has(self, table, name):
        return self.store._has_index(table, name)

    def test_an_absent_index_drops_quietly(self):
        s = self.store
        s.drop_search_indexes()
        s.drop_node_vector_index()
        s._vec_index_dropped = False            # 접기를 풀어 엔진에 다시 보낸다
        s.drop_search_indexes()                 # 셋 다 없다 — 던지지 않는다
        s.drop_node_vector_index()
        s.rebuild_fts_index()                   # 없는 FTS 는 그냥 만든다
        self.assertFalse(self._has("ChunkVec", s._VEC_INDEX))
        self.assertTrue(s._vec_index_dropped)

    def test_a_vector_drop_that_fails_otherwise_raises_and_is_not_folded(self):
        s = self.store
        with self._write_txn_held():
            with self.assertRaises(RuntimeError):
                s.drop_vector_index()
        self.assertTrue(self._has("ChunkVec", s._VEC_INDEX))
        self.assertFalse(s._vec_index_dropped, "실패한 드롭이 다음 드롭을 접게 했다")
        s.drop_vector_index()                   # 막힌 게 풀리면 제대로 드롭된다
        self.assertFalse(self._has("ChunkVec", s._VEC_INDEX))

    def test_a_node_vector_drop_that_fails_otherwise_raises(self):
        s = self.store
        with self._write_txn_held():
            with self.assertRaises(RuntimeError):
                s.drop_node_vector_index()
        self.assertTrue(self._has("NodeVec", s._NODE_VEC_INDEX))

    def test_an_fts_drop_that_fails_otherwise_raises(self):
        s = self.store
        s.drop_vector_index()                   # 벡터 쪽은 접혀서 FTS 드롭까지 간다
        with self._write_txn_held():
            with self.assertRaises(RuntimeError):
                s.drop_search_indexes()
        self.assertTrue(self._has("Chunk", s._FTS_INDEX))

    def test_the_document_write_path_fails_instead_of_inserting_vectors(self):
        from api import docstore
        with self._write_txn_held():
            with self.assertRaises(RuntimeError):
                docstore._drop_vector_index_if_any(self.store)
        self.assertTrue(self._has("ChunkVec", self.store._VEC_INDEX))

    @contextmanager
    def _vector_extension_unavailable(self):
        s = self.store
        s._exts.discard("vector")
        s._ext_failed["vector"] = "임베디드 vector 확장 미가용(test) — 번들 없음"
        try:
            yield
        finally:
            s._ext_failed.pop("vector")
            s._exts.add("vector")

    def test_the_document_write_path_still_passes_without_the_extension(self):
        """확장이 아예 없는 스토어의 정본 쓰기는 막지 않는다(`_drop_vector_index_if_any`)."""
        from api import docstore
        with self._vector_extension_unavailable():
            docstore._drop_vector_index_if_any(self.store)   # 던지지 않는다

    def test_the_v17_rekey_migration_survives_a_node_drop_that_raises(self):
        """재키잉 이행은 미러에 행을 넣지 않는다. 드롭이 던져도 기동(migrate)은 선다."""
        s = self.store
        s.drop_node_vector_index()               # 표를 버리기 전에 색인은 치워 둔다
        s.run("MERGE (m:_SchemaMeta {key:'schema_version'}) SET m.value = '17'")
        with self._vector_extension_unavailable():
            s.migrate(dim=DIM)                   # 던지지 않는다
        self.assertTrue(s._node_vec_rekeyed())


if __name__ == "__main__":
    unittest.main()
