"""Topic contexts read structure without a graph batch or a data repair."""
import unittest
from unittest.mock import patch

from api import fragment, query
from store import pkns
from test_decision_type import _fresh_store, _restore

OWNER = "user:topic-detail"
TEAM = "team:topic-detail"


class TopicContextTests(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()

    def tearDown(self):
        self.store.close()
        _restore(self.keep)

    def put(self, owner, nid, domain, kind="concept"):
        result = fragment.merge_fragment({"owner": owner, "nodes": [
            {"id": nid, "type": kind, "title": nid,
             **({"domain": domain} if kind != "experiment" else {})}]}, store=self.store)
        self.assertFalse(result.get("warnings"), result)

    def context(self, topic, owner=OWNER, offset=0):
        return query.node_context(owner=owner, node="domain:" + topic,
                                  links_offset=offset, store=self.store)

    def test_root_child_and_leaf_counts_are_distinct(self):
        self.put(OWNER, "concept:root", "project")
        self.put(OWNER, "record:child", "project/child", "record")
        self.put(OWNER, "episode:leaf", "project/child/leaf", "episode")
        root = self.context("project")
        self.assertTrue(root["executed"], root)
        self.assertEqual("domain", root["node_kind"])
        self.assertEqual({"path", "parent", "child_topics", "direct_nodes", "subtree_nodes",
                          "descendant_nodes", "structure", "description"}, set(root["topic"]))
        self.assertIsNone(root["topic"]["parent"])
        self.assertEqual((1, 1, 2, 3), tuple(root["topic"][k] for k in
                         ("child_topics", "direct_nodes", "descendant_nodes", "subtree_nodes")))
        child = self.context("project/child")["topic"]
        self.assertEqual("domain:project", child["parent"]["id"])
        leaf = self.context("project/child/leaf")
        self.assertEqual(0, leaf["topic"]["child_topics"])
        for invented in ("props", "tags", "rev", "updated_at", "temporal", "one_liner", "excerpts", "links"):
            self.assertNotIn(invented, leaf)

    def test_no_direct_members_is_a_success_and_bare_shared_domain_is_unavailable(self):
        self.put(OWNER, "concept:leaf", "project/child")
        result = self.context("project")
        self.assertTrue(result["executed"])
        self.assertEqual(0, result["topic"]["direct_nodes"])
        self.store.run("MERGE (:Domain {name:'empty'})")
        self.assertEqual(self.context("empty") | {"node": "same"},
                         self.context("missing") | {"node": "same"})
        self.assertEqual("unavailable", self.context("empty")["status"])

    def test_shared_paths_do_not_reveal_another_owner_or_team(self):
        with patch.dict("os.environ", {"VINC_PK_NAMESPACED": "on"}):
            for owner, suffix in ((OWNER, "mine"), (TEAM, "team"), ("user:other", "private")):
                self.put(owner, "concept:same", "project/" + suffix)
            for owner, suffix in ((OWNER, "mine"), (TEAM, "team"), ("user:other", "private")):
                result = self.context("project", owner)
                self.assertEqual(1, result["topic"]["subtree_nodes"])
                ids = [r["id"] for r in result["topic"]["structure"]["items"]]
                self.assertEqual(["domain:project/" + suffix], ids)
                denied = self.context("project/private", OWNER)
                missing = self.context("project/absent", OWNER)
                self.assertEqual(denied | {"node": "same"}, missing | {"node": "same"})

    def test_structure_continuation_reaches_every_member_once(self):
        for i in range(29):
            self.put(OWNER, f"concept:n{i:02}", "project")
        first = self.context("project")["topic"]["structure"]
        second = self.context("project", offset=first["next_offset"])["topic"]["structure"]
        self.assertEqual(29, first["total"])
        self.assertTrue(first["truncated"])
        self.assertFalse(second["truncated"])
        self.assertEqual(29, len({r["id"] for r in first["items"] + second["items"]}))

    def test_query_failure_and_unsupported_kind_do_not_report_empty(self):
        self.put(OWNER, "concept:mine", "project")
        with patch.object(self.store, "run", side_effect=RuntimeError("fixture offline")):
            result = self.context("project")
        self.assertEqual("query_failed", result["status"])
        self.assertFalse(result["executed"])
        self.assertNotIn("topic", result)
        self.put(OWNER, "experiment:mine", "", "experiment")
        unsupported = query.node_context(owner=OWNER, node="experiment:mine", store=self.store)
        self.assertEqual("unsupported_kind", unsupported["status"])
        inaccessible = query.node_context(owner=TEAM, node="experiment:mine", store=self.store)
        self.assertEqual("unavailable", inaccessible["status"])

    def test_drift_is_reported_without_writing_or_repairing(self):
        self.put(OWNER, "concept:mine", "project")
        self.store.run("MATCH (n:Concept)-[r:IN_DOMAIN]->(:Domain) WHERE n.concept_id=$id DELETE r",
                       {"id": pkns.key(OWNER, "concept:mine")})
        with patch.object(self.store, "run", wraps=self.store.run) as run:
            result = self.context("project")
        self.assertTrue(result["executed"], result)
        self.assertEqual(1, result["topic"]["consistency"]["column_direct_nodes"])
        self.assertEqual(0, result["topic"]["direct_nodes"])
        self.assertTrue(all(not any(word in call.args[0] for word in ("MERGE", "DELETE", " SET "))
                            for call in run.call_args_list))

    def test_ordinary_edgeless_node_context_remains_successful(self):
        self.put(OWNER, "concept:ordinary", "project")
        result = query.node_context(owner=OWNER, node="concept:ordinary", store=self.store)
        self.assertTrue(result["executed"], result)
        self.assertTrue(result["links"]["executed"])
        self.assertEqual(0, result["links"]["total"])
        self.assertNotIn("topic", result)
