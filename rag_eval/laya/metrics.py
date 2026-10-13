# -*- coding: utf-8 -*-
"""순수 지표. 표준 라이브러리만 쓴다(테스트가 모델 venv 없이 돈다). 근거: [[vinc.plan.laya-test-method-2026-10-07 §5]]"""
from __future__ import annotations

import math


def softmax(v) -> list[float]:
    m = max(v)
    e = [math.exp(x - m) for x in v]
    s = sum(e)
    return [x / s for x in e]


def _conf_correct(p, y):
    conf = [max(q, 1 - q) for q in p]
    correct = [1.0 if (q >= 0.5) == bool(t) else 0.0 for q, t in zip(p, y)]
    return conf, correct


def brier(p, y) -> float:
    return sum((q - t) ** 2 for q, t in zip(p, y)) / len(p) if len(p) else float("nan")


def ece(p, y, bins: int = 15) -> float:
    """이진 noul 의 ECE. confidence = max(p, 1-p), correct = (p>=0.5)==y. laya.ece_score 와 같은 식."""
    if not len(p):
        return float("nan")
    conf, correct = _conf_correct(p, y)
    n = len(p)
    total = 0.0
    for i in range(bins):
        lo, hi = 0.5 + 0.5 * i / bins, 0.5 + 0.5 * (i + 1) / bins
        idx = [j for j, c in enumerate(conf) if ((lo <= c <= hi) if i == 0 else (lo < c <= hi))]
        if idx:
            mc = sum(conf[j] for j in idx) / len(idx)
            ma = sum(correct[j] for j in idx) / len(idx)
            total += len(idx) / n * abs(mc - ma)
    return float(total)


def auroc(p, y) -> float:
    """Mann-Whitney, 동점은 평균 순위. 한 클래스뿐이면 nan."""
    pos = [q for q, t in zip(p, y) if t == 1]
    neg = [q for q, t in zip(p, y) if t == 0]
    if not pos or not neg:
        return float("nan")
    allp = sorted((v, i) for i, v in enumerate(pos + neg))
    ranks = [0.0] * len(allp)
    i = 0
    while i < len(allp):
        j = i
        while j + 1 < len(allp) and allp[j + 1][0] == allp[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[allp[k][1]] = r
        i = j + 1
    rank_pos = sum(ranks[: len(pos)])
    return float((rank_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def selective_curve(p, y, thresholds=None) -> list[dict]:
    """confidence 문턱별 (coverage, accuracy). 문턱 아래는 기권."""
    conf, correct = _conf_correct(p, y)
    ths = thresholds if thresholds is not None else [round(0.5 + 0.025 * i, 3) for i in range(20)]
    rows = []
    for t in ths:
        keep = [j for j, c in enumerate(conf) if c >= t]
        rows.append({"threshold": float(t), "coverage": len(keep) / len(p) if len(p) else 0.0,
                     "accuracy": sum(correct[j] for j in keep) / len(keep) if keep else float("nan")})
    return rows


def coverage_at_error(p, y, target_error: float) -> dict:
    """오류율 <= target 을 지키는 가장 낮은 문턱과 그때의 coverage. 보수적: 못 지키면 coverage 0."""
    for row in selective_curve(p, y):
        acc = row["accuracy"]
        if acc == acc and (1 - acc) <= target_error:
            return {"threshold": row["threshold"], "coverage": row["coverage"], "error": 1 - acc}
    return {"threshold": 1.0, "coverage": 0.0, "error": float("nan")}


def asymmetry(p_ab, p_ba) -> dict:
    d = [abs(a - b) for a, b in zip(p_ab, p_ba)]
    return {"mean": sum(d) / len(d) if d else float("nan"),
            "over_0_2": sum(1 for x in d if x > 0.2) / len(d) if d else float("nan")}


def majority_baseline(y) -> list[float]:
    m = 1.0 if (sum(y) / len(y) >= 0.5 if len(y) else False) else 0.0
    return [m] * len(y)
