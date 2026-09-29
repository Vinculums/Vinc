# -*- coding: utf-8 -*-
"""RAG 코드 리뷰(2026-09-29) 검색·스토어 결함의 회귀 테스트.

항목마다 클래스 하나이고, 클래스 이름에 리뷰 번호(RV-nn)를 단다. 스토어 쪽 결함은
**실엔진**에서 재현한다 — 필터와 kNN 은 스텁으로는 안 잡힌다(`_ChunkSpy` 는
필터를 기록만 하고 적용하지 않아서 RV-01 을 놓쳤다).
근거: [[vinc.audit.rag-code-review-2026-09-29]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class _EngineCase(unittest.TestCase):
    """임시 디렉터리에 실엔진 스토어를 연다(dim=4). 검색 확장이 없으면 skip."""

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

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def chunks(self, rows):
        """rows: (chunk_id, doc_id, owner, text, embedding). 청크와 그 벡터 하나를 심는다."""
        data = [{"c": c, "d": d, "o": o, "t": t, "e": e} for c, d, o, t, e in rows]
        self.store.run("UNWIND $r AS r CREATE (:Chunk {chunk_id: r.c, doc_id: r.d, "
                       "owner_id: r.o, text: r.t, layer: 'doc'})", {"r": data})
        self.store.run("UNWIND $r AS r CREATE (:ChunkVec {vec_id: r.c + '#000', "
                       "chunk_id: r.c, doc_id: r.d, owner_id: r.o, seq: 0, "
                       "embedding: r.e})", {"r": data})

    def build(self):
        try:
            self.store.build_search_indexes(dim=4)
        except RuntimeError as exc:
            self.skipTest(f"검색 확장 미가용({self.store.driver_name}): {exc}")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DocAndLayerFiltersRV01(_EngineCase):
    """`read_search` 가 보내는 `layer`·`doc_id` 가 조용히 버려져 다른 문서가 섞였다."""

    def setUp(self):
        super().setUp()
        self.chunks([("a#00000", "a", "u", "refund policy", [1.0, 0.0, 0.0, 0.0]),
                     ("b#00000", "b", "u", "refund dispute", [0.9, 0.1, 0.0, 0.0])])
        self.build()

    def test_doc_filter_excludes_other_documents(self):
        f = {"owner_id": "u", "doc_id": "a"}
        fts = self.store.fulltext_search("refund", 5, f)
        vec = self.store.vector_search([1.0, 0.0, 0.0, 0.0], 5, f)
        self.assertEqual([r["id"] for r in fts], ["a#00000"], fts)
        self.assertEqual([r["id"] for r in vec], ["a#00000"], vec)

    def test_layer_filter_is_applied(self):
        self.assertEqual(self.store.fulltext_search("refund", 5, {"layer": "plan"}), [])

    def test_an_unknown_filter_key_is_refused(self):
        """모르는 키는 버리지 않고 던진다(fail-closed). Chunk 에 없는 `domain` 도 그렇다."""
        with self.assertRaises(ValueError):
            self.store.fulltext_search("refund", 5, {"domain": "x"})
        with self.assertRaises(ValueError):
            self.store.vector_search([1.0, 0.0, 0.0, 0.0], 5, {"tenant_id": "t"})


if __name__ == "__main__":
    unittest.main()
