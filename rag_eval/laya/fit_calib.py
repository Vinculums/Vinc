# -*- coding: utf-8 -*-
"""술어 × 언어 보정 파일 여섯과 버킷별 기권 문턱. 근거: 테스트 방식 §4-3~6.

    python -m rag_eval.laya.fit_calib --in out/calib.jsonl --out calib/

laya.fit_temperatures 는 noul 을 한 버킷("noul:2")으로 묶으므로 술어·언어별로 레코드를 나눠 따로 맞춘다.
온도가 TEMP_MAX(5.0)에 붙으면 그 술어는 "보정으로 못 구함"으로 적는다. 레코드가 적으면 laya 가 기본
온도를 돌려주므로 n 과 함께 underpowered 를 적는다. mixed 는 en 파일을 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from .metrics import ece, softmax
from .pins import PREDICATES, SEED, TARGET_ERROR, pin_header, questions_hash
from .run_gold import read_jsonl


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--min-bucket-n", type=int, default=30)
    args = ap.parse_args(argv)

    import numpy as np
    from laya.calibrate import TEMP_MAX, TEMP_MIN, fit_abstention_thresholds, fit_temperatures, temp_bucket

    rows = [r for r in read_jsonl(args.inp) if r.get("label") in (0, 1) and not r.get("truncated")]
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        lang = "en" if r["lang"] == "mixed" else r["lang"]
        groups[(r["predicate"], lang)].append(r)

    args.out.mkdir(parents=True, exist_ok=True)
    summary = []
    for pred in PREDICATES:
        for lang in ("ko", "en"):
            recs_rows = groups.get((pred, lang), [])
            records = [(2, np.asarray(r["logits"], float), np.asarray([1 - r["label"], r["label"]], float), 2)
                       for r in recs_rows]
            entry = {"predicate": pred, "lang": lang, "n": len(records), "seed": SEED,
                     "questions_hash": questions_hash(), "bucket": temp_bucket(2, 2)}
            if not records:
                entry.update(temperature=1.0, status="no_records", min_confidence=None, underpowered=True)
            else:
                fit = fit_temperatures(records, compute_ece=True, seed=SEED)
                temps = list(fit.get("temperature") or [1.0, 1.0, 1.0])
                by_opt = dict(fit.get("temperature_by_options") or {})
                temperature = float(by_opt.get(entry["bucket"], temps[2] if len(temps) > 2 else 1.0))
                status = "ok"
                if temperature >= TEMP_MAX - 1e-6:
                    status = "temperature_at_max"
                elif temperature <= TEMP_MIN + 1e-6:
                    status = "temperature_at_min"
                try:
                    th = fit_abstention_thresholds(records, temps, by_opt, target_error=TARGET_ERROR,
                                                   min_bucket_n=args.min_bucket_n, conservative=True)
                    min_conf = th.get(entry["bucket"])
                except Exception as exc:  # noqa: BLE001
                    th, min_conf = {"error": f"{type(exc).__name__}: {exc}"}, None
                p_before = [softmax(r["logits"])[1] for r in recs_rows]
                p_after = [softmax([x / temperature for x in r["logits"]])[1] for r in recs_rows]
                y = [r["label"] for r in recs_rows]
                entry.update(temperature=temperature, status=status, min_confidence=min_conf,
                             laya_report=fit.get("report") or {}, thresholds=th,
                             ece_before_in_sample=ece(p_before, y), ece_after_in_sample=ece(p_after, y),
                             underpowered=len(records) < 50)
            blob = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
            entry["calibration_id"] = hashlib.sha256(blob.encode()).hexdigest()[:12]
            path = args.out / f"calib-{pred}-{lang}.json"
            path.write_text(json.dumps({"pins": pin_header(), **entry}, ensure_ascii=False, indent=1, default=str),
                            encoding="utf-8")
            summary.append({k: entry.get(k) for k in ("predicate", "lang", "n", "temperature", "status",
                                                      "min_confidence", "calibration_id", "underpowered")})
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in summary:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
