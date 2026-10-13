"""Freeze reviewed public inputs with equal predicate item counts."""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from .build_train import rows_for_pair
from .community_inputs import assert_disjoint, encoded, footprints, jsonl, sha
from .finetune import update_budget
from .pins import PREDICATES, QUESTIONS, questions_hash

SETS = {
    "train": ["laya-predicates-community-train-v1"],
    "calib": ["laya-predicates-community-calib-v1", "laya-predicates-community-calib-v2"],
    "eval": ["laya-predicates-community-v2"],
    "dev": ["laya-predicates-community-v1"],
}


def load_agreed(directory):
    gold = (directory / "gold.jsonl").read_bytes()
    agreement = (directory / "crossvendor-agreed.json").read_bytes()
    review = json.loads(agreement)
    if sha(gold) != review["gold_sha256"]:
        raise ValueError(f"gold digest mismatch: {directory.name}")
    pairs = [json.loads(line) for line in gold.decode().splitlines() if line]
    ids = [p["pair_id"] for p in pairs]
    keep, drop = review["agreed"], review["disagreed"]
    if (len(set(ids)) != len(ids) or len(set(keep)) != len(keep)
            or len(set(drop)) != len(drop) or set(keep) & set(drop)
            or set(keep) | set(drop) != set(ids)):
        raise ValueError(f"invalid agreement partition: {directory.name}")
    selected = [p for p in pairs if p["pair_id"] in set(keep)]
    if any(set(p["labels"]) != set(PREDICATES)
           or any(type(p["labels"][k]) is not int or p["labels"][k] not in (0, 1)
                  for k in PREDICATES) for p in selected):
        raise ValueError(f"invalid labels: {directory.name}")
    return selected, {"dataset": directory.name, "gold_sha256": sha(gold),
                      "agreed_sha256": sha(agreement), "pairs": len(selected), "excluded": drop}


def assert_roles(groups):
    assert_disjoint({k: groups[k] for k in ("train", "calib", "eval")})
    assert_disjoint({k: groups[k] for k in ("train", "calib", "dev")})
    # 개발·측정은 팩 노드를 공유하지만 문장과 fixture id 는 공유할 수 없다.
    dev_ids = {p[s]["id"] for p in groups["dev"] for s in ("a", "b")}
    eval_ids = {p[s]["id"] for p in groups["eval"] for s in ("a", "b")}
    if dev_ids & eval_ids or footprints(groups["dev"])[1] & footprints(groups["eval"])[1]:
        raise ValueError("development and measurement share fixture ids or text")
    for name, pairs in groups.items():
        if len({p["pair_id"] for p in pairs}) != len(pairs):
            raise ValueError(f"duplicate pair id within {name}")


def training_rows(pairs):
    rows = []
    for pair in pairs:
        new = rows_for_pair(pair["a"]["text"], pair["b"]["text"], pair.get("context"),
                            pair["labels"], "community-crossvendor-agreed", pair["lang"])
        for row in new:
            row["meta"].update(pair_id=pair["pair_id"], repetition=0)
        rows.extend(new)
        # supports AB 만 한 번 더 배정한다. 양성과 음성의 비율은 보존한다.
        extra = copy.deepcopy(new[0])
        extra["questions"] = {"supports": extra["questions"]["supports"]}
        extra["gold"] = {"supports": extra["gold"]["supports"]}
        extra["meta"]["repetition"] = 1
        rows.append(extra)
    return rows


def describe(pairs):
    return {lang: {"pairs": len(selected),
                   "positives": {pred: sum(p["labels"][pred] for p in selected) for pred in PREDICATES}}
            for lang in ("all", "ko", "en", "mixed")
            for selected in [[p for p in pairs if lang == "all" or p["lang"] == lang]]}


def assemble(root):
    groups, sources, question_bytes = {}, {}, None
    for split, names in SETS.items():
        groups[split], sources[split] = [], []
        for name in names:
            directory = root / name
            q = (directory / "questions.json").read_bytes()
            definition = json.loads(q)
            if definition.get("questions_hash") != questions_hash() or definition.get("questions") != QUESTIONS:
                raise ValueError(f"questions do not match harness: {name}")
            if question_bytes is not None and q != question_bytes:
                raise ValueError(f"questions differ: {name}")
            question_bytes = q
            selected, source = load_agreed(directory)
            groups[split].extend(selected)
            sources[split].append(source)
    assert_roles(groups)
    rows = training_rows(groups["train"])
    item_counts = Counter(p for r in rows for p in r["gold"])
    items = sum(item_counts.values())
    files = {"training.jsonl": jsonl(rows)}
    for split, pairs in groups.items():
        files[f"{split}.jsonl"] = jsonl(pairs)
        if split != "train":
            files[f"{split}.split.json"] = encoded({"split": {p["pair_id"]: split for p in pairs}})
    recipe = {"epochs": 8, "seed": 20261007, "micro_batch": 8, "grad_accum": 8,
              "encoder_lr": 2.5e-5, "head_lr": 1e-4, "max_len": 1024, "head_max_len": 256,
              "amp": True, "gradient_checkpointing": True, "calib_max": 0, "calib_frac": 0.0,
              "frozen": "encoder embeddings (names containing 'embeddings')", "loss": "rlcd",
              "base_revision": "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67",
              "base_model_sha256": "9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204",
              "training_sha256": sha(files["training.jsonl"]), "expected_items": items,
              "budget": update_budget(items, 8)}
    files["recipe.json"] = encoded(recipe)
    files["manifest.json"] = encoded({"sources": sources, "questions_hash": questions_hash(),
                                     "questions_file_sha256": sha(question_bytes),
                                     "training_rows": len(rows), "training_items": items,
                                     "items_by_predicate": dict(item_counts),
                                     "groups": {name: describe(pairs) for name, pairs in groups.items()},
                                     "dev_eval_shared_pack_nodes": True,
                                     "files": {name: sha(data) for name, data in files.items()}})
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
