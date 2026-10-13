"""AI 판정과 같은 에이전트의 재검토를 동결한다. 사람 검토와 학습 승인은 분리한다."""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import cohort_review as G, candidate_revision as R, data_contracts as D
from .pins import PREDICATES

SCHEMA = "vinc.laya.ai-candidate-freeze.v1"
EVENT = "vinc.laya.ai-annotation.v1"
TIER = "owner-delegated-ai-candidate-v1"
POLICY = G.DATA / "laya-ai-candidate-v1/authorization.json"


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def candidates(prepared, source_root):
    rows = []
    for cohort in G.COHORTS:
        ledger, queue, initial = G.frozen(Path(prepared), cohort, Path(source_root))
        pairs = {p["pair_id"]:p for p in ledger["pairs"]}
        rows.extend({"cohort": cohort, "cohort_role": ledger["cohort_role"], "session_sha256": D.fingerprint(initial),
                     "family_id": pairs[case["pair_id"]]["family_id"],
                     "language_cell": pairs[case["pair_id"]]["a"]["language"]+"_"+pairs[case["pair_id"]]["b"]["language"],
                     "evidence_sha256": D.fingerprint(case["evidence"]), **case} for case in queue)
    D.require(len({r["pair_id"] for r in rows}) == len(rows), "cross-cohort pair collision")
    return rows


def events(rows, cases, policy, stage, authors=None):
    expected = {c["pair_id"]: c for c in cases}
    D.require(len(rows) == len(expected) and {r["pair_id"] for r in rows} == set(expected), "complete unique cohort coverage required")
    result, now = {}, datetime.now(timezone.utc)
    fields = {"schema", "cohort", "pair_id", "pair_sha256", "evidence_sha256", "session_sha256", "rubric_id",
              "actor", "actor_kind", "model_family", "exact_served_model", "runtime", "stage", "at", "labels",
              "rationale", "evidence_strength", "reviews", "independent_review", "event_id"}
    for event in rows:
        case = expected[event["pair_id"]]
        D.require(set(event) == fields and event["schema"] == EVENT, "unsupported AI annotation schema")
        D.require(event["event_id"] == "annotation:" + D.fingerprint({k:v for k,v in event.items() if k != "event_id"}), "event hash mismatch")
        D.require(event["actor_kind"] == "ai" and event["actor"] in policy["allowed_actors"]
                  and event["independent_review"] is False, "AI provenance or independence changed")
        D.require(event["runtime"] == "Codex API" and event["model_family"] == "GPT-6"
                  and event["exact_served_model"] is None, "runtime identity differs from captured authorization")
        D.require(event["stage"] == stage, "annotation stage changed")
        D.require(all(event[k] == case[k] for k in ("cohort", "pair_sha256", "evidence_sha256", "session_sha256", "rubric_id")), "stale or cross-cohort evidence binding")
        D.require(D.labels_valid(event["labels"]), "three integer labels or null required")
        D.require(isinstance(event["rationale"], dict) and set(event["rationale"]) == set(PREDICATES)
                  and all(D.nonempty(v) for v in event["rationale"].values()), "three case rationales required")
        D.require(event["evidence_strength"] in {"direct", "inference", "ambiguous"}, "evidence strength required")
        at = D.stamp(event["at"])
        D.require(D.stamp(policy["valid_from"]) <= at <= now, "annotation time outside delegated scope")
        if authors is None:
            D.require(event["reviews"] == [], "author cannot review another event")
        else:
            prior = authors[event["pair_id"]]
            D.require(event["actor"] == prior["actor"] and event["reviews"] == [prior["event_id"]]
                      and at > D.stamp(prior["at"]), "self-review actor, reference or time changed")
        result[event["pair_id"]] = event
    return result


def evaluate(prepared, author_path, review_path, source_root=G.DATA, policy_path=POLICY):
    policy = json.loads(Path(policy_path).read_bytes())
    D.require(policy["tier"] == TIER and D.qualified(policy["decision_ref"])
              and policy["owner_delegation"] is True and policy["training_authorized"] is False
              and all(policy[k] is False for k in ("human_review_claim", "independent_review_claim", "teacher_api_authorized",
                                                  "source_permission_promotion", "model_admission_authorized")),
              "delegation tier or authority changed")
    cases = candidates(prepared, source_root)
    D.require(policy["cohort_counts"] == dict(Counter(c["cohort"] for c in cases)), "authorized cohort coverage changed")
    D.require(policy["prepared_manifest_sha256"] == D.fingerprint(json.loads((Path(prepared)/"manifest.json").read_bytes()))
              and all(c["session_sha256"] == policy["session_sha256"][c["cohort"]] for c in cases), "delegated candidate snapshot changed")
    D.require(D.digest(Path(author_path).read_bytes()) == policy["author_sha256"]
              and D.digest(Path(review_path).read_bytes()) == policy["self_review_sha256"], "registered AI annotation inputs changed")
    authors = events(read_lines(author_path), cases, policy, "author")
    reviews = events(read_lines(review_path), cases, policy, "self_review", authors)
    frozen = []
    for case in cases:
        initial, final = authors[case["pair_id"]], reviews[case["pair_id"]]
        frozen.append({**copy.deepcopy(case), "label_tier": TIER, "labels": final["labels"], "rationale": final["rationale"],
                       "evidence_strength": final["evidence_strength"], "author_event_id": initial["event_id"],
                       "self_review_event_id": final["event_id"], "human_review": False, "independent_review": False,
                       "hard_target_candidate": all(v is not None for v in final["labels"].values())
                           and final["evidence_strength"] != "ambiguous", "training_authorized": False})
    groups = {}
    for cohort in G.COHORTS:
        selected = [r for r in frozen if r["cohort"] == cohort]
        groups[cohort] = {"pairs": len(selected), "hard_target_candidates": sum(r["hard_target_candidate"] for r in selected),
                          "unknown_or_ambiguous": sum(not r["hard_target_candidate"] for r in selected),
                          "families": len({r["family_id"] for r in selected}),
                          "language_cells": dict(Counter(r["language_cell"] for r in selected)),
                          "label_counts": {p:dict(Counter("unknown" if r["labels"][p] is None else str(r["labels"][p]) for r in selected)) for p in PREDICATES}}
    report = {"schema": SCHEMA, "tier": TIER, "decision_ref": policy["decision_ref"], "cohorts": groups,
              "pairs": len(frozen), "ai_author_events": len(authors), "same_agent_review_events": len(reviews),
              "changed_label_cases": sum(authors[p]["labels"] != reviews[p]["labels"] for p in authors),
              "human_annotation_events": 0, "independent_review_events": 0, "training_exportable_pairs": 0,
              "training_authorized": False, "source_permissions_promoted": False, "rubric_promoted": False,
              "semantic_correctness_independently_verified": False, "label_from_generator_intent": False,
              "teacher_api_calls": 0, "laya_model_calls": 0, "training_runs": 0}
    return frozen, report


def freeze(prepared, author_path, review_path, out, source_root=G.DATA, policy_path=POLICY):
    frozen, report = evaluate(prepared, author_path, review_path, source_root, policy_path)
    out = G.new_output(out, prepared, source_root)
    D.require(all(not Path(p).resolve().is_relative_to(out.resolve()) for p in (author_path, review_path, policy_path)), "output contains annotation inputs")
    for path in (author_path, review_path, policy_path):
        parent = Path(path).parent
        if (parent/"manifest.json").exists() and json.loads((parent/"manifest.json").read_bytes()).get("schema") == SCHEMA:
            D.require(not out.resolve().is_relative_to(parent.resolve()), "output cannot modify previous AI freeze")
    out.mkdir(parents=True)
    for name, path in (("author.jsonl", author_path), ("self-review.jsonl", review_path), ("authorization.json", policy_path)):
        (out/name).write_bytes(Path(path).read_bytes())
    for cohort in G.COHORTS:
        rows = [r for r in frozen if r["cohort"] == cohort]
        (out/(cohort+".jsonl")).write_bytes(b"".join((json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+"\n").encode() for r in rows))
    R.write(out, "manifest.json", {**report, "files": R.inventory(out)})
    verify(prepared, out, source_root, policy_path)
    return report


def verify(prepared, out, source_root=G.DATA, policy_path=POLICY):
    out = Path(out)
    receipt = json.loads((out/"manifest.json").read_bytes())
    D.require(receipt["files"] == R.inventory(out), "AI freeze inventory/hash changed")
    D.require((out/"authorization.json").read_bytes() == Path(policy_path).read_bytes(), "saved delegation policy changed")
    frozen, report = evaluate(prepared, out/"author.jsonl", out/"self-review.jsonl", source_root, policy_path)
    D.require(receipt == {**report, "files": R.inventory(out)}, "AI freeze receipt changed")
    for cohort in G.COHORTS:
        D.require(read_lines(out/(cohort+".jsonl")) == [r for r in frozen if r["cohort"] == cohort], "AI candidate freeze differs from event replay")
    return {"verified": True, **report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--author", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--sources", type=Path, default=G.DATA)
    parser.add_argument("--policy", type=Path, default=POLICY)
    args = parser.parse_args(argv)
    try:
        if args.action == "freeze":
            D.require(args.author and args.review, "freeze requires actual AI author and self-review inputs")
            result = freeze(args.prepared,args.author,args.review,args.out,args.sources,args.policy)
        else:
            D.require(not(args.author or args.review), "verify accepts no annotation mutation arguments")
            result = verify(args.prepared,args.out,args.sources,args.policy)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))


if __name__ == "__main__":
    main()
