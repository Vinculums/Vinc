# -*- coding: utf-8 -*-
"""복원 리허설의 RAG 센서스 — 문서·청크·벡터가 어긋난 수를 제대로 세는가.

일부러 망가뜨린 스토어에서 센다: 벡터 구멍(needs_embed=0 인데 ChunkVec 이 빠짐),
청크 수 불일치, 메타 없는 청크, 소유자가 다른 청크. 근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from api import docstore as ds  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

import restore_rehearsal_check as rr  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class CensusE2E(unittest.TestCase):
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

    def _embedded_doc(self, text):
        d = ds.put_doc(self.store, owner="user:a", text=text, title=text[:8])
        cids = [r["id"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id", {"d": d["doc_id"]})]
        ds.apply_embeddings(self.store, d["doc_id"], {c: [1.0, 0, 0, 0] for c in cids})
        return d["doc_id"], cids

    def test_깨끗한_스토어는_전부_0_이다(self):
        self._embedded_doc("alpha body")
        got = rr.rag_census(self.store)
        self.assertEqual((got["docs"], got["docs_pending_embed"]), (1, 0))
        for key in ("docs_embedded_with_vector_holes", "docs_chunk_count_mismatch_or_tail",
                    "chunks_without_docmeta", "chunks_owner_differs_from_doc",
                    "chunk_vectors_without_chunk", "docs_with_empty_owner",
                    "doc_ids_shared_by_two_owners"):
            self.assertEqual(got[key], 0, key)

    def test_망가뜨린_만큼_센다(self):
        doc, cids = self._embedded_doc("alpha body")
        # 벡터 구멍: 임베딩 끝남(needs_embed=0)인데 청크 벡터 하나가 없다.
        self.store.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $c DETACH DELETE v", {"c": cids[0]})
        # 청크 수 불일치: 메타가 더 적다고 말한다.
        self.store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d SET d.chunk_count = 0", {"d": doc})
        # 소유자가 문서와 다른 청크.
        self.store.run("MATCH (c:Chunk) WHERE c.chunk_id = $c SET c.owner_id = 'user:b'",
                       {"c": cids[0]})
        got = rr.rag_census(self.store)
        self.assertEqual(got["docs_embedded_with_vector_holes"], 1)
        self.assertEqual(got["docs_chunk_count_mismatch_or_tail"], 1)
        self.assertEqual(got["chunks_owner_differs_from_doc"], 1)
        self.assertEqual(got["doc_ids_shared_by_two_owners"], 1)


if __name__ == "__main__":
    unittest.main()
