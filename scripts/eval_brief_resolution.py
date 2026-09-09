#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N1 — `brief` 가 질문이 가리키는 **노드**를 맞히는가.

**왜 이것이 첫 항목인가.** 그 아래 모든 측정(재료 품질·답변 정확도)이 해소된 노드 위에
선다. 노드가 틀리면 N2 는 그래프 품질이라 부르며 **해소 정확도를 재게 된다.**

**여태 없던 것.** 이 리포의 평가셋은 전부 문서나 절을 정답으로 단다
(`evalset_human` 의 `doc_id`, `evalset.json` 의 제목 질의 …). 노드 단위 채점은
2026-08-30 재실행의 14문항에서 손으로 한 번 했을 뿐이고 하네스가 없었다.
근거: [[record:no-node-level-gold-set-exists-2026-09-09]]

**무엇을 재는가.** `api.query.brief` 의 반환이 지표와 1:1이라 새 계약이 필요 없다:

    status      resolved | choose | none
    node.id     확정된 노드 · node.kind 종류
    candidates  확정 못 했을 때의 후보(순위 있음)
    grounds     답변 재료의 근거 id 들
    fell_through / unreadable   매치했으나 못 연 후보

⚠ **답 가능/불가를 가른다.** 그래프에 답이 없는 질문에 `resolved` 를 주는 것은 낮은
정확도가 아니라 **다른 고장**이다(세 호스트에서 재현됐다). 그래서 `answerable: false`
행의 정답은 노드가 아니라 "none 또는 choose" 다.

⚠ **정답 질문은 그래프 밖에 산다.** 질문을 노드 제목에 인용하면 그 노드가 그 질문을
영원히 이긴다(smoke 2026-09-06 A5). 이 파일도 예시 질문을 담지 않는다.

정답 파일(JSONL 또는 JSON 배열), 한 줄에 하나:

    {"query": "...", "gold_node_id": "concept:...", "gold_kind": "concept",
     "language_axis": "ko", "answerable": true, "gold_doc": "...", "note": "..."}

    answerable=false 행은 `gold_node_id` 를 비운다.

사용:
    python scripts/eval_brief_resolution.py --data <store> --owner <owner> \\
        --gold scripts/fixtures/brief_gold.jsonl --out out.json

종료코드 1: 채점할 것이 없다(정답 0건이거나 전 행이 실패).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from api import query as q            # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

#: 후보 순위를 이만큼까지 본다. R@10 이 계획의 마지막 칸이다.
RANKS = (1, 3, 10)


def load_gold(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        raw = pathlib.Path(p).read_text(encoding="utf-8").strip()
        if not raw:
            continue
        if raw.lstrip().startswith("["):
            rows.extend(json.loads(raw))
        else:
            rows.extend(json.loads(l) for l in raw.splitlines() if l.strip())
    return rows


def check_gold(rows: list[dict]) -> list[str]:
    """정답셋 자체를 먼저 검사한다. **낡은 평가셋은 측정하지 않는 측정이다** —
    `eval_tooluse.py` 가 같은 이유로 존재한다."""
    bad: list[str] = []
    for i, r in enumerate(rows):
        if not r.get("query"):
            bad.append(f"{i}: query 가 비었다")
        if r.get("answerable", True):
            if not r.get("gold_node_id"):
                bad.append(f"{i}: answerable 인데 gold_node_id 가 없다")
        elif r.get("gold_node_id"):
            bad.append(f"{i}: answerable=false 인데 gold_node_id 가 있다 — 둘 중 하나가 틀렸다")
        if r.get("language_axis") not in (None, "en", "ko", "ko2en", "en2ko"):
            bad.append(f"{i}: language_axis 가 낯설다: {r.get('language_axis')}")
    return bad


def gold_present(store, rows: list[dict], owner: str) -> list[str]:
    """정답 노드가 이 스토어에 실재하는가. **없으면 그 행은 필연 오답이고, 점수는
    그냥 낮게 나올 뿐이라 '해소가 나쁘다'로 오독된다**(조용한 0점 — 코퍼스 씨딩이
    같은 함정을 이미 한 번 냈다)."""
    missing = []
    for r in rows:
        nid = r.get("gold_node_id")
        if not nid:
            continue
        probe = q.execute(q.seed_lookup_plan(nid, owner=owner), None, store=store)
        if not (probe.get("executed")
                and any(x.get("id") == nid for x in probe.get("rows", []))):
            missing.append(nid)
    return missing


def rank_of(gold: str, res: dict) -> int:
    """정답 노드의 순위. 확정됐으면 1위, 아니면 후보 목록에서 찾는다. 0 = 없음."""
    node = (res.get("node") or {}).get("id")
    if node and node == gold:
        return 1
    for i, c in enumerate(res.get("candidates") or []):
        if (c.get("id") if isinstance(c, dict) else c) == gold:
            return i + 1
    return 0


def grade_one(res: dict, row: dict) -> dict:
    status = res.get("status")
    gold = row.get("gold_node_id") or ""
    answerable = bool(row.get("answerable", True))
    node = (res.get("node") or {}).get("id") or ""
    rank = rank_of(gold, res) if gold else 0
    out = {
        "query": row.get("query"), "axis": row.get("language_axis") or "?",
        "gold_kind": row.get("gold_kind") or "?", "answerable": answerable,
        "status": status, "node": node, "rank": rank,
        "resolved_via": res.get("resolved_via") or "",
        "grounds": len(res.get("grounds") or []),
        "unreadable": bool(res.get("unreadable")),
        "fell_through": bool(res.get("fell_through")),
    }
    if answerable:
        out["hit"] = {k: 1.0 if 0 < rank <= k else 0.0 for k in RANKS}
        # 확정했는데 정답이 아니다 — 모르는 것과 **다른 고장**이다.
        out["wrong_node"] = 1.0 if (status == "resolved" and node != gold) else 0.0
        out["false_resolved"] = 0.0
    else:
        out["hit"] = {k: 0.0 for k in RANKS}
        out["wrong_node"] = 0.0
        # 답이 없는 질문에 확정을 준다 — 정확도 문제가 아니라 정직성 문제다.
        out["false_resolved"] = 1.0 if status == "resolved" else 0.0
    out["chose"] = 1.0 if status == "choose" else 0.0
    return out


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]

    def mean(xs):
        return round(statistics.fmean(xs), 4) if xs else None

    out: dict = {
        "n": len(rows), "answerable": len(ans), "unanswerable": len(una),
        "node_recall": {f"R@{k}": mean([r["hit"][k] for r in ans]) for k in RANKS},
        "wrong_node_rate": mean([r["wrong_node"] for r in ans]),
        "false_resolved_rate": mean([r["false_resolved"] for r in una]),
        "choose_rate": mean([r["chose"] for r in rows]),
        "unreadable_rate": mean([1.0 if r["unreadable"] else 0.0 for r in rows]),
        "fell_through_rate": mean([1.0 if r["fell_through"] else 0.0 for r in rows]),
        "status_census": dict(Counter(r["status"] for r in rows)),
    }
    # 계층별 — 계획이 요구하는 축 둘. 종류별은 Episode 후보성이 여기서 드러난다.
    for key, label in (("axis", "by_axis"), ("gold_kind", "by_kind")):
        d: dict = {}
        for r in ans:
            b = d.setdefault(r[key], {"n": 0, **{f"R@{k}": [] for k in RANKS}})
            b["n"] += 1
            for k in RANKS:
                b[f"R@{k}"].append(r["hit"][k])
        out[label] = {k: {"n": v["n"], **{f"R@{r}": mean(v[f"R@{r}"]) for r in RANKS}}
                      for k, v in sorted(d.items())}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--gold", action="append", required=True, help="정답 파일(반복 가능)")
    ap.add_argument("--out", help="결과 JSON 경로(없으면 요약만 stdout)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--allow-missing-gold", action="store_true",
                    help="정답 노드가 스토어에 없어도 진행(기본은 거부 — 조용한 0점 방지)")
    a = ap.parse_args(argv)

    rows = load_gold(a.gold)
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print("정답이 0건이다 — 잴 것이 없다", file=sys.stderr)
        return 1
    for b in check_gold(rows):
        print(f"정답셋 오류 — {b}", file=sys.stderr)
    if check_gold(rows):
        return 1

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    missing = gold_present(store, rows, a.owner)
    if missing and not a.allow_missing_gold:
        print(f"정답 노드 {len(missing)}건이 이 스토어에 없다 — 평가가 무효다. "
              f"예: {missing[:3]}", file=sys.stderr)
        store.close()
        return 1

    graded, t0 = [], time.perf_counter()
    for r in rows:
        # 🔴 `query_embedding` 을 안 넘긴다. 이 스토어에는 벡터가 없을 수 있고(데스크탑
        # 스냅샷은 ChunkVec 0), 넘기면 없는 팔을 있는 척 재게 된다. N1 이 재는 것은
        # 해소이지 벡터 검색이 아니다 — 그 사실을 결과에 적는다.
        res = q.brief(r["query"], owner=a.owner, store=store)
        graded.append(grade_one(res, r))
    wall = time.perf_counter() - t0
    store.close()

    summ = summarize(graded)
    summ["wall_s"] = round(wall, 2)
    summ["arms_note"] = ("query_embedding 미전달 — 의미 팔 없음. 이 수치는 어휘·그래프 "
                         "경로만의 해소 정확도다")
    summ["missing_gold_nodes"] = missing
    out = {"kind": "brief.resolution", "store": a.data, "owner": a.owner,
           "gold_files": a.gold, "summary": summ, "per_query": graded}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
