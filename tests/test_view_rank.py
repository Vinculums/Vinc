# -*- coding: utf-8 -*-
"""시드 그래프 순위의 두 보탬 — 최근성을 섞은 `ppr_recent` 과 상금 기반 연결 선택.

재는 것:
  1. `ppr_recent`: 같은 거리면 최근 것이 위다. 시각을 모르는 노드는 아는 노드들의 평균을
     받는다(모른다고 오래된 것으로도, 새것으로도 치지 않는다). 몫이 0 이면 `ppr` 과 같다.
     나이는 집합에서 가장 새 노드부터 재므로 그래프가 그대로면 값도 그대로다
  2. `prize_subgraph`: 시드를 포함하고 연결돼 있으며 예산을 지킨다. 가깝지만 상금이 작은
     노드보다 한 홉 먼 상금 큰 노드를 고를 수 있다. 결정론이다
  3. `vinc_graph` 경로: `select` 를 안 주면 결과가 예전과 같다(숫자로 못 박는다). `prize` 면
     요청한 깊이를 두고 층 자르기와 같은 단위(시드 + 개념 `max_nodes`)로 고른 뒤, 부착은 층
     자르기처럼 합류시키고 `selection` 으로 말한다. 행·축 조회는 고른 개념에만 돈다
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


class Recency(unittest.TestCase):

    def test_same_distance_newer_ranks_higher(self):
        nodes = [{"id": "old", "ts": "2025-09-28"}, {"id": "new", "ts": "2026-09-20"}]
        r = q.ppr_recent(nodes, {"old": 0.5, "new": 0.5})
        self.assertGreater(r["new"], r["old"])
        self.assertAlmostEqual(sum(r.values()), 1.0)

    def test_weight_zero_is_plain_ppr(self):
        nodes = [{"id": "a", "ts": "2020-01-01"}, {"id": "b", "ts": "2026-09-27"}]
        r = q.ppr_recent(nodes, {"a": 0.7, "b": 0.3}, weight=0.0)
        self.assertAlmostEqual(r["a"], 0.7)
        self.assertAlmostEqual(r["b"], 0.3)

    def test_unknown_time_gets_the_average(self):
        nodes = [{"id": "a", "ts": "2026-09-28"}, {"id": "b", "ts": "2025-09-28"},
                 {"id": "c", "ts": ""}]
        r = q.ppr_recent(nodes, {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})
        self.assertLess(r["b"], r["c"])
        self.assertLess(r["c"], r["a"])

    def test_one_half_life_halves_the_recency_share(self):
        nodes = [{"id": "a", "ts": "2026-09-28"}, {"id": "b", "ts": "2026-06-30"}]  # 90일
        r = q.ppr_recent(nodes, {"a": 0.5, "b": 0.5}, weight=1.0)
        self.assertAlmostEqual(r["a"] / r["b"], 2.0, places=6)

    def test_age_is_measured_from_the_newest_node_not_today(self):
        # 90일 차이 나는 둘. 오늘이 언제든 새것의 최근성은 1, 헌것은 0.5 다. 오늘에서 재면
        # 둘 다 수년 묵어 최근성이 0 에 붙고 비가 1 로 무너진다.
        nodes = [{"id": "a", "ts": "2020-01-01"}, {"id": "b", "ts": "2019-10-03"}]
        r = q.ppr_recent(nodes, {"a": 0.5, "b": 0.5})
        self.assertAlmostEqual(r["a"] / r["b"], 1.0 / 0.75, places=6)

    def test_shifting_every_timestamp_changes_nothing(self):
        # 그래프가 안 바뀌면 값도 안 바뀐다 — 모든 시각을 같은 만큼 옮기는 것은 날이 바뀌는
        # 것과 같다(나이 차이는 그대로다).
        base = (("s", "2026-09-01"), ("x", "2024-01-01"), ("y", ""))

        def run(days):
            shaped = {"nodes": [{"id": i, "ts": (dt.date.fromisoformat(t)
                                                 + dt.timedelta(days=days)).isoformat()
                                 if t else ""} for i, t in base],
                      "edges": [{"source": "s", "target": "x", "type": "RELATED_TO"},
                                {"source": "s", "target": "y", "type": "RELATED_TO"}]}
            q.attach_ppr(shaped, ["s"])
            return {n["id"]: (n["ppr"], n["ppr_recent"]) for n in shaped["nodes"]}

        self.assertEqual(run(0), run(-400))

    def test_attach_ppr_keeps_ppr_and_adds_the_mix(self):
        shaped = {"nodes": [{"id": "s", "ts": "2026-09-01"}, {"id": "x", "ts": "2024-01-01"}],
                  "edges": [{"source": "s", "target": "x", "type": "RELATED_TO"}]}
        q.attach_ppr(shaped, ["s"])
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


def _kinds(res: dict) -> dict:
    out: dict = {}
    for n in res["nodes"]:
        out[n["kind"]] = out.get(n["kind"], 0) + 1
    return out


def _connected_from(res: dict, seed: str) -> set:
    adj: dict = {n["id"]: set() for n in res["nodes"]}
    for e in res["edges"]:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])
    seen, stack = {seed}, [seed]
    while stack:
        for u in adj[stack.pop()]:
            if u not in seen:
                seen.add(u)
                stack.append(u)
    return seen


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
class _Store(unittest.TestCase):
    OWNER = "tenant:local"
    SEED = "concept:seed"

    def nodes_and_edges(self) -> tuple[list, list]:
        raise NotImplementedError

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
        nodes, edges = self.nodes_and_edges()
        frag.merge_fragment({"owner": self.OWNER, "nodes": nodes, "edges": edges},
                            store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def g(self, **kw) -> dict:
        return q.graph(owner=self.OWNER, seed=self.SEED, store=self.store, **kw)


class GraphSelect(_Store):
    """시드에 잎 여덟, 잎 하나(hub)에서 다시 여섯: 깊이 2 의 개념 도달 집합은 14 다.
    레코드 둘은 시드에, 하나는 먼 잎(far0)에 붙는다."""

    def nodes_and_edges(self):
        nodes = [{"id": self.SEED, "type": "concept", "title": "Seed"}]
        edges = []
        for i in range(8):
            nodes.append({"id": f"concept:leaf{i}", "type": "concept", "title": f"Leaf {i}"})
            edges.append({"from": self.SEED, "to": f"concept:leaf{i}", "type": "relates_to"})
        for j in range(6):
            nodes.append({"id": f"concept:far{j}", "type": "concept", "title": f"Far {j}"})
            edges.append({"from": "concept:leaf0", "to": f"concept:far{j}", "type": "relates_to"})
        for rid, on in (("record:s0", self.SEED), ("record:s1", self.SEED),
                        ("record:f0", "concept:far0")):
            nodes.append({"id": rid, "type": "record", "title": rid})
            edges.append({"from": rid, "to": on, "type": "relates_to"})
        # 결정 하나가 먼 잎 여섯을 두고 내려졌다(결정 시드의 한 홉이 예산 5 를 넘는다).
        nodes.append({"id": "decision:d", "type": "decision", "title": "D"})
        edges += [{"from": "decision:d", "to": f"concept:far{j}", "type": "about"}
                  for j in range(6)]
        return nodes, edges

    def test_default_is_unchanged(self):
        a = self.g(depth=2, max_nodes=10)
        self.assertEqual(a, self.g(depth=2, max_nodes=10, select="layers"))
        self.assertNotIn("selection", a)
        # 예산 없는 쪽과 층 자르기의 숫자를 못 박는다 — 이 숫자가 곧 "기본은 그대로"의 뜻이다.
        self.assertEqual(a["depth_budget"], {"requested_depth": 2, "served_depth": 1,
                                             "reach_at_requested": 14, "reach_served": 8,
                                             "max_nodes": 10})
        self.assertEqual(_kinds(a), {"concept": 9, "record": 2})
        self.assertEqual(_kinds(self.g(depth=2)), {"concept": 15, "record": 3, "decision": 1})

    def test_a_decision_seed_falls_back_to_layers_and_says_so(self):
        kw = {"seed": "decision:d", "depth": 2, "max_nodes": 5}
        res = q.graph(owner=self.OWNER, store=self.store, select="prize", **kw)
        sel = res.pop("selection")
        self.assertEqual((sel["method"], sel["requested"]), ("layers", "prize"))
        self.assertIn("concept seeds only", sel["note"])
        self.assertEqual(res, q.graph(owner=self.OWNER, store=self.store, **kw))

    def test_prize_keeps_the_depth_and_fills_the_budget(self):
        res = self.g(depth=2, max_nodes=10, select="prize")
        self.assertEqual(res["depth"], 2)
        self.assertNotIn("depth_budget", res)
        sel = res["selection"]
        self.assertEqual((sel["method"], sel["reach"], sel["kept"]), ("prize", 14, 10))
        self.assertEqual(_kinds(res)["concept"], 11)          # 시드 + 예산 10
        ids = {n["id"] for n in res["nodes"]}
        self.assertIn("concept:leaf0", ids)        # 허브 잎은 상금이 커서 남는다
        self.assertTrue({"record:s0", "record:s1"} <= ids)   # 시드의 부착은 예산 밖이다
        self.assertEqual(_connected_from(res, self.SEED), ids)

    def test_rows_and_axes_are_read_for_the_kept_concepts_only(self):
        from unittest import mock
        seen: list = []

        def spy(fn):
            def wrapped(*a, **kw):
                seen.append(len(kw["ids"] if "ids" in kw else a[0]))
                return fn(*a, **kw)
            return wrapped

        with mock.patch.object(q, "graph_rows_for_ids_plan", spy(q.graph_rows_for_ids_plan)), \
                mock.patch.object(q, "_GRAPH_AXIS_PLANS",
                                  tuple(spy(f) for f in q._GRAPH_AXIS_PLANS)):
            res = self.g(depth=2, max_nodes=5, select="prize")
        self.assertEqual(res["selection"]["kept"], 5)
        self.assertEqual(len(seen), 1 + len(q._GRAPH_AXIS_PLANS))
        self.assertEqual(max(seen), 6)             # 도달 집합(15)이 아니라 시드 + 예산

    def test_the_cap_counts_concepts(self):
        from unittest import mock
        with mock.patch.object(q, "PRIZE_REACH_CAP", 13):
            res = self.g(depth=2, max_nodes=10, select="prize")
        self.assertEqual(res["selection"]["method"], "layers")
        self.assertIn("reach 14 ", res["selection"]["note"])
        self.assertEqual(res["depth_budget"]["reach_at_requested"], 14)


class PrizeUnit(_Store):
    """리뷰 탐침 모양: 시드 + 개념 이웃 열 + 시드에 붙은 레코드 여섯. 예산은 개념으로
    센다(시드 빼고). 레코드가 예산을 먹으면 시드 자신의 부착이 떨어진다."""

    def nodes_and_edges(self):
        nodes = [{"id": self.SEED, "type": "concept", "title": "Seed"}]
        edges = []
        for i in range(10):
            nodes.append({"id": f"concept:n{i}", "type": "concept", "title": f"N {i}"})
            edges.append({"from": self.SEED, "to": f"concept:n{i}", "type": "relates_to"})
        for j in range(6):
            nodes.append({"id": f"record:r{j}", "type": "record", "title": f"R {j}"})
            edges.append({"from": f"record:r{j}", "to": self.SEED, "type": "relates_to"})
        return nodes, edges

    def test_a_budget_that_fits_is_the_same_as_layers(self):
        layers = self.g(depth=2, max_nodes=10)
        self.assertEqual(_kinds(layers), {"concept": 11, "record": 6})
        self.assertNotIn("depth_budget", layers)
        self.assertEqual(self.g(depth=2, max_nodes=10, select="prize"), layers)

    def test_a_cut_keeps_the_seeds_own_records(self):
        res = self.g(depth=2, max_nodes=5, select="prize")
        self.assertEqual(res["depth"], 2)
        self.assertEqual((res["selection"]["reach"], res["selection"]["kept"]), (10, 5))
        self.assertEqual(_kinds(res), {"concept": 6, "record": 6})


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
