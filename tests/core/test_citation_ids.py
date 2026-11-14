"""인용 id는 문서 키가 아닌, 같은 소유자가 읽을 수 있는 노드다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import docstore, fragment, node_read, query as q
from store.backend import open_store
from store.config import StoreConfig


class CitationIdsTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store(StoreConfig(data_dir=str(Path(tempfile.mkdtemp()) / "store"),
                                           buffer_pool_mb=128, max_db_size=1 << 30))
        self.assertIsNotNone(self.store, "requires the prescribed project interpreter")
        self.store.provision(dim=4)
        fragment.merge_fragment({"owner": "user:cites", "nodes": [
            {"id": "concept:citation", "type": "concept", "title": "Canonical citation",
             "domain": "vinc/engineering/search", "one_liner": "Cite the exact node."},
            {"id": "record:citation", "type": "record", "title": "Citation evidence",
             "props": {"note": "The node exists."}}], "edges": [
            {"from": "record:citation", "to": "concept:citation", "type": "relates_to",
             "evidence": "Fixture evidence"}]}, store=self.store)
        for doc_id, topic in (("cites.present", "vinc/engineering/search"),
                              ("cites.missing", None), ("cites.foreign", None)):
            result = docstore.put_doc(self.store, owner="user:cites", doc_id=doc_id,
                                     title=doc_id, topic=topic,
                                     text="Canonical citation uses existing node identifiers.")
            self.assertTrue(result["executed"])
        fragment.merge_fragment({"owner": "user:other", "nodes": [
            {"id": "concept:doc.cites.foreign", "type": "concept", "title": "Private mirror"}
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()

    def test_search_cites_readable_mirrors_without_changing_chunk_ids(self):
        result = q.search("Canonical citation", owner="user:cites", store=self.store,
                          top_k=20)
        rows = {row["doc_id"]: row for row in result["rows"]}
        self.assertEqual(set(rows), {"cites.present", "cites.missing", "cites.foreign"})
        self.assertEqual(rows["cites.present"]["cite_id"], "concept:doc.cites.present")
        for doc in ("cites.missing", "cites.foreign"):
            self.assertIsNone(rows[doc]["cite_id"])
            self.assertIn("missing or unreadable", rows[doc]["cite_unavailable"])
        self.assertTrue(all(row["id"].startswith(row["doc_id"] + "#") for row in rows.values()))
        self.assertTrue(result["node_hits"])
        self.assertTrue(all(hit["cite_id"] == hit["id"] for hit in result["node_hits"]))
        self.assertNotIn("concept:doc.cites.foreign", [h["cite_id"] for h in result["node_hits"]])

    def test_brief_excerpts_links_and_sources_share_canonical_ids(self):
        result = q.brief("concept:citation", owner="user:cites", store=self.store)
        self.assertEqual(result["node"]["cite_id"], "concept:citation")
        ex = {row["doc_id"]: row for row in result["excerpts"]}
        self.assertEqual(ex["cites.present"]["cite_id"], "concept:doc.cites.present")
        self.assertIsNone(ex["cites.foreign"]["cite_id"])
        self.assertEqual(result["links"]["items"][0]["cite_id"], "record:citation")
        self.assertIn("cites.present", result["grounds"])
        self.assertIn("concept:doc.cites.present", result["cite_grounds"])
        self.assertNotIn("cites.present", result["cite_grounds"])
        ctx = q.node_context(owner="user:cites", node="concept:citation", store=self.store)
        sources = {row["doc_id"]: row for row in ctx["sources"]["items"]}
        self.assertEqual(sources["cites.present"]["cite_id"], "concept:doc.cites.present")
        self.assertIsNone(sources["cites.foreign"]["cite_id"])

    def test_choose_candidates_are_node_ids(self):
        out = q.brief("citation", owner="user:cites", store=self.store)
        self.assertTrue(out["candidates"])
        self.assertTrue(all(c["cite_id"] == c["id"] for c in out["candidates"]))

    def test_citation_lookup_failure_keeps_rows_and_exposes_failure(self):
        with patch.object(node_read, "_doc_cite_ids", side_effect=RuntimeError("lookup failed")):
            out = q.search("Canonical citation", owner="user:cites", store=self.store)
            brief = q.brief("concept:citation", owner="user:cites", store=self.store)
        self.assertTrue(out["rows"])
        self.assertTrue(all(row["cite_id"] is None for row in out["rows"]))
        self.assertIn("lookup failed", out["citation_error"])
        self.assertEqual(brief["status"], "resolved")
        self.assertTrue(brief["excerpts"])
        self.assertTrue(brief["links"]["items"])
        self.assertIn("lookup failed", brief["citation_error"])


class SearchCitationStoreFailureTests(unittest.TestCase):
    def _result(self):
        return {"rows": [{"id": "cites.present#00000", "doc_id": "cites.present",
                          "score": 7.5, "snippet": "Useful material"}],
                "node_hits": [{"id": "concept:citation", "title": "Citation"}],
                "next_cursor": "original-cursor", "arms": "fts"}

    def test_citation_only_store_open_failure_preserves_search_results(self):
        with patch.object(q._retrieval, "search", return_value=self._result()), \
                patch.object(node_read, "load_config", return_value=object()), \
                patch.object(node_read, "open_store", side_effect=RuntimeError("open failed")):
            out = q.search("citation", owner="user:cites")
        self.assertEqual(out["rows"][0]["id"], "cites.present#00000")
        self.assertEqual(out["rows"][0]["score"], 7.5)
        self.assertEqual(out["next_cursor"], "original-cursor")
        self.assertEqual(out["node_hits"][0]["cite_id"], "concept:citation")
        self.assertIsNone(out["rows"][0]["cite_id"])
        self.assertIn("open failed", out["citation_error"])

    def test_citation_store_close_failure_keeps_verified_citations(self):
        from unittest.mock import Mock
        store = Mock()
        store.run.return_value = [{"id": "concept:doc.cites.present"}]
        store.close.side_effect = RuntimeError("close failed")
        with patch.object(q._retrieval, "search", return_value=self._result()), \
                patch.object(node_read, "load_config", return_value=object()), \
                patch.object(node_read, "open_store", return_value=store):
            out = q.search("citation", owner="user:cites")
        self.assertEqual(out["rows"][0]["cite_id"], "concept:doc.cites.present")
        self.assertEqual(out["next_cursor"], "original-cursor")
        self.assertIn("close failed", out["citation_error"])
