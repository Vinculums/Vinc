"""파일럿 배분과 검토 큐를 검사한다. 생성자 라벨은 검토용 입력에서 제외한다."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from .data_contracts import digest, encoded, fingerprint, require, safe_file, validate
from .pins import QUESTIONS, questions_hash

CELLS = ("ko_ko", "en_en", "ko_en", "en_ko")
CONTRASTS = {"support", "restatement_or_topical", "scope_time_condition", "reverse_or_conflict"}


def validate_spec(spec):
    require(spec.get("schema") == "vinc.laya.pilot-review.v1", "unsupported review specification")
    require(spec.get("questions_hash") == questions_hash() and spec.get("questions") == QUESTIONS, "review question contract mismatch")
    require((spec.get("pairs"), spec.get("families"), spec.get("pairs_per_family")) == (240, 60, 4), "pilot allocation mismatch")
    require(spec.get("language_cells") == dict.fromkeys(CELLS, 60), "pilot language allocation mismatch")
    require(set(spec.get("contrasts", [])) == CONTRASTS and spec.get("minimum_human_review_per_cell") == 15, "pilot review quota mismatch")
    require(spec.get("instructions"), "review instructions required")
    return spec


def prepare(bundle, root, *, seed=20261011):
    report, _ = validate(bundle, root)
    require(not report["unresolved_near_duplicates"], "resolve near matches before pilot review")
    require(len(bundle["pairs"]) == 240, "pilot needs 240 pairs")
    require(report["language_cells"] == dict.fromkeys(CELLS, 60), "pilot needs 60 pairs per direction")
    families = defaultdict(list)
    for pair in bundle["pairs"]:
        families[pair["family_id"]].append(pair)
    require(len(families) == 60, "pilot needs 60 independent source families")
    for rows in families.values():
        require(len(rows) == 4 and {p["contrast"] for p in rows} == CONTRASTS, "each family needs four independently judged contrasts")
    require(len({s["category"] for s in bundle["sources"]}) >= 3, "pilot needs three source categories")
    span_sources = {s["span_id"]: s["source_id"] for s in bundle["spans"]}
    source_docs = {s["source_id"]: s["document_id"] for s in bundle["sources"]}
    source_groups = {s["source_id"]: s["groups"] for s in bundle["assignments"]}
    lineage = defaultdict(set)
    for pair in bundle["pairs"]:
        refs = {r for k in ("a", "b") for r in pair[k]["evidence"]} | set(pair.get("context_evidence", []))
        for ref in refs:
            sid = span_sources[ref]
            for key in ["source:" + sid, "doc:" + source_docs[sid], *source_groups[sid]]:
                lineage[key].add(pair["family_id"])
    require(all(len(f) == 1 for f in lineage.values()), "shared source lineage is not an independent pilot family")
    # 표본 순서는 seed와 쌍 id로 고정한다. 이 큐가 사람 검토 결과를 대신하지는 않는다.
    selected = []
    for cell in CELLS:
        rows = [p for p in bundle["pairs"] if p["a"]["language"] + "_" + p["b"]["language"] == cell]
        rows.sort(key=lambda p: digest((str(seed) + ":" + p["pair_id"]).encode()))
        by_family = {}
        for p in rows:
            by_family.setdefault(p["family_id"], p)
        require(len(by_family) >= 15, "review needs 15 distinct families per direction")
        selected.extend(list(by_family.values())[:15])
    # 쟁점은 표본 밖이어도 남긴다. labels/rationale/contrast는 blind 입력으로 보내지 않는다.
    selected_ids = {p["pair_id"] for p in selected}
    for pair in bundle["pairs"]:
        if pair.get("review_required") and pair["pair_id"] not in selected_ids:
            selected.append(pair)
    spans = {s["span_id"]: s for s in bundle["spans"]}
    sources = {s["source_id"]: s for s in bundle["sources"]}
    queue = []
    for pair in selected:
        refs = {r for k in ("a", "b") for r in pair[k]["evidence"]} | set(pair.get("context_evidence", []))
        evidence = []
        for ref in sorted(refs):
            span = spans[ref]
            source = sources[span["source_id"]]
            text = safe_file(root, source["snapshot_path"]).read_bytes().decode("utf-8")
            evidence.append({"span_id": ref, "text": text[span["start"]:span["end"]], "origin_uri": source["origin_uri"], "source_sha256": source["sha256"]})
        queue.append({"pair_id": pair["pair_id"], "pair_sha256": fingerprint(pair), "a": pair["a"]["text"],
                      "b": pair["b"]["text"], "context": pair.get("context"), "evidence": evidence,
                      "rubric_id": bundle["rubric"]["id"], "questions_hash": bundle["questions_hash"]})
    return queue, {"bundle_sha256": fingerprint(bundle), "selected": len(queue), "seed": seed,
                   "human_reviews_completed": 0, "screening_tier": "human_review_pending", "model_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    spec = validate_spec(json.loads(args.spec.read_text(encoding="utf-8")))
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    require(spec["rubric_id"] == bundle["rubric"]["id"], "review specification rubric mismatch")
    queue, receipt = prepare(bundle, args.bundle.parent)
    args.out.mkdir(parents=True, exist_ok=False)
    data = b"".join(json.dumps(x, ensure_ascii=False, sort_keys=True).encode() + b"\n" for x in queue)
    (args.out / "blind_review.jsonl").write_bytes(data)
    (args.out / "review-instructions.json").write_bytes(encoded({k: spec[k] for k in ("rubric_id", "questions", "instructions")}))
    receipt["review_spec_sha256"] = fingerprint(spec)
    receipt["queue_sha256"] = digest(data)
    (args.out / "manifest.json").write_bytes(encoded(receipt))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
