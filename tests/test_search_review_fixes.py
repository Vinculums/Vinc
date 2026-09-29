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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class OwnerBeforeKnnRV05(_EngineCase):
    """HNSW 전역 상위 k 를 먼저 뽑고 owner 를 뒤에 걸어서, 작은 스페이스가 0건이 됐다."""

    def test_a_small_owner_still_fills_top_k_behind_a_closer_large_owner(self):
        rows = [(f"a{i}#00000", "da", "A", "alpha", [1.0, 0.01 * (i % 7), 0.0, 0.0])
                for i in range(500)]
        rows += [(f"b{i}#00000", "db", "B", "beta", [0.2, 1.0, 0.1 * (i % 3), 0.0])
                 for i in range(20)]
        self.chunks(rows)
        self.build()
        got = self.store.vector_search([1.0, 0.0, 0.0, 0.0], 5, {"owner_id": "B"})
        self.assertEqual(len(got), 5, got)
        self.assertTrue(all(r["id"].startswith("b") for r in got), got)
        d = [r["distance"] for r in got]
        self.assertEqual(d, sorted(d), "가까운 것부터가 아니다")

    def test_every_owner_finds_its_own_mirror_row_at_distance_zero(self):
        owners = [f"user:o{i}" for i in range(8)]
        self.store.drop_node_vector_index()
        self.store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {vec_id: r.o + '#concept:x', "
            "node_id: 'concept:x', kind: 'concept', owner_id: r.o, title: 'x', "
            "text: 'x', model: 'test', embedding: [1.0, 0.0, 0.0, 0.0]})",
            {"rows": [{"o": o} for o in owners]})
        if self.store.create_node_vector_index():
            self.skipTest("벡터 확장 미가용")
        empty = [o for o in owners
                 if not self.store.node_vector_search([1.0, 0.0, 0.0, 0.0], 1, owner=o)]
        self.assertEqual(empty, [], "자기 행을 거리 0 에서 못 찾은 소유자가 있다")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DimensionMismatchRV06(_EngineCase):
    """질의 벡터 폭이 저장 열과 다르면 벡터 팔이 던지고, 이미 구한 키워드 결과까지 버려졌다."""

    def test_a_query_vector_of_another_width_still_returns_the_keyword_rows(self):
        from api import query as q
        self.chunks([("a#00000", "a", "u", "refund policy", [1.0, 0.0, 0.0, 0.0])])
        self.build()
        res = q.search("refund", query_embedding=[1.0, 0.0], owner="u", store=self.store)
        self.assertFalse(res.get("error"), res)
        self.assertEqual([r["id"] for r in res["rows"]], ["a#00000"])
        self.assertEqual(res["arms"], "fts")
        self.assertTrue(str(res.get("vector_note")).startswith(q._VECTOR_WIDTH_MISMATCH), res)
        self.assertNotIn("vector_rebuilding", res, "폭 불일치는 재빌드 창이 아니다")

    def test_any_other_vector_failure_also_keeps_the_keyword_rows(self):
        from api import query as q

        class _Broken:
            scoped_chunk_search = True

            def run(self, cypher, params=None):
                return []

            def fulltext_search(self, text, k, filters=None):
                return [{"id": "a#00000", "text": "refund policy", "score": 1.0}]

            def vector_search(self, e, k, filters=None):
                raise RuntimeError("IO exception: could not read the vector index")
        res = q.search("refund", query_embedding=[1.0], owner="u", store=_Broken())
        self.assertFalse(res.get("error"), res)
        self.assertEqual([r["id"] for r in res["rows"]], ["a#00000"])
        self.assertIn("could not read the vector index", str(res.get("vector_note")))
        self.assertNotIn("vector_rebuilding", res)


class _PagedStub:
    """청크 팔만 있는 스토어 더블. ``fail`` 이면 FTS 가 IO 오류를 낸다."""

    scoped_chunk_search = True

    def __init__(self):
        self.fail = False

    def run(self, cypher, params=None):
        return []

    def fulltext_search(self, text, k, filters=None):
        if self.fail:
            raise OSError("IO exception: could not read the index file")
        return [{"id": f"d#{i:05d}", "text": "t", "score": 3.0 - i} for i in range(3)][:k]


class ContinuationKeepsTheRealErrorRV16(unittest.TestCase):
    """이어 읽기 페이지에서 청크 팔이 실패하면 빈 풀 때문에 `stale_cursor` 로 덮였다."""

    def test_a_failing_second_page_reports_the_failure_not_a_stale_cursor(self):
        from api import query as q
        s = _PagedStub()
        first = q.search("t", top_k=1, store=s)
        self.assertTrue(first["next_cursor"])
        s.fail = True
        second = q.search("t", top_k=1, store=s, cursor=first["next_cursor"])
        self.assertIn("could not read the index file", str(second.get("error")), second)
        self.assertFalse(str(second.get("error")).startswith("stale_cursor"), second)


class CappedPoolRV20(unittest.TestCase):
    """관례 청크를 걸러 풀이 상한 아래로 줄면 `total_count` 가 전부처럼 읽혔다."""

    def test_the_cap_is_marked_when_the_raw_arm_reached_it(self):
        from api import graphmd
        from api import query as q

        class _S(_PagedStub):
            def fulltext_search(self, text, k, filters=None):
                conv = graphmd.conventions_doc_id(None)
                rows = [{"id": f"{conv}#{i:05d}", "text": "c", "score": 9.0} for i in range(3)]
                rows += [{"id": f"d{i}#00000", "text": "t", "score": 1.0} for i in range(k)]
                return rows[:k]
        res = q.search("t", top_k=5, store=_S())
        self.assertEqual(res["total_count"], q.SEARCH_POOL_CAP - 3)
        self.assertIs(res.get("total_count_capped"), True)


class CursorBoundToArmsRV21(unittest.TestCase):
    """1페이지는 두 팔 융합, 2페이지는 키워드 단독이어도 eid 하나가 맞으면 커서가 통과했다."""

    def test_a_page_served_by_other_arms_is_stale(self):
        from api import query as q

        class _S(_PagedStub):
            vec_ok = True

            def vector_search(self, e, k, filters=None):
                if not self.vec_ok:
                    raise RuntimeError("Table ChunkVec doesn't have an index with name chunk_vec")
                return [{"id": f"d#{i:05d}", "text": "t", "distance": 0.1 * i} for i in range(3)]
        s = _S()
        first = q.search("t", top_k=1, store=s, query_embedding=[1.0])
        self.assertEqual(first["arms"], "fts+vector")
        s.vec_ok = False
        second = q.search("t", top_k=1, store=s, query_embedding=[1.0],
                          cursor=first["next_cursor"])
        self.assertTrue(str(second.get("error")).startswith("stale_cursor"), second)


if __name__ == "__main__":
    unittest.main()
