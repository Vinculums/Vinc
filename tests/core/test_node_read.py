"""Exact node reads round-trip stored fields without traversing the graph."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from api import fragment, query
from store import pkns
from test_decision_type import _fresh_store, _restore

OWNER = "user:node-read"


class NodeReadTests(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()

    def tearDown(self):
        self.store.close()
        _restore(self.keep)

    def put(self, nodes, edges=(), owner=OWNER):
        result = fragment.merge_fragment(
            {"owner": owner, "nodes": nodes, "edges": list(edges)}, store=self.store)
        self.assertFalse(result.get("warnings"), result)
        return result

    def test_fields_round_trip_for_every_stored_kind(self):
        nodes = [
            {"id": "concept:c", "type": "concept", "title": "C", "domain": "vinc/test",
             "one_liner": "Stored description", "tags": ["one"], "props": {"nested": {"list": [1, True]}},
             "content_time": "2025-08"},
            {"id": "record:r", "type": "record", "title": "R", "domain": "vinc/test",
             "tags": ["two"], "props": {"claims": [{"claim": "yes", "evidence_ids": ["concept:c"]}]},
             "content_time": "2025", "valid_from": "2024-01", "valid_until": "2025-12"},
            {"id": "decision:d", "type": "decision", "title": "D", "domain": "vinc/test",
             "tags": ["three"], "props": {"reason": "evidence"}},
            {"id": "episode:e", "type": "episode", "title": "E", "domain": "vinc/test",
             "tags": ["four"], "props": {"outcome": "success"}, "date": "2025-01-02",
             "summary": "The measured run"},
            {"id": "bridge:b", "type": "bridge", "title": "B", "props": {"crossing": "two"}},
            {"id": "experiment:x", "type": "experiment", "title": "X", "tags": ["open"]},
        ]
        self.put(nodes, [
            {"from": "decision:d", "to": "concept:c", "type": "about", "evidence": "Fixture"}
        ])
        for authored in nodes:
            with self.subTest(kind=authored["type"]):
                result = query.node(authored["id"], owner=OWNER, store=self.store)
                self.assertTrue(result["found"], result)
                self.assertNotIn("rows", result)
                node = result["node"]
                for key, value in authored.items():
                    if key != "type":
                        self.assertEqual(node[key], value, key)
                self.assertEqual(node["kind"], authored["type"])
                self.assertEqual(node["owner_id"], OWNER)
                self.assertNotIn("x", node)
                self.assertNotIn("degree", node)
                self.assertNotIn("edges", result)
                if authored["type"] not in ("concept", "record"):
                    self.assertNotIn("content_time", node)
                if authored["type"] != "record":
                    self.assertNotIn("valid_from", node)

    def test_one_query_and_size_independent_of_neighbors(self):
        seed = {"id": "concept:seed", "type": "concept", "title": "Seed",
                "domain": "vinc/test", "props": {"payload": "x" * 8192}}
        self.put([seed])
        before = query.node(seed["id"], owner=OWNER, store=self.store)
        leaves = [{"id": f"concept:leaf{i}", "type": "concept", "title": "Leaf",
                   "domain": "vinc/test", "props": {"payload": "y" * 2000}} for i in range(8)]
        edges = [{"from": seed["id"], "to": leaf["id"], "type": "relates_to"} for leaf in leaves]
        self.put([seed, *leaves], edges)
        with patch.object(self.store, "run", wraps=self.store.run) as run:
            after = query.node(seed["id"], owner=OWNER, store=self.store)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(after["node"]["props"], before["node"]["props"])
        self.assertLess(abs(len(json.dumps(after)) - len(json.dumps(before))), 100)
        self.assertLess(len(json.dumps(after)), 10000)

    def test_other_owner_and_absent_id_are_indistinguishable(self):
        for kind in ("concept", "record", "decision", "episode", "bridge", "experiment"):
            node_id = f"{kind}:private"
            nodes = [{"id": node_id, "type": kind, "title": "Secret"}]
            edges = []
            if kind == "decision":
                nodes.append({"id": "concept:subject", "type": "concept", "title": "Subject"})
                edges.append({"from": node_id, "to": "concept:subject", "type": "about",
                              "evidence": "Fixture"})
            self.put(nodes, edges, owner="user:other")
            result = query.node(node_id, owner=OWNER, store=self.store)
            missing = query.node(f"{kind}:missing", owner=OWNER, store=self.store)
            self.assertEqual(result, missing)
            self.assertFalse(result["found"])
            self.assertIsNone(result["node"])

    def test_same_logical_id_in_distinct_spaces(self):
        with patch.dict("os.environ", {"VINC_PK_NAMESPACED": "on"}):
            for owner, marker in ((OWNER, "personal"), ("team:space-a", "space-a"),
                                  ("team:space-b", "space-b")):
                self.put([{"id": "record:same", "type": "record", "title": marker,
                           "props": {"marker": marker}}], owner=owner)
            for owner, marker in ((OWNER, "personal"), ("team:space-a", "space-a"),
                                  ("team:space-b", "space-b")):
                result = query.node("record:same", owner=owner, store=self.store)
                self.assertEqual(result["node"]["id"], "record:same")
                self.assertEqual(result["node"]["props"], {"marker": marker})

    def test_ownerless_bridge_is_not_exposed_as_owned(self):
        self.store.run("CREATE (b:Bridge {bridge_id: $id, title: 'Unowned', owner_id: ''})",
                       {"id": pkns.key(OWNER, "bridge:unowned")})
        self.assertFalse(query.node("bridge:unowned", owner=OWNER, store=self.store)["found"])

    def test_store_failure_is_not_a_missing_node(self):
        with patch.object(self.store, "run", side_effect=RuntimeError("offline")):
            result = query.node("concept:x", owner=OWNER, store=self.store)
        self.assertFalse(result["executed"])
        self.assertNotIn("found", result)
        self.assertIn("store query failed", result["error"])


if __name__ == "__main__":
    unittest.main()
