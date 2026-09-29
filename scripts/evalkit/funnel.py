# -*- coding: utf-8 -*-
"""노드 해소를 게이트 순서대로 읽는다 — RAG eval v2 의 F3(VIN-167).

    python -X utf8 -m evalkit.funnel <result.json> [--baseline <result.json>]

`run_nodes` 가 실행마다 이 보고를 결과에 싣는다. 여기 `main` 은 이미 있는 결과 파일을
다시 읽는 용도다(스토어를 다시 열지 않는다).

**게이트는 brief 가 실제로 지나는 순서다**: union(검색 node_hits 풀에 정답이 있나) →
exposed(brief 가 보여 주는 목록 안에 있나, `_BRIEF_CANDIDATES`) → first(1위인가) →
committed(확정했나). exposed 는 개수가 아니라 **brief 가 실제로 보여 준 목록**에
정답이 있는가다(순위 > 0) — 노출 수를 바꾸는 실험(X1)에서도 같은 자로 잰다. 문항마다 **처음 떨어진 게이트** 하나를 적는다. 층을 따로 재서
상관을 구하는 대신 이것으로 "어디서 잃었나"에 바로 답한다.
합집합은 brief 가 안에서 부르는 search 와 같은 깊이의 node_hits 다. node_hits 는 커서로
이어지지 않고 첫 페이지에 전부 온다(`vinc_search` 계약).

통계는 **같은 문항끼리 짝지어** 낸다(쌍 부트스트랩 백분위 구간 + McNemar 정확 검정).
두 실행의 구간을 따로 구해 겹치는지 보는 비교는 하지 않는다.
근거: [[vinc.plan.rag-eval-v2-0-9-6]] · [[vinc.research.rag-eval-report-review-2026-09-28]]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter

GATES = ("union", "exposed", "first", "committed")
BOOT_B, BOOT_SEED = 2000, 0


def _h(d: dict, k: int) -> float:
    # 새 실행은 int 키, JSON 에서 다시 읽은 결과는 str 키다.
    return float((d or {}).get(k, (d or {}).get(str(k), 0.0)))


def first_fail(r: dict) -> str:
    """답 있는 문항이 처음 떨어진 게이트. 다 지났으면 ``pass``.

    틀린 노드로 확정한 문항은 정답이 가려지므로 exposed(또는 union)에서 떨어진다.
    그것을 따로 보려면 :func:`outcome` 을 본다."""
    if not (r["union_rank"] or r["rank"]):
        return "union"
    if not r["rank"]:
        return "exposed"
    if not _h(r["hit"], 1):
        return "first"
    return "pass" if r["status"] == "resolved" else "committed"


def outcome(r: dict) -> str:
    if not r["answerable"]:
        return "false_resolved" if r["status"] == "resolved" else (r["status"] or "none")
    if r["status"] == "resolved":
        return "resolved_wrong" if r["wrong_node"] else "resolved_correct"
    return r["status"] or "none"


def ndcg_at(rank: int, k: int = 10, n_relevant: int = 1) -> float:
    """IDCG 는 **정답 목록(qrels)** 에서 구한다. 받은 목록에서 구하면 정답을 놓친 목록이
    벌을 안 받는다. 이 정답셋은 문항마다 정답이 하나라 IDCG 는 1 이다."""
    idcg = sum(1 / math.log2(i + 2) for i in range(min(n_relevant, k)))
    return (1 / math.log2(rank + 1)) / idcg if 0 < rank <= k else 0.0


def default_score(r: dict) -> float:
    """위험-커버리지 곡선의 확신 점수. 제품이 이미 쓰는 층(확정 > 고르기)을 먼저 놓고,
    층 안에서는 보여 준 첫 노드의 RRF 점수로 줄 세운다. X3(VIN-170)는 이 자리에 의미
    격차·토큰 차를 넣는다."""
    tier = {"resolved": 2, "choose": 1}.get(r["status"], 0)
    return tier + min(float(r.get("pred_rrf") or 0.0), 0.999)


def risk_coverage(rows: list[dict], score=default_score) -> dict:
    """보여 준 첫 노드를 답으로 내놓는다고 보고, 확신 순으로 하나씩 더 답할 때의 위험.

    답(예측)이 있는 문항은 확정·고르기뿐이다(none 은 기권이라 덮을 수 없다). 맞음은
    답 있는 문항에서 1위가 정답인 것, 답 없는 문항에 내놓은 답은 전부 틀림이다.
    AURC 는 덮은 점마다의 위험 평균이고, 커버리지 분모는 전체 문항이다."""
    n = len(rows)
    order = sorted((i for i, r in enumerate(rows) if r["status"] in ("resolved", "choose")),
                   key=lambda i: (-score(rows[i]), i))
    errs, points = 0, []
    for j, i in enumerate(order, 1):
        r = rows[i]
        errs += 0 if (r["answerable"] and _h(r["hit"], 1)) else 1
        points.append([round(j / n, 4), round(errs / j, 4)])
    committed = [r for r in rows if r["status"] == "resolved"]
    wrong = sum(1 for r in committed if not (r["answerable"] and _h(r["hit"], 1)))
    return {"n": n, "max_coverage": round(len(order) / n, 4) if n else 0.0,
            "aurc": round(sum(p[1] for p in points) / len(points), 4) if points else None,
            "operating_point": {"coverage": round(len(committed) / n, 4) if n else 0.0,
                                "risk": round(wrong / len(committed), 4) if committed else None},
            "points": points}


#: 승격 여백과 그 값에서 읽어 볼 문턱. 의미 쪽은 `_SEM_GAP` 주석의 표(1.29 시절 실측),
#: 토큰 쪽은 엄격 판정의 1·2. 1.30 런타임에서도 그 문턱이 서는지가 X3 의 둘째 질문이다.
PROMOTE_KEYS = {"sem_lead": (0.010, 0.025, 0.040), "sem_gap": (0.010, 0.025, 0.040),
                "tok_gap": (1, 2), "title_gap": (1, 2)}
#: 작동점을 찾는 둘째 선 — 외부 리포트가 쓰던 확정 조건부 오답 4/18. 첫째 선은 오늘의 위험.
LEAD_BAR = 4 / 18
CV_FOLDS, CV_REPS = 5, 20


def auroc(pos: list[float], neg: list[float]) -> float | None:
    """맞는 1위의 여백이 틀린 1위보다 클 확률(동점은 반). 0.5 면 가르지 못한다."""
    if not pos or not neg:
        return None
    s = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return s / (len(pos) * len(neg))


def _right(r: dict) -> bool:
    return bool(r["answerable"] and _h(r["hit"], 1))


def promote(rows: list[dict], key: str, t: float) -> list[dict]:
    """고르기이고 여백이 t 이상이면 보여 준 첫 노드로 확정한 것으로 바꾼 사본.
    brief 에 그 규칙을 넣었을 때의 답을 흉내 낸다(첫 노드가 상세에서 열린다고 본다)."""
    out = []
    for r in rows:
        v = (r.get("margin") or {}).get(key)
        if r["status"] == "choose" and v is not None and v >= t:
            r = {**r, "status": "resolved", "node": r.get("pred") or "",
                 "wrong_node": 1.0 if (r["answerable"] and not _right(r)) else 0.0,
                 "false_resolved": 0.0 if r["answerable"] else 1.0}
        out.append(r)
    return out


def _tally(rows: list[dict]) -> dict:
    res = [r for r in rows if r["status"] == "resolved"]
    ok = sum(1 for r in res if _right(r))
    fr = sum(1 for r in res if not r["answerable"])
    return {"resolved": len(res), "correct": ok, "wrong": len(res) - ok - fr,
            "false_resolved": fr, "risk": round((len(res) - ok) / len(res), 4) if res else None}


def _values(rows: list[dict], key: str) -> list[float]:
    return sorted({float(r["margin"][key]) for r in rows if r["status"] == "choose"
                   and (r.get("margin") or {}).get(key) is not None}, reverse=True)


def _best(rows: list[dict], key: str, bar: float) -> float | None:
    """위험이 bar 이하이면서 맞는 확정이 가장 많은 문턱. 하나도 늘지 않으면 None.
    같은 정답 수면 높은 문턱(덜 올리는 쪽)을 남긴다."""
    best, best_ok = None, _tally(rows)["correct"]
    for t in _values(rows, key):
        s = _tally(promote(rows, key, t))
        if s["risk"] is not None and s["risk"] <= bar + 1e-9 and s["correct"] > best_ok:
            best, best_ok = t, s["correct"]
    return best


def promotion_cv(rows: list[dict], key: str, bar: float) -> dict:
    """문턱을 고르는 행과 재는 행을 가른다(상태·답 여부로 층화한 5겹 × 20회, 씨앗 고정).
    같은 81행에서 고르고 재면 작동점이 낙관적이다 — 이 값이 판정에 쓰는 쪽이다."""
    base, got, ts = _tally(rows), Counter(), []
    for rep in range(CV_REPS):
        rng, strata, fold = random.Random(rep), {}, {}
        for i, r in enumerate(rows):
            strata.setdefault((r["answerable"], r["status"]), []).append(i)
        for idx in strata.values():
            rng.shuffle(idx)
            for j, i in enumerate(idx):
                fold[i] = j % CV_FOLDS
        for f in range(CV_FOLDS):
            t = _best([r for i, r in enumerate(rows) if fold[i] != f], key, bar)
            ts.append(t)
            if t is None:
                continue
            test = [r for i, r in enumerate(rows) if fold[i] == f]
            for a, b in zip(test, promote(test, key, t)):
                if a["status"] != b["status"]:
                    got["promoted"] += 1
                    got["correct" if _right(b) else
                        "wrong" if b["answerable"] else "false_resolved"] += 1
    per = {k: round(got[k] / CV_REPS, 2) for k in ("promoted", "correct", "wrong", "false_resolved")}
    tot, ok = base["resolved"] + per["promoted"], base["correct"] + per["correct"]
    known = sorted(t for t in ts if t is not None)
    return {"per_run": per, "correct_after": round(ok, 2),
            "risk_after": round((tot - ok) / tot, 4) if tot else None,
            "false_resolved_after": round(base["false_resolved"] + per["false_resolved"], 2),
            "folds_without_point": sum(1 for t in ts if t is None), "folds": len(ts),
            "threshold_median": known[len(known) // 2] if known else None}


def promotion(rows: list[dict], key: str, marks: tuple = ()) -> dict:
    """확정은 그대로 두고, 고르기 행을 여백 ``key`` 가 큰 것부터 확정으로 올릴 때의 곡선.

    맞음은 답 있는 문항에서 1위가 정답인 것이다. 틀림은 답 있는 문항의 오답(``wrong``)과
    답 없는 문항의 허위 확정(``false_resolved``)으로 갈라 적는다 — 합치면 정직성이 무너지는
    것이 안 보인다. 여백이 없는 행(None)은 올리지 않는다. 작동점은 위험이 선 이하인 문턱
    가운데 맞는 확정이 가장 많은 것이고, 짝지은 비교(:func:`paired`)와 교차검증
    (:func:`promotion_cv`)을 함께 싣는다."""
    start = _tally(rows)
    cuts = []
    for t in _values(rows, key):
        c = {"at": t, **_tally(promote(rows, key, t))}
        c["promoted"] = c["resolved"] - start["resolved"]
        cuts.append(c)
    today = (start["resolved"] - start["correct"]) / start["resolved"] if start["resolved"] else 0.0
    ops = {}
    for name, bar in (("today", today), ("lead_report_4_of_18", LEAD_BAR)):
        t = _best(rows, key, bar)
        ops[name] = {"bar": round(bar, 4), "threshold": t,
                     "point": next(c for c in cuts if c["at"] == t) if t is not None else None,
                     "paired": {m: v for m, v in paired(rows, promote(rows, key, t)).items()
                                if m in ("resolved_correct", "resolved_wrong", "false_resolved")}
                     if t is not None else None,
                     "cv": promotion_cv(rows, key, bar)}
    scored = [(float(r["margin"][key]), _right(r)) for r in rows if r["status"] == "choose"
              and (r.get("margin") or {}).get(key) is not None]
    pos, neg = [v for v, ok in scored if ok], [v for v, ok in scored if not ok]
    rng, boots = random.Random(BOOT_SEED), []
    if pos and neg:
        for _ in range(BOOT_B):
            s = [scored[rng.randrange(len(scored))] for _ in scored]
            a = auroc([v for v, ok in s if ok], [v for v, ok in s if not ok])
            if a is not None:
                boots.append(a)
        boots.sort()
    au = auroc(pos, neg)
    return {"key": key, "choose_rows": sum(1 for r in rows if r["status"] == "choose"),
            "with_value": len(scored), "right_first": len(pos),
            "base_precision": round(len(pos) / len(scored), 4) if scored else None,
            "auroc": round(au, 4) if au is not None else None,
            "auroc_ci95": [round(boots[int(0.025 * len(boots))], 4),
                           round(boots[int(0.975 * len(boots)) - 1], 4)] if boots else None,
            "start": start, "operating_points": ops,
            "at_marks": {str(m): next((c for c in reversed(cuts) if c["at"] >= m),
                                      {"promoted": 0, **start}) for m in marks},
            "cuts": cuts}


def report(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    n = len(ans)
    reach = {"union": [r for r in ans if r["union_rank"] or r["rank"]]}
    reach["exposed"] = [r for r in ans if r["rank"]]
    reach["first"] = [r for r in ans if _h(r["hit"], 1)]
    reach["committed"] = [r for r in reach["first"] if r["status"] == "resolved"]
    fails = [first_fail(r) for r in ans]

    def hist(key: str) -> dict:
        d: dict = {}
        for r, f in zip(ans, fails):
            d.setdefault(r[key], Counter())[f] += 1
        return {k: dict(v) for k, v in sorted(d.items())}

    resolved = [r for r in rows if r["status"] == "resolved"]
    correct = len(reach["committed"])
    return {
        "answerable": n,
        "gates": {g: {"n": len(reach[g]), "rate": round(len(reach[g]) / n, 4) if n else None}
                  for g in GATES},
        "first_fail": dict(Counter(fails)),
        "first_fail_by_kind": hist("gold_kind"),
        "first_fail_by_axis": hist("axis"),
        # 합집합에 든 정답을 어느 팔이 올렸나. 옛 결과(이 필드 이전)에는 없다.
        "union_arms": dict(Counter("+".join(r.get("union_arms") or []) or "?"
                                   for r in reach["union"])),
        "outcomes": dict(Counter(outcome(r) for r in rows)),
        "unanswerable_by_kind": {k: dict(Counter(outcome(r) for r in una
                                                 if r["unanswerable_kind"] == k))
                                 for k in sorted({r["unanswerable_kind"] for r in una})},
        "commit": {"coverage": round(len(resolved) / len(rows), 4) if rows else None,
                   "precision": round(correct / len(resolved), 4) if resolved else None,
                   "precision_answerable": round(correct / len([r for r in resolved
                                                               if r["answerable"]]), 4)
                   if any(r["answerable"] for r in resolved) else None,
                   "resolved": len(resolved), "correct": correct},
        "ndcg@10": {"union": round(sum(ndcg_at(r["union_rank"]) for r in ans) / n, 4) if n else None,
                    "exposed": round(sum(ndcg_at(r["rank"]) for r in ans) / n, 4) if n else None,
                    "idcg": "qrels (one gold per question)"},
        "risk_coverage": risk_coverage(rows),
        "response_bytes": _bytes_summary(rows),
        # 여백이 실린 실행에서만(기준 경로). X3 가 읽는 자리다.
        "promotion": {k: promotion(rows, k, marks) for k, marks in PROMOTE_KEYS.items()
                      if any((r.get("margin") or {}).get(k) is not None for r in rows)},
    }


def _bytes_summary(rows: list[dict]) -> dict | None:
    b = sorted(r["resp_bytes"] for r in rows if r.get("resp_bytes") is not None)
    if not b:
        return None
    return {"mean": round(sum(b) / len(b), 1), "median": b[len(b) // 2], "max": b[-1]}


#: 짝지어 비교하는 문항별 0/1 값 — (모집단, 값).
METRICS = {
    "in_union": ("answerable", lambda r: 1.0 if (r["union_rank"] or r["rank"]) else 0.0),
    "exposed": ("answerable", lambda r: 1.0 if r["rank"] else 0.0),
    "exposed_r1": ("answerable", lambda r: _h(r["hit"], 1)),
    "resolved_correct": ("answerable",
                         lambda r: 1.0 if r["status"] == "resolved" and not r["wrong_node"] else 0.0),
    "resolved_wrong": ("answerable", lambda r: float(r["wrong_node"])),
    "false_resolved": ("unanswerable", lambda r: float(r["false_resolved"])),
}


def mcnemar_p(b: int, c: int) -> float:
    """정확 이항 McNemar, 양측. b·c 는 두 방향의 뒤집힘 수다."""
    m = b + c
    if not m:
        return 1.0
    tail = sum(math.comb(m, k) for k in range(min(b, c) + 1)) / 2 ** m
    return round(min(1.0, 2 * tail), 4)


def paired(base: list[dict], run: list[dict]) -> dict:
    """같은 문항끼리 기준선과 비교한다. 문항 목록이 다르면 비교를 거부한다."""
    if [r["query"] for r in base] != [r["query"] for r in run]:
        raise ValueError("기준선과 문항 목록이 다르다 — 짝지을 수 없다")
    rng = random.Random(BOOT_SEED)
    out: dict = {"method": f"paired percentile bootstrap (B={BOOT_B}, seed={BOOT_SEED}) "
                           "+ exact McNemar on per-question flips"}
    for name, (pop, val) in METRICS.items():
        idx = [i for i, r in enumerate(base) if r["answerable"] == (pop == "answerable")]
        a = [val(base[i]) for i in idx]
        b = [val(run[i]) for i in idx]
        m = len(idx)
        d = [y - x for x, y in zip(a, b)]
        boots = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(BOOT_B)) if m else []
        lost = sum(1 for x, y in zip(a, b) if x and not y)
        gained = sum(1 for x, y in zip(a, b) if y and not x)
        out[name] = {"n": m, "base": sum(a), "run": sum(b),
                     "delta": round(sum(d) / m, 4) if m else None,
                     "ci95": [round(boots[int(0.025 * BOOT_B)], 4),
                              round(boots[int(0.975 * BOOT_B) - 1], 4)] if boots else None,
                     "lost": lost, "gained": gained, "mcnemar_p": mcnemar_p(lost, gained)}
    if all(r.get("resp_bytes") is not None for r in base + run):
        # 응답 크기는 0/1 이 아니라 연속값이라 뒤집힘 대신 문항별 차의 평균을 짝지어 잰다.
        d = [b["resp_bytes"] - a["resp_bytes"] for a, b in zip(base, run)]
        boots = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(BOOT_B))
        out["response_bytes"] = {"n": len(d), "delta_mean": round(sum(d) / len(d), 1),
                                 "ci95": [round(boots[int(0.025 * BOOT_B)], 1),
                                          round(boots[int(0.975 * BOOT_B) - 1], 1)],
                                 "max_increase": max(d)}
    return out


def _rows(path: str) -> list[dict]:
    runs = json.loads(open(path, encoding="utf-8").read())["runs"]
    return next(iter(runs.values()))["per_query"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("result")
    ap.add_argument("--baseline")
    a = ap.parse_args(argv)
    rows = _rows(a.result)
    out = {"funnel": report(rows)}
    if a.baseline:
        out["paired"] = paired(_rows(a.baseline), rows)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
