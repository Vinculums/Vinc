# -*- coding: utf-8 -*-
"""측정 150 의 보고서: 술어 × 언어 표, 기준선, 곡선, 비대칭도, 킬 기준 판정. 근거: 테스트 방식 §5 §7.

    python -m rag_eval.laya.report --in out/eval.jsonl --gold gold.jsonl --out out/report.md [--cosine cosine.jsonl]

기준선: 다수 클래스, 공동 출현 규칙(context 가 있으면 supports, 제목 Jaccard>=0.6 이면 same_as,
부정어 한쪽 포함이면 contradicts). 코사인 기준선은 --cosine 파일(pair_id, cos)이 있을 때만.
측정은 한 번만 돈다. 이 스크립트는 읽기만 한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from . import metrics as M
from .pins import ASYMMETRY_ABSTAIN, PREDICATES, SYMMETRIC, TARGET_ERROR, pin_header
from .run_gold import read_jsonl

NEG = re.compile(r"\b(not|never|no longer|cannot|without)\b|없다|아니다|금지|않는다|못한다", re.I)
GATE = {"auroc_margin": 0.05, "ece_max": 0.10, "coverage_min": 0.5}


def tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9가-힣]+", (s or "").lower()))


def rule_baseline(pred: str, g: dict) -> float:
    a, b, ctx = g.get("a_text", ""), g.get("b_text", ""), g.get("context")
    if pred == "supports":
        return 1.0 if ctx else 0.0
    if pred == "same_as":
        ta, tb = tokens(a), tokens(b)
        return 1.0 if ta and tb and len(ta & tb) / len(ta | tb) >= 0.6 else 0.0
    return 1.0 if bool(NEG.search(a)) != bool(NEG.search(b)) else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--gold", type=Path, default=None, help="규칙 기준선용 원문 텍스트(없으면 규칙 기준선 생략)")
    ap.add_argument("--cosine", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    rows = read_jsonl(args.inp)
    gold = {p["pair_id"]: {"a_text": p["a"]["text"], "b_text": p["b"]["text"], "context": p.get("context")}
            for p in read_jsonl(args.gold)} if args.gold else {}
    cos = {r["pair_id"]: float(r["cos"]) for r in read_jsonl(args.cosine)} if args.cosine else {}

    by: dict = defaultdict(dict)  # (pred, pair) -> direction -> row
    for r in rows:
        if r.get("label") in (0, 1) and not r.get("truncated"):
            by[(r["predicate"], r["pair_id"])][r["direction"]] = r

    result = {"pins": pin_header(), "gate": GATE, "target_error": TARGET_ERROR, "table": [], "curves": {}}
    lines = ["# Laya 술어 측정 보고서", "", f"입력: `{args.inp}`  행 {len(rows)}", "",
             "| 술어 | 언어 | n | Brier | ECE | AUROC | coverage | 비대칭 기권 | 게이트 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for pred in PREDICATES:
        for lang in ("ko", "en", "mixed", "all"):
            items = [(k, v) for k, v in by.items()
                     if k[0] == pred and "ab" in v and (lang == "all" or v["ab"]["lang"] == lang)]
            if not items:
                continue
            p_cal, p_raw, y, p_ab, p_ba, rule_p, cos_p, abst = [], [], [], [], [], [], [], 0
            for (_pr, pid), dirs in items:
                ab = dirs["ab"]
                pc = ab["p_cal"] if ab.get("p_cal") is not None else ab["p_raw"]
                if pred in SYMMETRIC and "ba" in dirs:
                    ba = dirs["ba"]
                    pb = ba["p_cal"] if ba.get("p_cal") is not None else ba["p_raw"]
                    p_ab.append(pc)
                    p_ba.append(pb)
                    if abs(pc - pb) > ASYMMETRY_ABSTAIN:
                        abst += 1
                        continue
                p_cal.append(pc)
                p_raw.append(ab["p_raw"])
                y.append(ab["label"])
                if pid in gold:
                    rule_p.append(rule_baseline(pred, gold[pid]))
                if pid in cos:
                    cos_p.append(cos[pid])
            if not y:
                continue
            row = {"predicate": pred, "lang": lang, "n": len(y), "positives": int(sum(y)),
                   "abstained_asymmetry": abst,
                   "brier": M.brier(p_cal, y), "ece": M.ece(p_cal, y), "auroc": M.auroc(p_cal, y),
                   "brier_raw": M.brier(p_raw, y), "ece_raw": M.ece(p_raw, y), "auroc_raw": M.auroc(p_raw, y),
                   "coverage_at_error": M.coverage_at_error(p_cal, y, TARGET_ERROR),
                   "baseline_majority_auroc": M.auroc(M.majority_baseline(y), y),
                   "baseline_rule_auroc": M.auroc(rule_p, y) if len(rule_p) == len(y) else None,
                   "baseline_cosine_auroc": M.auroc(cos_p, y) if len(cos_p) == len(y) else None,
                   "asymmetry": M.asymmetry(p_ab, p_ba) if p_ab else None}
            bests = [v for v in (row["baseline_majority_auroc"], row["baseline_rule_auroc"],
                                 row["baseline_cosine_auroc"]) if v is not None and v == v]
            best = max(bests) if bests else 0.5
            row["best_baseline_auroc"] = best
            g = {"auroc_ok": bool(row["auroc"] == row["auroc"] and row["auroc"] >= best + GATE["auroc_margin"]),
                 "ece_ok": bool(row["ece"] <= GATE["ece_max"]),
                 "coverage_ok": bool(row["coverage_at_error"]["coverage"] >= GATE["coverage_min"])}
            g["pass"] = all(g.values())
            row["gate"] = g
            result["table"].append(row)
            result["curves"][f"{pred}:{lang}"] = M.selective_curve(p_cal, y)
            lines.append(f"| {pred} | {lang} | {row['n']} (+{row['positives']}) | {row['brier']:.3f} | "
                         f"{row['ece']:.3f} | {row['auroc']:.3f} (best baseline {best:.3f}) | "
                         f"{row['coverage_at_error']['coverage']:.2f} @{TARGET_ERROR:.0%} err | {abst} | "
                         f"{'PASS' if g['pass'] else 'FAIL'} (auroc {g['auroc_ok']}, ece {g['ece_ok']}, cov {g['coverage_ok']}) |")
    lines += ["", "게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다."]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                                             encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
