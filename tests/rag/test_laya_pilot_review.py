"""수량만 채운 가짜 독립 가족과 생성자 라벨의 검토 입력 유출을 막는다."""
import copy
import tempfile
import unittest
from pathlib import Path

from rag_eval.laya import data_contracts as D, pilot_review as P
from rag_eval.laya.pins import render_state
from test_laya_data_contracts import fixture


class Pilot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        b = fixture(self.root)
        self.b = {**b, "sources": [], "spans": [], "pairs": [], "annotations": [], "assignments": []}
        for i in range(60):
            source = copy.deepcopy(b["sources"][0])
            source.update(source_id=f"fixture:s{i}", document_id=f"fixture:d{i}", snapshot_path=f"s{i}.txt", category=f"category{i % 3}")
            text = f"This is synthetic test source number {i}."
            self.root.joinpath(source["snapshot_path"]).write_bytes(text.encode())
            source["sha256"] = D.digest(text.encode())
            self.b["sources"].append(source)
            span = {"span_id": f"fixture:span{i}", "source_id": source["source_id"], "source_sha256": source["sha256"], "start": 0, "end": len(text), "excerpt_sha256": source["sha256"]}
            self.b["spans"].append(span)
            self.b["assignments"].append({**b["assignments"][0], "source_id": source["source_id"], "groups": [f"fixture:g{i}"]})
            for j, contrast in enumerate(sorted(P.CONTRASTS)):
                pair = copy.deepcopy(b["pairs"][0])
                pair.update(pair_id=f"fixture:data:{i}-{j}", family_id=f"fixture:f{i}", contrast=contrast)
                langs = P.CELLS[i // 15].split("_")
                for k, lang in zip(("a", "b"), langs):
                    pair[k].update(text=f"Test {i} {j} {k}", language=lang, endpoint_id=f"fixture:{i}:{j}:{k}", mode="authored", evidence=[span["span_id"]])
                pair["render_sha256"] = D.digest(render_state(pair["a"]["text"], pair["b"]["text"]).encode())
                self.b["pairs"].append(pair)

    def test_blind_queue_is_reproducible_and_has_fifteen_families_per_cell(self):
        queue, report = P.prepare(self.b, self.root)
        self.assertEqual(len(queue), 60)
        self.assertEqual((queue, report), P.prepare(self.b, self.root))
        self.assertEqual(report["human_reviews_completed"], 0)
        for row in queue:
            self.assertFalse({"labels", "rationale", "contrast", "mechanism"} & set(row))
        pairs = {p["pair_id"]: p for p in self.b["pairs"]}
        for cell in P.CELLS:
            chosen = [pairs[r["pair_id"]] for r in queue if pairs[r["pair_id"]]["a"]["language"] + "_" + pairs[r["pair_id"]]["b"]["language"] == cell]
            self.assertEqual(len({p["family_id"] for p in chosen}), 15)

    def test_required_case_outside_sample_is_added(self):
        queue, _ = P.prepare(self.b, self.root)
        selected = {x["pair_id"] for x in queue}
        pair = next(p for p in self.b["pairs"] if p["pair_id"] not in selected)
        pair["review_required"] = True
        self.assertEqual(len(P.prepare(self.b, self.root)[0]), 61)

    def test_shared_doc_is_not_sixty_independent_families(self):
        self.b["sources"][1]["document_id"] = self.b["sources"][0]["document_id"]
        with self.assertRaisesRegex(ValueError, "independent pilot family"):
            P.prepare(self.b, self.root)

    def test_incomplete_pilot_refused(self):
        self.b["pairs"].pop()
        with self.assertRaisesRegex(ValueError, "240 pairs"):
            P.prepare(self.b, self.root)


if __name__ == "__main__":
    unittest.main()
