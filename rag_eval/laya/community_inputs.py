"""Freeze cross-vendor agreed community inputs without loading a model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .build_train import rows_for_pair
from .pins import PREDICATES, questions_hash

SETS = {
    "train": "laya-predicates-community-train-v1",
    "calib": "laya-predicates-community-calib-v1",
    "eval": "laya-predicates-community-v1",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode()


def jsonl(rows) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()


def footprints(pairs):
    ids, texts = set(), set()
    for p in pairs:
        for side in ("a", "b"):
            ids.add(p[side]["id"])
            texts.add(p[side]["text"].strip())
            nid = p.get("provenance", {}).get(side, {}).get("node_id")
            if nid:
                ids.add(nid)
        if p.get("context"):
            texts.add(p["context"].strip())
    return ids, texts


def assert_disjoint(groups):
    names = list(groups)
    for i, name in enumerate(names):
        ids, texts = footprints(groups[name])
        for other in names[i + 1:]:
            oi, ot = footprints(groups[other])
            if ids & oi or texts & ot:
                raise ValueError(f"overlap between {name} and {other}: ids={len(ids & oi)}, texts={len(texts & ot)}")


def assemble(root: Path):
    groups, sources = {}, {}
    for split, name in SETS.items():
        directory = root / name
        gold_bytes = (directory / "gold.jsonl").read_bytes()
        agreed_bytes = (directory / "crossvendor-agreed.json").read_bytes()
        agreed = json.loads(agreed_bytes)
        if sha(gold_bytes) != agreed["gold_sha256"]:
            raise ValueError(f"gold digest mismatch: {name}")
        pairs = [json.loads(line) for line in gold_bytes.decode().splitlines() if line]
        by_id = {p["pair_id"]: p for p in pairs}
        keep = agreed["agreed"]
        drop = agreed["disagreed"]
        if len(by_id) != len(pairs) or len(set(keep)) != len(keep) or set(keep) & set(drop) or set(keep) | set(drop) != set(by_id):
            raise ValueError(f"invalid agreement partition: {name}")
        selected = [p for p in pairs if p["pair_id"] in set(keep)]
        if any(set(p["labels"]) != set(PREDICATES) or any(p["labels"][k] not in (0, 1) for k in PREDICATES) for p in selected):
            raise ValueError(f"invalid labels: {name}")
        groups[split] = selected
        sources[split] = {"dataset": name, "gold_sha256": sha(gold_bytes), "agreed_sha256": sha(agreed_bytes), "pairs": len(selected), "excluded": drop}
    assert_disjoint(groups)
    rows = []
    for p in groups["train"]:
        new = rows_for_pair(p["a"]["text"], p["b"]["text"], p.get("context"), p["labels"], "community-crossvendor-agreed", p["lang"])
        for row in new:
            row["meta"]["pair_id"] = p["pair_id"]
        rows.extend(new)
    files = {"training.jsonl": jsonl(rows)}
    for split in ("train", "calib", "eval"):
        files[f"{split}.jsonl"] = jsonl(groups[split])
        if split != "train":
            files[f"{split}.split.json"] = encoded({"split": {p["pair_id"]: split for p in groups[split]}})
    files["manifest.json"] = encoded({"sources": sources, "questions_hash": questions_hash(), "training_rows": len(rows), "training_items": sum(len(r["gold"]) for r in rows), "cross_split_id_overlap": 0, "cross_split_text_overlap": 0, "files": {name: sha(data) for name, data in files.items()}})
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = assemble(args.root)
    if args.check:
        for name, data in files.items():
            if (args.out / name).read_bytes() != data:
                raise ValueError(f"frozen input differs: {name}")
    else:
        if args.out.exists():
            raise FileExistsError(args.out)
        args.out.mkdir(parents=True)
        for name, data in files.items():
            (args.out / name).write_bytes(data)
    print(f"{'verified' if args.check else 'wrote'} {len(files)} input files")


if __name__ == "__main__":
    main()
