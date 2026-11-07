"""Owner-scoped canonical descriptions reuse document and conversion storage."""
import hashlib
import unittest
from unittest.mock import patch

from api import fragment, query, topic_description
from store import pkns
from store.topic_transfer import read_snapshot, plan_transfer, transformed_snapshot, _write_difference
from test_decision_type import _fresh_store, _restore

OWNER = "user:description-person"
TEAM = "team:00000000-0000-4000-8000-000000000487"


class TopicDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.namespace = patch.dict("os.environ", {"VINC_PK_NAMESPACED": "on"})
        self.namespace.start()
        self.store, self.keep = _fresh_store()
        self.seed(OWNER, "concept:mine", "project")

    def tearDown(self):
        self.store.close()
        _restore(self.keep)
        self.namespace.stop()

    def seed(self, owner, nid, domain):
        result = fragment.merge_fragment({"owner": owner, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": domain}]}, store=self.store)
        self.assertFalse(result.get("warnings"), result)

    def save(self, text, content_hash="", owner=OWNER, node="domain:project"):
        return topic_description.put(self.store, owner=owner, node=node, text=text,
                                     expected_content_hash=content_hash)

    def read(self, owner=OWNER, domain="project"):
        return topic_description.read(self.store, owner=owner, domain=domain)

    def test_create_edit_clear_and_exact_original_text(self):
        self.assertEqual({"executed": True, "status": "ok", "exists": False,
                          "text": "", "content_hash": ""}, self.read())
        first = self.save("A description.\n한국어 설명 <script>literal</script>")
        self.assertTrue(first["executed"], first)
        self.assertEqual(first["text"], self.read()["text"])
        self.assertEqual(hashlib.sha256(first["text"].encode()).hexdigest(), first["content_hash"])
        context = query.node_context(owner=OWNER, node="domain:project", store=self.store)
        self.assertEqual(first["text"], context["topic"]["description"]["text"])
        second = self.save("Edited description", first["content_hash"])
        self.assertTrue(second["executed"], second)
        self.assertEqual(first["doc_id"], second["doc_id"])
        empty = self.save("", second["content_hash"])
        self.assertTrue(empty["executed"], empty)
        self.assertEqual("", self.read()["text"])
        self.assertTrue(self.read()["exists"])

    def test_graph_marks_the_description_mirror_and_nothing_else(self):
        # 화면은 이 표시로 설명 미러를 지도·트리·수에서 뺀다. 다른 소유자의 같은 경로 설명은 표시하지 않는다
        first = self.save("Shown in the topic detail, not as a node")
        self.seed("user:other-description", "concept:mine", "project")
        other = self.save("Other owner's description", owner="user:other-description")
        for kwargs in ({}, {"seed": "concept:mine", "depth": 1}):
            got = query.graph(owner=OWNER, store=self.store, **kwargs)
            self.assertTrue(got["executed"], got)
            marked = {n["id"] for n in got["nodes"] if n.get("topic_description")}
            if kwargs:
                self.assertEqual(set(), marked - {"concept:doc." + first["doc_id"]})
                continue
            self.assertEqual({"concept:doc." + first["doc_id"]}, marked)
            self.assertNotIn("concept:doc." + other["doc_id"], {n["id"] for n in got["nodes"]})
            self.assertIn("concept:mine", {n["id"] for n in got["nodes"]})

    def test_two_editors_conflict_without_mutation(self):
        original = self.save("First")
        winner = self.save("Winner", original["content_hash"])
        before = read_snapshot(self.store)
        loser = self.save("Stale editor", original["content_hash"])
        self.assertEqual("conflict", loser["status"])
        self.assertFalse(loser["executed"])
        self.assertEqual(before, read_snapshot(self.store))
        self.assertEqual(winner["text"], self.read()["text"])

    def test_unchanged_save_does_not_stamp_a_new_revision(self):
        first = self.save("Already saved")
        before = read_snapshot(self.store)
        same = self.save(first["text"], first["content_hash"])
        self.assertTrue(same["executed"])
        self.assertEqual(before, read_snapshot(self.store))

    def test_serial_writer_accepts_only_one_concurrent_edit(self):
        from concurrent.futures import ThreadPoolExecutor
        from api.jobs import JobQueue
        from api.server import VincApp
        original = self.save("First")
        app = object.__new__(VincApp)
        app.store = self.store
        app.jobs = JobQueue()
        try:
            def submit(text):
                return app.write_topic_description({"owner": OWNER, "node": "domain:project",
                    "text": text, "expected_content_hash": original["content_hash"]})[1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, ["One editor", "Another editor"]))
            self.assertEqual([False, True], sorted(r["executed"] for r in results))
            self.assertEqual(1, sum(r["status"] == "conflict" for r in results))
            self.assertIn(self.read()["text"], ["One editor", "Another editor"])
        finally:
            app.jobs.shutdown()

    def test_same_path_isolated_across_owners_and_team(self):
        for owner, text in ((OWNER, "Personal description"), (TEAM, "Team description"),
                            ("user:other-description", "Other description")):
            if owner != OWNER:
                self.seed(owner, "concept:mine", "project")
            result = self.save(text, owner=owner)
            self.assertTrue(result["executed"], result)
        for owner, text in ((OWNER, "Personal description"), (TEAM, "Team description"),
                            ("user:other-description", "Other description")):
            self.assertEqual(text, self.read(owner)["text"])
        denied = self.save("Must not create", owner="user:inaccessible")
        missing = self.save("Must not create", node="domain:not-there")
        self.assertEqual("unavailable", denied["status"])
        self.assertEqual(denied, missing)
        self.assertFalse(self.read("user:inaccessible")["exists"])

    def test_failure_is_not_an_empty_description(self):
        first = self.save("Kept text")
        with patch.object(self.store, "run", side_effect=RuntimeError("fixture read failed")):
            failed = self.read()
            refused = self.save("New text", first["content_hash"])
        self.assertEqual("query_failed", failed["status"])
        self.assertNotIn("text", failed)
        self.assertEqual("query_failed", refused["status"])
        self.assertEqual(first["text"], self.read()["text"])

    def test_conversion_preserves_description_and_unrelated_owner(self):
        first = self.save("A topic description without external resources")
        self.seed("user:other-description", "concept:mine", "project")
        self.save("Other owner's description", owner="user:other-description")
        before = read_snapshot(self.store, vocabulary_owner=OWNER)
        plan = plan_transfer(before, source=OWNER, target=TEAM, topic="project")
        self.assertTrue(plan.ready, plan.blockers)
        after = transformed_snapshot(before, plan, stamp="2026-10-14T00:00:00.000Z")
        _write_difference(self.store, before, after)
        moved = self.read(TEAM)
        self.assertTrue(moved["executed"], moved)
        self.assertEqual(first["doc_id"], moved["doc_id"])
        self.assertEqual(first["text"], moved["text"])
        self.assertFalse(self.read(OWNER)["exists"])
        self.assertEqual("Other owner's description", self.read("user:other-description")["text"])
        edited = self.save("Edited in team", moved["content_hash"], owner=TEAM)
        self.assertTrue(edited["executed"], edited)

    def test_rename_keeps_document_identity(self):
        first = self.save("Rename preserves this description")
        self.store.run("MATCH (c:Concept) WHERE c.concept_id=$id SET c.domain=$domain",
                       {"id": pkns.key(OWNER, "concept:doc." + first["doc_id"]), "domain": "renamed"})
        self.assertEqual(first["doc_id"], self.read(domain="renamed")["doc_id"])
        self.assertFalse(self.read(domain="project")["exists"])

    def test_unconfirmed_write_is_not_reported_saved(self):
        with patch("api.docstore.put_doc", side_effect=RuntimeError("fixture response loss")):
            result = self.save("Input retained by caller")
        self.assertFalse(result["executed"])
        self.assertEqual("unconfirmed", result["status"])
        self.assertTrue(result["write_may_have_applied"])

    def test_empty_first_save_does_not_create_metadata_or_structure(self):
        before = read_snapshot(self.store)
        result = self.save("")
        self.assertTrue(result["executed"])
        self.assertFalse(result["exists"])
        self.assertEqual(before, read_snapshot(self.store))
