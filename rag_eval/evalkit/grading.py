# -*- coding: utf-8 -*-
"""노드 해소 채점 — 전송과 무관한 순수 함수들.

`rag_eval/brief_resolution.py`(인프로세스 기준 하네스)에서 **그대로 옮겨 왔다**. 같은
채점을 CLI·REST v1 경로(`evalkit.run_nodes`)도 써야 세 경로의 문항별 대조가 채점 차이가
아니라 전송 차이만 잰다. 옛 하네스는 이 이름들을 다시 내보내므로 부르는 쪽은 그대로다.
근거: [[vinc.plan.rag-eval-v2-0-9-6]]
"""
from __future__ import annotations

import json
import pathlib
import statistics
from collections import Counter

#: 후보 순위를 이만큼까지 본다. R@10 이 계획의 마지막 칸이다.
RANKS = (1, 3, 10)

#: 이 아래를 "제목 낱말을 거의 안 남긴 질문"으로 본다. 축끼리 비교할 때 이 층만 보면
#: 저작 차이가 빠진다 — 2026-09-09 에 en 중앙값이 ko 의 두 배였다.
LOW_REUSE = 0.34


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


#: 답 불가에는 **두 종류**가 있고 섞으면 한 수가 두 고장을 뜻한다.
#:
#: ``no-topic`` 은 그래프가 그 주제를 아예 안 담은 경우다(사무실 임대료). ``attribute-absent``
#: 는 주제 노드가 **있는데** 물은 속성이 없는 경우다("이 문장을 누가 썼나" — 그 개념은
#: 제목 토큰이 완전히 맞아 1위로 오지만 저자는 저작된 적이 없다). 뒤엣것이 훨씬 어렵고,
#: 검색 문헌이 relevance ≠ answerability 라 부르는 자리다.
#:
#: 이 갈래가 없을 때 이 정답셋의 답 불가는 6문항이었고 **그중 셋이 어휘 겹침 0.00** 이라
#: 후보 자체가 안 생겼다 — 거저 통과하는 문항으로 정직성을 재고 있었다.
#: 근거: [[record:the-unanswerable-gold-was-too-easy-to-measure-honesty-2026-09-10]]
UNANSWERABLE_KINDS = frozenset({"no-topic", "attribute-absent"})


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
        if not r.get("answerable", True) and r.get("unanswerable_kind") not in UNANSWERABLE_KINDS:
            # 종류가 없으면 두 고장이 한 수에 섞인다(:data:`UNANSWERABLE_KINDS` 주석).
            bad.append(f"{i}: answerable=false 인데 unanswerable_kind 가 "
                       f"{r.get('unanswerable_kind')!r} 다 — {sorted(UNANSWERABLE_KINDS)} 중 하나여야 한다")
        if r.get("language_axis") not in (None, "en", "ko", "ko2en", "en2ko"):
            bad.append(f"{i}: language_axis 가 낯설다: {r.get('language_axis')}")
    return bad


def rank_of(gold: str, res: dict) -> int:
    """정답 노드의 순위. 확정됐으면 1위, 아니면 후보 목록에서 찾는다. 0 = 없음."""
    node = (res.get("node") or {}).get("id")
    if node and node == gold:
        return 1
    for i, c in enumerate(res.get("candidates") or []):
        if (c.get("id") if isinstance(c, dict) else c) == gold:
            return i + 1
    return 0


def union_rank_of(gold: str, hits: list) -> int:
    """후보 **합집합**에서의 순위. `rank_of` 와 다른 것을 센다.

    ⚠ `brief` 는 확정했을 때 `candidates` 를 싣지 않는다(계약대로다 — 확정했으면 후보를
    내밀 이유가 없다). 그래서 `rank_of` 만으로 R@3·R@10 을 내면 **확정한 질의는 전부
    순위 0** 이 되어, 실제로는 2위에 있던 정답이 "없음"으로 세어진다. 첫 런에서 R@1 과
    R@3 이 거의 같게 나온 것이 그 그림자였다(0.1622 대 0.1892, 합집합으로는 0.3243).
    그러니 노출 순위와 합집합 순위를 **둘 다** 싣는다: 앞은 부르는 쪽이 보는 것,
    뒤는 이 파이프라인이 가진 천장.
    근거: [[record:the-candidate-union-not-the-ranking-is-what-caps-node-resolution-2026-09-09]]"""
    for i, h in enumerate(hits or []):
        if (h.get("id") if isinstance(h, dict) else h) == gold:
            return i + 1
    return 0


def margins(res: dict, pred: str, hits: list, sem: list | None) -> dict:
    """고르기 1위를 확정으로 올릴지 가를 여백들(X3, VIN-170). 판정에 쓰지 않고 잰다.

    - ``tok_gap``·``title_gap``: brief 가 보여 준 1·2위 후보의 토큰 차(엄격 판정이 보는 값)
    - ``sem_gap``: 의미 팔 원시 목록의 1-2위 거리 차(`_SEM_GAP` 이 보는 값)
    - ``sem_lead``: 보여 준 첫 노드가 의미 팔에서 나머지보다 얼마나 가까운가. 그 노드가
      의미 1위면 ``sem_gap`` 과 같고, 아니면 음수, 의미 팔에 없으면 None
    - ``sem_dist_missing``: node_hits 에서 의미 팔에 들었는데 `distance` 가 빠진 행 수
      (바깥에서 이 격차를 못 읽는 정도)
    근거: [[vinc.plan.rag-eval-v2-0-9-6]]"""
    c = [x for x in res.get("candidates") or [] if isinstance(x, dict)]
    out: dict = {"tok_gap": None, "title_gap": None, "sem_gap": None, "sem_lead": None,
                 "sem_dist_missing": sum(1 for h in hits or [] if isinstance(h, dict)
                                         and "semantic" in (h.get("arms") or [])
                                         and h.get("distance") is None)}
    if len(c) > 1:
        out["tok_gap"] = int(c[0].get("hit_tokens") or 0) - int(c[1].get("hit_tokens") or 0)
        out["title_gap"] = int(c[0].get("title_tokens") or 0) - int(c[1].get("title_tokens") or 0)
    if sem is not None:
        d = [(h.get("id"), float(h["distance"])) for h in sem if h.get("distance") is not None]
        if len(d) > 1:
            out["sem_gap"] = round(d[1][1] - d[0][1], 6)
        mine = [x for i, x in d if i == pred]
        rest = [x for i, x in d if i != pred]
        if mine and rest:
            out["sem_lead"] = round(min(rest) - mine[0], 6)
    return out


def grade_one(res: dict, row: dict, hits: list | None = None, sem: list | None = None) -> dict:
    status = res.get("status")
    gold = row.get("gold_node_id") or ""
    answerable = bool(row.get("answerable", True))
    node = (res.get("node") or {}).get("id") or ""
    rank = rank_of(gold, res) if gold else 0
    urank = union_rank_of(gold, hits) if gold else 0
    by_id = {h.get("id"): h for h in hits or [] if isinstance(h, dict)}
    cands = res.get("candidates") or []
    first = cands[0] if cands else {}
    # 보여 준 첫 노드 — 확정이면 그 노드, 고르기면 첫 후보. 깔때기의 확신 점수가 쓴다.
    pred = node or (first.get("id") if isinstance(first, dict) else first) or ""
    out = {
        "query": row.get("query"), "axis": row.get("language_axis") or "?",
        "gold_kind": row.get("gold_kind") or "?", "answerable": answerable,
        "unanswerable_kind": row.get("unanswerable_kind") or "",
        "status": status, "node": node, "rank": rank, "union_rank": urank,
        "title_reuse": row.get("_title_reuse", 0.0),
        "resolved_via": res.get("resolved_via") or "",
        "grounds": len(res.get("grounds") or []),
        "unreadable": bool(res.get("unreadable")),
        "fell_through": bool(res.get("fell_through")),
        "union_arms": sorted((by_id.get(gold) or {}).get("arms") or []) if urank else [],
        "pred": pred,
        "pred_rrf": float((by_id.get(pred) or {}).get("rrf_score") or 0.0),
        "margin": margins(res, pred, hits or [], sem),
        # 이 경로가 받은 brief 응답의 크기. 노출을 넓히는 비용이 여기서 보인다(X1).
        "resp_bytes": len(json.dumps(res, ensure_ascii=False).encode("utf-8")),
    }
    if answerable:
        out["hit"] = {k: 1.0 if 0 < rank <= k else 0.0 for k in RANKS}
        out["uhit"] = {k: 1.0 if 0 < urank <= k else 0.0 for k in RANKS}
        # 확정했는데 정답이 아니다 — 모르는 것과 **다른 고장**이다.
        out["wrong_node"] = 1.0 if (status == "resolved" and node != gold) else 0.0
        out["false_resolved"] = 0.0
    else:
        out["hit"] = {k: 0.0 for k in RANKS}
        out["uhit"] = {k: 0.0 for k in RANKS}
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
        # 합집합 회수율 — 이 파이프라인의 천장. 노출 회수율과의 차이가 곧 "확정 뒤에
        # 가려진 정답"의 크기다. R@10 은 후보 목록이 top_k 로 잘리므로 그 길이가 상한이다.
        "union_recall": {f"R@{k}": mean([r["uhit"][k] for r in ans]) for k in RANKS},
        "gold_in_union_rate": mean([1.0 if r["union_rank"] else 0.0 for r in ans]),
        "wrong_node_rate": mean([r["wrong_node"] for r in ans]),
        "false_resolved_rate": mean([r["false_resolved"] for r in una]),
        # 종류별로 갈라 낸다 — 합친 수는 두 고장의 가중평균이라 어느 쪽이 움직였는지 못 본다.
        "false_resolved_by_kind": {
            k: {"n": len([r for r in una if r["unanswerable_kind"] == k]),
                "rate": mean([r["false_resolved"] for r in una
                              if r["unanswerable_kind"] == k])}
            for k in sorted(UNANSWERABLE_KINDS)},
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
    # 축을 비교하려면 겹침을 함께 봐야 한다 — 이 둘이 떨어져 있으면 교란이 숨는다.
    reuse: dict = {}
    for r in ans:
        b = reuse.setdefault(r["axis"], {"n": 0, "reuse": [], "in_union": 0,
                                         "low_n": 0, "low_in_union": 0})
        b["n"] += 1
        b["reuse"].append(r["title_reuse"])
        hit = 1 if r["union_rank"] else 0
        b["in_union"] += hit
        if r["title_reuse"] <= LOW_REUSE:
            b["low_n"] += 1
            b["low_in_union"] += hit
    out["by_axis_reuse"] = {
        k: {"n": v["n"], "median_title_reuse": round(statistics.median(v["reuse"]), 4),
            "gold_in_union": f'{v["in_union"]}/{v["n"]}',
            f"gold_in_union_at_reuse_le_{LOW_REUSE}": f'{v["low_in_union"]}/{v["low_n"]}'}
        for k, v in sorted(reuse.items())}
    return out
