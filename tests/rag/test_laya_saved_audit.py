"""VIN-440 진단의 이식이 저장된 결과를 바꾸지 않는지 대조한다."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rag_eval.laya import audit_saved, audit_verify
from rag_eval.laya import data_contracts as D, pilot_review as P


class SavedAudit(unittest.TestCase):
    def test_preparation_manifest_and_unapproved_example(self):
        root = Path(__file__).resolve().parents[2] / "rag_eval/evalsets/laya-data-pipeline-v1"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected, name)
        bundle = json.loads((root / "contract-example.json").read_text(encoding="utf-8"))
        actual = D.validate(bundle, root)[0]
        self.assertEqual(actual, json.loads((root / "contract-validation.json").read_text(encoding="utf-8")))
        self.assertEqual(actual["exportable_pairs"], 0)
        self.assertEqual(manifest["actual_pilot_pairs"], 0)
        P.validate_spec(json.loads((root / "pilot-review-spec.json").read_text(encoding="utf-8")))

    def test_original_diagnostics_and_180_checks_reproduce_with_explicit_lf_conversion(self):
        root = Path(__file__).resolve().parents[2]
        dataset = root / "rag_eval/evalsets/laya-predicates-community-supports-v2"
        run = dataset / "run-2026-10-11-supports-v2"
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            analysis, verification = Path(tmp) / "analysis.json", Path(tmp) / "verification.json"
            common = ["--dataset", str(dataset), "--run", str(run)]
            audit_saved.main(common + ["--out", str(analysis)])
            audit_verify.main(common + ["--analysis", str(analysis), "--out", str(verification)])
            self.assertEqual(hashlib.sha256(analysis.read_bytes()).hexdigest(), "cb412ad6ca14051dea663fbe595a08693615c9f7437b12282522c2e61baa264e")
            self.assertEqual(hashlib.sha256(analysis.read_bytes().replace(b"\n", b"\r\n")).hexdigest(), "42d6137fb2a8c7950e73b222d139841b838f9bbb0ec0d029acf96b07de2b91b2")
            result = json.loads(verification.read_text())
            self.assertEqual(result["checks_passed"], 180)
            self.assertEqual(hashlib.sha256(verification.read_bytes()).hexdigest(), "9742c8ddca4e5efe3f263768cf7772caf1cacc3381a5e7a1836d3f894d66a860")
            result["analysis_sha256"] = "42d6137fb2a8c7950e73b222d139841b838f9bbb0ec0d029acf96b07de2b91b2"
            old_bytes = json.dumps(result, indent=2).replace("\n", "\r\n").encode()
            self.assertEqual(hashlib.sha256(old_bytes).hexdigest(), "578194df9f8ecc58cb507be951efec3ccc840fff0cc700067246084687cb8aef")


if __name__ == "__main__":
    unittest.main()
