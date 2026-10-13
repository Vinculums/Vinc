"""무라벨 파일럿의 출처 동결과 검토 자료를 모델 호출 없이 대조한다."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from . import data_contracts as D, pilot_review as P


def read(root, name):
    return json.loads(D.safe_file(root, name).read_text(encoding="utf-8"))


def verify(root):
    root = Path(root)
    manifest = read(root, "manifest.json")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p != root / "manifest.json"}
    D.require(set(manifest["files"]) == actual, "artifact inventory changed")
    for name, expected in manifest["files"].items():
        D.require(D.digest(D.safe_file(root, name).read_bytes()) == expected, f"artifact hash mismatch: {name}")
    b, frozen = read(root, "bundle.json"), read(root, "source-freeze.json")
    D.require(b["source_freeze_sha256"] == D.digest((root / "source-freeze.json").read_bytes()), "source freeze hash mismatch")
    D.require(b["sources"] == frozen["sources"] and b["assignments"] == frozen["assignments"], "source assignment changed after freeze")
    D.require(all(D.stamp(p["authored_at"]) > D.stamp(frozen["created_at"]) for p in b["pairs"]), "authoring did not follow source freeze")
    D.require(not b["annotations"] and b["label_status"] == "unlabeled" and b["training_authorized"] is False, "draft contains labels or training authorization")
    report, accepted = D.validate(b, root)
    D.require(not accepted and report == read(root, "validation.json"), "saved validation changed")
    spec = P.validate_spec(read(root, "review-spec.json"))
    D.require(spec["rubric_id"] == b["rubric"]["id"], "review rubric mismatch")
    queue, receipt = P.prepare(b, root)
    saved = [json.loads(line) for line in (root / "review/blind-review.jsonl").read_text(encoding="utf-8").splitlines()]
    D.require(saved == queue, "blind queue differs from frozen pairs")
    receipt_saved = read(root, "review/manifest.json")
    D.require(all(receipt_saved.get(k) == v for k, v in receipt.items()), "review receipt mismatch")
    D.require(receipt_saved["queue_sha256"] == D.digest((root / "review/blind-review.jsonl").read_bytes()), "queue hash mismatch")
    D.require(receipt_saved["review_spec_sha256"] == D.fingerprint(spec), "review spec hash mismatch")
    forms = [json.loads(line) for line in (root / "review/human-input.jsonl").read_text(encoding="utf-8").splitlines()]
    D.require([(x["pair_id"], x["pair_sha256"]) for x in forms] == [(x["pair_id"], x["pair_sha256"]) for x in queue], "human forms differ from queue")
    D.require(all(x["status"] == "unfilled" and x["actor"] is None and x["at"] is None and all(v is None for v in x["labels"].values()) and all(v is None for v in x["rationale"].values()) for x in forms), "filled form must be ingested as a new annotation version")
    allocations = {a["family_id"]: a for a in frozen["allocation"]}
    span_sources = {s["span_id"]: s["source_id"] for s in b["spans"]}
    for pair in b["pairs"]:
        a = allocations[pair["family_id"]]
        D.require(pair["a"]["language"] + "_" + pair["b"]["language"] == a["language_cell"], "language allocation changed after freeze")
        D.require(all(span_sources[e] == a["source_id"] for side in ("a", "b") for e in pair[side]["evidence"]), "family source changed after freeze")
        D.require(pair["author_kind"] == "ai" and pair["exact_served_model"] is None, "draft author identity changed")
    for license_record in frozen["licenses"].values():
        D.require(D.digest(D.safe_file(root, license_record["path"]).read_bytes()) == license_record["sha256"], "license snapshot changed")
    audit = read(root, "audit.json")
    D.require(audit["pairs"] == report["pairs"] and audit["context_pairs"] == sum(bool(p["context"]) for p in b["pairs"]), "audit counts changed")
    pairs = {p["pair_id"]: p for p in b["pairs"]}
    counts = Counter(pairs[q["pair_id"]]["a"]["language"] + "_" + pairs[q["pair_id"]]["b"]["language"] for q in queue)
    return {"verified": True, "pairs": len(pairs), "families": report["families"], "sources": report["sources"],
            "language_cells": report["language_cells"], "review_selected": len(queue), "review_language_cells": dict(counts),
            "human_reviews_completed": 0, "training_exportable_pairs": 0, "model_calls": 0,
            "manifest_sha256": D.digest((root / "manifest.json").read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.directory)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
