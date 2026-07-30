# -*- coding: utf-8 -*-
"""api/query.nav_tree — 좌측 문서 트리(D69) 계약 테스트.

  1. **순수 층**(드라이버 불요): 깊이 산출 `_domain_depths` — 체인·다이아몬드·
     사이클·고립 개념. 방향 정규화(DERIVES_FROM 뒤집기)는 호출측 책임이라 여기서는
     "정규화된 엣지가 들어오면 층이 맞는가"만 본다.
  2. **실엔진 층**(ladybug 미설치 시 skip): 관계 종류별 뒤집기, 교차 주제 엣지 무시,
     문서 정렬, 빈 주제 존치, 미분류 버킷, owner 격리.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class DomainDepthTests(unittest.TestCase):
    """`_domain_depths(concepts, edges)` — edges 는 (얕은 쪽, 깊은 쪽) 정규화 완료."""

    def _depths(self, ids, edges):
        return q._domain_depths({i: "d" for i in ids}, edges)

    def test_chain(self):
        got = self._depths(["a", "b", "c"], [("a", "b"), ("b", "c")])
        self.assertEqual(got, {"a": 0, "b": 1, "c": 2})

    def test_isolated_concepts_are_roots(self):
        # 엣지가 하나도 없으면 전부 0층 — 계층이 없는 것을 "깊다"고 하지 않는다.
        self.assertEqual(self._depths(["x", "y"], []), {"x": 0, "y": 0})

    def test_diamond_takes_shortest_layer(self):
        # a→b, a→c, b→d, c→d. BFS 라 d 는 2층(가장 이른 도달).
        got = self._depths(["a", "b", "c", "d"],
                           [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
        self.assertEqual(got, {"a": 0, "b": 1, "c": 1, "d": 2})

    def test_pure_cycle_terminates_and_lands_on_floor(self):
        # 순수 사이클은 진입차수 0 이 없다 — Kahn 이었으면 영영 안 벗겨진다.
        got = self._depths(["p", "q"], [("p", "q"), ("q", "p")])
        self.assertEqual(got, {"p": 0, "q": 0})          # 루트가 없으니 바닥 한 층
        # 체인 + 매달린 사이클: 체인은 정상 층, 사이클은 그 아래 한 층에 모인다.
        got2 = self._depths(["a", "b", "p", "q"],
                            [("a", "b"), ("p", "q"), ("q", "p")])
        self.assertEqual((got2["a"], got2["b"]), (0, 1))
        self.assertEqual((got2["p"], got2["q"]), (2, 2))

    def test_self_loop_ignored(self):
        self.assertEqual(self._depths(["a", "b"], [("a", "a"), ("a", "b")]),
                         {"a": 0, "b": 1})

    def test_depth_is_capped(self):
        n = q._DEPTH_CAP + 20
        ids = [f"n{i:03d}" for i in range(n)]
        got = self._depths(ids, [(ids[i], ids[i + 1]) for i in range(n - 1)])
        self.assertLessEqual(max(got.values()), q._DEPTH_CAP)
        self.assertEqual(len(got), n)                    # 상한을 넘어도 빠지지 않는다


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NavTreeE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _concept(self, cid, domain, owner=None, title=None):
        self.store.run(
            "MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.domain=$d, n.owner_id=$o",
            {"id": cid, "t": title or cid.split(":")[-1], "d": domain,
             "o": owner or self.OWNER})

    def _rel(self, rel, a, b):
        self.store.run(
            f"MATCH (a:Concept {{concept_id:$a}}), (b:Concept {{concept_id:$b}}) "
            f"MERGE (a)-[:{rel}]->(b)", {"a": a, "b": b})

    def _doc(self, body, title, owner=None):
        return ds.put_doc(self.store, owner=owner or self.OWNER, text=body,
                          title=title)["doc_id"]

    def _tree(self, owner=None):
        res = q.nav_tree(owner=owner or self.OWNER, store=self.store)
        self.assertTrue(res["executed"], res.get("note"))
        return res

    def _group(self, res, domain):
        return next(g for g in res["domains"] if g["domain"] == domain)

    def test_prerequisite_keeps_direction_derives_flips(self):
        # 같은 모양의 두 주제. prereq 는 그대로, derives 는 뒤집혀야 층이 반대가 된다.
        for cid in ("concept:base", "concept:leaf"):
            self._concept(cid, "phys")
        self._rel("PREREQUISITE_OF", "concept:base", "concept:leaf")
        for cid in ("concept:source", "concept:derived"):
            self._concept(cid, "math")
        self._rel("DERIVES_FROM", "concept:derived", "concept:source")

        self._doc("base 를 다룬 글.", "P0")
        self._doc("leaf 를 다룬 글.", "P1")
        self._doc("source 를 다룬 글.", "M0")
        self._doc("derived 를 다룬 글.", "M1")
        res = self._tree()
        phys = {d["title"]: d["depth"] for d in self._group(res, "phys")["docs"]}
        math = {d["title"]: d["depth"] for d in self._group(res, "math")["docs"]}
        self.assertEqual(phys, {"P0": 0, "P1": 1})
        # derived→source 의 DERIVES_FROM 은 source 가 얕다. 뒤집기를 빼먹으면
        # 여기가 {"M0": 1, "M1": 0} 으로 뒤집힌 채 조용히 통과한다.
        self.assertEqual(math, {"M0": 0, "M1": 1})

    def test_cross_domain_edge_does_not_set_depth(self):
        self._concept("concept:alpha", "phys")
        self._concept("concept:beta", "math")
        self._rel("PREREQUISITE_OF", "concept:alpha", "concept:beta")
        self._doc("beta 를 다룬 글.", "B")
        res = self._tree()
        # 주제를 가로지르는 선행 관계는 math 안의 계층이 아니다 — beta 는 여전히 뿌리.
        self.assertEqual(self._group(res, "math")["docs"][0]["depth"], 0)

    def test_docs_sorted_by_depth_then_title(self):
        for cid in ("concept:aa", "concept:bb", "concept:cc"):
            self._concept(cid, "phys")
        self._rel("PREREQUISITE_OF", "concept:aa", "concept:bb")
        self._rel("PREREQUISITE_OF", "concept:bb", "concept:cc")
        self._doc("cc 를 다룬 글.", "zeta")      # 2층이지만 제목은 마지막
        self._doc("aa 를 다룬 글.", "yota")      # 0층
        self._doc("bb 를 다룬 글.", "alpha")     # 1층
        docs = self._group(self._tree(), "phys")["docs"]
        self.assertEqual([d["title"] for d in docs], ["yota", "alpha", "zeta"])
        self.assertEqual([d["depth"] for d in docs], [0, 1, 2])
        self.assertEqual(docs[0]["node_id"], "concept:aa")   # 착지 노드 동반

    def test_empty_domain_kept_and_unlinked_doc_unfiled(self):
        self._concept("concept:lonely", "art")     # 개념만 있고 문서 없음
        self._doc("어떤 개념도 부르지 않는 글.", "떠도는 글")
        res = self._tree()
        art = self._group(res, "art")
        self.assertEqual((art["docs"], art["concepts"]), ([], 1))
        self.assertEqual([d["title"] for d in res["unfiled"]], ["떠도는 글"])
        self.assertEqual(res["counts"]["unfiled"], 1)

    def test_owner_isolation(self):
        self._concept("concept:mine", "phys")
        self._concept("concept:theirs", "phys", owner="user:b")
        self._doc("mine 를 다룬 글.", "내 글")
        self._doc("theirs 를 다룬 글.", "남의 글", owner="user:b")
        mine = self._tree()
        self.assertEqual([d["title"] for g in mine["domains"] for d in g["docs"]], ["내 글"])
        self.assertEqual(self._group(mine, "phys")["concepts"], 1)
        theirs = self._tree(owner="user:b")
        self.assertEqual([d["title"] for g in theirs["domains"] for d in g["docs"]],
                         ["남의 글"])

    def test_cycle_in_store_terminates(self):
        # 사이클이 있어도 응답이 온다(무한 루프·타임아웃 회귀 방지).
        # ⚠ 개념 제목은 2글자 이상이어야 링커가 문다(토큰 길이 하한 2 —
        # engines.mentions_link._tokens). 한 글자 제목은 링크가 안 걸려 미분류로 샌다.
        for cid in ("concept:pp", "concept:qq"):
            self._concept(cid, "phys")
        self._rel("PREREQUISITE_OF", "concept:pp", "concept:qq")
        self._rel("PREREQUISITE_OF", "concept:qq", "concept:pp")
        self._doc("pp 를 다룬 글.", "P")
        docs = self._group(self._tree(), "phys")["docs"]
        self.assertEqual(len(docs), 1)
        self.assertLessEqual(docs[0]["depth"], q._DEPTH_CAP)


if __name__ == "__main__":
    unittest.main()
