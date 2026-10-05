"""Disposable actual-schema fixtures; review must issue only MATCH queries."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api import docstore
from api.mirror_provenance import review_mirrors
from api.mirror_provenance import graph_with_mirror_review
from store import pkns
from store.backend import open_store
from store.config import load_config

try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    _DRIVER = None


class ReadOnly:
    def __init__(self, store):
        self.store = store
        self.queries = []

    def run(self, query, params=None):
        if not query.startswith("MATCH ") or any(
                word in query.split() for word in ("SET", "CREATE", "MERGE", "DELETE", "CALL")):
            raise AssertionError("review attempted a write or procedure")
        self.queries.append(query)
        return self.store.run(query, params)


@unittest.skipIf(_DRIVER is None, "embedded driver unavailable")
class MirrorProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            "MEMORY_STORE_BACKEND": "embedded", "MEMORY_DATA_DIR": self.tmp.name + "/store",
            "VINC_LBUG_EXT_DIR": str(Path(__file__).resolve().parents[2] / "packaging/lbug_extensions"),
            "VINC_PK_NAMESPACED": os.environ.get("VINC_PK_NAMESPACED", "0")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = open_store(load_config())
        self.assertIsNotNone(self.store, "embedded engine unavailable")
        self.store.provision(dim=4)
        self.ro = ReadOnly(self.store)
        self.owner = "user:a"

    def tearDown(self):
        self.store.close()
        self.env.stop()
        self.tmp.cleanup()

    def put(self, title="Curated unrelated title", text="ordinary source body", doc="sample"):
        result = docstore.put_doc(self.store, owner=self.owner, doc_id=doc, title=title,
                                  text=text, topic="vinc/product")
        self.assertTrue(result["executed"], result)
        return "concept:doc." + doc

    def review(self, mid, **kwargs):
        return review_mirrors(self.ro, owner=self.owner,
                              isolated_mirror_ids=[mid], **kwargs)["findings"][0]

    def test_normal_isolation_does_not_require_semantic_link(self):
        mid = self.put()
        result = self.review(mid)
        self.assertEqual(result["classification"], "normal_isolation")
        self.assertEqual(result["expected_mentions"], 0)
        self.assertTrue(result["body_verified"])

    def test_present_expected_mentions_and_missing_pair(self):
        mid = self.put(title="source body")
        self.assertEqual(self.review(mid)["classification"], "normal_isolation")
        self.store.run("MATCH (c:Chunk)-[r:MENTIONS]->(n:Concept) "
                       "WHERE n.concept_id = $id DELETE r", {"id": pkns.key(self.owner, mid)})
        result = self.review(mid)
        self.assertEqual(result["classification"], "missing_expected_mentions")
        self.assertEqual(result["missing_expected_mentions"],
                         [{"chunk_id": "sample#00000", "expected_method": "title"}])
        self.assertNotIn("text", str(result.keys()))

    def test_existing_pair_with_wrong_method_requires_review(self):
        mid = self.put(title="source body")
        self.store.run("MATCH (c:Chunk)-[r:MENTIONS]->(n:Concept) "
                       "WHERE n.concept_id = $id SET r.method = 'source'",
                       {"id": pkns.key(self.owner, mid)})
        result = self.review(mid)
        self.assertEqual(result["classification"], "missing_expected_mentions")
        self.assertEqual(result["observed_mentions"], 1)
        self.assertEqual(result["missing_expected_mentions"][0]["expected_method"], "title")

    def test_source_basename_rule_is_evidence_not_semantic_edge(self):
        mid = self.put()
        self.store.run("MATCH (c:Chunk) WHERE c.doc_id = 'sample' "
                       "SET c.source_file = '/original/source.md'")
        self.store.run("MATCH (n:Concept) WHERE n.concept_id = $id "
                       "SET n.source_file = 'relative/source.md'", {"id": pkns.key(self.owner, mid)})
        result = self.review(mid)
        self.assertEqual(result["classification"], "missing_expected_mentions")
        self.assertEqual(result["missing_expected_mentions"][0]["expected_method"], "source")

    def test_missing_source_metadata(self):
        mid = self.put()
        self.store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id DELETE d", {"id": "sample"})
        self.assertEqual(self.review(mid)["classification"], "missing_source_document")

    def test_foreign_owner_source_is_not_read_or_disclosed(self):
        mid = self.put()
        self.store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.owner_id = $o",
                       {"id": "sample", "o": "user:b"})
        result = self.review(mid)
        self.assertEqual(result["classification"], "missing_source_document")
        self.assertNotIn("source", result)

    def test_foreign_mirror_unavailable(self):
        mid = self.put()
        result = review_mirrors(self.ro, owner="user:b", isolated_mirror_ids=[mid])["findings"][0]
        self.assertEqual(result["classification"], "incomplete_or_unverified_evidence")
        self.assertNotIn("source", result)

    def test_incomplete_hash_and_legacy_offsets(self):
        for mutation, reason in [
            ("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.chunk_count = -1", "chunk count"),
            ("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.content_hash = 'wrong'", "SHA256"),
            ("MATCH (c:Chunk) WHERE c.doc_id = $id SET c.char_start = -1", "offsets")]:
            mid = self.put()
            self.store.run(mutation, {"id": "sample"})
            result = self.review(mid)
            self.assertEqual(result["classification"], "incomplete_or_unverified_evidence")
            self.assertIn(reason, result["reason"])
            self.assertNotIn("missing_expected_mentions", result)

    def test_chunk_bound_has_no_partial_body_conclusion(self):
        mid = self.put()
        self.store.run("CREATE (c:Chunk {chunk_id: $id, doc_id: 'sample', owner_id: $owner, "
                       "chunk_index: 1, text: 'tail', char_start: 20, char_end: 24})",
                       {"id": "sample#00001", "owner": self.owner})
        result = self.review(mid, max_chunks=1)
        self.assertEqual(result["classification"], "incomplete_or_unverified_evidence")
        self.assertIn("bound exceeded", result["reason"])
        self.assertNotIn("body_verified", result)

    def test_stale_unexpected_mentions_are_not_called_normal(self):
        mid = self.put()
        self.store.run("MATCH (c:Chunk), (n:Concept) WHERE c.chunk_id = $c "
                       "AND n.concept_id = $n CREATE (c)-[:MENTIONS {method: 'source'}]->(n)",
                       {"c": "sample#00000", "n": pkns.key(self.owner, mid)})
        result = self.review(mid)
        self.assertEqual(result["classification"], "incomplete_or_unverified_evidence")
        self.assertIn("stale", result["reason"])

    def test_explicit_selection_bounds(self):
        for ids in ([], ["concept:doc.sample"] * 33, ["concept:sample"], ["concept:doc."]):
            with self.assertRaises(ValueError):
                review_mirrors(self.ro, owner=self.owner, isolated_mirror_ids=ids)
        self.assertEqual(self.ro.queries, [])

    def test_changed_metadata_rejects_conclusion(self):
        mid = self.put()
        original = self.ro.run
        calls = 0

        def changing(query, params=None):
            nonlocal calls
            rows = original(query, params)
            if "MATCH (d:_DocMeta)" in query:
                calls += 1
                if calls == 2:
                    rows = [dict(rows[0], updated_at="changed")]
            return rows

        with patch.object(self.ro, "run", side_effect=changing):
            result = self.review(mid)
        self.assertEqual(result["classification"], "incomplete_or_unverified_evidence")
        self.assertIn("changed", result["reason"])

    def test_graph_default_unchanged_and_review_nested_without_source_text(self):
        from api import query
        mid = self.put(title="source body")
        args = {"owner": self.owner, "seed": mid, "depth": 1, "store": self.ro}
        original = query.graph(**args)
        self.assertEqual(graph_with_mirror_review(**args), original)
        selected = graph_with_mirror_review(review_mirror=True, **args)
        review = selected.pop("mirror_review")
        self.assertEqual(selected, original)
        self.assertEqual(len(review["findings"]), 1)
        self.assertTrue(review["findings"][0]["body_verified"])
        self.assertNotIn("ordinary source body", str(review))
        self.assertFalse(review["repair_authorized"])

    def test_graph_foreign_and_absent_mirror_do_not_read_source(self):
        mid = self.put()
        for owner, seed in (("user:b", mid), (self.owner, "concept:doc.unknown")):
            self.ro.queries.clear()
            result = graph_with_mirror_review(owner=owner, seed=seed, depth=1,
                                               review_mirror=True, store=self.ro)
            self.assertFalse(result["found"])
            self.assertNotIn("mirror_review", result)
            self.assertFalse(any("_DocMeta" in q for q in self.ro.queries))

    def test_graph_wrong_seed_rejected_before_query(self):
        for seed in (None, "concept:ordinary", "concept:doc.", "user:a\x1fconcept:doc.sample"):
            with self.assertRaises(ValueError):
                graph_with_mirror_review(owner=self.owner, seed=seed,
                                         review_mirror=True, store=self.ro)
        self.assertEqual(self.ro.queries, [])

    def test_graph_team_owned_mirror_uses_same_exact_owner_scope(self):
        self.owner = "team:test-mirror-review"
        mid = self.put()
        result = graph_with_mirror_review(owner=self.owner, seed=mid, depth=1,
                                          review_mirror=True, store=self.ro)
        self.assertTrue(result["mirror_review"]["findings"][0]["body_verified"])
        denied = graph_with_mirror_review(owner="user:a", seed=mid, depth=1,
                                          review_mirror=True, store=self.ro)
        self.assertFalse(denied["found"])
        self.assertNotIn("mirror_review", denied)

    def test_connected_mirror_is_not_mislabeled_isolated(self):
        from api.fragment import merge_fragment
        mid = self.put()
        merge_fragment({"owner": self.owner,
                        "nodes": [{"id": mid, "type": "concept", "title": "Curated unrelated title"},
                                  {"id": "concept:neighbor", "type": "concept", "title": "Neighbor"}],
                        "edges": [{"from": mid, "to": "concept:neighbor", "type": "relates_to"}]},
                       store=self.store)
        result = graph_with_mirror_review(owner=self.owner, seed=mid, depth=1,
                                          review_mirror=True, store=self.ro)
        self.assertGreater(result["mirror_review"]["selected_semantic_degree"], 0)
        self.assertEqual(result["mirror_review"]["findings"][0]["classification"],
                         "normal_source_provenance")

    def test_sidecar_parses_boolean_over_existing_read_route(self):
        from api.server import VincApp
        from types import SimpleNamespace
        mid = self.put()
        fake = SimpleNamespace(_read=lambda fn: (200, fn(self.ro)))
        status, result = VincApp.read_graph(fake, {"owner": self.owner, "seed": mid,
                                                  "depth": "1", "review_mirror": "True"})
        self.assertEqual(status, 200)
        self.assertIn("mirror_review", result)
        _, disabled = VincApp.read_graph(fake, {"owner": self.owner, "seed": mid,
                                               "depth": "1", "review_mirror": "false"})
        self.assertNotIn("mirror_review", disabled)
        with self.assertRaises(ValueError):
            VincApp.read_graph(fake, {"owner": self.owner, "seed": mid, "review_mirror": "yes"})


if __name__ == "__main__":
    unittest.main()
