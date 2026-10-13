"""후보 문맥의 수정 계보와 별도 검토 묶음을 만든다. 라벨이나 학습 승인은 만들지 않는다."""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from pathlib import Path

from . import candidate_revision as R, data_contracts as D, human_review as H, pilot_verify as V
from .pins import PREDICATES, QUESTIONS, questions_hash, render_state

SCHEMA = "vinc.laya.candidate-context.v1"


def lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def text_hash(text):
    return D.digest(text.encode("utf-8"))


def context_hash(context):
    # VIN-475의 원문 투영과 같은 직렬화다. 새 정규화로 원본 해시를 바꾸지 않는다.
    return text_hash(json.dumps(context, ensure_ascii=False, sort_keys=True))


def native_candidates(inputs, audit, plan):
    D.require(plan["schema"] == SCHEMA and plan["training_authorized"] is False,
              "unknown context plan or training authorization")
    D.require(D.digest(inputs.read_bytes()) == plan["inputs_sha256"], "native input hash mismatch")
    D.require(D.digest(audit.read_bytes()) == plan["audit_sha256"], "native audit hash mismatch")
    D.stamp(plan["authored_at"])
    rows, reviews = lines(inputs), lines(audit)
    by_id, review_by_id = ({r["candidate_id"]: r for r in rs} for rs in (rows, reviews))
    D.require(len(by_id) == len(rows) == 60 and set(by_id) == set(review_by_id), "native review identities mismatch")
    priority = {r["candidate_id"] for r in reviews if r["review_route"] != "native_fact_or_restatement"}
    specs = plan["repairs"]
    D.require(len(specs) == len(priority) == 33 and {r["parent_id"] for r in specs} == priority,
              "all thirty-three priority dispositions required")
    result, changes = [], []
    for spec in specs:
        D.require(set(spec) == {"parent_id", "disposition", "sentence_indices", "claim", "context", "reason"},
                  "unexpected repair fields")
        parent, review = by_id[spec["parent_id"]], review_by_id[spec["parent_id"]]
        D.require(context_hash(parent["source_context"]) == parent["source_context_sha256"], "source context hash mismatch")
        D.require(parent["language"] == "en_en" and not parent["annotation_events"]
                  and all(v is None for v in parent["predicate_labels"].values()), "parent label authority changed")
        D.require(review["source_context_sha256"] == parent["source_context_sha256"]
                  and all(v is None for v in review["predicate_labels"].values())
                  and review["training_export_approved"] is False and review["human_review"] is False,
                  "audit scope or authority changed")
        D.require(spec["reason"].strip() and spec["disposition"] in ("authored_candidate", "deferred"),
                  "disposition or reason missing")
        change = {"parent_id": parent["candidate_id"], "parent_sha256": D.fingerprint(parent),
                  "source_context_sha256": parent["source_context_sha256"], "source": parent["source"],
                  "original_evidence_sha256": text_hash(parent["evidence"]),
                  "original_claim_sha256": text_hash(parent["claim"]), "disposition": spec["disposition"],
                  "reason": spec["reason"], "predicate_gold": False, "original_claim_adjudicated": False}
        if spec["disposition"] == "deferred":
            D.require(spec["claim"] is None and spec["context"] is None and spec["sentence_indices"] == [],
                      "deferred case must not contain invented repair")
            changes.append(change)
            continue
        D.require(isinstance(spec["claim"], str) and spec["claim"].strip()
                  and isinstance(spec["context"], str) and 0 < len(spec["context"]) <= 400,
                  "nonempty claim and untruncated context required")
        refs = []
        if parent["source"] == "scifact":
            abstract = parent["source_context"]["abstract"]
            indices = spec["sentence_indices"]
            D.require(indices and len(set(indices)) == len(indices)
                      and all(type(i) is int and 0 <= i < len(abstract) for i in indices), "invalid abstract citation")
            full = "\n".join(abstract)
            for i in indices:
                start = sum(len(t) + 1 for t in abstract[:i])
                refs.append({"source_projection": "scifact-abstract-sentences-v1", "sentence_index": i,
                             "start": start, "end": start + len(abstract[i]), "text": abstract[i],
                             "excerpt_sha256": text_hash(abstract[i]), "projection_sha256": text_hash(full)})
            evidence = "\n".join(abstract[i] for i in indices)
        else:
            raw = parent["source_context"]["raw_source_row"]
            D.require(parent["source"] == "vitaminc" and spec["sentence_indices"] == []
                      and raw["evidence"] == parent["evidence"] and raw["claim"] == parent["claim"],
                      "native revision source mismatch")
            evidence = raw["evidence"]
            refs.append({"source_projection": "vitaminc-saved-evidence-v1", "start": 0, "end": len(evidence),
                         "text": evidence, "excerpt_sha256": text_hash(evidence), "projection_sha256": text_hash(evidence)})
        pid = "candidate:native-context-v1:" + D.fingerprint({"parent": parent["candidate_id"], "spec": spec})[:24]
        locator = copy.deepcopy(parent["locator"])
        if parent["source"] == "scifact":
            locator.pop("claim_id", None)
            locator.update(member="data/corpus.jsonl", sentence_indices=spec["sentence_indices"])
        pair = {"pair_id": pid, "parents": [parent["candidate_id"]], "revision": "1", "role": "train-candidate",
                "family_id": parent["independence_group"], "language": "en_en", "source": parent["source"],
                "locator": locator, "parent_locator": copy.deepcopy(parent["locator"]),
                "source_context_sha256": parent["source_context_sha256"],
                "a": {"text": evidence, "language": "en", "mode": "quoted_exact"},
                "b": {"text": spec["claim"], "language": "en", "mode": "authored"},
                "context": spec["context"], "evidence": refs, "authored_at": plan["authored_at"],
                "author_kind": "ai", "exact_served_model": None,
                "predicate_labels": dict.fromkeys(PREDICATES), "annotation_events": [], "training_authorized": False,
                "render_sha256": text_hash(render_state(evidence, spec["claim"], spec["context"]))}
        result.append(pair)
        changes.append({**change, "new_pair_id": pid, "new_pair_sha256": D.fingerprint(pair),
                        "new_evidence_sha256": text_hash(evidence), "new_claim_sha256": text_hash(spec["claim"]),
                        "new_context_sha256": text_hash(spec["context"]),
                        "changed_evidence": evidence != parent["evidence"], "changed_claim": spec["claim"] != parent["claim"]})
    D.require(len({p["pair_id"] for p in result}) == len(result)
              and len({p["family_id"] for p in result}) == len(result), "duplicate native candidate or family")
    return {"schema": SCHEMA, "dataset_id": "candidate:native-context-v1", "pairs": result,
            "dispositions": changes, "annotations": [], "training_authorized": False,
            "questions": QUESTIONS, "questions_hash": questions_hash()}


def native_queue(bundle, rubric):
    return [{"pair_id": p["pair_id"], "pair_sha256": D.fingerprint(p), "a": p["a"]["text"], "b": p["b"]["text"],
             "context": p["context"], "evidence": [{**r, "source_sha256": r["projection_sha256"],
                        "source_context_sha256": p["source_context_sha256"], "source_locator": p["locator"],
                        "origin_uri": "frozen-source:" + p["source"] + ":" + str(p["locator"].get("doc_id", p["locator"].get("unique_id")))
                                      + ":" + r["source_projection"]} for r in p["evidence"]],
             "rubric_id": rubric, "questions_hash": bundle["questions_hash"]}
            for p in sorted(bundle["pairs"], key=lambda r: r["pair_id"])]


def review_session(bundle, queue, instructions, root):
    session = H.session(bundle, queue, instructions, "author")
    D.require(session["cases"] and len(session["cases"]) == len(queue), "empty or incomplete review session")
    payload = json.dumps(session, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = Path(H.__file__).with_name("human_review.html").read_text(encoding="utf-8")
    root.mkdir(parents=True, exist_ok=False)
    (root / "session.json").write_bytes(D.encoded(session))
    (root / "review.html").write_text(template.replace("__SESSION_JSON__", payload).replace("__SESSION_HASH__", D.fingerprint(session)), encoding="utf-8")
    R.write(root, "manifest.json", {"session_sha256": D.fingerprint(session), "queue_sha256": D.fingerprint(queue),
            "cases": len(queue), "human_annotations": 0, "exportable_pairs": 0, "automatic_ingestion_supported": False,
            "files": R.inventory(root)})
    return {"cases": len(queue), "session_sha256": D.fingerprint(session),
            "language_cells": dict(Counter(p["a"]["language"] + "_" + p["b"]["language"] for p in bundle["pairs"]
                                  if p["pair_id"] in {q["pair_id"] for q in queue}))}


def build(original, revision, development, inputs, audit, plan_path, out):
    original, revision, development, inputs, audit, plan_path, out = map(Path, (original, revision, development, inputs, audit, plan_path, out))
    # 원본 및 저장된 검토판을 덮어쓰는 경로는 거절한다.
    roots = (original, revision, development, inputs.parent, audit.parent, plan_path.parent)
    D.require(not out.exists() and all(not out.resolve().is_relative_to(r.resolve()) for r in roots), "output must be new and outside frozen inputs")
    D.require(not out.resolve().is_relative_to(Path(__file__).resolve().parents[2]), "hydrated sources must stay outside repository")
    reuse = R.verify(original, revision, development)
    plan = V.read(plan_path.parent, plan_path.name)
    native = native_candidates(inputs, audit, plan)
    previous, history, dev = (V.read(p, "bundle.json") for p in (original, revision, development))
    active = R.validate_selection(previous, history, V.read(revision, "selection.json"))
    # 전체 문장 일치는 새 자료의 충돌만 검사한다. 재사용 후보의 이미 알려진 계보를 새 누출로 세지 않는다.
    dev_text = {D.normalized(p[s]["text"]) for p in dev["pairs"] for s in ("a", "b")}
    D.require(not dev_text & {D.normalized(p[s]["text"]) for p in native["pairs"] for s in ("a", "b")}, "native endpoint crosses development")
    out.mkdir(parents=True)
    R.write(out, "native-bundle.json", native)
    R.write(out, "before-after.json", native["dispositions"])
    instructions = V.read(original, "review/instructions.json")
    views = {}
    for name, bundle, rows, source_root in (("pilot", history, active, original), ("development", dev, dev["pairs"], development)):
        queue = R.hydrated_queue(bundle, rows, source_root)
        views[name] = review_session(bundle, queue, instructions, out / name)
    native["rubric"] = copy.deepcopy(history["rubric"])
    views["native"] = review_session(native, native_queue(native, history["rubric"]["id"]), instructions, out / "native")
    receipt = {"schema": SCHEMA, "reuse": reuse, "inputs_sha256": D.digest(inputs.read_bytes()),
               "audit_sha256": D.digest(audit.read_bytes()), "plan_sha256": D.digest(plan_path.read_bytes()),
               "native_bundle_sha256": D.fingerprint(V.read(out, "native-bundle.json")),
               "priorities": len(native["dispositions"]), "authored_candidates": len(native["pairs"]),
               "deferred": sum(r["disposition"] == "deferred" for r in native["dispositions"]),
               "review_groups": views, "predicate_gold": 0, "human_reviews": 0, "training_exportable_pairs": 0,
               "model_calls": 0, "existing_sessions_rebased": False, "automatic_ingestion_supported": False,
               "source_eligibility_inherited_not_promoted": True, "semantic_independence_proven": False,
               "files": R.inventory(out)}
    R.write(out, "manifest.json", receipt)
    return receipt


def verify(out):
    out = Path(out)
    receipt = V.read(out, "manifest.json")
    D.require(receipt["schema"] == SCHEMA and receipt["files"] == R.inventory(out), "output inventory/hash changed")
    D.require(all(receipt[k] == 0 for k in ("predicate_gold", "human_reviews", "training_exportable_pairs", "model_calls"))
              and receipt["existing_sessions_rebased"] is False and receipt["automatic_ingestion_supported"] is False,
              "review authority changed")
    native = V.read(out, "native-bundle.json")
    D.require(native["training_authorized"] is False and not native["annotations"], "native labels or training authorization")
    D.require(len(native["dispositions"]) == receipt["priorities"] == 33
              and native["dispositions"] == V.read(out, "before-after.json"), "disposition receipt mismatch")
    D.require(len(native["pairs"]) == receipt["authored_candidates"]
              and D.fingerprint(native) == receipt["native_bundle_sha256"], "native bundle receipt mismatch")
    for pair in native["pairs"]:
        D.require(not pair["annotation_events"] and pair["training_authorized"] is False
                  and all(v is None for v in pair["predicate_labels"].values()), "invented native labels")
    for name, expected in receipt["review_groups"].items():
        session, manifest = (V.read(out / name, p) for p in ("session.json", "manifest.json"))
        D.require(D.fingerprint(session) == expected["session_sha256"] == manifest["session_sha256"]
                  and len(session["cases"]) == expected["cases"] == manifest["cases"], "session receipt mismatch")
        D.require(manifest["files"] == R.inventory(out / name) and session["stage"] == "author", "review session changed")
        D.require(all(not c["reviews"] and c["author_actor"] is None and "prior_judgments" not in c
                      and not ({"contrast", "source_native_label", "predicate_labels", "rationale"} & c.keys())
                      for c in session["cases"]), "session exposes prior labels or author intent")
    return {"verified": True, "priorities": receipt["priorities"], "authored_candidates": receipt["authored_candidates"],
            "deferred": receipt["deferred"], "review_groups": receipt["review_groups"], "training_exportable_pairs": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    for name in ("original", "revision", "development", "inputs", "audit", "plan"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    supplied = [getattr(args, n) for n in ("original", "revision", "development", "inputs", "audit", "plan")]
    try:
        D.require(not any(supplied) if args.verify else all(supplied), "build needs all inputs; verify accepts only output")
        result = verify(args.out) if args.verify else build(*supplied, args.out)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
