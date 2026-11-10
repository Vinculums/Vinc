# -*- coding: utf-8 -*-
"""엔진 방언 게이트(gate_engine_dialect)의 판정 시험.

잠그는 것: 질의 문자열 안의 엔진 전용 구문만 세고 주석·독스트링은 세지 않는다,
허용 목록보다 늘면 떨어지고 줄었는데 목록이 그대로여도 떨어진다, 그리고 지금 리포는
커밋된 허용 목록과 정확히 맞는다.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import gate_engine_dialect as G               # noqa: E402


def families(src: str) -> list[str]:
    return sorted(f for f, _line, _frag in G.scan_source(src))


class ScanTests(unittest.TestCase):
    def test_질의_문자열의_전용_구문을_계열별로_센다(self):
        src = (
            'q = "MATCH (n)-[* SHORTEST 1..3]-(m) RETURN label(n)"\n'
            'store.run("CALL QUERY_FTS_INDEX(\'Chunk\', \'idx\', $q)")\n'
            'store.run("CHECKPOINT")\n'
            'store.run("CALL show_tables() RETURN *")\n'
            'store.run("ALTER TABLE Concept ADD x STRING")\n'
        )
        self.assertEqual(families(src),
                         ["catalog", "engine_ops", "fts", "functions", "schema", "traversal"])

    def test_f_string_의_고정_조각도_센다(self):
        src = 'd = 2\nq = f"MATCH (n)-[* TRAIL 1..{d}]-(m) RETURN n"\n'
        self.assertEqual(families(src), ["traversal"])

    def test_주석과_독스트링은_세지_않는다(self):
        src = (
            '"""모듈 설명: SHORTEST 와 CHECKPOINT 를 쓴다."""\n'
            'def f():\n'
            '    """label(r) 을 쓴다."""\n'
            '    # MATCH (n)-[* SHORTEST 1..2]-(m)\n'
            '    return "MATCH (n) RETURN n.title"\n'
        )
        self.assertEqual(families(src), [])

    def test_줄_번호는_여러_줄_문자열_안의_위치다(self):
        src = 'q = (\n    "MATCH (n)\\n"\n)\nr = """\nMATCH (a)\nRETURN label(a)\n"""\n'
        hits = G.scan_source(src)
        self.assertEqual([(f, line) for f, line, _ in hits], [("functions", 6)])


class CompareTests(unittest.TestCase):
    ALLOWED = {"api/query.py": {"traversal": 2}}

    def test_늘면_떨어진다(self):
        grew, shrank = G.compare({"api/query.py": {"traversal": 3}}, self.ALLOWED)
        self.assertEqual((grew, shrank), (["api/query.py [traversal] 2 -> 3"], []))

    def test_새_파일도_늘어난_것이다(self):
        grew, _ = G.compare({"api/query.py": {"traversal": 2}, "api/new.py": {"fts": 1}},
                            self.ALLOWED)
        self.assertEqual(grew, ["api/new.py [fts] 0 -> 1"])

    def test_줄었는데_목록이_그대로면_알린다(self):
        grew, shrank = G.compare({"api/query.py": {"traversal": 1}}, self.ALLOWED)
        self.assertEqual((grew, shrank), ([], ["api/query.py [traversal] 2 -> 1"]))


class RepoTests(unittest.TestCase):
    def test_리포는_허용_목록과_정확히_맞는다(self):
        current = G.counts_of(G.scan_repo())
        grew, shrank = G.compare(current, G.load_allowlist())
        self.assertEqual((grew, shrank), ([], []))

    def test_어댑터는_세지_않는다(self):
        self.assertTrue(G.ADAPTERS.isdisjoint(G.load_allowlist()))
        self.assertTrue(G.EMBEDDED_TOOLS.isdisjoint(G.load_allowlist()))
        # 임베디드 전용 도구는 scripts 아래에만 있다(제품 경로를 빼는 구멍이 되지 않게).
        self.assertTrue(all(p.startswith("scripts/") for p in G.EMBEDDED_TOOLS))


if __name__ == "__main__":
    unittest.main()
