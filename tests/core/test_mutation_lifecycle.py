"""조건 쓰기·확인 미리보기·영수증은 실제 임시 저장소에서 검증한다."""
import unittest
from uuid import uuid4
from unittest.mock import patch

from api import fragment, query, docstore, mutation_preview, operation_receipts
from test_decision_type import _fresh_store, _restore

OWNER = "user:mutation-synthetic"


class MutationLifecycleTests(unittest.TestCase):
    pk_mode = "on"
    def setUp(self):
        self.namespace = patch.dict("os.environ", {"VINC_PK_NAMESPACED": self.pk_mode,
            "VINC_DURABLE_ACK": "off", "VINC_TOPIC_CONVERSION": "0", "VINC_STORE_SNAPSHOT": ""})
        self.namespace.start()
        self.store, self.keep = _fresh_store()

    def tearDown(self):
        self.store.close()
        _restore(self.keep)
        self.namespace.stop()

    def ingest(self, nodes, **kw):
        return fragment.merge_fragment({"owner": OWNER, "nodes": nodes, **kw}, store=self.store)

    def seed(self, ident="concept:a", **kw):
        result = self.ingest([{"id": ident, "type": "concept", "title": "Original", **kw}])
        self.assertTrue(result["executed"], result)
        return query.node(ident, owner=OWNER, store=self.store)["node"]

    def test_patch_conflict_checks_entire_fragment_before_mutation(self):
        a = self.seed(props={"old": 1, "nested": {"keep": 2, "remove": 3}})
        won = self.ingest([{"id": "concept:a", "type": "concept", "expected_rev": a["rev"],
                            "props_patch": {"nested": {"remove": None, "add": [1, 2]}}}])
        self.assertTrue(won["executed"], won)
        loser = self.ingest([{"id": "concept:new", "type": "concept", "title": "Must not exist"},
                            {"id": "concept:a", "type": "concept", "expected_rev": a["rev"], "title": "Stale"}])
        self.assertFalse(loser["executed"], loser)
        self.assertEqual("write_conflict", loser["error"])
        self.assertFalse(query.node("concept:new", owner=OWNER, store=self.store)["found"])
        self.assertEqual({"old": 1, "nested": {"keep": 2, "add": [1, 2]}},
                         query.node("concept:a", owner=OWNER, store=self.store)["node"]["props"])

    def test_document_guard(self):
        first = docstore.put_doc(self.store, owner=OWNER, doc_id="synthetic-doc", text="First", title="Doc")
        stale = docstore.put_doc(self.store, owner=OWNER, doc_id="synthetic-doc", text="Stale",
                                expected_content_hash="wrong")
        self.assertFalse(stale["executed"], stale)
        self.assertEqual("First", docstore.get_doc(self.store, "synthetic-doc", owner=OWNER)["text"])
        ok = docstore.put_doc(self.store, owner=OWNER, doc_id="synthetic-doc", text="Second",
                             expected_content_hash=first["content_hash"])
        self.assertTrue(ok["executed"], ok)

    def test_receipt_replay_and_body_conflict(self):
        op = str(uuid4())
        body = {"nodes": [{"id": "concept:a", "type": "concept", "title": "Receipt"}]}
        calls = []
        def apply():
            calls.append(1)
            return self.ingest(body["nodes"])
        first = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments=body, operation_id=op, apply=apply)
        self.assertTrue(first["executed"], first)
        again = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments=body, operation_id=op, apply=apply)
        self.assertTrue(again["replayed"], again)
        self.assertEqual(1, len(calls))
        conflict = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments={"nodes": []}, operation_id=op, apply=apply)
        self.assertEqual("operation_id_conflict", conflict["error"])
        self.assertFalse(operation_receipts.status(self.store, owner="user:other", operation_id=op)["found"])
        self.assertEqual("committed", operation_receipts.status(self.store, owner=OWNER, operation_id=op)["state"])

    def test_receipted_document_edit(self):
        docstore.put_doc(self.store, owner=OWNER, doc_id="receipt-doc", text="First", title="Doc")
        result = operation_receipts.execute(self.store, owner=OWNER, operation="doc.put", arguments={"text": "Next"},
            operation_id=str(uuid4()), apply=lambda: docstore.put_doc(self.store, owner=OWNER,
                doc_id="receipt-doc", text="Next"))
        self.assertTrue(result["executed"], result)
        self.assertEqual("Next", docstore.get_doc(self.store, "receipt-doc", owner=OWNER)["text"])

    def test_node_preview_and_stale_digest(self):
        self.seed()
        mutation_preview._incident(self.store, OWNER, ["concept:a"])
        mutation_preview._shadows(self.store, OWNER, "concept:a")
        args = {"node": "concept:a"}
        preview = mutation_preview.preview(self.store, owner=OWNER, operation="delete", arguments=args)
        self.assertTrue(preview["executed"], preview)
        self.ingest([{"id": "concept:a", "type": "concept", "title": "Changed"}])
        calls = []
        result = mutation_preview.apply_checked(self.store, owner=OWNER, operation="delete", arguments=args,
            digest=preview["preview_digest"], apply=lambda: calls.append(1))
        self.assertEqual("preview_conflict", result["error"])
        self.assertEqual([], calls)

    def test_document_preview_lists_retained_mirror(self):
        docstore.put_doc(self.store, owner=OWNER, doc_id="preview-doc", title="Doc", text="Canonical", topic="project")
        mutation_preview._rows(self.store, "Chunk", "n.doc_id=$doc", {"doc": "preview-doc"})
        result = mutation_preview.preview(self.store, owner=OWNER, operation="doc.delete", arguments={"id": "preview-doc"})
        self.assertTrue(result["executed"], result)
        self.assertEqual("concept:doc.preview-doc", result["preview"]["retained_mirror"]["id"])
        self.assertEqual(1, result["preview"]["deleted_state"]["chunks"]["count"])

    def test_create_only_and_duplicate_cannot_bypass_conditions(self):
        a = self.seed()
        for nodes, options in [
            ([{"id": "concept:a", "type": "concept", "title": "Overwrite"}], {"create_only": True}),
            ([{"id": "concept:a", "type": "concept", "expected_rev": a["rev"]},
              {"id": "concept:a", "type": "concept", "title": "Bypass"}], {}),
            ([{"id": "concept:a", "type": "record", "expected_rev": a["rev"]}], {}),
            ([{"id": "concept:a", "type": "concept", "expected_rev": None}], {}),
            ([{"id": "concept:a", "type": "concept", "props_patch": {}}], {}),
        ]:
            result = self.ingest(nodes, **options)
            self.assertFalse(result["executed"], result)
            self.assertEqual("Original", query.node("concept:a", owner=OWNER, store=self.store)["node"]["title"])

    def test_omitted_empty_patch_and_replace_are_distinct(self):
        self.seed(props={"saved": 1})
        self.ingest([{"id": "concept:a", "type": "concept", "title": "Title only"}])
        current = query.node("concept:a", owner=OWNER, store=self.store)["node"]
        self.assertEqual({"saved": 1}, current["props"])
        self.ingest([{"id": "concept:a", "type": "concept", "expected_rev": current["rev"], "props_patch": {}}])
        self.assertEqual(current["rev"], query.node("concept:a", owner=OWNER, store=self.store)["node"]["rev"])
        self.ingest([{"id": "concept:a", "type": "concept", "props": {}}])
        self.assertEqual({}, query.node("concept:a", owner=OWNER, store=self.store)["node"]["props"])

    def test_serial_writer_two_editors_only_one_wins(self):
        from concurrent.futures import ThreadPoolExecutor
        from api.server import VincApp
        a = self.seed()
        app = VincApp(store=self.store)
        try:
            def save(title):
                return app.write_ingest({"owner": OWNER, "nodes": [{"id": "concept:a", "type": "concept",
                    "expected_rev": a["rev"], "title": title}]})[1]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(save, ("Editor one", "Editor two")))
            self.assertEqual(1, sum(r["executed"] for r in results), results)
            self.assertEqual(1, sum(r.get("error") == "write_conflict" for r in results), results)
        finally:
            app.jobs.shutdown()

    def test_actual_mcp_ingest_alias_and_direct_route_share_one_receipt(self):
        from api.server import VincApp
        from api.cli_tools import _InProcessBridge
        from vinc_mcp import tools
        app = VincApp(store=self.store)
        bridge = _InProcessBridge(app)
        bridge.owner = OWNER
        op = str(uuid4())
        nodes = [{"id":"concept:once","type":"concept","title":"Once"}]
        args = {"nodes":nodes,"edges":[],"operation_id":op,"create_only":True}
        try:
            first = tools.call("vinc_ingest", args, bridge)
            self.assertTrue(first["executed"], first)
            self.assertEqual("committed", first["receipt"]["state"])
            again = tools.call("vinc_ingest", args, bridge)
            self.assertTrue(again["replayed"], again)
            alias = app.write_ingest({**args,"owner":OWNER})[1]
            self.assertTrue(alias["replayed"], alias)
            self.assertEqual(1, query.node("concept:once", owner=OWNER, store=self.store)["node"]["rev"])
            status = tools.call("vinc_operation_status", {"operation_id":op}, bridge)
            self.assertEqual("committed", status["state"], status)
        finally:
            app.jobs.shutdown()

    def test_each_other_public_writer_replays_its_receipt_through_the_real_dispatcher(self):
        from api.server import VincApp
        from api.cli_tools import _InProcessBridge
        from vinc_mcp import tools
        self.seed("concept:keep")
        self.seed("concept:drop")
        self.seed("concept:delete")
        fragment.merge_fragment({"owner":OWNER,"edges":[{"from":"concept:keep","to":"concept:delete",
            "type":"derives_from"}]}, store=self.store)
        docstore.put_doc(self.store,owner=OWNER,doc_id="outline",title="Outline",text="## Heading\nSynthetic section",topic="project")
        cases = [
            ("vinc_doc_put", {"doc_id":"edited","text":"Exact synthetic body","title":"Doc","topic":"project"}),
            ("vinc_doc_extract", {"doc_id":"outline","apply":True,"candidates":[{"title":"Extracted","one_liner":"Synthetic."}]}),
            ("vinc_merge_reject", {"a":"concept:keep","b":"concept:drop","note":"Synthetic"}),
            ("vinc_delete_edge", {"from_id":"concept:keep","to_id":"concept:delete","type":"derives_from"}),
            ("vinc_merge_nodes", {"keep_id":"concept:keep","drop_id":"concept:drop"}),
            ("vinc_delete", {"node_id":"concept:delete"}),
            ("vinc_doc_delete", {"doc_id":"edited"}),
        ]
        app = VincApp(store=self.store)
        bridge = _InProcessBridge(app)
        bridge.owner = OWNER
        try:
            for name, arguments in cases:
                with self.subTest(name=name):
                    arguments["operation_id"] = str(uuid4())
                    first = tools.call(name, arguments, bridge)
                    self.assertTrue(first["executed"], first)
                    self.assertEqual("committed", first["receipt"]["state"])
                    again = tools.call(name, arguments, bridge)
                    self.assertTrue(again["replayed"], again)
                    self.assertEqual(first["receipt"], again["receipt"])
        finally:
            app.jobs.shutdown()

    def test_lost_commit_response_is_confirmed_on_identical_retry(self):
        op = str(uuid4())
        original = self.store.run
        def lost_commit(cy, *args, **kw):
            result = original(cy, *args, **kw)
            if cy == "COMMIT":
                raise ConnectionError("synthetic lost commit response")
            return result
        def apply():
            return self.ingest([{"id": "concept:a", "type": "concept", "title": "Once"}])
        with patch.object(self.store, "run", side_effect=lost_commit):
            lost = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
                arguments={}, operation_id=op, apply=apply)
        self.assertTrue(lost["write_may_have_applied"], lost)
        again = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments={}, operation_id=op, apply=lambda: self.fail("must not replay mutation"))
        self.assertTrue(again["replayed"], again)
        self.assertEqual(1, query.node("concept:a", owner=OWNER, store=self.store)["node"]["rev"])

    def test_accepted_interruption_is_not_automatically_applied_again(self):
        op = str(uuid4())
        def interrupted():
            self.seed()
            raise RuntimeError("synthetic interruption")
        result = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments={}, operation_id=op, apply=interrupted)
        self.assertTrue(result["write_may_have_applied"], result)
        self.assertFalse(query.node("concept:a", owner=OWNER, store=self.store)["found"])
        self.assertEqual("accepted", operation_receipts.status(self.store, owner=OWNER, operation_id=op)["state"])
        again = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments={}, operation_id=op, apply=lambda: self.fail("unknown outcome cannot replay"))
        self.assertEqual("operation_outcome_unknown", again["error"])

    def test_receipts_survive_reopen_and_errors_rollback(self):
        from store.backend import open_store
        from store.config import load_config
        op = str(uuid4())
        def failed():
            self.seed()
            return {"executed": True, "error": "synthetic_failure"}
        result = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
            arguments={}, operation_id=op, apply=failed)
        self.assertFalse(result["executed"], result)
        self.assertFalse(query.node("concept:a", owner=OWNER, store=self.store)["found"])
        self.store.close()
        self.store = open_store(load_config())
        result = operation_receipts.status(self.store, owner=OWNER, operation_id=op)
        self.assertEqual("not_applied", result["state"], result)

    def test_relation_evidence_changes_invalidate_preview_and_boundaries_refuse(self):
        from store import pkns
        self.seed("concept:a")
        self.seed("concept:b")
        fragment.merge_fragment({"owner": OWNER, "edges": [{"from": "concept:a", "to": "concept:b",
            "type": "derives_from", "evidence": "First"}]}, store=self.store)
        args = {"from": "concept:a", "to": "concept:b", "type": "derives_from"}
        before = mutation_preview.preview(self.store, owner=OWNER, operation="delete_edge", arguments=args)
        self.assertTrue(before["executed"], before)
        self.assertEqual(1, len(before["preview"]["deleted_edges"]))
        self.store.run("MATCH (a:Concept)-[r:DERIVES_FROM]->(b:Concept) WHERE a.concept_id=$a "
            "SET r.evidence='Changed'", {"a": pkns.key(OWNER, "concept:a")})
        result = mutation_preview.apply_checked(self.store, owner=OWNER, operation="delete_edge", arguments=args,
            digest=before["preview_digest"], apply=lambda: self.fail("stale preview"))
        self.assertEqual("preview_conflict", result["error"])
        self.store.run("MATCH (b:Concept) WHERE b.concept_id=$b SET b.owner_id='user:other'",
                      {"b": pkns.key(OWNER, "concept:b")})
        boundary = mutation_preview.preview(self.store, owner=OWNER, operation="delete", arguments={"node": "concept:a"})
        self.assertEqual("preview_boundary_unavailable", boundary["error"])
        self.assertNotIn("Changed", str(boundary))

    def test_merge_exposes_property_loss_and_refuses_unsupported_edges(self):
        self.seed("concept:a", domain="project")
        self.seed("concept:b", domain="project")
        fragment.merge_fragment({"owner": OWNER, "nodes": [{"id": "episode:e", "type": "episode", "title": "Event"}],
            "edges": [{"from": "episode:e", "to": "concept:b", "type": "about", "evidence": "Must retain"}]}, store=self.store)
        result = mutation_preview.preview(self.store, owner=OWNER, operation="merge_nodes",
                                           arguments={"keep": "concept:a", "drop": "concept:b"})
        self.assertTrue(result["executed"], result)
        self.assertFalse(result["preview"]["apply_supported"])
        self.assertEqual("ABOUT", result["preview"]["unsupported_edges"][0]["type"])

    def test_failed_and_malformed_reads_do_not_become_empty_props_or_absent_ids(self):
        from store import pkns
        a = self.seed(props={"saved": 1})
        self.store.run("MATCH (n:Concept) WHERE n.concept_id=$id SET n.props='malformed'",
                      {"id": pkns.key(OWNER, "concept:a")})
        malformed = self.ingest([{"id":"concept:a","type":"concept","expected_rev":a["rev"],"props_patch":{"new":2}}])
        self.assertEqual("stored_props_unavailable", malformed["error"])
        original = self.store.run
        def unavailable(cy, *args, **kw):
            if "AS id" in cy and "Concept" in cy:
                return None
            return original(cy, *args, **kw)
        with patch.object(self.store, "run", side_effect=unavailable):
            failed = self.ingest([{"id":"concept:a","type":"concept","create_only":True}])
        self.assertEqual("write_precondition_unavailable", failed["error"])

    def test_receipt_capacity_refuses_before_apply(self):
        def apply():
            return {"executed":True}
        with patch.object(operation_receipts, "MAX_OWNER_RECEIPTS", 1):
            first = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
                arguments={}, operation_id=str(uuid4()), apply=apply)
            self.assertTrue(first["executed"], first)
            second = operation_receipts.execute(self.store, owner=OWNER, operation="ingest:fragment",
                arguments={}, operation_id=str(uuid4()), apply=lambda:self.fail("capacity refusal must not write"))
            self.assertEqual("receipt_capacity_unavailable", second["error"])

    def test_over_budget_preview_never_claims_completeness(self):
        self.seed()
        with patch.object(mutation_preview, "MAX_BYTES", 1):
            result = mutation_preview.preview(self.store, owner=OWNER, operation="delete", arguments={"node":"concept:a"})
        self.assertFalse(result["executed"], result)
        self.assertFalse(result["preview_complete"])
        self.assertEqual("preview_incomplete", result["error"])

    def test_create_only_foreign_pk_collision_refuses_the_whole_legacy_fragment(self):
        fragment.merge_fragment({"owner":"user:foreign","nodes":[{"id":"concept:taken","type":"concept",
            "title":"Private title","props":{"secret":"private"}}]}, store=self.store)
        result = self.ingest([{"id":"concept:free","type":"concept","title":"Free"},
                              {"id":"concept:taken","type":"concept","title":"Mine"}], create_only=True)
        if self.pk_mode == "off":
            self.assertFalse(result["executed"], result)
            self.assertEqual("write_target_unavailable", result["error"])
            self.assertFalse(query.node("concept:free", owner=OWNER, store=self.store)["found"])
            self.assertNotIn("Private title", str(result))
            self.assertNotIn("current_rev", result)
        else:
            self.assertTrue(result["executed"], result)
            self.assertEqual("Mine", query.node("concept:taken", owner=OWNER, store=self.store)["node"]["title"])

    def test_receipts_refuse_a_backend_without_the_native_atomicity_contract(self):
        class OtherStore:
            def run(self, *args):
                raise AssertionError("unsupported backend must not receive a write")
        result = operation_receipts.execute(OtherStore(), owner=OWNER, operation="doc.put", arguments={},
            operation_id=str(uuid4()), apply=lambda:self.fail("unsupported backend cannot apply"))
        self.assertEqual("receipt_atomicity_not_supported", result["error"])


class MutationLifecycleLegacyTests(MutationLifecycleTests):
    pk_mode = "off"
