"""Verify laya-predicates-ai-v2 without loading or running Laya."""
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
split = split_doc["split"]
n = len(rows)
check(n >= 300, "at least 300 rows required")
check(len({r["pair_id"] for r in rows}) == n, "duplicate pair ids")
check(len({tuple(sorted([r["a"]["text"], r["b"]["text"]])) for r in rows}) == n, "duplicate text pairs")
check(set(split) == {r["pair_id"] for r in rows}, "split membership mismatch")
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
    check(r["provenance"]["selected_using_model_output"] is False, "gold row selected with model output")
for p in PRED:
    for value in (0, 1):
        check(sum(r["labels"][p] == value for r in rows) >= 60, f"{p} class {value} quota")
for lang in ("ko", "en", "mixed"):
    check(sum(r["lang"] == lang for r in rows) >= 100, "language quota " + lang)
# no family and no real node crosses the split
fam_side = collections.defaultdict(set)
node_side = collections.defaultdict(set)
for r in rows:
    fam_side[r["family_id"]].add(split[r["pair_id"]])
    for k in ("a", "b"):
        nid = r["provenance"][k].get("node_id")
        if nid:
            node_side[nid].add(split[r["pair_id"]])
check(all(len(v) == 1 for v in fam_side.values()), "family crosses calib/eval")
check(all(len(v) == 1 for v in node_side.values()), "real node crosses calib/eval")
# calibration buckets: the harness needs 30 records per (predicate, language file); mixed uses the en file
for bucket in ("ko", "en"):
    m = sum(1 for r in rows if split[r["pair_id"]] == "calib" and (r["lang"] == "ko") == (bucket == "ko"))
    check(m >= 30, "calibration bucket below min_bucket_n: " + bucket)
neg = re.compile(r"\b(?:not|no|never|without|cannot|neither|nor)\b|없|않|아니|안 된다|못|무관", re.I)
semantic = [r for r in rows if r["hard_kind"] == "semantic_reversal"]
check(len(semantic) >= 30 and all(r["labels"]["contradicts"] == 1 and not neg.search(r["a"]["text"] + " " + r["b"]["text"])
                                  for r in semantic), "semantic reversal challenge")
check(sum(1 for r in rows if r["hard_kind"] in ("paraphrase", "translation", "real_restatement")) >= 30, "paraphrase challenge")
check(sum(1 for r in rows if r["hard_kind"] in ("lexical_overlap", "time_shift", "scope_shift")) >= 30, "lexical overlap challenge")
check(sum(1 for r in rows if r["hard_kind"] in ("topical_only", "reversed_direction")) >= 30, "topical challenge")
for name in ("calib", "eval"):
    check(read(name + ".jsonl") == [r for r in rows if split[r["pair_id"]] == name], name + " projection mismatch")
check(read("blind-inputs.jsonl") == [{k: r[k] for k in ("pair_id", "a", "b", "context", "lang")} for r in rows], "blind inputs differ")
check(read("reference-labels.jsonl") == [{k: r[k] for k in ("pair_id", "labels", "rationale", "author", "labeled_at")} for r in rows],
      "reference labels projection mismatch")
blind = {r["pair_id"]: r for r in read("review-labels-blind.jsonl")}
check(set(blind) == {r["pair_id"] for r in rows}, "blind review membership")
check(all(blind[r["pair_id"]]["labels"] == r["labels"] for r in rows), "a kept pair disagrees with the blind review")
gold_ids = {r["a"]["id"] for r in rows} | {r["b"]["id"] for r in rows}
gold_nodes = {r["provenance"][k].get("node_id") for r in rows for k in ("a", "b")} - {None}
for d in read("disputed.jsonl"):
    check(d["blind_review"]["labels"] != d["labels"], "disputed row without a disagreement")
for a in read("fill-same-as-audit.jsonl"):
    check(a["selected_using_model_output"] is True, "audit row flag")
    check(a["a"]["id"] not in gold_nodes | gold_ids and a["b"]["id"] not in gold_nodes | gold_ids, "audit row shares a node with gold")
manifest = ROOT / "manifest.json"
if manifest.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for name, digest in data["sha256"].items():
        check(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "hash mismatch " + name)
c = collections.Counter(split.values())
print(f"PASS: {n} pairs, {3 * n} labels, calib {c['calib']} / eval {c['eval']}, class and language quotas, challenge quotas,")
print("      no family or real node across the split, projections, blind-review agreement on kept pairs, hashes.")
print("LIMIT: AI labels by one vendor; most positives pair a real node with an authored or translated statement.")
print("No model inference was performed by this validator.")
