# -*- coding: utf-8 -*-
"""`vinc_path` 의 가장자리 입력 셋 — 같은 끝점 · 최단만 · 거절 순서.

감사에서 라이브로 재현한 것들이다:

- ``a = b = concept:vinc-mcp`` → 멀쩡한 개념인데 ``found:false, "no path within
  max_depth=6"``. 엔진 패턴이 ``1..d`` 라 자기 자신까지는 매치가 0이었다.
- ``weight=hops`` 가 최단보다 긴 경로를 함께 돌려줬다. 설명은 "ALL shortest paths
  (ties included)" 를 약속한다.
- 없는 ``record:x`` → "unknown node". 문서화된 거절은 "개념 층만 걷는다" 인데 존재
  판정이 먼저 돌아 그 문장에 도달하지 못했다.

근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from _pkseed import _seed  # noqa: E402

_DRIVER, _NAME = import_driver()


def _node(cid: str) -> dict:
    return {"concept_id": cid, "title": cid.upper()}


class _Store:
    """경로 쿼리에는 준비된 행을, PK 조회에는 아는 id 만, 유사도 검색에는 빈손을 준다."""

    def __init__(self, existing: set[str], path_rows: list[dict] | None = None):
        self.existing = existing
        self.path_rows = path_rows or []
        self.path_calls = 0

    def run(self, cypher: str, params: dict | None = None):
        if "ALL SHORTEST" in cypher:
            self.path_calls += 1
            return self.path_rows
        if "UNION ALL" in cypher or "CONTAINS" in cypher.upper():
            return []
        for v in (params or {}).values():
            if isinstance(v, str):
                for e in self.existing:
                    if v.endswith(e):
                        return [{"id": e, "title": "Title of " + e}]
        return []

    def close(self):
        pass


class SameEndpoint(unittest.TestCase):
    def test_hops_answers_one_length_zero_path(self):
        store = _Store({"concept:vinc-mcp"})
        r = q.shortest_path("concept:vinc-mcp", "concept:vinc-mcp", store=store)
        self.assertTrue(r["executed"])
        self.assertTrue(r["found"])
        self.assertEqual(r["length"], 0)
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["weight"], "hops")
        self.assertEqual(r["paths"], [{"ids": ["concept:vinc-mcp"],
                                       "titles": ["Title of concept:vinc-mcp"],
                                       "length": 0}])
        self.assertNotIn("error", r)
        self.assertEqual(store.path_calls, 0, "자기 자신까지는 엔진에 물을 것이 없다")

    def test_confidence_answers_the_same_with_zero_cost(self):
        store = _Store({"concept:vinc-mcp"})
        r = q.shortest_path("concept:vinc-mcp", "concept:vinc-mcp",
                            weight="confidence", store=store)
        self.assertTrue(r["found"])
        self.assertEqual(r["weight"], "confidence")
        self.assertEqual(r["length"], 0)
        self.assertEqual(r["cost"], 0.0)
        self.assertEqual(r["paths"][0]["ids"], ["concept:vinc-mcp"])
        self.assertEqual(r["paths"][0]["cost"], 0.0)

    def test_a_missing_concept_is_still_refused(self):
        for w in ("hops", "confidence"):
            r = q.shortest_path("concept:ghost", "concept:ghost", weight=w,
                                store=_Store(set()))
            self.assertFalse(r["found"], w)
            self.assertIn("unknown node: concept:ghost", r.get("error", ""), w)

    def test_a_non_concept_is_still_refused_even_when_it_exists(self):
        for w in ("hops", "confidence"):
            r = q.shortest_path("record:x", "record:x", weight=w,
                                store=_Store({"record:x"}))
            self.assertFalse(r["found"], w)
            self.assertIn("concept layer only", r.get("error", ""), w)

    def test_an_open_type_node_is_not_a_concept(self):
        """개방층(Entity) 접두는 거절 목록에 없지만 개념도 아니다."""
        r = q.shortest_path("theory:duality", "theory:duality",
                            store=_Store({"theory:duality"}))
        self.assertFalse(r["found"])
        self.assertIn("concept layer only", r.get("error", ""))


class OnlyShortestHops(unittest.TestCase):
    def test_longer_rows_are_dropped(self):
        rows = [
            {"ns": [_node("a"), _node("x"), _node("y"), _node("b")]},   # 3
            {"ns": [_node("a"), _node("q"), _node("b")]},               # 2
            {"ns": [_node("a"), _node("p"), _node("b")]},               # 2 (동률)
        ]
        r = q.shortest_path("a", "b", store=_Store({"a", "b"}, rows))
        self.assertTrue(r["found"])
        self.assertEqual(r["length"], 2)
        self.assertEqual(r["count"], 2, "동률은 전부, 더 긴 것은 빼고")
        self.assertEqual([p["ids"] for p in r["paths"]],
                         [["a", "p", "b"], ["a", "q", "b"]])
        self.assertTrue(all(p["length"] == 2 for p in r["paths"]))


class RefusalOrder(unittest.TestCase):
    def test_a_missing_record_gets_the_concept_only_refusal(self):
        r = q.shortest_path("record:x", "concept:vinc-mcp",
                            store=_Store({"concept:vinc-mcp"}))
        self.assertFalse(r["found"])
        self.assertIn("concept layer only", r.get("error", ""))
        self.assertIn("record:x", r["error"])
        self.assertNotIn("unknown node", r["error"])

    def test_a_missing_concept_is_still_unknown(self):
        r = q.shortest_path("concept:ghost", "concept:vinc-mcp",
                            store=_Store({"concept:vinc-mcp"}))
        self.assertIn("unknown node: concept:ghost", r.get("error", ""))
        self.assertNotIn("concept layer only", r["error"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class SameEndpointEmbedded(unittest.TestCase):
    """실엔진에서도 `1..d` 패턴을 우회해 길이 0으로 답하는가."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     "MEMORY_EMBEDDING_MODEL": "local-en"}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_same_endpoint_on_the_real_engine(self):
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        with store.session() as s:
            for cid in ("a", "b"):
                _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id='u', "
                      "n.title=$t, n.domain='m'", id=cid, t="Title " + cid)
            _seed(s.run, "MATCH (p:Concept{concept_id:'a'}),(t:Concept{concept_id:'b'}) "
                  "MERGE (p)-[:RELATED_TO]->(t)")
        try:
            hops = q.shortest_path("a", "a", owner="u", store=store)
            conf = q.shortest_path("a", "a", owner="u", weight="confidence", store=store)
            ghost = q.shortest_path("nope", "nope", owner="u", store=store)
        finally:
            store.close()
        for r in (hops, conf):
            self.assertTrue(r["found"], r)
            self.assertEqual(r["length"], 0)
            self.assertEqual(r["paths"][0]["ids"], ["a"])
            self.assertEqual(r["paths"][0]["titles"], ["Title a"])
        self.assertFalse(ghost["found"])
        self.assertIn("unknown node", ghost.get("error", ""))


if __name__ == "__main__":
    unittest.main()
