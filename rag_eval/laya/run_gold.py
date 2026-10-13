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
    ap.add_argument("--model-dir", default=None,
                    help="핀 체크포인트 대신 올릴 로컬 체크포인트(미세조정, §7.1). 머리에 sha256 을 적는다")
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
    agent = load_agent(args.model_dir)
    load_s = time.perf_counter() - t0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    header = pin_header(args.model_dir)
    header.update(kind="run_gold", gold=str(args.gold), split=args.split, n_pairs=len(pairs),
                  load_s=round(load_s, 1), calib_files=sorted(f"{k[0]}:{k[1]}" for k in calib))
    # state 마다 forward 가 둘이다: system_one(usage·절단 정보)과 records_from_labeled(원시 logits).
    # 그래서 state 당 지연은 둘을 따로 재고, 처리량은 끝에 predict_batch(batch_size=16) 로 따로 잰다(§5).
    lat_predict: list[float] = []
    lat_logits: list[float] = []
    probe_states: list[dict] = []
    n_truncated = 0
    with args.out.open("w", encoding="utf-8") as fh:
        fh.write("# " + json.dumps(header, ensure_ascii=False) + "\n")
        for pair in pairs:
            lang = pair.get("lang", "en")
            for direction, state, preds in states_for(pair):
                qs = {p: QUESTIONS[p] for p in preds}
                t = time.perf_counter()
                ans = agent.system_one({"text": state}, qs)
                t1 = time.perf_counter()
                dummy = {p: [0.0, 1.0] for p in preds}  # 타깃은 쓰지 않는다. logits 만 받는다
                recs = records_from_labeled(agent, [({"text": state}, qs, dummy)])
                t2 = time.perf_counter()
                lat_predict.append(t1 - t)
                lat_logits.append(t2 - t1)
                if len(probe_states) < 32:
                    probe_states.append({"text": state})
                usage = ans.get("usage", {})
                if usage.get("truncated"):
                    n_truncated += 1
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
                        "state_tokens_dropped": usage.get("state_tokens_dropped"),
                        "truncated": bool(usage.get("truncated")),
                    }, ensure_ascii=False) + "\n")
        # 처리량: 같은 state 32개를 predict_batch(batch_size=16) 로 한 번 더 돌려 state 당 초를 잰다.
        # 위의 per-state 지연은 forward 둘의 합이라 처리량이 아니다(handoff 10-07 지적).
        batch16 = None
        if probe_states:
            t = time.perf_counter()
            agent.predict_batch(probe_states, dict(QUESTIONS), batch_size=16)
            batch16 = round((time.perf_counter() - t) / len(probe_states), 3)
        timing = {"kind": "timing", "n_states": len(lat_predict),
                  "median_s_predict": _median(lat_predict), "median_s_logits": _median(lat_logits),
                  "median_s_total": _median([a + b for a, b in zip(lat_predict, lat_logits)]),
                  "batch16_s_per_state": batch16, "batch16_n_states": len(probe_states),
                  "truncated_states": n_truncated,
                  "note": "per-state seconds cover two forwards (system_one + records_from_labeled); "
                          "batch16_s_per_state is the predict_batch(batch_size=16) throughput on the first states"}
        fh.write("# " + json.dumps(timing) + "\n")
    print(f"wrote {args.out} pairs={len(pairs)} states={len(lat_predict)} median_s_total={timing['median_s_total']} "
          f"batch16_s_per_state={batch16} truncated_states={n_truncated} load_s={load_s:.1f}")
    return 0


def _median(xs: list[float]):
    return round(sorted(xs)[len(xs) // 2], 3) if xs else None


if __name__ == "__main__":
    sys.exit(main())
