# -*- coding: utf-8 -*-
"""시드 그래프 순위의 두 보탬 — 최근성을 섞은 `ppr_recent` 과 상금 기반 연결 선택.

재는 것:
  1. `ppr_recent`: 같은 거리면 최근 것이 위다. 시각을 모르는 노드는 아는 노드들의 평균을
     받는다(모른다고 오래된 것으로도, 새것으로도 치지 않는다). 몫이 0 이면 `ppr` 과 같다
  2. `prize_subgraph`: 시드를 포함하고 연결돼 있으며 예산을 지킨다. 가깝지만 상금이 작은
     노드보다 한 홉 먼 상금 큰 노드를 고를 수 있다. 결정론이다
  3. `vinc_graph` 경로: `select` 를 안 주면 결과가 예전과 같다. `prize` 면 요청한 깊이를
     그대로 두고 예산만큼 고른 뒤 `selection` 으로 말한다
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()
TODAY = dt.date(2026, 9, 28)


class Recency(unittest.TestCase):

    def test_same_distance_newer_ranks_higher(self):
        nodes = [{"id": "old", "ts": "2025-09-28"}, {"id": "new", "ts": "2026-09-20"}]
        r = q.ppr_recent(nodes, {"old": 0.5, "new": 0.5}, today=TODAY)
        self.assertGreater(r["new"], r["old"])
        self.assertAlmostEqual(sum(r.values()), 1.0)

    def test_weight_zero_is_plain_ppr(self):
        nodes = [{"id": "a", "ts": "2020-01-01"}, {"id": "b", "ts": "2026-09-27"}]
        r = q.ppr_recent(nodes, {"a": 0.7, "b": 0.3}, today=TODAY, weight=0.0)
        self.assertAlmostEqual(r["a"], 0.7)
        self.assertAlmostEqual(r["b"], 0.3)

    def test_unknown_time_gets_the_average(self):
        nodes = [{"id": "a", "ts": "2026-09-28"}, {"id": "b", "ts": "2025-09-28"},
                 {"id": "c", "ts": ""}]
        r = q.ppr_recent(nodes, {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}, today=TODAY)
        self.assertLess(r["b"], r["c"])
        self.assertLess(r["c"], r["a"])

    def test_one_half_life_halves_the_recency_share(self):
        nodes = [{"id": "a", "ts": "2026-09-28"}, {"id": "b", "ts": "2026-06-30"}]  # 90일
        r = q.ppr_recent(nodes, {"a": 0.5, "b": 0.5}, today=TODAY, weight=1.0)
        self.assertAlmostEqual(r["a"] / r["b"], 2.0, places=6)

    def test_attach_ppr_keeps_ppr_and_adds_the_mix(self):
        shaped = {"nodes": [{"id": "s", "ts": "2026-09-01"}, {"id": "x", "ts": "2024-01-01"}],
                  "edges": [{"source": "s", "target": "x", "type": "RELATED_TO"}]}
        q.attach_ppr(shaped, ["s"], today=TODAY)
        for n in shaped["nodes"]:
            self.assertIn("ppr", n)
            self.assertIn("ppr_recent", n)


class PrizeSubgraph(unittest.TestCase):

    def _graph(self):
        nodes = [{"id": i} for i in ("root", "a", "b", "c", "d")]
        edges = [{"source": "root", "target": "a"}, {"source": "root", "target": "b"},
                 {"source": "b", "target": "c"}, {"source": "c", "target": "d"}]
        return nodes, edges

    def test_a_far_central_node_beats_a_near_peripheral_one(self):
        nodes, edges = self._graph()
        prizes = {"root": 0.3, "a": 0.01, "b": 0.1, "c": 0.5, "d": 0.02}
        self.assertEqual(q.prize_subgraph(nodes, edges, prizes, "root", 3), ["root", "b", "c"])

    def test_connected_rooted_and_within_budget(self):
        nodes, edges = self._graph()
        prizes = {"a": 0.9, "d": 0.8}
        got = q.prize_subgraph(nodes, edges, prizes, "root", 4)
        self.assertEqual(got[0], "root")
        self.assertEqual(len(got), 4)
        # d 는 c 를 거쳐야만 닿는다 — c 없이 d 가 들어오면 연결이 깨진 것이다.
        if "d" in got:
            self.assertIn("c", got)

    def test_deterministic_and_unknown_root(self):
        nodes, edges = self._graph()
        self.assertEqual(q.prize_subgraph(nodes, edges, {}, "root", 3),
                         q.prize_subgraph(list(reversed(nodes)), list(reversed(edges)), {},
                                          "root", 3))
        self.assertEqual(q.prize_subgraph(nodes, edges, {}, "nope", 3), [])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
class GraphSelect(unittest.TestCase):
    OWNER = "tenant:local"

    def setUp(self):
        from api import fragment as frag
        from store.backend import open_store
        from store.config import load_config
        keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
        self._keep = {k: os.environ.get(k) for k in keys}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
        os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)
        # 시드에 잎 여덟, 그리고 잎 하나(hub)에서 다시 여섯이 뻗는다: 깊이 2 는 15 노드다.
        nodes = [{"id": "concept:seed", "type": "concept", "title": "Seed"}]
        edges = []
        for i in range(8):
            nodes.append({"id": f"concept:leaf{i}", "type": "concept", "title": f"Leaf {i}"})
            edges.append({"from": "concept:seed", "to": f"concept:leaf{i}", "type": "relates_to"})
        for j in range(6):
            nodes.append({"id": f"concept:far{j}", "type": "concept", "title": f"Far {j}"})
            edges.append({"from": "concept:leaf0", "to": f"concept:far{j}", "type": "relates_to"})
        frag.merge_fragment({"owner": self.OWNER, "nodes": nodes, "edges": edges},
                            store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_is_unchanged(self):
        a = q.graph(owner=self.OWNER, seed="concept:seed", depth=2, max_nodes=10,
                    store=self.store)
        b = q.graph(owner=self.OWNER, seed="concept:seed", depth=2, max_nodes=10,
                    select="layers", store=self.store)
        self.assertEqual(a, b)
        self.assertEqual(a["depth_budget"]["served_depth"], 1)
        self.assertNotIn("selection", a)

    def test_prize_keeps_the_depth_and_fills_the_budget(self):
        res = q.graph(owner=self.OWNER, seed="concept:seed", depth=2, max_nodes=10,
                      select="prize", store=self.store)
        self.assertEqual(res["depth"], 2)
        self.assertNotIn("depth_budget", res)
        self.assertEqual(res["selection"]["method"], "prize")
        self.assertEqual(res["selection"]["kept"], 10)
        ids = {n["id"] for n in res["nodes"]}
        self.assertIn("concept:seed", ids)
        self.assertIn("concept:leaf0", ids)        # 허브 잎은 상금이 커서 남는다
        # 남은 노드는 모두 시드와 이어져 있다(실린 엣지만으로).
        adj: dict = {i: set() for i in ids}
        for e in res["edges"]:
            adj[e["source"]].add(e["target"])
            adj[e["target"]].add(e["source"])
        seen, stack = {"concept:seed"}, ["concept:seed"]
        while stack:
            for u in adj[stack.pop()]:
                if u not in seen:
                    seen.add(u)
                    stack.append(u)
        self.assertEqual(seen, ids)


class Surfaces(unittest.TestCase):

    def test_the_tool_passes_select(self):
        from vinc_mcp import tools

        class _B:
            owner = "tenant:local"

            def request(self, method, path, params=None, body=None, **kw):
                self.seen = params
                return {}

        b = _B()
        tools._t_graph({"seed_id": "concept:x", "select": "prize"}, b)
        self.assertEqual(b.seen["select"], "prize")
        tools._t_graph({"seed_id": "concept:x", "select": "bogus"}, b)
        self.assertNotIn("select", b.seen)
        spec = {s["name"]: s for s in tools.specs()}["vinc_graph"]
        self.assertEqual(spec["inputSchema"]["properties"]["select"]["enum"], ["layers", "prize"])


if __name__ == "__main__":
    unittest.main()
