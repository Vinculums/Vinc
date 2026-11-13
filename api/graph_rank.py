# -*- coding: utf-8 -*-
"""지도 순위: 시드 편향 개인화 페이지랭크(`ppr`), 그것에 최근성을 섞은 값, 그리고 노드 예산 안에서
시드와 이어진 채 `ppr` 합이 큰 부분 그래프를 고르는 상금 선택.

`graph()` 의 seed ego 뷰와 `decide()` 가 `attach_ppr` 를 부르고, `graph(select="prize")` 가
`_prize_pick` 을 부른다. 순위 함수는 받은 노드·엣지만으로 값을 정하는 순수 함수이고(반복 수 고정,
난수·시간 무의존), 스토어를 읽는 것은 상금 선택의 엣지 조회 하나다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`execute` 등)은 이 모듈에서 찾으므로, 상금 선택의
조회를 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 계획·실행과 엣지 통행 비용은 `api/query_base.py` 에서
읽는다(`tests/core/test_graph_rank.py` 가 둘을 본다).
"""
from __future__ import annotations

import datetime as _dt

from store import pkns

from .query_base import QueryPlan, _edge_cost, execute


# ── ego-Personalized PageRank (E-G2) — 순수 함수 시드 편향 랭킹 ────────────────
# 리서치(HippoRAG PPR)의 시드 편향 확산을 **백엔드 밖 결정론 순수 함수**로 옮긴다.
# ego 서브그래프(수십~수백 노드) 위 power iteration(반복 수 고정 → 결정론, 난수·
# 시간 무의존 — `_cluster_organic` 의 "시드 고정 + 반복 고정" 전례 동형). 전이
# 가중에 엣지 타입 차등을 준다: Bridge CONNECTS(1급 크로스도메인 결합, §3.6)·방향
# 관계(선행/파생/상속)를 우대해 Bridge 신호와 방향 구조를 랭킹에 편입한다. 전역
# 지표가 필요하면(코퍼스 급성장) store 어댑터 algo `page_rank` 로 위임(E-G3
# graph_algo) — 인터페이스 동일 유지. Cypher 내 점수식 없음(§4-2 · 임베디드 방언
# 이식성). `rrf_fuse`/`score_preference` 와 같은 순수 함수 이웃.
PPR_EDGE_WEIGHTS = {
    "CONNECTS": 2.0,          # Bridge 매개 — 1급 크로스도메인 결합(§3.6)
    "PREREQUISITE_OF": 1.5, "DERIVES_FROM": 1.5, "INHERITS_FROM": 1.5,  # 방향 구조
}
PPR_DEFAULT_WEIGHT = 1.0     # RELATED_TO·ABOUT·ATTACHES_TO·TYPED 등


def ppr_scores(nodes: list[dict], edges: list[dict], seeds: list[str], *,
               alpha: float = 0.15, iters: int = 30,
               edge_weights: dict | None = None) -> dict[str, float]:
    """시드 편향 개인화 페이지랭크 — 순수·결정론(반복 수 고정, 난수·시간 무의존).

    ``nodes``: shape_graph 노드 리스트(각 ``{id, ...}``). ``edges``: shape_graph
    엣지 리스트(각 ``{source, target, type}``). ``seeds``: 개인화 벡터를 실을
    시드 id(보통 ``[seed]``). 반환 ``{node_id: score}`` (합=1 정규화, 시드에
    가까운 하위 연결 구조로 질량이 집중).

    전이 그래프는 **대칭**(엣지가 양방향 전이 — RWR/HippoRAG 관례)이며 엣지 타입
    가중(:data:`PPR_EDGE_WEIGHTS`)을 곱한다. 댕글링(전이 이웃 0) 질량은 개인화
    벡터로 재분배해 싱크를 막는다(질량 보존). 시드가 그래프 밖이거나 비면 균등
    개인화로 강등. ``alpha`` = 재시작(teleport) 확률(=1-damping)."""
    ids = [n["id"] for n in nodes if n.get("id")]
    if not ids:
        return {}
    idx = {nid: i for i, nid in enumerate(ids)}
    n = len(ids)
    ew = {**PPR_EDGE_WEIGHTS, **(edge_weights or {})}
    out: list[dict[int, float]] = [dict() for _ in range(n)]   # out[i][j] = 누적 가중
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if s not in idx or t not in idx or s == t:
            continue
        w = ew.get(e.get("type") or "", PPR_DEFAULT_WEIGHT)
        si, ti = idx[s], idx[t]
        out[si][ti] = out[si].get(ti, 0.0) + w
        out[ti][si] = out[ti].get(si, 0.0) + w   # 대칭 전이
    seed_idx = [idx[s] for s in seeds if s in idx]
    p = [0.0] * n
    if seed_idx:
        for i in seed_idx:
            p[i] = 1.0 / len(seed_idx)
    else:
        for i in range(n):
            p[i] = 1.0 / n
    r = list(p)
    for _ in range(max(1, int(iters))):
        nr = [alpha * p[i] for i in range(n)]
        dangling = 0.0
        for i in range(n):
            deg = sum(out[i].values())
            if deg <= 0:
                dangling += r[i]
                continue
            share = (1.0 - alpha) * r[i]
            for j, w in out[i].items():
                nr[j] += share * (w / deg)
        if dangling:                                   # 댕글링 질량 → 개인화 재분배
            for i in range(n):
                nr[i] += (1.0 - alpha) * dangling * p[i]
        r = nr
    total = sum(r) or 1.0
    return {ids[i]: r[i] / total for i in range(n)}


# ── 최근성(Generative Agents 의 recency × relevance, 모델 없이) ─────────────────
# `ppr` 은 "시드에서 얼마나 가까운가"만 본다. 같은 거리라면 최근에 생긴 것이 지금 일에 더
# 닿아 있을 가능성이 크다. Park et al. 2023 의 기억 점수는 최근성·중요도·관련도를 **더하지만**
# 여기서는 반감기로 줄어드는 최근성을 `ppr` 에 **곱한다**(중요도는 모델이 매기므로 뺀다).
# 곱이라서 시드가 닿지 않는 노드(ppr 0)는 새것이어도 0 에 머문다. 그 값을 `ppr_recent` 로
# **따로** 싣는다 — `ppr` 을 바꾸면 그것을 읽는 쪽의 순위가 말없이 바뀐다.
# 나이는 오늘이 아니라 **이 노드 집합에서 가장 새 `ts`** 부터 잰다. 그래서 값은 그래프만의
# 순수 함수다(그래프가 그대로면 날이 바뀌어도 그대로다).
# ⚠ "최근"의 뜻이 종류마다 다르다(`ts` 투영이 다르다): Concept·Record·Decision 은 생성
# 시각(없으면 갱신), Episode 는 사건 날짜, Entity 는 갱신 시각, Bridge 는 생성 시각(없으면
# 갱신)을 읽는다. 쓰기 경로가 Bridge 의 생성 시각을 찍기 시작하기 전에 만든 Bridge 는 그
# 열이 비어 갱신 시각으로 떨어진다.
# 근거: [[vinc.research.graphrag-agent-memory-literature-2026-09-28]] §4 Generative Agents 행
PPR_RECENCY_HALF_LIFE_DAYS = 90.0
#: 최근성이 섞이는 몫. 0 이면 `ppr_recent == ppr`, 1 이면 최근성만큼 줄어든다.
PPR_RECENCY_WEIGHT = 0.5


def _node_date(ts: str | None) -> _dt.date | None:
    """노드 시각(`ts`)의 날짜 부분. 못 읽으면 None."""
    v = (ts or "").strip()
    if len(v) < 10:
        return None
    try:
        return _dt.date.fromisoformat(v[:10])
    except ValueError:
        return None


def ppr_recent(nodes: list[dict], scores: dict[str, float], *,
               half_life_days: float = PPR_RECENCY_HALF_LIFE_DAYS,
               weight: float = PPR_RECENCY_WEIGHT) -> dict[str, float]:
    """`ppr` 에 최근성을 섞는다: `ppr × ((1-w) + w × 0.5^(나이/반감기))`, 합=1 로 정규화.
    나이는 집합에서 가장 새 노드부터 잰 일 수다(가장 새 노드의 최근성이 1).

    시각을 모르는 노드는 **아는 노드들의 평균 최근성**을 받는다. 모른다고 오래된 것으로
    치면 시각 없는 행이 전부 밀려나고, 새것으로 치면 거꾸로 올라온다 — 둘 다
    데이터가 말하지 않은 것을 만든다."""
    hl = max(1e-6, float(half_life_days))
    w = min(1.0, max(0.0, float(weight)))
    dates = {nd.get("id"): _node_date(nd.get("ts")) for nd in nodes}
    newest = max((d for d in dates.values() if d), default=None)
    rec: dict[str, float | None] = {
        nid: None if d is None else 0.5 ** ((newest - d).days / hl)
        for nid, d in dates.items()}
    known = [v for v in rec.values() if v is not None]
    neutral = sum(known) / len(known) if known else 1.0
    raw = {nid: scores.get(nid, 0.0) * ((1.0 - w) + w * (neutral if r is None else r))
           for nid, r in rec.items() if nid}
    total = sum(raw.values()) or 1.0
    return {nid: v / total for nid, v in raw.items()}


def attach_ppr(shaped: dict, seeds: list[str], *, alpha: float = 0.15,
               iters: int = 30, recent: bool = False) -> dict:
    """shaped 그래프의 각 노드에 ``ppr`` 필드(6자리 반올림)를 부여한다(E-G2, 가산
    메타 — 기존 소비처 무영향). seed ego 뷰·decide 에서 호출. 시드 없거나 노드 0이면
    무동작(빈 그래프 안전). ``recent`` 면 최근성을 섞은 ``ppr_recent`` 도 싣는다
    (:func:`ppr_recent`). 기본은 끈다: 기본 출력(`vinc_graph` layers·decide)은 바이트
    단위로 그대로여야 한다."""
    nodes = shaped.get("nodes") or []
    if not nodes:
        return shaped
    scores = ppr_scores(nodes, shaped.get("edges") or [], seeds,
                        alpha=alpha, iters=iters)
    mixed = ppr_recent(nodes, scores) if recent else {}
    for nd in nodes:
        nd["ppr"] = round(scores.get(nd.get("id"), 0.0), 6)
        if recent:
            nd["ppr_recent"] = round(mixed.get(nd.get("id"), 0.0), 6)
    return shaped


# ── 상금 기반 연결 선택(PCST 방식, G-Retriever 의 부분 그래프 고르기를 모델 없이) ──
# 노드 예산이 있을 때 지금은 **층 단위로** 자른다(몇 홉까지는 다 봤다는 말이 참으로
# 남는다). 그 대신 "시드와 이어진 채 상금이 가장 큰 부분 그래프"를 고르면 가까워도 덜
# 중요한 노드 대신 한 홉 더 먼 중요한 노드가 들어온다. 상금은 `ppr`, 비용은 엣지 통행
# 비용(:func:`_edge_cost`)이다. 정확한 PCST(He et al. 2024 는 pcst_fast)가 아니라 그
# 예산판의 탐욕 근사다: 시드에서 시작해 연결을 지키며 상금이 가장 큰 이웃을 하나씩 더한다.
# 동률은 싼 연결, 그다음 id 순이라 결정론이다.
def prize_subgraph(nodes: list[dict], edges: list[dict], prizes: dict[str, float],
                   root: str, budget: int) -> list[str]:
    """`root` 를 포함하고 연결된 노드 `budget` 개. 상금 합이 크도록 탐욕으로 고른다."""
    ids = {n.get("id") for n in nodes if n.get("id")}
    if root not in ids:
        return []
    adj: dict[str, dict[str, float]] = {i: {} for i in ids}
    for e in edges or []:
        a, b = e.get("source"), e.get("target")
        if a in ids and b in ids and a != b:
            c = _edge_cost(e)
            adj[a][b] = min(c, adj[a].get(b, c))
            adj[b][a] = min(c, adj[b].get(a, c))
    chosen = [root]
    inside = {root}
    frontier: dict[str, float] = dict(adj[root])
    while frontier and len(chosen) < max(1, int(budget)):
        best = min(frontier, key=lambda v: (-prizes.get(v, 0.0), frontier[v], v))
        chosen.append(best)
        inside.add(best)
        frontier.pop(best)
        for u, c in adj[best].items():
            if u not in inside:
                frontier[u] = min(c, frontier.get(u, c))
    return chosen


#: 상금 기반 선택을 하는 도달 집합(시드를 뺀 개념 수)의 상한. 선택은 도달 집합 **전부**의
#: 개념↔개념 엣지를 id 만으로 읽는다(행·축은 고른 개념에만). 그 조회도 도달 집합에 비례하므로
#: 여기서 막고, 넘으면 층 단위 자르기로 돌아가 그렇다고 말한다.
#: 근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]
PRIZE_REACH_CAP = 600


def concept_edge_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합 **안의** 개념↔개념 엣지를 id·타입·신뢰도만으로. 상금 선택이 순위를 매기는
    그래프다. props·tags·추적 열을 싣는 :func:`graph_rows_for_ids_plan` 과 축 합류는 고른
    개념에만 돈다. owner 는 양 끝 모두에 건다(SEC-1, graph_rows 와 같은 이유)."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    where = "c.concept_id IN $ids AND d.concept_id IN $ids"
    if owner:
        where += " AND c.owner_id = $owner AND d.owner_id = $owner"
        params["owner"] = owner
    cypher = (f"MATCH (c:Concept)-[r]->(d:Concept) WHERE {where} "
              "RETURN c.concept_id AS source, d.concept_id AS target, "
              "type(r) AS type, r.confidence AS confidence")
    return QueryPlan("concept_edge_ids", cypher, params,
                     note="concept to concept edges inside the id set (ids only)")


def _prize_pick(seed: str, reach_rows: list[dict], max_nodes: int, *, owner: str,
                cfg, store) -> tuple[list[str] | None, str]:
    """도달 집합(개념)에서 시드 + 개념 `max_nodes` 개를 상금(`ppr`)으로 고른다. 예산 단위는 층
    자르기와 같다(시드 빼고 개념만 센다). 돌려주는 것은 (고른 id, `selection.note`) 이고, 고르지
    않으면 id 자리가 None 이며 문장이 그 까닭이다(부르는 쪽이 층 자르기로 돌아간다).

    순위 그래프는 개념↔개념 엣지뿐이다. 시드에서 재시작하는 PPR 은 거기서 시드와 이어지지 않은
    개념(매개자·주제 노드로만 이어진 것)에 정확히 0 을 주므로, 후보는 ppr > 0 인 개념이다. 후보가
    예산 안이면 상금은 자를 것이 없다. 그대로 고르면 "깊이 N 을 다 봤다"는 거짓이 되고 층 자르기가
    내는 깊이 안내도 사라지므로 층 자르기로 돌린다.
    ponytail: 매개자로만 이어진 개념은 후보가 못 된다. 필요해지면 매개자 팔을 id 만으로 더 읽어
    쌍으로 접을 것."""
    ids = sorted({seed, *(r["id"] for r in reach_rows)})
    res = execute(concept_edge_ids_plan(ids, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return None, "the edge lookup for prize selection did not run"
    edges = [e for e in res.get("rows", []) if e.get("source") and e.get("target")]
    nodes = [{"id": i} for i in ids]
    prizes = ppr_scores(nodes, edges, [seed])
    ranked = sum(v > 0 for v in prizes.values()) - 1      # 시드 빼고
    reach = len(ids) - 1
    if ranked <= int(max_nodes):
        return None, (f"only {ranked} of the {reach} concepts in reach link to the seed "
                      f"through concepts, and the budget of {max_nodes} holds them all, "
                      "leaving prize nothing to rank")
    left = reach - ranked
    note = (f"the {ranked} concepts in reach that link to the seed through concepts were "
            "ranked by ppr and the connected set with the most was kept, with what is "
            "attached to it")
    if left:
        note += (f"; {left} concepts reached only through records, episodes, bridges, "
                 "decisions or topics were not candidates and were left out")
    return prize_subgraph(nodes, edges, prizes, seed, int(max_nodes) + 1), note
