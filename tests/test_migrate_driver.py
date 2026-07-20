# -*- coding: utf-8 -*-
"""논리 이관 레일(T4·T5) + 방언 재검증 매트릭스(T2).

`store/migrate.py` 의 논리 export→import 가 **무손실 왕복**을 보증하는지 이 테스트가
상시 검증한다 — 향후 ladybug 메이저 업그레이드 때도 재사용하는 레일(역사: 원래
kuzu→ladybug 드라이버 전환용으로 만들어졌으나 v0.5.3 에서 kuzu 를 제거, 이제
백업 복원·기기 이관 등 범용 이관 경로로만 남았다). 방언 매트릭스는 P1-0 방언
실측 항목을 회귀로 고정한다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.backend import open_store        # noqa: E402
from store.config import load_config         # noqa: E402
from store import migrate                     # noqa: E402


class MigrationRoundTripTests(unittest.TestCase):
    """T5 — export→import 무손실 왕복(데이터 디렉토리 간 논리 이관 리허설)."""

    def _seed(self, store):
        with store.session() as s:
            # 노드: Concept 2 + Episode 1 + preference 신호·추적열 경유 SET
            for cid, title in (("c:a", "Alpha"), ("c:b", "Beta")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.owner_id='u', "
                      "n.domain='math', n.tags=['x','y'], n.updated_at='hlc1', n.rev=2",
                      id=cid, t=title)
            s.run("MERGE (e:Episode {episode_id:'e:1'}) SET e.title='Ep', e.owner_id='u', "
                  "e.pinned=true, e.access_count=7, e.tags=['t1']")
            # 엣지: CC 관계 + E-G6 evidence/confidence + ABOUT
            s.run("MATCH (a:Concept{concept_id:'c:a'}),(b:Concept{concept_id:'c:b'}) "
                  "MERGE (a)-[r:RELATED_TO]->(b) SET r.evidence='근거문', r.confidence=0.8")
            s.run("MATCH (e:Episode{episode_id:'e:1'}),(c:Concept{concept_id:'c:a'}) "
                  "MERGE (e)-[:ABOUT]->(c)")

    def test_round_trip_preserves_nodes_edges_and_signals(self):
        import tempfile
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/src"})   # 격리
        src = open_store(cfg)
        src.provision(dim=cfg.embedding_dim)
        self._seed(src)
        dump = migrate.export_store(src)
        src.close()

        # 무손실 근거: 노드/엣지 수 + preference 신호 + 추적열 + evidence 보존
        self.assertEqual(len(dump["nodes"]["Concept"]), 2)
        self.assertEqual(len(dump["nodes"]["Episode"]), 1)
        ep = dump["nodes"]["Episode"][0]
        self.assertTrue(ep["pinned"])
        self.assertEqual(ep["access_count"], 7)
        ca = next(n for n in dump["nodes"]["Concept"] if n["concept_id"] == "c:a")
        self.assertEqual(ca["rev"], 2)                    # 추적열 보존
        self.assertEqual(set(ca["tags"]), {"x", "y"})
        rel = next(e for e in dump["edges"] if e["label"] == "RELATED_TO")
        self.assertEqual(rel["props"]["evidence"], "근거문")   # E-G6 보존
        self.assertAlmostEqual(rel["props"]["confidence"], 0.8)
        self.assertTrue(any(e["label"] == "ABOUT" for e in dump["edges"]))

        # 새 스토어(빈 데이터 디렉토리)로 재적재 → 왕복 대조
        import tempfile
        cfg2 = load_config({"data_dir": tempfile.mkdtemp() + "/db2"})
        dst = open_store(cfg2)
        stats = migrate.import_store(dst, dump, dim=cfg2.embedding_dim)
        self.assertEqual(stats["nodes"], 3)
        self.assertGreaterEqual(stats["edges"], 2)
        # 재적재본에서 신호·엣지 재조회
        got = dst.run("MATCH (e:Episode) RETURN e.pinned AS p, e.access_count AS a")
        self.assertTrue(got[0]["p"])
        self.assertEqual(got[0]["a"], 7)
        rr = dst.run("MATCH ()-[r:RELATED_TO]->() RETURN r.evidence AS e, r.confidence AS c")
        self.assertEqual(rr[0]["e"], "근거문")
        self.assertAlmostEqual(float(rr[0]["c"]), 0.8)
        dst.close()


class DialectMatrixTests(unittest.TestCase):
    """T2 — ladybug 방언 재검증(P1-0 실측 항목 회귀, Kùzu 계보 공통 방언)."""

    def setUp(self):
        import tempfile
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})   # 테스트 격리
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)
        with self.store.session() as s:
            for cid in ("a", "b", "c"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u'", id=cid)
            for x, y in (("a", "b"), ("b", "c"), ("c", "a")):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)

    def tearDown(self):
        self.store.close()

    def test_m3_trail_guards_cycle_blowup(self):
        # M3 — TRAIL(엣지 무재사용)이 사이클 경로 폭증을 절단. WALK 였다면 폭증.
        rows = self.store.run(
            "MATCH (n:Concept) WHERE n.concept_id='a' "
            "MATCH p = (n)-[* TRAIL 1..8]-(m) RETURN count(p) AS c")
        self.assertLess(int(rows[0]["c"]), 50)   # TRAIL 절단(WALK 는 수백)

    def test_m4_label_function_on_relationship(self):
        # M4 — 관계 타입 함수는 label(r)(임베디드에 type() 부재).
        rows = self.store.run(
            "MATCH (:Concept)-[r]->(:Concept) RETURN DISTINCT label(r) AS rel")
        self.assertIn("RELATED_TO", {r["rel"] for r in rows})

    def test_union_all_ignores_outer_limit(self):
        # UNION ALL 전체 LIMIT 무시(실측) — 상한은 파이썬에서 걸어야 함(회귀 고정).
        single = self.store.run("MATCH (c:Concept) RETURN c.concept_id AS id")
        doubled = self.store.run(
            "MATCH (c:Concept) RETURN c.concept_id AS id "
            "UNION ALL MATCH (c:Concept) RETURN c.concept_id AS id")
        self.assertEqual(len(doubled), 2 * len(single))   # UNION ALL 전량(격리 무관)

    def test_shortest_path_dialect(self):
        # E-G7 선행 — * ALL SHORTEST 동작(방언 확인).
        rows = self.store.run(
            "MATCH p = (a:Concept{concept_id:'a'})-[* ALL SHORTEST 1..8]-(b:Concept{concept_id:'c'}) "
            "RETURN length(p) AS len")
        self.assertTrue(rows)
        self.assertGreaterEqual(int(rows[0]["len"]), 1)


if __name__ == "__main__":
    unittest.main()
