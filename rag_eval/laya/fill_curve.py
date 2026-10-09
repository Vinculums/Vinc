"""고정된 후보·모델·보정값으로 로컬 채움 곡선을 잰다. 학습과 허브 쓰기는 없다.

근거: [[vinc.plan.laya-test-method-2026-10-07 §6 §8]]
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import time

from .fill_candidates import digest, write_json
from .metrics import softmax
from .pins import ASYMMETRY_ABSTAIN, QUESTIONS, load_agent, pin_header, render_state
from .run_gold import load_calib, read_jsonl

PREDICATES = ("contradicts", "same_as")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eligible(pair):
    return all(pair[k]["id"].startswith("concept:") for k in ("a", "b"))


def freeze(path, manifest):
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("frozen run manifest differs; refusing resume")
    else:
        write_json(path, manifest)


def completed(path, valid_ids):
    rows, keys = [], set()
    if not path.exists():
        return rows, keys
    for row in read_jsonl(path):
        key = (row["pair_id"], row["direction"])
        if row["pair_id"] not in valid_ids or row["direction"] not in ("ab", "ba") or key in keys:
            raise ValueError("unknown or repeated inference direction")
        if set(row["logits"]) != set(PREDICATES):
            raise ValueError("incomplete direction in inference journal")
        for logits in row["logits"].values():
            if len(logits) != 2 or not all(math.isfinite(v) for v in logits):
                raise ValueError("invalid logits")
        keys.add(key)
        rows.append(row)
    return rows, keys


def infer_direction(agent, state):
    """공개 records_from_labeled와 같은 인코딩·logits 경로를 한 번만 실행한다."""
    from laya.agent import _option_logits
    from laya.common import collate_items
    import torch
    ids = list(PREDICATES)
    internal = {p: agent._to_internal(QUESTIONS[p]) for p in ids}
    items = agent._encode_state({"text": state}, ids, internal)
    with torch.inference_mode():
        logits, _ = agent._forward(collate_items([items], agent.tok.pad_token_id))
    raw = _option_logits(logits, items, 0)
    stats = [it["state_stats"] for it in items]
    return {"logits": {p: [float(v) for v in r] for p, r in zip(ids, raw)},
            "truncated": any(s["truncated"] for s in stats),
            "state_tokens": stats[0]["state_tokens"],
            "state_tokens_dropped": max(s["state_tokens_dropped"] for s in stats)}


def decision(pair, pred, dirs, calibration):
    if not eligible(pair):
        return {"accepted": False, "reasons": ["unsupported_carrier"]}
    if set(dirs) != {"ab", "ba"}:
        raise ValueError("incomplete measurement; no final report is allowed")
    lang = pair["lang"] if pair["lang"] == "ko" else "en"
    cal = calibration[(pred, lang)]
    probs = {d: softmax([v / cal["temperature"] for v in row["logits"][pred]])[1]
             for d, row in dirs.items()}
    p, reverse = probs["ab"], probs["ba"]
    reasons = []
    if any(row["truncated"] for row in dirs.values()):
        reasons.append("truncated")
    threshold = cal.get("min_confidence")
    if threshold is None:
        reasons.append("missing_threshold")
    elif max(p, 1-p) < threshold:
        reasons.append("below_threshold")
    if abs(p-reverse) > ASYMMETRY_ABSTAIN:
        reasons.append("asymmetry")
    return {"accepted": not reasons, "reasons": reasons, "p": p, "p_reverse": reverse,
            "threshold": threshold, "calibration_id": cal["calibration_id"],
            "positive": p >= .5, "asymmetry": abs(p-reverse)}


def belief_fragment(pair, pred, result, *, run_id, model_sha, day):
    a, b = pair["a"], pair["b"]
    p = result["p"]
    nid = "record:belief-" + digest([a["id"], pred, b["id"]])[:12] + "-" + run_id
    node = {"id": nid, "type": "record", "title": f"{pred} judgment for {pair['pair_id']}",
            "domain": a["domain"], "valid_from": day, "valid_until": "",
            "props": {"belief_schema": "vinc.belief.v1",
                      "subject": {"from": a["id"], "predicate": pred, "to": b["id"]},
                      "p": p, "distribution": [{"option": "false", "p": 1-p}, {"option": "true", "p": p}],
                      "directional_probabilities": {"ab": p, "ba": result["p_reverse"]},
                      "judged_by": "laya:laya-multilingual-vinc-ft-v2@0.3.28#" + model_sha,
                      "calibration_id": result["calibration_id"],
                      "evidence_ids": list(dict.fromkeys([a["id"], b["id"], *pair.get("context_ids", [])])),
                      "independence_group": "laya-run:" + run_id,
                      "rev_seen": {n["id"]: n["rev"] for n in (a,b)}, "grade": "model"}}
    from api.belief_contract import validate
    validate(node)
    return {"pair_id": pair["pair_id"], "predicate": pred,
            "nodes": [{"id": n["id"], "type": "concept"} for n in (a,b)] + [node],
            "edges": [{"from": nid, "to": n["id"], "type": "relates_to",
                       "evidence": "Local frozen Laya measurement; not written to the live graph"} for n in (a,b)]}


def summarize(pairs, rows, calibration, out, *, run_id, model_sha, day):
    by = {}
    for row in rows:
        dirs = by.setdefault(row["pair_id"], {})
        if row["direction"] in dirs:
            raise ValueError("duplicate inference direction")
        dirs[row["direction"]] = row
    expected = {r["pair_id"] for r in pairs if eligible(r)}
    if set(by) != expected or any(set(dirs) != {"ab", "ba"} for dirs in by.values()):
        raise ValueError("raw results do not cover exactly the supported candidate pairs")
    outcomes, beliefs, abstained = [], [], []
    for pair in pairs:
        for pred in PREDICATES:
            result = decision(pair, pred, by.get(pair["pair_id"], {}), calibration)
            row = {"pair_id": pair["pair_id"], "predicate": pred, "lang": pair["lang"],
                   "sources": pair["sources"], "eligible": eligible(pair), **result}
            outcomes.append(row)
            if result["accepted"]:
                beliefs.append(belief_fragment(pair, pred, result, run_id=run_id, model_sha=model_sha, day=day))
            else:
                abstained.append(row)
    for name, data in (("beliefs",beliefs), ("abstained",abstained), ("outcomes",outcomes)):
        with (out / (name + ".jsonl")).open("w", encoding="utf-8", newline="\n") as fh:
            for row in data:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    table = []
    sources = sorted({s for row in pairs for s in row["sources"]})
    for pred in PREDICATES:
        for dimension, values in (("all", ["all"]), ("lang", ["ko", "en", "mixed"]), ("generator", sources)):
            for value in values:
                selected = [r for r in outcomes if r["predicate"] == pred and
                            (dimension == "all" or dimension == "lang" and r["lang"] == value
                             or dimension == "generator" and value in r["sources"])]
                n, supported = len(selected), sum(r["eligible"] for r in selected)
                accepted = [r for r in selected if r["accepted"]]
                k = len(accepted)
                table.append({"predicate": pred, "dimension": dimension, "value": value,
                              "candidates": n, "supported": supported, "accepted": k, "abstained": n-k,
                              "fill_rate": k/n if n else None, "abstention_rate": (n-k)/n if n else None,
                              "supported_fill_rate": k/supported if supported else None,
                              "positive": sum(r["positive"] for r in accepted),
                              "negative": sum(not r["positive"] for r in accepted),
                              "exclusive_generator_accepted": sum(len(r["sources"]) == 1 for r in accepted) if dimension == "generator" else None,
                              "reasons": dict(Counter(reason for r in selected for reason in r["reasons"]))})
    summary = {"run_id": run_id, "candidate_pairs": len(pairs), "supported_pairs": len(expected),
               "inference_directions": len(rows), "table": table,
               "generator_membership_is_overlapping": True, "inference_seconds": sum(r["seconds"] for r in rows),
               "live_belief_writes": 0, "labels_used": False}
    write_json(out / "summary.json", summary)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, type=Path)
    ap.add_argument("--calib", required=True, type=Path)
    ap.add_argument("--calibration-pin", required=True, type=Path)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--expected-model-sha", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--day", required=True)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    date.fromisoformat(args.day)
    args.out.mkdir(parents=True, exist_ok=True)
    pins = pin_header(args.model_dir)
    if pins["model_dir_sha256"] != args.expected_model_sha:
        raise ValueError("checkpoint hash differs from the approved pin")
    calibration_pin = json.loads(args.calibration_pin.read_text(encoding="utf-8"))
    if calibration_pin["model_sha256"] != args.expected_model_sha or any(
            sha(args.calib / name) != expected for name, expected in calibration_pin["calibration_sha256"].items()):
        raise ValueError("calibration differs from the pre-evaluation pin")
    if not args.report_only and (pins.get("laya") != "0.3.28" or pins.get("torch") != "2.14.1+cpu"):
        raise ValueError("measurement requires the pinned CPU environment")
    manifest = {"candidates_sha256": sha(args.candidates), "model_sha256": args.expected_model_sha,
                "checkpoint_json_sha256": {p.relative_to(args.model_dir).as_posix(): sha(p)
                                            for p in sorted(Path(args.model_dir).rglob("*.json"))},
                "calibrations": {p.name: sha(p) for p in sorted(args.calib.glob("calib-*.json"))},
                "questions_hash": pins["questions_hash"], "predicates": list(PREDICATES),
                "measurement_code_sha256": sha(Path(__file__)),
                "calibration_pin_sha256": sha(args.calibration_pin),
                "run_id": args.run_id, "day": args.day, "asymmetry_abstain": ASYMMETRY_ABSTAIN}
    freeze(args.out / "frozen.json", manifest)
    pairs, calibration = read_jsonl(args.candidates), load_calib(args.calib)
    if len({p["pair_id"] for p in pairs}) != len(pairs):
        raise ValueError("duplicate candidate pair id")
    for pred in PREDICATES:
        for lang in ("ko","en"):
            cal = calibration[(pred,lang)]
            if not 0.5 <= cal["temperature"] <= 5 or not cal["calibration_id"]:
                raise ValueError("invalid calibration")
    rows, done = completed(args.out / "raw.jsonl", {p["pair_id"] for p in pairs if eligible(p)})
    if not args.report_only:
        load_start = time.perf_counter()
        agent = load_agent(args.model_dir)
        if agent.device.type != "cpu" or agent.cfg.get("max_len") != 1024:
            raise ValueError("CPU and the registered token budget are required")
        pins["load_seconds"] = time.perf_counter() - load_start
        write_json(args.out / "runtime.json", pins)
        with (args.out / "raw.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
            for pair in pairs:
                if not eligible(pair):
                    continue
                for direction, a, b in (("ab","a","b"), ("ba","b","a")):
                    if (pair["pair_id"], direction) in done:
                        continue
                    state = render_state(pair[a]["text"], pair[b]["text"], pair.get("context"))
                    start = time.perf_counter()
                    row = {"pair_id": pair["pair_id"], "direction": direction, **infer_direction(agent, state)}
                    row["seconds"] = time.perf_counter() - start
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    rows.append(row)
                    done.add((pair["pair_id"], direction))
                    if len(rows) % 50 == 0:
                        print(f"directions {len(rows)}/{2*sum(eligible(p) for p in pairs)} last_s={row['seconds']:.2f}", flush=True)
    summary = summarize(pairs, rows, calibration, args.out, run_id=args.run_id,
                        model_sha=args.expected_model_sha, day=args.day)
    print(json.dumps([r for r in summary["table"] if r["dimension"] == "all"]), flush=True)


if __name__ == "__main__":
    main()
