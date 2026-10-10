"""Offline preparation/import for VIN-425. No HTTP transport or model execution.

Protocol: [[vinc.plan.predicate-judge-comparison-preparation-2026-10-10]].
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .laya.metrics import softmax
from .laya.pins import QUESTIONS, questions_hash
from .laya.run_gold import read_jsonl, states_for

EPSILON = 1e-12
CONTRACT = Path(__file__).parent / "evalsets/predicate-judge-comparison-v1/contract.json"
CODE = ("rag_eval/judge_compare.py", "rag_eval/laya/fit_calib.py", "rag_eval/laya/report.py",
        "rag_eval/laya/pins.py", "rag_eval/laya/run_gold.py", "rag_eval/laya/metrics.py")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_rows(path, rows, provenance):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("# " + json.dumps(provenance, ensure_ascii=False) + "\n")
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def timestamp(value):
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if time.utcoffset() is None or time.utcoffset().total_seconds() != 0:
        raise ValueError("receipt timestamps must use UTC")
    return time


def number(value, minimum=0, maximum=math.inf):
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid numeric value")
    return value


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def read_journal(path):
    return [json.loads(line, object_pairs_hook=unique_object)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def payload(provider, state, predicates, model):
    if provider == "jev":
        return {"model": model, "state": state,
                "questions": {p: copy.deepcopy(QUESTIONS[p]) for p in predicates}}
    if provider == "openai_decisions":
        return {"model": model, "input": state, "questions": [
            {"type": "predicate", "name": p, "instructions": QUESTIONS[p]["instructions"]
             + "\nTrue: " + QUESTIONS[p]["criteria"]["true"]
             + "\nFalse: " + QUESTIONS[p]["criteria"]["false"]} for p in predicates]}
    raise ValueError("unknown provider")


def pairs_for(root, spec):
    gold, split = root / spec["gold"], root / spec["split"]
    if file_hash(gold) != spec["gold_sha256"] or file_hash(split) != spec["split_sha256"]:
        raise ValueError("frozen input hash mismatch")
    pairs = read_jsonl(gold)
    assignments = json.loads(split.read_text(encoding="utf-8"))
    # Existing split files contain their map under 'split'. Never make a new split.
    assignments = assignments.get("split", assignments)
    if set(assignments) != {p["pair_id"] for p in pairs}:
        raise ValueError("split membership mismatch")
    selected = [p for p in pairs if assignments[p["pair_id"]] == spec["partition"]]
    if len(selected) != spec["pairs"] or len({p["pair_id"] for p in selected}) != len(selected):
        raise ValueError("pair count or uniqueness mismatch")
    return selected


def requests_for(provider, model, set_name, pairs):
    requests = []
    for pair in pairs:
        for direction, state, predicates in states_for(pair):
            body = payload(provider, state, predicates, model)
            requests.append({"request_id": f"{set_name}:{pair['pair_id']}:{direction}",
                             "set": set_name, "pair_id": pair["pair_id"], "direction": direction,
                             "predicates": predicates, "body": body, "body_sha256": digest(body)})
    return requests


def check_contract(root, contract):
    from .laya.report import GATE
    if (contract["gates"] != GATE or contract["target_error"] != 0.05
            or contract["asymmetry_abstain"] != 0.2
            or contract["calibration"] != {"temp_min": 0.5, "temp_max": 5.0, "min_bucket_n": 30,
                                           "seed": 20261007, "mixed_uses": "en", "epsilon": EPSILON}):
        raise ValueError("registered gate or calibration recipe mismatch")
    if questions_hash() != contract["questions_hash"]:
        raise ValueError("frozen question hash mismatch")
    return {name: pairs_for(root, spec) for name, spec in contract["sets"].items()}


def probabilities(provider, response, predicates, model):
    if response.get("model") != model:
        raise ValueError("returned model differs from the frozen model")
    usage = response["usage"]
    for key in ("input_tokens", "output_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError("missing or invalid token usage")
    answers = response["answers"]
    if provider == "openai_decisions":
        if not isinstance(answers, list) or len({a.get("name") for a in answers}) != len(answers):
            raise ValueError("duplicate or malformed answers")
        answers = {a.get("name"): a for a in answers}
        kind, field = "predicate", "probability"
    elif provider == "jev":
        kind, field = "noul", "noul"
    else:
        raise ValueError("unknown provider")
    if not isinstance(answers, dict) or set(answers) != set(predicates):
        raise ValueError("missing or extra answers")
    out = {}
    for pred in predicates:
        if answers[pred].get("type") != kind:
            raise ValueError("refusal or unexpected answer type")
        out[pred] = number(answers[pred][field], 0, 1)
    return out


def normalize(provider, model, requests, receipts, pairs, calibration=None, frozen_at=None):
    expected = {r["request_id"]: r for r in requests}
    received = {r["request_id"]: r for r in receipts}
    if len(received) != len(receipts) or set(received) != set(expected):
        raise ValueError("incomplete, extra or duplicate request receipts")
    gold = {p["pair_id"]: p for p in pairs}
    rows, total_in, total_out, elapsed = [], 0, 0, []
    for rid, request in expected.items():
        receipt = received[rid]
        if receipt.get("attempt") != 1 or type(receipt.get("attempt")) is not int:
            raise ValueError("only one physical attempt is registered")
        if receipt["body_sha256"] != request["body_sha256"]:
            raise ValueError("receipt request hash mismatch")
        start, end = timestamp(receipt["started_at"]), timestamp(receipt["completed_at"])
        duration = number(receipt["elapsed_s"])
        if end < start or (frozen_at and start <= timestamp(frozen_at)):
            raise ValueError("receipt predates calibration freeze or has invalid chronology")
        if abs((end - start).total_seconds() - duration) > max(1.0, duration * 0.05):
            raise ValueError("elapsed time disagrees with UTC timestamps")
        response = receipt["response"]
        probs = probabilities(provider, response, request["predicates"], model)
        total_in += response["usage"]["input_tokens"]
        total_out += response["usage"]["output_tokens"]
        elapsed.append(duration)
        pair = gold[request["pair_id"]]
        for pred, original in probs.items():
            p = min(1 - EPSILON, max(EPSILON, original))
            logits = [math.log1p(-p), math.log(p)]
            bucket = (pred, "en" if pair["lang"] == "mixed" else pair["lang"])
            calib = calibration[bucket] if calibration is not None else None
            pc = softmax([x / calib["temperature"] for x in logits])[1] if calib else None
            rows.append({"pair_id": pair["pair_id"], "direction": request["direction"],
                         "predicate": pred, "lang": pair["lang"], "source": pair.get("source"),
                         "hard_kind": pair.get("hard_kind"), "label": pair["labels"][pred],
                         "k": 2, "logits": logits, "logits_kind": "probability_pseudo_logits",
                         "p_provider": original, "probability_clipped": p != original,
                         "p_raw": p, "p_agent": original, "p_cal": pc,
                         "calibration_id": calib["calibration_id"] if calib else None,
                         "abstain_below": calib["min_confidence"] if calib else None,
                         "truncated": False, "request_id": rid, "provider": provider,
                         "returned_model": response["model"]})
    return rows, {"requests": len(receipts), "input_tokens": total_in, "output_tokens": total_out,
                  "elapsed_s": elapsed, "usage_unit": "one request, not one predicate"}


def audit_counts(rows):
    """Keep the audited false and true denominators distinct; never relabel them."""
    by_pair = {}
    for row in rows:
        if row["predicate"] == "same_as":
            by_pair.setdefault(row["pair_id"], {})[row["direction"]] = row
    result = {"false": {"n": 0, "accepted": 0, "ab_p_ge_half": 0},
              "true": {"n": 0, "accepted": 0, "ab_p_ge_half": 0}}
    for directions in by_pair.values():
        ab, ba = directions["ab"], directions["ba"]
        bucket = result["true" if ab["label"] else "false"]
        p, reverse, threshold = ab["p_cal"], ba["p_cal"], ab["abstain_below"]
        bucket["n"] += 1
        bucket["ab_p_ge_half"] += int(p >= 0.5)
        bucket["accepted"] += int(p >= 0.5 and threshold is not None
                                  and max(p, 1 - p) >= threshold and abs(p - reverse) <= 0.2)
    return result


def prepared(directory, root):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["contract"] != json.loads(CONTRACT.read_text(encoding="utf-8")):
        raise ValueError("prepared contract differs from the reviewed contract")
    provider = manifest["provider"]
    if manifest["provenance"] != provenance(provider, manifest["contract"]):
        raise ValueError("provider provenance mismatch")
    if manifest["code_sha256"] != {path: file_hash(root / path) for path in CODE}:
        raise ValueError("harness changed after preparation")
    return manifest, check_contract(root, manifest["contract"])


def provenance(provider, contract):
    return {"provider": provider, **contract["providers"][provider],
            "questions_hash": questions_hash(), "contract_sha256": digest(contract),
            "epsilon": EPSILON, "logits_kind": "probability_pseudo_logits",
            "source_calibration": contract["sets"]["calib"]["gold_sha256"]}


def verified_calibration(directory, manifest):
    from .laya.run_gold import load_calib
    lock = json.loads((directory / "calibration-frozen.json").read_text(encoding="utf-8"))
    if lock["provenance"] != manifest["provenance"]:
        raise ValueError("frozen provider mismatch")
    expected = {"calib.jsonl"} | {f"calibration/calib-{p}-{lang}.json"
                                 for p in QUESTIONS for lang in ("ko", "en")}
    if set(lock["files"]) != expected:
        raise ValueError("incomplete calibration freeze")
    for relative, sha in lock["files"].items():
        if file_hash(directory / relative) != sha:
            raise ValueError("calibration changed after freeze")
    return lock, load_calib(directory / "calibration")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("prepare", "import", "release-eval"))
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--directory", type=Path, required=True)
    ap.add_argument("--provider", choices=("jev", "openai_decisions"))
    ap.add_argument("--set", dest="set_name", choices=("calib", "a", "b", "c", "d"))
    ap.add_argument("--journal", type=Path)
    args = ap.parse_args(argv)
    directory, root = args.directory, args.root
    if args.action == "prepare":
        if not args.provider:
            ap.error("prepare requires --provider")
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        sets = check_contract(root, contract)
        pins = provenance(args.provider, contract)
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / "manifest.json", {"contract": contract, "provider": args.provider,
                                                 "provenance": pins,
                                                 "code_sha256": {p: file_hash(root / p) for p in CODE}})
        write_json(directory / "provenance.json", pins)
        requests = requests_for(args.provider, pins["model"], "calib", sets["calib"])
        write_rows(directory / "requests-calib.jsonl", requests, pins)
        return 0
    manifest, sets = prepared(directory, root)
    provider, pins = manifest["provider"], manifest["provenance"]
    if args.action == "release-eval":
        from .laya.run_gold import load_calib
        calibration = load_calib(directory / "calibration")
        expected = {(p, lang) for p in QUESTIONS for lang in ("ko", "en")}
        if set(calibration) != expected:
            raise ValueError("all six calibration files are required")
        raw = read_jsonl(directory / "calib.jsonl")
        # Refit inputs and provider provenance must be attached by --provenance.
        for (pred, lang), entry in calibration.items():
            if entry["pins"] != pins or entry["questions_hash"] != questions_hash():
                raise ValueError("calibration provider or question mismatch")
            number(entry["temperature"], 0.5, 5.0)
            if entry["min_confidence"] is not None:
                number(entry["min_confidence"], 0.5, 1)
            if not entry.get("calibration_id"):
                raise ValueError("missing calibration id")
            if (entry.get("source_input_sha256") != file_hash(directory / "calib.jsonl")
                    or entry.get("min_bucket_n") != 30 or entry["seed"] != 20261007):
                raise ValueError("calibration input or threshold recipe mismatch")
            count = sum(r["predicate"] == pred and ("en" if r["lang"] == "mixed" else r["lang"]) == lang
                        for r in raw)
            body = {k: v for k, v in entry.items() if k not in ("pins", "calibration_id")}
            cid = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]
            if entry["n"] != count or entry["calibration_id"] != cid:
                raise ValueError("calibration count or identifier mismatch")
        header = json.loads((directory / "calib.jsonl").read_text(encoding="utf-8").splitlines()[0][2:])
        if header["pins"] != pins:
            raise ValueError("calibration input provider mismatch")
        files = {"calib.jsonl": file_hash(directory / "calib.jsonl")}
        for path in sorted((directory / "calibration").glob("calib-*.json")):
            files[f"calibration/{path.name}"] = file_hash(path)
        write_json(directory / "calibration-frozen.json", {
            "frozen_at": datetime.now(timezone.utc).isoformat(), "files": files, "provenance": pins})
        for name in ("a", "b", "c", "d"):
            write_rows(directory / f"requests-{name}.jsonl",
                       requests_for(provider, pins["model"], name, sets[name]), pins)
        return 0
    if not args.journal or not args.set_name:
        ap.error("import requires --journal and --set")
    name = args.set_name
    requests = requests_for(provider, pins["model"], name, sets[name])
    calibration, frozen_at = None, None
    if name != "calib":
        lock, calibration = verified_calibration(directory, manifest)
        frozen_at = lock["frozen_at"]
    rows, usage = normalize(provider, pins["model"], requests, read_journal(args.journal),
                            sets[name], calibration, frozen_at)
    if name == "d":
        usage["same_as_audit"] = audit_counts(rows)
    header = {"pins": pins, "journal_sha256": file_hash(args.journal), "set": name}
    write_rows(directory / f"{name}.jsonl", rows, header)
    write_json(directory / f"{name}-usage.json", usage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
