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


class RebuildPathTests(unittest.TestCase):
    """M3R — 차원이 바뀌는 모델 교체의 재구축 경로(`migrate.rebuild_store`).

    🔴 **여기서 지키는 값들은 사라져도 오류가 안 난다.** `_DocMeta`·`content_hash`·
    `sync_seq`·삭제 표식은 없으면 그냥 "없는 상태"로 조용히 동작한다. 그래서 이
    테스트가 유일한 방어다 — 리허설에서 실제로 물린 모양 그대로 못 박는다
    (문서 10건이 왕복 뒤 0건이 되고 재큐가 0건이었다. 예외는 하나도 없었다).
    """

    def _mkstore(self, dim: int | None = None):
        import tempfile
        over = {"data_dir": tempfile.mkdtemp() + "/db"}
        if dim == 1024:
            over["embedding_model"] = "BAAI/bge-m3"       # 차원이 바뀌는 쪽
        cfg = load_config(over)
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        return st, cfg

    def _seed_docs(self, store, n: int):
        """문서 n건을 **프로덕션 경로로** 넣는다 — 손으로 MERGE 하면 `_DocMeta` 가
        어떻게 채워지는지를 테스트가 스스로 정해 버려 회귀 가치가 없다."""
        from api import docstore as ds
        for i in range(n):
            ds.put_doc(store, owner="u", doc_id=f"doc:{i}", title=f"T{i}",
                       text=f"재구축 리허설 문서 {i}\n\nrebuild rehearsal body {i}",
                       link=False)
        ds.switch_embed_model(store, "intfloat/multilingual-e5-base")

    def test_왕복이_문서_정체성과_커서를_보존한다(self):
        src, _ = self._mkstore()
        self._seed_docs(src, 3)
        # 동기화 커서·삭제 표식을 사람이 만든 값으로 심는다(왕복에서 살아남아야 한다).
        src.run("MATCH (d:_DocMeta) WHERE d.doc_id='doc:0' SET d.sync_seq = 41")
        src.run("MERGE (t:_DocTombstone {doc_id:'doc:gone'}) SET t.owner_id='u', "
                "t.deleted_at='hlc9', t.sync_seq=40")
        src.run("MERGE (m:_SyncMeta {key:'cursor:u'}) SET m.value='42'")
        before = src.run("MATCH (d:_DocMeta) WHERE d.doc_id='doc:0' "
                         "RETURN d.content_hash AS h, d.sync_seq AS s")[0]
        self.assertTrue(before["h"], "seed 가 content_hash 를 안 남겼다 — 전제 붕괴")

        dump = migrate.export_store(src)
        src.close()
        dst, cfg2 = self._mkstore()
        migrate.import_store(dst, dump, dim=cfg2.embedding_dim)

        got = dst.run("MATCH (d:_DocMeta) WHERE d.doc_id='doc:0' "
                      "RETURN d.content_hash AS h, d.sync_seq AS s, d.title AS t")[0]
        self.assertEqual(got["h"], before["h"], "content_hash 유실 — 변경감지가 죽는다")
        self.assertEqual(int(got["s"]), 41, "sync_seq 유실 — 동기화 커서가 되감긴다")
        self.assertEqual(got["t"], "T0")
        tomb = dst.run("MATCH (t:_DocTombstone) RETURN count(t) AS c")[0]["c"]
        self.assertEqual(int(tomb), 1, "삭제 표식 유실 — 다음 동기화에서 부활한다")
        cur = dst.run("MATCH (m:_SyncMeta) WHERE m.key='cursor:u' RETURN m.value AS v")
        self.assertEqual(cur[0]["v"], "42", "_SyncMeta 커서 유실")
        dst.close()

    def test_스키마_버전은_덤프가_아니라_대상의_사실이다(self):
        """구 덤프를 신 스토어에 부으면 버전이 되감긴다. 그건 대상의 사실이 아니다."""
        from store import embedded_schema as es
        src, _ = self._mkstore()
        dump = migrate.export_store(src)
        src.close()
        dump["meta"]["_SchemaMeta"] = [{"key": "schema_version", "value": "3"}]
        dst, cfg2 = self._mkstore()
        migrate.import_store(dst, dump, dim=cfg2.embedding_dim)
        v = dst.run("MATCH (m:_SchemaMeta) WHERE m.key='schema_version' "
                    "RETURN m.value AS v")
        self.assertEqual(int(v[0]["v"]), es.SCHEMA_VERSION)
        dst.close()

    def test_재구축이_전_문서를_재큐한다(self):
        """🔴 **무음 실패의 본체.** `_DocMeta` 가 없으면 `switch_embed_model` 은 문서를
        0건 세고 0건을 재큐한다 — 재임베딩이 영영 일어나지 않는데 오류가 없다.
        문서 N건을 넣고 왕복시켜 N건이 재큐되는지를 본다."""
        n = 4
        src, _ = self._mkstore()
        self._seed_docs(src, n)
        dst, cfg2 = self._mkstore(dim=1024)
        self.assertEqual(cfg2.embedding_dim, 1024, "차원이 안 바뀌면 재구축 시험이 아니다")
        out = migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024)
        src.close()

        self.assertEqual(out["docs"], n)
        self.assertEqual(out["requeued"], n, "재큐 0건 = 재임베딩이 안 일어난다")
        pend = dst.run("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c")
        self.assertEqual(int(pend[0]["c"]), n)
        # 벡터 열이 실제로 새 차원인가(그러지 않으면 첫 쓰기에서 캐스팅 예외가 난다).
        dims = [r for r in dst.run("CALL TABLE_INFO('ChunkVec') RETURN *")
                if r.get("name") == "embedding"]
        self.assertIn("1024", str(dims[0]["type"]), f"벡터 열이 안 넓어졌다: {dims}")
        dst.close()

    def test_재구축_직후_FTS_가_답한다__재임베딩_전에(self):
        """색인 빌드는 최적화가 아니라 **필수 단계**다. import 만 하고 두면 새 스토어에
        FTS 가 없어, 재충전 창 동안 검색이 "벡터만 비는" 게 아니라 통째로 죽는다
        (`Table Chunk doesn't have an index with name chunk_ft`). 재임베딩은 시작도
        안 한 상태에서 텍스트 팔이 살아 있어야 강등이 설계된 모양이 된다."""
        src, _ = self._mkstore()
        self._seed_docs(src, 3)
        dst, _ = self._mkstore(dim=1024)
        try:
            migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024)
        except RuntimeError as exc:            # FTS 확장 미가용 환경(번들 없음)
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        finally:
            src.close()
        # 벡터는 비어 있다(재임베딩 전) — 그런데 검색은 살아 있어야 한다.
        self.assertEqual(int(dst.run("MATCH (v:ChunkVec) RETURN count(v) AS c")[0]["c"]), 0)
        hits = dst.fulltext_search("rehearsal", 5)
        dst.close()
        self.assertTrue(hits, "재구축 직후 FTS 가 답하지 않는다 — 재충전 창이 검색 불능")

    def test_재임베딩이_중간에_끊겨도_검색이_산다(self):
        """워커가 죽는 것은 정상 시나리오다(허브는 재기동이 잦다). 절반만 채운
        상태에서도 텍스트 팔은 답해야 한다 — 그것이 강등의 전부다."""
        from api import docstore as ds
        src, _ = self._mkstore()
        self._seed_docs(src, 4)
        dst, _ = self._mkstore(dim=1024)
        try:
            migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024)
        except RuntimeError as exc:
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        finally:
            src.close()
        # 문서 하나만 채우고 "죽는다".
        pend = ds.pending_embed_chunks(dst, limit=1)
        self.assertTrue(pend, "재큐가 비어 있다 — 앞의 테스트가 먼저 깨질 것이다")
        ds.apply_embeddings(dst, pend[0]["doc_id"],
                            {r["chunk_id"]: [[0.0] * 1024] for r in pend})
        left = dst.run("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c")
        self.assertGreater(int(left[0]["c"]), 0, "전부 끝나 버려 중단 상황이 아니다")
        hits = dst.fulltext_search("rehearsal", 5)
        dst.close()
        self.assertTrue(hits, "재임베딩 중단 상태에서 FTS 가 죽었다")


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
