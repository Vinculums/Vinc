# -*- coding: utf-8 -*-
"""Neo4j 어댑터의 순수 부분 — 드라이버나 서버 없이 도는 것만(`store.neo4j_backend`).

잠그는 것: 타입 붙은 NULL 과 props 키 지우기 식만 바꾸고 다른 문장은 그대로 둔다, Lucene 특수
문자는 낱말로 찾게 이스케이프한다, 버전 하한 비교. 서버가 필요한 동작은 적합성 스위트
(`tests/core/test_store_conformance.py`, `VINC_CONFORMANCE_NEO4J_URI`)가 본다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import neo4j_backend as N  # noqa: E402


class TranslateTests(unittest.TestCase):
    def test_타입_붙은_NULL_은_NULL_이다(self):
        self.assertEqual(N.to_neo4j_cypher("RETURN CAST(NULL AS DOUBLE) AS conf, CAST( NULL AS STRING ) AS ev"),
                         "RETURN NULL AS conf, NULL AS ev")

    def test_props_키_지우기는_지우지_않은_식이_된다(self):
        q = ("WHERE regexp_replace(lower(coalesce(c.props,'')), $props_key, ' ', 'g') CONTAINS $q0 "
             "OR lower(coalesce(c.title,'')) CONTAINS $q0")
        self.assertEqual(N.to_neo4j_cypher(q),
                         "WHERE lower(coalesce(c.props,'')) CONTAINS $q0 "
                         "OR lower(coalesce(c.title,'')) CONTAINS $q0")

    def test_다른_문장은_그대로다(self):
        q = "MATCH (n:Concept) WHERE n.concept_id STARTS WITH $p RETURN CAST(n.rev AS STRING) AS r"
        self.assertEqual(N.to_neo4j_cypher(q), q)


class LuceneTests(unittest.TestCase):
    def test_특수_문자는_낱말로_찾는다(self):
        self.assertEqual(N.lucene_terms("c++ a:b (x)"), r"c\+\+ a\:b \(x\)")

    def test_빈_질의는_빈_문자열이다(self):
        self.assertEqual(N.lucene_terms("   "), "")


class VersionTests(unittest.TestCase):
    def test_하한_비교(self):
        self.assertGreaterEqual(N._version_tuple("2026.01.4"), N.VERSION_FLOOR)
        self.assertLess(N._version_tuple("5.26.0"), N.VERSION_FLOOR)
        self.assertEqual(N._version_tuple("garbage"), (0, 0))


if __name__ == "__main__":
    unittest.main()
