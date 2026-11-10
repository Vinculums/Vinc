# -*- coding: utf-8 -*-
"""순회 질의를 GQL 표준형으로 옮겨도 결과가 그대로인가(엔진 방언 B2, traversal 계열).

`api/query.py` 의 순회 여섯은 수량 관계 `-[]-{1,d}` 와 선택자 `SHORTEST 1`·`ALL SHORTEST` 로
쓰고, 임베디드 어댑터(`to_engine_cypher`)가 ladybug 의 `[* TRAIL|SHORTEST|ALL SHORTEST a..b]`
로 바꿔 보낸다. 잠그는 것:

- 번역이 그 꼴만 바꾸고 속성 맵·맨 관계·다른 문장은 건드리지 않는다.
- 사이클(시드로 돌아오는 길)과 소유자 둘이 있는 스토어에서 **옛 ladybug 질의와 새 플랜이 같은
  행을 낸다**. 옛 질의는 바꾸기 직전 main 의 문자열을 그대로 옮겨 둔 것이다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import pkns  # noqa: E402
from store.embedded_backend import _import_driver, to_engine_cypher  # noqa: E402
from _pkseed import _seed  # noqa: E402

_DRIVER, _NAME = _import_driver()


class TranslateTests(unittest.TestCase):
    def test_선택자_없는_수량_관계는_TRAIL_이다(self):
        self.assertEqual(to_engine_cypher("MATCH path = (n)-[]-{1,3}(m) RETURN n"),
                         "MATCH path = (n)-[* TRAIL 1..3]-(m) RETURN n")

    def test_선택자는_관계_괄호_안으로_들어간다(self):
        self.assertEqual(
            to_engine_cypher("MATCH SHORTEST 1 (n)-[]-{1,2}(m:Concept) WHERE m.x = 1 RETURN m"),
            "MATCH (n)-[* SHORTEST 1..2]-(m:Concept) WHERE m.x = 1 RETURN m")
        self.assertEqual(
            to_engine_cypher("MATCH p = ALL SHORTEST (a)-[]-{1,6}(b) RETURN nodes(p)"),
            "MATCH p = (a)-[* ALL SHORTEST 1..6]-(b) RETURN nodes(p)")

    def test_방향과_관계_이름도_지킨다(self):
        self.assertEqual(to_engine_cypher("MATCH (a)-[r:A|B]->{2,4}(b) RETURN b"),
                         "MATCH (a)-[r:A|B* TRAIL 2..4]->(b) RETURN b")

    def test_속성_맵과_한_칸_관계는_그대로다(self):
        q = "MATCH (n:Concept {concept_id: $x})-[r:RELATED_TO]-(m) RETURN m"
        self.assertIs(to_engine_cypher(q), q)


#: 바꾸기 직전(main 315db9a1) `api/query.py` 의 ladybug 질의. 비교의 기준이다.
def _old(name: str, depth: int, owner: bool, seed_label: str = "Concept") -> str:
    pk = {"Concept": "concept_id", "Decision": "decision_id"}[seed_label]
    nw = f"n.{pk} = $seed" + (" AND n.owner_id = $owner" if owner else "")
    mw = " WHERE m.owner_id = $owner" if owner else ""
    if name == "subgraph_ids":
        return (f"MATCH (n:{seed_label}) WHERE {nw} "
                f"MATCH (n)-[* SHORTEST 1..{depth}]-(m:Concept){mw} "
                "RETURN DISTINCT m.concept_id AS id")
    if name == "subgraph_hops":
        return (f"MATCH (n:{seed_label}) WHERE {nw} "
                f"MATCH (n)-[r* SHORTEST 1..{depth}]-(m:Concept){mw} "
                "RETURN m.concept_id AS id, min(length(r)) AS hops")
    if name == "path_candidates":
        return (f"MATCH (n:Concept) WHERE {nw} "
                f"MATCH (n)-[* SHORTEST 1..{depth}]-(m:Concept){mw} "
                "RETURN DISTINCT m.concept_id AS id")
    if name == "entity_subgraph_ids":
        nw = "n.entity_id = $seed" + (" AND n.owner_id = $owner" if owner else "")
        return (f"MATCH (n:Entity) WHERE {nw} "
                f"MATCH (n)-[* TRAIL 1..{depth}]-(m:Entity){mw} "
                "RETURN DISTINCT m.entity_id AS id")
    raise KeyError(name)


def _old_decide(depth: int) -> str:
    return ("MATCH (n) WHERE n.concept_id = $tkey OR n.bridge_id = $tkey "
            "OR n.title = $topic "
            f"MATCH path = (n)-[* TRAIL 1..{depth}]-(m) "
            "RETURN n, relationships(path) AS rels, m "
            "LIMIT 100")


def _old_shortest(depth: int, owner: bool) -> str:
    aw, bw = "a.concept_id = $a", "b.concept_id = $b"
    if owner:
        aw += " AND a.owner_id = $owner"
        bw += " AND b.owner_id = $owner"
    return (f"MATCH p = (a:Concept)-[* ALL SHORTEST 1..{depth}]-(b:Concept) "
            f"WHERE {aw} AND {bw} "
            "RETURN nodes(p) AS ns, length(p) AS len ORDER BY len LIMIT 25")


def _canon(rows: list[dict]) -> Counter:
    """행을 비교할 수 있는 꼴로. 노드·관계는 PK·제목·타입만 남긴다(내부 id 는 엔진 것)."""
    def v(x):
        if isinstance(x, dict):
            keep = {k: x[k] for k in sorted(x) if k.endswith("_id") or k in ("title", "_label")}
            return tuple(sorted((k, v(val)) for k, val in keep.items()))
        if isinstance(x, list):
            return tuple(v(i) for i in x)
        return x
    return Counter(tuple(sorted((k, v(val)) for k, val in r.items())) for r in rows)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class SameRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._saved = {}
        for k, val in {"MEMORY_STORE_BACKEND": "embedded",
                       "MEMORY_DATA_DIR": os.path.join(cls._tmp, "store"),
                       "MEMORY_EMBEDDING_MODEL": "local-en"}.items():
            cls._saved[k] = os.environ.get(k)
            os.environ[k] = val
        from store.backend import open_store
        from store.config import load_config
        cls.cfg = load_config()
        cls.store = open_store(cls.cfg)
        cls.store.provision(dim=cls.cfg.embedding_dim)
        with cls.store.session() as s:
            for owner, ids in (("u", ("s", "n1", "n2", "n3", "n4", "n5", "x")),
                               ("v", ("vs", "w1", "w2"))):
                for cid in ids:
                    _seed(s.run, "MERGE (n:Concept {concept_id:$id}) SET n.owner_id=$o, "
                          "n.title=$t, n.domain='m'", owner=owner, id=cid, o=owner, t=cid)
            # 사이클 s-n1-n2-s, 꼬리 n2-n3-n4-n5, 지름길 n1-n3 과 s-x-n3. 그래서 s 에서 n3 까지
            # 길이 2 인 최단이 셋이다(s-n1-n3 · s-n2-n3 · s-x-n3). 소유자 v 의 vs-s 는 소유자를
            # 건너는 엣지다(쓰기 API 는 만들지 않지만 owner 술어가 도달 노드를 거르는지 본다).
            for owner, pairs in (("u", (("s", "n1"), ("n1", "n2"), ("n2", "s"), ("n2", "n3"),
                                        ("n3", "n4"), ("n4", "n5"), ("n1", "n3"),
                                        ("s", "x"), ("x", "n3"))),
                                 ("v", (("vs", "w1"), ("w1", "w2"), ("w2", "vs"), ("vs", "s")))):
                for a, b in pairs:
                    _seed(s.run, "MATCH (p:Concept {concept_id:$a}), (t:Concept {concept_id:$b}) "
                          "MERGE (p)-[:RELATED_TO]->(t)", owner=owner, a=a, b=b)
            _seed(s.run, "MERGE (d:Decision {decision_id:'dec'}) SET d.owner_id='u', d.title='dec'")
            _seed(s.run, "MATCH (d:Decision {decision_id:'dec'}), (c:Concept {concept_id:'n4'}) "
                  "MERGE (d)-[:PREREQUISITE_OF]->(c)")
            for eid in ("e1", "e2", "e3"):
                _seed(s.run, "MERGE (e:Entity {entity_id:$id}) SET e.owner_id='u', e.title=$t, "
                      "e.type='thing'", id=eid, t=eid)
            for a, b in (("e1", "e2"), ("e2", "e3"), ("e3", "e1")):
                _seed(s.run, "MATCH (p:Entity {entity_id:$a}), (t:Entity {entity_id:$b}) "
                      "MERGE (p)-[:TYPED {type:'links'}]->(t)", a=a, b=b)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        for k, val in cls._saved.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val

    @staticmethod
    def owners():
        # owner 없는 읽기는 로컬 단일 사용자 경로다. 키 합성이 켜지면 논리 키로는 못 찾는다.
        return ("u",) if pkns.enabled() else ("u", None)

    def same(self, plan, old_cypher):
        new = self.store.run(plan.cypher, plan.params)
        old = self.store.run(old_cypher, plan.params)
        self.assertTrue(old, f"기준 질의가 빈 결과라 비교가 무의미하다: {plan.kind}")
        self.assertEqual(_canon(new), _canon(old), plan.kind)

    def test_도달_집합과_홉_수(self):
        from api import query as q
        for owner in self.owners():
            for depth in (1, 2, 3, 4):
                for seed, label in (("s", "Concept"), ("dec", "Decision")):
                    self.same(q.subgraph_ids_plan(seed, depth=depth, owner=owner, seed_label=label),
                              _old("subgraph_ids", depth, bool(owner), label))
                    self.same(q.subgraph_hops_plan(seed, depth=depth, owner=owner, seed_label=label),
                              _old("subgraph_hops", depth, bool(owner), label))
            for depth in (2, 6, 8):
                self.same(q.path_candidates_plan("s", depth=depth, owner=owner),
                          _old("path_candidates", depth, bool(owner)))

    def test_엔티티_순회는_사이클로_시드에_돌아온다(self):
        from api import query as q
        for depth in (1, 2, 3):
            plan = q.entity_subgraph_ids_plan("e1", depth=depth, owner="u")
            self.same(plan, _old("entity_subgraph_ids", depth, True))
        ids = {r["id"] for r in self.store.run(plan.cypher, plan.params)}
        self.assertIn("e1", ids)   # TRAIL 의미 그대로: 시드로 돌아오는 길이 있으면 시드도 낸다

    def test_결정_순회와_최단_경로(self):
        from api import query as q
        for depth in (1, 2, 3):
            self.same(q.decide_plan("s", depth=depth, owner="u"), _old_decide(depth))
        for owner in self.owners():
            for a, b, depth in (("s", "n3", 6), ("n1", "n5", 8), ("s", "s", 4), ("x", "n4", 2)):
                plan = q.shortest_path_plan(a, b, max_depth=depth, owner=owner)
                new = self.store.run(plan.cypher, plan.params)
                old = self.store.run(_old_shortest(depth, bool(owner)), plan.params)
                self.assertEqual(_canon(new), _canon(old), (a, b, depth, owner))
        plan = q.shortest_path_plan("s", "n3", max_depth=6, owner="u")
        lens = [r["len"] for r in self.store.run(plan.cypher, plan.params)]
        self.assertEqual(lens, [2, 2, 2])   # 같은 길이 최단 셋을 다 낸다


if __name__ == "__main__":
    unittest.main()
