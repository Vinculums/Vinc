# -*- coding: utf-8 -*-
"""RAG 코드 리뷰(2026-09-29) 중 **랭킹을 바꾸는** 검색 결함의 회귀 테스트.

항목마다 클래스 하나이고, 클래스 이름에 리뷰 번호(RV-nn)를 단다. 이 셋은 G0 평가를
움직일 수 있어서 랭킹을 안 바꾸는 항목(tests/test_search_review_fixes.py)과 PR 을
따로 둔다.
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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class KindPrefixOutOfIdScanRV11(unittest.TestCase):
    """`concept` 토큰이 모든 Concept id 에 걸려 스캔 상한을 채우고 진짜 매치를 잘랐다."""

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

    def test_concept_drift_still_finds_the_drift_record(self):
        from api import query as q
        from store import pkns
        n = q.NODE_SCAN_CAP + 100
        self.store.run("UNWIND $r AS r CREATE (:Concept {concept_id: r.id, title: r.t, "
                       "owner_id: 'u', domain: ''})",
                       {"r": [{"id": pkns.key("u", f"concept:c{i:05d}"), "t": f"thing {i}"}
                              for i in range(n)]})
        self.store.run("CREATE (:Record {record_id: $id, title: 'Drift', owner_id: 'u', "
                       "domain: ''})", {"id": pkns.key("u", "record:drift")})
        hits = q._node_fallback_search(self.store, "concept drift", owner="u")
        self.assertIn("record:drift", [h["id"] for h in hits])


class GraphArmNeighbourOrderRV18(unittest.TestCase):
    """1-hop 이웃이 id 알파벳순으로 잘려 1위 청크의 이웃이 밀려났다."""

    def test_the_neighbour_of_the_top_chunk_comes_first(self):
        from api import query as q

        class _S:
            def run(self, cypher, params=None):
                return [{"chunk_id": "c1", "id": "concept:a", "title": "a", "domain": "",
                         "nb_id": "concept:zeta", "nb_title": "zeta", "nb_domain": ""},
                        {"chunk_id": "c2", "id": "concept:b", "title": "b", "domain": "",
                         "nb_id": "concept:alpha", "nb_title": "alpha", "nb_domain": ""}]
        out = q._graph_arm_search(_S(), ["c1", "c2"], limit=3)
        self.assertEqual([r["id"] for r in out], ["concept:a", "concept:b", "concept:zeta"])


class KoreanStopwordsRV19(unittest.TestCase):
    """불용어 판정이 조사 절단 뒤라 `무엇인가` 가 `무엇인` 으로 빠져나갔고, 복합 조사는
    끝 글자만 벗겨졌다(`시스템에는` → `시스템에`)."""

    def test_a_stopword_is_dropped_before_its_particle_is_stripped(self):
        from api import query as q
        self.assertEqual(q.normalize_query_tokens("낙관적 동시성 제어는 무엇인가"),
                         ["낙관적", "동시성", "제어"])

    def test_compound_particles_are_stripped_whole(self):
        from api import query as q
        self.assertEqual(q.normalize_query_tokens("시스템에는 api와의 서버에서의 정책과의"),
                         ["시스템", "api", "서버", "정책"])


if __name__ == "__main__":
    unittest.main()
