"""학습 내보내기의 누출·권한·검토 경계. 테스트 인물과 문장은 모두 가상이다."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rag_eval.laya import abstention as A, data_contracts as D, exposure_registry as E, pilot_review as P
from rag_eval.laya.pins import PREDICATES, questions_hash, render_state


def fixture(root):
    text = "Fixture observation: run R passed all ten checks."
    (root / "snapshot.txt").write_bytes(text.encode())
    registry = {"schema": "vinc.laya.exposures.v1", "events": [{"event_id": "exposure:old", "kind": "measurement_opened", "at": "2026-10-10T00:00:00Z", "reference": "fixture:old", "family_ids": ["other:family"]}]}
    (root / "history.json").write_bytes(D.encoded(registry))
    grant = {"status": "approved", "decision_ref": "fixture:permission", "approved_by": "fixture:owner"}
    source = {"source_id": "fixture:source", "owner_scope": "fixture", "origin_kind": "synthetic", "origin_uri": "fixture:source", "revision": "1", "license_id": "fixture-only", "redaction_version": "none", "document_id": "fixture:doc", "captured_at": "2026-10-11T00:00:00Z", "snapshot_path": "snapshot.txt", "sha256": D.digest(text.encode()), "category": "technical_specification", "eligibility": {"training": copy.deepcopy(grant), "export": copy.deepcopy(grant)}}
    span = {"span_id": "fixture:span", "source_id": source["source_id"], "source_sha256": source["sha256"], "start": 0, "end": len(text), "excerpt_sha256": source["sha256"]}
    a = {"text": text, "language": "en", "endpoint_id": "fixture:a", "revision": "1", "mode": "quotation", "evidence": [span["span_id"]]}
    b = {"text": "Run R satisfies those ten checks.", "language": "en", "endpoint_id": "fixture:b", "revision": "1", "mode": "authored", "evidence": [span["span_id"]]}
    pair = {"pair_id": "fixture:data:p1", "family_id": "fixture:family", "a": a, "b": b, "context": None, "parents": [], "domain": "engineering", "mechanism": "measurement", "claim_time": "2026-10-11", "authored_at": "2026-10-11T01:00:00Z", "render_sha256": D.digest(render_state(a["text"], b["text"]).encode()), "contrast": "support"}
    author = {"event_id": "fixture:author", "pair_id": pair["pair_id"], "pair_sha256": D.fingerprint(pair), "labels": dict(zip(PREDICATES, [1, 0, 0])), "actor": "fixture:generator", "actor_kind": "ai", "stage": "author", "rationale": dict.fromkeys(PREDICATES, "Fixture rationale for validator tests."), "evidence_strength": "direct", "at": "2026-10-11T02:00:00Z", "rubric_id": "fixture:rubric"}
    review = {**copy.deepcopy(author), "event_id": "fixture:review", "actor": "fixture:human", "actor_kind": "human", "stage": "review", "at": "2026-10-11T03:00:00Z", "reviews": [author["event_id"]]}
    return {"schema": D.SCHEMA, "dataset_id": "fixture:data", "questions_hash": questions_hash(), "render_version": "laya-state-v1", "sources": [source], "spans": [span], "pairs": [pair], "annotations": [author, review], "assignments": [{"source_id": source["source_id"], "role": "train", "assigned_at": "2026-10-11T00:30:00Z", "groups": ["fixture:topic"]}], "exposures": [], "exposure_registry": {"path": "history.json", "sha256": D.digest(D.encoded(registry))}, "rubric": {"id": "fixture:rubric", "status": "approved", "decision_ref": "fixture:rubric-decision", "approved_by": "fixture:owner"}}


class Contracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.b = fixture(self.root)

    def check(self):
        return D.validate(self.b, self.root)[0]

    def rehash_pair(self):
        pair = self.b["pairs"][0]
        pair["render_sha256"] = D.digest(render_state(pair["a"]["text"], pair["b"]["text"], pair.get("context")).encode())
        for e in self.b["annotations"]:
            e["pair_sha256"] = D.fingerprint(pair)

    def test_export_reuses_pinned_encoding_and_refuses_overwrite(self):
        out = self.root / "out"
        receipt = D.export_training(self.b, self.root, out)
        rows = [json.loads(x) for x in (out / "training.jsonl").read_text().splitlines()]
        self.assertEqual((receipt["training_rows"], receipt["training_items"]), (2, 5))
        self.assertEqual(rows[0]["state"], render_state(self.b["pairs"][0]["a"]["text"], self.b["pairs"][0]["b"]["text"]))
        self.assertEqual(rows[0]["gold"]["supports"]["probabilities"], {"false": 0.0, "true": 1.0})
        self.assertEqual(rows[1]["meta"]["direction"], "ba")
        with self.assertRaises(FileExistsError):
            D.export_training(self.b, self.root, out)

    def test_pending_source_and_rubric_are_inspectable_but_not_exportable(self):
        for where in ("source", "rubric"):
            with self.subTest(where=where):
                self.b = fixture(self.root)
                if where == "source":
                    self.b["sources"][0]["eligibility"]["training"]["status"] = "pending"
                else:
                    self.b["rubric"]["status"] = "proposed"
                self.assertEqual(self.check()["exportable_pairs"], 0)
                with self.assertRaisesRegex(ValueError, "no reviewed"):
                    D.export_training(self.b, self.root, self.root / "out")
                self.assertFalse((self.root / "out").exists())

    def test_ai_agreement_and_self_review_cannot_claim_human_gold(self):
        self.b["annotations"][1]["actor_kind"] = "ai"
        self.assertEqual(self.check()["exportable_pairs"], 0)
        self.b["annotations"][1]["actor"] = "fixture:generator"
        with self.assertRaisesRegex(ValueError, "self review"):
            self.check()

    def test_null_labels_stay_unknown_not_negative(self):
        for e in self.b["annotations"]:
            e["labels"]["supports"] = None
        report = self.check()
        self.assertEqual(report["excluded"], {"fixture:data:p1": "unknown_labels"})

    def test_disagreement_requires_explicit_adjudication(self):
        self.b["annotations"][1]["labels"]["supports"] = 0
        self.assertEqual(self.check()["exportable_pairs"], 0)
        final = copy.deepcopy(self.b["annotations"][1])
        final.update(event_id="fixture:final", stage="adjudication", at="2026-10-11T04:00:00Z", reviews=["fixture:author", "fixture:review"])
        self.b["annotations"].append(final)
        self.assertEqual(self.check()["exportable_pairs"], 1)
        final["reviews"].pop()
        with self.assertRaisesRegex(ValueError, "all prior"):
            self.check()

    def test_mutation_refusals(self):
        mutations = [
            (lambda b: b.update(questions_hash="wrong"), "question"),
            (lambda b: b["sources"][0].update(sha256="wrong"), "snapshot hash"),
            (lambda b: b["spans"][0].update(end=10000), "span"),
            (lambda b: b["spans"][0].update(excerpt_sha256="wrong"), "excerpt"),
            (lambda b: b["sources"][0].update(snapshot_path="../outside.txt"), "escapes"),
            (lambda b: b["assignments"][0].update(assigned_at="2026-10-12T00:00:00Z"), "after authoring"),
            (lambda b: b["pairs"][0].update(context="x" * 401), "truncated"),
            (lambda b: b["pairs"][0]["a"].update(text="wrong quotation"), "quotation differs"),
            (lambda b: b["pairs"][0].update(family_id="unqualified"), "qualified family"),
            (lambda b: b["pairs"][0].update(render_sha256="wrong"), "render fingerprint"),
            (lambda b: b["annotations"][1].update(pair_sha256="stale"), "stale"),
            (lambda b: b["annotations"][1]["labels"].update(supports=True), "integer"),
            (lambda b: b["sources"].append(copy.deepcopy(b["sources"][0])), "duplicate"),
            (lambda b: b["sources"][0]["eligibility"]["training"].pop("decision_ref"), "approval"),
            (lambda b: b["exposure_registry"].update(sha256="wrong"), "registry hash"),
        ]
        for mutation, message in mutations:
            with self.subTest(message=message):
                self.b = fixture(self.root)
                mutation(self.b)
                with self.assertRaisesRegex(ValueError, message):
                    self.check()

    def test_exposed_measurement_can_only_be_diagnostic(self):
        e = {"event_id": "exposure:new", "kind": "measurement_opened", "at": "2026-10-11T04:00:00Z", "reference": "fixture:measured", "text_hashes": [D.digest(D.normalized(self.b["pairs"][0]["a"]["text"]).encode())]}
        self.b["exposures"] = [e]
        with self.assertRaisesRegex(ValueError, "diagnostic only"):
            self.check()
        self.b["assignments"][0]["role"] = "sealed_test"
        with self.assertRaisesRegex(ValueError, "sealed test"):
            self.check()
        self.b["assignments"][0]["role"] = "diagnostic"
        self.assertEqual(self.check()["exportable_pairs"], 0)

    def test_shared_document_and_translation_group_cannot_cross_splits(self):
        for field in ("document", "translation"):
            with self.subTest(field=field):
                self.b = fixture(self.root)
                source = copy.deepcopy(self.b["sources"][0])
                source["source_id"] = "fixture:second"
                assignment = {"source_id": source["source_id"], "role": "dev", "assigned_at": "2026-10-11T00:30:00Z", "groups": ["fixture:other"]}
                if field == "translation":
                    source["document_id"] = "fixture:second-doc"
                    assignment["groups"] = ["fixture:topic"]
                self.b["sources"].append(source)
                self.b["assignments"].append(assignment)
                with self.assertRaisesRegex(ValueError, "crosses split"):
                    self.check()

    def test_normalization_and_registry_contains_no_raw_text(self):
        pairs = [{"pair_id": "old", "family_id": "f1", "a": {"id": "x:a", "text": "Ｆoo  BAR"}, "b": {"id": "x:b", "text": "한글 문장"}}]
        path = self.root / "old.jsonl"
        path.write_text(json.dumps(pairs[0]), encoding="utf-8")
        registry = E.build([("public-v1", path)], "2026-10-11T00:00:00Z", "fixture:record")
        self.assertIn(D.digest(b"foo bar"), registry["events"][0]["text_hashes"])
        self.assertNotIn("한글", json.dumps(registry, ensure_ascii=False))

    def test_history_rejects_deleted_disputes_and_modified_pairs(self):
        current = copy.deepcopy(self.b)
        current["previous_bundle_sha256"] = D.fingerprint(self.b)
        D.validate_append_only(self.b, current)
        current["annotations"].pop()
        with self.assertRaisesRegex(ValueError, "append, not replace"):
            D.validate_append_only(self.b, current)

    def test_history_allows_cited_permission_update_but_not_source_replacement(self):
        current = copy.deepcopy(self.b)
        current["previous_bundle_sha256"] = D.fingerprint(self.b)
        current["sources"][0]["eligibility"]["training"]["decision_ref"] = "fixture:new-decision"
        D.validate_append_only(self.b, current)
        current["sources"][0]["sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "new identity"):
            D.validate_append_only(self.b, current)

    def add_dev_pair(self, b_text):
        source = copy.deepcopy(self.b["sources"][0])
        raw = "A separate fictional source in a different document."
        (self.root / "dev.txt").write_bytes(raw.encode())
        source.update(source_id="fixture:dev-source", document_id="fixture:dev-doc", snapshot_path="dev.txt", sha256=D.digest(raw.encode()))
        self.b["sources"].append(source)
        span = {"span_id": "fixture:dev-span", "source_id": source["source_id"], "source_sha256": source["sha256"], "start": 0, "end": len(raw), "excerpt_sha256": source["sha256"]}
        self.b["spans"].append(span)
        self.b["assignments"].append({"source_id": source["source_id"], "role": "dev", "assigned_at": "2026-10-11T00:30:00Z", "groups": ["fixture:dev-topic"]})
        pair = copy.deepcopy(self.b["pairs"][0])
        pair.update(pair_id="fixture:data:dev", family_id="fixture:dev-family")
        for side, text in (("a", raw), ("b", b_text)):
            pair[side].update(text=text, mode="authored", endpoint_id=f"fixture:dev-{side}", evidence=[span["span_id"]])
        pair["render_sha256"] = D.digest(render_state(pair["a"]["text"], pair["b"]["text"]).encode())
        self.b["pairs"].append(pair)
        return pair

    def test_normalized_text_overlap_is_blocked_even_with_new_source_ids(self):
        self.add_dev_pair(" RUN  R satisfies those ten CHECKS. ")
        with self.assertRaisesRegex(ValueError, "normalized text"):
            self.check()

    def test_near_duplicate_review_is_required_and_hash_bound(self):
        other = self.add_dev_pair("Run R satisfies those ten checks today.")
        report = self.check()
        self.assertFalse(report["ready_for_export"])
        self.assertEqual(len(report["unresolved_near_duplicates"]), 1)
        with self.assertRaisesRegex(ValueError, "near duplicates"):
            D.export_training(self.b, self.root, self.root / "out")
        ids = report["unresolved_near_duplicates"][0]
        review = {"pair_ids": ids, "pair_hashes": {p["pair_id"]: D.fingerprint(p) for p in self.b["pairs"]}, "actor_kind": "human", "actor": "fixture:reviewer", "rationale": "Fixture review only; distinct events.", "resolution": "distinct_evidence", "at": "2026-10-11T04:00:00Z"}
        self.b["near_duplicate_reviews"] = [review]
        self.assertTrue(self.check()["ready_for_export"])
        review["pair_hashes"][other["pair_id"]] = "stale"
        with self.assertRaisesRegex(ValueError, "stale near"):
            self.check()


class Abstention(unittest.TestCase):
    def test_impossible_cut_never_accepts_rounded_one(self):
        p = A.empirical_policy([0.999999] * 30, [0] * 30)
        self.assertEqual(p["mode"], "abstain_all")
        self.assertIsNone(p["min_confidence"])
        self.assertFalse(A.decide(1.0, p)["accepted"])
        self.assertFalse(A.decide(0.0, p)["accepted"])

    def test_achievable_one_is_distinct_from_no_cut(self):
        p = A.empirical_policy([0.999999] * 30, [1] * 30)
        self.assertEqual(p["min_confidence"], 1.0)
        self.assertTrue(A.decide(0.999999, p)["accepted"])
        self.assertEqual(A.decide(1.0, p, symmetric=True)["reason"], "missing_reverse")
        self.assertEqual(A.decide(1.0, p, symmetric=True, reverse_probability=0.0)["reason"], "asymmetry")

    def test_invalid_and_small_buckets_refuse(self):
        self.assertEqual(A.empirical_policy([0.9], [1])["reason"], "insufficient_bucket")
        for ps, ys in (([float("nan")], [1]), ([0.5], [True]), ([0.9], [])):
            with self.subTest(ps=ps), self.assertRaises(ValueError):
                A.empirical_policy(ps, ys)
        with self.assertRaises(ValueError):
            A.decide(1, {"schema": "vinc.laya.abstention.v1", "min_confidence": 1.0})


if __name__ == "__main__":
    unittest.main()
