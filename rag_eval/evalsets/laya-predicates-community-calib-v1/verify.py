"""Verify laya-predicates-community-calib-v1 without loading or running Laya."""
import collections
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRED = ("supports", "contradicts", "same_as")
_H = re.compile(r"[가-힣]")


def read(name):
    return [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def check(ok, message):
    if not ok:
        raise SystemExit("FAIL: " + message)


def lang_one(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    ratio = sum(1 for c in letters if _H.match(c)) / len(letters)
    return "ko" if ratio > 0.6 else "en" if ratio < 0.1 else "mixed"


def detect_lang(*texts):  # the rule of rag_eval/laya/pins.py
    langs = {lang_one(t) for t in texts if t and t.strip()}
    return "en" if not langs else langs.pop() if len(langs) == 1 else "mixed"


rows = read("gold.jsonl")
split_doc = json.loads((ROOT / "gold.split.json").read_text(encoding="utf-8"))
n = len(rows)
check(n >= 110, "at least 110 rows required")
check(len({r["pair_id"] for r in rows}) == n, "duplicate pair ids")
check(len({tuple(sorted([r["a"]["text"], r["b"]["text"]])) for r in rows}) == n, "duplicate text pairs")
check(set(split_doc["split"]) == {r["pair_id"] for r in rows}, "split membership mismatch")
check(set(split_doc["split"].values()) == {"calib"}, "this set is calibration only: every pair must be calib")
check(hashlib.sha256((ROOT / "gold.jsonl").read_bytes()).hexdigest() == split_doc["gold_sha256"], "split/gold digest mismatch")
for r in rows:
    check(set(r["labels"]) == set(PRED), "predicate keys")
    check(all(type(v) is int and v in (0, 1) for v in r["labels"].values()), "non-binary label")
    check(all(r["rationale"].get(p) for p in PRED), "missing label rationale")
    check(r["a"]["text"] != r["b"]["text"], "identical statement pair")
    check(all(0 < len(r[k]["text"]) <= 400 for k in ("a", "b")), "statement character budget")
    check("AI" in r["author"], "AI author must be explicit")
    check(not (r["labels"]["same_as"] and r["labels"]["supports"]), "restatement labeled as evidence")
    check(not (r["labels"]["contradicts"] and (r["labels"]["supports"] or r["labels"]["same_as"])), "incompatible labels")
    check(r["lang"] == detect_lang(r["a"]["text"], r["b"]["text"]), "language field disagrees with the harness rule " + r["pair_id"])
    check(any(r["provenance"][k]["kind"] != "ai_authored_fixture" for k in ("a", "b")), "pair without pack content " + r["pair_id"])
    check(r["provenance"]["selected_using_model_output"] is False, "row selected with model output")
for lang in ("ko", "en", "mixed"):
    check(sum(r["lang"] == lang for r in rows) >= 35, "language quota " + lang)
    for p in PRED:
        check(sum(r["labels"][p] for r in rows if r["lang"] == lang) >= 7, f"{p} positives in {lang}")
# calibration buckets of rag_eval.laya.fit_calib: mixed uses the en file, symmetric predicates are asked in both directions
for bucket in ("ko", "en"):
    pairs = sum(("en" if r["lang"] == "mixed" else r["lang"]) == bucket for r in rows)
    check(pairs >= 30, "a calibration bucket is below min_bucket_n 30: supports " + bucket)
nodes = {r["provenance"][k].get("node_id") for r in rows for k in ("a", "b")} - {None}
check(all(i.startswith(("concept:physics-", "concept:math-", "bridge:")) for i in nodes), "a node is outside the physics and mathematics packs")
measure = ROOT.parent / "laya-predicates-community-v1" / "gold.jsonl"
if measure.exists():
    other = [json.loads(line) for line in measure.read_text(encoding="utf-8").splitlines() if line.strip()]
    other_nodes = {g["provenance"][k].get("node_id") for g in other for k in ("a", "b")} - {None}
    check(not nodes & other_nodes, "a node is shared with the community measurement set")
    check(not {r[k]["text"] for r in rows for k in ("a", "b")} & {g[k]["text"] for g in other for k in ("a", "b")},
          "a text is shared with the community measurement set")
check(read("blind-inputs.jsonl") == [{k: r[k] for k in ("pair_id", "a", "b", "context", "lang")} for r in rows], "blind inputs differ")
check(read("reference-labels.jsonl") == [{k: r[k] for k in ("pair_id", "labels", "rationale", "author", "labeled_at")} for r in rows],
      "reference labels projection mismatch")
blind = {r["pair_id"]: r for r in read("review-labels-blind.jsonl")}
check(set(blind) == {r["pair_id"] for r in rows}, "blind review membership")
check(all(blind[r["pair_id"]]["labels"] == r["labels"] for r in rows), "a kept pair disagrees with the blind review")
manifest = ROOT / "manifest.json"
if manifest.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in data["sha256"].items():
        check(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "hash mismatch " + name)
c = collections.Counter(r["lang"] for r in rows)
print(f"PASS: {n} calibration pairs ({dict(c)}), no node or text shared with the community measurement set, projections, hashes.")
print("LIMIT: AI labels by one vendor; Korean sides are AI renderings.")
print("No model inference was performed by this validator.")
