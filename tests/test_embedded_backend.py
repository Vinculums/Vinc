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
        from engines.recall_ingest import merge_episode
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
            s.run("MERGE (c:Chunk {chunk_id:'ch1'}) SET c.text=$t, c.embedding=$e, "
                  "c.source_file='/abs/topo.md'",
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
        """F5 — alice/bob 브릿지 + 레거시 '' 브릿지: atlas_l0(alice) 의 Bridge 결합에
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
        res = q.atlas_l0(owner="user:alice", store=store)
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

    def test_migrate_records_schema_version_3(self):
        """v3 마이그레이션 — provision(=migrate) 후 _SchemaMeta 가 3 을 기록하고
        Bridge.owner_id(v2)·관계 evidence/confidence(v3, E-G6) 가 동작한다(신규
        설치는 base DDL, 구 설치는 ALTER 수렴)."""
        from store.config import load_config
        from store.backend import open_store

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                         "RETURN m.value AS v")
        self.assertEqual(rows[0]["v"], "3")
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


if __name__ == "__main__":
    unittest.main()
