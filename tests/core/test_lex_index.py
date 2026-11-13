# -*- coding: utf-8 -*-
"""v21 — 전문 색인을 한글 두 글자 묶음 열(`Chunk.lex`)로 옮기는 일.

설계: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02]] · 근거: [[vinc.research.korean-retrieval-2026-10-01]]

지키는 것:
  ① 분석기는 순수하고, 색인 쪽과 질의 쪽이 같은 함수다(`store.lexical`).
  ② 표지(`lex_index = ready`)가 서기 전에는 색인도 질의도 오늘처럼 `text` 다.
  ③ 이관은 FTS 만 내리고(HNSW 그대로) 빈 `lex` 를 채운 뒤 새 열로 색인을 만들고 표지를 세운다.
     벡터 수가 그대로이고 의미 검색이 살아 있어야 한다. 두 번째 실행은 아무것도 안 한다.
  ④ 기본(`defer`)은 서비스 중에 이관하지 않는다. `background` 에서만 기동 뒤 잡으로 돌고,
     그동안 `/health` 는 스토어를 읽지 않고 `migrating` 을 싣는다.

엔진 시험은 로컬(ladybug 0.18)과 CI(0.20.4) 양쪽에서 돈다. 엔진 동작(FTS 만 내린 채 기존 행
SET · HNSW 생존)은 CI 판이 증명한다 — 로컬 초록만으로 허브 엔진을 말하지 않는다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from store.embedded_schema import LEX_INDEX_KEY, migration_statements  # noqa: E402
from store.lexical import coverage, lex_text, lex_tokens  # noqa: E402

_DRIVER, _NAME = import_driver()

_DOC = ("# 당일전략\n\n12월에 베팅하는 건 굉장히 드물고 하수라서, 실제 승부는 10월·11월에 난다.\n\n"
        "The quarter end rebalancing drives institutions.\n")


class LexicalTests(unittest.TestCase):
    def test_한글은_겹치는_두_글자다(self):
        self.assertEqual(lex_text("승부는"), "승부 부는")
        self.assertEqual(lex_text("하수라서"), "하수 수라 라서")

    def test_한_글자_낱말은_그대로다(self):
        self.assertEqual(lex_text("안 산다"), "안 산다")

    def test_영어는_낱말_그대로_소문자다(self):
        self.assertEqual(lex_text("Quarter END"), "quarter end")

    def test_섞인_낱말은_갈라진다(self):
        self.assertEqual(lex_text("AI가"), "ai 가")
        self.assertEqual(lex_text("10월·11월"), "10 월 11 월")

    def test_조사가_달라도_두_글자가_겹친다(self):
        # 이것이 고치려는 결함이다: 색인이 `승부는` 을 통째로 담으면 `승부` 는 못 맞춘다.
        self.assertIn("승부", lex_tokens("승부는"))
        self.assertIn("승부", lex_tokens("승부를"))

    def test_v20_은_lex_열만_더한다(self):
        self.assertIn("ALTER TABLE Chunk ADD lex STRING DEFAULT ''", migration_statements(20))
        self.assertNotIn("ALTER TABLE Chunk ADD lex STRING DEFAULT ''", migration_statements(21))

    def test_덮음은_질의_토큰의_비율이다(self):
        q = lex_tokens("승부 하수")
        self.assertEqual(coverage(q, lex_text(_DOC)), 1.0)
        self.assertEqual(coverage(q, lex_text("quarter end")), 0.0)
        self.assertEqual(coverage(set(), "아무"), 0.0)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class LexIndexEngineTests(unittest.TestCase):
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
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ids(self, word):
        return [r["id"] for r in self.store.fulltext_search(word, 10)]

    def _as_pre_v21(self, doc_id):
        """표지 전의 스토어를 만든다 — `lex` 가 빈 옛 행과 `text` 위의 FTS, 그리고 HNSW."""
        self.store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k DELETE m", {"k": LEX_INDEX_KEY})
        self.store._lex_ready = None
        self.store.drop_search_indexes()
        # 색인이 없을 때만 기존 Chunk 행에 SET 할 수 있다(색인이 걸린 채면 프로세스가 죽는다).
        self.store.run("MATCH (c:Chunk) SET c.lex = ''")
        ids = [r["id"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id", {"d": doc_id})]
        ds.apply_embeddings(self.store, doc_id, {i: [0.1, 0.2, 0.3, 0.4] for i in ids})
        self.store.ensure_search_indexes(dim=4)
        return ids

    def test_새_스토어는_처음부터_lex_위에_선다(self):
        self.assertTrue(self.store.lex_ready(), "청크 0개 스토어는 기동에서 바로 표지가 서야 한다")
        ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        self.store.ensure_search_indexes(dim=None)
        hits = self.store.fulltext_search("승부", 5)
        self.assertTrue(hits, "조사 없는 낱말로 조사 붙은 본문을 찾아야 한다")
        self.assertIn("lex", hits[0], "관문이 읽을 lex 가 행에 실려야 한다")
        self.assertTrue(self._ids("rebalancing"), "영어 낱말은 그대로 찾혀야 한다")

    def test_표지_전에는_오늘처럼_text_다(self):
        d = ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        self._as_pre_v21(d["doc_id"])
        self.assertFalse(self.store.lex_ready())
        self.assertEqual(self._ids("승부"), [], "표지 전 질의는 오늘 색인을 그대로 탄다")
        self.assertTrue(self._ids("승부는"))
        self.assertNotIn("lex", self.store.fulltext_search("승부는", 5)[0])
        # 자기복구 재빌드도 표지를 따른다 — 표지 전이면 `text` 위에 다시 선다.
        self.store.rebuild_fts_index()
        self.assertEqual(self._ids("승부"), [])

    def test_이관은_lex_를_채우고_HNSW_를_그대로_둔다(self):
        d = ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        ids = self._as_pre_v21(d["doc_id"])
        vecs = self.store.run("MATCH (v:ChunkVec) RETURN count(v) AS c")[0]["c"]
        self.assertGreater(vecs, 0)
        out = self.store.migrate_lex_index()
        self.assertTrue(out["migrated"])
        self.assertEqual(out["backfilled"], len(ids))
        self.assertTrue(self.store.lex_ready())
        self.assertTrue(self._ids("승부"), "이관 뒤에는 조사 없는 낱말이 맞아야 한다")
        self.assertEqual(self.store.run("MATCH (v:ChunkVec) RETURN count(v) AS c")[0]["c"], vecs)
        self.assertTrue(self.store.vector_search([0.1, 0.2, 0.3, 0.4], 5), "의미 팔이 살아 있어야 한다")
        empty = self.store.run("MATCH (c:Chunk) WHERE c.lex = '' RETURN count(c) AS c")[0]["c"]
        self.assertEqual(empty, 0)
        self.assertEqual(self.store.migrate_lex_index(), {"migrated": False, "reason": "already ready"})

    def test_표지_전에_죽은_이관은_다음_실행이_끝낸다(self):
        d = ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        self._as_pre_v21(d["doc_id"])
        with mock.patch.object(type(self.store), "_set_lex_ready",
                               side_effect=RuntimeError("killed before the marker")):
            with self.assertRaises(RuntimeError):
                self.store.migrate_lex_index()
        self.assertFalse(self.store.lex_ready(), "표지 없이 끝났으면 준비된 것이 아니다")
        self.assertTrue(self._ids("rebalancing"), "죽은 뒤에도 검색은 답해야 한다")
        out = self.store.migrate_lex_index()
        self.assertTrue(out["migrated"])
        self.assertTrue(self.store.lex_ready())
        self.assertTrue(self._ids("승부"))

    def test_표지는_다시_열어도_남는다(self):
        d = ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        self._as_pre_v21(d["doc_id"])
        self.store.migrate_lex_index()
        self.store.close()
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        self.assertTrue(self.store.lex_ready())
        self.assertTrue(self._ids("승부"))

    def test_put_doc_은_같은_MERGE_에서_lex_를_쓴다(self):
        d = ds.put_doc(self.store, owner="user:a", text=_DOC, title="A")
        got = self.store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.lex AS lex, c.text AS t",
                             {"d": d["doc_id"]})
        self.assertTrue(got)
        for r in got:
            self.assertEqual(r["lex"], lex_text(r["t"]))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class LexMigrationServingTests(unittest.TestCase):
    """기본은 서비스 중에 이관하지 않는다. `background` 일 때만 기동 뒤 잡으로 돈다."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.data = os.path.join(self._tmp, "store")
        from store.backend import open_store
        from store.config import load_config
        st = open_store(load_config({"data_dir": self.data}))
        st.provision(dim=4)
        ds.put_doc(st, owner="tenant:local", text=_DOC, title="A")
        st.run("MATCH (m:_SchemaMeta) WHERE m.key = $k DELETE m", {"k": LEX_INDEX_KEY})
        st._lex_ready = None
        st.drop_search_indexes()
        st.run("MATCH (c:Chunk) SET c.lex = ''")
        st.ensure_search_indexes(dim=None)
        st.close()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _open(self, env):
        from api.server import VincApp
        from store.config import load_config
        with mock.patch.dict(os.environ, env, clear=False):
            return VincApp.open(load_config({"data_dir": self.data}))

    def test_기본은_defer_라_이관하지_않는다(self):
        env = {k: v for k, v in os.environ.items() if k != "VINC_LEX_BACKFILL"}
        with mock.patch.dict(os.environ, env, clear=True):
            app = self._open({})
        try:
            self.assertIsNone(app.lex_migration)
            self.assertFalse(app.store.lex_ready())
        finally:
            app.close()

    def test_background_는_기동_뒤_잡으로_옮긴다(self):
        import time
        app = self._open({"VINC_LEX_BACKFILL": "background"})
        try:
            deadline = time.time() + 120
            while app.lex_migration is not None and time.time() < deadline:
                time.sleep(0.2)
            self.assertIsNone(app.lex_migration, "이관이 끝나면 상태가 내려가야 한다")
            self.assertTrue(app.store.lex_ready(), app.lex_migration_result)
            self.assertTrue(app.lex_migration_result and app.lex_migration_result.get("migrated"))
        finally:
            app.close()


class HealthWhileMigratingTests(unittest.TestCase):
    """이관 중 `/health` 는 스토어를 읽지 않는다 — 읽으면 헬스가 가드에 막혀 앱이 사이드카가
    죽은 줄 안다(앱은 12초쯤 기다린다)."""

    def test_migrating_은_싣고_백로그는_안_읽는다(self):
        import contextlib
        import threading
        import urllib.request
        from api.server import VincApp, make_server
        from api.auth import SessionAuth

        class _Store:
            driver_name = "fake"
            def reader_view(self): return self
            def run(self, cypher, params=None): return []
            def fulltext_search(self, *a, **k): return []
            def vector_search(self, *a, **k): return []
            def close(self): pass

        app = VincApp(store=_Store(), auth=SessionAuth("t"))
        app.lex_migration = {"step": "search-index", "started_at": "2026-10-02T00:00:00Z"}
        app.embed_health = mock.Mock(side_effect=AssertionError("이관 중에 스토어를 읽었다"))
        httpd = make_server(app, host="127.0.0.1", port=0)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/health"
            with contextlib.closing(urllib.request.urlopen(url, timeout=5)) as r:
                body = json.loads(r.read().decode("utf-8"))
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["migrating"]["step"], "search-index")
            self.assertNotIn("embed", body)
            app.embed_health.assert_not_called()
        finally:
            httpd.shutdown()
            app.close()


if __name__ == "__main__":
    unittest.main()
