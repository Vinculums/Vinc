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
# 재큐 정책은 api 층의 것이라 store 가 import 하지 않는다 — 호출부가 넘긴다(VIN-242).
from api import docstore as ds                # noqa: E402


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

    def test_왕복이_엣지의_출생을_보존한다(self):
        """출생 열(`created`, v14)을 가진 관계 전부 — 백업 복원·기기 이관·재구축이 이 왕복을
        탄다. 이관 명세가 그 열을 빠뜨려서 엣지의 출생이 **오류 없이** 비어 돌아왔다.
        관계마다 명세의 첫 쌍으로 엣지 하나를 만들어 본다(라벨 목록을 손으로 적지 않는다)."""
        import tempfile
        from store import embedded_schema as es
        pks = {lbl: spec["pk"] for lbl, spec in es.node_table_specs().items()}
        born = {}
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/src"})
        src = open_store(cfg)
        src.provision(dim=cfg.embedding_dim)
        for i, rel in enumerate(es.EDGE_BIRTH_RELS):
            spec = next(s for s in es.rel_table_specs() if s["label"] == rel)
            a, b = spec["from"], spec["to"]
            aid, bid = f"{a.lower()}:{rel.lower()}-a", f"{b.lower()}:{rel.lower()}-b"
            born[rel] = f"2026-01-{i + 1:02d}T00:00:00Z"
            src.run(f"MERGE (x:{a} {{{pks[a]}: $i}})", {"i": aid})
            src.run(f"MERGE (y:{b} {{{pks[b]}: $i}})", {"i": bid})
            src.run(f"MATCH (x:{a} {{{pks[a]}: $a}}), (y:{b} {{{pks[b]}: $b}}) "
                    f"CREATE (x)-[:{rel} {{created: $c}}]->(y)",
                    {"a": aid, "b": bid, "c": born[rel]})
        dump = migrate.export_store(src)
        src.close()
        dumped = {e["label"]: e["props"].get("created") for e in dump["edges"]}
        self.assertEqual({r: dumped.get(r) for r in born}, born, "덤프에 출생이 없다")

        cfg2 = load_config({"data_dir": tempfile.mkdtemp() + "/dst"})
        dst = open_store(cfg2)
        migrate.import_store(dst, dump, dim=cfg2.embedding_dim)
        got = {}
        for rel in born:
            rows = dst.run(f"MATCH ()-[r:{rel}]->() RETURN r.created AS c")
            got[rel] = rows[0]["c"] if rows else None
        dst.close()
        self.assertEqual(got, born, "재적재본에서 엣지의 출생이 비었다")


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
        out = migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
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
            migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
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
            migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
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

    def test_재구축이_노드_미러_벡터도_버린다(self):
        """NodeVec(v17)은 리허설 뒤에 생긴 표다. 옛 차원의 행이 덤프에 남으면 새 열에
        넣을 때 행마다 캐스팅 예외가 난다 — ChunkVec 과 같은 이유로 버리고, 기동이
        전량 재구축한다."""
        src, _ = self._mkstore()
        self._seed_docs(src, 2)
        src.run("CREATE (:NodeVec {vec_id:'u#concept:x', node_id:'concept:x', kind:'concept', "
                "owner_id:'u', title:'X', text:'X', model:'e5', embedding:$e})",
                {"e": [0.0] * 768})
        dst, _ = self._mkstore(dim=1024)
        try:
            out = migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
        except RuntimeError as exc:
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        finally:
            src.close()
        self.assertEqual(out["node_vectors_dropped"], 1)
        self.assertEqual(int(dst.run("MATCH (v:NodeVec) RETURN count(v) AS c")[0]["c"]), 0)
        self.assertEqual(migrate.vector_dim(dst, "NodeVec"), 1024)
        dst.close()

    def test_차원_이행_훅은_다를_때만_재구축해_갈아_끼운다(self):
        """허브 기동의 `migrate-dim`. 768 스토어에 1024 모델을 대면 새 파일로 재구축해
        자리에 놓고 옛것은 `.pre-768` 로 남긴다. 같은 차원이면 손대지 않는다(멱등)."""
        import os
        import tempfile
        from unittest import mock
        src, cfg = self._mkstore()                    # 768
        self._seed_docs(src, 3)
        path = cfg.data_dir
        src.close()
        snap = tempfile.mkdtemp() + "/snap"           # 영속 볼륨 자리(다른 디렉터리)
        try:
            with mock.patch.dict(os.environ, {"VINC_STORE_SNAPSHOT": snap}):
                out = migrate.migrate_dim_if_needed(path, model="BAAI/bge-m3", target_dim=1024, requeue=ds.switch_embed_model)
        except RuntimeError as exc:
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        self.assertTrue(out["changed"])
        self.assertEqual(out["previous_dim"], 768)
        self.assertEqual((out["docs"], out["requeued"]), (3, 3))
        self.assertTrue(os.path.exists(out["kept"]), "옛 스토어가 되돌림용으로 남아야 한다")
        # 2026-09-11: `kept` 는 컨테이너 로컬이라 리비전이 바뀌면 사라진다. 볼륨에도 있어야 한다.
        self.assertEqual(out.get("kept_on_volume"), f"{snap}.pre-768",
                         out.get("kept_on_volume_error"))
        self.assertTrue(os.path.exists(f"{snap}.pre-768"), "되돌림 사본이 볼륨에 없다")
        st = open_store(load_config({"data_dir": path, "embedding_model": "BAAI/bge-m3"}))
        try:
            self.assertEqual(migrate.vector_dim(st, "ChunkVec"), 1024)
            n = st.run("MATCH (d:_DocMeta) RETURN count(d) AS c")[0]["c"]
            self.assertEqual(int(n), 3, "문서가 재구축에서 사라졌다")
            from api import docstore as ds
            self.assertEqual(ds.embed_model_state(st)["stored"], "BAAI/bge-m3",
                             "워커가 같은 이름을 들고 와야 재큐를 두 번 하지 않는다")
        finally:
            st.close()
        again = migrate.migrate_dim_if_needed(path, model="BAAI/bge-m3", target_dim=1024, requeue=ds.switch_embed_model)
        self.assertFalse(again["changed"], "차원이 같으면 아무것도 안 해야 한다(멱등)")

    def _seed_v17_source(self, store):
        """노드 미러가 v18 이전 모양인 원본: NodeVec 의 pk 가 `node_id` 이고 `vec_id` 가 없다.
        모양은 커밋 03c4167 직전 스키마 그대로 적는다(현재 명세에서 파생하면 역사가 흐른다).
        문서 3건 · 개념 2개와 엣지 1개 · 옛 모양의 미러 행 1개를 싣는다."""
        from store import pkns
        self._seed_docs(store, 3)
        a, b = pkns.key("user:a", "concept:a"), pkns.key("user:a", "concept:b")
        for k in (a, b):
            store.run("MERGE (c:Concept {concept_id: $k}) SET c.owner_id = 'user:a', "
                      "c.title = 'X'", {"k": k})
        store.run("MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
                  "MERGE (a)-[:RELATED_TO]->(b)", {"a": a, "b": b})
        store.drop_node_vector_index()
        store.run("DROP TABLE NodeVec")
        store.run("CREATE NODE TABLE NodeVec(node_id STRING, kind STRING, owner_id STRING, "
                  "title STRING, text STRING, model STRING, embedding FLOAT[768], "
                  "PRIMARY KEY(node_id))")
        store.run("CREATE (:NodeVec {node_id:'concept:a', kind:'concept', owner_id:'user:a', "
                  "title:'X', text:'X', model:'e5', embedding:$e})", {"e": [0.0] * 768})
        store.run("MERGE (m:_SchemaMeta {key:'schema_version'}) SET m.value = '17'")
        return a, b

    def _assert_rebuilt_intact(self, store, a, b):
        n = store.run("MATCH (c:Concept) RETURN count(c) AS c")[0]["c"]
        self.assertEqual(int(n), 2, "개념이 재구축에서 사라졌다")
        e = store.run("MATCH (x:Concept {concept_id: $a})-[r:RELATED_TO]->"
                      "(y:Concept {concept_id: $b}) RETURN count(r) AS c", {"a": a, "b": b})
        self.assertEqual(int(e[0]["c"]), 1, "엣지가 재구축에서 사라졌다")
        d = store.run("MATCH (d:_DocMeta) RETURN count(d) AS c")[0]["c"]
        self.assertEqual(int(d), 3, "_DocMeta 가 재구축에서 사라졌다")
        self.assertEqual(migrate.vector_dim(store, "NodeVec"), 1024)

    def test_v17_노드_미러를_가진_원본도_재구축된다(self):
        """🔴 원본의 NodeVec 이 v17 모양이면 export 가 `vec_id` 로 투영하다 깨져 아무것도
        옮기기 전에 멈췄다. 그 표는 재구축이 어차피 버리는 표다.
        근거: [[record:rebuild-store-fails-on-a-store-whose-node-mirror-predates-v18-2026-09-13]]"""
        src, _ = self._mkstore()
        a, b = self._seed_v17_source(src)
        dst, _ = self._mkstore(dim=1024)
        try:
            out = migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
        except RuntimeError as exc:
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        finally:
            src.close()
        try:
            self.assertEqual((out["docs"], out["requeued"]), (3, 3))
            self.assertEqual(out["node_vectors_dropped"], 1)
            self._assert_rebuilt_intact(dst, a, b)
        finally:
            dst.close()

    def test_차원_이행_훅이_v17_노드_미러_원본을_읽기만_하고_갈아_끼운다(self):
        """부팅 훅은 원본을 마이그레이션하지 않고 연다. 그 원본이 되돌림 사본(`.pre-768`)이
        되므로 재구축은 원본에 쓰지 않아야 하고, 사본은 v17 모양 그대로 남아야 한다."""
        import os
        import tempfile
        from unittest import mock
        src, cfg = self._mkstore()
        a, b = self._seed_v17_source(src)
        path = cfg.data_dir
        src.close()
        try:
            with mock.patch.dict(os.environ, {"VINC_STORE_SNAPSHOT": tempfile.mkdtemp() + "/s"}):
                out = migrate.migrate_dim_if_needed(path, model="BAAI/bge-m3", target_dim=1024, requeue=ds.switch_embed_model)
        except RuntimeError as exc:
            if "확장" in str(exc) or "extension" in str(exc).lower():
                self.skipTest(f"FTS 확장 미가용 — {exc}")
            raise
        self.assertTrue(out["changed"])
        self.assertEqual((out["docs"], out["requeued"]), (3, 3))
        st = open_store(load_config({"data_dir": path, "embedding_model": "BAAI/bge-m3"}))
        try:
            self._assert_rebuilt_intact(st, a, b)
        finally:
            st.close()
        kept = open_store(load_config({"data_dir": out["kept"]}))
        try:
            v = kept.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' RETURN m.value AS v")
            self.assertEqual(v[0]["v"], "17", "되돌림 사본이 마이그레이션됐다")
            self.assertFalse(kept._node_vec_rekeyed(), "되돌림 사본의 미러가 재키잉됐다")
        finally:
            kept.close()

    def test_네임스페이스_스토어의_재구축은_물리_키를_지킨다(self):
        """🔴 2026-09-11 라이브 사고의 회귀 시험. 플래그가 켜진 런타임에서 export 는 읽기
        경로를 지나며 물리 키(owner U+001F id)를 논리 id 로 벗기고, import 가 그것을 그대로
        써서 정확 id 조회가 전부 죽었다. 같은 논리 id 를 가진 소유자 둘은 한 행으로 접혔다.
        재구축은 raw 로 읽어야 하고, 두 소유자의 행이 각자의 물리 키로 다시 찾아져야 한다."""
        import os
        from unittest import mock
        from store import pkns
        with mock.patch.dict(os.environ, {"VINC_PK_NAMESPACED": "on"}):
            src, _ = self._mkstore()
            for o in ("user:a", "user:b"):
                src.run("MERGE (c:Concept {concept_id: $k}) SET c.owner_id = $o, c.title = 'X'",
                        {"k": pkns.key(o, "concept:x"), "o": o})
            dst, _ = self._mkstore(dim=1024)
            try:
                migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
            except RuntimeError as exc:
                if "확장" in str(exc) or "extension" in str(exc).lower():
                    self.skipTest(f"FTS 확장 미가용 — {exc}")
                raise
            finally:
                src.close()
            rows = dst.run("MATCH (c:Concept) WHERE c.title = 'X' RETURN c.owner_id AS o")
            self.assertEqual(len(rows), 2, "두 소유자의 같은 논리 id 가 한 행으로 접혔다")
            for o in ("user:a", "user:b"):
                hit = dst.run("MATCH (c:Concept {concept_id: $k}) RETURN c.owner_id AS o",
                              {"k": pkns.key(o, "concept:x")})
                self.assertEqual([r["o"] for r in hit], [o], f"{o} 의 물리 키로 정확 조회가 안 된다")
            dst.close()

    def test_네임스페이스_런타임에서_논리_키_스토어는_재구축을_거절한다(self):
        """벗겨진 키를 가진 스토어를 그대로 옮기면 같은 고장을 새 파일에 복제한다. 멈춘다."""
        import os
        from unittest import mock
        # fragment 가 `pkns.key` 로 합성하는 라벨은 여섯이다 — Bridge·Entity 도 든다
        # (`api/fragment.py` `_OWNER_SCOPED_LABELS`). 넷만 보던 가드가 그 둘을 놓쳤다.
        for label, pk in (("Concept", "concept_id"), ("Bridge", "bridge_id"),
                          ("Entity", "entity_id")):
            with self.subTest(label=label):
                src, _ = self._mkstore()
                src.run(f"MERGE (c:{label} {{{pk}: '{label.lower()}:bare'}}) "
                        "SET c.owner_id = 'user:a', c.title = 'B'")
                dst, _ = self._mkstore(dim=1024)
                with mock.patch.dict(os.environ, {"VINC_PK_NAMESPACED": "on"}):
                    with self.assertRaises(RuntimeError) as ctx:
                        migrate.rebuild_store(src, dst, model="BAAI/bge-m3", dim=1024, requeue=ds.switch_embed_model)
                self.assertIn("migrate_pk_namespacing", str(ctx.exception))
                self.assertIn(label, str(ctx.exception))
                src.close()
                dst.close()


class SchemaLagExportTests(unittest.TestCase):
    """스토어가 코드보다 낮은 스키마일 때 export 가 **행을 잃지 않는다**.

    🔴 이 손실은 오류를 내지 않는다. 2026-09-02 실측: schema_version 14 스토어를
    v16 코드로 export 하니 Record 가 뒤에 얻은 `valid_from` 때문에 전량 투영이
    깨졌고, `except Exception: rows = []` 가 그것을 삼켜 **Record 2행이 0행으로**
    덤프됐다. 백업 복원·기기 이관·재구축이 모두 이 함수를 타므로, 낮은 스키마의
    사본을 되살리면 라벨 하나가 통째로 빈 채 "성공"한다.
    """

    def _mkstore(self):
        import tempfile
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        return st, cfg

    def test_열이_모자란_라벨도_행을_잃지_않는다(self):
        st, _cfg = self._mkstore()
        try:
            with st.session() as s:
                s.run("MERGE (n:Record {record_id:'r:1'}) SET n.title='보존', "
                      "n.owner_id='u', n.valid_from='2026-01-01'")
            # 낮은 스키마를 흉내낸다 — 코드는 이 열을 알고 스토어는 모르는 상태.
            st.run("ALTER TABLE Record DROP valid_from")
            dump = migrate.export_store(st)
            rows = dump["nodes"].get("Record") or []
            self.assertEqual(len(rows), 1, "열 하나가 없다고 행이 사라지면 안 된다")
            self.assertEqual(rows[0]["record_id"], "r:1")
            self.assertEqual(rows[0]["title"], "보존")
            self.assertNotIn("valid_from", rows[0], "없는 열은 덤프에도 없다")
        finally:
            st.close()

    def test_출생_열이_없는_관계도_엣지를_잃지_않는다(self):
        """v14 이전 스토어에는 관계의 출생 열(`created`)이 없다. 명세가 그 열을 싣게 된
        뒤에도 그런 스토어의 엣지는 좁혀서 덤프된다 — 열 하나 때문에 엣지가 빠지면 안 된다."""
        st, _cfg = self._mkstore()
        try:
            with st.session() as s:
                s.run("MERGE (a:Concept {concept_id:'c:a'}) SET a.owner_id='u'")
                s.run("MERGE (b:Concept {concept_id:'c:b'}) SET b.owner_id='u'")
                s.run("MATCH (a:Concept{concept_id:'c:a'}),(b:Concept{concept_id:'c:b'}) "
                      "CREATE (a)-[:RELATED_TO {evidence: '근거'}]->(b)")
            st.run("ALTER TABLE RELATED_TO DROP created")
            dump = migrate.export_store(st)
            rels = [e for e in dump["edges"] if e["label"] == "RELATED_TO"]
            self.assertEqual(len(rels), 1, "출생 열이 없다고 엣지가 사라지면 안 된다")
            self.assertEqual(rels[0]["props"].get("evidence"), "근거")
            self.assertNotIn("created", rels[0]["props"], "없는 열은 덤프에도 없다")
        finally:
            st.close()

    def test_미프로비저닝_스토어는_그대로_빈_집합이다(self):
        """좁히기가 손실만 막고 기존 동작은 안 바꾼다 — 없는 테이블은 여전히 빈 집합.

        (라벨 하나만 지우는 방식은 못 쓴다: Record 는 ATTACHES_TO 가 참조해서
        DROP TABLE 이 거절된다. 아예 프로비저닝하지 않은 스토어가 이 경우의 실물이다.)
        """
        import tempfile
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        st = open_store(cfg)                      # provision 하지 않는다
        try:
            dump = migrate.export_store(st)
            self.assertEqual(dump["nodes"].get("Record"), [])
            self.assertEqual(dump["nodes"].get("Concept"), [])
            self.assertEqual(dump["edges"], [])
        finally:
            st.close()


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
