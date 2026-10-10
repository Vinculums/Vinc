# -*- coding: utf-8 -*-
"""측정 150 의 보고서: 술어 × 언어 표, 기준선, 곡선, 비대칭도, 킬 기준 판정. 근거: 테스트 방식 §5 §7.

    python -m rag_eval.laya.report --in out/eval.jsonl --gold gold.jsonl --out out/report.md [--cosine cosine.jsonl]

기준선: 다수 클래스, 공동 출현 규칙(context 가 있으면 supports, 제목 Jaccard>=0.6 이면 same_as,
부정어 한쪽 포함이면 contradicts). 코사인 기준선은 --cosine 파일(pair_id, cos)이 있을 때만.
측정은 한 번만 돈다. 이 스크립트는 읽기만 한다.

커버리지는 **보정 단계에서 동결된 기권 문턱**(행의 abstain_below, 보정 파일의 min_confidence)으로 센다.
평가 라벨을 보고 문턱을 고르는 사후 곡선(posthoc_coverage_at_error)은 진단으로만 남긴다.
분모는 적격 쌍 전부다(라벨 0/1, 절단 아님). 비대칭도 0.2 초과와 문턱 미달은 둘 다 기권으로 세고,
AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 낸다. 기권한 쌍을 먼저 떨구고 지표를 내면
모델이 고른 쉬운 쌍만 재는 것이라 그렇게 하지 않는다(10-08, handoff 지적 둘을 고친 것).
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


def _p(row: dict) -> float:
    return row["p_cal"] if row.get("p_cal") is not None else row["p_raw"]


def frozen_coverage(items: list[dict]) -> dict:
    """동결 문턱 커버리지. items 의 원소: {conf, correct, threshold|None, asym|None, has_reverse}.

    covered = 문턱이 있고 conf >= 문턱이고 (대칭 술어면) 역방향이 있고 비대칭도 <= 0.2.
    분모는 len(items). 문턱이 없는 쌍은 덮지 못한 것으로 센다(--calib 없이 돌린 결과나 보정 파일 결손)."""
    n = len(items)
    covered, below, asym, missing, no_th = [], 0, 0, 0, 0
    for it in items:
        th = it["threshold"]
        if th is None:
            no_th += 1
            continue
        ok = it["conf"] >= th
        if not ok:
            below += 1
        if it["asym"] is not None and it["asym"] > ASYMMETRY_ABSTAIN:
            asym += 1
            ok = False
        if it["asym"] is None and not it["has_reverse"]:
            missing += 1
            ok = False
        if ok:
            covered.append(it)
    k = len(covered)
    err = (1 - sum(c["correct"] for c in covered) / k) if k else float("nan")
    ths = sorted({it["threshold"] for it in items if it["threshold"] is not None})
    return {"n": n, "covered": k, "coverage": k / n if n else 0.0, "error": err,
            "thresholds": ths, "abstained_below_threshold": below, "abstained_asymmetry": asym,
            "abstained_missing_reverse": missing, "no_threshold": no_th}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--gold", type=Path, default=None, help="규칙 기준선용 원문 텍스트(없으면 규칙 기준선 생략)")
    ap.add_argument("--cosine", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--provenance", type=Path, help="Optional provider pins JSON for imported probabilities")
    args = ap.parse_args(argv)
    pins = json.loads(args.provenance.read_text(encoding="utf-8")) if args.provenance else pin_header()

    rows = read_jsonl(args.inp)
    gold = {p["pair_id"]: {"a_text": p["a"]["text"], "b_text": p["b"]["text"], "context": p.get("context")}
            for p in read_jsonl(args.gold)} if args.gold else {}
    cos = {r["pair_id"]: float(r["cos"]) for r in read_jsonl(args.cosine)} if args.cosine else {}

    n_truncated = sum(1 for r in rows if r.get("truncated"))
    n_unlabeled = sum(1 for r in rows if r.get("label") not in (0, 1))
    by: dict = defaultdict(dict)  # (pred, pair) -> direction -> row
    for r in rows:
        if r.get("label") in (0, 1) and not r.get("truncated"):
            by[(r["predicate"], r["pair_id"])][r["direction"]] = r

    result = {"pins": pins, "gate": GATE, "target_error": TARGET_ERROR,
              "asymmetry_abstain": ASYMMETRY_ABSTAIN, "rows": len(rows),
              "rows_truncated_excluded": n_truncated, "rows_unlabeled_excluded": n_unlabeled,
              "table": [], "curves": {}}
    lines = [f"# {pins.get('provider', 'Laya')} 술어 측정 보고서", "",
             f"입력: `{args.inp}`  행 {len(rows)} (절단으로 제외 {n_truncated}, 라벨 없음 {n_unlabeled})", "",
             "| 술어 | 언어 | n | Brier | ECE | AUROC | coverage@동결 문턱 (실제 오류) | 기권 문턱/비대칭 | 게이트 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for pred in PREDICATES:
        for lang in ("ko", "en", "mixed", "all"):
            items = [(k, v) for k, v in by.items()
                     if k[0] == pred and "ab" in v and (lang == "all" or v["ab"]["lang"] == lang)]
            if not items:
                continue
            p_cal, p_raw, y, p_ab, p_ba, rule_p, cos_p, cov_items, calib_ids = [], [], [], [], [], [], [], [], set()
            for (_pr, pid), dirs in items:
                ab = dirs["ab"]
                pc = _p(ab)
                asym = None
                has_reverse = "ba" in dirs
                if pred in SYMMETRIC and has_reverse:
                    pb = _p(dirs["ba"])
                    p_ab.append(pc)
                    p_ba.append(pb)
                    asym = abs(pc - pb)
                p_cal.append(pc)
                p_raw.append(ab["p_raw"])
                y.append(ab["label"])
                if ab.get("calibration_id"):
                    calib_ids.add(ab["calibration_id"])
                cov_items.append({"conf": max(pc, 1 - pc), "correct": 1.0 if (pc >= 0.5) == bool(ab["label"]) else 0.0,
                                  "threshold": ab.get("abstain_below"), "asym": asym,
                                  "has_reverse": has_reverse if pred in SYMMETRIC else True})
                if pid in gold:
                    rule_p.append(rule_baseline(pred, gold[pid]))
                if pid in cos:
                    cos_p.append(cos[pid])
            if not y:
                continue
            frozen = frozen_coverage(cov_items)
            row = {"predicate": pred, "lang": lang, "n": len(y), "positives": int(sum(y)),
                   "calibration_ids": sorted(calib_ids),
                   "brier": M.brier(p_cal, y), "ece": M.ece(p_cal, y), "auroc": M.auroc(p_cal, y),
                   "brier_raw": M.brier(p_raw, y), "ece_raw": M.ece(p_raw, y), "auroc_raw": M.auroc(p_raw, y),
                   "frozen": frozen,
                   "posthoc_coverage_at_error": M.coverage_at_error(p_cal, y, TARGET_ERROR),
                   "baseline_majority_auroc": M.auroc(M.majority_baseline(y), y),
                   "baseline_rule_auroc": M.auroc(rule_p, y) if len(rule_p) == len(y) else None,
                   "baseline_cosine_auroc": M.auroc(cos_p, y) if len(cos_p) == len(y) else None,
                   "asymmetry": M.asymmetry(p_ab, p_ba) if p_ab else None}
            bests = [v for v in (row["baseline_majority_auroc"], row["baseline_rule_auroc"],
                                 row["baseline_cosine_auroc"]) if v is not None and v == v]
            best = max(bests) if bests else 0.5
            row["best_baseline_auroc"] = best
            err = frozen["error"]
            g = {"auroc_ok": bool(row["auroc"] == row["auroc"] and row["auroc"] >= best + GATE["auroc_margin"]),
                 "ece_ok": bool(row["ece"] <= GATE["ece_max"]),
                 "coverage_ok": bool(frozen["no_threshold"] == 0 and frozen["coverage"] >= GATE["coverage_min"]),
                 # 동결 문턱에서 실제 오류율이 목표 안인지. 사전 등록 게이트 셋에 들지 않으므로 pass 에 안 섞는다.
                 "error_within_target": (bool(err <= TARGET_ERROR) if err == err else None)}
            g["pass"] = all(g[k] for k in ("auroc_ok", "ece_ok", "coverage_ok"))
            row["gate"] = g
            result["table"].append(row)
            result["curves"][f"{pred}:{lang}"] = M.selective_curve(p_cal, y)
            err_s = f"{err:.3f}" if err == err else "n/a"
            lines.append(f"| {pred} | {lang} | {row['n']} (+{row['positives']}) | {row['brier']:.3f} | "
                         f"{row['ece']:.3f} | {row['auroc']:.3f} (best baseline {best:.3f}) | "
                         f"{frozen['coverage']:.2f} ({err_s}) | {frozen['abstained_below_threshold']}/{frozen['abstained_asymmetry']} | "
                         f"{'PASS' if g['pass'] else 'FAIL'} (auroc {g['auroc_ok']}, ece {g['ece_ok']}, cov {g['coverage_ok']}"
                         f"{'' if frozen['no_threshold'] == 0 else ', 동결 문턱 없음'}) |")
    lines += ["", "게이트 수치는 사전 등록(§7)이고 측정 뒤 바꾸지 않는다. 코사인 기준선이 없으면 생략된 것이다.",
              f"coverage 는 보정에서 동결한 기권 문턱(abstain_below)으로 세고 분모는 적격 쌍 전부다. 비대칭도 {ASYMMETRY_ABSTAIN} 초과와 "
              "문턱 미달은 기권이다. AUROC·Brier·ECE 는 기권과 무관하게 적격 쌍 전부에서 냈다. "
              "평가 라벨로 문턱을 고르는 사후 곡선은 JSON 의 posthoc_coverage_at_error 에 진단으로만 있다."]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str),
                                             encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
