# -*- coding: utf-8 -*-
"""검색 색인 연산을 어댑터가 낸다 — 색인 목록·FTS 드롭·청크 벡터 거리.

잠그는 것: 목록이 기본 키 색인 없이 (표, 색인) 쌍을 주고, FTS 드롭이 그 목록에서
`Chunk` 를 빼며 두 번 불러도 같고, 청크 거리는 가장 가까운 조각의 코사인 거리이며
**읽기 뷰로도** 같은 값이 나온다(조회 경로는 `_ReaderView` 를 지난다).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class SearchIndexOpsTests(unittest.TestCase):
    def setUp(self):
        env = {"MEMORY_STORE_BACKEND": "embedded",
               "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store"),
               "MEMORY_EMBEDDING_MODEL": "local-en"}
        self._saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        from store.backend import open_store
        from store.config import load_config
        self.cfg = load_config()
        self.store = open_store(self.cfg)
        self.store.provision(dim=self.cfg.embedding_dim)

    def tearDown(self):
        self.store.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _vec(self, *head):
        return list(head) + [0.0] * (self.cfg.embedding_dim - len(head))

    def test_FTS_드롭은_목록에서_Chunk_를_빼고_멱등이다(self):
        self.store.build_search_indexes(dim=self.cfg.embedding_dim)
        before = set(self.store.search_indexes())
        self.assertIn(("Chunk", self.store._FTS_INDEX), before)
        self.assertFalse(any(name == "_PK" for _, name in before))
        self.store.drop_fts_index()
        self.store.drop_fts_index()
        after = set(self.store.search_indexes())
        self.assertEqual(before - after, {("Chunk", self.store._FTS_INDEX)})

    def test_청크_거리는_가장_가까운_조각이고_읽기_뷰도_같다(self):
        s = self.store
        s.run("CREATE (:Chunk {chunk_id:'c1', owner_id:'u', text:'a'}), "
              "(:Chunk {chunk_id:'c2', owner_id:'u', text:'b'}), "
              "(:Chunk {chunk_id:'c3', owner_id:'u', text:'c'})")
        s.run("CREATE (:ChunkVec {vec_id:'v1', chunk_id:'c1', embedding:$a}), "
              "(:ChunkVec {vec_id:'v2', chunk_id:'c1', embedding:$b}), "
              "(:ChunkVec {vec_id:'v3', chunk_id:'c2', embedding:$b})",
              {"a": self._vec(1.0), "b": self._vec(0.0, 1.0)})
        q = self._vec(1.0)
        got = s.chunk_vector_distances(["c1", "c2", "c3"], q)
        self.assertEqual(set(got), {"c1", "c2"})          # 벡터 없는 c3 는 빠진다
        self.assertAlmostEqual(got["c1"], 0.0, places=5)  # 두 조각 중 가까운 쪽
        self.assertAlmostEqual(got["c2"], 1.0, places=5)  # 직교 = 거리 1
        reader = s.reader_view()
        try:
            self.assertEqual(reader.chunk_vector_distances(["c1", "c2", "c3"], q), got)
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
