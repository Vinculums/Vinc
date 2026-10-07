# -*- coding: utf-8 -*-
"""골드(또는 후보)에 Laya 를 돌려 원시 logits 와 usage 를 남긴다. 양방향 질의 포함.

    python -m rag_eval.laya.run_gold --gold gold.jsonl --split calib --out out/calib.jsonl
    python -m rag_eval.laya.run_gold --gold gold.jsonl --split eval  --calib calib/ --out out/eval.jsonl

출력 한 줄 = (pair, 방향, 술어) 하나. logits 는 보정 전 원시값(2 markers: false, true), p_raw 는 그 softmax,
p_agent 는 laya 가 자기 온도로 낸 값, p_cal 은 --calib 가 있을 때 (predicate, lang) 보정 파일의 온도를
적용한 값. truncated 행은 남기되 표시한다(보고서가 뺀다). 근거: [[vinc.plan.laya-test-method-2026-10-07 §2 §4 §9]]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .metrics import softmax
from .pins import PREDICATES, QUESTIONS, SYMMETRIC, load_agent, pin_header, render_state
from .split import load_or_make


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _flat(x) -> list:
    """numpy 배열·중첩 리스트를 평평한 float 리스트로. 모델 venv 밖에서도 읽히게 numpy 를 들이지 않는다."""
    if hasattr(x, "tolist"):
        x = x.tolist()
    out = []
    for v in (x if isinstance(x, (list, tuple)) else [x]):
        out.extend(_flat(v) if isinstance(v, (list, tuple)) else [v])
    return out


def load_calib(calib_dir: Path | None) -> dict:
    if not calib_dir:
        return {}
    out = {}
    for f in calib_dir.glob("calib-*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[(d["predicate"], d["lang"])] = d
    return out


def states_for(pair: dict):
    """(방향, state, 술어 목록). 대칭 술어는 역방향도 묻는다."""
    a, b, ctx = pair["a"]["text"], pair["b"]["text"], pair.get("context")
    yield "ab", render_state(a, b, ctx), list(PREDICATES)
    yield "ba", render_state(b, a, ctx), [p for p in PREDICATES if p in SYMMETRIC]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--split", choices=["calib", "eval", "all"], default="all")
    ap.add_argument("--calib", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    pairs = read_jsonl(args.gold)
    split = load_or_make(args.gold, pairs)
    if args.split != "all":
        pairs = [p for p in pairs if split.get(p["pair_id"]) == args.split]
    if args.limit:
        pairs = pairs[: args.limit]
    calib = load_calib(args.calib)

    from laya.calibrate import records_from_labeled  # 지연 import: 모델 venv 에서만

    t0 = time.perf_counter()
    agent = load_agent()
    load_s = time.perf_counter() - t0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = pin_header()
    header.update(kind="run_gold", gold=str(args.gold), split=args.split, n_pairs=len(pairs),
                  load_s=round(load_s, 1), calib_files=sorted(f"{k[0]}:{k[1]}" for k in calib))
    latencies: list[float] = []
    with args.out.open("w", encoding="utf-8") as fh:
        fh.write("# " + json.dumps(header, ensure_ascii=False) + "\n")
        for pair in pairs:
            lang = pair.get("lang", "en")
            for direction, state, preds in states_for(pair):
                qs = {p: QUESTIONS[p] for p in preds}
                t = time.perf_counter()
                ans = agent.system_one({"text": state}, qs)
                dummy = {p: [0.0, 1.0] for p in preds}  # 타깃은 쓰지 않는다. logits 만 받는다
                recs = records_from_labeled(agent, [({"text": state}, qs, dummy)])
                latencies.append(time.perf_counter() - t)
                usage = ans.get("usage", {})
                for (qt, logits, _tgt, k), p in zip(recs, preds):
                    logits = [float(x) for x in _flat(logits)[:k]]
                    p_raw = softmax(logits)[1] if k == 2 else float("nan")
                    cal = calib.get((p, lang)) or calib.get((p, "en"))
                    p_cal = softmax([x / cal["temperature"] for x in logits])[1] if cal and k == 2 else None
                    label = (pair.get("labels") or {}).get(p)
                    fh.write(json.dumps({
                        "pair_id": pair["pair_id"], "direction": direction, "predicate": p, "lang": lang,
                        "source": pair.get("source"), "hard_kind": pair.get("hard_kind"), "label": label,
                        "k": int(k), "logits": logits, "p_raw": p_raw, "p_agent": ans["answers"][p]["noul"],
                        "p_cal": p_cal, "calibration_id": cal["calibration_id"] if cal else None,
                        "abstain_below": cal.get("min_confidence") if cal else None,
                        "input_tokens": usage.get("input_tokens"), "state_tokens": usage.get("state_tokens"),
                        "truncated": bool(usage.get("truncated")),
                    }, ensure_ascii=False) + "\n")
        median = sorted(latencies)[len(latencies) // 2] if latencies else None
        fh.write("# " + json.dumps({"kind": "timing", "n_calls": len(latencies), "median_s": median}) + "\n")
    print(f"wrote {args.out} pairs={len(pairs)} calls={len(latencies)} median_s={median} load_s={load_s:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
