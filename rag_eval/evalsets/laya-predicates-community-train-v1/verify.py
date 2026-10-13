"""Verify laya-predicates-community-train-v1 without loading or running Laya."""
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
check(n >= 450, "at least 450 rows required")
check(len({r["pair_id"] for r in rows}) == n, "duplicate pair ids")
check(len({tuple(sorted([r["a"]["text"], r["b"]["text"]])) for r in rows}) == n, "duplicate text pairs")
check(set(split_doc["split"]) == {r["pair_id"] for r in rows}, "split membership mismatch")
check(set(split_doc["split"].values()) == {"calib"}, "this set is training only: every pair carries the trainable split value")
check(hashlib.sha256((ROOT / "gold.jsonl").read_bytes()).hexdigest() == split_doc["gold_sha256"], "split/gold digest mismatch")
for r in rows:
    check(set(r["labels"]) == set(PRED), "predicate keys")
    check(all(type(v) is int and v in (0, 1) for v in r["labels"].values()), "non-binary label")
    check(sum(r["labels"].values()) <= 1, "more than one positive label")
    check(all(r["rationale"].get(p) for p in PRED), "missing label rationale")
    check(r["a"]["text"] != r["b"]["text"], "identical statement pair")
    check(all(0 < len(r[k]["text"]) <= 400 for k in ("a", "b")), "statement character budget")
    check("AI" in r["author"], "AI author must be explicit")
    check(r["lang"] == detect_lang(r["a"]["text"], r["b"]["text"]), "language field disagrees with the harness rule " + r["pair_id"])
    check(any(r["provenance"][k]["kind"] == "ai_statement_grounded_in_pack_document" for k in ("a", "b")), "pair without a grounded statement " + r["pair_id"])
    check(not any(ch in r[k]["text"] for k in ("a", "b") for ch in "—–"), "dash in a statement " + r["pair_id"])
for lang in ("ko", "en", "mixed"):
    for p in PRED:
        check(sum(r["labels"][p] for r in rows if r["lang"] == lang) >= 28, f"{p} positives in {lang}")
nodes = {r["provenance"][k].get("node_id") for r in rows for k in ("a", "b")} - {None}
check(all(i.startswith(("concept:physics-", "concept:math-", "bridge:")) for i in nodes), "a node is outside the physics and mathematics packs")
check(not nodes & {"bridge:math-econ", "bridge:math-art", "bridge:art-math-deeper"}, "a bridge into the measurement domains is used")
texts = {r[k]["text"] for r in rows for k in ("a", "b")}
for other_name in ("laya-predicates-community-v1", "laya-predicates-community-calib-v1"):
    other = ROOT.parent / other_name / "gold.jsonl"
    check(other.exists(), "sibling set missing: " + other_name)
    rows_other = [json.loads(line) for line in other.read_text(encoding="utf-8").splitlines() if line.strip()]
    other_nodes = {g["provenance"][k].get("node_id") for g in rows_other for k in ("a", "b")} - {None}
    check(not nodes & other_nodes, "a pack node is shared with " + other_name)
    check(not texts & {g[k]["text"] for g in rows_other for k in ("a", "b")}, "a text is shared with " + other_name)
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
print(f"PASS: {n} training pairs ({dict(c)}), {len(nodes)} pack nodes, none shared with the measurement or calibration sets, projections, hashes.")
print("LIMIT: AI-written and AI-labelled; Korean statements are AI-written; subject matter leans to quantum gravity.")
print("No model inference was performed by this validator.")
