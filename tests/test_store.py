# -*- coding: utf-8 -*-
"""Tests for the store layer — embedded config, schema DDL, provision dry-run."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tempfile  # noqa: E402

from store import config as cfgmod  # noqa: E402
from store import embedded_schema as eschema  # noqa: E402
from store import provision  # noqa: E402
from store.theme_store import ThemeStore, LEGACY_NODE_TYPES, LEGACY_EDGE_TYPES  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_default_backend_is_embedded(self):
        self.assertEqual(cfgmod.StoreConfig().backend, "embedded")
        self.assertEqual(cfgmod.load_config().backend, "embedded")

    def test_default_embedding_is_local_en_768(self):
        # 영어 우선·오프라인 기본(§8, D10) — API 키 불요(F5)
        self.assertEqual(cfgmod.DEFAULT_EMBEDDING_MODEL, "local-en")
        self.assertEqual(cfgmod.StoreConfig().embedding_dim, 768)

    def test_embedding_dim_by_model(self):
        self.assertEqual(cfgmod.StoreConfig(embedding_model="local-en-large").embedding_dim, 1024)
        self.assertEqual(cfgmod.StoreConfig(embedding_model="anthropic").embedding_dim, 1024)
        self.assertEqual(cfgmod.StoreConfig(embedding_model="local-en").embedding_dim, 768)

    def test_overrides_win_over_env(self):
        old = os.environ.get("MEMORY_DATA_DIR")
        os.environ["MEMORY_DATA_DIR"] = "/tmp/env-dir"
        try:
            c = cfgmod.load_config({"data_dir": "/tmp/override-dir"})
            self.assertEqual(c.data_dir, "/tmp/override-dir")
        finally:
            if old is None:
                os.environ.pop("MEMORY_DATA_DIR", None)
            else:
                os.environ["MEMORY_DATA_DIR"] = old

    def test_backend_env_override(self):
        old = os.environ.get("MEMORY_STORE_BACKEND")
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        try:
            self.assertEqual(cfgmod.load_config().backend, "embedded")
        finally:
            if old is None:
                os.environ.pop("MEMORY_STORE_BACKEND", None)
            else:
                os.environ["MEMORY_STORE_BACKEND"] = old


class EmbeddedSchemaTests(unittest.TestCase):
    def test_node_tables_cover_engine_keys(self):
        ddl = eschema.render_embedded_ddl(dim=768)
        joined = "\n".join(ddl)
        for label in ("Episode", "Concept", "Bridge", "SkillExecution",
                      "Chunk", "LearnerPattern"):
            self.assertIn(f"CREATE NODE TABLE {label}", joined)

    def test_episode_declares_scope_and_preference_columns(self):
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        ep = [s for s in eschema.render_embedded_ddl(dim=768) if "TABLE Episode" in s][0]
        for col in ("episode_id", "owner_id", "tenant_id", "domain",
                    "pinned", "tags", "access_count"):
            self.assertIn(col, ep)
        self.assertIn("PRIMARY KEY(episode_id)", ep)

    def test_chunk_embedding_dim_substituted(self):
        chunk = [s for s in eschema.render_embedded_ddl(dim=768) if "TABLE Chunk" in s][0]
        self.assertIn("FLOAT[768]", chunk)
        self.assertNotIn("{dim}", chunk)

    def test_no_unsubstituted_placeholders(self):
        for s in eschema.render_embedded_ddl(dim=1024):
            self.assertNotIn("{dim}", s)

    def test_entity_and_typed_carrier_present(self):
        # 사용자 저작 타입의 물리 캐리어 — Entity 노드(theme_id 컬럼) + TYPED 관계.
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        entity = [s for s in eschema.render_embedded_ddl(dim=768) if "TABLE Entity" in s][0]
        for col in ("entity_id", "type", "title", "owner_id", "theme_id", "tags"):
            self.assertIn(col, entity)
        self.assertIn("PRIMARY KEY(entity_id)", entity)
        self.assertIn("CREATE REL TABLE TYPED(FROM Entity TO Entity, type STRING)", ddl)

    def test_no_theme_or_type_node_tables_in_graph(self):
        # 어휘는 그래프 밖 메타(themes.json) — 그래프 스키마에 Theme/Type 테이블 없음.
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        self.assertNotIn("CREATE NODE TABLE Theme", ddl)
        self.assertNotIn("CREATE NODE TABLE Type", ddl)


class ProvisionTests(unittest.TestCase):
    def test_dry_run_returns_ddl_statements(self):
        c = cfgmod.StoreConfig(embedding_model="local-en")
        res = provision.provision(c, dry_run=True)
        self.assertTrue(res.dry_run)
        self.assertEqual(len(res.statements), len(eschema.render_embedded_ddl(dim=768)))
        self.assertEqual(res.applied, 0)
        # 노드 테이블 선행 후 관계 테이블(FROM/TO 노드 존재 전제)
        self.assertTrue(all(s.startswith(("CREATE NODE TABLE", "CREATE REL TABLE"))
                            for s in res.statements))

    def test_relation_tables_declared(self):
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        # concepts(Concept↔Concept 3종) · bridges(CROSSES/CONNECTS)
        self.assertIn("CREATE REL TABLE PREREQUISITE_OF(FROM Concept TO Concept)", ddl)
        self.assertIn("CREATE REL TABLE CROSSES(FROM Bridge TO Domain)", ddl)
        self.assertIn("CONNECTS(FROM Bridge TO Concept, role STRING)", ddl)

    def test_plan_matches_dim(self):
        c = cfgmod.StoreConfig(embedding_model="local-en-large")  # 1024
        stmts = provision.plan(c)
        self.assertTrue(any("FLOAT[1024]" in s for s in stmts))


class ThemeStoreTests(unittest.TestCase):
    def _store(self):
        return ThemeStore(tempfile.mkdtemp())

    def test_create_seeds_legacy_enum_as_system_draft_proposed(self):
        ts = self._store()
        ts.create_theme("t", "Title", ["질문?"])
        for n in LEGACY_NODE_TYPES:
            t = ts.resolve_type("t", "node", n)
            self.assertEqual((t["status"], t["provenance"]), ("proposed", "system_draft"))
        for e in LEGACY_EDGE_TYPES:
            t = ts.resolve_type("t", "edge", e)
            self.assertEqual((t["status"], t["provenance"]), ("proposed", "system_draft"))

    def test_guiding_questions_optional(self):
        ts = self._store()
        theme = ts.create_theme("t")
        self.assertEqual(theme["guiding_questions"], [])

    def test_create_is_idempotent(self):
        ts = self._store()
        ts.create_theme("t", "First")
        again = ts.create_theme("t", "Second")
        self.assertEqual(again["title"], "First")  # 기존 유지

    def test_propose_type_folds_in_idempotent(self):
        ts = self._store()
        ts.create_theme("t")
        self.assertTrue(ts.propose_type("t", "node", "해체주의"))
        self.assertFalse(ts.propose_type("t", "node", "해체주의"))
        t = ts.resolve_type("t", "node", "해체주의")
        self.assertEqual((t["status"], t["provenance"]), ("proposed", "ingest_proposed"))

    def test_ratify_reject_rename_transitions(self):
        ts = self._store()
        ts.create_theme("t")
        ts.propose_type("t", "node", "a")
        self.assertEqual(ts.ratify("t", "a")["status"], "authored")
        ts.propose_type("t", "edge", "b")
        renamed = ts.rename("t", "b", "c")
        self.assertEqual((renamed["name"], renamed["status"], renamed["provenance"]),
                         ("c", "authored", "user"))
        self.assertIsNone(ts.resolve_type("t", "edge", "b"))
        self.assertTrue(ts.reject("t", "c"))
        self.assertIsNone(ts.resolve_type("t", "edge", "c"))

    def test_list_types_status_filter(self):
        ts = self._store()
        ts.create_theme("t")
        ts.propose_type("t", "node", "x")
        ts.ratify("t", "x")
        authored = [t["name"] for t in ts.list_types("t", status="authored")]
        self.assertEqual(authored, ["x"])

    def test_persists_across_reload(self):
        d = tempfile.mkdtemp()
        ThemeStore(d).create_theme("t")
        self.assertTrue(ThemeStore(d).has_theme("t"))
        self.assertEqual(ThemeStore(d).active_theme_id(), "t")

    def test_in_memory_mode_when_no_data_dir(self):
        ts = ThemeStore(None)
        ts.create_theme("t")
        self.assertTrue(ts.has_theme("t"))   # 무 크래시 — 인메모리 강등

    def test_data_dir_is_db_file_path_uses_sibling_not_inside(self):
        # cfg.data_dir 는 임베디드 DB **파일** 경로다(kuzu 는 디렉토리를 거부). 그
        # 경로 안(<data_dir>/themes.json)이 아니라 형제(<data_dir>.themes.json)에
        # 써야 실배포에서 NotADirectoryError 로 죽지 않는다.
        base = tempfile.mkdtemp()
        db_file = os.path.join(base, "store")
        open(db_file, "w").close()           # data_dir 가 파일로 실존하는 상황 재현
        ts = ThemeStore(db_file)
        ts.create_theme("t")                 # 파일 안에 쓰려 하면 NotADirectoryError
        self.assertTrue(os.path.isfile(db_file + ".themes.json"))
        self.assertFalse(os.path.isdir(db_file))        # DB 파일은 여전히 파일
        self.assertTrue(ThemeStore(db_file).has_theme("t"))   # 재로드 지속

    def test_reject_does_not_touch_graph_only_vocabulary(self):
        # 경계: reject 는 어휘만 — resolve 후 사라지지만 그래프 인스턴스는 별개(미검증 대상).
        ts = self._store()
        ts.create_theme("t")
        ts.propose_type("t", "node", "x")
        ts.reject("t", "x")
        self.assertIsNone(ts.resolve_type("t", "node", "x"))


if __name__ == "__main__":
    unittest.main()
