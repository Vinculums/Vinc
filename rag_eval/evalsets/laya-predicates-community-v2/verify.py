"""Verify laya-predicates-community-v2 without loading or running Laya."""
import collections
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRED = ("supports", "contradicts", "same_as")
GROUNDED = "ai_statement_grounded_in_pack_document"
# Other community sets. No statement of this set may appear in any of them.
SIBLINGS = ("laya-predicates-community-v1", "laya-predicates-community-calib-v1", "laya-predicates-community-train-v1",
            "laya-predicates-community-calib-v2")
# Sets a judge is trained or calibrated on. No pack node of this set may appear in them.
OTHER_SIDE = ("laya-predicates-community-calib-v1", "laya-predicates-community-train-v1", "laya-predicates-community-calib-v2")
_H = re.compile(r"[가-힣]")


def read(name, root=ROOT):
    return [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines() if line.strip()]


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


def nodes_of(rows):
    return {r.get("provenance", {}).get(k, {}).get("node_id") for r in rows for k in ("a", "b")} - {None}


rows = read("gold.jsonl")
split_doc = json.loads((ROOT / "gold.split.json").read_text(encoding="utf-8"))
n = len(rows)
check(n == 300, "300 pairs expected")
check(len({r["pair_id"] for r in rows}) == n, "duplicate pair ids")
texts = [r[k]["text"] for r in rows for k in ("a", "b")]
check(len(set(texts)) == len(texts), "a statement is used in two pairs")
check(set(split_doc["split"]) == {r["pair_id"] for r in rows}, "split membership mismatch")
check(set(split_doc["split"].values()) == {"eval"}, "this set is measurement only")
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
    check(any(r["provenance"][k]["kind"] == GROUNDED for k in ("a", "b")), "pair without a grounded statement " + r["pair_id"])
    check(not any(ch in r[k]["text"] for k in ("a", "b") for ch in "—–*$"), "dash or markup in a statement " + r["pair_id"])
    check(r["provenance"]["pack_domain"] in ("art", "economics"), "a pair is outside the art and economics packs " + r["pair_id"])
for p in PRED:
    check(sum(r["labels"][p] for r in rows) == 60, f"60 {p} positives expected")
    for lang, want in (("en", 24), ("ko", 18), ("mixed", 18)):
        check(sum(r["labels"][p] for r in rows if r["lang"] == lang) == want, f"{p} positives in {lang}")
nodes = nodes_of(rows)
check(len(nodes) == 57, "57 pack nodes expected")
text_set = set(texts)
for name in SIBLINGS:
    other = ROOT.parent / name / "gold.jsonl"
    if not other.exists():
        check(name.endswith("-v2"), "sibling set missing: " + name)
        continue
    other_rows = read("gold.jsonl", ROOT.parent / name)
    check(not text_set & {g[k]["text"] for g in other_rows for k in ("a", "b")}, "a statement is shared with " + name)
    if name in OTHER_SIDE:
        check(not nodes & nodes_of(other_rows), "a pack node is shared with " + name)
check(read("blind-inputs.jsonl") == [{k: r[k] for k in ("pair_id", "a", "b", "context", "lang")} for r in rows], "blind inputs differ")
check(read("reference-labels.jsonl") == [{k: r[k] for k in ("pair_id", "labels", "rationale", "author", "labeled_at")} for r in rows],
      "reference labels projection mismatch")
manifest = ROOT / "manifest.json"
if manifest.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in data["sha256"].items():
        check(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "hash mismatch " + name)
c = collections.Counter(r["lang"] for r in rows)
print(f"PASS: {n} measurement pairs ({dict(c)}), 60 positives per predicate, {len(nodes)} art and economics pack nodes, "
      "no statement shared with another community set, no node shared with a training or calibration set, projections, hashes.")
print("LIMIT: written and labelled by one AI; not yet read by a second reader; Korean statements are AI-written.")
print("No model inference was performed by this validator.")
