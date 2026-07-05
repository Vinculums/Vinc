# -*- coding: utf-8 -*-
"""EmbeddedBackend (LadybugDB/Kùzu) 실행 e2e — 설계 §4·§5.

임베디드 드라이버(ladybug/kuzu)가 설치된 환경에서만 실행(미설치 시 skip). Neo4j
서버 e2e 와 달리 임베디드 e2e 는 인프로세스라 CI/개발 환경에서 그대로 돈다 —
Vinc 의 실제 배포 타깃(§1).

검증: ① 스키마 선행(노드 테이블 DDL) 프로비저닝 ② 리팩터된 엔진 write helper 가
store.session() 경유로 LadybugDB 에 적재 ③ MERGE 멱등(2회→1) ④ api.query 백엔드
중립 executor 가 임베디드에서 read.
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
# ⚠ ladybug 와 kuzu 는 한 프로세스에 공존 불가(pybind11 타입 재등록 충돌). 테스트는
# **활성 기본 드라이버 하나만** 쓴다 — 두 번째 드라이버를 절대 import 하지 않는다.


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug/kuzu) 미설치 — e2e skip")
class EmbeddedE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
            "MEMORY_EMBEDDING_MODEL": "local-en",  # 768d (영어 기본, design §8)
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_provision_renders_node_tables(self):
        from store.config import load_config
        from store.backend import open_store
        store = open_store(load_config())
        self.assertIsNotNone(store)
        stmts = store.provision(dim=load_config().embedding_dim)
        self.assertTrue(any("CREATE NODE TABLE Episode" in s for s in stmts))
        store.close()

    def test_recall_engine_write_and_query_roundtrip(self):
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_to_neo4j import merge_episode
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        ep = {
            "episode_id": "s1", "title": "Refund policy session",
            "date": "2026-07-04", "owner_id": "user:weg1456",
            "tenant_id": "tenant:local", "domain": "billing",
            "workspace": "planning", "mode": "advisor",
            "summary": "discussed refunds", "completed_items": ["refund flow"],
            "patterns": ["p1", "p2"], "source_file": "recall/s1.md",
        }
        with store.session() as session:
            merge_episode(session, ep)
            merge_episode(session, ep)   # 재실행 — MERGE 멱등
        store.close()

        res = q.recall(owner="user:weg1456", domain="billing")
        self.assertTrue(res["executed"])
        self.assertEqual(len(res["rows"]), 1, "MERGE 멱등 실패(2회 적재→1행 기대)")
        self.assertEqual(res["rows"][0]["title"], "Refund policy session")

    def test_preference_recall_ranks_on_embedded(self):
        """preference recall 이 임베디드에서 동작(방언 회귀 방지) — 점수는 백엔드 밖."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_to_neo4j import merge_episode
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        base = {"date": "2026-07-01", "tenant_id": "t", "workspace": "w",
                "mode": "m", "summary": "s", "completed_items": [],
                "patterns": [], "source_file": "f"}
        with store.session() as s:
            merge_episode(s, {**base, "episode_id": "plain", "title": "plain",
                              "owner_id": "u", "domain": "d", "date": "2026-07-03"})
            merge_episode(s, {**base, "episode_id": "pinned", "title": "pinned",
                              "owner_id": "u", "domain": "d"})
            s.run("MATCH (e:Episode {episode_id:'pinned'}) "
                  "SET e.pinned=true, e.access_count=10")
        store.close()

        r = q.recall(owner="u", rank="preference")
        self.assertTrue(r["executed"])
        self.assertEqual(r["rows"][0]["id"], "pinned")   # pin+freq 부스트로 선두
        self.assertGreater(r["rows"][0]["score"], r["rows"][1]["score"])

    def test_graph_query_uses_label_on_embedded(self):
        """graph 마이닝 질의가 임베디드에서 동작(§6-2 M4: type(r)→label(r))."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid, dom in [("a", "math"), ("b", "math"), ("iso", "art")]:
                s.run(f"MERGE (n:Concept {{concept_id:'{cid}'}}) "
                      f"SET n.title='{cid}', n.owner_id='u', n.domain='{dom}'")
            s.run("MATCH (a:Concept{concept_id:'a'}), (b:Concept{concept_id:'b'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
        store.close()

        g = q.graph(owner="u")
        self.assertTrue(g["executed"])
        self.assertEqual(len(g["nodes"]), 3)          # iso 고립 노드 포함(OPTIONAL)
        self.assertEqual(len(g["edges"]), 1)
        self.assertEqual(g["edges"][0]["type"], "RELATED_TO")

    def test_memory_relation_tables_usable(self):
        """§5-3 관계 테이블(Concept↔Concept 3종, Bridge→Domain/Concept)이 Kùzu 에서
        선언·MERGE 가능(엔진 concepts/bridges 의 관계 Cypher 동형)."""
        from store.config import load_config
        from store.backend import open_store

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            s.run("MERGE (a:Concept {concept_id:'c1'}) SET a.title='A'")
            s.run("MERGE (b:Concept {concept_id:'c2'}) SET b.title='B'")
            for rel in ("PREREQUISITE_OF", "DERIVES_FROM", "RELATED_TO"):
                s.run(f"MATCH (a:Concept {{concept_id:'c1'}}), "
                      f"(b:Concept {{concept_id:'c2'}}) MERGE (a)-[:{rel}]->(b)")
            s.run("MERGE (br:Bridge {bridge_id:'br1'}) SET br.title='X'")
            s.run("MERGE (d:Domain {name:'art'})")
            s.run("MATCH (br:Bridge {bridge_id:'br1'}), (d:Domain {name:'art'}) "
                  "MERGE (br)-[:CROSSES]->(d)")
            s.run("MATCH (br:Bridge {bridge_id:'br1'}), (c:Concept {concept_id:'c1'}) "
                  "MERGE (br)-[r:CONNECTS {role:'input'}]->(c)")
        rows = store.run("MATCH (a:Concept)-[r]->(b:Concept) "
                         "RETURN count(r) AS c")
        store.close()
        self.assertEqual(rows[0]["c"], 3)  # 3 Concept 관계

    def test_pack_ingest_overlays_preference_roundtrip(self):
        """A+2 2단계(§10): 재수집된 Episode 위에 pack.json 의 preference 신호만
        오버레이 — 구조(title 등)는 보존, pinned/tags/access 는 팩 값으로. 멱등."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_to_neo4j import merge_episode
        from engines.pack_ingest import overlay_pack, PACK_KIND
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        # 1단계 재수집: 구조만(런타임 preference 없음).
        with store.session() as s:
            merge_episode(s, {"episode_id": "s1", "title": "Refund session",
                              "date": "2026-07-01", "owner_id": "u", "tenant_id": "t",
                              "domain": "billing", "workspace": "w", "mode": "m",
                              "summary": "structural summary", "completed_items": [],
                              "patterns": [], "source_file": "recall/s1.md"})
        # 서버측 pack.json 모사 — preference 신호가 실려 있고 title 은 다른(구/무의미) 값.
        pack = {"kind": PACK_KIND, "owner": "u", "episodes": [{
            "id": "s1", "title": "STALE TITLE", "owner": "u", "domain": "billing",
            "pinned": True, "tags": ["refund", "kr"], "access_count": 9,
            "last_access": "2026-07-04"}]}
        with store.session() as s:
            n1 = overlay_pack(s, pack)
            n2 = overlay_pack(s, pack)   # 재실행 — 멱등
        store.close()
        self.assertEqual((n1, n2), (1, 1))

        r = q.recall(owner="u", rank="preference")
        self.assertTrue(r["executed"])
        row = next(x for x in r["rows"] if x["id"] == "s1")
        self.assertTrue(row["pinned"])                       # 팩 preference 오버레이
        self.assertEqual(set(row["tags"]), {"refund", "kr"})
        self.assertEqual(row["access_count"], 9)
        self.assertEqual(row["title"], "Refund session")     # 재수집 구조 보존(팩 미덮어씀)

    def test_pack_only_ingest_creates_node(self):
        """대안 경로(§10 '팩 단독'): 재수집 없이 팩만 적재 시 노드가 생성되고
        구조는 팩 수록 범위로 채워진다."""
        from store.config import load_config
        from store.backend import open_store
        from engines.pack_ingest import overlay_pack, PACK_KIND
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        pack = {"kind": PACK_KIND, "owner": "u", "episodes": [{
            "id": "p1", "title": "Pack-only ep", "owner": "u", "domain": "d",
            "date": "2026-07-03", "pinned": False, "tags": [], "access_count": 0}]}
        with store.session() as s:
            overlay_pack(s, pack)
        store.close()

        r = q.recall(owner="u")
        ids = {x["id"]: x for x in r["rows"]}
        self.assertIn("p1", ids)
        self.assertEqual(ids["p1"]["title"], "Pack-only ep")   # 팩 구조 채움

    def test_decide_traversal_on_embedded(self):
        """decide 질의가 임베디드에서 동작(§6-2 M7 라벨없는 MATCH 속성 바인딩 +
        M3 TRAIL 사이클 가드). WALK 였다면 사이클서 경로 폭증, `n.id` 였다면 Binder
        오류 — 둘 다 회귀 방지."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b", "c"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u'",
                      id=cid)
            # 삼각 사이클 a→b→c→a (WALK 라면 깊은 depth 서 경로 폭증)
            for src, dst in (("a", "b"), ("b", "c"), ("c", "a")):
                s.run("MATCH (x:Concept{concept_id:$s}),(y:Concept{concept_id:$t}) "
                      "MERGE (x)-[:RELATED_TO]->(y)", s=src, t=dst)
        store.close()

        r = q.decide("a", depth=4)
        self.assertTrue(r["executed"], r.get("note"))
        self.assertGreater(len(r["rows"]), 0)   # 사이클 위 노드서 경로 존재

    def test_vector_and_fts_search(self):
        """M5/M6: 벡터(HNSW)+FTS(BM25) 검색 — 활성 기본 드라이버로. 검색 확장이
        없으면(ladybug 는 별도 다운로드 → 오프라인 시 번들 §9; 프록시 차단 환경)
        skip. kuzu 는 정적 링크라 그대로 실행. (두 드라이버 동시 import 금지.)"""
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()                    # 활성 기본 드라이버(두 번째 import 금지)
        store = open_store(cfg)
        self.assertIsNotNone(store)
        store.provision(dim=4)
        with store.session() as s:
            for cid, txt, emb in [
                ("a", "refund policy billing", [1.0, 0.1, 0.0, 0.0]),
                ("b", "login oauth flow", [0.0, 1.0, 0.1, 0.0]),
                ("c", "refund dispute", [0.9, 0.2, 0.0, 0.0])]:
                s.run("MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t, c.embedding=$e",
                      id=cid, t=txt, e=emb)
        try:
            store.build_search_indexes(dim=4)   # 적재 후 인덱스 build(HNSW)
        except RuntimeError as exc:
            store.close()
            self.skipTest(f"검색 확장 미가용({store.driver_name}) — §9 번들 필요: {exc}")
        v = store.vector_search([1.0, 0.0, 0.0, 0.0], top_k=2)
        f = store.fulltext_search("refund", top_k=3)
        store.close()
        self.assertEqual(v[0]["id"], "a")                       # 최근접
        self.assertEqual({r["id"] for r in f}, {"a", "c"})      # refund 포함 청크

    def test_hybrid_search_rrf_via_api(self):
        """api.query.search 하이브리드(BM25+벡터 RRF, §4-2) — 쿼리 임베딩 주입형."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=4)
        with store.session() as s:
            for cid, txt, emb in [
                ("a", "refund policy billing", [1.0, 0.1, 0.0, 0.0]),
                ("b", "login oauth flow", [0.0, 1.0, 0.1, 0.0]),
                ("c", "refund dispute resolution", [0.9, 0.2, 0.0, 0.0])]:
                s.run("MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t, c.embedding=$e",
                      id=cid, t=txt, e=emb)
        try:
            store.build_search_indexes(dim=4)
        except RuntimeError as exc:
            store.close()
            self.skipTest(f"검색 확장 미가용 — §9: {exc}")
        store.close()
        # BM25 단독
        r1 = q.search("refund", top_k=3)
        self.assertTrue(r1["executed"])
        self.assertTrue({"a", "c"} <= {row["id"] for row in r1["rows"]})
        # 하이브리드(쿼리 임베딩 주입) → RRF 융합
        r2 = q.search("refund", query_embedding=[1.0, 0.0, 0.0, 0.0], top_k=3)
        self.assertTrue(r2["executed"])
        self.assertTrue(all("rrf_score" in row for row in r2["rows"]))
        self.assertEqual(r2["rows"][0]["id"], "a")   # 벡터 최근접 + BM25 refund 겹침

    def test_scope_filter_isolates_owners(self):
        """스코프 1차(§1) — owner 필터로 다른 계정 데이터 미혼입."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_to_neo4j import merge_episode
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        base = {"date": "2026-07-04", "tenant_id": "tenant:local",
                "workspace": "", "mode": "", "summary": "", "completed_items": [],
                "patterns": [], "source_file": ""}
        with store.session() as session:
            merge_episode(session, {**base, "episode_id": "a", "title": "A",
                                    "owner_id": "user:alice", "domain": "d"})
            merge_episode(session, {**base, "episode_id": "b", "title": "B",
                                    "owner_id": "user:bob", "domain": "d"})
        store.close()

        res = q.recall(owner="user:alice")
        ids = {r["id"] for r in res["rows"]}
        self.assertEqual(ids, {"a"}, "스코프 밖(bob) 데이터가 혼입됨 — §1 위반")


if __name__ == "__main__":
    unittest.main()
