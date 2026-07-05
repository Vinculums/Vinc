# -*- coding: utf-8 -*-
"""Tests for the store layer (config, schema, provision dry-run)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import config as cfgmod  # noqa: E402
from store import schema  # noqa: E402
from store import provision  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_embedding_dim_by_model(self):
        self.assertEqual(cfgmod.StoreConfig(embedding_model="anthropic").embedding_dim, 1024)
        self.assertEqual(cfgmod.StoreConfig(embedding_model="local").embedding_dim, 768)

    def test_overrides_win_over_env(self):
        old = os.environ.get("NEO4J_URI")
        os.environ["NEO4J_URI"] = "neo4j://env:7687"
        try:
            c = cfgmod.load_config({"uri": "neo4j://override:7687"})
            self.assertEqual(c.uri, "neo4j://override:7687")
        finally:
            if old is None:
                del os.environ["NEO4J_URI"]
            else:
                os.environ["NEO4J_URI"] = old

    def test_password_redacted(self):
        c = cfgmod.StoreConfig(password="secret")
        self.assertEqual(c.redacted()["password"], "***")


class SchemaTests(unittest.TestCase):
    def test_migrations_cover_all_engine_node_keys(self):
        ids = {m.id for m in schema.MIGRATIONS}
        for needed in ("c_episode_id", "c_concept_id", "c_bridge_id", "c_skillexec_id",
                       "c_chunk_id", "i_chunk_embedding", "i_chunk_text_ft"):
            self.assertIn(needed, ids)

    def test_migrations_include_owner_tenant_indexes(self):
        by_id = {m.id: m for m in schema.MIGRATIONS}
        self.assertIn("i_episode_owner", by_id)
        self.assertIn("i_episode_tenant", by_id)
        self.assertIn("Episode", by_id["i_episode_owner"].cypher)
        self.assertIn("owner_id", by_id["i_episode_owner"].cypher)
        # indexes are non-unique (CREATE INDEX, not CONSTRAINT ... UNIQUE)
        self.assertIn("CREATE INDEX", by_id["i_episode_owner"].cypher)
        self.assertNotIn("UNIQUE", by_id["i_episode_owner"].cypher)

    def test_migrations_include_learning_domain_indexes(self):
        by_id = {m.id: m for m in schema.MIGRATIONS}
        # domain is a first-class, open learning axis on both learning node types
        for mid, label, prop in [
            ("i_episode_domain", "Episode", "domain"),
            ("i_concept_owner", "Concept", "owner_id"),
            ("i_concept_domain", "Concept", "domain"),
        ]:
            self.assertIn(mid, by_id)
            self.assertIn(label, by_id[mid].cypher)
            self.assertIn(prop, by_id[mid].cypher)

    def test_migrations_include_learner_pattern(self):
        by_id = {m.id: m for m in schema.MIGRATIONS}
        self.assertIn("c_learnerpattern_id", by_id)      # unique pattern_id constraint
        self.assertIn("IS UNIQUE", by_id["c_learnerpattern_id"].cypher)
        self.assertIn("i_learnerpattern_owner", by_id)   # owner-scoped index
        self.assertIn("owner_id", by_id["i_learnerpattern_owner"].cypher)

    def test_render_substitutes_dim(self):
        rendered = schema.render(schema.MIGRATIONS, dim=768)
        vec = [s for s in rendered if "chunk_embedding" in s][0]
        self.assertIn("768", vec)
        self.assertNotIn("{dim}", vec)

    def test_no_unsubstituted_placeholders(self):
        for s in schema.render(schema.MIGRATIONS, dim=1024):
            self.assertNotIn("{dim}", s)


class ProvisionTests(unittest.TestCase):
    def test_dry_run_returns_statements(self):
        c = cfgmod.StoreConfig(embedding_model="anthropic")
        res = provision.provision(c, dry_run=True)
        self.assertTrue(res.dry_run)
        self.assertEqual(len(res.statements), len(schema.MIGRATIONS))
        self.assertEqual(res.applied, 0)

    def test_plan_matches_dim(self):
        c = cfgmod.StoreConfig(embedding_model="local")
        stmts = provision.plan(c)
        self.assertTrue(any("768" in s for s in stmts))


class BackfillTests(unittest.TestCase):
    def test_backfill_plan_only_targets_missing_owner(self):
        stmts = provision.backfill_owner_plan(owner="tenant:shared")
        cypher, params = stmts[0]
        self.assertIn("WHERE e.owner_id IS NULL", cypher)  # non-destructive
        self.assertEqual(params["owner"], "tenant:shared")

    def test_backfill_plan_adds_tenant_when_given(self):
        stmts = provision.backfill_owner_plan(owner="tenant:shared", tenant="acme")
        self.assertEqual(len(stmts), 2)
        self.assertIn("WHERE e.tenant_id IS NULL", stmts[1][0])
        self.assertEqual(stmts[1][1]["tenant"], "acme")

    def test_backfill_plan_omits_tenant_when_absent(self):
        stmts = provision.backfill_owner_plan(owner="tenant:shared")
        self.assertEqual(len(stmts), 1)

    def test_backfill_dry_run_reports_without_applying(self):
        c = cfgmod.StoreConfig(tenant_id="acme")
        res = provision.backfill_owner(c, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["tenant"], "acme")  # defaults to cfg.tenant_id
        self.assertEqual(res["updated"], 0)

    def test_backfill_domain_only_where_missing(self):
        stmts = provision.backfill_domain_plan()
        cypher, _ = stmts[0]
        self.assertIn("e.domain IS NULL", cypher)          # non-destructive
        self.assertIn("SET e.domain = e.workspace", cypher)  # derive from workspace
        self.assertIn("e.workspace <> ''", cypher)          # never stamp empty

    def test_backfill_domain_dry_run(self):
        res = provision.backfill_domain(cfgmod.StoreConfig(), dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["updated"], 0)


class DropTests(unittest.TestCase):
    def test_drop_plan_for_tenant_db(self):
        c = cfgmod.StoreConfig(database="mem_acme")
        self.assertEqual(provision.drop_plan(c), ["DROP DATABASE mem_acme IF EXISTS"])

    def test_drop_dry_run_does_not_drop(self):
        c = cfgmod.StoreConfig(database="mem_acme")
        res = provision.drop_database(c, dry_run=True)
        self.assertFalse(res["dropped"])
        self.assertEqual(res["database"], "mem_acme")

    def test_drop_refuses_non_tenant_database(self):
        for bad in ("neo4j", "system", None):
            with self.assertRaises(RuntimeError):
                provision.drop_plan(cfgmod.StoreConfig(database=bad))


if __name__ == "__main__":
    unittest.main()
