# -*- coding: utf-8 -*-
"""Tests for the store layer — embedded config, schema DDL, provision dry-run."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import config as cfgmod  # noqa: E402
from store import embedded_schema as eschema  # noqa: E402
from store import provision  # noqa: E402


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
        # E-G6 — CC 4종 + ABOUT 에 evidence/confidence 선택 열 부여(관계 출처 추적).
        self.assertIn("CREATE REL TABLE PREREQUISITE_OF(FROM Concept TO Concept, "
                      "evidence STRING, confidence DOUBLE)", ddl)
        self.assertIn("CREATE REL TABLE CROSSES(FROM Bridge TO Domain)", ddl)
        self.assertIn("CONNECTS(FROM Bridge TO Concept, role STRING)", ddl)
        # F4 — episodic↔semantic 다리(+ E-G6 증거·신뢰도)
        self.assertIn("CREATE REL TABLE ABOUT(FROM Episode TO Concept, "
                      "evidence STRING, confidence DOUBLE)", ddl)
        # S4 확장 — 엣지 삭제 표식(메타, 부팅 시 멱등 생성)
        self.assertIn("CREATE NODE TABLE _EdgeTombstone(ekey STRING", ddl)

    def test_bridge_owner_column_and_v2_migration(self):
        # F5 — Bridge 소유 스탬프: 신규 설치는 base DDL, 구 설치는 v2 ALTER(백필 '').
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        bridge_ddl = next(s for s in ddl.splitlines() if s.startswith("CREATE NODE TABLE Bridge"))
        self.assertIn("owner_id STRING", bridge_ddl)
        self.assertEqual(eschema.SCHEMA_VERSION, 3)   # E-G6 관계 증거·신뢰도
        self.assertIn("ALTER TABLE Bridge ADD owner_id STRING DEFAULT ''",
                      eschema.migration_statements(1))
        # 0→3 는 v1(추적열)·v2(Bridge owner)·v3(관계 증거) 스텝을 모두 포함한다
        v0 = eschema.migration_statements(0)
        self.assertIn("ALTER TABLE Bridge ADD owner_id STRING DEFAULT ''", v0)
        self.assertTrue(any("ADD updated_at" in s for s in v0))
        self.assertTrue(any("ADD evidence STRING" in s for s in v0))     # v3
        self.assertTrue(any("ADD confidence DOUBLE" in s for s in v0))   # v3
        # v2→3 = 관계 증거 ALTER만(Bridge·추적열은 이미 적용됨)
        v2 = eschema.migration_statements(2)
        self.assertTrue(all("evidence" in s or "confidence" in s for s in v2))
        self.assertEqual(eschema.migration_statements(3), [])   # 최신은 no-op

    def test_plan_matches_dim(self):
        c = cfgmod.StoreConfig(embedding_model="local-en-large")  # 1024
        stmts = provision.plan(c)
        self.assertTrue(any("FLOAT[1024]" in s for s in stmts))


if __name__ == "__main__":
    unittest.main()
