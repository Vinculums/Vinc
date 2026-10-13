import copy
import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from rag_eval.laya import candidate_context as C, data_contracts as D, candidate_revision as R


class NativeContextTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs, self.audit = self.root / "inputs.jsonl", self.root / "audit.jsonl"
        rows, reviews, repairs = [], [], []
        for i in range(60):
            context = {"abstract": [f"Measured output {i}.", f"Model scope {i}."]}
            row = {"candidate_id": f"scifact:{i}", "source": "scifact", "language": "en_en",
                   "annotation_events": [], "predicate_labels": dict.fromkeys(C.PREDICATES),
                   "evidence": context["abstract"][0], "claim": f"Old claim {i}.",
                   "independence_group": f"family:{i}", "locator": {"doc_id": str(i)},
                   "source_context": context, "source_context_sha256": C.context_hash(context)}
            rows.append(row)
            reviews.append({"candidate_id": row["candidate_id"], "review_route": "context_or_scope_review" if i < 33 else "native_fact_or_restatement",
                            "source_context_sha256": row["source_context_sha256"], "predicate_labels": dict.fromkeys(C.PREDICATES),
                            "training_export_approved": False, "human_review": False})
            if i < 33:
                repairs.append({"parent_id": row["candidate_id"], "disposition": "authored_candidate" if i < 24 else "deferred",
                                "sentence_indices": [0, 1] if i < 24 else [], "claim": f"Bounded claim {i}." if i < 24 else None,
                                "context": "Recorded experiment only." if i < 24 else None,
                                "reason": "Resolve the model scope." if i < 24 else "Missing original endpoint."})
        self.inputs.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        self.audit.write_text("".join(json.dumps(r) + "\n" for r in reviews), encoding="utf-8")
        self.plan = {"schema": C.SCHEMA, "training_authorized": False, "authored_at": "2026-10-13T12:00:00Z",
                     "inputs_sha256": D.digest(self.inputs.read_bytes()), "audit_sha256": D.digest(self.audit.read_bytes()),
                     "repairs": repairs}

    def test_source_projection_offsets_and_unassigned_authority(self):
        bundle = C.native_candidates(self.inputs, self.audit, self.plan)
        self.assertEqual((len(bundle["pairs"]), len(bundle["dispositions"])), (24, 33))
        full = "Measured output 0.\nModel scope 0."
        for span in bundle["pairs"][0]["evidence"]:
            self.assertEqual(full[span["start"]:span["end"]], span["text"])
            self.assertEqual(C.text_hash(span["text"]), span["excerpt_sha256"])
        self.assertTrue(all(all(v is None for v in p["predicate_labels"].values()) for p in bundle["pairs"]))
        self.assertFalse(bundle["training_authorized"])
        self.assertEqual(bundle["pairs"][0]["locator"]["sentence_indices"], [0, 1])
        self.assertEqual(bundle["pairs"][0]["locator"]["member"], "data/corpus.jsonl")
        self.assertEqual(bundle["pairs"][0]["parent_locator"], {"doc_id": "0"})
        evidence = C.native_queue(bundle, "rubric:test")[0]["evidence"]
        self.assertTrue(all(r["source_sha256"] == r["projection_sha256"] for r in evidence))

    def test_changed_source_or_review_bytes_refused(self):
        for path in (self.inputs, self.audit):
            old = path.read_bytes()
            path.write_bytes(old + b" ")
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "hash mismatch"):
                C.native_candidates(self.inputs, self.audit, self.plan)
            path.write_bytes(old)

    def test_incomplete_or_duplicate_priority_disposition_refused(self):
        for change in (lambda p: p["repairs"].pop(), lambda p: p["repairs"].__setitem__(1, copy.deepcopy(p["repairs"][0]))):
            plan = copy.deepcopy(self.plan)
            change(plan)
            with self.assertRaisesRegex(ValueError, "thirty-three"):
                C.native_candidates(self.inputs, self.audit, plan)

    def test_unresolved_case_cannot_invent_a_repair(self):
        plan = copy.deepcopy(self.plan)
        plan["repairs"][-1]["claim"] = "Invented result."
        with self.assertRaisesRegex(ValueError, "deferred case"):
            C.native_candidates(self.inputs, self.audit, plan)

    def test_bad_indices_context_truncation_and_label_injection_refused(self):
        mutations = ({"sentence_indices": [-1]}, {"sentence_indices": [0, 0]}, {"context": "x" * 401},
                     {"predicate_labels": {"supports": True}}, {"claim": ""})
        for mutation in mutations:
            plan = copy.deepcopy(self.plan)
            plan["repairs"][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                C.native_candidates(self.inputs, self.audit, plan)

    def test_parent_context_and_ai_audit_authority_refused_even_after_repin(self):
        for path, mutation in ((self.inputs, lambda r: r["source_context"]["abstract"].append("Unverified change")),
                               (self.inputs, lambda r: r["predicate_labels"].update(supports=False)),
                               (self.audit, lambda r: r.update(human_review=True))):
            old = path.read_bytes()
            rows = C.lines(path)
            mutation(rows[0])
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            plan = copy.deepcopy(self.plan)
            plan["inputs_sha256" if path == self.inputs else "audit_sha256"] = D.digest(path.read_bytes())
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                C.native_candidates(self.inputs, self.audit, plan)
            path.write_bytes(old)

    def test_blind_session_escapes_source_and_has_no_prior_judgments(self):
        bundle = C.native_candidates(self.inputs, self.audit, self.plan)
        bundle["rubric"] = {"id": "rubric:test"}
        queue = C.native_queue(bundle, "rubric:test")
        queue[0]["a"] = "</script><script>alert(1)</script>"
        dest = self.root / "session"
        result = C.review_session(bundle, queue, {}, dest)
        self.assertEqual(result["cases"], 24)
        session = json.loads((dest / "session.json").read_bytes())
        self.assertTrue(all(not r["reviews"] and r["author_actor"] is None for r in session["cases"]))
        self.assertFalse({"parents", "predicate_labels", "source_native_label", "contrast"} & session["cases"][0].keys())
        self.assertNotIn("</script><script>alert(1)", (dest / "review.html").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            C.review_session(bundle, queue, {}, dest)

    def test_frozen_input_and_repository_outputs_refused_before_reuse(self):
        with mock.patch.object(R, "verify") as reused:
            for out in (self.root / "inside", Path(C.__file__).resolve().parent / "bad-output"):
                with self.subTest(out=str(out)), self.assertRaisesRegex(ValueError, "outside"):
                    C.build(self.root, self.root, self.root, self.inputs, self.audit, self.root / "plan.json", out)
            reused.assert_not_called()

    def test_output_replay_and_authority_or_blindness_tampering_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        original, revision, development = (self.root / n for n in ("old", "revision", "dev"))
        for directory in (original, revision, development):
            directory.mkdir()
        pilot_pair = {"pair_id": "pilot:1", "a": {"text": "A", "language": "ko"},
                      "b": {"text": "B", "language": "en"}, "context": "Scope"}
        dev_pair = {"pair_id": "dev:1", "a": {"text": "different", "language": "en"},
                    "b": {"text": "other", "language": "en"}, "context": "Scope"}
        for directory, pair in ((original, pilot_pair), (revision, pilot_pair), (development, dev_pair)):
            R.write(directory, "bundle.json", {"dataset_id": directory.name, "annotations": [],
                    "pairs": [pair], "rubric": {"id": "rubric:test"}})
        R.write(original, "review/instructions.json", {})
        R.write(revision, "selection.json", {})
        plan = self.root / "plan.json"
        plan.write_bytes(D.encoded(self.plan))

        def queue(bundle, pairs, root):
            return [{"pair_id": p["pair_id"], "pair_sha256": D.fingerprint(p), "a": p["a"]["text"],
                     "b": p["b"]["text"], "context": p["context"], "evidence": []} for p in pairs]

        with mock.patch.object(R, "verify", return_value={"verified": True}), \
             mock.patch.object(R, "validate_selection", return_value=[pilot_pair]), \
             mock.patch.object(R, "hydrated_queue", side_effect=queue):
            first, replay = (Path(outside.name) / n for n in ("first", "replay"))
            C.build(original, revision, development, self.inputs, self.audit, plan, first)
            C.build(original, revision, development, self.inputs, self.audit, plan, replay)
        self.assertEqual(R.inventory(first), R.inventory(replay))
        self.assertEqual((first / "manifest.json").read_bytes(), (replay / "manifest.json").read_bytes())
        self.assertTrue(C.verify(first)["verified"])
        manifest = json.loads((first / "manifest.json").read_bytes())
        changed = copy.deepcopy(manifest)
        changed["training_exportable_pairs"] = 1
        (first / "manifest.json").write_bytes(D.encoded(changed))
        with self.assertRaisesRegex(ValueError, "authority changed"):
            C.verify(first)
        (first / "manifest.json").write_bytes(D.encoded(manifest))
        session = json.loads((first / "native/session.json").read_bytes())
        session["cases"][0]["source_native_label"] = "SUPPORT"
        (first / "native/session.json").write_bytes(D.encoded(session))
        group = json.loads((first / "native/manifest.json").read_bytes())
        group.update(files=R.inventory(first / "native"), session_sha256=D.fingerprint(session))
        (first / "native/manifest.json").write_bytes(D.encoded(group))
        manifest["review_groups"]["native"]["session_sha256"] = D.fingerprint(session)
        manifest["files"] = R.inventory(first)
        (first / "manifest.json").write_bytes(D.encoded(manifest))
        with self.assertRaisesRegex(ValueError, "author intent"):
            C.verify(first)
