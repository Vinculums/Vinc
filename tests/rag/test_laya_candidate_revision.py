"""수정 이력, 개발 분할 및 실제 출처를 라벨 없이 검증한다."""
import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag_eval.laya import candidate_revision as R, data_contracts as D, pilot_verify as V
from rag_eval.laya import development_sources as S
from rag_eval.laya.pins import render_state

DATA = Path(__file__).resolve().parents[2] / "rag_eval/evalsets"
ORIGINAL = DATA / "laya-contrast-pilot-v1"
REVISION = DATA / "laya-contrast-revision-v2"
DEV = DATA / "laya-contrast-dev-v1"


class CandidateRevision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous = V.read(ORIGINAL, "bundle.json")
        cls.history = V.read(REVISION, "bundle.json")
        cls.dev = V.read(DEV, "bundle.json")
        cls.chosen = V.read(REVISION, "selection.json")

    def joint(self):
        return R.joint_bundle(self.history, self.dev, ORIGINAL, DEV)

    def test_actual_cohorts_reproduce_and_do_not_export(self):
        before = (ORIGINAL / "manifest.json").read_bytes()
        result = R.verify(ORIGINAL, REVISION, DEV)
        self.assertEqual((result["history_pairs"], result["active_candidates"], result["development_pairs"]), (252, 240, 96))
        self.assertEqual(result["joint_pairs_checked"], 348)
        self.assertEqual(result["development_literal_language_checks"], 32)
        self.assertEqual((result["human_annotations"], result["training_exportable_pairs"], result["model_calls"]), (0, 0, 0))
        self.assertEqual(before, (ORIGINAL / "manifest.json").read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "no reviewed eligible"):
                D.export_training(self.history, ORIGINAL, Path(tmp) / "export")

    def test_old_payload_cannot_be_replaced(self):
        changed = copy.deepcopy(self.history)
        changed["pairs"][0]["a"]["text"] = "rewritten parent"
        with self.assertRaisesRegex(ValueError, "history must append"):
            R.validate_selection(self.previous, changed, self.chosen)

    def test_selection_rejects_stale_hash_and_child_without_parent(self):
        changed = copy.deepcopy(self.chosen)
        pid = next(iter(changed["active_pair_hashes"]))
        changed["active_pair_hashes"][pid] = "0" * 64
        with self.assertRaisesRegex(ValueError, "selection or candidate hash"):
            R.validate_selection(self.previous, self.history, changed)
        history = copy.deepcopy(self.history)
        history["pairs"][-1]["parents"] = []
        chosen = R.selection(self.previous, history, self.chosen["replacements"])
        with self.assertRaisesRegex(ValueError, "replacement lineage"):
            R.validate_selection(self.previous, history, chosen)

    def test_source_hash_and_document_crossings_are_refused(self):
        for field in ("sha256", "document_id"):
            joint = self.joint()
            joint["sources"][60][field] = joint["sources"][0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "snapshot hash|crosses split"):
                D.validate(joint, DATA)

    def test_shared_lineage_and_family_cannot_cross_roles(self):
        joint = self.joint()
        joint["assignments"][60]["groups"] = joint["assignments"][0]["groups"]
        with self.assertRaisesRegex(ValueError, "lineage group crosses"):
            D.validate(joint, DATA)
        joint = self.joint()
        joint["pairs"][252]["family_id"] = joint["pairs"][0]["family_id"]
        with self.assertRaisesRegex(ValueError, "family crosses"):
            D.validate(joint, DATA)

    def test_exact_text_crossing_is_refused_and_near_match_is_unresolved(self):
        for suffix in ("", " extra"):
            joint = self.joint()
            # 내용이 긴 원문 끝에 한 단어만 보태도 근접 후보로 걸려야 한다.
            original = max(joint["pairs"][:240], key=lambda p: len(D.lexical(p["a"]["text"])))
            target = joint["pairs"][252]
            target["a"]["text"] = original["a"]["text"] + suffix
            target["render_sha256"] = D.digest(render_state(target["a"]["text"], target["b"]["text"], target["context"]).encode())
            if not suffix:
                with self.assertRaisesRegex(ValueError, "normalized text crosses"):
                    D.validate(joint, DATA)
            else:
                report, _ = D.validate(joint, DATA)
                self.assertTrue(report["unresolved_near_duplicates"])
                self.assertFalse(report["ready_for_export"])

    def test_frozen_allocation_before_authoring_is_required(self):
        dev = copy.deepcopy(self.dev)
        dev["assignments"][0]["assigned_at"] = "2099-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "split assigned after authoring"):
            D.validate(dev, DEV)

    def test_opened_measurement_cannot_be_reused_for_development(self):
        dev = copy.deepcopy(self.dev)
        dev["exposures"].append({"event_id": "exposure:test-opened", "kind": "measurement_opened",
                                "at": "2026-10-11T12:00:00Z", "reference": "test only",
                                "source_ids": [dev["sources"][0]["source_id"]]})
        with self.assertRaisesRegex(ValueError, "diagnostic only"):
            D.validate(dev, DEV)

    def test_created_development_data_cannot_become_sealed_measurement(self):
        dev = copy.deepcopy(self.dev)
        for row in dev["assignments"]:
            row["role"] = "sealed_test"
        with self.assertRaisesRegex(ValueError, "exposed data cannot be sealed"):
            D.validate(dev, DEV)

    def test_blind_queue_uses_current_hashes_and_hides_author_intent(self):
        active = R.validate_selection(self.previous, self.history, self.chosen)
        queue = R.blind_queue(self.history, active, ORIGINAL)
        self.assertEqual(queue, R.blind_queue(self.history, list(reversed(active)), ORIGINAL))
        self.assertFalse(set(self.chosen["superseded_pair_hashes"]) & {q["pair_id"] for q in queue})
        for q in queue:
            self.assertTrue(q["evidence"])
            self.assertFalse({"contrast", "labels", "mechanism", "parents", "rationale"} & set(q))
        hydrated = R.hydrated_queue(self.history, active, ORIGINAL)
        self.assertEqual(len(hydrated), 240)
        for q in hydrated:
            for evidence in q["evidence"]:
                self.assertEqual(D.digest(evidence["text"].encode()), evidence["excerpt_sha256"])

    def test_literal_receipt_rejects_wrong_korean_field_or_sql_output(self):
        recipes = V.read(DEV, "authoring-recipes.json")
        for prefix in ("fact", "obs"):
            changed = copy.deepcopy(recipes)
            row = next(r for r in changed if r["source_id"].split("-")[-1].startswith(prefix))
            row["evidence"]["ko"] += " 잘못된 값"
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, "evidence differs"):
                R.literal_receipts(self.dev, changed, DEV)

    def test_recipe_cannot_add_teacher_labels(self):
        recipes = V.read(DEV, "authoring-recipes.json")
        recipes[0]["labels"] = {"supports": 1}
        empty = copy.deepcopy(self.dev); empty["pairs"] = []; empty["spans"] = []
        with self.assertRaisesRegex(ValueError, "unexpected authoring recipe"):
            R.add_recipes(empty, V.read(DEV, "source-freeze.json"), recipes, DEV, self.dev["pairs"][0]["authored_at"])

    def test_filled_form_and_queue_injection_fail_even_with_new_inventory_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "revision"
            shutil.copytree(REVISION, target)
            for file, change, error in (("review/human-input.json", lambda rows: rows[0].update(actor="invented"), "invented annotations"),
                                        ("review/blind-review.json", lambda rows: rows[0].update(labels={"supports": 1}), "blind queue mismatch")):
                shutil.copyfile(REVISION / file, target / file)
                rows = V.read(target, file); change(rows); (target / file).write_bytes(D.encoded(rows))
                (target / "manifest.json").write_bytes(D.encoded({"schema": "vinc.laya.artifacts.v1", "files": R.inventory(target)}))
                with self.subTest(file=file), self.assertRaisesRegex(ValueError, error):
                    R.verify(ORIGINAL, target, DEV)
                shutil.copyfile(REVISION / file, target / file)

    def test_source_capture_runs_actual_sql_without_model_calls(self):
        observed = S.observe({"key": "test-null", "setup": "", "query": "SELECT NULL IS NULL;"})
        self.assertEqual(observed["rows"], [[1]])
        self.assertEqual(observed["returncode"], 0)
        self.assertIn("sqlite3.connect(':memory:')", observed["code"])
        self.assertIsNone(observed["exact_served_model"])

    def test_capture_never_overwrites_frozen_destination(self):
        plan = V.read(DEV, "source-plan.json")
        with patch.object(S, "fetch", side_effect=AssertionError("no fetch expected")):
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                S.freeze(plan, DEV)

    def test_build_from_frozen_inputs_reproduces_exact_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original, revision, dev = [base / p.name for p in (ORIGINAL, REVISION, DEV)]
            shutil.copytree(ORIGINAL, original)
            frozen = V.read(DEV, "source-freeze.json")
            files = ["source-freeze.json", "source-plan.json"] + [s["snapshot_path"] for s in frozen["sources"]] + [r["path"] for r in frozen["licenses"].values()]
            for name in files:
                dest = dev / name; dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DEV / name, dest)
            result = R.build(original, revision, dev, V.read(REVISION, "authoring-recipes.json"),
                             V.read(DEV, "authoring-recipes.json"), self.dev["pairs"][0]["authored_at"],
                             self.dev["source_freeze_commit"])
            self.assertTrue(result["verified"])
            for old, new in ((REVISION, revision), (DEV, dev)):
                self.assertEqual((old / "manifest.json").read_bytes(), (new / "manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
