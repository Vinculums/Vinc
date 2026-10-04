"""Topic-root census must report anomalies without judging valid multi-root spaces."""
from pathlib import Path
import tempfile
import unittest

from api import fragment, query
from api.server import VincApp
from store.backend import open_store
from store.config import load_config


class DomainRoots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = open_store(load_config({"data_dir": str(Path(self.tmp.name) / "db")}))
        self.assertIsNotNone(self.store)
        self.store.provision(dim=4)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.store.close)

    def add(self, owner, domains):
        result = fragment.merge_fragment({"owner": owner, "nodes": [
            {"id": f"concept:{i}", "type": "concept", "title": f"Node {i}", "domain": d}
            for i, d in enumerate(domains)]}, store=self.store)
        self.assertTrue(result["executed"], result)

    def test_three_outside_nodes_are_visible_and_other_owner_is_absent(self):
        self.add("user:mine", ["vinc/core"] * 9 + ["product/one", "product/two", "engineering", ""])
        self.add("user:theirs", ["other/core"] * 20)
        before = query.domains(owner="user:mine", store=self.store)["rows"]
        result = query.domain_root_diagnosis(owner="user:mine", store=self.store, member_limit=1)
        self.assertEqual(result["largest_root"], "vinc")
        self.assertEqual(result["assigned_nodes"], 12)
        self.assertEqual(result["unassigned_nodes"], 1)
        self.assertEqual(result["outside_node_count"], 3)
        self.assertEqual(result["outside_domain_count"], 3)
        self.assertEqual(len(result["outside_largest_root"]), 1)
        self.assertTrue(result["members_truncated"])
        self.assertEqual({r["root"] for r in result["roots"]}, {"vinc", "product", "engineering"})
        self.assertAlmostEqual(sum(r["share"] for r in result["roots"]), 1)
        self.assertEqual(query.domains(owner="user:mine", store=self.store)["rows"], before)

    def test_tied_roots_are_not_reported_as_a_wrong_root(self):
        self.add("user:mine", ["one/core", "two/core"])
        result = query.domain_root_diagnosis(owner="user:mine", store=self.store)
        self.assertIsNone(result["largest_root"])
        self.assertEqual(result["outside_largest_root"], [])
        self.assertEqual(len(result["roots"]), 2)

    def test_empty_space_and_missing_owner_are_explicit(self):
        result = query.domain_root_diagnosis(owner="user:empty", store=self.store)
        self.assertTrue(result["executed"])
        self.assertEqual(result["roots"], [])
        self.assertEqual(result["assigned_nodes"], 0)
        self.assertFalse(query.domain_root_diagnosis(owner="", store=self.store)["executed"])

    def test_request_routes_to_the_census_without_algo(self):
        self.add("user:mine", ["vinc/core"])
        app = VincApp(store=self.store)
        self.addCleanup(app.jobs.shutdown, drain=False)
        status, result = app.read_diagnose({"owner": "user:mine", "kind": "domain_roots"})
        self.assertEqual(status, 200)
        self.assertEqual(result["kind"], "domain_root_diagnosis")
        self.assertEqual(result["largest_root"], "vinc")
