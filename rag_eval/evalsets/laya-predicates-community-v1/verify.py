"""Verify laya-predicates-community-v1 without loading or running Laya."""
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
check(n >= 120, "at least 120 rows required")
check(len({r["pair_id"] for r in rows}) == n, "duplicate pair ids")
check(len({tuple(sorted([r["a"]["text"], r["b"]["text"]])) for r in rows}) == n, "duplicate text pairs")
check(set(split_doc["split"]) == {r["pair_id"] for r in rows}, "split membership mismatch")
check(set(split_doc["split"].values()) == {"eval"}, "this set is measurement only: every pair must be eval")
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
    check(r["provenance"]["selected_using_model_output"] is False, "gold row selected with model output")
for p in PRED:
    check(sum(r["labels"][p] for r in rows) >= 24, p + " positives")
for lang in ("ko", "en", "mixed"):
    check(sum(r["lang"] == lang for r in rows) >= 35, "language quota " + lang)
gold_nodes = {r["provenance"][k].get("node_id") for r in rows for k in ("a", "b")} - {None}
check(all(i.startswith(("concept:art-", "concept:economics-")) for i in gold_nodes), "a gold node is outside the measurement packs")
neg = read("train-negatives.jsonl")
neg_nodes = {g[k]["id"] for g in neg for k in ("a", "b")}
check(all(i.startswith(("concept:physics-", "concept:math-")) for i in neg_nodes), "a training node is outside the training packs")
check(not gold_nodes & neg_nodes, "a node sits on both sides of the pack split")
gold_texts = {r[k]["text"] for r in rows for k in ("a", "b")}
check(not gold_texts & {g[k]["text"] for g in neg for k in ("a", "b")}, "a text sits on both sides of the pack split")
for g in neg:
    check(g["labels"] == {"supports": None, "contradicts": None, "same_as": 0}, "training negative label shape")
    check(g["a"]["id"] != g["b"]["id"] and g["a"]["text"] != g["b"]["text"], "training negative is not two distinct concepts")
check(len({tuple(sorted((g["a"]["id"], g["b"]["id"]))) for g in neg}) == len(neg), "duplicate training negative")
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
print(f"PASS: {n} measurement pairs ({dict(c)}), {len(neg)} training negatives, pack split holds, projections, hashes.")
print("LIMIT: AI labels by one vendor; Korean sides are AI renderings; training negatives are labelled by rule.")
print("No model inference was performed by this validator.")
