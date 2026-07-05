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
            "summary": "discussed refunds", "completed": True,
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
                "workspace": "", "mode": "", "summary": "", "completed": False,
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
