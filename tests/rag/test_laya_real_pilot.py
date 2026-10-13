"""실제 파일럿의 스냅샷 변조와 검토 결과 위장을 거절한다."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from rag_eval.laya import data_contracts as D, pilot_verify as V

DATA = Path(__file__).resolve().parents[2] / "rag_eval/evalsets/laya-contrast-pilot-v1"


class RealPilot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "pilot"
        shutil.copytree(DATA, self.root)

    def refresh_manifest(self):
        manifest = V.read(self.root, "manifest.json")
        for name in manifest["files"]:
            manifest["files"][name] = D.digest((self.root / name).read_bytes())
        (self.root / "manifest.json").write_bytes(D.encoded(manifest))

    def test_real_unlabeled_pilot_and_review_allocation(self):
        result = V.verify(self.root)
        self.assertEqual((result["pairs"], result["families"], result["sources"]), (240, 60, 60))
        self.assertEqual(result["language_cells"], dict.fromkeys(("ko_ko", "en_en", "ko_en", "en_ko"), 60))
        self.assertEqual(result["review_selected"], 63)
        self.assertEqual(result["training_exportable_pairs"], 0)

    def test_source_mutation_fails_even_with_refreshed_inventory(self):
        (self.root / "sources/spec01.txt").write_bytes(b"replaced source")
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
            V.verify(self.root)

    def test_post_authoring_assignment_change_is_refused(self):
        b = V.read(self.root, "bundle.json")
        b["assignments"][0]["role"] = "sealed_test"
        (self.root / "bundle.json").write_bytes(D.encoded(b))
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "assignment changed"):
            V.verify(self.root)

    def test_generator_label_cannot_leak_into_blind_queue(self):
        p = self.root / "review/blind-review.jsonl"
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
        rows[0]["labels"] = {"supports": 1}
        p.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows))
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "blind queue differs"):
            V.verify(self.root)

    def test_prefilled_human_identity_is_not_an_empty_review(self):
        p = self.root / "review/human-input.jsonl"
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
        rows[0]["actor"] = "fabricated reviewer"
        p.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in rows))
        self.refresh_manifest()
        with self.assertRaisesRegex(ValueError, "filled form"):
            V.verify(self.root)


if __name__ == "__main__":
    unittest.main()
