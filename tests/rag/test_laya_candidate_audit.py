"""출처 대조를 정답이나 승인으로 바꾸지 않고 변조된 근거를 거절하는지 확인한다."""
import contextlib
import copy
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from rag_eval.laya import candidate_audit as A, data_contracts as D, pilot_verify as V

DATA = Path(__file__).resolve().parents[2] / "rag_eval/evalsets"
PILOT = DATA / "laya-contrast-pilot-v1"
AUDIT = DATA / "laya-contrast-pilot-audit-v1"


class CandidateAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = V.read(PILOT, "bundle.json")
        cls.recipes = {r["source_id"]: r for r in V.read(PILOT, "authoring-recipes.json")}
        cls.sources = {s["source_id"]: s for s in cls.bundle["sources"]}
        cls.issues = V.read(AUDIT, "issues.json")

    def fact(self):
        source = self.sources["source:laya-p1-fact01"]
        return V.read(PILOT, source["snapshot_path"]), source, copy.deepcopy(self.recipes[source["source_id"]])

    def observation(self):
        source = self.sources["source:laya-p1-obs01"]
        return V.read(PILOT, source["snapshot_path"]), copy.deepcopy(self.recipes[source["source_id"]])

    def test_saved_audit_reproduces_without_labels_or_input_changes(self):
        before = (PILOT / "manifest.json").read_bytes()
        result = A.audit(PILOT, self.issues)
        self.assertEqual(D.encoded(result), (AUDIT / "audit.json").read_bytes())
        self.assertEqual((result["summary"]["pairs_checked"], result["summary"]["literal_evidence_language_checks"]), (240, 80))
        self.assertEqual(result["summary"]["source_inspection_pairs"], 12)
        self.assertFalse(result["summary"]["cross_role_comparisons_applicable"])
        self.assertFalse(result["summary"]["historical_exposure_registry_is_exhaustive"])
        for row in result["pairs"]:
            self.assertNotIn("labels", row)
            self.assertNotIn("contrast", row)
        self.assertEqual(before, (PILOT / "manifest.json").read_bytes())

    def test_changed_numeric_evidence_is_refused(self):
        raw, source, recipe = self.fact()
        recipe["evidence"]["en"] = recipe["evidence"]["en"].replace("magnitude 8", "magnitude 9")
        with self.assertRaisesRegex(ValueError, "frozen value"):
            A.fact_receipt(raw, source, recipe)

    def test_korean_numeric_mismatch_is_refused(self):
        raw, source, recipe = self.fact()
        recipe["evidence"]["ko"] += " changed"
        with self.assertRaisesRegex(ValueError, "Korean catalog"):
            A.fact_receipt(raw, source, recipe)

    def test_multi_event_source_is_not_silently_first_selected(self):
        raw, source, recipe = self.fact()
        raw["features"].append(raw["features"][0])
        with self.assertRaisesRegex(ValueError, "one catalog"):
            A.fact_receipt(raw, source, recipe)

    def test_changed_catalog_revision_is_refused(self):
        raw, source, recipe = self.fact()
        raw["features"][0]["properties"]["updated"] += 1
        with self.assertRaisesRegex(ValueError, "revision"):
            A.fact_receipt(raw, source, recipe)

    def test_saved_stdout_mismatch_is_refused_without_replay(self):
        raw, recipe = self.observation()
        raw["stdout"] = "False\n"
        with self.assertRaisesRegex(ValueError, "saved stdout"):
            A.observation_receipt(raw, recipe)

    def test_failed_observation_cannot_be_called_successful(self):
        raw, recipe = self.observation()
        raw["returncode"] = 1
        with self.assertRaisesRegex(ValueError, "successful recorded"):
            A.observation_receipt(raw, recipe)

    def test_missing_input_scope_is_refused(self):
        raw, recipe = self.observation()
        recipe["context"]["ko"] = "missing code"
        with self.assertRaisesRegex(ValueError, "input missing"):
            A.observation_receipt(raw, recipe)

    def test_authoring_intent_does_not_override_endpoint_text(self):
        pair = copy.deepcopy(self.bundle["pairs"][0])
        pair["a"]["text"] += " mutation"
        with self.assertRaisesRegex(ValueError, "authoring recipe"):
            A.recipe_endpoints(self.recipes["source:laya-p1-fact01"], pair)

    def test_issue_anchor_and_hash_are_bound_to_source(self):
        contents = {sid: (PILOT / s["snapshot_path"]).read_bytes().decode() for sid, s in self.sources.items()}
        for field, replacement, message in (("source_sha256", "0" * 64, "source hash"),
                                            ("source_anchors", ["absent anchor"], "anchor missing")):
            issues = copy.deepcopy(self.issues)
            issues["issues"][0][field] = replacement
            with self.assertRaisesRegex(ValueError, message):
                A.issue_receipts(issues, self.bundle, contents)

    def test_issue_metadata_cannot_smuggle_predicate_labels(self):
        issues = copy.deepcopy(self.issues)
        issues["issues"][0]["labels"] = {"supports": 1}
        with self.assertRaisesRegex(ValueError, "unexpected evidence issue"):
            A.issue_receipts(issues, self.bundle, {})

    def test_known_exposure_matches_each_supported_identity(self):
        pair = self.bundle["pairs"][0]
        source = self.sources["source:laya-p1-fact01"]
        identifiers = (("source_hashes", source["sha256"]), ("family_ids", pair["family_id"]),
                       ("source_ids", pair["a"]["endpoint_id"]), ("document_ids", source["document_id"]),
                       ("text_hashes", D.digest(D.normalized(pair["a"]["text"]).encode())))
        for field, value in identifiers:
            with self.subTest(field=field):
                event = {"event_id": "exposure:fixture", "kind": "training_used", field: [value]}
                self.assertEqual(A.exposure_matches(pair, source, [event]), [{"event_id": "exposure:fixture", "kind": "training_used"}])
                event[field] = ["unmatched"]
                self.assertEqual(A.exposure_matches(pair, source, [event]), [])

    def test_output_cannot_be_written_into_frozen_input(self):
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stderr(io.StringIO()):
            A.main(["--directory", str(PILOT), "--issues", str(AUDIT / "issues.json"), "--out", str(PILOT / "new-audit.json")])
        self.assertEqual(exc.exception.code, 2)

    def test_output_cannot_overwrite_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit.json"
            target.write_bytes(b"existing")
            with self.assertRaises(SystemExit) as exc, contextlib.redirect_stderr(io.StringIO()):
                A.main(["--directory", str(PILOT), "--issues", str(AUDIT / "issues.json"), "--out", str(target)])
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(target.read_bytes(), b"existing")

    def test_source_corruption_is_caught_before_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pilot"
            shutil.copytree(PILOT, root)
            (root / "sources/obs01.txt").write_bytes(b"replaced")
            with self.assertRaisesRegex(ValueError, "artifact hash"):
                A.audit(root, self.issues)


if __name__ == "__main__":
    unittest.main()
