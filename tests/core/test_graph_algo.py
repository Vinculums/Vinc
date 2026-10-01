# -*- coding: utf-8 -*-
"""`api/graph_algo.py` — 약연결 성분과 Leiden 의 계약.

재는 것:
  1. **Leiden 의 보증**: 돌려준 커뮤니티는 전부 연결돼 있다(Traag 2019 정리 2). Louvain 은
     커뮤니티의 최대 16%를 끊긴 채 돌려줄 수 있고, 그것이 이번 교체의 이유다
  2. **결정론**: 같은 그래프는 언제나 같은 번호를 낸다 — 답변 재료가 호출마다 바뀌면 안 된다
  3. **품질**: 알려진 그래프에서 알려진 모듈러리티에 닿는다(가중치 없는 Zachary 가라테 0.4198)
  4. **모양**: 고립 노드는 -1, 번호는 큰 커뮤니티부터 0, 겹친 엣지·자기 루프는 결과를 흔들지
     않는다

networkx 없이 돈다(사이드카가 그것을 싣지 않는다). 2026-09-28 에 networkx 3.x 와 따로 대조했다:
무작위 그래프 셋·caveman·BA 에서 모듈러리티가 소수 넷째 자리까지 같고, networkx Louvain 과
같은 수준이며, 끊긴 커뮤니티는 0 이었다.
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import graph_algo as ga  # noqa: E402

#: Zachary 가라테 클럽(34 노드, 78 엣지, 가중치 없음). 알려진 최대 모듈러리티 0.4198.
KARATE = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (0, 7), (0, 8), (0, 10), (0, 11),
          (0, 12), (0, 13), (0, 17), (0, 19), (0, 21), (0, 31), (1, 2), (1, 3), (1, 7),
          (1, 13), (1, 17), (1, 19), (1, 21), (1, 30), (2, 3), (2, 7), (2, 8), (2, 9),
          (2, 13), (2, 27), (2, 28), (2, 32), (3, 7), (3, 12), (3, 13), (4, 6), (4, 10),
          (5, 6), (5, 10), (5, 16), (6, 16), (8, 30), (8, 32), (8, 33), (9, 33), (13, 33),
          (14, 32), (14, 33), (15, 32), (15, 33), (18, 32), (18, 33), (19, 33), (20, 32),
          (20, 33), (22, 32), (22, 33), (23, 25), (23, 27), (23, 29), (23, 32), (23, 33),
          (24, 25), (24, 27), (24, 31), (25, 31), (26, 29), (26, 33), (27, 33), (28, 31),
          (28, 33), (29, 32), (29, 33), (30, 32), (30, 33), (31, 32), (31, 33), (32, 33)]


def _karate():
    edges = [(f"n{a:02d}", f"n{b:02d}") for a, b in KARATE]
    nodes = sorted({x for e in edges for x in e})
    return nodes, edges


def _random_graph(n: int, m: int, seed: int):
    rng = random.Random(seed)
    nodes = [f"v{i:05d}" for i in range(n)]
    edges = set()
    while len(edges) < m:
        a, b = rng.randrange(n), rng.randrange(n)
        if a != b:
            edges.add((nodes[min(a, b)], nodes[max(a, b)]))
    return nodes, sorted(edges)


def _connected(members: set, edges) -> bool:
    adj: dict = {m: set() for m in members}
    for a, b in edges:
        if a in members and b in members:
            adj[a].add(b)
            adj[b].add(a)
    start = next(iter(members))
    seen, stack = {start}, [start]
    while stack:
        for u in adj[stack.pop()]:
            if u not in seen:
                seen.add(u)
                stack.append(u)
    return seen == members


def _groups(assign):
    out: dict = {}
    for n, c in assign.items():
        if c != ga.ISOLATED:
            out.setdefault(c, set()).add(n)
    return out


class LeidenGuarantee(unittest.TestCase):

    def test_every_community_is_connected(self):
        for seed in range(6):
            nodes, edges = _random_graph(600, 900 + 150 * seed, seed)
            groups = _groups(ga.leiden(nodes, edges))
            for c, members in groups.items():
                self.assertTrue(_connected(members, edges), (seed, c, len(members)))

    def test_the_same_graph_gives_the_same_communities(self):
        nodes, edges = _random_graph(800, 1600, 11)
        first = ga.leiden(nodes, edges)
        self.assertEqual(first, ga.leiden(nodes, edges))
        # 입력 순서가 달라도 같다 — 투영은 조회 순서를 약속하지 않는다.
        self.assertEqual(first, ga.leiden(list(reversed(nodes)), list(reversed(edges))))

    def test_karate_reaches_its_known_modularity(self):
        nodes, edges = _karate()
        assign = ga.leiden(nodes, edges)
        self.assertAlmostEqual(ga.modularity(nodes, edges, assign), 0.4198, places=3)

    def test_original_graph_restarts_improve_and_preserve_modularity(self):
        nodes, edges = _random_graph(60, 120, 1)
        qualities = []
        for cap in (1, 2, 3):
            with patch.object(ga, "MAX_RESTARTS", cap):
                assign = ga.leiden(nodes, edges)
            qualities.append(ga.modularity(nodes, edges, assign))
            self.assertTrue(all(_connected(g, edges) for g in _groups(assign).values()))
        self.assertAlmostEqual(qualities[0], 0.4374652777777778)
        self.assertGreater(qualities[1], qualities[0] + 0.02)
        self.assertGreaterEqual(qualities[2], qualities[1])

    def test_large_sparse_graph_reaches_reference_quality(self):
        nodes, edges = _random_graph(2500, 5000, 20260928)
        assign = ga.leiden(nodes, edges)
        self.assertGreater(ga.modularity(nodes, edges, assign), 0.542)
        self.assertTrue(all(_connected(g, edges) for g in _groups(assign).values()))

    def test_edges_can_be_consumed_once(self):
        nodes, edges = _random_graph(60, 120, 1)
        self.assertEqual(ga.leiden(nodes, edges), ga.leiden(nodes, iter(edges)))

    def test_two_cliques_joined_by_one_edge_split_in_two(self):
        a = [f"a{i}" for i in range(5)]
        b = [f"b{i}" for i in range(5)]
        edges = [(x, y) for grp in (a, b) for i, x in enumerate(grp) for y in grp[i + 1:]]
        edges.append(("a0", "b0"))
        groups = _groups(ga.leiden(a + b, edges))
        self.assertEqual(sorted(sorted(g) for g in groups.values()), [sorted(a), sorted(b)])


class Shape(unittest.TestCase):

    def test_isolated_nodes_are_minus_one(self):
        res = ga.leiden(["a", "b", "lonely"], [("a", "b")])
        self.assertEqual(res["lonely"], ga.ISOLATED)
        self.assertEqual(res["a"], res["b"])

    def test_no_edges_at_all(self):
        self.assertEqual(ga.leiden(["a", "b"], []), {"a": ga.ISOLATED, "b": ga.ISOLATED})

    def test_numbers_go_from_the_biggest_community(self):
        edges = [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y")]
        res = ga.leiden(["a", "b", "c", "x", "y"], edges)
        self.assertEqual({res["a"], res["b"], res["c"]}, {0})
        self.assertEqual({res["x"], res["y"]}, {1})

    def test_self_loops_are_ignored_and_duplicate_edges_add_weight(self):
        nodes, edges = _karate()
        loops = [(n, n) for n in nodes[:5]]
        self.assertEqual(ga.leiden(nodes, edges), ga.leiden(nodes, edges + loops))
        doubled = edges + edges[:10]
        weighted = [(a, b, 2.0 if (a, b) in edges[:10] else 1.0) for a, b in edges]
        self.assertEqual(ga.leiden(nodes, doubled), ga.leiden(nodes, weighted))

    def test_an_edge_endpoint_missing_from_nodes_is_added(self):
        res = ga.leiden(["a"], [("a", "b")])
        self.assertEqual(res["a"], res["b"])


class Components(unittest.TestCase):

    def test_components_and_orphans(self):
        res = ga.weakly_connected_components(["a", "b", "c", "lonely"],
                                             [("a", "b"), ("b", "c")])
        self.assertEqual(res, {"a": 0, "b": 0, "c": 0, "lonely": 1})

    def test_direction_is_ignored(self):
        res = ga.weakly_connected_components(["a", "b", "c"], [("b", "a"), ("c", "b")])
        self.assertEqual(len(set(res.values())), 1)


class Quality(unittest.TestCase):
    """`community_quality` — 숫자만 싣고 판정은 안 한다. 읽는 쪽이 믿을 수 있는 숫자인지를 잰다."""

    def test_the_shares_add_up_to_the_modularity(self):
        """몫의 합이 전체 모듈러리티와 같아야 몫이라 부를 수 있다."""
        nodes, edges = _karate()
        assign = ga.leiden(nodes, edges)
        qual = ga.community_quality(nodes, edges, assign)
        self.assertAlmostEqual(sum(v["modularity_share"] for v in qual.values()),
                               ga.modularity(nodes, edges, assign), places=3)

    def test_leiden_communities_always_read_connected(self):
        nodes, edges = _random_graph(400, 900, seed=7)
        qual = ga.community_quality(nodes, edges, ga.leiden(nodes, edges))
        self.assertTrue(qual)
        self.assertTrue(all(v["connected"] for v in qual.values()))

    def test_a_split_community_reads_disconnected(self):
        """Leiden 이 아닌 배정을 넣으면 끊긴 것을 끊겼다고 말한다 — 지표가 늘 참을 돌려주는
        장식이 아니라는 확인."""
        nodes = ["a", "b", "c", "d"]
        edges = [("a", "b"), ("c", "d")]
        qual = ga.community_quality(nodes, edges, {"a": 0, "b": 0, "c": 0, "d": 0})
        self.assertFalse(qual[0]["connected"])

    def test_two_cliques_joined_by_one_edge(self):
        left = [f"l{i}" for i in range(4)]
        right = [f"r{i}" for i in range(4)]
        edges = [(a, b) for g in (left, right) for i, a in enumerate(g) for b in g[i + 1:]]
        edges.append(("l0", "r0"))
        assign = {n: 0 for n in left} | {n: 1 for n in right}
        qual = ga.community_quality(left + right, edges, assign)
        for c in (0, 1):
            self.assertEqual(qual[c]["size"], 4)
            self.assertEqual(qual[c]["internal_edges"], 6)
            self.assertEqual(qual[c]["boundary_edges"], 1)
            self.assertEqual(qual[c]["density"], 1.0)
            self.assertAlmostEqual(qual[c]["conductance"], 1 / 13, places=4)
            self.assertGreater(qual[c]["modularity_share"], 0)

    def test_isolated_nodes_are_left_out(self):
        qual = ga.community_quality(["a", "b", "z"], [("a", "b")],
                                    {"a": 0, "b": 0, "z": ga.ISOLATED})
        self.assertEqual(set(qual), {0})


if __name__ == "__main__":
    unittest.main()
