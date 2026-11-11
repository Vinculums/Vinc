#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""노드 종류(kind) 기준선 두 개 — "지금 유효한 것을 고르는가"와 "종류별 목록이 맞는가".

**무엇을 재는가.**

1. 현재 유효 고르기(current pick). 서로 대체(SUPERSEDES)한 주장 여럿 중 지금 유효한 것을
   정답으로 둔 질문에서, search 의 node_hits 와 brief 가 정답을 몇 번째에 두는가.
   대체된 쪽(옛 주장)이 정답보다 앞에 서는가도 함께 센다. 모드 셋(기본·current·historical)을 잰다.
2. 종류별 목록(kind lists). "X 에 관한 결정"·"Y 를 다룬 사건" 같은 요청에서 상위 k 의
   정밀도와 재현율. 비교하는 팔:
     - `text`    종류를 질문 글자에만 적는다(필터 없음, 오늘 호출자가 할 수 있는 것).
     - `client`  넓게(limit 100) 받아 호출부가 id 접두로 거른 뒤 k 개.
     - `oracle`  종류로 먼저 거르고 그다음 순위를 매긴다(세 노드 팔 전부, 자르기 전).
   `oracle` 은 `kinds` 필터가 없는 동안 하네스가 흉내 낸 것이다. 필터가 생기면 그 결과가
   이 흉내와 행마다 같아야 한다.

**정답은 그래프 자신의 간선에서 온다.** 목록 정답은 Decision/Episode 의 ABOUT, Record 의
ATTACHES_TO 가 가리키는 개념이다. 간선이 빠진 관련 노드는 정답에 없으므로 정밀도는 낮게
나온다(하한). 현재 유효 정답은 과제 파일의 `sources.support` 이고, 대체된 쪽은 그 노드에서
SUPERSEDES 를 따라 스토어에서 찾는다.

**종류가 순위에 새는 자리**(누수 스위치, :data:`LEAKS`)는 하나씩 꺼 보고 상위 5가 바뀐
질문 수를 센다. 제품 코드는 고치지 않고 이 프로세스 안에서만 바꾼다.

사용:
    python -X utf8 -m rag_eval.kind_eval --data <store copy> --owner <owner> \\
        --onnx <encoder.onnx> [--tasks <tasks.json>] [--model BAAI/bge-m3] --out <dir>

모델 호출·답 생성·판정은 하지 않는다. 검색 지표만 낸다.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import random
import sys
import time
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 노드 종류 → (라벨, PK 열). 목록 정답과 대체 계보를 읽을 때 쓴다.
LABELS = {"concept": ("Concept", "concept_id"), "record": ("Record", "record_id"),
          "decision": ("Decision", "decision_id"), "episode": ("Episode", "episode_id")}

#: 목록 정답의 간선 — 그 종류의 노드가 주제 개념을 가리키는 간선 이름.
LIST_EDGES = {"decision": "ABOUT", "episode": "ABOUT", "record": "ATTACHES_TO"}

#: 종류를 글자로 적은 질문 틀. 오늘은 이것이 호출자가 종류를 말할 유일한 길이다.
KIND_QUERY = {"decision": "decisions about {t}", "episode": "which episodes touched {t}",
              "record": "records about {t}"}

KS = (5, 10)
CLIENT_LIMIT = 100          # vinc_search 의 limit 최댓값


def kind_of(node_id: str) -> str:
    return str(node_id or "").split(":", 1)[0]


# ── 질문셋 ───────────────────────────────────────────────────────────────────

def _strip(store_mod, v):
    return store_mod.strip(str(v or ""))


def retired_ids(store, owner: str) -> set[str]:
    """SUPERSEDES 의 옛 끝(대체된 노드) 전부."""
    from store import pkns
    out: set[str] = set()
    for label in ("Record", "Decision", "Concept"):
        pk = LABELS[label.lower()][1]
        for r in store.run(f"MATCH (a:{label})-[:SUPERSEDES]->(b:{label}) "
                           f"WHERE b.owner_id = $o RETURN b.{pk} AS old", {"o": owner}) or []:
            out.add(_strip(pkns, r.get("old")))
    return out


def build_list_set(store, owner: str, *, min_gold: int = 3, max_gold: int = 10,
                   kinds=("decision", "episode", "record")) -> list[dict]:
    """주제 개념마다 종류별 정답 목록을 만든다. 기본 보기에서 감춰지는(historical) 노드는
    정답에서 뺀다. `gold_current` 는 거기서 대체된 노드도 뺀 것이다."""
    from store import pkns
    from vinc_rag import retrieval as R
    retired = retired_ids(store, owner)
    rows: list[dict] = []
    for kind in kinds:
        label, pk = LABELS[kind]
        rel = LIST_EDGES[kind]
        groups: dict[str, set] = {}
        titles: dict[str, str] = {}
        for r in store.run(f"MATCH (m:{label})-[:{rel}]->(c:Concept) "
                           "WHERE m.owner_id = $o AND c.owner_id = $o "
                           f"RETURN c.concept_id AS c, c.title AS t, m.{pk} AS m",
                           {"o": owner}) or []:
            c, m = _strip(pkns, r.get("c")), _strip(pkns, r.get("m"))
            if c and m:
                groups.setdefault(c, set()).add(m)
                titles[c] = str(r.get("t") or "").strip()
        for c in sorted(groups):
            members = sorted(groups[c])
            vf = R._validity_for_ids(store, members, owner=owner)
            gold = [m for m in members
                    if R._visible(*vf.get(m, ("", "")), historical=False, scope=None)]
            if not (min_gold <= len(gold) <= max_gold) or not titles[c]:
                continue
            rows.append({"id": f"{kind}:{c}", "kind": kind, "topic": c,
                         "topic_title": titles[c], "doc_mirror": c.startswith("concept:doc."),
                         "query": KIND_QUERY[kind].format(t=titles[c]),
                         "query_bare": titles[c], "gold": gold,
                         "gold_current": [m for m in gold if m not in retired]})
    return rows


def superseded_chain(store, owner: str, node_id: str, cap: int = 20) -> list[str]:
    """`node_id` 에서 SUPERSEDES(new→old)를 따라 닿는 옛 노드들. 같은 종류끼리만 잇는다."""
    from store import pkns
    kind = kind_of(node_id)
    if kind not in LABELS:
        return []
    label, pk = LABELS[kind]
    seen, frontier = [], [node_id]
    while frontier and len(seen) < cap:
        nxt = []
        for r in store.run(f"MATCH (a:{label})-[:SUPERSEDES]->(b:{label}) "
                           f"WHERE a.{pk} IN $ids AND a.owner_id = $o RETURN b.{pk} AS old",
                           {"ids": pkns.keys(owner, frontier), "o": owner}) or []:
            old = _strip(pkns, r.get("old"))
            if old and old != node_id and old not in seen:
                seen.append(old)
                nxt.append(old)
        frontier = nxt
    return seen


def build_current_set(store, owner: str, tasks: list[dict]) -> list[dict]:
    """과제 파일(`vinc.agent-tasks.v1`)의 각 과제 → {query, gold, superseded}.
    정답(support)이 하나가 아니거나 대체된 노드가 스토어에 없으면 그 행은 뺀다(사유를 남긴다)."""
    rows = []
    for t in tasks:
        support = [s for c in t.get("checks", []) if c.get("type") == "sources"
                   for s in c.get("support", [])]
        row = {"id": t.get("id"), "query": t.get("prompt", ""),
               "gold": support[0] if len(support) == 1 else "",
               "presupposes_old": t.get("presupposes_old")}
        if len(support) != 1:
            row["skip"] = f"support has {len(support)} ids"
        else:
            row["superseded"] = superseded_chain(store, owner, support[0])
            if not row["superseded"]:
                row["skip"] = "no SUPERSEDES chain from the gold node in this store"
        rows.append(row)
    return rows


# ── 채점(순수) ───────────────────────────────────────────────────────────────

def list_scores(returned: list[str], gold: list[str], k: int, kind: str = "") -> dict:
    """상위 k 의 정밀도(맞은 수 / k)와 재현율(맞은 수 / 정답 수), 그리고 종류 순도."""
    top = list(returned)[:k]
    g = set(gold)
    hit = sum(1 for x in top if x in g)
    out = {"hits": hit, "p": hit / k, "r": hit / len(g) if g else 0.0, "returned": len(top)}
    if kind:
        out["purity"] = sum(1 for x in top if kind_of(x) == kind) / k
    return out


def pick_scores(returned: list[str], gold: str, superseded: list[str]) -> dict:
    """정답 순위(1부터, 없으면 None)와 대체된 노드 중 가장 앞의 순위."""
    ids = list(returned)
    r_new = ids.index(gold) + 1 if gold in ids else None
    olds = [ids.index(o) + 1 for o in superseded if o in ids]
    r_old = min(olds) if olds else None
    return {"rank_new": r_new, "rank_old": r_old,
            "first": r_new == 1, "top3": r_new is not None and r_new <= 3,
            "old_shown": r_old is not None,
            "old_above_new": r_old is not None and (r_new is None or r_old < r_new)}


def brief_pick(res: dict, gold: str, superseded: list[str]) -> dict:
    status = res.get("status")
    node = (res.get("node") or {}).get("id")
    cands = [c.get("id") for c in res.get("candidates") or [] if c.get("id")]
    old = set(superseded)
    return {"status": status, "node": node,
            "resolved_new": status == "resolved" and node == gold,
            "resolved_old": status == "resolved" and node in old,
            "choose_new_first": status == "choose" and bool(cands) and cands[0] == gold,
            "new_offered": node == gold or gold in cands,
            "old_offered": node in old or any(c in old for c in cands)}


def bootstrap_mean(xs: list[float], n: int = 2000, seed: int = 0) -> list[float]:
    """평균의 백분위 부트스트랩 95% 구간. 표본이 없으면 빈 목록."""
    if not xs:
        return []
    rnd = random.Random(seed)
    m = len(xs)
    means = sorted(sum(xs[rnd.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return [round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)]


def mean(xs) -> float:
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def paired(a: list[float], b: list[float]) -> dict:
    """같은 행끼리의 차(b - a): 평균, 구간, 이긴/진/같은 행 수."""
    d = [y - x for x, y in zip(a, b)]
    return {"n": len(d), "mean": mean(d), "ci95": bootstrap_mean(d),
            "wins": sum(1 for v in d if v > 0), "losses": sum(1 for v in d if v < 0),
            "ties": sum(1 for v in d if v == 0)}


# ── 종류 먼저 거르기(오라클) ─────────────────────────────────────────────────

@contextlib.contextmanager
def kind_prefilter(store, kind: str):
    """세 노드 팔을 자르기 **전에** `kind` 로 거른다. 순위 규칙은 그대로다.
    제목 팔은 상한까지 받아 거른 뒤 자르고, 의미 팔은 미러 전량을 거리순으로 받아 거른 뒤
    자르며, 그래프 팔은 개념만 내므로 `kind` 가 concept 가 아니면 비운다."""
    from vinc_rag import retrieval as R
    orig_title, orig_vec = R._node_fallback_search, store.node_vector_search

    def title(st, query, *, limit=20, **kw):
        rows = orig_title(st, query, limit=R.NODE_SCAN_CAP, **kw)
        return [r for r in rows if kind_of(r.get("id")) == kind][:max(1, int(limit))]

    def vec(embedding, top_k, *a, **kw):
        rows = orig_vec(embedding, max(int(top_k), 100_000), *a, **kw) or []
        return [h for h in rows if (h.get("kind") or kind_of(h.get("id"))) == kind][:int(top_k)]

    def graph(*a, **kw):
        return R.__dict__["_graph_arm_search_orig"](*a, **kw) if kind == "concept" else []

    R.__dict__["_graph_arm_search_orig"] = R._graph_arm_search
    try:
        with mock.patch.object(R, "_node_fallback_search", title), \
                mock.patch.object(store, "node_vector_search", vec), \
                mock.patch.object(R, "_graph_arm_search", graph):
            yield
    finally:
        R.__dict__.pop("_graph_arm_search_orig", None)


# ── 누수 스위치 ──────────────────────────────────────────────────────────────

def _slug(node_id: str) -> str:
    s = str(node_id or "")
    return s.split(":", 1)[1] if ":" in s else s


@contextlib.contextmanager
def leak_tiebreak_slug():
    """제목 팔의 마지막 동점 깨기를 id 문자열(종류 접두가 앞)에서 슬러그·제목으로 바꾼다."""
    from vinc_rag import retrieval as R
    orig = R._node_fallback_search

    def title(st, query, *, limit=20, **kw):
        rows = orig(st, query, limit=R.NODE_SCAN_CAP, **kw)
        rows.sort(key=lambda r: (-r["hit_tokens"], -r["title_tokens"],
                                 R._FIELD_RANK.get(r["matched_in"], 9),
                                 _slug(r.get("id")), str(r.get("title") or ""), str(r.get("id"))))
        return rows[:max(1, int(limit))]
    with mock.patch.object(R, "_node_fallback_search", title):
        yield


@contextlib.contextmanager
def leak_identity():
    """대조군: 스위치와 같은 감싸기를 원래 키로 돈다. 바뀐 수가 0 이어야 감싸기가 무해하다."""
    from vinc_rag import retrieval as R
    orig = R._node_fallback_search

    def title(st, query, *, limit=20, **kw):
        return orig(st, query, limit=R.NODE_SCAN_CAP, **kw)[:max(1, int(limit))]
    with mock.patch.object(R, "_node_fallback_search", title):
        yield


@contextlib.contextmanager
def leak_no_graph_arm():
    """그래프 팔(개념만 올린다)을 끈다."""
    from vinc_rag import retrieval as R
    with mock.patch.object(R, "_graph_arm_search", lambda *a, **kw: []):
        yield


@contextlib.contextmanager
def leak_brief_rank_without_arms():
    """brief 해소 순서에서 팔 개수 항을 뺀다(그래프 팔은 개념에만 붙는다)."""
    from api import query as Q

    def rank(rows):
        strict = Q._brief_strict_on()
        return sorted(rows, key=lambda h: (-1 if (strict and h.get("sem_decisive")) else 0,
                                           -Q._brief_int(h.get("hit_tokens")),
                                           -Q._brief_int(h.get("title_tokens"))))
    with mock.patch.object(Q, "_brief_rank", rank):
        yield


@contextlib.contextmanager
def leak_current_relevance_only():
    """mode=current 의 정렬에서 "구간 있음(current)이 unknown 보다 먼저" 항을 뺀다.
    같은 주장 묶음의 최신순 재배열은 그대로 둔다."""
    from store import search_temporal as T
    orig = T.current_order

    def order_safe(rows, sources=None):
        # 청크 풀(`doc_id` 가 있는 행)은 그대로 둔다 — 노드 순서만 바꾼다.
        if any("doc_id" in r for r in rows):
            return orig(rows, sources)
        # 구간 상태를 모두 같게 보이게 한 뒤 원래 함수를 부르고, 행 객체로 되돌린다.
        saved = {id(r): r["temporal"]["validity"] for r in rows}
        for r in rows:
            r["temporal"]["validity"] = "current"
        try:
            orig(rows, sources)
        finally:
            for r in rows:
                r["temporal"]["validity"] = saved.get(id(r), r["temporal"]["validity"])
    with mock.patch.object(T, "current_order", order_safe):
        yield


#: 이름 → (스위치, 적용 표면). 표면: search 는 node_hits 상위 5, brief 는 해소 결과.
LEAKS = {
    "identity": (leak_identity, "search"),
    "tiebreak_slug": (leak_tiebreak_slug, "search"),
    "no_graph_arm": (leak_no_graph_arm, "search"),
    "brief_rank_without_arms": (leak_brief_rank_without_arms, "brief"),
    "current_relevance_only": (leak_current_relevance_only, "search_current"),
}


def title_arm_ties(store, query: str, owner: str, k: int = 5) -> dict:
    """제목 팔에서 id 동점 깨기가 무엇을 정하는가: 자르는 자리(k 와 k+1)의 동점, 그리고
    상위 k 안에서 키가 같은 이웃 쌍(그중 종류가 다른 쌍)."""
    from vinc_rag import retrieval as R
    rows = R._node_fallback_search(store, query, limit=R.NODE_SCAN_CAP, owner=owner,
                                   doc_intervals=True)
    key = lambda r: (-r["hit_tokens"], -r["title_tokens"],  # noqa: E731
                     R._FIELD_RANK.get(r["matched_in"], 9))
    cut = len(rows) > k and key(rows[k - 1]) == key(rows[k])
    cut_kinds = (cut and kind_of(rows[k - 1]["id"]) != kind_of(rows[k]["id"]))
    pairs = [(a, b) for a, b in zip(rows[:k], rows[1:k]) if key(a) == key(b)]
    return {"candidates": len(rows), "tie_at_cut": cut, "tie_at_cut_cross_kind": bool(cut_kinds),
            "tied_pairs_in_top": len(pairs),
            "tied_pairs_cross_kind": sum(1 for a, b in pairs
                                         if kind_of(a["id"]) != kind_of(b["id"]))}


# ── 실행 ─────────────────────────────────────────────────────────────────────

def _ids(res: dict) -> list[str]:
    return [h.get("id") for h in res.get("node_hits") or [] if h.get("id")]


def run_current(t, rows: list[dict]) -> list[dict]:
    """현재 유효 고르기. `t` 는 :class:`rag_eval.evalkit.transports.InProcess`."""
    out = []
    for row in rows:
        if row.get("skip"):
            out.append(row)
            continue
        rec = dict(row)
        vec = t._vec(row["query"])
        for name, kw in (("general", {}), ("current", {"mode": "current"}),
                         ("historical", {"historical": True})):
            for k in (5, 10):
                res = t.q.search(row["query"], top_k=k, owner=t.owner, store=t.store,
                                 query_embedding=vec, **kw)
                rec[f"search_{name}_k{k}"] = {"ids": _ids(res),
                                              **pick_scores(_ids(res), row["gold"], row["superseded"])}
        for name, kw in (("general", {}), ("current", {"mode": "current"})):
            res = t.q.brief(row["query"], owner=t.owner, store=t.store, query_embedding=vec, **kw)
            rec[f"brief_{name}"] = brief_pick(res, row["gold"], row["superseded"])
        out.append(rec)
    return out


def run_lists(t, rows: list[dict], *, ks=KS) -> list[dict]:
    out = []
    for row in rows:
        rec = dict(row)
        kind = row["kind"]
        for qkey in ("query", "query_bare"):
            q = row[qkey]
            vec = t._vec(q)
            tag = "kw" if qkey == "query" else "bare"
            for k in ks:
                res = t.q.search(q, top_k=k, owner=t.owner, store=t.store, query_embedding=vec)
                rec[f"text_{tag}_k{k}"] = {"ids": _ids(res), **list_scores(_ids(res), row["gold"], k, kind)}
                with kind_prefilter(t.store, kind):
                    res = t.q.search(q, top_k=k, owner=t.owner, store=t.store, query_embedding=vec)
                rec[f"oracle_{tag}_k{k}"] = {"ids": _ids(res), **list_scores(_ids(res), row["gold"], k, kind)}
            if tag == "kw":
                res = t.q.search(q, top_k=CLIENT_LIMIT, owner=t.owner, store=t.store,
                                 query_embedding=vec)
                kept = [x for x in _ids(res) if kind_of(x) == kind]
                for k in ks:
                    rec[f"client_kw_k{k}"] = {"ids": kept[:k], **list_scores(kept, row["gold"], k, kind)}
        if kind == "decision":      # "지금 유효한 결정" — current 정답과 mode=current
            q, vec = row["query"], t._vec(row["query"])
            for k in ks:
                res = t.q.search(q, top_k=k, owner=t.owner, store=t.store, query_embedding=vec,
                                 mode="current")
                rec[f"text_kw_current_k{k}"] = {"ids": _ids(res),
                                                **list_scores(_ids(res), row["gold_current"], k, kind)}
                with kind_prefilter(t.store, kind):
                    res = t.q.search(q, top_k=k, owner=t.owner, store=t.store,
                                     query_embedding=vec, mode="current")
                rec[f"oracle_kw_current_k{k}"] = {"ids": _ids(res),
                                                  **list_scores(_ids(res), row["gold_current"], k, kind)}
        out.append(rec)
    return out


def run_leaks(t, queries: list[dict], *, k: int = 5, leaks=None) -> dict:
    """질문마다 기준과 스위치를 켠 상위 k 를 비교한다. `queries` 행: {id, query, gold?}."""
    leaks = leaks or LEAKS
    out: dict = {}
    base_s, base_c, base_b = {}, {}, {}
    for row in queries:
        vec = t._vec(row["query"])
        base_s[row["id"]] = _ids(t.q.search(row["query"], top_k=k, owner=t.owner, store=t.store,
                                            query_embedding=vec))
        base_c[row["id"]] = _ids(t.q.search(row["query"], top_k=k, owner=t.owner, store=t.store,
                                            query_embedding=vec, mode="current"))
        b = t.q.brief(row["query"], owner=t.owner, store=t.store, query_embedding=vec)
        base_b[row["id"]] = (b.get("status"), (b.get("node") or {}).get("id"))
    for name, (switch, surface) in leaks.items():
        per = []
        for row in queries:
            vec = t._vec(row["query"])
            with switch():
                if surface == "brief":
                    b = t.q.brief(row["query"], owner=t.owner, store=t.store, query_embedding=vec)
                    after, before = (b.get("status"), (b.get("node") or {}).get("id")), base_b[row["id"]]
                else:
                    kw = {"mode": "current"} if surface == "search_current" else {}
                    after = _ids(t.q.search(row["query"], top_k=k, owner=t.owner, store=t.store,
                                            query_embedding=vec, **kw))
                    before = (base_c if kw else base_s)[row["id"]]
            rec = {"id": row["id"], "changed": after != before}
            if surface != "brief":
                rec["set_changed"] = set(after) != set(before)
                gold = row.get("gold")
                if gold:
                    rb = before.index(gold) + 1 if gold in before else None
                    ra = after.index(gold) + 1 if gold in after else None
                    rec["gold_before"], rec["gold_after"] = rb, ra
            else:
                rec["before"], rec["after"] = list(before), list(after)
            per.append(rec)
        out[name] = {"surface": surface, "n": len(per),
                     "changed": sum(r["changed"] for r in per),
                     "set_changed": sum(r.get("set_changed", False) for r in per),
                     "gold_better": sum(1 for r in per if _better(r.get("gold_before"), r.get("gold_after"))),
                     "gold_worse": sum(1 for r in per if _better(r.get("gold_after"), r.get("gold_before"))),
                     "rows": per}
    return out


def _better(before, after) -> bool:
    """after 가 before 보다 앞선 순위인가(None 은 목록 밖)."""
    if after is None:
        return False
    return before is None or after < before


# ── 요약 ─────────────────────────────────────────────────────────────────────

def summarize_current(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r.get("skip")]
    out = {"n": len(scored), "skipped": [{"id": r["id"], "why": r["skip"]} for r in rows if r.get("skip")]}
    for key in [k for k in (scored[0] if scored else {}) if k.startswith("search_")]:
        xs = [r[key] for r in scored]
        out[key] = {m: {"count": sum(1 for x in xs if x[m]), "rate": mean(1.0 if x[m] else 0.0 for x in xs),
                        "ci95": bootstrap_mean([1.0 if x[m] else 0.0 for x in xs])}
                    for m in ("first", "top3", "old_shown", "old_above_new")}
        out[key]["new_missing"] = sum(1 for x in xs if x["rank_new"] is None)
    for key in [k for k in (scored[0] if scored else {}) if k.startswith("brief_")]:
        xs = [r[key] for r in scored]
        out[key] = {m: sum(1 for x in xs if x[m]) for m in
                    ("resolved_new", "resolved_old", "choose_new_first", "new_offered", "old_offered")}
        out[key]["status"] = {s: sum(1 for x in xs if x["status"] == s) for s in ("resolved", "choose", "none")}
    return out


def summarize_lists(rows: list[dict], *, ks=KS) -> dict:
    out: dict = {}
    groups = {"all": rows}
    for kind in sorted({r["kind"] for r in rows}):
        groups[kind] = [r for r in rows if r["kind"] == kind]
        groups[kind + ":topic"] = [r for r in groups[kind] if not r["doc_mirror"]]
        groups[kind + ":doc_mirror"] = [r for r in groups[kind] if r["doc_mirror"]]
    for gname, grows in groups.items():
        if not grows:
            continue
        g: dict = {"n": len(grows), "gold_size_mean": mean(len(r["gold"]) for r in grows)}
        for k in ks:
            arms = [a for a in (f"text_kw_k{k}", f"client_kw_k{k}", f"oracle_kw_k{k}",
                                f"text_bare_k{k}", f"oracle_bare_k{k}",
                                f"text_kw_current_k{k}", f"oracle_kw_current_k{k}")
                    if all(a in r for r in grows)]
            for a in arms:
                ps = [r[a]["p"] for r in grows]
                rs = [r[a]["r"] for r in grows]
                g[a] = {"p": mean(ps), "p_ci95": bootstrap_mean(ps), "r": mean(rs),
                        "r_ci95": bootstrap_mean(rs),
                        "purity": mean(r[a].get("purity", 0) for r in grows)}
            for lo, hi in ((f"text_kw_k{k}", f"oracle_kw_k{k}"), (f"client_kw_k{k}", f"oracle_kw_k{k}"),
                           (f"text_kw_k{k}", f"client_kw_k{k}"), (f"oracle_kw_k{k}", f"oracle_bare_k{k}"),
                           (f"text_kw_current_k{k}", f"oracle_kw_current_k{k}")):
                if lo in g and hi in g:
                    g[f"gap_r:{hi}-{lo}"] = paired([r[lo]["r"] for r in grows], [r[hi]["r"] for r in grows])
                    g[f"gap_p:{hi}-{lo}"] = paired([r[lo]["p"] for r in grows], [r[hi]["p"] for r in grows])
        out[gname] = g
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, help="스토어 사본(원본 아님)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--model", default="BAAI/bge-m3")
    ap.add_argument("--tasks", help="현재 유효 과제 파일(vinc.agent-tasks.v1)")
    ap.add_argument("--min-gold", type=int, default=3)
    ap.add_argument("--max-gold", type=int, default=10)
    ap.add_argument("--skip", nargs="*", default=[], choices=("current", "lists", "leaks"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from rag_eval.evalkit.runtime import fingerprint
    from rag_eval.evalkit.transports import InProcess
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t = InProcess(a.data, a.owner, f"{a.model}={a.onnx}")
    header = {"fingerprint": fingerprint(), "data": a.data, "owner": a.owner,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        cur = []
        if a.tasks and "current" not in a.skip:
            tasks = json.loads(pathlib.Path(a.tasks).read_text(encoding="utf-8"))
            tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
            cur = run_current(t, build_current_set(t.store, a.owner, tasks))
            (out / "current_pick.json").write_text(json.dumps(
                {**header, "summary": summarize_current(cur), "rows": cur}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        lists = []
        if "lists" not in a.skip:
            lists = run_lists(t, build_list_set(t.store, a.owner, min_gold=a.min_gold,
                                                max_gold=a.max_gold))
            (out / "kind_lists.json").write_text(json.dumps(
                {**header, "summary": summarize_lists(lists), "rows": lists}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        if "leaks" not in a.skip:
            qs = ([{"id": "cur:" + r["id"], "query": r["query"], "gold": r["gold"]}
                   for r in cur if not r.get("skip")]
                  + [{"id": "list:" + r["id"], "query": r["query"]} for r in lists])
            res = run_leaks(t, qs)
            ties = [{"id": q["id"], **title_arm_ties(t.store, q["query"], a.owner)} for q in qs]
            (out / "kind_leaks.json").write_text(json.dumps(
                {**header, "leaks": res, "title_arm_ties": ties}, ensure_ascii=False, indent=1),
                encoding="utf-8")
    finally:
        t.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
