"""AI 후보의 검수 이력, 출처별 사용 범위와 개발 비교 입력을 동결한다."""
from __future__ import annotations

import argparse
import copy
import json
import random
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import ai_candidates as A, data_contracts as D, intervention as I
from .pins import PREDICATES, QUESTIONS, render_state

SCHEMA = "vinc.laya.ai-curation-preparation.v1"
DATA = Path(__file__).resolve().parents[1] / "evalsets"
REGISTRY = DATA / "laya-ai-curation-v1/inputs.json"


def read(path):
    return json.loads(Path(path).read_bytes())


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def cross_audit(train, dev):
    def keys(rows, kind):
        if kind == "endpoint":
            return {normalized(r[k]) for r in rows for k in ("a", "b")}
        if kind in ("origin_uri", "source_sha256"):
            return {e[kind] for r in rows for e in r["evidence"]}
        return {r[kind] for r in rows}
    overlaps = {k: sorted(keys(train, k) & keys(dev, k)) for k in
                ("pair_id", "family_id", "endpoint", "origin_uri", "source_sha256")}
    # 어휘 유사도는 검토 후보일 뿐 의미적 독립성을 입증하지 않는다.
    def grams(r):
        text = normalized(r["a"] + " " + r["b"])
        return {text[i:i+5] for i in range(max(1, len(text)-4))}
    near = []
    left, right = [(r, grams(r)) for r in train], [(r, grams(r)) for r in dev]
    for a, x in left:
        for b, y in right:
            score = len(x & y) / len(x | y) if x | y else 1.0
            if score >= .8:
                near.append({"train": a["pair_id"], "dev": b["pair_id"], "jaccard5": score})
    return {"exact_overlaps": overlaps, "near_pairs": near, "threshold": .8,
            "semantic_cross_language_independence_proven": False}


def validate_dispositions(rows, notes):
    expected = {r["pair_id"]: r for r in rows if not r["hard_target_candidate"]}
    D.require(len(notes) == len(expected) and {n["pair_id"] for n in notes} == set(expected),
              "complete unique unknown-case dispositions required")
    for n in notes:
        r = expected[n["pair_id"]]
        D.require(n["action"] == "retain_exclusion" and n["labels"] == r["labels"],
                  "this preparation cannot silently repair labels")
        D.require(n["pair_sha256"] == r["pair_sha256"] and n["evidence_sha256"] == r["evidence_sha256"]
                  and n["reviews"] == r["self_review_event_id"], "stale disposition binding")
        D.require(n["actor"] == "codex:oc-20261015-vin511-1:primary" and n["independent"] is False,
                  "curation provenance changed")
        D.require(D.nonempty(n["reason"]) and n["rule"] in ("scope", "condition", "partial_claim", "evidence_boundary"),
                  "case-specific disposition required")
        D.require(D.stamp("2026-10-15T00:00:00Z") <= D.stamp(n["at"]) <= datetime.now(timezone.utc),
                  "curation timestamp outside current run")


def select(rows, eligibility):
    D.require(len({r["pair_id"] for r in rows}) == len(rows), "duplicate candidate")
    expected = {e["origin_uri"] for r in rows for e in r["evidence"]}
    D.require(len(eligibility) == len(expected) and {e["origin_uri"] for e in eligibility} == expected,
              "complete unique source dispositions required")
    by_source = {e["origin_uri"]: e for e in eligibility}
    train, dev, excluded = [], [], []
    for r in rows:
        D.require(r["label_tier"] == A.TIER and r["training_authorized"] is False
                  and r["human_review"] is False and r["independent_review"] is False, "candidate authority changed")
        D.require(r["cohort"] in A.G.COHORTS and r["cohort_role"] ==
                  ("dev" if r["cohort"] == "development" else "train-candidate"), "cohort role changed")
        fully_defined = all(type(r["labels"].get(p)) is int and r["labels"][p] in (0, 1) for p in PREDICATES)
        D.require(r["hard_target_candidate"] == (fully_defined and r["evidence_strength"] != "ambiguous"),
                  "hard-target flag differs from labels")
        eligible = True
        for e in r["evidence"]:
            s = by_source[e["origin_uri"]]
            D.require(s["source_sha256"] == e["source_sha256"] and s["training_execution_authorized"] is False
                      and s["production_authorized"] is False and s["prior_training_status"] == "pending",
                      "source binding or authority changed")
            D.require(s["scope"] == "local_development_preparation" and s["rubric_id"] == r["rubric_id"]
                      and D.nonempty(s["basis"]) and D.nonempty(s["rights_ref"]), "source scope or rubric changed")
            eligible &= s["selected_for_preparation"] is True
        if fully_defined and r["hard_target_candidate"] and eligible:
            (dev if r["cohort"] == "development" else train).append(copy.deepcopy(r))
        else:
            excluded.append(r["pair_id"])
    audit = cross_audit(train, dev)
    D.require(not any(audit["exact_overlaps"].values()), "train/development source or identity crossing")
    return train, dev, excluded, audit


def counts(rows):
    return {"pairs": len(rows), "families": len({r["family_id"] for r in rows}),
            "language_cells": dict(sorted(Counter(r["language_cell"] for r in rows).items())),
            "labels_by_language": {lang: {p: dict(sorted(Counter(str(r["labels"][p]) for r in rows
                if r["language_cell"] == lang).items())) for p in PREDICATES}
                for lang in sorted({r["language_cell"] for r in rows})}}


def comparison_draws(core, train, seed=20261007):
    baseline, receipt = I.balanced_rows(core, [], "core", seed)
    alternative = copy.deepcopy(baseline)
    replacements = []
    for y in (0, 1):
        candidates = sorted([r for r in train if r["labels"]["supports"] == y], key=lambda r: r["pair_id"])
        D.require(candidates, "supports class absent")
        # 두 팔의 동일한 위치만 교체한다. 다른 술어와 나머지 core 항목은 그대로 둔다.
        positions = [i for i, r in enumerate(baseline) if "supports" in r["gold"]
                     and r["gold"]["supports"]["probabilities"]["true"] == y][:126]
        D.require(len(positions) == 126, "insufficient matched core replacement slots")
        rng, draws = random.Random(f"{seed}:curated-supports:{y}"), []
        while len(draws) < len(positions):
            cycle = list(candidates)
            rng.shuffle(cycle)
            draws.extend(cycle[:len(positions)-len(draws)])
        for position, row in zip(positions, draws):
            # 계획 파일은 학습기의 입력 형식이 아니다. 실행 승인은 별도 실행 패키지가 검사한다.
            alternative[position] = {"curated_pair_id": row["pair_id"], "pair_sha256": row["pair_sha256"],
                                     "task": "supports", "direction": "AB", "target": y,
                                     "label_tier": A.TIER, "training_authorized": False}
            replacements.append({"position": position, **alternative[position]})
    return {"core_exposure": receipt, "control_sha256": D.fingerprint(baseline),
            "intervention_plan_sha256": D.fingerprint(alternative), "replacement_items": len(replacements),
            "unique_replacement_pairs": len({r["curated_pair_id"] for r in replacements}),
            "unchanged_items": len(baseline)-len(replacements), "replacements": sorted(replacements, key=lambda r:r["position"])}


def draft_inputs(core, train, dev):
    control, _ = I.balanced_rows(core, [], "core", 20261007)
    plan = comparison_draws(core, train)
    intervention, by_id = copy.deepcopy(control), {r["pair_id"]:r for r in train}
    def render(r, tasks, reverse=False):
        D.require(len(r["context"].strip()) <= 400, "context would be silently truncated")
        a, b = (r["b"],r["a"]) if reverse else (r["a"],r["b"])
        return {"state":render_state(a,b,r["context"]), "questions":{p:QUESTIONS[p] for p in tasks},
                "gold":{p:{"probabilities":{"false":float(1-r["labels"][p]),"true":float(r["labels"][p])}} for p in tasks},
                "meta":{"pair_id":r["pair_id"],"family_id":r["family_id"],"language_cell":r["language_cell"],
                        "direction":"ba" if reverse else "ab", "label_tier":A.TIER,"training_authorized":False}}
    for item in plan["replacements"]:
        intervention[item["position"]] = render(by_id[item["curated_pair_id"]],["supports"])
    development = [render(r,tasks,reverse) for r in dev
                   for tasks,reverse in [(PREDICATES,False),(("contradicts","same_as"),True)]]
    return {"schema":SCHEMA, "training_execution_authorized":False,
            "control":control,"intervention":intervention,"development":development}


def build(prepared, freeze, registry=REGISTRY):
    policy = read(registry)
    D.require(policy["schema"] == SCHEMA and policy["training_execution_authorized"] is False,
              "preparation-only registry required")
    root = Path(registry).parent
    D.require(set(policy["files"]) == {"unknown-dispositions.json", "source-dispositions.json", "rights-receipts.json"},
              "curation input inventory changed")
    for name, sha in policy["files"].items():
        path = (root / name).resolve()
        D.require(path.is_relative_to(root.resolve()) and D.digest(path.read_bytes()) == sha, "registered input changed")
    A.verify(prepared, freeze)
    D.require(D.digest((Path(freeze)/"manifest.json").read_bytes()) == policy["parent_manifest_sha256"],
              "parent AI freeze changed")
    rows = [r for cohort in A.G.COHORTS for r in A.read_lines(Path(freeze)/(cohort+".jsonl"))]
    notes, sources = read(root/"unknown-dispositions.json"), read(root/"source-dispositions.json")
    for pin in read(root/"rights-receipts.json")["pins"]:
        if "path" in pin:
            D.require(D.digest((DATA/pin["source_set"]/pin["path"]).read_bytes()) == pin["sha256"],
                      "pinned source notice changed")
        else:
            registered = read(DATA/pin["source_set"]/"source-freeze.json")["files"]
            D.require({k:v for k,v in pin.items() if k != "source_set"} in registered,
                      "native rights receipt differs from source freeze")
    validate_dispositions(rows, notes)
    train, dev, excluded, audit = select(rows, sources)
    core_path = DATA/"laya-predicates-community-supports-v2/training.jsonl"
    D.require(D.digest(core_path.read_bytes()) == policy["core_training_sha256"], "historical core input changed")
    draws = comparison_draws(A.read_lines(core_path), train)
    selection = {"train": [{"pair_id":r["pair_id"], "sha256":D.fingerprint(r)} for r in train],
                 "development": [{"pair_id":r["pair_id"], "sha256":D.fingerprint(r)} for r in dev], "excluded": excluded}
    return {"schema": SCHEMA, "registry_sha256": D.digest(Path(registry).read_bytes()),
            "parent_manifest_sha256": policy["parent_manifest_sha256"], "selection": selection,
            "counts": {"train":counts(train), "development":counts(dev)}, "unknown_reviewed": len(notes),
            "unknown_labels_changed": 0, "source_dispositions": len(sources), "audit":audit, "comparison":draws,
            "training_execution_authorized":False, "production_authorized":False,
            "independent_semantic_review":False, "new_model_calls":0,
            "exposure": "AI authored and repeatedly inspected; development only, not sealed or independent gold"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare", "verify"))
    p.add_argument("--prepared", required=True, type=Path)
    p.add_argument("--freeze", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    result = build(args.prepared, args.freeze)
    if args.action == "prepare":
        D.require(not args.out.exists(), "output already exists")
        D.require(not args.out.resolve().is_relative_to(args.freeze.resolve())
                  and not args.out.resolve().is_relative_to(args.prepared.resolve()), "output would alter previous freeze")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(D.encoded(result))
    else:
        D.require(args.out.read_bytes() == D.encoded(result), "curation replay changed")
    print(json.dumps({"verified":True, "counts":result["counts"], "unknown_reviewed":result["unknown_reviewed"],
                      "sha256":D.fingerprint(result), "near_pairs":len(result["audit"]["near_pairs"])}))


if __name__ == "__main__":
    main()
