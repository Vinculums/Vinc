# -*- coding: utf-8 -*-
"""EmbeddedBackend (LadybugDB) 실행 e2e — 설계 §4·§5.

임베디드 드라이버(ladybug)가 설치된 환경에서만 실행(미설치 시 skip — 통상 항상
설치돼 있음, v0.5.3 부터 유일 드라이버). Neo4j 서버 e2e 와 달리 임베디드 e2e 는
인프로세스라 CI/개발 환경에서 그대로 돈다 — Vinc 의 실제 배포 타깃(§1).

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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
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

    def test_write_tenancy_2owner_scoped(self):
        """SEC-3 — 강제 owner 쓰기 경로가 **실 엔진에서** 교차테넌트를 차단하는가.
        스텁(test_write_tenancy)이 초록이어도 엔진이 다르게 굴 수 있으니(SEC-1 교훈)
        진짜 Kùzu 로 A 가 B 의 노드를 delete/merge 못 하고, ingest 소유권 탈취가
        거부되는지 본다. SEC-1 읽기 격리(test_scope_filter_isolates_owners)의 쓰기측."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)

        def ingest(owner, nid, title="x"):
            frag.merge_fragment(
                {"owner": owner, "nodes": [{"id": nid, "type": "concept", "title": title}],
                 "edges": []}, store=store)

        def exists(nid):
            r = store.run("MATCH (c:Concept) WHERE c.concept_id = $id RETURN count(c) AS c",
                          {"id": nid})
            return bool(r and r[0]["c"])

        ingest("user:A", "concept:shared", "A owns")
        ingest("user:B", "concept:bonly", "B owns")

        # delete: B 는 A 의 노드를 못 지운다(found:False, 노드 생존) / A 는 자기 것을 지운다
        r = q.delete_node("concept:shared", owner="user:B", store=store)
        self.assertIs(r.get("found"), False)
        self.assertTrue(exists("concept:shared"))
        r = q.delete_node("concept:shared", owner="user:A", store=store)
        self.assertTrue(r.get("found") and r.get("deleted"))
        self.assertFalse(exists("concept:shared"))

        # ingest 소유권 탈취 거부: A 가 B 소유 id 를 강제 owner 로 적재 → owner 불변·미덮어씀·경고
        out = frag.merge_fragment(
            {"owner": "user:A",
             "nodes": [{"id": "concept:bonly", "type": "concept", "title": "HIJACK"}], "edges": []},
            store=store)
        row = store.run("MATCH (c:Concept) WHERE c.concept_id = 'concept:bonly' "
                        "RETURN c.owner_id AS o, c.title AS t")[0]
        self.assertEqual(row["o"], "user:B")
        self.assertNotEqual(row["t"], "HIJACK")
        self.assertTrue(any("another tenant" in str(w) for w in (out.get("warnings") or [])))

        # merge: B 는 A 의 두 concept 를 병합 못 한다(found:False, drop 생존) / A 는 병합한다
        ingest("user:A", "concept:a1")
        ingest("user:A", "concept:a2")
        r = q.merge_nodes("concept:a1", "concept:a2", owner="user:B", store=store)
        self.assertIs(r.get("found"), False)
        self.assertTrue(exists("concept:a2"))
        r = q.merge_nodes("concept:a1", "concept:a2", owner="user:A", store=store)
        self.assertTrue(r.get("found"))
        self.assertFalse(exists("concept:a2"))
        store.close()

    def test_recall_engine_write_and_query_roundtrip(self):
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_ingest import merge_episode
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
        from engines.recall_ingest import merge_episode
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
        # 개념만 센다 — `/graph` 는 2026-08-14 부터 주제 마크(`domain:*`)도 함께 낸다.
        concepts = [n for n in g["nodes"] if not n["id"].startswith("domain:")]
        self.assertEqual(len(concepts), 3)            # iso 고립 노드 포함(OPTIONAL)
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

    def test_tag_set_union_on_embedded(self):
        """tag 오버레이가 임베디드에서 동작(§6-2 리스트컴프 미지원 회귀 방지). 구
        list-comp 였다면 kuzu 파서 오류로 크래시 — list_distinct(list_cat) 정합."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_ingest import merge_episode
        from api import preference as pref
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            merge_episode(s, {"episode_id": "t1", "title": "T", "date": "2026-07-01",
                              "owner_id": "u", "tenant_id": "t", "domain": "d",
                              "workspace": "w", "mode": "m", "summary": "s",
                              "completed_items": [], "patterns": [], "source_file": "f"})
        store.close()

        pref.tag("t1", tags=["billing", "kr"])
        pref.tag("t1", tags=["kr", "refund"])   # kr 중복 — 합집합, 미중복
        r = q.recall(owner="u")
        row = next(x for x in r["rows"] if x["id"] == "t1")
        self.assertEqual(set(row["tags"]), {"billing", "kr", "refund"})
        self.assertEqual(len(row["tags"]), 3)   # 중복 제거 확인

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
        # decide 계약은 {nodes, edges, conflicts}(A1.9 A5 재설계 — 경로 blowup 제거,
        # label=title·props 셰이핑). 사이클 위 노드서 서브그래프가 재구성돼야 한다.
        self.assertTrue(r["found"])
        self.assertGreater(len(r["nodes"]), 0)   # 사이클 위 노드서 서브그래프 존재
        self.assertGreater(len(r["edges"]), 0)   # 순회 엣지 존재

    def test_vector_and_fts_search(self):
        """M5/M6: 벡터(HNSW)+FTS(BM25) 검색. 검색 확장이 없으면(ladybug 는 별도
        다운로드 → 오프라인 시 번들 §9; 프록시 차단 환경) skip."""
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        store = open_store(cfg)
        self.assertIsNotNone(store)
        store.provision(dim=4)
        # v8 — 벡터는 **자식(ChunkVec)** 에 산다. FTS 는 여전히 Chunk.text 다.
        # 즉 두 인덱스가 서로 다른 테이블에 걸리고, vector_search 가 자식→부모로
        # 되돌린다. Chunk.embedding 에 써 봐야 검색에 안 잡힌다(더 이상 안 읽는다).
        with store.session() as s:
            for cid, txt, emb in [
                ("a", "refund policy billing", [1.0, 0.1, 0.0, 0.0]),
                ("b", "login oauth flow", [0.0, 1.0, 0.1, 0.0]),
                ("c", "refund dispute", [0.9, 0.2, 0.0, 0.0])]:
                s.run("MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t", id=cid, t=txt)
                s.run("MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
                      "v.doc_id='d', v.owner_id='', v.seq=0, v.embedding=$e",
                      vid=f"{cid}#000", id=cid, e=emb)
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

    def test_두_팔_다_owner_필터로_실제로_돈다(self):
        """SEC-1 의 급소는 **쿼리가 실제로 도는가**다. F2 가 청크 팔에 owner 를 넘기기
        시작하자 벡터 팔이 `Parser exception` 으로 통째로 죽었다(2026-08-07 실측):
        `vector_search` 는 자식→부모 되돌리기로 `WHERE` 를 이미 쓰는데 필터가 `WHERE`
        를 또 붙여 `WHERE … WHERE …` 가 됐다.

        가려져 있던 이유가 둘이다. ① 그전엔 owner 가 청크 팔에 안 가서 필터가 빈
        문자열이었고 ② 질의 임베딩을 만들 주체가 없어 벡터 팔이 아예 안 돌았다.
        그래서 **실엔진 · 두 소유자 · 두 팔**을 한 번에 건다 — 스텁으로는 안 잡힌다."""
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        store = open_store(cfg)
        self.assertIsNotNone(store)
        store.provision(dim=4)
        with store.session() as s:
            for cid, txt, emb, own in [
                ("u1a", "refund policy billing", [1.0, 0.1, 0.0, 0.0], "u1"),
                ("u2a", "refund policy billing", [1.0, 0.1, 0.0, 0.0], "u2")]:
                s.run("MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t, c.owner_id=$o",
                      id=cid, t=txt, o=own)
                s.run("MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
                      "v.doc_id='d', v.owner_id=$o, v.seq=0, v.embedding=$e",
                      vid=f"{cid}#000", id=cid, o=own, e=emb)
        try:
            store.build_search_indexes(dim=4)
        except RuntimeError as exc:
            store.close()
            self.skipTest(f"검색 확장 미가용({store.driver_name}) — §9 번들 필요: {exc}")
        f = {"owner_id": "u1"}
        vec = store.vector_search([1.0, 0.0, 0.0, 0.0], top_k=5, filters=f)
        fts = store.fulltext_search("refund", top_k=5, filters=f)
        store.close()
        # 던지지 않는 것만으로는 부족하다 — 실제로 걸러졌는지 본다.
        self.assertEqual({r["id"] for r in vec}, {"u1a"},
                         f"벡터 팔 owner 필터가 깨졌다: {vec}")
        self.assertEqual({r["id"] for r in fts}, {"u1a"},
                         f"FTS 팔 owner 필터가 깨졌다: {fts}")

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
                # v8 — 벡터는 자식(ChunkVec), 텍스트는 부모(Chunk).
                s.run("MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t", id=cid, t=txt)
                s.run("MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
                      "v.doc_id='d', v.owner_id='', v.seq=0, v.embedding=$e",
                      vid=f"{cid}#000", id=cid, e=emb)
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

    def test_eg1_graph_arm_surfaces_mentioned_concepts(self):
        """E-G1: 상위 Chunk 의 :MENTIONS 이웃 개념이 node_hits 그래프 암으로 승격.
        핵심 — 질문과 title 매치가 없는 개념도, 관련 청크가 그것을 언급하면 검색에
        나온다(다중 홉 가교, HippoRAG 이부 그래프 결정론 번역)."""
        from store.config import load_config
        from store.backend import open_store
        from engines.mentions_link import link_mentions
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=4)
        with store.session() as s:
            # 청크: refund 를 논하지만 본문에 'gauge' 라는 단어는 없다.
            # ⚠ owner_id 를 반드시 찍는다. 이 픽스처는 put_doc 을 우회해 손으로 심는데,
            #    실제 쓰기 경로는 `c.owner_id = $owner` 를 항상 찍는다(api/docstore.py).
            #    F2(SEC-1)가 청크 팔에 owner 필터를 넣은 뒤로는 owner 없는 청크가
            #    스코프 조회에서 빠지므로, 안 찍으면 이 테스트만 현실과 어긋난 상태를
            #    검증하게 된다 — 실패는 회귀가 아니라 픽스처가 낡았다는 신호였다.
            s.run("MERGE (c:Chunk {chunk_id:'ch1'}) SET c.text=$t, c.embedding=$e, "
                  "c.owner_id='u', c.source_file='/abs/topo.md'",
                  t="refund policy and dispute resolution", e=[1.0, 0.1, 0.0, 0.0])
            # 개념 gauge: title 이 'gauge' 라 쿼리 'refund' 와 title 매치 안 됨.
            # 그러나 source_file basename(topo.md) 이 청크와 같아 MENTIONS 로 이어진다.
            s.run("MERGE (c:Concept {concept_id:'gauge'}) SET c.title='gauge', "
                  "c.owner_id='u', c.domain='physics', c.source_file='physics/topo.md'")
            # 이웃 개념 duality: gauge 와 RELATED_TO — 1-hop 확산 대상.
            s.run("MERGE (c:Concept {concept_id:'duality'}) SET c.title='duality', "
                  "c.owner_id='u', c.domain='physics', c.source_file='physics/x.md'")
            s.run("MATCH (a:Concept{concept_id:'gauge'}),(b:Concept{concept_id:'duality'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
        try:
            store.build_search_indexes(dim=4)
        except RuntimeError as exc:
            store.close()
            self.skipTest(f"검색 확장 미가용 — §9: {exc}")
        stats = link_mentions(store, owner="u")   # 결정론 링커
        store.close()
        self.assertGreaterEqual(stats["links"], 1)
        self.assertEqual(stats["by_method"].get("source"), 1)   # basename 일치

        r = q.search("refund", owner="u", top_k=5)
        self.assertTrue(r["executed"])
        hit_ids = {h["id"] for h in r["node_hits"]}
        # gauge: 직접 언급(그래프 암), duality: 1-hop 이웃 — 둘 다 title 매치 없이 등장
        self.assertIn("gauge", hit_ids)
        self.assertIn("duality", hit_ids)
        gauge = next(h for h in r["node_hits"] if h["id"] == "gauge")
        self.assertIn("graph", gauge["arms"])   # D24 매치 표기

    def test_eg5_neighbors_and_eg7_shortest_path(self):
        """E-G5 단일 홉 관측 + E-G7 최단 경로 — 임베디드 실행."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b", "c", "d", "e"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            # a-b, a-c (a 의 이웃 2개), 경로 a..d: a-b-...-d 없음; a-c-e-d 구성
            for x, y in (("a", "b"), ("a", "c"), ("c", "e"), ("e", "d")):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)

        # E-G5 — a 의 1-hop 이웃 = {b, c}
        nb = q.neighbors("a", owner="u", store=store)
        self.assertTrue(nb["executed"])
        self.assertEqual({n["id"] for n in nb["neighbors"]}, {"b", "c"})
        self.assertEqual(nb["by_rel"].get("RELATED_TO"), 2)

        # E-G7 — a → d 최단 경로 = a-c-e-d (길이 3)
        sp = q.shortest_path("a", "d", owner="u", store=store)
        store.close()
        self.assertTrue(sp["executed"])
        self.assertTrue(sp["found"])
        self.assertEqual(sp["length"], 3)
        self.assertEqual(sp["paths"][0]["ids"], ["a", "c", "e", "d"])
        self.assertEqual(sp["weight"], "hops")

    def test_eg8_confidence_weighted_path_diverges_from_hops(self):
        """E-G8 가중 최단 경로 — 홉 최단과 **다른 답**을 내는지 임베디드 실행으로 확인.

        홉으로는 a-y-z(2), 증거로는 a-b-c-z(3). 두 축이 갈리는 것이 이 기능의 존재
        이유이므로, 갈린다는 사실 자체를 회귀로 고정한다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b", "c", "y", "z"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            for x, y, conf in (("a", "b", 0.9), ("b", "c", 0.9), ("c", "z", 0.9),
                               ("a", "y", 0.2), ("y", "z", 0.2)):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[r:RELATED_TO]->(t) SET r.confidence = $c",
                      x=x, y=y, c=conf)

        hops = q.shortest_path("a", "z", owner="u", store=store)
        conf = q.shortest_path("a", "z", owner="u", weight="confidence", store=store)
        bogus = q.shortest_path("a", "z", owner="u", weight="nonsense", store=store)
        missing = q.shortest_path("a", "nope", owner="u", weight="confidence", store=store)
        store.close()

        self.assertTrue(hops["found"])
        self.assertEqual(hops["paths"][0]["ids"], ["a", "y", "z"])       # 홉 최단

        self.assertTrue(conf["executed"])
        self.assertTrue(conf["found"])
        self.assertEqual(conf["weight"], "confidence")
        self.assertEqual(conf["paths"][0]["ids"], ["a", "b", "c", "z"])  # 증거 최선
        self.assertEqual(conf["paths"][0]["titles"], ["a", "b", "c", "z"])
        self.assertAlmostEqual(conf["cost"], 0.3, places=6)
        self.assertEqual(conf["length"], 3)          # 홉으로는 더 길다 — 의도된 결과

        # 미지 weight 는 조용히 hops 로 강등하고 실제 적용 축을 에코한다.
        self.assertEqual(bogus["weight"], "hops")
        self.assertEqual(bogus["paths"][0]["ids"], ["a", "y", "z"])
        # 도달 불가는 executed=True + found=False (경로 부재와 같은 계약)
        self.assertTrue(missing["executed"])
        self.assertFalse(missing["found"])

    def test_g1_shortest_reach_set_matches_trail(self):
        """G1 — 도달집합 순회를 TRAIL→SHORTEST 로 바꿔도 **집합이 같다**(시드 제외).

        교체 근거가 '결과 동일 + 더 쌈'이므로 동일성 쪽을 회귀로 고정한다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("s", "n1", "n2", "n3", "n4"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            # 사이클 포함(시드 복귀 경로 존재) — TRAIL 이 시드를 행으로 내던 조건
            for x, y in (("s", "n1"), ("n1", "n2"), ("n2", "s"),
                         ("n2", "n3"), ("n3", "n4")):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)

        def reach(sem: str) -> set:
            cy = ("MATCH (n:Concept) WHERE n.concept_id = 's' AND n.owner_id = 'u' "
                  f"MATCH (n)-[* {sem} 1..3]-(m:Concept) WHERE m.owner_id = 'u' "
                  "RETURN DISTINCT m.concept_id AS id")
            return {r["id"] for r in store.run(cy)}

        trail, shortest = reach("TRAIL"), reach("SHORTEST")
        plan_ids = {r["id"] for r in q.execute(
            q.subgraph_ids_plan("s", depth=3, owner="u"), cfg, store=store).get("rows", [])}
        store.close()

        # 차집합은 시드 자신뿐 — 호출부가 시드를 합류시키므로 최종 결과는 동일하다.
        self.assertEqual(trail - shortest, {"s"})
        self.assertEqual(shortest - trail, set())
        self.assertEqual(plan_ids, shortest)
        self.assertEqual({"s", *plan_ids}, {"s", *trail})

    def test_a110_natural_question_ranks_the_right_concept(self):
        """A1.10 — 자연어 질문이 옳은 개념을 **1위로** 올린다(묻기 표면의 근본 회귀).

        구 토큰 AND 에서는 조사·의문사 때문에 제목 매치가 0건이라 node_hits 가 그래프
        암(=id 알파벳순)으로만 채워졌고, 묻기가 그 첫 후보를 답으로 냈다. 실측 오답:
        "결제 실패하면 어떻게 되나요?" → `멱등 키`(i < p). 그 오답을 여기 박아 둔다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid, title, dom, summary in (
                ("concept:payment-failure", "결제 실패 처리", "billing",
                 "카드 승인이 거절되면 재시도하고 실패하면 유예 상태로 돌린다."),
                ("concept:idempotency-key", "멱등 키", "billing",
                 "같은 요청이 두 번 와도 한 번만 처리되게 하는 식별자다."),
                ("concept:refund-policy", "환불 정책", "billing",
                 "결제 후 7일 이내 전액 환불한다."),
                ("concept:optimistic-locking", "낙관적 동시성 제어", "db",
                 "커밋 시점에 버전을 검사해 어긋나면 실패시킨다."),
            ):
                # one_liner 는 비운다 — MCP 적재분의 실태(쓰기 경로 없음).
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', c.title=$t, "
                      "c.domain=$d, c.one_liner='', c.props=$p",
                      id=cid, t=title, d=dom, p='{"summary": "%s"}' % summary)

        def top(question):
            r = q.search(question, owner="u", top_k=10, store=store)
            hits = r.get("node_hits") or []
            return (hits[0]["id"] if hits else None), hits

        # ① 오답 회귀 — 결제 실패를 물으면 결제 실패가 1위여야 한다.
        first, hits = top("결제 실패하면 어떻게 되나요?")
        self.assertEqual(first, "concept:payment-failure",
                         f"멱등 키 오답 재발: {[(h['id'], h.get('hit_tokens')) for h in hits]}")
        # ② 매치 근거가 수치로 나온다(묻기의 확정/되묻기 판정 재료).
        self.assertGreaterEqual(hits[0].get("hit_tokens", 0), 1)
        self.assertEqual(hits[0].get("matched_in"), "title")

        # ③ 조사가 붙어도 제목으로 잡힌다("환불은" → "환불").
        first, _ = top("환불은 며칠 이내에 되나요?")
        self.assertEqual(first, "concept:refund-policy")

        # ④ 제목이 한국어여도 영어 슬러그(id)로 찾힌다(구 검색은 id 를 안 봤다).
        first, hits = top("what is optimistic locking?")
        self.assertEqual(first, "concept:optimistic-locking")
        self.assertEqual(hits[0].get("matched_in"), "id")
        store.close()

    def test_eg3_component_diagnosis_and_communities(self):
        """E-G3: algo 확장(WCC·Louvain) 구조 진단. ladybug 오프라인 미번들이면
        강등(executed=False)이라 skip."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b", "c", "d", "e", "lone"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            # 컴포넌트1: a-b-c 삼각, 컴포넌트2: d-e, 고아: lone
            for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("d", "e")):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)
        # algo 확장 가용성 확인(다운로드형 — 오프라인 배포 시 §9 번들 필요)
        diag = q.component_diagnosis(owner="u", store=store)
        if not diag["executed"]:
            store.close()
            self.skipTest(f"algo 확장 미가용 — §9: {diag.get('note')}")
        # 고아 = lone (단일 노드 컴포넌트)
        self.assertIn("lone", diag["orphans"])
        self.assertEqual(diag["orphan_count"], 1)
        # 컴포넌트 3개(삼각·페어·고아), 최대 컴포넌트 크기 3
        self.assertEqual(diag["component_count"], 3)
        self.assertEqual(diag["components"][0]["size"], 3)

        com = q.communities(owner="u", store=store)
        store.close()
        self.assertTrue(com["executed"])
        # 최소 2개 커뮤니티(삼각·페어 분리) — 고립 lone(-1)은 제외
        sizes = {c["size"] for c in com["communities"]}
        self.assertIn(3, sizes)
        self.assertTrue(all(c["dominant_domain"] == "math" for c in com["communities"]))

    def test_eg3_community_context_ranks_by_mentions_and_attaches_excerpts(self):
        """unified-kg-rag-comparison §4 — Louvain 을 answer material 에 배선.
        시드 하나가 속한 커뮤니티만 골라, 언급이 가장 많은 대표에게 발췌를 붙인다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b", "c", "d", "e", "lone"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("d", "e")):
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)
            # 'a' 를 두 번, 'b' 를 한 번 언급 — 대표 순위는 a > b > c(무언급, 사전순).
            for i, target in enumerate(("a", "a", "b")):
                s.run("MERGE (ch:Chunk {chunk_id:$cid}) SET ch.owner_id='u', "
                      "ch.doc_id='doc:1', ch.section_title='S', ch.text=$t "
                      "WITH ch MATCH (c:Concept{concept_id:$target}) "
                      "MERGE (ch)-[:MENTIONS {method:'fts'}]->(c)",
                      cid=f"ch{i}", t=f"about {target}", target=target)

        diag = q.component_diagnosis(owner="u", store=store)
        if not diag["executed"]:
            store.close()
            self.skipTest(f"algo 확장 미가용 — §9: {diag.get('note')}")

        ctx = q.community_context(owner="u", node="a", store=store)
        self.assertTrue(ctx["executed"])
        self.assertEqual(ctx["size"], 3)
        self.assertEqual(ctx["dominant_domain"], "math")
        rep_ids = [r["id"] for r in ctx["representatives"]]
        self.assertEqual(rep_ids, ["a", "b", "c"])   # 언급 2 > 1 > 0, 나머지는 사전순
        self.assertIn("about a", ctx["representatives"][0]["excerpt"])
        self.assertEqual(ctx["representatives"][2]["excerpt"], "")  # 'c' 는 무언급
        self.assertEqual(ctx["members"], [])   # 3명 다 대표(cap=5)라 나머지가 없다

        # 같은 커뮤니티의 다른 시드('b')를 넣어도 같은 군집이 나온다.
        ctx_b = q.community_context(owner="u", node="b", store=store)
        self.assertEqual(ctx_b["community"], ctx["community"])

        # 고립 노드는 커뮤니티가 없다 — 실패가 아니라 명시적 강등.
        lone = q.community_context(owner="u", node="lone", store=store)
        self.assertFalse(lone["executed"])
        self.assertIn("lone", lone["note"])

        # 미지 노드도 마찬가지.
        ghost = q.community_context(owner="u", node="concept:ghost", store=store)
        store.close()
        self.assertFalse(ghost["executed"])

    def test_eg3_community_context_caps_representatives_and_reports_the_rest(self):
        """커뮤니티가 대표 상한(5)보다 크면, 나머지는 발췌 없이 members 에 실리고
        진짜 잘렸으면 members_truncated 로 말한다(F6 과 같은 규율)."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        n = 6   # 대표 상한(5)을 넘기되 member_limit(기본 50)은 안 넘는 크기
        with store.session() as s:
            for i in range(n):
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='math'", id=f"n{i}")
            # 완전그래프(클리크) — 사슬은 Louvain 이 모듈성 최적화로 반으로 쪼갠다
            # (실측: 8노드 사슬 → 4+4). 클리크는 내부가 최대로 밀집해 하나로 남는다.
            for i in range(n):
                for j in range(i + 1, n):
                    s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                          "MERGE (p)-[:RELATED_TO]->(t)", x=f"n{i}", y=f"n{j}")

        diag = q.component_diagnosis(owner="u", store=store)
        if not diag["executed"]:
            store.close()
            self.skipTest(f"algo 확장 미가용 — §9: {diag.get('note')}")

        ctx = q.community_context(owner="u", node="n0", store=store)
        store.close()
        self.assertTrue(ctx["executed"])
        self.assertEqual(ctx["size"], n)
        self.assertEqual(len(ctx["representatives"]), 5)          # 상한
        self.assertEqual(len(ctx["members"]), n - 5)              # 나머지는 id/title 만
        self.assertFalse(ctx["members_truncated"])                # member_limit(50) 안 넘음
        self.assertTrue(all(set(m) == {"id", "title"} for m in ctx["members"]))

    def test_eg3_member_lists_declare_truncation(self):
        """F6 — 큰 컴포넌트의 member 목록이 **조용히** 잘리지 않는가.

        구버전은 members 를 50개로 자르고 아무 표시도 안 했다. 그러면 호출자는
        size=60 인데 members 가 50개인 것을 데이터 불일치로 읽거나, 더 나쁘게는
        50개가 전부라고 믿는다. size 는 언제나 진짜 총수여야 하고, 잘렸으면
        members_truncated 로 말해야 한다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        n = 60                                   # 기본 상한 50 을 넘긴다
        with store.session() as s:
            for i in range(n):
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='math'", id=f"n{i}")
            for i in range(n - 1):               # 사슬 하나 = 컴포넌트 하나
                s.run("MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=f"n{i}", y=f"n{i+1}")

        diag = q.component_diagnosis(owner="u", store=store)
        if not diag["executed"]:
            store.close()
            self.skipTest(f"algo 확장 미가용 — §9: {diag.get('note')}")
        big = diag["components"][0]
        self.assertEqual(big["size"], n)                     # 총수는 진실
        self.assertEqual(len(big["members"]), 50)            # 목록은 기본 상한
        self.assertTrue(big["members_truncated"])            # 그리고 그렇다고 말한다

        # 상한을 올리면 전부 온다 — 그리고 잘림 표시가 내려간다.
        full = q.component_diagnosis(owner="u", store=store, member_limit=n)
        big2 = full["components"][0]
        self.assertEqual(len(big2["members"]), n)
        self.assertFalse(big2["members_truncated"])

        # communities 도 같은 계약을 진다.
        com = q.communities(owner="u", store=store)
        if com["executed"]:
            self.assertTrue(all("members_truncated" in c for c in com["communities"]))
            cut = [c for c in com["communities"] if c["size"] > 50]
            self.assertTrue(all(c["members_truncated"] for c in cut))

        # neighbors 도 마찬가지 — 사슬 중간 노드는 이웃 2개라 안 잘린다.
        nb = q.neighbors(node="n1", owner="u", store=store)
        store.close()
        self.assertIn("neighbors_truncated", nb)
        self.assertFalse(nb["neighbors_truncated"])

    def test_tool_descriptions_do_not_lie_about_their_return_shape(self):
        """설명의 `Returns {…}` 가 **실제 반환 키**와 맞는가.

        이 가드가 없어서 실제로 틀린 문구가 들어갔다: vinc_graph 설명이
        {nodes:[{id,type,title…}], edges:[{src,dst,rel,confidence}], counts} 라고
        적었는데 엔진은 label/kind/source/target 을 주고 counts 는 아예 없었다.
        설명은 주석이 아니라 계약이라, 확신에 차서 틀린 문구는 얇지만 참인 원문보다
        나쁘다 — 모델이 없는 키를 읽으려 든다.

        검사 대상은 `TOOLS` 가 아니라 **tools/list 가 실제로 내보내는 스펙**이다.
        rpc 에 description 을 통째로 갈아치우는 override 계층이 있어서, tools.py 를
        고쳐도 호스트에는 안 닿는 상태가 실제로 있었다.

        top-level 키만 본다(중첩까지 파싱하면 테스트가 문법 검사기가 된다)."""
        import re
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        from vinc_mcp.rpc import _tool_specs

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("alpha", "beta"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            s.run("MATCH (p:Concept{concept_id:'alpha'}),(t:Concept{concept_id:'beta'}) "
                  "MERGE (p)-[:RELATED_TO]->(t)")

        calls = {
            "vinc_domains":      lambda: q.domains(owner="u", store=store),
            "vinc_recall":       lambda: q.recall(owner="u", store=store),
            "vinc_graph":        lambda: q.graph(owner="u", seed="concept:alpha",
                                                 depth=1, store=store),
            "vinc_decide":       lambda: q.decide("concept:alpha", depth=1, owner="u",
                                                  store=store),
            "vinc_suggest_ties": lambda: q.suggest_ties(owner="u", node="concept:alpha",
                                                        store=store),
            "vinc_neighbors":    lambda: q.neighbors("concept:alpha", owner="u",
                                                     store=store),
            # F10·F11 로 봉투가 바뀐 둘도 같은 가드에 넣는다 — 새 필드를 설명에
            # 적으면서 이름을 틀리는 것이 정확히 B-2 에서 일어난 일이다.
            "vinc_merge_candidates": lambda: q.suggest_merge_candidates(owner="u",
                                                                        store=store),
            "vinc_merge_reject": lambda: q.reject_merge_pair(
                owner="u", a_id="concept:alpha", b_id="concept:beta", store=store),
        }
        def _returns_block(text):
            """`Returns {` 부터 짝이 맞는 `}` 까지. 중첩이 있어 정규식으로는 못 뜬다."""
            i = text.find("Returns {")
            if i < 0:
                return None
            i += len("Returns ")
            depth = 0
            for j in range(i, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[i + 1:j]
            return None

        by_name = {t["name"]: t for t in _tool_specs()}
        try:
            for name, call in calls.items():
                desc = by_name[name]["description"]
                block = _returns_block(desc)
                self.assertIsNotNone(block, f"{name}: 설명에 Returns 절이 없다")
                # 중첩 {...} 를 걷어내고 남은 top-level 키 이름만 취한다.
                flat = block
                while "{" in flat:
                    new_flat = re.sub(r"\[?\{[^{}]*\}\]?", "", flat)
                    if new_flat == flat:
                        break
                    flat = new_flat
                claimed = {k.strip().rstrip(":") for k in flat.split(",")
                           if re.fullmatch(r"[a-z_]+:?", k.strip())}
                self.assertTrue(claimed, f"{name}: Returns 절에서 키를 못 뽑았다")
                got = call()
                missing = sorted(k for k in claimed if k not in got)
                self.assertFalse(missing,
                                 f"{name}: 설명이 주장한 키 {missing} 가 실제 반환에 없다 "
                                 f"(실제: {sorted(got)})")
        finally:
            store.close()

    def test_neighbors_sees_attachments_not_just_concepts(self):
        """F14 — `vinc_neighbors` 가 Record·Episode·Bridge 를 통째로 못 봤다.

        neighbors_plan 의 패턴이 `(n:Concept)-[r]-(m:Concept)` 라 다른 라벨이
        **필터가 아니라 매치 모양에서** 빠졌다. 라이브 실측: concept:vinc-mcp 의
        1-hop 이웃 15개 중 7개(RELATED_TO)만 보였고, 빠진 8개가 하필 사실이 사는
        곳(Record 6 · Episode 1 · Bridge 1)이었다. 도구 설명은 "graph 대신
        neighbors 로 한 홉씩 걸으라"고 권하므로 그 조언을 따르면 Record 를 영영
        못 본다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("hub", "other"):
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='infra'", id=cid)
            s.run("MATCH (a:Concept{concept_id:'hub'}),(b:Concept{concept_id:'other'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
            s.run("MERGE (r:Record {record_id:'record:fact-1'}) SET r.owner_id='u', "
                  "r.title='붙은 사실', r.domain='infra'")
            s.run("MATCH (r:Record{record_id:'record:fact-1'}),(c:Concept{concept_id:'hub'}) "
                  "MERGE (r)-[:ATTACHES_TO {type:'relates_to'}]->(c)")
            s.run("MERGE (e:Episode {episode_id:'episode:2026-01-01-x'}) SET e.owner_id='u', "
                  "e.title='일어난 일', e.domain='infra', e.date='2026-01-01'")
            s.run("MATCH (e:Episode{episode_id:'episode:2026-01-01-x'}),"
                  "(c:Concept{concept_id:'hub'}) MERGE (e)-[:ABOUT]->(c)")

        nb = q.neighbors("hub", owner="u", store=store)
        store.close()
        ids = {n["id"] for n in nb["neighbors"]}
        self.assertIn("other", ids, "개념 축이 사라지면 안 된다")
        self.assertIn("record:fact-1", ids, "Record 부착이 안 보인다(F14)")
        self.assertIn("episode:2026-01-01-x", ids, "Episode 부착이 안 보인다(F14)")
        self.assertEqual(nb["count"], 3)
        self.assertEqual(nb["by_rel"].get("ATTACHES_TO"), 1)
        self.assertEqual(nb["by_rel"].get("ABOUT"), 1)

    def test_neighbors_attachments_respect_owner_scope(self):
        """SEC-1 — 부착 축을 새로 열면서 남의 Record 가 새어 나오면 안 된다."""
        from store.config import load_config
        from store.backend import open_store
        from api import query as q
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            s.run("MERGE (c:Concept {concept_id:'shared'}) SET c.owner_id='a', "
                  "c.title='shared', c.domain='infra'")
            for owner, rid in (("a", "record:mine"), ("b", "record:theirs")):
                s.run("MERGE (r:Record {record_id:$rid}) SET r.owner_id=$o, "
                      "r.title=$rid, r.domain='infra'", rid=rid, o=owner)
                s.run("MATCH (r:Record{record_id:$rid}),(c:Concept{concept_id:'shared'}) "
                      "MERGE (r)-[:ATTACHES_TO {type:'relates_to'}]->(c)", rid=rid)

        nb = q.neighbors("shared", owner="a", store=store)
        store.close()
        ids = {n["id"] for n in nb["neighbors"]}
        self.assertIn("record:mine", ids)
        self.assertNotIn("record:theirs", ids, "다른 소유자의 Record 가 샜다")

    def test_scope_filter_isolates_owners(self):
        """스코프 1차(§1) — owner 필터로 다른 계정 데이터 미혼입."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_ingest import merge_episode
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

    def test_purge_owner_deletes_only_that_owner(self):
        """계정 삭제 캐스케이드(§5.9) — owner purge 가 **그 소유만** 지우고 다른
        owner 는 온전. 소유 스코프 삭제이므로 SEC-1/SEC-2 격리가 전제 — 실엔진
        2-소유자로 '한쪽만 지워지는가'를 직접 본다(스텁으론 못 잡는 층)."""
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_ingest import merge_episode
        from api import query as q
        from api import fragment as frag

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        base = {"date": "2026-07-04", "tenant_id": "tenant:local", "workspace": "",
                "mode": "", "summary": "", "completed_items": [], "patterns": [],
                "source_file": ""}
        with store.session() as s:
            merge_episode(s, {**base, "episode_id": "ea", "title": "A",
                              "owner_id": "user:alice", "domain": "d"})
            merge_episode(s, {**base, "episode_id": "eb", "title": "B",
                              "owner_id": "user:bob", "domain": "d"})
        # 개념은 원격 쓰기 표면(MCP fragment)으로 소유 스탬핑 — apply_delta 와 동경로.
        frag.merge_fragment({"owner": "user:alice",
                             "nodes": [{"id": "concept:a", "type": "concept", "title": "CA"}]},
                            store=store)
        frag.merge_fragment({"owner": "user:bob",
                             "nodes": [{"id": "concept:b", "type": "concept", "title": "CB"}]},
                            store=store)

        out = q.purge_owner("user:alice", store=store)
        self.assertTrue(out["executed"], out)
        self.assertEqual(out["deleted"]["Episode"], 1)
        self.assertEqual(out["deleted"]["Concept"], 1)

        def _count(label: str, owner: str) -> int:
            r = store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c",
                          {"o": owner})
            return int(r[0]["c"]) if r else -1

        # alice 는 전멸, bob 은 온전
        self.assertEqual(_count("Episode", "user:alice"), 0, "alice 에피소드가 남음")
        self.assertEqual(_count("Concept", "user:alice"), 0, "alice 개념이 남음")
        self.assertEqual(_count("Episode", "user:bob"), 1, "bob 에피소드가 함께 지워짐 — 격리 위반")
        self.assertEqual(_count("Concept", "user:bob"), 1, "bob 개념이 함께 지워짐 — 격리 위반")

        # 멱등 — 다시 purge 해도 0건, 에러 없음
        again = q.purge_owner("user:alice", store=store)
        self.assertTrue(again["executed"])
        self.assertEqual(again["total"], 0)
        store.close()

    def test_open_layer_typed_merge_semantics(self):
        """F1 — 핵심 Kùzu 가정 검증: rel-속성이 MERGE 패턴에 참여하면 같은 type 은
        멱등, 다른 type 은 병렬 엣지. + fragment 경로의 Entity/TYPED·revstamp 왕복."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        fragment = {
            "nodes": [{"id": "policy:p1", "type": "policy", "title": "P1"},
                      {"id": "policy:p2", "type": "policy", "title": "P2"}],
            "edges": [{"from": "policy:p1", "to": "policy:p2", "type": "blocks"}],
            "owner": "user:alice",
        }
        r1 = frag.merge_fragment(fragment, store=store)
        self.assertEqual(r1["merged"]["nodes"]["created"], 2, r1)
        r2 = frag.merge_fragment(fragment, store=store)   # 멱등 재적재
        self.assertEqual(r2["merged"]["nodes"], {"created": 0, "matched": 2})
        frag.merge_fragment({**fragment, "edges": [
            {"from": "policy:p1", "to": "policy:p2", "type": "enables"}]}, store=store)
        rows = store.run("MATCH (:Entity)-[r:TYPED]->(:Entity) RETURN count(r) AS c")
        self.assertEqual(int(rows[0]["c"]), 2)   # blocks(멱등) + enables(병렬)
        rev = store.run("MATCH (n:Entity) WHERE n.entity_id = 'policy:p1' "
                        "RETURN n.rev AS r")
        self.assertEqual(int(rev[0]["r"]), 1)    # 동일 재적재 무스탬프
        store.close()

    def test_bridge_domain_field_warns_not_crashes_batch(self):
        """실측 회귀(2026-07-21, vinc_ingest MCP 실사용 중 발견): Bridge 는 domain
        열이 없다(source_domain/target_domain 대신) — 예전엔 여기서 Binder 예외가
        나머지 배치(다른 노드 전부 + 모든 엣지)를 통째로 날렸다. 이제 그 노드만
        경고로 스킵하고 나머지는 정상 처리돼야 한다(merge_fragment 의 문서화 계약
        "malformed nodes... skipped, reported in warnings with partial:true")."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        fragment = {
            "nodes": [
                {"id": "concept:c1", "type": "concept", "title": "C1"},
                {"id": "bridge:b1", "type": "bridge", "title": "B1", "domain": "cs"},
            ],
            "edges": [{"from": "bridge:b1", "to": "concept:c1", "type": "connects_via"}],
            "owner": "user:alice",
        }
        r = frag.merge_fragment(fragment, store=store)
        self.assertTrue(r["executed"])
        # 배치 계속 진행 확인 — concept 는 살아있고, bridge 도 domain 필드만 빼고 생성됨.
        self.assertEqual(r["merged"]["nodes"]["created"], 2, r)
        self.assertTrue(any("Bridge" in w and "domain" in w for w in r["warnings"]), r["warnings"])
        # 엣지도 처리됨(배치가 노드 단계에서 죽지 않았다는 증거).
        self.assertEqual(r["merged"]["edges"]["created"], 1, r)
        rows = store.run("MATCH (b:Bridge) WHERE b.bridge_id = 'bridge:b1' "
                         "RETURN b.title AS title")
        self.assertEqual(rows[0]["title"], "B1")   # 나머지 필드는 정상 반영
        store.close()

    def test_about_edge_roundtrip_and_graph_visibility(self):
        """F4/F2 — about 적재 → graph(owner, domain) 축 합류로 에피소드 노드·ABOUT
        엣지가 렌더되고 개념 degree 에 반영된다(브릿지 팔 과소 카운트의 동형 회귀)."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        frag.merge_fragment({
            "nodes": [{"id": "episode:e1", "type": "episode", "title": "E1", "domain": "d"},
                      {"id": "concept:c1", "type": "concept", "title": "C1", "domain": "d"}],
            "edges": [{"from": "episode:e1", "to": "concept:c1", "type": "about"}],
            "owner": "user:alice",
        }, store=store)
        res = q.graph(owner="user:alice", domain="d", store=store)
        by_id = {n["id"]: n for n in res["nodes"]}
        self.assertIn("episode:e1", by_id, res)
        self.assertEqual(by_id["episode:e1"]["kind"], "episode")
        self.assertEqual(by_id["concept:c1"]["degree"], 1)
        self.assertTrue(any(e["type"] == "ABOUT" for e in res["edges"]))
        store.close()

    def test_bridge_owner_scoping_2owner_with_legacy(self):
        """F5 — alice/bob 브릿지 + 레거시 '' 브릿지: view_l0(alice) 의 Bridge 결합에
        bob 것은 없고, 자기 것 + 레거시(과도기 술어)는 있다. 실엔진 2-소유자 검증
        ([[vinc-owner-isolation-sec1]] — 스텁 아님)."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag
        from api import query as q

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)

        def _mk(owner: str, bid: str) -> None:
            frag.merge_fragment({"nodes": [{"id": bid, "type": "bridge", "title": bid}],
                                 "owner": owner}, store=store)
            store.run("MATCH (b:Bridge) WHERE b.bridge_id = $id "
                      "SET b.source_domain = 'm', b.target_domain = 'p'", {"id": bid})

        _mk("user:alice", "bridge:a")
        _mk("user:bob", "bridge:b")
        store.run("MERGE (b:Bridge {bridge_id:'bridge:legacy'}) "
                  "SET b.source_domain='m', b.target_domain='p', b.owner_id=''")
        res = q.view_l0(owner="user:alice", store=store)
        ties = {(t["source"], t["target"]): t["ties"] for t in res["ties"]}
        self.assertEqual(ties.get(("m", "p")), 2, res)   # alice + legacy, bob 제외
        store.close()

    def test_edge_tombstone_roundtrip(self):
        """S4 확장 실엔진 — 신규 엣지의 끝점 스탬프 → delete_edge(삭제+표식) →
        delta export 에 edge_tombstones → 재생성이 표식을 상쇄."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag
        from api import query as q
        from api import sync

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        frag.merge_fragment({
            "nodes": [{"id": "concept:ea", "type": "concept", "title": "A", "domain": "d"},
                      {"id": "concept:eb", "type": "concept", "title": "B", "domain": "d"}],
            "edges": [{"from": "concept:ea", "to": "concept:eb", "type": "relates_to"}],
            "owner": "user:alice",
        }, store=store)
        seq = store.run("MATCH (n:Concept) WHERE n.concept_id = 'concept:ea' "
                        "RETURN n.sync_seq AS s")
        self.assertGreater(int(seq[0]["s"]), 0)     # 엣지 생성이 끝점을 스탬프
        res = q.delete_edge("concept:ea", "relates_to", "concept:eb",
                            owner="user:alice", store=store)
        self.assertTrue(res["found"], res)
        left = store.run("MATCH (:Concept)-[r:RELATED_TO]->(:Concept) RETURN count(r) AS c")
        self.assertEqual(int(left[0]["c"]), 0)
        ts = store.run("MATCH (t:_EdgeTombstone) RETURN count(t) AS c")
        self.assertEqual(int(ts[0]["c"]), 1)
        out = sync.export_delta(owner="user:alice", since=0, domains=["d"], store=store)
        self.assertEqual(len(out["edge_tombstones"]), 1, out)
        self.assertEqual(out["edge_tombstones"][0]["src"], "concept:ea")
        # 재생성 → 로컬 표식 상쇄(낡은 삭제가 재전파되지 않는다)
        frag.merge_fragment({
            "nodes": [{"id": "concept:ea", "type": "concept", "title": "A"},
                      {"id": "concept:eb", "type": "concept", "title": "B"}],
            "edges": [{"from": "concept:ea", "to": "concept:eb", "type": "relates_to"}],
            "owner": "user:alice",
        }, store=store)
        ts2 = store.run("MATCH (t:_EdgeTombstone) RETURN count(t) AS c")
        self.assertEqual(int(ts2[0]["c"]), 0)
        store.close()

    def test_migrate_records_schema_version_4(self):
        """v4 마이그레이션 — provision(=migrate) 후 _SchemaMeta 가 4 를 기록하고
        Bridge.owner_id(v2)·관계 evidence/confidence(v3, E-G6)·Chunk 소유+오프셋
        (v4, D45 Chunk=SSoT) 가 동작한다(신규 설치는 base DDL, 구 설치는 ALTER 수렴)."""
        from store.config import load_config
        from store.backend import open_store

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                         "RETURN m.value AS v")
        # 리터럴로 못박는다(파생 금지) — 버전을 올릴 때 이 줄이 실패해야 "그 버전이
        # 요구하는 데이터/DDL 스텝을 실제로 넣었나"를 한 번 보게 된다.
        self.assertEqual(rows[0]["v"], "13")   # v13 — 결정을 주제 계층에(E-1 보정)
        # v12 — Decision 노드 테이블 + **개념과 같은 자리**의 끝점 쌍이 실동작한다.
        # 신규 설치는 base DDL 이, 구 설치는 ALTER ADD FROM/TO 가 같은 형태로 수렴.
        store.run("MERGE (d:Decision {decision_id:'decision:t1'}) "
                  "SET d.owner_id='user:x', d.title='T', d.domain='vinc/design'")
        store.run("MERGE (c:Concept {concept_id:'concept:t1'}) SET c.owner_id='user:x'")
        store.run("MATCH (a:Decision{decision_id:'decision:t1'}), "
                  "(b:Concept{concept_id:'concept:t1'}) "
                  "MERGE (a)-[r:ABOUT]->(b) SET r.evidence='왜 이 값인가'")
        ab = store.run("MATCH (:Decision)-[r:ABOUT]->(:Concept) RETURN r.evidence AS e")
        self.assertEqual(ab[0]["e"], "왜 이 값인가",
                         "Decision→Concept ABOUT 쌍이 안 열렸다 — v12 ALTER 누락")
        store.run("MERGE (d2:Decision {decision_id:'decision:t2'}) SET d2.owner_id='user:x'")
        store.run("MATCH (a:Decision{decision_id:'decision:t1'}), "
                  "(b:Decision{decision_id:'decision:t2'}) "
                  "MERGE (a)-[:DERIVES_FROM]->(b)")
        lin = store.run("MATCH (:Decision)-[:DERIVES_FROM]->(:Decision) RETURN count(*) AS c")
        self.assertEqual(int(lin[0]["c"]), 1, "결정 계보(Decision→Decision) 쌍 누락")
        # v4 — Chunk 소유·오프셋 열 + _DocMeta 레지스트리가 실동작한다.
        store.run("MERGE (c:Chunk {chunk_id:'k0'}) SET c.owner_id='user:x', "
                  "c.char_start=0, c.char_end=5, c.text='hello', c.doc_id='d0'")
        got = store.run("MATCH (c:Chunk) WHERE c.owner_id='user:x' "
                        "RETURN c.char_start AS cs, c.char_end AS ce")
        self.assertEqual((int(got[0]["cs"]), int(got[0]["ce"])), (0, 5))
        # v5 — _DocMeta.sync_seq + _DocTombstone(문서 단위 동기화)가 실동작한다.
        store.run("MERGE (d:_DocMeta {doc_id:'d0'}) SET d.owner_id='user:x', "
                  "d.content_hash='h', d.chunk_count=1, d.sync_seq=3")
        dm = store.run("MATCH (d:_DocMeta) RETURN d.content_hash AS h, d.sync_seq AS s")
        self.assertEqual(dm[0]["h"], "h")
        self.assertEqual(int(dm[0]["s"]), 3)
        store.run("MERGE (t:_DocTombstone {doc_id:'d9'}) SET t.owner_id='user:x', "
                  "t.deleted_at='2026', t.sync_seq=4")
        ts = store.run("MATCH (t:_DocTombstone) RETURN count(t) AS c")
        self.assertEqual(int(ts[0]["c"]), 1)
        store.run("MERGE (b:Bridge {bridge_id:'b0'}) SET b.owner_id = 'user:x'")
        got = store.run("MATCH (b:Bridge) WHERE b.owner_id = 'user:x' RETURN count(b) AS c")
        self.assertEqual(int(got[0]["c"]), 1)
        # E-G6 — 관계 증거·신뢰도 열이 존재해 SET/조회가 동작한다.
        for cid in ("x", "y"):
            store.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u'", {"id": cid})
        store.run("MATCH (a:Concept{concept_id:'x'}),(b:Concept{concept_id:'y'}) "
                  "MERGE (a)-[r:RELATED_TO]->(b) SET r.evidence='원문', r.confidence=0.75")
        ev = store.run("MATCH ()-[r:RELATED_TO]->() "
                       "RETURN r.evidence AS e, r.confidence AS c")
        self.assertEqual(ev[0]["e"], "원문")
        self.assertAlmostEqual(float(ev[0]["c"]), 0.75)
        store.close()

    def test_hub_pull_delta_serves_data_without_local_domain_selection(self):
        """CRITICAL 회귀(실측 확정, 2026-07-21) — "계정 키 넣고 동기화해도 안
        불러와짐"의 근본원인. `read_sync_pull_delta` 는 허브(공유 멀티테넌트 스토어)
        에서 호출되는데, `sync.export_delta` 의 `domains` 미지정 폴백
        (`get_selected_domains`)은 **로컬 디바이스의 Settings 도메인 체크박스**용
        전역(owner 미분리) 메타키라 허브에선 항상 빈 값 → 모든 pull 이 결정론적으로
        빈 번들이었다. 이 테스트는 도메인 선택을 **한 번도 설정하지 않은** 스토어
        에서(허브의 실제 상태와 동일) pull 이 실제 데이터를 반환하는지 검증한다."""
        from store.config import load_config
        from store.backend import open_store
        from api import fragment as frag
        from api.server import VincApp

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        # get_selected_domains 를 세팅하는 코드를 의도적으로 전혀 부르지 않는다 —
        # 허브엔 그걸 채우는 라우트가 없다는 실제 조건을 그대로 재현.
        frag.merge_fragment({
            "nodes": [{"id": "concept:pulltest", "type": "concept", "title": "Pull Test",
                      "domain": "cs"}],
            "owner": "user:alice",
        }, store=store)

        app = VincApp(store=store)
        code, out = app.read_sync_pull_delta({"owner": "user:alice", "since": "0"})
        self.assertEqual(code, 200, out)
        self.assertTrue(out.get("executed"), out)
        ids = {n["id"] for n in (out.get("nodes") or [])}
        self.assertIn("concept:pulltest", ids,
                       "허브 pull 이 로컬전용 도메인선택 폴백에 걸려 빈 번들을 반환함 — 회귀")
        store.close()


if __name__ == "__main__":
    unittest.main()
