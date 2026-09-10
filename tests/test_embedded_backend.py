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
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import pkns  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pkseed import _seed  # noqa: E402  (스토어 직접 시드용 — tests/_pkseed.py)


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

        def exists(nid, owner="user:A"):
            r = store.run("MATCH (c:Concept) WHERE c.concept_id = $id RETURN count(c) AS c",
                          {"id": pkns.key(owner, nid)})
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
        row = store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                        "RETURN c.owner_id AS o, c.title AS t",
                        {"i": pkns.key("user:B", "concept:bonly")})[0]
        self.assertEqual(row["o"], "user:B")
        self.assertNotEqual(row["t"], "HIJACK")
        # ⚠ 플래그가 켜지면 이 경고가 **없는 것이 맞다**: A 의 쓰기는 자기 물리 키로
        # 가므로 B 의 행과 부딪치지 않는다(감사 H1 을 없애는 것이 design A 의 목적).
        # 위 두 축(B 의 owner·title 불변)은 두 모드에서 똑같이 성립해야 한다.
        if not pkns.enabled():
            self.assertTrue(any("another tenant" in str(w)
                                for w in (out.get("warnings") or [])))

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
            # `_Session.run` 은 kwargs 다(dict 아님).
            _seed(s.run, "MATCH (e:Episode {episode_id:$i}) "
                  "SET e.pinned=true, e.access_count=10",
                  i=pkns.key("u", "pinned"))
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
                _seed(s.run, f"MERGE (n:Concept {{concept_id:'{cid}'}}) "
                      f"SET n.title='{cid}', n.owner_id='u', n.domain='{dom}'")
            _seed(s.run, "MATCH (a:Concept{concept_id:'a'}), (b:Concept{concept_id:'b'}) "
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
            _seed(s.run, "MERGE (a:Concept {concept_id:'c1'}) SET a.title='A'")
            _seed(s.run, "MERGE (b:Concept {concept_id:'c2'}) SET b.title='B'")
            for rel in ("PREREQUISITE_OF", "DERIVES_FROM", "RELATED_TO"):
                _seed(s.run, f"MATCH (a:Concept {{concept_id:'c1'}}), "
                      f"(b:Concept {{concept_id:'c2'}}) MERGE (a)-[:{rel}]->(b)")
            _seed(s.run, "MERGE (br:Bridge {bridge_id:'br1'}) SET br.title='X'")
            _seed(s.run, "MERGE (d:Domain {name:'art'})")
            _seed(s.run, "MATCH (br:Bridge {bridge_id:'br1'}), (d:Domain {name:'art'}) "
                  "MERGE (br)-[:CROSSES]->(d)")
            _seed(s.run, "MATCH (br:Bridge {bridge_id:'br1'}), (c:Concept {concept_id:'c1'}) "
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

        pref.tag("t1", tags=["billing", "kr"], owner="u")
        pref.tag("t1", tags=["kr", "refund"], owner="u")   # kr 중복 — 합집합, 미중복
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.title=$t, "
                      "n.owner_id='u'", id=cid, t=cid)
            # 삼각 사이클 a→b→c→a (WALK 라면 깊은 depth 서 경로 폭증)
            for src, dst in (("a", "b"), ("b", "c"), ("c", "a")):
                _seed(s.run, "MATCH (x:Concept{concept_id:$s}),(y:Concept{concept_id:$t}) "
                      "MERGE (x)-[:RELATED_TO]->(y)", s=src, t=dst)
        store.close()

        # owner 를 실어야 물리 키를 짚는다(PK 네임스페이싱). 프로덕션의 MCP 표면도
        # 항상 owner 를 넘긴다.
        r = q.decide("a", depth=4, owner="u")
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
                _seed(s.run, "MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t", id=cid, t=txt)
                _seed(s.run, "MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
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
                _seed(s.run, "MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t, c.owner_id=$o",
                      id=cid, t=txt, o=own)
                _seed(s.run, "MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
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
                _seed(s.run, "MERGE (c:Chunk {chunk_id:$id}) SET c.text=$t", id=cid, t=txt)
                _seed(s.run, "MERGE (v:ChunkVec {vec_id:$vid}) SET v.chunk_id=$id, "
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
            _seed(s.run, "MERGE (c:Chunk {chunk_id:'ch1'}) SET c.text=$t, c.embedding=$e, "
                  "c.owner_id='u', c.source_file='/abs/topo.md'",
                  t="refund policy and dispute resolution", e=[1.0, 0.1, 0.0, 0.0])
            # 개념 gauge: title 이 'gauge' 라 쿼리 'refund' 와 title 매치 안 됨.
            # 그러나 source_file basename(topo.md) 이 청크와 같아 MENTIONS 로 이어진다.
            _seed(s.run, "MERGE (c:Concept {concept_id:'gauge'}) SET c.title='gauge', "
                  "c.owner_id='u', c.domain='physics', c.source_file='physics/topo.md'")
            # 이웃 개념 duality: gauge 와 RELATED_TO — 1-hop 확산 대상.
            _seed(s.run, "MERGE (c:Concept {concept_id:'duality'}) SET c.title='duality', "
                  "c.owner_id='u', c.domain='physics', c.source_file='physics/x.md'")
            _seed(s.run, "MATCH (a:Concept{concept_id:'gauge'}),(b:Concept{concept_id:'duality'}) "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            # a-b, a-c (a 의 이웃 2개), 경로 a..d: a-b-...-d 없음; a-c-e-d 구성
            for x, y in (("a", "b"), ("a", "c"), ("c", "e"), ("e", "d")):
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            for x, y, conf in (("a", "b", 0.9), ("b", "c", 0.9), ("c", "z", 0.9),
                               ("a", "y", 0.2), ("y", "z", 0.2)):
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='m'", id=cid)
            # 사이클 포함(시드 복귀 경로 존재) — TRAIL 이 시드를 행으로 내던 조건
            for x, y in (("s", "n1"), ("n1", "n2"), ("n2", "s"),
                         ("n2", "n3"), ("n3", "n4")):
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)

        def reach(sem: str) -> set:
            cy = ("MATCH (n:Concept) WHERE n.concept_id = $s AND n.owner_id = 'u' "
                  f"MATCH (n)-[* {sem} 1..3]-(m:Concept) WHERE m.owner_id = 'u' "
                  "RETURN DISTINCT m.concept_id AS id")
            return {r["id"] for r in store.run(cy, {"s": pkns.key("u", "s")})}

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
                _seed(s.run, "MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', c.title=$t, "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            # 컴포넌트1: a-b-c 삼각, 컴포넌트2: d-e, 고아: lone
            for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("d", "e")):
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            for x, y in (("a", "b"), ("b", "c"), ("c", "a"), ("d", "e")):
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
                      "MERGE (p)-[:RELATED_TO]->(t)", x=x, y=y)
            # 'a' 를 두 번, 'b' 를 한 번 언급 — 대표 순위는 a > b > c(무언급, 사전순).
            for i, target in enumerate(("a", "a", "b")):
                _seed(s.run, "MERGE (ch:Chunk {chunk_id:$cid}) SET ch.owner_id='u', "
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
                _seed(s.run, "MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='math'", id=f"n{i}")
            # 완전그래프(클리크) — 사슬은 Louvain 이 모듈성 최적화로 반으로 쪼갠다
            # (실측: 8노드 사슬 → 4+4). 클리크는 내부가 최대로 밀집해 하나로 남는다.
            for i in range(n):
                for j in range(i + 1, n):
                    _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
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
                _seed(s.run, "MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='math'", id=f"n{i}")
            for i in range(n - 1):               # 사슬 하나 = 컴포넌트 하나
                _seed(s.run, "MATCH (p:Concept{concept_id:$x}),(t:Concept{concept_id:$y}) "
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
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$id, n.domain='math'", id=cid)
            _seed(s.run, "MATCH (p:Concept{concept_id:'alpha'}),(t:Concept{concept_id:'beta'}) "
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
                _seed(s.run, "MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$id, c.domain='infra'", id=cid)
            _seed(s.run, "MATCH (a:Concept{concept_id:'hub'}),(b:Concept{concept_id:'other'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
            _seed(s.run, "MERGE (r:Record {record_id:'record:fact-1'}) SET r.owner_id='u', "
                  "r.title='붙은 사실', r.domain='infra'")
            _seed(s.run, "MATCH (r:Record{record_id:'record:fact-1'}),(c:Concept{concept_id:'hub'}) "
                  "MERGE (r)-[:ATTACHES_TO {type:'relates_to'}]->(c)")
            _seed(s.run, "MERGE (e:Episode {episode_id:'episode:2026-01-01-x'}) SET e.owner_id='u', "
                  "e.title='일어난 일', e.domain='infra', e.date='2026-01-01'")
            _seed(s.run, "MATCH (e:Episode{episode_id:'episode:2026-01-01-x'}),"
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
        # 이 픽스처는 소유자가 둘이라 `_seed` 의 단일 owner 로는 안 된다 — 노드마다
        # 자기 소유자로 합성한 물리 키를 직접 바인딩한다. 제목은 논리 슬러그로 남긴다.
        shared = pkns.key("a", "shared")
        with store.session() as s:
            s.run("MERGE (c:Concept {concept_id:$i}) SET c.owner_id='a', "
                  "c.title='shared', c.domain='infra'", i=shared)
            for owner, rid in (("a", "record:mine"), ("b", "record:theirs")):
                key = pkns.key(owner, rid)
                s.run("MERGE (r:Record {record_id:$rid}) SET r.owner_id=$o, "
                      "r.title=$t, r.domain='infra'", rid=key, o=owner, t=rid)
                s.run("MATCH (r:Record{record_id:$rid}),(c:Concept{concept_id:$c}) "
                      "MERGE (r)-[:ATTACHES_TO {type:'relates_to'}]->(c)",
                      rid=key, c=shared)

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
        rev = store.run("MATCH (n:Entity) WHERE n.entity_id = $i "
                        "RETURN n.rev AS r",
                        {"i": pkns.key("user:alice", "policy:p1")})
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
        rows = store.run("MATCH (b:Bridge) WHERE b.bridge_id = $i "
                         "RETURN b.title AS title",
                         {"i": pkns.key("user:alice", "bridge:b1")})
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
                      "SET b.source_domain = 'm', b.target_domain = 'p'",
                      {"id": pkns.key(owner, bid)})

        _mk("user:alice", "bridge:a")
        _mk("user:bob", "bridge:b")
        # ⚠ 레거시 행은 owner 가 비어 있다 — PR-D 이관 전 상태이므로 PK 도 논리
        # 슬러그로 남는 것이 맞다(`pkns.key` 는 owner 가 비면 합성하지 않는다).
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
        seq = store.run("MATCH (n:Concept) WHERE n.concept_id = $i "
                        "RETURN n.sync_seq AS s",
                        {"i": pkns.key("user:alice", "concept:ea")})
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
        self.assertEqual(rows[0]["v"], "19")   # v19 — 도메인 계층 백필 재실행
        # v17 — 미러는 **부팅 DDL 이 만든다**(ALTER 도 백필도 없다). 실제로 서는지
        # 여기서 확인한다: 이 가정이 틀리면 채우기가 'Table NodeVec does not exist' 로
        # 죽는다(2026-09-09 에 실제로 물렸다).
        # v18 — 키가 `vec_id`(소유자+id)다. 같은 논리 id 가 스페이스마다 살기 때문이고,
        # `node_id` 를 키로 두던 동안 소유자 여섯인 그래프에서 재구축이 네 번 죽었다.
        store.run("CREATE (:NodeVec {vec_id:'user:x#concept:t1', node_id:'concept:t1', "
                  "kind:'concept', owner_id:'user:x', title:'T', text:'T. 한 줄 설명', "
                  f"model:'m', embedding:{[0.0] * cfg.embedding_dim}}})")
        mir = store.run("MATCH (n:NodeVec) RETURN n.title AS t, n.text AS x")
        self.assertEqual(mir[0]["t"], "T")
        self.assertNotEqual(mir[0]["t"], mir[0]["x"],
                            "라벨(title)과 찾는 표면(text)은 다른 열이어야 한다")
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
        # v15 — Record 유효구간 열이 존재해 SET/조회가 동작한다(W13).
        store.run("MERGE (r:Record {record_id:'record:v15'}) SET r.owner_id='user:x', "
                  "r.valid_from='2026-01', r.valid_until='2026-08'")
        vv = store.run("MATCH (r:Record) WHERE r.record_id='record:v15' "
                       "RETURN r.valid_from AS vf, r.valid_until AS vu")
        self.assertEqual((vv[0]["vf"], vv[0]["vu"]), ("2026-01", "2026-08"))
        # v16 — ATTACHES_TO 근거·신뢰도 열이 존재해 SET/조회가 동작한다(E-G6 확장).
        store.run("MATCH (r:Record {record_id:'record:v15'}), "
                  "(c:Concept {concept_id:'x'}) "
                  "MERGE (r)-[a:ATTACHES_TO]->(c) "
                  "SET a.type='supports', a.evidence='근거 문장', a.confidence=0.8")
        av = store.run("MATCH (:Record)-[a:ATTACHES_TO]->(:Concept) "
                       "RETURN a.evidence AS e, a.confidence AS c")
        self.assertEqual(av[0]["e"], "근거 문장")
        self.assertAlmostEqual(float(av[0]["c"]), 0.8)
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


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class TwoStoreTimeRoundtrip(unittest.TestCase):
    """기기 둘이 **같은 노드의 출생과 내용 시점에 합의하는가**(D72 · 2026-08-17).

    이 클래스가 스텁이 아니라 실엔진 두 벌인 이유: 이 결함은 플랜 층에서 안 보였다.
    export 가 열을 안 싣는다는 사실은 cypher 문자열을 봐야 보이고, 그 결과(수신측이
    자기 시각으로 출생을 다시 찍음)는 **두 스토어를 실제로 왕복시켜야** 드러난다.
    수정 전 실측: 같은 노드가 A 에서 …44.334Z · B 에서 …45.296Z(호출 사이 벽시계 차).
    """

    OWNER = "user:alice"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # ⚠ `MEMORY_DATA_DIR` 도 **반드시** 여기 든다: `_store()` 가 매번 덮어쓰는데
        # 복원 목록에서 빠지면, 이 클래스가 끝난 뒤 그 변수가 지워진 임시 경로를
        # 가리킨 채 남아 **뒤에 도는 다른 파일의 테스트가 깨진다**(실측 — 단독으로는
        # 통과하고 전체 스위트에서만 test_server 둘이 죽었다).
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "a")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        self.a = self._store("a")
        self.b = self._store("b")

    def _store(self, tag):
        from store.config import load_config
        from store.backend import open_store
        os.environ["MEMORY_DATA_DIR"] = os.path.join(self._tmp, tag)
        cfg = load_config()
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        return st

    def tearDown(self):
        for st in (self.a, self.b):
            try:
                st.close()
            except Exception:
                pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _row(self, store, cid):
        # 쓰기는 `merge_fragment`·`apply_delta` 로 갔고 읽기는 스토어 직접이다 —
        # PK 네임스페이싱이 켜지면 노드 PK 가 물리 키라 여기서도 합성해야 한다.
        rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                         "RETURN c.created AS created, c.content_time AS ct",
                         {"i": pkns.key(self.OWNER, cid)})
        return rows[0] if rows else {}

    #: A 가 실어 보내는 출생 — 못박는다(아래 docstring).
    BORN = "2019-01-01T00:00:00.000Z"

    def test_both_devices_agree_on_birth_and_content_time(self):
        """번들이 출생·내용시점을 싣고 **수신측이 자기 시각 대신 그것을 받는가**(D72).

        A 의 출생을 **못박는다** — 벽시계에 기대면 안 된다. min 화해
        (`api/sync._reconcile_birth`)는 수신측을 min(자기값, 들은값)으로 **내릴** 뿐
        발신측을 올리지 않으므로, "둘이 같다"는 A 의 스탬프가 B 의 것보다 이를 때만
        참인데 두 스토어의 HLC 는 그 순서를 보장하지 않는다(2026-08-23 CI 에서 14ms
        역전 · 판정은
        [[record:main-ci-red-before-the-roster-merge-2026-08-23]] 와 거기 걸린
        `decision:birth-agreement-needs-a-full-session`). 양방향 수렴은
        §4 의 일회성 되감기가 하지 이 한 방향 apply 가 아니다.

        값을 못박으면 벽시계가 빠지고 검사할 질문만 남는다: 실패는 곧 B 가 자기
        시각을 찍었다는 뜻이고 그것이 D72 의 재발이다."""
        from api import fragment as frag
        from api import sync

        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:relativity", "type": "concept", "title": "Relativity",
             "domain": "phys", "content_time": "1905", "created": self.BORN}]},
            store=self.a)
        bundle = sync.export_delta(owner=self.OWNER, since=0, domains=["phys"],
                                   store=self.a)
        res = sync.apply_delta(owner=self.OWNER, bundle=bundle, store=self.b)
        self.assertTrue(res["executed"], res)

        a, b = self._row(self.a, "concept:relativity"), self._row(self.b, "concept:relativity")
        self.assertEqual(a["created"], self.BORN, "A 가 실은 출생을 잃었다")
        self.assertEqual(b["created"], self.BORN,
                         "출생이 기기를 못 건넜다 — B 가 자기 시각을 찍었다")
        self.assertEqual(b["ct"], "1905", "내용 시점이 기기를 못 건넜다")

    def test_birth_takes_the_earliest_even_when_the_local_row_is_older(self):
        """min 규칙 — 수신측이 더 이른 출생을 갖고 있으면 그것을 지킨다(역방향)."""
        from api import fragment as frag
        from api import sync

        for st in (self.a, self.b):
            frag.merge_fragment({"owner": self.OWNER, "nodes": [
                {"id": "concept:c", "type": "concept", "title": "C", "domain": "phys"}]},
                store=st)
        # B 가 훨씬 이른 출생을 갖게 만든다(다른 경로로 먼저 알았던 노드).
        self.b.run("MATCH (c:Concept) WHERE c.concept_id=$i "
                   "SET c.created = '2019-01-01T00:00:00.000Z'",
                   {"i": pkns.key(self.OWNER, "concept:c")})
        # A 에서 내용을 고쳐 delta 를 만든다.
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:c", "type": "concept", "title": "C 고침", "domain": "phys"}]},
            store=self.a)
        bundle = sync.export_delta(owner=self.OWNER, since=0, domains=["phys"], store=self.a)
        sync.apply_delta(owner=self.OWNER, bundle=bundle, store=self.b)
        self.assertEqual(self._row(self.b, "concept:c")["created"],
                         "2019-01-01T00:00:00.000Z",
                         "늦은 원격 출생이 이른 로컬 출생을 덮었다")

    def test_birth_converges_even_when_the_merge_changes_nothing(self):
        """필드 변화가 없어 쓰기가 안 일어나는 경로(`skipped_stale`)에서도 화해한다.

        여기서 건너뛰면 이미 갈라진 출생이 영영 만나지 못한다 — 이 결함의 잔존 형태다."""
        from api import fragment as frag
        from api import sync

        for st in (self.a, self.b):
            frag.merge_fragment({"owner": self.OWNER, "nodes": [
                {"id": "concept:same", "type": "concept", "title": "S", "domain": "phys"}]},
                store=st)
        self.b.run("MATCH (c:Concept) WHERE c.concept_id=$i "
                   "SET c.created = '2099-01-01T00:00:00.000Z'",
                   {"i": pkns.key(self.OWNER, "concept:same")})   # 갈라진 상태
        bundle = sync.export_delta(owner=self.OWNER, since=0, domains=["phys"], store=self.a)
        out = sync.apply_delta(owner=self.OWNER, bundle=bundle, store=self.b)
        self.assertEqual(out["merged"], 0, "이 시나리오는 필드 변화가 없어야 한다")
        self.assertEqual(self._row(self.b, "concept:same")["created"],
                         self._row(self.a, "concept:same")["created"])

    # ── §4 이미 갈라진 출생의 일회성 화해 ────────────────────────────────────
    class _InProcessHub:
        """상대 스토어를 허브처럼 쓰는 remote 어댑터(HTTP 없이 같은 코드 경로)."""

        def __init__(self, store, owner, domains):
            self.store, self.owner, self.domains = store, owner, domains

        def pull(self, since):
            from api import sync
            return sync.export_delta(owner=self.owner, since=since,
                                     domains=self.domains, store=self.store)

        def push(self, chunks):
            from api import sync
            for c in chunks:
                sync.apply_delta(owner=self.owner, bundle=c, store=self.store)
            return {"ok": True}

    def _diverged_pair(self, a_born, b_born):
        """수정 이전 상태를 만든다 — 같은 노드, 기기마다 다른 생일."""
        from api import fragment as frag
        for st in (self.a, self.b):
            frag.merge_fragment({"owner": self.OWNER, "nodes": [
                {"id": "concept:old", "type": "concept", "title": "Old", "domain": "phys"}]},
                store=st)
            from api import sync
            sync.set_selected_domains(st, ["phys"])
        old = pkns.key(self.OWNER, "concept:old")
        self.a.run("MATCH (c:Concept) WHERE c.concept_id=$i SET c.created=$v",
                   {"i": old, "v": a_born})
        self.b.run("MATCH (c:Concept) WHERE c.concept_id=$i SET c.created=$v",
                   {"i": old, "v": b_born})

    def test_oneshot_resync_converges_a_birth_that_diverged_before_the_fix(self):
        """delta 는 안 변한 노드를 안 나른다 — 그래서 한 번은 전량이 오가야 한다.

        A 가 **더 이른** 값을 갖는 방향으로 짠다: pull 만 되감아서는 안 되고 push 까지
        돌아야 허브가 그것을 듣는다는 것을 이 방향만이 드러낸다."""
        from api import sync
        self._diverged_pair("2019-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z")
        # 이미 한참 동기화한 기기를 흉내 낸다(커서가 앞서 있다).
        sync.set_cursor(self.a, "push", 999)
        sync.set_cursor(self.a, "pull", 999)

        out = sync.run(self.a, None, owner=self.OWNER,
                       remote=self._InProcessHub(self.b, self.OWNER, ["phys"]))
        self.assertTrue(out["birth_resync"], "일회성 되감기가 안 돌았다")
        self.assertEqual(self._row(self.a, "concept:old")["created"],
                         "2019-01-01T00:00:00.000Z")
        self.assertEqual(self._row(self.b, "concept:old")["created"],
                         "2019-01-01T00:00:00.000Z", "허브가 더 이른 출생을 못 들었다")

    def test_without_the_oneshot_the_divergence_survives(self):
        """§4 가 왜 필요한지의 대우 — 표식이 이미 찍힌 기기는 delta 만 돌고,
        안 변한 노드는 실리지 않으므로 갈라진 채 남는다."""
        from api import sync
        from api import revstamp
        self._diverged_pair("2019-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z")
        revstamp._meta_set(self.a, sync._ONESHOT_KEY, "done")
        sync.set_cursor(self.a, "push", 999)
        sync.set_cursor(self.a, "pull", 999)

        out = sync.run(self.a, None, owner=self.OWNER,
                       remote=self._InProcessHub(self.b, self.OWNER, ["phys"]))
        self.assertFalse(out["birth_resync"])
        self.assertEqual(self._row(self.b, "concept:old")["created"],
                         "2099-01-01T00:00:00.000Z")

    def test_records_and_their_typed_edges_cross_to_the_other_device(self):
        """기록(Record)이 기기 사이를 오가는가, 그리고 **원래 관계 타입이 살아남는가**.

        Record 는 라이브 실측 277건으로 노드 종류 중 최대인데 `_SYNC_TYPES` 에 없어
        통째로 안 넘어갔다. 급소는 노드가 아니라 **엣지**다: Record 참여 엣지는 전부
        `ATTACHES_TO` 한 라벨로 저장되고 원 타입은 `r.type` 에 실린다. 라벨을 역맵에
        태우면 네 갈래가 한 이름으로 뭉개져 수신측이 되살릴 수 없다 — 그래서
        `_record_edges_plan` 이 `r.type` 을 fragment 타입으로 돌려준다.

        `derives_from` 을 고르는 이유: `relates_to` 는 무방향이라 뭉개져도 티가 안
        난다. 방향이 있는 갈래여야 타입이 실제로 보존됐는지 갈린다."""
        from api import fragment as frag
        from api import sync
        # sync.run 은 **로컬의 선택 도메인**을 읽는다(get_selected_domains) — 안 세우면
        # "nothing leaves the device" 로 정상 종료해 아무것도 안 나간다.
        sync.set_selected_domains(self.a, ["phys"])
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:target", "type": "concept", "title": "T", "domain": "phys"},
            {"id": "record:measured", "type": "record", "title": "재 봤다",
             "domain": "phys", "content_time": "1905",
             "props": {"note": "0.3% 아래로 유지됐다"}},
        ], "edges": [
            {"from": "record:measured", "to": "concept:target", "type": "derives_from",
             "evidence": "그 값은 대상에서 나왔다"},
        ]}, store=self.a)

        out = sync.run(self.a, None, owner=self.OWNER,
                       remote=self._InProcessHub(self.b, self.OWNER, ["phys"]))
        self.assertTrue(out["executed"])

        rid = pkns.key(self.OWNER, "record:measured")
        got = self.b.run("MATCH (r:Record) WHERE r.record_id = $i "
                         "RETURN r.title AS t, r.domain AS d, r.props AS p, "
                         "r.content_time AS ct, r.created AS born", {"i": rid})
        self.assertTrue(got, "기록이 반대편 기기에 아예 없다")
        self.assertEqual(got[0]["t"], "재 봤다")
        self.assertEqual(got[0]["d"], "phys")
        self.assertEqual(got[0]["ct"], "1905")          # Record 도 두 시간열을 갖는다
        self.assertTrue(got[0]["born"], "출생이 안 실렸다 — _BIRTH_LABELS 확인")
        self.assertIn("0.3%", got[0]["p"] or "")        # props 왕복

        edge = self.b.run(
            "MATCH (a:Record)-[e:ATTACHES_TO]->(b:Concept) "
            "WHERE a.record_id = $i RETURN e.type AS t", {"i": rid})
        self.assertTrue(edge, "엣지가 안 넘어왔다")
        self.assertEqual(edge[0]["t"], "derives_from",
                         "원 타입이 ATTACHES_TO 라벨로 뭉개졌다")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EdgeBirth(unittest.TestCase):
    """v14 — 관계에도 출생이 있는가(세계선 M2 의 전제).

    실엔진인 이유: `ON CREATE SET` 의 의미(재-MERGE 에서 안 뜀)는 엔진이 정하는 것이라
    스텁으로는 검사할 수 없다. 그리고 다섯 갈래(TYPED·CONNECTS·ABOUT·CC·ATTACHES_TO)가
    **전부** 찍는지는 실제로 넣어 봐야 안다 — 하나라도 빠지면 재생이 절름발이가 된다.
    """

    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    _NODES = [
        {"id": "concept:a", "type": "concept", "title": "A", "domain": "d"},
        {"id": "concept:b", "type": "concept", "title": "B", "domain": "d"},
        {"id": "decision:d1", "type": "decision", "title": "D", "domain": "d"},
        {"id": "record:r1", "type": "record", "title": "R", "domain": "d"},
        {"id": "bridge:br", "type": "bridge", "title": "BR"},
        {"id": "theory:t1", "type": "theory", "title": "T1"},
        {"id": "theory:t2", "type": "theory", "title": "T2"},
    ]
    _EDGES = [
        {"from": "concept:a", "to": "concept:b", "type": "relates_to"},
        {"from": "decision:d1", "to": "concept:a", "type": "about"},
        {"from": "record:r1", "to": "concept:a", "type": "derives_from"},
        {"from": "bridge:br", "to": "concept:a", "type": "connects_via", "role": "x"},
        {"from": "theory:t1", "to": "theory:t2", "type": "implies"},
    ]

    def _births(self, rel):
        return [r["c"] for r in
                self.store.run(f"MATCH ()-[r:{rel}]->() RETURN r.created AS c")]

    def test_every_authored_relation_gets_a_birth(self):
        from api import fragment as frag
        res = frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES,
                                   "edges": self._EDGES}, store=self.store)
        self.assertEqual(res["merged"]["edges"]["created"], 5, res.get("warnings"))
        for rel in ("RELATED_TO", "ABOUT", "ATTACHES_TO", "CONNECTS", "TYPED"):
            got = self._births(rel)
            self.assertTrue(got and all(got), f"{rel} 에 출생이 안 찍혔다: {got}")
        # 한 배치는 **한 순간**이다 — 엣지마다 HLC 를 새로 뽑으면 한 번의 적재가
        # 시간축에 흩어져 보이고 메타 왕복도 엣지 수만큼 는다.
        all_births = {b for rel in ("RELATED_TO", "ABOUT", "ATTACHES_TO", "CONNECTS", "TYPED")
                      for b in self._births(rel)}
        self.assertEqual(len(all_births), 1, f"한 배치가 여러 시각을 썼다: {all_births}")

    def test_remerge_never_moves_an_edge_birth(self):
        """`ON CREATE SET` 의 의미가 이것이다 — 출생은 처음 이어진 때다."""
        from api import fragment as frag
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES,
                             "edges": self._EDGES}, store=self.store)
        first = self._births("RELATED_TO")[0]
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES,
                             "edges": self._EDGES}, store=self.store)
        self.assertEqual(self._births("RELATED_TO")[0], first)
        # 상류가 다른 값을 실어 다시 보내도(팩 재-import) 안 바뀐다
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES,
                             "edges": [dict(self._EDGES[0],
                                            created="1999-01-01T00:00:00.000Z")]},
                            store=self.store)
        self.assertEqual(self._births("RELATED_TO")[0], first)

    def test_a_supplied_birth_is_taken_on_a_new_edge(self):
        """동기화·팩이 원본의 출생을 나른다 — 신규일 때는 그것을 쓴다."""
        from api import fragment as frag
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES[:2],
                             "edges": [{"from": "concept:a", "to": "concept:b",
                                        "type": "relates_to",
                                        "created": "2019-05-05T00:00:00.000Z"}]},
                            store=self.store)
        self.assertEqual(self._births("RELATED_TO"), ["2019-05-05T00:00:00.000Z"])

    def test_legacy_edges_are_not_backfilled(self):
        """구 엣지는 '' 로 남는다. 모르는 시간을 지어내면 모든 옛 관계가 오늘 태어난
        것이 되고, 그 순간 시간축은 '마이그레이션한 날' 밴드 하나가 된다(v7 과 같은 규율)."""
        from api import fragment as frag
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES[:2],
                             "edges": [self._EDGES[0]]}, store=self.store)
        self.store.run("MATCH ()-[r:RELATED_TO]->() SET r.created = ''")   # 구 행 흉내
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES[:2],
                             "edges": [self._EDGES[0]]}, store=self.store)
        self.assertEqual(self._births("RELATED_TO"), [""])

    def test_edge_birth_survives_a_sync_hop(self):
        """수신 기기에서 엣지가 **그 기기 시각으로 다시 태어나면** 안 된다 —
        노드에서 방금 고친 것과 같은 병이다(e489186)."""
        from api import fragment as frag
        from api import sync
        frag.merge_fragment({"owner": self.OWNER, "nodes": self._NODES[:2],
                             "edges": [self._EDGES[0]]}, store=self.store)
        born = self._births("RELATED_TO")[0]
        bundle = sync.export_delta(owner=self.OWNER, since=0, domains=["d"],
                                   store=self.store)
        self.assertTrue(bundle["edges"], "번들에 엣지가 없다")
        self.assertEqual(bundle["edges"][0].get("created"), born,
                         "번들이 엣지 출생을 안 실었다")

        os.environ["MEMORY_DATA_DIR"] = os.path.join(self._tmp, "b")
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        b = open_store(cfg)
        b.provision(dim=cfg.embedding_dim)
        try:
            sync.apply_delta(owner=self.OWNER, bundle=bundle, store=b)
            got = [r["c"] for r in
                   b.run("MATCH ()-[r:RELATED_TO]->() RETURN r.created AS c")]
            self.assertEqual(got, [born], "수신 기기에서 엣지가 다시 태어났다")
        finally:
            b.close()


class IsolatedSeedFields(unittest.TestCase):
    """고립 노드(연결 0)를 `graph(seed=...)` 로 읽으면 실린 값이 그대로 오는가.

    실엔진인 이유: 이 경로는 순회가 **빈 결과**를 냈을 때만 도는 폴백이라(C4),
    스텁으로는 그 갈래에 도달하지 못한다.

    무엇을 막는가 — 이 폴백은 값을 조회하지 않고 `domain/tags/props/ts` 를 전부
    빈 값으로 **지어내고** 있었다. 저장은 멀쩡한데 읽기가 비어 보이니, 그래프에
    근거를 옮긴 뒤 읽어서 확인하는 절차([[vinc.canon.code-annotation]] §5)가 거꾸로
    "쓰기가 조용히 실패했다"는 오진을 만든다. 2026-08-18 에 실제로 두 번 그랬다.
    """

    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_isolated_record_keeps_its_fields(self):
        from api import fragment as frag
        from api import query as q
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "record:lone", "type": "record", "title": "Lone", "domain": "d",
             "tags": ["fact"], "props": {"note": "the argument lives here"},
             "content_time": "2026-08-18"}]}, store=self.store)
        res = q.graph(owner=self.OWNER, seed="record:lone", store=self.store)
        self.assertTrue(res.get("found"))
        node = next(n for n in res["nodes"] if n["id"] == "record:lone")
        self.assertEqual(node["degree"], 0, "연결이 없어야 이 갈래를 지난다")
        self.assertEqual(node["domain"], "d")
        self.assertEqual(node["tags"], ["fact"])
        self.assertEqual(node["props"].get("note"), "the argument lives here")
        self.assertEqual(node["content_ts"], "2026-08-18")

    def test_isolated_entity_has_no_domain_column_and_does_not_crash(self):
        """개방층은 열 구성이 다르다 — 없는 열을 물으면 Binder 예외로 조회가 죽는다."""
        from api import fragment as frag
        from api import query as q
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "theory:lone", "type": "theory", "title": "T", "tags": ["x"]}]},
            store=self.store)
        res = q.graph(owner=self.OWNER, seed="theory:lone", store=self.store)
        self.assertTrue(res.get("found"))
        node = next(n for n in res["nodes"] if n["id"] == "theory:lone")
        self.assertEqual(node["tags"], ["x"])
        self.assertEqual(node["domain"], "")
