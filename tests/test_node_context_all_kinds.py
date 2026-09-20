# -*- coding: utf-8 -*-
"""VIN-89 — Bridge 와 개방 타입(Entity)도 `node_context` 가 읽는다.

두 종은 **그래프에는 있는데** 이 표면만 "unknown node" 로 답했다(실측 2026-09-20,
VIN-86 이 먼저 관측). 주제 트리에 못 서는 것은 스키마다(`IN_DOMAIN` 이 네 라벨에만
걸린다) — 그러나 그것이 "읽을 수 없다"는 뜻은 아니었고, 노드를 못 열면 전체 목록에서
닿아도 빈 화면이 나온다.

근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §3]] — Bridge 와 Entity 는 나브
트리 질의가 자리를 못 준다는 이유로 사라져서는 안 된다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeContextAllKindsE2E(unittest.TestCase):
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

    def _k(self, i):
        return pkns.key(self.OWNER, i)

    def _ctx(self, node):
        return q.node_context(owner=self.OWNER, node=node, store=self.store)

    def _seed_bridge(self):
        self.store.run(
            "MERGE (c:Concept {concept_id:$i}) SET c.title='왼편', c.domain='a', "
            "c.owner_id=$o", {"i": self._k("concept:left"), "o": self.OWNER})
        self.store.run(
            "MERGE (b:Bridge {bridge_id:$i}) SET b.title='다리', b.owner_id=$o, "
            "b.status='live', b.source_domain='a', b.target_domain='b', "
            "b.props='{\"why\":\"실측\"}'",
            {"i": self._k("bridge:ab"), "o": self.OWNER})
        self.store.run("MERGE (d:Domain {name:'b'})")
        self.store.run(
            "MATCH (b:Bridge {bridge_id:$b}), (c:Concept {concept_id:$c}) "
            "MERGE (b)-[:CONNECTS {role:'anchor'}]->(c)",
            {"b": self._k("bridge:ab"), "c": self._k("concept:left")})
        self.store.run(
            "MATCH (b:Bridge {bridge_id:$b}), (d:Domain {name:'b'}) "
            "MERGE (b)-[:CROSSES]->(d)", {"b": self._k("bridge:ab")})

    def _seed_entities(self):
        for eid, title in (("theory:duality", "쌍대성"), ("theory:other", "딴것")):
            self.store.run(
                "MERGE (e:Entity {entity_id:$i}) SET e.type='theory', e.title=$t, "
                "e.owner_id=$o, e.tags=['x','y']",
                {"i": self._k(eid), "t": title, "o": self.OWNER})
        self.store.run(
            "MATCH (a:Entity {entity_id:$a}), (b:Entity {entity_id:$b}) "
            "MERGE (a)-[:TYPED {type:'refines'}]->(b)",
            {"a": self._k("theory:duality"), "b": self._k("theory:other")})

    def test_a_bridge_reads_with_its_endpoints(self):
        self._seed_bridge()
        res = self._ctx("bridge:ab")
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["node_kind"], "bridge")
        self.assertEqual(res["title"], "다리")
        # Bridge 에는 `domain` 열이 없다 — 지어내지 않고 잇는 두 주제를 따로 낸다.
        self.assertEqual(res["domain"], "")
        self.assertEqual(res["bridge"], {"status": "live", "source_domain": "a",
                                         "target_domain": "b"})
        self.assertEqual(res["props"], {"why": "실측"})
        kinds = {(i["type"], i["id"]): i["note"] for i in res["links"]["items"]}
        self.assertEqual(kinds, {("CONNECTS", "concept:left"): "anchor",
                                 ("CROSSES", "domain:b"): ""})

    def test_an_authored_open_kind_reads_as_its_own_type(self):
        self._seed_entities()
        res = self._ctx("theory:duality")
        self.assertTrue(res["executed"], res.get("note"))
        # 캐리어 이름('entity')이 아니라 **저작된 타입**이 종이다.
        self.assertEqual(res["node_kind"], "theory")
        self.assertEqual(res["title"], "쌍대성")
        self.assertEqual(res["tags"], ["x", "y"])
        # 개방 타입에는 props 도 domain 도 없다(스키마). 빈 값이지 지어낸 값이 아니다.
        self.assertEqual(res["props"], {})
        self.assertEqual(res["domain"], "")

    def test_a_typed_edge_keeps_its_direction_at_both_ends(self):
        # 같은 엣지를 양 끝에서 보면 방향이 달라야 한다 — 한쪽으로 접으면
        # 화면이 반대 방향을 잃는다.
        self._seed_entities()
        a = self._ctx("theory:duality")["links"]
        b = self._ctx("theory:other")["links"]
        self.assertEqual([(i["dir"], i["type"], i["id"]) for i in a["items"]],
                         [("out", "refines", "theory:other")])
        self.assertEqual([(i["dir"], i["type"], i["id"]) for i in b["items"]],
                         [("in", "refines", "theory:duality")])
        self.assertEqual((a["out"], a["in"]), (1, 0))
        self.assertEqual((b["out"], b["in"]), (0, 1))

    def test_another_owners_bridge_and_entity_stay_unknown(self):
        # SEC-1 — 남의 노드는 제목도 연결수도 새지 않는다. 없는 것과 같은 답이다.
        self._seed_bridge()
        self._seed_entities()
        for node in ("bridge:ab", "theory:duality"):
            res = q.node_context(owner="user:b", node=node, store=self.store)
            self.assertFalse(res["executed"], f"{node} 가 남의 소유자에게 열렸다")
            self.assertIn("unknown node", res.get("note", ""))
            self.assertEqual(res["title"] if "title" in res else "", "")

    def test_a_missing_id_still_says_unknown(self):
        res = self._ctx("bridge:nope")
        self.assertFalse(res["executed"])
        self.assertIn("unknown node", res.get("note", ""))


if __name__ == "__main__":
    unittest.main()
