# -*- coding: utf-8 -*-
"""노드 해소를 게이트 순서대로 읽는다 — RAG eval v2 의 F3(VIN-167).

    python -X utf8 -m evalkit.funnel <result.json> [--baseline <result.json>]

`run_nodes` 가 실행마다 이 보고를 결과에 싣는다. 여기 `main` 은 이미 있는 결과 파일을
다시 읽는 용도다(스토어를 다시 열지 않는다).

**게이트는 brief 가 실제로 지나는 순서다**: union(검색 node_hits 풀에 정답이 있나) →
exposed(brief 가 보여 주는 셋 안에 있나, `_BRIEF_CANDIDATES`) → first(1위인가) →
committed(확정했나). 문항마다 **처음 떨어진 게이트** 하나를 적는다. 층을 따로 재서
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
EXPOSED = 3          # `api.query._BRIEF_CANDIDATES` — 결과 헤더의 constants 가 코드 값을 싣는다
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
    if not _h(r["hit"], EXPOSED):
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


def report(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    n = len(ans)
    reach = {"union": [r for r in ans if r["union_rank"] or r["rank"]]}
    reach["exposed"] = [r for r in ans if _h(r["hit"], EXPOSED)]
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
    }


#: 짝지어 비교하는 문항별 0/1 값 — (모집단, 값).
METRICS = {
    "in_union": ("answerable", lambda r: 1.0 if (r["union_rank"] or r["rank"]) else 0.0),
    "exposed_r3": ("answerable", lambda r: _h(r["hit"], EXPOSED)),
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
