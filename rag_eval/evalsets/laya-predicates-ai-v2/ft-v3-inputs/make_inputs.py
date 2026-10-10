"""Freeze the inputs of the third Laya fine-tuning round (VIN-421). No model is loaded.

    python make_inputs.py            write the files and the training set (refuses to overwrite)
    python make_inputs.py --check    rebuild everything in memory and a scratch directory and compare with disk

Selection never uses random(): candidates are ordered by sha256 of a fixed tag and their id, so the same
files come out on every Python version. Plan and reasons: graph doc vinc.plan.laya-finetune-v3-2026-10-10.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETS = HERE.parent.parent
ROOT = SETS.parent.parent
V2, V1, COMMUNITY = SETS / "laya-predicates-ai-v2", SETS / "laya-predicates-ai-v1", SETS / "laya-predicates-community-v1"
BASE = V1 / "run-2026-10-09-ft-v2" / "train" / "train-v2.jsonl"
SEED = 20261007
HOLD = {"supports": 8, "contradicts": 8, "same_as": 8, "none": 16}      # per language: 40 pairs
COMMUNITY_TAKE = {("physics",): 50, ("math",): 50, ("math", "physics"): 20}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump(rows):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def order(tag, key):
    return hashlib.sha256(f"{SEED}:{tag}:{key}".encode("utf-8")).hexdigest()


def holdout():
    gold = read(V2 / "gold.jsonl")
    split = json.loads((V2 / "gold.split.json").read_text(encoding="utf-8"))["split"]
    picked = []
    for lang in ("en", "ko", "mixed"):
        for cls, take in HOLD.items():
            ids = sorted((g["pair_id"] for g in gold if split[g["pair_id"]] == "calib" and g["lang"] == lang
                          and g["label_class"] == cls), key=lambda i: order("holdout", i))
            assert len(ids) > take, (lang, cls, len(ids))
            picked += ids[:take]
    return sorted(picked), [g for g in gold if g["pair_id"] in set(picked)]


def community_negatives():
    rows = read(COMMUNITY / "train-negatives.jsonl")
    out = []
    for domains, take in COMMUNITY_TAKE.items():
        pool = sorted((g for g in rows if tuple(g["provenance"]["domains"]) == domains),
                      key=lambda g: order("community", g["a"]["id"] + "|" + g["b"]["id"]))
        out += pool[:take]
    return out


def audit():
    """Split the audited pairs by connected component of shared nodes, so that no node is on both sides."""
    rows = sorted(read(V2 / "fill-same-as-audit.jsonl"), key=lambda r: r["sample_rank"])
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        parent[find(r["a"]["id"])] = find(r["b"]["id"])
    comps = {}
    for r in rows:
        comps.setdefault(find(r["a"]["id"]), []).append(r)
    train, diag = [], []
    for comp in sorted(comps.values(), key=lambda c: c[0]["sample_rank"]):
        # a component with a true same_as pair cannot be a negative, so it is measured
        side = diag if any(r["labels"]["same_as"] for r in comp) or len(diag) < len(train) else train
        side += comp
    negatives = [{"a": r["a"], "b": r["b"], "lang": r["lang"], "source": "fill-audit-negative",
                  "labels": {"supports": None, "contradicts": None, "same_as": 0},
                  "provenance": {"rule": "audited-model-accepted-pair", "audit_pair_id": r["pair_id"],
                                 "label_basis": "read once by the set author; the v2 checkpoint accepted the pair as same_as"}}
                 for r in train]
    gold = [{"pair_id": r["pair_id"], "a": r["a"], "b": r["b"], "context": None, "lang": r["lang"], "source": "fill-audit",
             "labels": r["labels"], "hard_kind": "model_accepted_same_as", "author": r["author"],
             "labeled_at": r["labeled_at"], "note": r["note"]} for r in diag]
    return negatives, gold


def files():
    ids, held = holdout()
    audit_neg, audit_gold = audit()
    out = {
        "holdout-v3.json": (json.dumps({"seed": SEED, "method": "per language 8 supports, 8 contradicts, 8 same_as and 16 none, "
                                        "ordered by sha256 of seed:holdout:pair_id", "holdout": ids}, indent=1) + "\n").encode(),
        "holdout/gold.jsonl": dump(held),
        "holdout/gold.split.json": (json.dumps({"split": {i: "calib" for i in ids}}, indent=1) + "\n").encode(),
        "same-as-negatives-v3.jsonl": dump(community_negatives() + audit_neg),
        "audit-diagnostic/gold.jsonl": dump(audit_gold),
        "audit-diagnostic/gold.split.json": (json.dumps({"split": {g["pair_id"]: "eval" for g in audit_gold}}, indent=1) + "\n").encode(),
    }
    return out


BUILD = ["--gold-dir", V2, "--holdout", HERE / "holdout-v3.json", "--train-gold-dir", V1,
         "--block-gold-dir", COMMUNITY, "--block-gold-dir", HERE / "audit-diagnostic",
         "--same-as-negatives", HERE / "same-as-negatives-v3.jsonl", "--base-training", BASE,
         "--candidates", "unused", "--typed-decisions", "unused"]


def build_train(out):
    sys.path.insert(0, str(ROOT))
    import contextlib
    import io
    from rag_eval.laya import build_train as bt
    import os
    os.chdir(ROOT)  # 요약에 적히는 경로가 기계마다 달라지지 않게 리포 루트 기준 상대 경로로 부른다

    def rel(a):
        return os.path.relpath(a, ROOT).replace(os.sep, "/") if isinstance(a, Path) else a
    with contextlib.redirect_stdout(io.StringIO()):
        assert bt.main([rel(a) for a in BUILD] + ["--out", rel(out) if HERE in out.parents else str(out)]) == 0
    return json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))


def manifest(summary):
    names = sorted(files()) + ["train/train-v3.jsonl"]
    return {
        "round": "laya-multilingual-vinc-ft-v3", "issue": "VIN-421", "plan": "vinc.plan.laya-finetune-v3-2026-10-10",
        "inputs_sha256": {"laya-predicates-ai-v2/gold.jsonl": sha((V2 / "gold.jsonl").read_bytes()),
                          "laya-predicates-ai-v2/fill-same-as-audit.jsonl": sha((V2 / "fill-same-as-audit.jsonl").read_bytes()),
                          "laya-predicates-ai-v1/gold.jsonl": sha((V1 / "gold.jsonl").read_bytes()),
                          "laya-predicates-community-v1/gold.jsonl": sha((COMMUNITY / "gold.jsonl").read_bytes()),
                          "laya-predicates-community-v1/train-negatives.jsonl": sha((COMMUNITY / "train-negatives.jsonl").read_bytes()),
                          "train-v2.jsonl": sha(BASE.read_bytes())},
        "sha256": {n: sha((HERE / n).read_bytes()) for n in names},
        "training": {"rows": summary["rows"], "counts": summary["counts"], "excluded": summary["excluded"]},
    }


def main():
    built = files()
    if "--check" in sys.argv[1:]:
        for name, data in built.items():
            assert (HERE / name).read_bytes() == data, "differs: " + name
        with tempfile.TemporaryDirectory() as tmp:
            summary = build_train(Path(tmp) / "train-v3.jsonl")
            assert summary["sha256"] == sha((HERE / "train" / "train-v3.jsonl").read_bytes()), "training set differs"
        want = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
        assert manifest(summary) == want, "manifest differs"
        print(f"PASS: holdout {len(json.loads(built['holdout-v3.json'])['holdout'])} pairs, training set {summary['rows']} rows "
              f"sha256 {summary['sha256']}, {len(want['sha256'])} files match the manifest. No model was loaded.")
        return 0
    for name, data in built.items():
        path = HERE / name
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    summary = build_train(HERE / "train" / "train-v3.jsonl")
    (HERE / "manifest.json").write_text(json.dumps(manifest(summary), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
