# -*- coding: utf-8 -*-
"""Query planners + executors for recall / search / decide.

A QueryPlan is a (cypher, params) pair plus metadata. Plans are pure and
testable. ``execute`` runs a plan against Neo4j (driver imported lazily); when
no driver/password is available it returns the plan with ``executed=False`` so
callers — and tests — get a deterministic result without a live database.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from store.config import StoreConfig, load_config
from store.backend import open_store


@dataclass
class QueryPlan:
    kind: str            # recall | search | decide
    cypher: str
    params: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cypher": self.cypher, "params": self.params, "note": self.note}


# ── Plan builders (pure) ─────────────────────────────────────────────────────

# Default preference weights for rank="preference". Tunable per call.
PREF_WEIGHTS = {"w_pin": 3.0, "w_freq": 1.0, "w_tag": 2.0, "freq_k": 5.0}


def score_preference(rows: list[dict], *, weights: dict | None = None,
                     tags: list[str] | None = None, limit: int = 10) -> list[dict]:
    """소유자 선호 신호로 recall 후보를 랭크하는 **순수 함수**(백엔드 무의존, §4-2).

    점수 = w_pin·pin + w_freq·(access_count 포화항) + w_tag·(tags 교집합 수).
    각 행에 ``score`` 를 부여하고 점수 내림차순으로 상한 ``limit`` 까지 반환한다.
    입력 ``rows`` 는 recency(date DESC) 정렬 상태를 전제 — 파이썬 정렬이 안정적이라
    동점은 recency tie-break 로 유지된다."""
    w = {**PREF_WEIGHTS, **(weights or {})}
    tagset = set(tags or [])

    def _score(r: dict) -> float:
        pin = 1.0 if r.get("pinned") else 0.0
        ac = float(r.get("access_count") or 0)
        freq = ac / (ac + w["freq_k"]) if (ac + w["freq_k"]) else 0.0
        overlap = float(len(tagset & set(r.get("tags") or []))) if tagset else 0.0
        return w["w_pin"] * pin + w["w_freq"] * freq + w["w_tag"] * overlap

    scored = sorted(rows, key=_score, reverse=True)  # stable → recency tie-break 유지
    for r in scored:
        r["score"] = _score(r)
    return scored[:limit]


def recall_plan(query: str = "", *, owner: str | None = None, tenant: str | None = None,
                domain: str | None = None, workspace: str | None = None,
                tags: list[str] | None = None, rank: str = "recency", limit: int = 10,
                weights: dict | None = None) -> QueryPlan:
    """Recall an individual's *learning* episodes, scoped by ownership and domain,
    ordered by recency or by owner-preference alignment.

    The memory is one person's cross-domain learning. ``owner`` is the primary
    (individual) scope — "my memory". ``domain`` is a **free, user-defined** string
    (physics, math, art, economics, finance, …) — never a fixed list; pass whatever
    the learner actually used, or omit to recall across all their domains. ``tenant``
    is the governance boundary; ``workspace`` narrows further. Each filter is
    optional and ANDed. Text filter matches title/summary.

    ``rank``:
      - ``"recency"`` (default): ``ORDER BY e.date DESC``.
      - ``"preference"``: score each Episode by the owner's signals — pin (strong),
        access frequency (saturating), and tag overlap with ``tags`` — then order
        by that score, recency as tie-break ("내 선호에 맞춤")."""
    where = []
    params: dict = {"limit": int(limit)}
    if owner:
        where.append("e.owner_id = $owner")
        params["owner"] = owner
    if domain:
        where.append("e.domain = $domain")
        params["domain"] = domain
    if tenant:
        where.append("e.tenant_id = $tenant")
        params["tenant"] = tenant
    if workspace:
        where.append("e.workspace = $workspace")
        params["workspace"] = workspace
    if query:
        where.append("(toLower(e.title) CONTAINS toLower($q) OR toLower(e.summary) CONTAINS toLower($q))")
        params["q"] = query
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    # 투영에 preference 신호(pinned/tags/access_count/last_access)를 항상 포함한다 —
    # 이 신호는 런타임 축적분으로 원문 재수집으로 복원 불가하며 pack.json 이 유일한
    # 무손실 이관 통로다(설계 §10·D4). recency·preference 공통.
    projection = ("e.episode_id AS id, e.title AS title, e.date AS date, "
                  "e.owner_id AS owner, e.domain AS domain, e.tenant_id AS tenant, "
                  "e.workspace AS workspace, e.summary AS summary, "
                  "coalesce(e.pinned, false) AS pinned, coalesce(e.tags, []) AS tags, "
                  "coalesce(e.access_count, 0) AS access_count, e.last_access AS last_access")

    if rank == "preference":
        # 선호 점수는 **백엔드 밖 순수 함수**(:func:`score_preference`)로 매긴다 —
        # RRF 융합과 동형(§4-2). 임베디드(Kùzu 계보)는 toFloat·리스트 컴프리헨션·
        # 인덱스 스코어링 방언이 Neo4j 와 달라(§6), Cypher 내 점수식은 이식성이 낮다.
        # 스코프 필터된 후보 전량을 신호와 함께 가져와(단일 사용자 규모 — §12 R5)
        # 인-프로세스로 랭크·상한한다.
        # ⚠ kuzu 는 **미사용 파라미터를 거부**한다("Parameter … not found", §6 방언).
        # preference 플랜은 Cypher 에 $limit 를 쓰지 않으므로(파이썬에서 상한) 제거.
        params.pop("limit", None)
        cypher = (
            f"MATCH (e:Episode) {clause} "
            f"RETURN {projection} "
            "ORDER BY e.date DESC"        # 안정 정렬 기반(동점 시 recency tie-break)
        )
        return QueryPlan("recall", cypher, params,
                         note="episodic recall (preference — scored in-process)")

    cypher = (
        f"MATCH (e:Episode) {clause} "
        f"RETURN {projection} "
        "ORDER BY e.date DESC LIMIT $limit"
    )
    return QueryPlan("recall", cypher, params, note="3-tier episodic memory (owner-scoped)")


def rrf_fuse(ranked_lists: list[list[dict]], *, k: int = 60,
             top_k: int | None = None, id_key: str = "id") -> list[dict]:
    """Reciprocal Rank Fusion — 하이브리드 검색의 순수 융합 함수(§4-2, 백엔드 밖).

    각 순위 리스트에서 항목의 기여 = 1/(k + rank). 여러 암(vector·BM25)의 기여를
    합산해 재정렬한다. 학습·점수 보정 불요(k≈60 기본). 각 행에 ``rrf_score`` 부여."""
    scores: dict = {}
    rows_by_id: dict = {}
    for rows in ranked_lists:
        for rank, row in enumerate(rows):
            rid = row.get(id_key)
            if rid is None:
                continue
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
            rows_by_id.setdefault(rid, row)
    fused = sorted(rows_by_id.values(), key=lambda r: scores[r[id_key]], reverse=True)
    for r in fused:
        r["rrf_score"] = scores[r[id_key]]
    return fused[:top_k] if top_k else fused


def decide_plan(topic: str, *, depth: int = 2) -> QueryPlan:
    """Decision support: traverse policy/concept relations around a topic to
    surface conflicts, prerequisites, and cross-domain bridges.

    임베디드(Kùzu 계보) 방언 정합(§6-2, P1-0 실측):
    - **M7**: 라벨 없는 `MATCH (n)` 에서 참조하는 속성은 **어느 노드 테이블엔가 존재해야**
      한다 — 부재 시 Binder 오류. 기존 `n.id` 는 모든 테이블에 없어(episode_id/
      concept_id/bridge_id/… 만 존재) 항상 오류이자 사문(死文)이므로 제거하고, 실제
      시작 노드(Concept·Bridge)의 식별자·title 로 매칭한다.
    - **M3**: 네이티브 가변길이 `*1..d` 는 기본 WALK 라 엣지 재사용 사이클을 허용해
      경로 수가 폭증한다(3노드 사이클서 *1..8 시 510경로 실측). `TRAIL`(엣지 무재사용,
      키워드는 범위 **앞**)로 가드 → 동일 셋업 6경로."""
    depth = max(1, min(int(depth), 4))
    params = {"topic": topic}
    cypher = (
        "MATCH (n) WHERE n.concept_id = $topic OR n.bridge_id = $topic "
        "OR n.title = $topic "
        f"MATCH path = (n)-[* TRAIL 1..{depth}]-(m) "
        "RETURN n, relationships(path) AS rels, m "
        "LIMIT 100"
    )
    return QueryPlan("decide", cypher, params, note="policy/concept relation traversal")


# ── Executor ─────────────────────────────────────────────────────────────────
# 드라이버 취득은 store.backend.open_store 로 일원화(설계 §4-1). 여기서는 백엔드를
# 열고 plan.cypher 를 store.run 으로 실행할 뿐 — 드라이버를 직접 열지 않는다.

def execute(plan: QueryPlan, cfg: StoreConfig | None = None, *, store=None) -> dict:
    """Run a plan if a store is reachable; otherwise return the plan only.

    백엔드 중립(설계 §4-3): `open_store(cfg)` 가 None(도달 불가)이면 순수 플랜을
    `executed=False` 로 반환 — DB 부재 시 결정론 계약 유지(§1 불변 원칙).

    ``store`` 주입(사이드카 §11 P2): 호출부가 이미 연 스토어(잡 워커의 writer 또는
    read 뷰)를 넘기면 그것을 재사용한다 — 이때는 **열지도 닫지도 않는다**(수명은
    호출부 소유). 미주입(기본)이면 종전대로 호출당 open→close 한다(기존 계약 불변)."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"plan": plan.to_dict(), "executed": False, "rows": []}
    if store is None:
        base["note"] = "no reachable store (dry plan returned)"
        return base
    try:
        rows = store.run(plan.cypher, plan.params)
        base["executed"] = True
        base["rows"] = rows
    except Exception as exc:
        # 강등(graceful degradation, §3-4): 미프로비저닝·미구현 방언(search 는
        # M5 store.fulltext_search 로 승격 예정 — §4-2·§6-2) 등 스토어가 플랜을
        # 실행하지 못하면 순수 플랜을 executed=False 로 반환한다(무 크래시 계약).
        base["note"] = f"store 실행 실패 — dry plan 반환: {type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()   # 주입 스토어는 호출부가 소유 — 여기서 닫지 않는다.
    return base


# ── Public entry points ──────────────────────────────────────────────────────

def recall(query: str = "", *, owner: str | None = None, tenant: str | None = None,
           domain: str | None = None, workspace: str | None = None,
           tags: list[str] | None = None, rank: str = "recency", limit: int = 10,
           weights: dict | None = None, cfg=None, store=None) -> dict:
    res = execute(
        recall_plan(query, owner=owner, tenant=tenant, domain=domain,
                    workspace=workspace, tags=tags, rank=rank, limit=limit, weights=weights),
        cfg, store=store,
    )
    # preference 랭킹은 백엔드 밖 순수 함수로(§4-2). 플랜은 후보 전량을 date DESC 로
    # 가져오고, 여기서 점수·상한을 적용한다.
    if rank == "preference" and res.get("executed"):
        res["rows"] = score_preference(res["rows"], weights=weights, tags=tags, limit=limit)
    return res


def domains_plan(*, owner: str | None = None) -> QueryPlan:
    """Discover the learning domains present in the store — enumerated from the
    data, never a fixed list. Domains are whatever the learner chose to study; you
    find out *what* someone has learned by asking the graph, not a config enum.
    Optionally scoped to one ``owner``. Returns ``{domain, episodes}`` rows.

    Episode 전용 — Atlas L0(:func:`atlas_l0`)가 Concept 축과 별도로 이 플랜을 그대로
    재사용하므로(§ concept_domains_plan 병행) 여기서는 손대지 않는다. 도메인 목록
    화면의 Episode 편향 해소는 :func:`domain_counts_plan`/:func:`domains` 가 맡는다."""
    where = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL" if owner else "WHERE e.domain IS NOT NULL"
    params: dict = {"owner": owner} if owner else {}
    cypher = (
        f"MATCH (e:Episode) {where} "
        "RETURN e.domain AS domain, count(*) AS episodes "
        "ORDER BY episodes DESC"
    )
    return QueryPlan("domains", cypher, params, note="open-domain discovery (data-driven, not a fixed list)")


def domain_counts_plan(*, owner: str | None = None) -> QueryPlan:
    """`/domains` 의 Episode 편향 해소(C2) — Episode 만 집계하면 Concept·Record
    위주 도메인이 목록에서 통째로 빠진다(실사용 사고 원인). Episode·Concept·Record
    3개 노드 테이블을 도메인별로 각각 집계해 ``kind`` 태그와 함께 한 결과셋으로
    담는다 — 합산·정렬은 :func:`domains`(순수 후처리)가 맡는다.

    단일 Cypher(UNION ALL) — Kùzu 0.11.3 실측 지원 확인(2026-07): 서로 다른 라벨의
    MATCH 절을 UNION ALL 로 이어도 파라미터 공유·리터럴 컬럼이 그대로 동작한다."""
    if owner:
        where_e = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL"
        where_c = "WHERE c.owner_id = $owner AND c.domain IS NOT NULL"
        where_r = "WHERE r.owner_id = $owner AND r.domain IS NOT NULL"
        params: dict = {"owner": owner}
    else:
        where_e = "WHERE e.domain IS NOT NULL"
        where_c = "WHERE c.domain IS NOT NULL"
        where_r = "WHERE r.domain IS NOT NULL"
        params = {}
    cypher = (
        f"MATCH (e:Episode) {where_e} "
        "RETURN e.domain AS domain, count(*) AS n, 'episodes' AS kind "
        "UNION ALL "
        f"MATCH (c:Concept) {where_c} "
        "RETURN c.domain AS domain, count(*) AS n, 'concepts' AS kind "
        "UNION ALL "
        f"MATCH (r:Record) {where_r} "
        "RETURN r.domain AS domain, count(*) AS n, 'records' AS kind"
    )
    return QueryPlan("domains", cypher, params,
                     note="Episode+Concept+Record 집계 (open-domain discovery, data-driven)")


def domains(*, owner: str | None = None, cfg=None, store=None) -> dict:
    """도메인 목록(C2) — Episode·Concept·Record 3종을 모두 세어 `{domain, episodes,
    concepts, records, total}` 로 병합, total 내림차순 정렬. `episodes` 필드는
    하위호환으로 유지(값 = Episode 카운트만)."""
    res = execute(domain_counts_plan(owner=owner), cfg, store=store)
    if not res.get("executed"):
        return res
    agg: dict[str, dict] = {}
    for row in res.get("rows", []):
        dom = row.get("domain")
        if not dom:
            continue
        bucket = agg.setdefault(dom, {"domain": dom, "episodes": 0, "concepts": 0, "records": 0})
        kind = row.get("kind")
        if kind in bucket:
            bucket[kind] += int(row.get("n") or 0)
    rows = list(agg.values())
    for r in rows:
        r["total"] = r["episodes"] + r["concepts"] + r["records"]
    rows.sort(key=lambda r: (-r["total"], r["domain"]))
    res["rows"] = rows
    return res


def learner_model_plan(*, owner: str) -> QueryPlan:
    """Fetch an individual's *learning method* — their LearnerPattern nodes (how
    they learn: preferred structure, absorption style, cross-domain habits …).
    This is the most personal layer: the cognitive fingerprint that makes an
    export "my learning *and how I learn it*", not just a list of episodes."""
    cypher = (
        "MATCH (p:LearnerPattern) WHERE p.owner_id = $owner "
        "RETURN p.code AS code, p.name AS name, p.description AS description, "
        "p.domain AS domain, p.observations AS observations "
        "ORDER BY p.code"
    )
    return QueryPlan("learner_model", cypher, {"owner": owner}, note="personal learning-method model")


def learner_model(*, owner: str, cfg=None, store=None) -> dict:
    return execute(learner_model_plan(owner=owner), cfg, store=store)


def graph_plan(*, owner: str, domain: str | None = None) -> QueryPlan:
    """The learning knowledge-graph for an owner: Concept nodes + their relations
    (a row per concept, plus a row per outgoing Concept→Concept edge). ``domain``
    (free/open) optionally narrows to one domain. Shaped into nodes/edges by
    :func:`shape_graph`."""
    where = ["c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if domain:
        where.append("c.domain = $domain")
        params["domain"] = domain
    clause = "WHERE " + " AND ".join(where)
    # 관계 타입 함수는 `label(r)` — 임베디드(Kùzu 계보)에 `type()` 부재(§6-2 M4,
    # P1-0 실측 확정). Neo4j 는 label()도 지원하므로 백엔드 중립.
    cypher = (
        f"MATCH (c:Concept) {clause} "
        "OPTIONAL MATCH (c)-[r]->(d:Concept) "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags"
    )
    return QueryPlan("graph", cypher, params, note="learning knowledge-graph (concepts + relations)")


def _props_to_obj(raw) -> dict:
    """props 정규화(A1.9 A4) — 스토어는 props 를 JSON **문자열**로 저장한다(임베디드
    스키마). 다리(bridge)에서 vinc_ingest 로 바로 되먹일 수 있게 dict 로 파싱한다.
    dict 면 그대로, 문자열이면 json.loads, 실패·null 이면 {}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {"_": v}
        except Exception:
            return {}
    return {}


def shape_graph(rows: list[dict]) -> dict:
    """Turn graph rows into ``{nodes, edges}`` (pure). Nodes de-duplicated by id
    with degree counted; edges mark ``cross_domain`` when endpoints differ in
    domain. Domain is a free/open string — never interpreted against a list.

    A1.9(A4): 노드에 ``props``(dict)·``tags``(list)를 실어 다리에서 요약이 유실되지
    않게 한다(예전엔 id/label/domain/kind/degree 만 반환).

    F2/F3(2026-07-19): 행이 이종 kind(bridge/episode/record/entity)와 엣지 질화자
    (role)를 실어오면 그대로 셰이핑한다 — ``kind``/``target_kind`` 부재 시
    'concept'(하위호환), ``domain2``(브릿지 target_domain)는 존재 시에만 노드에
    부착. **degree 는 도착한 모든 엣지 행을 양 끝에 카운트** — 축 플랜(CONNECTS/
    ABOUT/ATTACHES_TO/TYPED)이 합류하는 것 자체가 F3(과소 카운트) 수리다(같은
    물리 엣지는 정확히 한 플랜에서만 오므로 이중 계상 없음). 노드 dedup 은 선착
    우선 — 호출부가 Concept 축을 먼저 넣는 고정 순서로 결정론을 보장한다."""
    nodes: dict[str, dict] = {}

    def _node(nid, label, dom, props=None, tags=None, kind=None, domain2=None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label or nid, "domain": dom or "",
                          "kind": kind or "concept", "degree": 0,
                          "props": _props_to_obj(props), "tags": list(tags or [])}
            if domain2:
                nodes[nid]["domain2"] = domain2
        return nodes[nid]

    edges: list[dict] = []
    for r in rows:
        nid = r.get("id")
        if not nid:
            continue
        _node(nid, r.get("label"), r.get("domain"), r.get("props"), r.get("tags"),
              r.get("kind"), r.get("domain2"))
        tgt = r.get("target")
        if tgt:
            # C5: target 은 target_label(=title)로 만든다 — 예전엔 id 를 label 로 넘겨
            # target-only 로 등장한 노드가 항상 id 노출로 폴백됐다(_node 의
            # `label or nid` 폴백은 title 이 정말 없을 때만 쓰여야 한다).
            _node(tgt, r.get("target_label"), r.get("target_domain"),
                  r.get("target_props"), r.get("target_tags"), r.get("target_kind"))
            src_dom, tgt_dom = r.get("domain"), r.get("target_domain")
            # cross_domain: 끝점 차이 규칙 유지 — 브릿지는 domain=source_domain 이라
            # 원측(source) 팔은 in-cluster, 반대측 팔만 cross(기하 정합, 특례 불요).
            cross = bool(src_dom and tgt_dom and src_dom != tgt_dom)
            edge = {"source": nid, "target": tgt,
                    "type": r.get("rel") or "RELATED_TO", "cross_domain": cross}
            if r.get("role"):
                edge["role"] = r["role"]
            edges.append(edge)
            nodes[nid]["degree"] += 1
            nodes[tgt]["degree"] += 1
    return {"nodes": list(nodes.values()), "edges": edges}


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


def attach_ppr(shaped: dict, seeds: list[str], *, alpha: float = 0.15,
               iters: int = 30) -> dict:
    """shaped 그래프의 각 노드에 ``ppr`` 필드(6자리 반올림)를 부여한다(E-G2, 가산
    메타 — 기존 소비처 무영향). seed ego 뷰·decide 에서 호출. 시드 없거나 노드 0이면
    무동작(빈 그래프 안전)."""
    nodes = shaped.get("nodes") or []
    if not nodes:
        return shaped
    scores = ppr_scores(nodes, shaped.get("edges") or [], seeds,
                        alpha=alpha, iters=iters)
    for nd in nodes:
        nd["ppr"] = round(scores.get(nd.get("id"), 0.0), 6)
    return shaped


def subgraph_ids_plan(seed: str, *, depth: int = 2, owner: str | None = None) -> QueryPlan:
    """Seed+depth 순회 — Atlas L2 노드 포커스와 MCP `vinc_graph` 의 공유 플랜(D16,
    MCP-SKELETON §10 공백 해소). `decide_plan` 과 동일한 TRAIL 가드(엣지 무재사용,
    §6-2 M3)로 seed 에서 depth 이내에 닿는 Concept id 를 수집한다.

    depth 의미(F2/F4 이후): 무라벨 순회는 모든 rel 테이블을 지난다 — Bridge 매개
    (CONNECTS 2홉)에 더해 ABOUT/ATTACHES_TO 도 홉이 되므로, depth-2 에서 같은
    에피소드/레코드에 공동 부착된 개념들이 서로의 이웃이 된다(브릿지 형제와 동형).
    부착도 구조라는 의도된 의미 — rel 타입 제한(-[:A|B *]-)은 방언 위험 대비 이득이
    없어 하지 않는다.

    ⚠ owner 필수성(SEC-1): 이 플랜은 여태 owner 를 받지 않아 **호스티드 멀티테넌트에서
    남의 개념을 순회**했다(`vinc_graph` 는 seed 필수라 항상 이 경로). owner 를 주면
    시드와 도달 노드를 모두 그 소유자로 제한한다. 중간 홉은 필터하지 않지만, 쓰기가
    owner 스탬프를 강제하므로 교차 소유자 엣지가 생기지 않아 결과는 owner-clean 이다.
    owner=None(로컬 단일 사용자)이면 기존 동작."""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": seed}
    n_where, m_where = "n.concept_id = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Concept) WHERE {n_where} "
        f"MATCH (n)-[* TRAIL 1..{depth}]-(m:Concept){m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("subgraph_ids", cypher, params,
                     note=f"seed+depth traversal (depth={depth})")


def graph_rows_for_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합의 Concept 행 + 나가는 엣지(§4-2 — 엣지의 서브그래프 외부 여과는
    파이썬 shape 단계에서). `graph_plan` 과 동일 투영.

    ⚠ owner 필수성(SEC-1): id 집합이 이미 owner-scoped 여도 **엣지 대상 d 는 별개**다 —
    필터가 없으면 남의 노드가 target_label/target_props 로 노출된다. 소유자를 주면
    행(c)과 엣지 대상(d)을 모두 제한한다."""
    params: dict = {"ids": ids}
    c_where, d_where = "c.concept_id IN $ids", ""
    if owner:
        c_where += " AND c.owner_id = $owner"
        d_where = " WHERE d.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (c:Concept) WHERE {c_where} "
        f"OPTIONAL MATCH (c)-[r]->(d:Concept){d_where} "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags"
    )
    return QueryPlan("graph", cypher, params, note="subgraph rows for id set")


# ── Atlas 축 플랜(F2 — Concept 단일 축 해소) ─────────────────────────────────
# 부착 축 3종(Bridge·Episode·Record)을 별도 순수 플랜으로 두고 파이썬 셰이핑에서
# 합류한다(UNION ALL 메가플랜 대신 — 이종 투영의 NULL 패딩 회피 + 플랜별 강등:
# 미마이그레이션 스토어에서 새 축만 조용히 빈 행이 되고 Concept 축은 산다).
# 공통 계약: **비-OPTIONAL 매치** — 부착 없는 항목은 렌더 대상이 아니다(Atlas =
# 연결 뷰; 미부착 Episode/Record 의 표면은 Records/L0 집계). domain(L1 렌즈)과
# ids(L2 ego 후조회)는 상호 배제. ids 모드는 **어느 한쪽 끝점**이 집합에 들면
# 매치 — concept 시드에선 팔이 kept 개념에 닿을 때(접착제), episode:/record:/
# bridge: 시드에선 자기 부착이 나온다(고립 폴백 대신 depth-1 이웃 렌더).

def bridge_rows_plan(*, owner: str, domain: str | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Bridge 노드 + CONNECTS 팔(F2). 노드 domain = source_domain(단일 결정값),
    target_domain 은 domain2 로 병기(상세 레일용). L1 소속은 선언 필드(source/
    target_domain)를 신뢰 — 실 CONNECTS 끝점과 어긋난 브릿지는 렌즈에서 빠질 수
    있다(L2 에선 보임, v1 수용). owner: Bridge 과도기 술어 + Concept 엄격."""
    assert not (domain and ids), "domain and ids are mutually exclusive"
    where = [_bridge_owner_pred("b"), "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if domain:
        where.append("(b.source_domain = $domain OR b.target_domain = $domain)")
        params["domain"] = domain
    if ids:
        where.append("(c.concept_id IN $ids OR b.bridge_id IN $ids)")
        params["ids"] = ids
    cypher = (
        "MATCH (b:Bridge)-[r:CONNECTS]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN b.bridge_id AS id, b.title AS label, b.source_domain AS domain, "
        "b.target_domain AS domain2, b.props AS props, 'bridge' AS kind, "
        "'CONNECTS' AS rel, r.role AS role, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind"
    )
    return QueryPlan("graph_axis_bridge", cypher, params, note="bridge axis rows (F2)")


def episode_rows_plan(*, owner: str, domain: str | None = None,
                      ids: list[str] | None = None) -> QueryPlan:
    """Episode 노드 + ABOUT 팔(F2/F4). L1 소속은 (자기 domain OR 부착 개념 domain)
    — 렌즈 밖 에피소드가 렌즈 안 개념에 부착된 경우가 정확히 F2 가 숨기던 연결이라
    OR 가 의도다."""
    assert not (domain and ids), "domain and ids are mutually exclusive"
    where = ["e.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if domain:
        where.append("(e.domain = $domain OR c.domain = $domain)")
        params["domain"] = domain
    if ids:
        where.append("(c.concept_id IN $ids OR e.episode_id IN $ids)")
        params["ids"] = ids
    cypher = (
        "MATCH (e:Episode)-[r:ABOUT]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS label, e.domain AS domain, "
        "e.props AS props, coalesce(e.tags, []) AS tags, 'episode' AS kind, "
        "'ABOUT' AS rel, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind"
    )
    return QueryPlan("graph_axis_episode", cypher, params, note="episode axis rows (F2/F4)")


def record_rows_plan(*, owner: str, domain: str | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Record 노드 + ATTACHES_TO 팔(F2). 엣지 질화자 r.type 은 role 슬롯으로 투영
    (CONNECTS.role 과 단일 선택 열 공유 — shape_graph 계약)."""
    assert not (domain and ids), "domain and ids are mutually exclusive"
    where = ["rc.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if domain:
        where.append("(rc.domain = $domain OR c.domain = $domain)")
        params["domain"] = domain
    if ids:
        where.append("(c.concept_id IN $ids OR rc.record_id IN $ids)")
        params["ids"] = ids
    cypher = (
        "MATCH (rc:Record)-[r:ATTACHES_TO]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN rc.record_id AS id, rc.title AS label, rc.domain AS domain, "
        "rc.props AS props, coalesce(rc.tags, []) AS tags, 'record' AS kind, "
        "'ATTACHES_TO' AS rel, r.type AS role, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind"
    )
    return QueryPlan("graph_axis_record", cypher, params, note="record axis rows (F2)")


_GRAPH_AXIS_PLANS = (bridge_rows_plan, episode_rows_plan, record_rows_plan)


# ── entity-시드 L2(F1) — TYPED 는 Entity↔Entity 폐쇄 성분 ────────────────────
# Entity 는 domain 열이 없고(테마 귀속) 어떤 레거시 rel 테이블에도 닿지 않으므로
# L1/개념시드 L2 에는 **구조적으로** 등장하지 않는다. 가시성은 entity: 시드 전용.

def entity_subgraph_ids_plan(seed: str, *, depth: int = 2,
                             owner: str | None = None) -> QueryPlan:
    """entity-시드 순회 — subgraph_ids_plan 과 동형(TRAIL 가드, depth 1..4 클램프).
    Entity 는 신규 테이블(레거시 '' 없음)이라 엄격 owner."""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": seed}
    n_where, m_where = "n.entity_id = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Entity) WHERE {n_where} "
        f"MATCH (n)-[* TRAIL 1..{depth}]-(m:Entity){m_where} "
        "RETURN DISTINCT m.entity_id AS id"
    )
    return QueryPlan("entity_subgraph_ids", cypher, params,
                     note=f"entity seed traversal (depth={depth})")


def entity_rows_for_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """Entity id 집합 행 + 나가는 TYPED 엣지. rel 은 label(r)="TYPED"(무의미한 캐리어
    이름)가 아니라 **r.type(사용자 저작 타입)** 을 투영 — 그것이 의미다. domain 은
    ''(Entity 무도메인 — entity-시드 L2 는 단일 중앙 클러스터로 레이아웃)."""
    params: dict = {"ids": ids}
    e_where, d_where = "e.entity_id IN $ids", ""
    if owner:
        e_where += " AND e.owner_id = $owner"
        d_where = " WHERE d.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (e:Entity) WHERE {e_where} "
        f"OPTIONAL MATCH (e)-[r:TYPED]->(d:Entity){d_where} "
        "RETURN e.entity_id AS id, e.title AS label, '' AS domain, "
        "coalesce(e.tags, []) AS tags, 'entity' AS kind, "
        "r.type AS rel, d.entity_id AS target, '' AS target_domain, "
        "d.title AS target_label, coalesce(d.tags, []) AS target_tags, "
        "'entity' AS target_kind"
    )
    return QueryPlan("entity_graph", cypher, params, note="entity rows for id set (TYPED)")


_SEED_TABLES = {   # seed id 접두사 → (라벨, PK 열) — C4 선조회용 (+ entity, F1)
    "concept": ("Concept", "concept_id"),
    "episode": ("Episode", "episode_id"),
    "record": ("Record", "record_id"),
    "bridge": ("Bridge", "bridge_id"),
    "entity": ("Entity", "entity_id"),
}


def _seed_table(seed: str) -> tuple[str, str]:
    """seed id → (라벨, PK 열). 접두사 없음(콜론 부재) = Concept(하위호환).
    **미지 접두사 = Entity**(F1) — 개방층 노드 id 는 사용자 저작 타입이 접두사라
    (`theory:duality` 등) 열거 불가능하다; 레거시 접두사는 전부 위 표에 있으므로
    표 밖 접두사는 개방층으로 라우팅하는 것이 정확하다(실측: 구 Concept 폴백은
    theory:* 시드를 '없음'으로 오판)."""
    prefix = seed.split(":", 1)[0] if ":" in seed else ""
    if not prefix:
        return ("Concept", "concept_id")
    return _SEED_TABLES.get(prefix, ("Entity", "entity_id"))


def seed_lookup_plan(seed: str, *, owner: str | None = None) -> QueryPlan:
    """seed 노드 존재 선조회(C4) — '노드 없음'과 '노드는 있으나 연결 0(고립)'을
    구분하기 위한 최소 조회(id·title 만). id 접두사(`concept:`·`episode:`·
    `record:`·`bridge:`·`entity:`)로 테이블을 판별하고, 접두사가 없거나 미지의
    접두사면 Concept 로 시도한다. `graph()` 는 순회 결과가 비었을 때만 이 플랜을
    쓴다 — 순회가 이미 노드를 찾았다면(=시드가 확실히 존재) 굳이 다시 묻지 않는다.

    owner(SEC-1): Concept/Episode/Record/Entity 는 엄격 owner 로 제한한다. Bridge 는
    v2(F5)에서 owner_id 를 얻어 과도기 술어(:func:`_bridge_owner_pred`)로 필터한다
    — 구 '필터 불가' 예외(존재 여부 노출)는 해소됐다. 미지 접두사는 Entity 로
    프로브한다(:func:`_seed_table` — 개방층 id 는 임의 접두사)."""
    label, pk = _seed_table(seed)
    params: dict = {"seed": seed}
    where = f"n.{pk} = $seed"
    if owner:
        where += (f" AND {_bridge_owner_pred('n')}" if label == "Bridge"
                  else " AND n.owner_id = $owner")
        params["owner"] = owner
    cypher = f"MATCH (n:{label}) WHERE {where} RETURN n.{pk} AS id, n.title AS title"
    return QueryPlan("seed_lookup", cypher, params, note=f"seed existence probe ({label})")


LAYOUTS = ("shell", "radial", "circle", "tree", "organic")
_DIRECTED_RELS = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")


def _cluster_shell(members, ids, adj, directed, math, GOLDEN):
    """fibonacci 구면 산개(기본) — 허브가 중심, 반경 = 0.08·√i."""
    count = len(members)
    pos = {}
    for i, n in enumerate(members):
        r = 0.08 * math.sqrt(i)
        t = i / max(count - 1, 1)
        phi = math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * t)))   # 극각 — 구면 균등
        theta = i * GOLDEN
        pos[n["id"]] = (r * math.sin(phi) * math.cos(theta),
                        r * math.sin(phi) * math.sin(theta),
                        r * math.cos(phi))
    return pos


def _cluster_radial(members, ids, adj, directed, math, GOLDEN):
    """Centralized Burst — 허브(정렬 1위)가 중심, BFS 깊이 = 동심 껍질 반경."""
    inset = set(ids)
    depth = {ids[0]: 0}
    frontier = [ids[0]]
    while frontier:
        nxt = []
        for u in frontier:
            for v in sorted(adj.get(u, ())):
                if v in inset and v not in depth:
                    depth[v] = depth[u] + 1
                    nxt.append(v)
        frontier = nxt
    stray = max(depth.values(), default=0) + 1   # 미도달(고아) — 최외곽 껍질
    counts: dict[int, int] = {}
    for n in members:
        d = depth.get(n["id"], stray)
        counts[d] = counts.get(d, 0) + 1
    ring: dict[int, int] = {}
    pos = {}
    for n in members:
        d = depth.get(n["id"], stray)
        j = ring[d] = ring.get(d, -1) + 1
        k = counts[d]
        # 껍질 반경은 노드 수에 적응 — 둘레 간격이 노드 지름(≈0.17)을 넘도록
        r = max(0.14 * d, 0.033 * k) if d else 0.0
        theta = 2 * math.pi * j / max(k, 1) + d * 0.5   # 껍질 내 균등 + 위상 오프셋
        pos[n["id"]] = (r * math.cos(theta), r * math.sin(theta),
                        0.04 * d * math.sin(theta * 0.7))
    return pos


def _cluster_circle(members, ids, adj, directed, math, GOLDEN):
    """Circular Ties — 원둘레 정렬(정렬 순서 유지), 엣지는 현(chord)으로 읽힌다."""
    count = len(members)
    R = min(0.42, 0.10 * math.sqrt(count) + 0.08)
    pos = {}
    for i, n in enumerate(members):
        theta = 2 * math.pi * i / max(count, 1)
        pos[n["id"]] = (R * math.cos(theta), R * math.sin(theta), 0.0)
    return pos


def _cluster_tree(members, ids, adj, directed, math, GOLDEN):
    """Ramification — 방향 관계(prerequisite/derives/inherits)로 계층 배치.
    루트 = 방향 유입이 없는 노드, 층 = BFS 깊이(순환은 방문 가드로 절단).
    방향 엣지에 안 걸린 노드는 최하단 별도 층."""
    inset = set(ids)
    incoming = {i: 0 for i in ids}
    for s, targets in directed.items():
        if s not in inset:
            continue
        for t in targets:
            if t in inset:
                incoming[t] = incoming.get(t, 0) + 1
    involved = {i for i in ids
                if incoming.get(i, 0) or any(t in inset for t in directed.get(i, ()))}
    roots = sorted(i for i in involved if incoming.get(i, 0) == 0) or sorted(involved)[:1]
    layer = {r: 0 for r in roots}
    frontier = list(roots)
    while frontier:
        nxt = []
        for u in frontier:
            for v in sorted(directed.get(u, ())):
                if v in inset and v not in layer:
                    layer[v] = layer[u] + 1
                    nxt.append(v)
        frontier = nxt
    bottom = max(layer.values(), default=0) + 1
    rows: dict[int, list] = {}
    for n in members:
        rows.setdefault(layer.get(n["id"], bottom), []).append(n["id"])
    total = len(rows)
    pos = {}
    for li, (lv, row) in enumerate(sorted(rows.items())):
        y = (total - 1) / 2 * 0.13 - li * 0.13     # 위(루트) → 아래
        k = len(row)
        for j, nid in enumerate(row):
            pos[nid] = ((j - (k - 1) / 2) * 0.12, y, 0.0)
    return pos


def _cluster_organic(members, ids, adj, directed, math, GOLDEN):
    """Organic Rhizome — 힘-지향을 **시드 고정 + 반복 수 고정**으로 결정론화(D17
    유지). 초기 배치 = shell(순수), 척력 1/d² + 엣지 스프링, 60 반복 후 정규화."""
    pos = {k: list(v) for k, v in
           _cluster_shell(members, ids, adj, directed, math, GOLDEN).items()}
    inset = set(ids)
    springs = [(u, v) for u in ids for v in sorted(adj.get(u, ()))
               if v in inset and u < v]
    K_REP, K_SPR, REST, STEP = 0.009, 0.05, 0.34, 1.0
    for _ in range(60):
        force = {i: [0.0, 0.0, 0.0] for i in ids}
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                u, v = ids[a], ids[b]
                dx = [pos[u][c] - pos[v][c] for c in range(3)]
                d2 = max(dx[0] ** 2 + dx[1] ** 2 + dx[2] ** 2, 1e-6)
                f = min(K_REP / d2, 0.05)
                d = math.sqrt(d2)
                for c in range(3):
                    force[u][c] += f * dx[c] / d
                    force[v][c] -= f * dx[c] / d
        for u, v in springs:
            dx = [pos[u][c] - pos[v][c] for c in range(3)]
            d = max(math.sqrt(dx[0] ** 2 + dx[1] ** 2 + dx[2] ** 2), 1e-6)
            f = K_SPR * (d - REST)
            for c in range(3):
                force[u][c] -= f * dx[c] / d
                force[v][c] += f * dx[c] / d
        for i in ids:
            for c in range(3):
                pos[i][c] += max(-0.04, min(0.04, force[i][c] * STEP))
    radii = sorted(math.sqrt(sum(p[c] ** 2 for c in range(3))) for p in pos.values())
    # 80분위 반경 기준 정규화(양방향) — 최외곽(척력에 밀린 고아)이 아니라 본체
    # 밀도를 목표에 맞춘다. rmax 기준은 고아 하나가 본체를 상대 압축시킨다(실측).
    r_ref = radii[int(0.8 * (len(radii) - 1))] if radii else 0.0
    scale = 0.30 / r_ref if r_ref > 1e-6 else 1.0
    return {i: (p[0] * scale, p[1] * scale, p[2] * scale) for i, p in pos.items()}


_CLUSTER_LAYOUTS = {"shell": _cluster_shell, "radial": _cluster_radial,
                    "circle": _cluster_circle, "tree": _cluster_tree,
                    "organic": _cluster_organic}


def layout_positions(nodes: list[dict], edges: list[dict],
                     layout: str = "shell") -> list[dict]:
    """결정론 3D 레이아웃(D17 + D21 개정 + D34 프리셋) — **순수 함수**, 난수·시간
    무의존이라 같은 그래프는 항상 같은 좌표(플랜 순수성 계약과 동형). 도메인
    클러스터 중심을 xy 단위원 둘레(z=0)에 정렬 배치하고, 클러스터 내부는 프리셋
    (shell·radial·circle·tree·organic — D34, 미지 값은 shell 강등)으로 편다.
    내부 정렬은 (pinned DESC, degree DESC, id) — pin 이 허브 승격 1순위(D32)."""
    import math
    GOLDEN = 2.399963229728653
    cluster_fn = _CLUSTER_LAYOUTS.get(layout, _cluster_shell)
    adj: dict[str, set] = {}
    directed: dict[str, set] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        if (e.get("type") or "") in _DIRECTED_RELS:
            directed.setdefault(s, set()).add(t)
    by_domain: dict[str, list[dict]] = {}
    for n in nodes:
        by_domain.setdefault(n.get("domain") or "", []).append(n)
    domains = sorted(by_domain)
    d_count = max(len(domains), 1)
    for di, dom in enumerate(domains):
        if d_count == 1:
            cx, cy = 0.0, 0.0
        else:
            ang = 2 * math.pi * di / d_count
            cx, cy = math.cos(ang), math.sin(ang)
        members = sorted(by_domain[dom],
                         key=lambda n: (-int(bool(n.get("pinned"))),
                                        -int(n.get("degree") or 0), str(n.get("id"))))
        ids = [n["id"] for n in members]
        pos = cluster_fn(members, ids, adj, directed, math, GOLDEN)
        for n in members:
            lx, ly, lz = pos[n["id"]]
            n["x"] = round(cx + lx, 6)
            n["y"] = round(cy + ly, 6)
            n["z"] = round(lz, 6)
    return nodes


def graph(*, owner: str, domain: str | None = None, seed: str | None = None,
          depth: int = 2, layout: str = "shell", cfg=None, store=None) -> dict:
    """지식 그래프 — 기본은 owner(±domain) 스코프 전체(Atlas L1), ``seed`` 지정 시
    seed+depth ego 서브그래프(Atlas L2 = MCP `vinc_graph` 동일 경로). 노드에는
    결정론 레이아웃 좌표(x,y,z)가 포함된다(D17) — ``layout`` 프리셋은 D34
    (shell·radial·circle·tree·organic, 미지 값 shell 강등).

    F2(2026-07-19): Concept 단일 축 해소 — 축 플랜 3종(Bridge·Episode·Record)이
    합류해 부착 노드·엣지가 렌더된다. L2 에선 매개자(브릿지 등)가 keep 집합에
    편입돼 '보이지 않는 접착제'(kept 개념 둘이 엣지 없이 떠 있던 문제)가 사라진다.
    entity: 시드는 전용 순회(TYPED 폐쇄 성분 — :func:`entity_subgraph_ids_plan`)."""
    if seed:
        # SEC-1: seed 갈래도 owner 스코프를 지킨다. 여태 owner 를 받고도 쓰지 않아
        # 호스티드에서 남의 서브그래프가 그대로 보였다(vinc_graph 는 seed 필수).
        # entity 판정은 접두사 라우팅(_seed_table) — 개방층 id 는 임의 접두사(F1).
        entity_seed = _seed_table(seed)[0] == "Entity"
        ids_res = execute(
            entity_subgraph_ids_plan(seed, depth=depth, owner=owner) if entity_seed
            else subgraph_ids_plan(seed, depth=depth, owner=owner), cfg, store=store)
        if not ids_res.get("executed"):
            return {"executed": False, "seed": seed, "depth": depth, "nodes": [], "edges": [],
                    "note": ids_res.get("note", "")}
        ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
        res = execute(
            entity_rows_for_ids_plan(ids, owner=owner) if entity_seed
            else graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
        rows = list(res.get("rows", []))
        keep = set(ids)   # 엣지의 depth+1 유출 대상 제거(ego 경계)
        if not entity_seed:
            # F2 — 접착제 후조회: 축 플랜을 ids 모드로 실행해 매개자(브릿지·에피소드·
            # 레코드)와 그 팔을 합류시키고, 양 끝점을 keep 에 편입한다(매개자 렌더 +
            # episode:/record:/bridge: 시드의 depth-1 이웃). 플랜별 강등이 미마이그레이션
            # 스토어를 자가 처리한다(빈 행 — Concept 축은 산다).
            for plan_fn in _GRAPH_AXIS_PLANS:
                ax = execute(plan_fn(owner=owner, ids=ids), cfg, store=store)
                if not ax.get("executed"):
                    continue
                ax_rows = ax.get("rows", [])
                rows += ax_rows
                for r in ax_rows:
                    if r.get("id"):
                        keep.add(r["id"])
                    if r.get("target"):
                        keep.add(r["target"])
        shaped = shape_graph(rows)
        shaped["edges"] = [e for e in shaped["edges"] if e["target"] in keep and e["source"] in keep]
        shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
        executed = res.get("executed", False)
        found = True
        if not shaped["nodes"]:
            # C4: 순회·행 조회가 둘 다 비었다 — '시드 자체가 없음'과 '시드는 있으나
            # 연결이 0(고립)'이 여태 구분 불가했다(실사용에서 실재 episode 시드가
            # 빈 응답을 받아 '데이터 없음'으로 오판됨). 이 갈래에서만 선조회한다 —
            # Concept/Entity 시드가 실재하면 위 rows 플랜이 이미 자기 자신을 한 행
            # (고립 시 rel/target=NULL)으로 돌려주므로 shaped["nodes"] 가 비지 않고,
            # 부착 있는 episode/record/bridge 시드는 축 플랜(ids 모드)이 잡는다.
            # 여기 도달하는 건 시드가 아예 없거나, Concept 밖 타입이 **부착도 0**
            # 인(진짜 고립) 경우뿐이다.
            seed_res = execute(seed_lookup_plan(seed, owner=owner), cfg, store=store)
            if not seed_res.get("executed"):
                return {"executed": False, "seed": seed, "depth": depth, "nodes": [], "edges": [],
                        "note": seed_res.get("note", "")}
            seed_row = next((r for r in seed_res.get("rows", []) if r.get("id") == seed), None)
            found = seed_row is not None
            if found:
                kind = seed.split(":", 1)[0] if ":" in seed else "concept"
                shaped["nodes"] = [{"id": seed, "label": seed_row.get("title") or seed,
                                    "domain": "", "kind": kind, "degree": 0,
                                    "props": {}, "tags": []}]   # A4 형태 정합
        # E-G2 — 시드 관점 개인화 랭킹(ppr). layout 전에 부여(순수 메타, 좌표 무관).
        attach_ppr(shaped, [seed])
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout)
        return {"executed": executed, "seed": seed, "depth": depth, "found": found, **shaped}
    res = execute(graph_plan(owner=owner, domain=domain), cfg, store=store)
    rows = list(res.get("rows", []))
    # F2 — 부착 축 합류(L1/전체 뷰). 고정 순서 concat: Concept 축 선행 = 노드 dedup
    # 의 선착 우선이 Concept 투영을 이기게(결정론).
    for plan_fn in _GRAPH_AXIS_PLANS:
        ax = execute(plan_fn(owner=owner, domain=domain), cfg, store=store)
        if ax.get("executed"):
            rows += ax.get("rows", [])
    shaped = shape_graph(rows)
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout)
    return {"executed": res.get("executed", False), **shaped}


def domain_ties_plan(*, owner: str) -> QueryPlan:
    """Atlas L0 결합 행렬(D16) — 도메인이 다른 Concept 간 직접 엣지를 도메인 쌍으로 집계."""
    cypher = (
        "MATCH (a:Concept)-[r]->(b:Concept) "
        "WHERE a.owner_id = $owner AND b.owner_id = $owner AND a.domain <> b.domain "
        "RETURN a.domain AS source, b.domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("domain_ties", cypher, {"owner": owner}, note="cross-domain tie matrix (L0)")


def _bridge_owner_pred(alias: str) -> str:
    """Bridge 소유 술어(F5, **과도기**) — ``(owner OR '')``. '' = v2 마이그레이션이
    백필한 레거시 미소유 행: 신뢰할 소유 원천이 없어(호스티드 다중 테넌트 혼재)
    실소유 백필이 불가하고, 엄격 ``= $owner`` 는 기존 로컬 사용자의 L0 결합을 즉시
    소거하는 회귀다. 이 술어는 오늘의 완전 무스코프(전 테넌트 전량 노출)를 "내 것 +
    마이그레이션 이전분"으로 좁히는 단조 개선이며, 신규 쓰기는 전부 스탬프되므로
    (fragment·bridges_ingest) 새 데이터는 누출 불가. 로컬 백필(bridges_ingest
    재실행)이 보편화되면 OR 절만 삭제해 엄격 전환한다 — 이 함수가 유일 정의 지점."""
    return f"({alias}.owner_id = $owner OR {alias}.owner_id = '')"


def bridge_ties_plan(*, owner: str | None = None) -> QueryPlan:
    """L0 결합 행렬의 Bridge 축 — 실데이터(weg-9000 허브)에서 개념 간 엣지는 전부
    도메인 내부이고 크로스도메인은 :Bridge 가 1급으로 담는다(2026-07-10 실측).
    v2(F5)에서 Bridge 가 owner_id 를 얻어 owner 지정 시 과도기 술어로 스코프한다
    (구 '소유 스탬프 없음 → 필터 불가' 제약 해소 — 호스티드 교차테넌트 집계 차단)."""
    where = "b.source_domain IS NOT NULL AND b.target_domain IS NOT NULL"
    params: dict = {}
    if owner:
        where += f" AND {_bridge_owner_pred('b')}"
        params["owner"] = owner
    cypher = (
        "MATCH (b:Bridge) "
        f"WHERE {where} "
        "RETURN b.source_domain AS source, b.target_domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("bridge_ties", cypher, params, note="bridge tie matrix (L0)")


def direct_coupling_plan(*, owner: str, a: str, b: str) -> QueryPlan:
    """Compare 뷰(D22) — 두 도메인 사이 **개념 대 개념** 직접 크로스도메인 엣지."""
    cypher = (
        "MATCH (x:Concept)-[r]->(y:Concept) "
        "WHERE x.owner_id = $owner AND y.owner_id = $owner "
        "AND ((x.domain = $a AND y.domain = $b) OR (x.domain = $b AND y.domain = $a)) "
        "RETURN x.concept_id AS a_id, x.title AS a_title, x.domain AS a_domain, "
        "y.concept_id AS b_id, y.title AS b_title, label(r) AS rel"
    )
    return QueryPlan("coupling_direct", cypher, {"owner": owner, "a": a, "b": b},
                     note="concept-level direct cross-domain edges")


def bridge_coupling_plan(*, a: str, b: str, owner: str | None = None) -> QueryPlan:
    """Compare 뷰(D22) — Bridge 매개 개념쌍: (cA)←CONNECTS─(Bridge)─CONNECTS→(cB),
    cA.domain=$a · cB.domain=$b. weg-9000 실데이터에서 직접 엣지는 0 이고 결합은
    Bridge 가 담당(실측) — 매개쌍이 주 신호다. owner 지정 시(F5) Concept 양끝은
    엄격, Bridge 는 과도기 술어(:func:`_bridge_owner_pred`)로 스코프한다."""
    where = "x.domain = $a AND y.domain = $b"
    params: dict = {"a": a, "b": b}
    if owner:
        where += (" AND x.owner_id = $owner AND y.owner_id = $owner "
                  f"AND {_bridge_owner_pred('br')}")
        params["owner"] = owner
    cypher = (
        "MATCH (x:Concept)<-[:CONNECTS]-(br:Bridge)-[:CONNECTS]->(y:Concept) "
        f"WHERE {where} "
        "RETURN x.concept_id AS a_id, x.title AS a_title, "
        "y.concept_id AS b_id, y.title AS b_title, "
        "br.bridge_id AS via, br.title AS via_title"
    )
    return QueryPlan("coupling_bridge", cypher, params,
                     note="bridge-mediated concept pairs")


def coupling(*, owner: str, a: str, b: str, cfg=None, store=None) -> dict:
    """두 도메인의 개념 결합쌍(D22) — 직접 엣지 ∪ Bridge 매개. 쌍 단위 dedup,
    직접 결합 우선 정렬."""
    d = execute(direct_coupling_plan(owner=owner, a=a, b=b), cfg, store=store)
    br = execute(bridge_coupling_plan(a=a, b=b, owner=owner), cfg, store=store)
    pairs: dict[tuple[str, str], dict] = {}
    for r in d.get("rows", []):
        # 방향 정규화: a 도메인 쪽을 왼쪽에.
        left_first = r.get("a_domain") == a
        ai, at = (r["a_id"], r["a_title"]) if left_first else (r["b_id"], r["b_title"])
        bi, bt = (r["b_id"], r["b_title"]) if left_first else (r["a_id"], r["a_title"])
        pairs[(ai, bi)] = {"a_id": ai, "a_title": at or ai, "b_id": bi,
                           "b_title": bt or bi, "kind": "direct", "rel": r.get("rel")}
    for r in br.get("rows", []):
        key = (r["a_id"], r["b_id"])
        if key not in pairs:   # 직접 결합이 매개보다 강한 신호 — 우선
            pairs[key] = {"a_id": r["a_id"], "a_title": r.get("a_title") or r["a_id"],
                          "b_id": r["b_id"], "b_title": r.get("b_title") or r["b_id"],
                          "kind": "bridge", "via": r.get("via"),
                          "via_title": r.get("via_title")}
    out = sorted(pairs.values(), key=lambda p: (p["kind"] != "direct", p["a_title"]))
    return {"executed": bool(d.get("executed") or br.get("executed")),
            "a": a, "b": b, "pairs": out}


def concept_domains_plan(*, owner: str) -> QueryPlan:
    """L0 도메인 집계의 Concept 축 — Episode 없는 코퍼스(개념 위주 허브, 실측:
    weg-9000 적재분)에서도 L0 가 그려져야 한다."""
    cypher = (
        "MATCH (c:Concept) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
        "RETURN c.domain AS domain, count(*) AS records ORDER BY records DESC"
    )
    return QueryPlan("concept_domains", cypher, {"owner": owner}, note="concept counts per domain (L0)")


def atlas_l0(*, owner: str, cfg=None, store=None) -> dict:
    """Atlas L0 Domain Atlas 데이터(D16) — 도메인별 레코드 수(Episode ∪ Concept
    합산) + 도메인 간 결합 행렬(Concept 직접 엣지 + Bridge 합산). UI 는 이 응답만으로
    L0 를 그린다(그래프 로직 UI 반입 금지)."""
    d = execute(domains_plan(owner=owner), cfg, store=store)
    c = execute(concept_domains_plan(owner=owner), cfg, store=store)
    counts: dict[str, int] = {}
    for row in d.get("rows", []):
        counts[row["domain"]] = counts.get(row["domain"], 0) + int(row.get("episodes") or 0)
    for row in c.get("rows", []):
        counts[row["domain"]] = counts.get(row["domain"], 0) + int(row.get("records") or 0)
    domains = [{"domain": k, "episodes": v} for k, v in
               sorted(counts.items(), key=lambda kv: -kv[1])]
    t = execute(domain_ties_plan(owner=owner), cfg, store=store)
    b = execute(bridge_ties_plan(owner=owner), cfg, store=store)
    merged: dict[tuple[str, str], int] = {}
    for row in [*t.get("rows", []), *b.get("rows", [])]:
        key = (row.get("source") or "", row.get("target") or "")
        if key[0] and key[1]:
            merged[key] = merged.get(key, 0) + int(row.get("ties") or 0)
    ties = [{"source": s, "target": tg, "ties": n}
            for (s, tg), n in sorted(merged.items(), key=lambda kv: -kv[1])]
    return {"executed": bool(d.get("executed") or c.get("executed")),
            "domains": domains, "ties": ties}


def promote_candidates_plan(*, owner: str, min_access: int = 3) -> QueryPlan:
    """확정(pin) 후보 조회(Phase E1 — 조회 전용, 쓰기는 기존 /pin 경유라 단일 쓰기
    표면 유지). 반복 관측된 발견 = 아직 pin 되지 않았는데 실사용 신호(access_count·
    tags)가 임계를 넘는 Episode — weg-9000 pattern-promoter 의 "N≥3 승격"을 실측
    신호로 재구현한 것(학습 어휘 미반입, 갭 분석 권고 4)."""
    cypher = (
        "MATCH (e:Episode) WHERE e.owner_id = $owner "
        "AND coalesce(e.pinned, false) = false "
        "AND (coalesce(e.access_count, 0) >= $min_access OR size(coalesce(e.tags, [])) >= 2) "
        "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
        "coalesce(e.access_count, 0) AS access_count, coalesce(e.tags, []) AS tags, "
        "e.last_access AS last_access "
        "ORDER BY access_count DESC LIMIT 20"
    )
    return QueryPlan("promote_candidates", cypher,
                     {"owner": owner, "min_access": int(min_access)},
                     note="pin candidates from measured signals (read-only)")


def promote_candidates(*, owner: str, min_access: int = 3, cfg=None, store=None) -> dict:
    return execute(promote_candidates_plan(owner=owner, min_access=min_access), cfg, store=store)


def bridge_neighbors_plan(node: str, *, owner: str) -> QueryPlan:
    """잇기 제안의 접착제 인지(F3) — ``node`` 와 Bridge 매개로 이미 이어진 개념
    (CONNECTS 양팔 반대편). Bridge 는 과도기 술어, 개념은 엄격 owner."""
    cypher = (
        "MATCH (x:Concept)<-[:CONNECTS]-(b:Bridge)-[:CONNECTS]->(y:Concept) "
        f"WHERE x.concept_id = $node AND {_bridge_owner_pred('b')} "
        "AND y.owner_id = $owner "
        "RETURN DISTINCT y.concept_id AS id"
    )
    return QueryPlan("bridge_neighbors", cypher, {"node": node, "owner": owner},
                     note="bridge-mediated neighbors (tie-suggest exclusion)")


def concept_meta_plan(*, owner: str) -> QueryPlan:
    """잇기 제안용 Concept 메타(id·title·domain·tags) — 순수 조회."""
    cypher = (
        "MATCH (c:Concept) WHERE c.owner_id = $owner "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "coalesce(c.tags, []) AS tags ORDER BY id"
    )
    return QueryPlan("concept_meta", cypher, {"owner": owner},
                     note="concept metadata for tie suggestions")


def suggest_ties(*, owner: str, node: str, limit: int = 5, cfg=None, store=None) -> dict:
    """고아(저연결) 노드의 잇기 후보 — D25(임베딩 유사도)의 **전 단계** 결정론 근사
    (D32): 같은 도메인 +1, 태그 교집합 각 +2. 이미 연결된 노드는 제외. 조회 전용 —
    확정(엣지 생성)은 fragment MERGE(POST /ingest/fragment) 경유로 단일 쓰기
    표면(R4)을 유지한다."""
    meta = execute(concept_meta_plan(owner=owner), cfg, store=store)
    if not meta.get("executed"):
        return {"executed": False, "node": node, "suggestions": [],
                "note": meta.get("note", "")}
    rows = meta.get("rows", [])
    me = next((r for r in rows if r.get("id") == node), None)
    if me is None:
        return {"executed": True, "node": node, "suggestions": [],
                "note": f"unknown concept: {node}"}
    g = execute(graph_plan(owner=owner), cfg, store=store)
    linked = {node}
    for r in g.get("rows", []):
        if r.get("id") == node and r.get("target"):
            linked.add(r["target"])
        elif r.get("target") == node and r.get("id"):
            linked.add(r["id"])
    # F3 — 접착제 인지: 브릿지 매개로 이미 이어진 개념도 linked 에 합류시켜 기존
    # 글루를 중복 제안하지 않는다(graph_plan 은 CC 직접 엣지만 본다).
    bn = execute(bridge_neighbors_plan(node, owner=owner), cfg, store=store)
    if bn.get("executed"):
        linked.update(r["id"] for r in bn.get("rows", []) if r.get("id"))
    my_tags = set(me.get("tags") or [])
    my_domain = me.get("domain") or ""
    out = []
    for r in rows:
        cid = r.get("id")
        if not cid or cid in linked:
            continue
        shared = sorted(my_tags & set(r.get("tags") or []))
        score = 2 * len(shared) + (1 if my_domain and r.get("domain") == my_domain else 0)
        if score <= 0:
            continue
        out.append({"id": cid, "title": r.get("title") or cid,
                    "domain": r.get("domain") or "", "shared_tags": shared,
                    "score": score})
    out.sort(key=lambda s: (-s["score"], s["id"]))   # 결정론 순서
    return {"executed": True, "node": node,
            "suggestions": out[:max(1, int(limit))]}


# ── 단일 홉 관측 창구 (E-G5) — SoG Observe-then-Navigate 경량 프리미티브 ────────
# 리서치 SoG(Search-on-Graph)의 "단일 홉 관측 창구"를 경량 MCP 표면으로 제공한다.
# 상류 모델이 depth-2 ego 전체(vinc_graph 최소 단위) 대신 한 홉씩 관측·항해하게 해
# 토큰을 아낀다(-69.9% 근거). 조회 전용 — 개념의 1-hop 관계망을 rel 타입별로 요약.

def neighbors_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """개념의 1-hop 이웃(무방향) — rel 타입·이웃 id/title/domain. `label(r)`(임베디드
    타입 함수 §6-2 M4). owner 지정 시 양 끝 스코프."""
    params: dict = {"node": node}
    n_where, m_where = "n.concept_id = $node", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " AND m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Concept)-[r]-(m:Concept) WHERE {n_where}{m_where} "
        "RETURN label(r) AS rel, m.concept_id AS id, m.title AS title, m.domain AS domain"
    )
    return QueryPlan("neighbors", cypher, params, note="single-hop observation (SoG)")


def neighbors(node: str, *, owner: str | None = None, limit: int = 50,
              cfg=None, store=None) -> dict:
    """E-G5 — 개념의 1-hop 관계망 요약(조회 전용). 반환 {executed, node,
    neighbors:[{rel,id,title,domain}], by_rel:{rel:count}, count}. 결정론 순서
    (rel, id). 상류 SoG 항해의 관측 표면."""
    res = execute(neighbors_plan(node, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return {"kind": "neighbors", "executed": False, "node": node,
                "neighbors": [], "by_rel": {}, "count": 0, "note": res.get("note", "")}
    seen: set = set()
    out: list[dict] = []
    by_rel: dict[str, int] = {}
    for r in sorted(res.get("rows", []),
                    key=lambda r: (str(r.get("rel")), str(r.get("id")))):
        key = (r.get("rel"), r.get("id"))
        if not r.get("id") or key in seen:
            continue
        seen.add(key)
        out.append({"rel": r.get("rel"), "id": r.get("id"),
                    "title": r.get("title") or r.get("id"), "domain": r.get("domain") or ""})
        by_rel[r.get("rel")] = by_rel.get(r.get("rel"), 0) + 1
    return {"kind": "neighbors", "executed": True, "node": node,
            "neighbors": out[:max(1, int(limit))], "by_rel": by_rel, "count": len(out)}


# ── 최단 경로 증거 체인 (E-G7) — 분산 증거 단편화 해소 ─────────────────────────
# 리서치 최단 경로 — 두 개념 사이 물리적으로 연결된 가장 조밀한 엣지 체인을 인출해
# 상류 모델에 증거로 준다(추측 없는 인과 증빙). kuzu `* ALL SHORTEST`(실측 확인) —
# 동률 최단 경로 전부. TRAIL ego 순회(목표 미지정)와 달리 **목표쌍 지정형**.

def shortest_path_plan(a_id: str, b_id: str, *, max_depth: int = 6,
                       owner: str | None = None) -> QueryPlan:
    """두 개념 간 최단 경로(들). `* ALL SHORTEST 1..d`(엔드포인트 owner 스코프 —
    교차소유자 엣지 부재라 중간 홉 스코프 불요). nodes(p) 시퀀스 + 길이 반환."""
    depth = max(1, min(int(max_depth), 8))
    params: dict = {"a": a_id, "b": b_id}
    a_where, b_where = "a.concept_id = $a", "b.concept_id = $b"
    if owner:
        a_where += " AND a.owner_id = $owner"
        b_where += " AND b.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH p = (a:Concept)-[* ALL SHORTEST 1..{depth}]-(b:Concept) "
        f"WHERE {a_where} AND {b_where} "
        "RETURN nodes(p) AS ns, length(p) AS len ORDER BY len LIMIT 25"
    )
    return QueryPlan("shortest_path", cypher, params,
                     note=f"all-shortest concept path (≤{depth})")


def _node_seq(ns) -> list[dict]:
    """nodes(p) 원소(노드 dict)에서 {id,title} 시퀀스 추출(방언 키 방어)."""
    out = []
    for n in ns or []:
        if isinstance(n, dict):
            cid = n.get("concept_id") or n.get("id")
            if cid:
                out.append({"id": cid, "title": n.get("title") or cid})
    return out


def shortest_path(a_id: str, b_id: str, *, max_depth: int = 6,
                  owner: str | None = None, cfg=None, store=None) -> dict:
    """E-G7 — 두 개념 간 최단 경로 증거 체인(조회 전용). 반환 {executed, found,
    a, b, length, paths:[{ids,titles}], count}. 경로 부재 시 found=False."""
    res = execute(shortest_path_plan(a_id, b_id, max_depth=max_depth, owner=owner),
                  cfg, store=store)
    base = {"kind": "shortest_path", "executed": res.get("executed", False),
            "found": False, "a": a_id, "b": b_id, "length": None,
            "paths": [], "count": 0}
    if not res.get("executed"):
        base["note"] = res.get("note", "")
        return base
    seqs = []
    for r in res.get("rows", []):
        seq = _node_seq(r.get("ns"))
        if len(seq) >= 2:
            seqs.append({"ids": [s["id"] for s in seq],
                         "titles": [s["title"] for s in seq],
                         "length": len(seq) - 1})
    # 결정론 정렬(길이→노드 시퀀스) + dedup
    uniq: dict = {}
    for s in sorted(seqs, key=lambda s: (s["length"], s["ids"])):
        uniq.setdefault(tuple(s["ids"]), s)
    paths = list(uniq.values())
    base.update(found=bool(paths), paths=paths, count=len(paths),
                length=paths[0]["length"] if paths else None)
    return base


# ── ER 병합 후보 제안 (E-G4) — 조회 전용, 자동 병합 금지(THESIS T1) ────────────
# 리서치 ③(개체 식별 해소)의 **검출 단계만** 채택한다 — 자동 병합은 영구 금지
# (오병합 비가역·LLM 판정 반입 금지·"시스템은 제안만"). 여기서는 결정론 타이틀
# 유사로 중복 후보를 **제안**하고, 확정(엣지 재지향+삭제)은 기존 merge_nodes
# (비가역·사용자 지시·단일 쓰기 표면 R4) 경유. suggest_ties(D25 전 단계)와 동형의
# 조회-전용 계층. 실사용 사고(중복 개념 오염 concept:ipaas)의 예방 표면.
import re as _re_merge   # 모듈 상단 import 회피(국소 사용)

_MERGE_TOKEN_RE = _re_merge.compile(r"[0-9a-z가-힣]+")


def _norm_title(title: str) -> tuple[str, frozenset]:
    """타이틀 정규화 → (정규 문자열, 토큰셋). 소문자·영숫자/한글 토큰(길이≥2 우선,
    단 전부 짧으면 원형 유지). 결정론."""
    toks = [t for t in _MERGE_TOKEN_RE.findall((title or "").lower())]
    sig = [t for t in toks if len(t) >= 2] or toks
    return ("".join(toks), frozenset(sig))


def merge_candidates(rows: list[dict], *, min_score: float = 0.6,
                     limit: int = 20) -> list[dict]:
    """중복 개념 후보쌍을 결정론적으로 산출(순수 — I/O 없음). ``rows``: [{id, title,
    domain}]. 규칙(강→약): 정규 타이틀 완전일치(exact_title, 1.0) · 토큰 자카드
    ≥min_score(token_overlap) · 한쪽이 다른쪽의 부분집합(subset, 0.8). 반환은
    (동일도메인 우선, 점수 내림차순, id 사전순) 정렬 리스트."""
    prepped = []
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        norm, toks = _norm_title(r.get("title") or "")
        if not toks:
            continue
        prepped.append({"id": cid, "title": r.get("title") or cid,
                        "domain": r.get("domain") or "", "norm": norm, "toks": toks})
    prepped.sort(key=lambda x: x["id"])   # 결정론 기준 순서
    out = []
    for i in range(len(prepped)):
        a = prepped[i]
        for j in range(i + 1, len(prepped)):
            b = prepped[j]
            score = 0.0
            reason = None
            if a["norm"] and a["norm"] == b["norm"]:
                score, reason = 1.0, "exact_title"
            else:
                inter = len(a["toks"] & b["toks"])
                union = len(a["toks"] | b["toks"]) or 1
                jac = inter / union
                if a["toks"] <= b["toks"] or b["toks"] <= a["toks"]:
                    score, reason = max(0.8, jac), "subset"
                elif jac >= min_score:
                    score, reason = jac, "token_overlap"
            if reason:
                out.append({"a_id": a["id"], "a_title": a["title"],
                            "b_id": b["id"], "b_title": b["title"],
                            "score": round(score, 4), "reason": reason,
                            "same_domain": bool(a["domain"] and a["domain"] == b["domain"])})
    out.sort(key=lambda c: (not c["same_domain"], -c["score"], c["a_id"], c["b_id"]))
    return out[:max(1, int(limit))]


def suggest_merge_candidates(*, owner: str | None = None, min_score: float = 0.6,
                             limit: int = 20, cfg=None, store=None) -> dict:
    """E-G4 — owner 개념의 중복 병합 후보(조회 전용). 확정은 vinc_merge_nodes 경유
    (자동 병합 금지 THESIS T1). 반환 {executed, candidates:[…], count}."""
    where = "WHERE c.owner_id = $owner " if owner else ""
    params = {"owner": owner} if owner else None
    plan = QueryPlan("merge_candidates",
                     "MATCH (c:Concept) " + where +
                     "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain",
                     params or {}, note="concept dup candidates (read-only)")
    res = execute(plan, cfg, store=store)
    if not res.get("executed"):
        return {"kind": "merge_candidates", "executed": False,
                "candidates": [], "count": 0, "note": res.get("note", "")}
    cands = merge_candidates(res.get("rows", []), min_score=min_score, limit=limit)
    return {"kind": "merge_candidates", "executed": True,
            "candidates": cands, "count": len(cands)}


# ── 구조 진단 (E-G3) — algo 확장 위임(WCC·Louvain) · 조회 전용 ─────────────────
# 구조 연산을 네이티브 알고리즘에 위임(리서치 ④). 전부 조회 전용·강등 계약:
# store 가 graph_algo 를 미지원하거나(추상화 폴백) algo 확장이 부재하면(오프라인
# 미번들 §9) executed=False + note 로 강등한다(무크래시). owner 스코프는 결과를
# owner 개념 집합과 교차해 적용(project_graph 는 전체 투영 — 교차소유자 엣지 부재라
# 컴포넌트/커뮤니티는 owner-clean).

def _owner_concept_ids(store, owner: str | None) -> set[str] | None:
    """owner 의 Concept id 집합(스코프 교차용). owner=None(로컬)이면 None(전체)."""
    if not owner:
        return None
    rows = store.run("MATCH (c:Concept) WHERE c.owner_id = $owner "
                     "RETURN c.concept_id AS id", {"owner": owner})
    return {r["id"] for r in rows if r.get("id")}


def _run_algo(store, algo: str, *, rel_tables: tuple | None = None):
    """store.graph_algo 안전 호출 — (rows, None) 또는 (None, note). ``rel_tables``
    미지정 시 어댑터 기본(개념 관계 4종). Louvain 은 단일 엣지 테이블만 지원하므로
    (kuzu 0.11.3 실측) 호출부가 RELATED_TO 단일 투영을 넘긴다."""
    fn = getattr(store, "graph_algo", None)
    if fn is None:
        return None, "algo_unsupported: store has no graph_algo (backend fallback)"
    try:
        return (fn(algo, rel_tables=rel_tables) if rel_tables else fn(algo)), None
    except Exception as exc:
        return None, f"{_classify_chunk_error(exc)}: {type(exc).__name__}: {exc}"


def component_diagnosis(*, owner: str | None = None, cfg=None, store=None) -> dict:
    """E-G3 WCC 구조 진단 — 연결 컴포넌트 수·크기 분포 + 고아(단일 노드) 개념 목록.
    "연결 안 된 섬 n개"(상태 화면)와 suggest_ties 후보 소스의 근거. 조회 전용.

    반환 {executed, components:[{group, size, members(≤50)}], orphans:[id…],
    component_count, orphan_count}. algo 부재 시 executed=False + note."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "component_diagnosis", "executed": False,
            "components": [], "orphans": [], "component_count": 0, "orphan_count": 0}
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        rows, note = _run_algo(store, "weakly_connected_components")
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        groups: dict = {}
        for r in rows:
            cid, grp = r.get("id"), r.get("value")
            if not cid or (scope is not None and cid not in scope):
                continue
            groups.setdefault(grp, []).append(cid)
        comps = [{"group": g, "size": len(m), "members": sorted(m)[:50]}
                 for g, m in groups.items()]
        comps.sort(key=lambda c: (-c["size"], str(c["group"])))   # 큰 컴포넌트 우선
        orphans = sorted(m[0] for m in groups.values() if len(m) == 1)
        base.update(executed=True, components=comps, orphans=orphans,
                    component_count=len(comps), orphan_count=len(orphans))
    finally:
        if owns:
            store.close()
    return base


def communities(*, owner: str | None = None, cfg=None, store=None) -> dict:
    """E-G3 Louvain 커뮤니티 검출 — 개념 군집(도메인 축과 별개의 '실측 군집' 렌즈).
    각 커뮤니티에 지배 도메인·크기·대표(사전순 첫 id)를 붙여 **테마 proposed 제안**의
    근거로 쓴다(재가는 사용자 — T1/T3, 여기서는 쓰기 없음). 조회 전용.

    반환 {executed, communities:[{community, size, dominant_domain, members(≤50)}],
    community_count}. Louvain 미배정(-1, 고립)은 별도 집계에서 제외. algo 부재 시
    executed=False + note."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "communities", "executed": False,
            "communities": [], "community_count": 0}
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        # Louvain 은 단일 엣지 테이블만(kuzu 0.11.3) — RELATED_TO 단일 투영(개념
        # 관계의 주축: wikilink 추론이 전부 RELATED_TO 로 강등돼 밀도 최고). 방향
        # 관계는 커뮤니티 검출에서 제외되나 근사로 충분(도메인 축 별개의 '실측 군집').
        rows, note = _run_algo(store, "louvain", rel_tables=("RELATED_TO",))
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        # 도메인 부착(지배 도메인 산정) — owner 스코프 개념 도메인 맵.
        dwhere = "WHERE c.owner_id = $owner " if owner else ""
        dparams = {"owner": owner} if owner else None
        drows = store.run("MATCH (c:Concept) " + dwhere +
                          "RETURN c.concept_id AS id, c.domain AS domain", dparams)
        domain_of = {r["id"]: (r.get("domain") or "") for r in drows}
        groups: dict = {}
        for r in rows:
            cid, com = r.get("id"), r.get("value")
            if not cid or com == -1 or (scope is not None and cid not in scope):
                continue   # -1 = Louvain 미배정(고립) — 커뮤니티 아님
            groups.setdefault(com, []).append(cid)
        out = []
        for com, members in groups.items():
            dom_counts: dict = {}
            for cid in members:
                d = domain_of.get(cid, "")
                if d:
                    dom_counts[d] = dom_counts.get(d, 0) + 1
            dominant = max(dom_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] \
                if dom_counts else ""
            out.append({"community": com, "size": len(members),
                        "dominant_domain": dominant, "members": sorted(members)[:50]})
        out.sort(key=lambda c: (-c["size"], str(c["community"])))
        base.update(executed=True, communities=out, community_count=len(out))
    finally:
        if owns:
            store.close()
    return base


def _classify_chunk_error(exc: Exception) -> str:
    """Chunk 검색 실패 사유 분류(T3b-1 — '실패 침묵' 제거의 근거). '인덱스 부재'와
    '확장 미가용'과 '미프로비저닝'을 구분해 응답 error 에 접두로 담는다(실측 문구):
    - index_missing: "Table Chunk doesn't have an index with name chunk_ft"
    - extension_unavailable: `_load_ext` 강등 문구("... 확장 미가용 ...", 오프라인 미번들)
    - not_provisioned: "Table Chunk does not exist"(스키마 선행 안 됨)."""
    m = str(exc).lower()
    if "have an index" in m:                       # doesn't/does not have an index
        return "index_missing"
    if "확장 미가용" in str(exc) or ("extension" in m and "미가용" in str(exc)):
        return "extension_unavailable"
    if "does not exist" in m or ("table" in m and "exist" in m):
        return "not_provisioned"
    return "error"


def _chunk_count(store) -> int:
    """Chunk 행 수(지연 빌드 여부 판단용). 조회 실패(미프로비저닝 등)는 0 으로 강등."""
    try:
        r = store.run("MATCH (c:Chunk) RETURN count(c) AS c")
        return int(r[0].get("c")) if r else 0
    except Exception:
        return 0


def _chunk_arms(store, query, query_embedding, top_k, filters) -> list[dict]:
    """Chunk FTS(+선택 벡터) 하이브리드 검색 코어 — 기존 §4-2 RRF 로직 그대로.
    인덱스·확장 부재는 예외로 전파(호출부가 지연 빌드·error 표면화를 담당)."""
    kw = store.fulltext_search(query, top_k, filters)           # BM25 암
    arms = [kw]
    arm_names = ["keyword"]
    if query_embedding is not None:
        arms.append(store.vector_search(query_embedding, top_k, filters))
        arm_names.append("semantic")
    # 매치 표기(D24): 융합 전 각 arm 멤버십을 행에 주석 — UI 의 ≡/≈ 표기 데이터.
    membership: dict = {}
    for name, rows_ in zip(arm_names, arms):
        for r in rows_:
            membership.setdefault(r.get("id"), []).append(name)
    rows = (rrf_fuse(arms, top_k=top_k) if len(arms) > 1 else arms[0][:top_k])
    for r in rows:
        r["arms"] = membership.get(r.get("id"), [])
    return rows


def _node_fallback_search(store, query: str, *, limit: int = 20,
                          owner: str | None = None) -> list[dict]:
    """Concept·Record 타이틀/요약/props 폴백 검색(T3b C1 **핵심**) — FTS·벡터 결과와
    **무관하게 항상** 수행한다. Concept/Record 는 Chunk 인덱스에 안 들어가(제목·요약을
    가진 노드가 여태 검색 자체가 불가) Chunk 0개 스토어에선 검색이 통째로 비었다 —
    실사용 사고의 직접 원인. 이 폴백이 그 갈래를 해소한다.

    **A1.9 토큰화**(구 검색 0건 해소): 예전엔 쿼리 전체를 통짜 substring 매치해
    "agentic automation" 처럼 단어가 여러 필드에 흩어지면 0건이었다. 이제 공백으로
    토큰화(상한 8)해 **모든 토큰이 어느 필드(title/one_liner/props)든 포함**되면 매치한다
    (토큰 AND). 단일 토큰이면 기존 통짜 매치와 동일(하위호환).

    Kùzu 0.11.3 실측: `lower(coalesce(x,'')) CONTAINS $tok`(토큰은 파이썬에서 소문자화).
    반환 행: {id, title, kind('concept'|'record'), domain, matched_in('title'|
    'one_liner'|'props')} — matched_in 은 **모든 토큰을 담은 최강 필드**(전부 title 이면
    'title'). ⚠ UNION ALL 은 전체 결과 LIMIT 를 무시하므로(실측) 상한은 파이썬 슬라이스로
    건다. 빈 쿼리·토큰 없음은 모든 노드를 삼키므로 스킵([])."""
    q = (query or "").strip()
    if not q:
        return []
    toks = [t for t in q.lower().split() if t][:8]
    if not toks:
        return []
    params: dict = {f"q{i}": t for i, t in enumerate(toks)}
    idx = range(len(toks))
    # SEC-1: 소유자 스코프. 없으면(로컬 단일 사용자) 기존 전역 검색.
    own_c = own_r = ""
    if owner:
        own_c, own_r = " AND c.owner_id = $owner", " AND r.owner_id = $owner"
        params["owner"] = owner

    def _all(field: str) -> str:      # 모든 토큰이 이 한 필드에 (matched_in 판정용)
        return "(" + " AND ".join(
            f"lower(coalesce({field},'')) CONTAINS $q{i}" for i in idx) + ")"

    def _any(fields: tuple[str, ...], i: int) -> str:   # 토큰 i 가 지정 필드들 중 어디든
        return "(" + " OR ".join(
            f"lower(coalesce({f},'')) CONTAINS $q{i}" for f in fields) + ")"

    c_fields = ("c.title", "c.one_liner", "c.props")
    r_fields = ("r.title", "r.props")
    where_c = " AND ".join(_any(c_fields, i) for i in idx)   # 토큰 전부, 각자 어느 필드든
    where_r = " AND ".join(_any(r_fields, i) for i in idx)
    cypher = (
        f"MATCH (c:Concept) WHERE {where_c}{own_c} "
        "RETURN c.concept_id AS id, c.title AS title, 'concept' AS kind, c.domain AS domain, "
        f"CASE WHEN {_all('c.title')} THEN 'title' "
        f"WHEN {_all('c.one_liner')} THEN 'one_liner' ELSE 'props' END AS matched_in "
        "UNION ALL "
        f"MATCH (r:Record) WHERE {where_r}{own_r} "
        "RETURN r.record_id AS id, r.title AS title, 'record' AS kind, r.domain AS domain, "
        f"CASE WHEN {_all('r.title')} THEN 'title' ELSE 'props' END AS matched_in"
    )
    rows = store.run(cypher, params)
    # 결정론 순서: 타이틀 매치 우선(가장 강한 신호), 그 다음 id. 상한은 파이썬에서.
    rows.sort(key=lambda r: (0 if r.get("matched_in") == "title" else 1, str(r.get("id"))))
    return rows[:max(1, int(limit))]


def _graph_arm_search(store, chunk_ids: list[str], *, owner: str | None = None,
                      limit: int = 20) -> list[dict]:
    """E-G1 그래프 암 — 상위 Chunk 가 :MENTIONS 하는 Concept + 그 1-hop Concept
    이웃을 후보로 승격한다(리서치 ① 다중 홉 가교 — 이부 그래프 확산의 결정론 번역).

    ``chunk_ids`` 는 **랭크 순서**(상위 청크 우선). 직접 언급 개념이 이웃보다 앞서고
    (더 강한 신호), 같은 층 내에서는 그 개념을 언급한 최상위 청크의 랭크로 정렬한다.
    반환 행은 node_hits 동형 {id,title,kind:'concept',domain,matched_in:'graph'} —
    :func:`search` 가 title 폴백과 RRF 융합한다. MENTIONS/Chunk 부재 시 [](하위호환).

    owner(SEC-1): Concept·이웃 모두 owner 스코프. `-[r]-` 무방향 1-hop(방언 중립)."""
    if not chunk_ids:
        return []
    params: dict = {"ids": list(chunk_ids)}
    own_c = own_nb = ""
    if owner:
        own_c = " AND c.owner_id = $owner"
        own_nb = " AND nb.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
        f"WHERE ch.chunk_id IN $ids{own_c} "
        "OPTIONAL MATCH (c)-[r]-(nb:Concept) "
        f"WHERE nb.concept_id <> c.concept_id{own_nb} "
        "RETURN ch.chunk_id AS chunk_id, c.concept_id AS id, c.title AS title, "
        "c.domain AS domain, nb.concept_id AS nb_id, nb.title AS nb_title, "
        "nb.domain AS nb_domain"
    )
    rows = store.run(cypher, params)
    rank = {cid: i for i, cid in enumerate(chunk_ids)}
    direct: dict[str, dict] = {}   # concept_id → best (min chunk rank)
    neigh: dict[str, dict] = {}    # 1-hop 이웃(직접 언급이 아닌 것만)
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        cr = rank.get(r.get("chunk_id"), 10 ** 9)
        cur = direct.get(cid)
        if cur is None or cr < cur["_r"]:
            direct[cid] = {"id": cid, "title": r.get("title"),
                           "domain": r.get("domain"), "_r": cr}
        nb = r.get("nb_id")
        if nb and nb != cid:
            neigh.setdefault(nb, {"id": nb, "title": r.get("nb_title"),
                                  "domain": r.get("nb_domain")})
    for cid in direct:            # 직접 언급이면 이웃 목록에서 제외(중복 방지)
        neigh.pop(cid, None)
    ordered = (sorted(direct.values(), key=lambda x: (x["_r"], x["id"]))
               + sorted(neigh.values(), key=lambda x: x["id"]))
    out = [{"id": x["id"], "title": x.get("title") or x["id"], "kind": "concept",
            "domain": x.get("domain") or "", "matched_in": "graph"}
           for x in ordered]
    return out[:max(1, int(limit))]


def _fuse_node_hits(title_hits: list[dict], graph_hits: list[dict], *,
                    top_k: int) -> list[dict]:
    """title 폴백 + 그래프 암을 개념-공간 RRF 융합(§4-2, D24 매치 표기). 각 행에
    ``arms``(title/graph 멤버십)를 부여한다. 그래프 암이 비면 **융합하지 않고**
    title 순서를 그대로 유지(하위호환 — 기존 Chunk 0 스토어 동작 불변)."""
    membership: dict = {}
    for name, rows_ in (("title", title_hits), ("graph", graph_hits)):
        for r in rows_:
            membership.setdefault(r.get("id"), set()).add(name)
    if not graph_hits:
        for r in title_hits:
            r["arms"] = sorted(membership.get(r.get("id"), []))
        return title_hits
    fused = rrf_fuse([title_hits, graph_hits], top_k=top_k)   # title 우선(행 유지)
    for r in fused:
        r["arms"] = sorted(membership.get(r.get("id"), []))
    return fused


def search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, owner: str | None = None,
           cfg=None, store=None) -> dict:
    """하이브리드 검색 **재설계**(T3b) — BM25(FTS)+(선택)벡터 RRF(§4-2) 위에 3개
    실사용 사고 대응을 얹는다:

      1. **실패 침묵 제거** — Chunk 검색 실행 실패 시 최상위 ``error`` 로 표면화한다
         (``executed:False`` 유지). '인덱스 부재'와 '확장 미가용'을 접두로 구분
         (:func:`_classify_chunk_error`). 이전엔 실패가 `executed:false`+빈 rows 로
         침묵해 호출자가 '결과 없음'으로 오판했다.
      2. **지연 인덱스 자동 빌드** — FTS 가 '인덱스 부재'로 실패하면 Chunk 행이 있을
         때 인덱스를 1회 빌드(멱등)하고 재검색한다. Chunk 0개면 빌드 없이 조용히 폴백.
      3. **Concept/Record 타이틀 폴백(핵심)** — FTS/벡터 결과와 무관하게 항상 노드
         폴백 검색을 수행해 신규 필드 ``node_hits`` 에 담는다. ``rows`` 는 Chunk 결과
         전용(하위호환). Chunk 0개 스토어에서도 개념이 검색된다.

    벡터 암은 **쿼리 임베딩 주입형**(§3-1) — 미주입 시 BM25 단독.
    ``store`` 주입(사이드카 §11 P2): read 뷰를 넘기면 재사용(열지도 닫지도 않음)."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "search", "executed": False, "rows": [], "node_hits": []}
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    try:
        # (3) 노드 폴백(title) — FTS/벡터와 무관하게 항상. 실패해도 Chunk 경로 무영향.
        # E-G1: 여기서는 title 폴백만 담고, Chunk 하이브리드 뒤 그래프 암과 융합한다.
        title_hits: list[dict] = []
        try:
            title_hits = _node_fallback_search(store, query, limit=top_k, owner=owner)
        except Exception as exc:
            base["node_error"] = f"{type(exc).__name__}: {exc}"
        base["node_hits"] = title_hits   # 그래프 암 실패해도 최소 title 폴백은 보장

        # (1)(2) Chunk 하이브리드 — 실패는 error 로 표면화, 인덱스 부재는 지연 빌드 후 재시도.
        try:
            base["rows"] = _chunk_arms(store, query, query_embedding, top_k, filters)
            base["executed"] = True
        except Exception as exc:
            if _classify_chunk_error(exc) != "index_missing":
                base["error"] = f"{_classify_chunk_error(exc)}: {type(exc).__name__}: {exc}"
                base["note"] = base["error"]
            else:
                n = _chunk_count(store)
                builder = getattr(store, "ensure_search_indexes", None)
                if n == 0:
                    # Chunk 0 — 빌드 시도 없이 조용히 폴백(node_hits 가 답, 무 에러).
                    base["executed"] = True
                    base["note"] = "no chunks indexed — node_hits only"
                elif builder is None:
                    base["error"] = f"index_missing: {type(exc).__name__}: {exc}"
                    base["note"] = base["error"]
                else:
                    try:
                        # 벡터 인덱스는 쿼리 임베딩 검색이 필요할 때만 빌드(dim 주입).
                        vdim = cfg.embedding_dim if query_embedding is not None else None
                        builder(dim=vdim)
                        base["rows"] = _chunk_arms(store, query, query_embedding, top_k, filters)
                        base["executed"] = True
                        base["note"] = "search index built on demand"
                    except Exception as exc2:
                        base["error"] = (f"index_build_failed: "
                                         f"{type(exc2).__name__}: {exc2}")
                        base["note"] = base["error"]

        # (4) E-G1 그래프 암 — 상위 Chunk 의 :MENTIONS 이웃 개념을 title 폴백과 융합.
        # 실패는 검색을 막지 않는다(그래프 암은 증강 — 부재 시 title 만, 하위호환).
        try:
            chunk_ids = [r.get("id") for r in base.get("rows", []) if r.get("id")]
            graph_hits = _graph_arm_search(store, chunk_ids, owner=owner, limit=top_k)
            base["node_hits"] = _fuse_node_hits(title_hits, graph_hits, top_k=top_k)
        except Exception as exc:
            base.setdefault("node_error", f"{type(exc).__name__}: {exc}")
            base["node_hits"] = _fuse_node_hits(title_hits, [], top_k=top_k)
    finally:
        if owns:
            store.close()
    return base


def decide_topic_resolve_plan(topic: str, *, owner: str | None = None) -> QueryPlan:
    """decide 토픽 → seed concept_id 해석(A1.9 A5). concept_id 정확일치 또는 title
    대소문자 무시 일치. decide_plan 의 `title=$topic`(완전일치) 대비 title 은 관대.

    ⚠ owner(SEC-1): 소유자를 주면 그 스코프 안에서만 해석한다. 없으면 남의 개념을
    시드로 잡아 서브그래프 전체를 되돌려줬다. OR 조건은 반드시 괄호로 묶는다 —
    안 그러면 owner AND 가 뒤 절에만 붙어 필터가 무력화된다."""
    params: dict = {"topic": topic}
    where = ("(c.concept_id = $topic "
             "OR lower(coalesce(c.title,'')) = lower($topic))")
    if owner:
        where += " AND c.owner_id = $owner"
        params["owner"] = owner
    cypher = f"MATCH (c:Concept) WHERE {where} RETURN c.concept_id AS id LIMIT 1"
    return QueryPlan("decide_resolve", cypher, params, note="resolve topic → concept seed")


def decide(topic: str, *, depth: int = 2, owner: str | None = None,
           cfg=None, store=None) -> dict:
    """결정 지원(A1.9 A5 정규화) — MCP-INTERFACE §4 계약 `{nodes, edges, conflicts}`.

    **왜 decide_plan 을 직접 실행하지 않나**: decide_plan 의 경로 순회
    `(n)-[* TRAIL]-(m)` 는 각 행이 시드 n 전체를 재직렬화(49컬럼 합집합, 대부분 null →
    토큰 폭발, 노션 §3.7)하고 중간 노드의 문자열 id 를 돌려주지 않아 깨끗한 그래프
    재구성이 불가하다. 그래서 **검증된 하위 planner 를 조합**한다: 토픽→seed 해석 →
    `subgraph_ids_plan`(TRAIL 순회) → `graph_rows_for_ids_plan` → `shape_graph`
    (dedup·props/tags 포함). blowup 을 없애고 label=title·props 를 얻는다.
    (decide_plan 은 `/plan` 표시·테스트용으로 유지 — 순회 의도의 표현.)

    ``conflicts``: 스키마에 1급 '상충' 관계가 없으므로, 결정에 유의미한 구조적 긴장 =
    **크로스도메인 엣지**(docstring 의 'cross-domain bridges')를 투영한다(날조 없음)."""
    base = {"kind": "decide", "executed": False, "topic": topic, "depth": depth,
            "found": False, "nodes": [], "edges": [], "conflicts": []}
    # SEC-1: 토픽 해석·순회·행조회 전부 owner 스코프. 없으면 남의 개념을 시드로
    # 잡아 서브그래프가 통째로 노출됐다(vinc_decide 는 owner 를 보내지도 않았다).
    res = execute(decide_topic_resolve_plan(topic, owner=owner), cfg, store=store)
    if not res.get("executed"):
        base["note"] = res.get("note", "")
        return base
    base["executed"] = True
    seed = next((r["id"] for r in res.get("rows", []) if r.get("id")), None)
    if not seed:
        base["note"] = f"topic not found: {topic}"
        return base
    base["found"] = True
    ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner), cfg, store=store)
    ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
    rows_res = execute(graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
    shaped = shape_graph(rows_res.get("rows", []))
    keep = set(ids)   # ego 경계(depth+1 유출 제거) — graph() 와 동일 정책
    shaped["edges"] = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
    attach_ppr(shaped, [seed])   # E-G2 — 토픽(시드) 관점 개인화 랭킹
    conflicts = [{"source": e["source"], "target": e["target"], "type": e["type"],
                  "reason": "cross_domain"} for e in shaped["edges"] if e.get("cross_domain")]
    return {**base, "nodes": shaped["nodes"], "edges": shaped["edges"], "conflicts": conflicts}


# ── 삭제 / 병합 (T3c C3 — 오염 정리 쓰기 표면) ────────────────────────────────
# 실사용 사고 ③: 쓰기 표면이 MERGE 단독이라 중복·빈 껍데기 노드(concept:ipaas 등)가
# 생겨도 되돌릴 수단이 없어 오염이 영구화됐다. delete/merge 는 그 정리 수단이다 —
# 둘 다 **비가역**. 사이드카에서는 잡 큐(run_sync)로 직렬화된다(단일 writer, R4).

_OWNER_SCOPED_LABELS = {"Concept", "Episode", "Record"}   # owner_id 열 보유(스코프 가드)


def _write_tombstone(store, node_id: str, label: str, owner: str | None) -> None:
    """삭제 표식(S4) — 삭제 전파용. 하드삭제(DETACH DELETE)만으론 수신측이 삭제를
    영영 모른다. 삭제 직전 노드의 rev/updated_at 을 보존하고 새 deleted_at(HLC)·
    sync_seq 를 찍어 `_Tombstone` 에 남긴다. owner 스코프 라벨(Concept/Episode/Record)
    만 — 그 외는 동기화 대상이 아니라 전파할 곳이 없다. 지연 import(순환 회피)."""
    if label not in _OWNER_SCOPED_LABELS:
        return
    from . import revstamp
    pk = {"Concept": "concept_id", "Episode": "episode_id", "Record": "record_id"}[label]
    rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id "
                     "RETURN n.rev AS rev, n.updated_at AS ua, n.owner_id AS owner",
                     {"id": node_id})
    if not rows:
        return
    own = owner or rows[0].get("owner") or "user:local"
    store.run("MERGE (t:_Tombstone {node_id: $id}) SET t.label = $l, t.owner_id = $o, "
              "t.deleted_at = $da, t.rev = $rev, t.sync_seq = $seq, t.origin = 'local'",
              {"id": node_id, "l": label, "o": own,
               "da": revstamp.hlc_now(store), "rev": int(rows[0].get("rev") or 0),
               "seq": revstamp.next_seq(store, own)})
_CC_RELS = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")  # Concept↔Concept

# 동기화 대상 엣지 rel → (src 라벨, src PK, dst 라벨, dst PK). 엣지 삭제 표면
# (delete_edge)과 sync 의 엣지 툼스톤 적용이 공유하는 단일 정의. fragment 타입명
# (relates_to·about…)도 여기로 정규화된다.
SYNC_EDGE_RELS: dict[str, tuple[str, str, str, str]] = {
    "RELATED_TO": ("Concept", "concept_id", "Concept", "concept_id"),
    "PREREQUISITE_OF": ("Concept", "concept_id", "Concept", "concept_id"),
    "DERIVES_FROM": ("Concept", "concept_id", "Concept", "concept_id"),
    "INHERITS_FROM": ("Concept", "concept_id", "Concept", "concept_id"),
    "ABOUT": ("Episode", "episode_id", "Concept", "concept_id"),
}
_FRAGMENT_EDGE_TO_REL = {   # fragment 엣지 타입명 → rel (api/fragment 와 동형)
    "relates_to": "RELATED_TO", "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM", "inherits_from": "INHERITS_FROM",
    "about": "ABOUT",
}


def _write_edge_tombstone(store, src: str, rel: str, dst: str, owner: str | None) -> None:
    """엣지 삭제 표식(S4 확장) — 엣지는 타임스탬프가 없어 노드보다 단순하다:
    (src, rel, dst) 키와 삭제 시각(HLC)·sync_seq 만 기록. owner 는 src 노드의
    소유를 승계(무스코프 삭제 시)한다 — 노드 툼스톤과 같은 규칙."""
    from . import revstamp
    sl, sp, _, _ = SYNC_EDGE_RELS[rel]
    own = owner
    if not own:
        rows = store.run(f"MATCH (n:{sl}) WHERE n.{sp} = $id RETURN n.owner_id AS o",
                         {"id": src})
        own = (rows[0].get("o") if rows else None) or "user:local"
    store.run("MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = $r, "
              "t.dst = $d, t.owner_id = $o, t.deleted_at = $da, t.sync_seq = $seq, "
              "t.origin = 'local'",
              {"k": f"{src}|{rel}|{dst}", "s": src, "r": rel, "d": dst, "o": own,
               "da": revstamp.hlc_now(store), "seq": revstamp.next_seq(store, own)})


def delete_edge(src: str, edge_type: str, dst: str, *, owner: str | None = None,
                cfg=None, store=None) -> dict:
    """엣지 1개를 삭제하고 **엣지 툼스톤**을 남긴다(S4 확장 — 삭제 전파). 대상은
    동기화 rel 5종(CC 4종 + ABOUT)만 — 그 외(CONNECTS·ATTACHES_TO·TYPED)는 동기화
    밖이라 툼스톤 의미가 없고 질화자(role/type) 정합이 필요해 후속 과제.

    ``edge_type`` 은 fragment 타입명(relates_to·about…)과 rel 명(RELATED_TO…) 둘 다
    허용. RELATED_TO 는 의미상 무방향 — **양방향 물리 엣지를 모두** 지운다.
    owner(SEC-3): 주어지면 양 끝점을 owner_id 로 스코프 — 남의 엣지는 못 지운다."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    rel = _FRAGMENT_EDGE_TO_REL.get((edge_type or "").strip().lower(),
                                    (edge_type or "").strip().upper())
    base = {"kind": "delete_edge", "executed": False, "found": False,
            "src": src, "rel": rel, "dst": dst}
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    if rel not in SYNC_EDGE_RELS:
        base["executed"] = True
        base["note"] = (f"unsupported edge type {edge_type!r} — deletable: "
                        f"{sorted(_FRAGMENT_EDGE_TO_REL)} (sync-covered rels only)")
        return base
    sl, sp, dl, dp = SYNC_EDGE_RELS[rel]
    scope_a = scope_b = ""
    params: dict = {"s": src, "d": dst}
    if owner:
        scope_a, scope_b = " AND a.owner_id = $owner", " AND b.owner_id = $owner"
        params["owner"] = owner
    try:
        pairs = [(src, dst)]
        if rel == "RELATED_TO":   # 무방향 의미 — 역방향 물리 엣지도 대상
            pairs.append((dst, src))
        deleted = 0
        for s_, d_ in pairs:
            p = {**params, "s": s_, "d": d_}
            cnt = store.run(
                f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b} "
                "RETURN count(r) AS c", p)
            if cnt and int(cnt[0].get("c") or 0):
                store.run(
                    f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                    f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b} DELETE r", p)
                deleted += int(cnt[0].get("c") or 0)
        base["executed"] = True
        if not deleted:
            base["note"] = "edge not found"
            return base
        base["found"] = True
        base["deleted"] = {"edges": deleted}
        # 툼스톤은 정준 방향(요청 방향) 1개 — apply 측이 RELATED_TO 를 양방향 삭제한다.
        _write_edge_tombstone(store, src, rel, dst, owner)
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def delete_node(node_id: str, *, owner: str | None = None, cfg=None, store=None) -> dict:
    """노드 1개를 부착 엣지와 함께 **비가역** 삭제(T3c). id 접두사로 테이블을 판별
    (:data:`_SEED_TABLES` 재사용) — 미지 접두사(Entity 등)는 표를 특정할 수 없어
    ``found:False`` 로 안전 거부한다. 존재 확인 → 부착 엣지 수(양방향) 카운트 →
    ``DETACH DELETE``(Kùzu 0.11.3 실측 지원). ``owner`` 를 주면 owner_id 열이 있는
    라벨(Concept/Episode/Record)에 한해 소유 일치 시에만 삭제(스코프 1차 §1 가드).

    반환: 삭제 시 ``{executed:True, found:True, deleted:{node:1, edges:n}}``,
    미존재·미지 접두사 시 ``{executed:True, found:False}``."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "delete", "executed": False, "found": False, "node": node_id}
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    prefix = node_id.split(":", 1)[0] if ":" in node_id else ""
    label_pk = _SEED_TABLES.get(prefix)
    try:
        if label_pk is None:
            base["executed"] = True
            base["note"] = f"unknown id prefix — cannot resolve table for {node_id!r}"
            return base
        label, pk = label_pk
        scope = ""
        params: dict = {"id": node_id}
        if owner and label in _OWNER_SCOPED_LABELS:
            scope = " AND n.owner_id = $owner"
            params["owner"] = owner
        elif owner:
            # SEC-3: owner 강제 경로인데 라벨(Bridge 등)이 owner_id 를 안 가지면 스코프
            # 불가 → 무스코프 삭제로 교차테넌트가 되므로 **거부**한다(fail-closed).
            # 로컬 무스코프(owner=None)는 이 분기를 타지 않아 현행 유지.
            base["executed"] = True
            base["note"] = f"refused: {label} is not owner-scoped ({node_id})"
            return base
        exists = store.run(
            f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} RETURN count(n) AS c", params)
        base["executed"] = True
        if not (exists and exists[0].get("c")):
            base["note"] = f"node not found: {node_id}"
            return base
        base["found"] = True
        deg = store.run(
            f"MATCH (n:{label})-[r]-() WHERE n.{pk} = $id{scope} RETURN count(r) AS c", params)
        edges = int(deg[0].get("c")) if deg else 0
        _write_tombstone(store, node_id, label, owner)   # 삭제 전파용 표식(S4)
        store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} DETACH DELETE n", params)
        base["deleted"] = {"node": 1, "edges": edges}
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def merge_nodes(keep_id: str, drop_id: str, *, owner: str | None = None,
                cfg=None, store=None) -> dict:
    """두 Concept 를 **비가역** 병합(T3c) — ``drop`` 의 모든 연결을 ``keep`` 으로
    재지향한 뒤 ``drop`` 을 삭제한다. v0.1.7 범위는 **Concept↔Concept 전용**(실사용
    정리 대상이 concept 중복 껍데기) — 그 외 타입 쌍은 ``executed:True``+``error`` 로
    거부한다. drop 의 title/props 는 버린다(keep 우선).

    재지향 대상: CC 4종(:data:`_CC_RELS`)의 outgoing·incoming 각각, ATTACHES_TO
    (Record→drop) incoming, CONNECTS(Bridge→drop) incoming. keep→keep 자기루프가 될
    엣지(상대가 keep/drop)·keep 에 이미 있는 동일 엣지는 스킵한다(중복 방지;
    RELATED_TO 는 무방향이라 역방향 존재도 중복으로 간주 — fragment.py 정책 일치).

    반환: ``{executed, found, repointed, skipped, dropped}``. keep/drop 미존재 시
    ``found:False``."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "merge", "executed": False, "keep": keep_id, "drop": drop_id}
    try:
        if store is None:
            base["note"] = base["error"] = "no reachable store"
            return base
        kp = keep_id.split(":", 1)[0] if ":" in keep_id else ""
        dp = drop_id.split(":", 1)[0] if ":" in drop_id else ""
        if kp != "concept" or dp != "concept":
            base["executed"] = True
            base["error"] = ("merge_nodes v0.1.7 supports concept↔concept only "
                             f"(got keep={kp or '?'}, drop={dp or '?'})")
            return base
        if keep_id == drop_id:
            base["executed"] = True
            base["error"] = "keep and drop are the same node"
            return base

        # SEC-3: owner 가 주어지면 모든 Concept/Record MATCH 를 소유자로 스코프한다.
        # keep·drop 중 하나라도 호출자 소유가 아니면 _exists 게이트에서 found:False 로
        # 즉시 중단(어떤 쓰기도 하기 전) → 남의 concept 병합·재지향·삭제 불가. 재지향·
        # 최종삭제도 소유 노드에만 닿는다. owner=None(로컬 단일유저)이면 현행(무스코프).
        osc = bool(owner)
        oscope = (lambda var: f" AND {var}.owner_id = $owner") if osc else (lambda var: "")
        # F5 — Bridge 는 과도기 술어(레거시 '' 포함, _bridge_owner_pred 와 동일 정책).
        bscope = (lambda var: f" AND ({var}.owner_id = $owner OR {var}.owner_id = '')"
                  ) if osc else (lambda var: "")
        op = {"owner": owner} if osc else {}   # 파라미터 스프레드(무스코프면 빈 dict)

        def _exists(cid: str) -> bool:
            r = store.run(
                f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} "
                "RETURN count(c) AS c", {"id": cid, **op})
            return bool(r and r[0].get("c"))

        base["executed"] = True
        if not (_exists(keep_id) and _exists(drop_id)):
            base["found"] = False
            base["note"] = "keep or drop not found"
            return base
        base["found"] = True

        repointed = skipped = 0

        def _cc_exists(rel: str, s: str, d: str) -> bool:
            r = store.run(
                f"MATCH (a:Concept)-[:{rel}]->(b:Concept) "
                f"WHERE a.concept_id = $s AND b.concept_id = $d{oscope('a')}{oscope('b')} "
                "RETURN count(*) AS c",
                {"s": s, "d": d, **op})
            return bool(r and r[0].get("c"))

        def _others(rel: str, incoming: bool) -> set:
            # drop 과 이웃 x 를 모두 owner 로 스코프 — 남의 노드로 향한 엣지는 재지향 대상 아님.
            if incoming:
                cy = (f"MATCH (x:Concept)-[:{rel}]->(d:Concept) "
                      f"WHERE d.concept_id = $id{oscope('d')}{oscope('x')} "
                      "RETURN DISTINCT x.concept_id AS other")
            else:
                cy = (f"MATCH (d:Concept)-[:{rel}]->(x:Concept) "
                      f"WHERE d.concept_id = $id{oscope('d')}{oscope('x')} "
                      "RETURN DISTINCT x.concept_id AS other")
            return {row["other"] for row in store.run(cy, {"id": drop_id, **op}) if row.get("other")}

        for rel in _CC_RELS:
            for incoming in (False, True):
                for other in _others(rel, incoming):
                    if other in (keep_id, drop_id):      # keep 자기루프·drop 내부 엣지 → 스킵
                        skipped += 1
                        continue
                    # incoming: (other)-[rel]->(keep) / outgoing: (keep)-[rel]->(other)
                    s, d = (other, keep_id) if incoming else (keep_id, other)
                    dup = _cc_exists(rel, s, d)
                    if not dup and rel == "RELATED_TO":
                        dup = _cc_exists(rel, d, s)       # 무방향 — 역방향도 중복
                    if dup:
                        skipped += 1
                        continue
                    store.run(
                        "MATCH (a:Concept),(b:Concept) "
                        f"WHERE a.concept_id = $s AND b.concept_id = $d{oscope('a')}{oscope('b')} "
                        f"MERGE (a)-[:{rel}]->(b)", {"s": s, "d": d, **op})
                    repointed += 1

        # ATTACHES_TO incoming: (rec:Record)-[a]->(drop) → (rec)-[a]->(keep), type 보존.
        # SEC-3: Record·drop·keep 모두 owner 스코프 — 남의 Record 를 내 keep 으로 못 옮긴다.
        for row in store.run(
                "MATCH (r:Record)-[a:ATTACHES_TO]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{oscope('r')} "
                "RETURN DISTINCT r.record_id AS other, a.type AS type", {"id": drop_id, **op}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (r:Record)-[:ATTACHES_TO]->(k:Concept) "
                f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                "RETURN count(*) AS c",
                {"r": other, "k": keep_id, **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("type") is not None:
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "MERGE (r)-[e:ATTACHES_TO]->(k) SET e.type = $t",
                    {"r": other, "k": keep_id, "t": row["type"], **op})
            else:
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "MERGE (r)-[e:ATTACHES_TO]->(k)", {"r": other, "k": keep_id, **op})
            repointed += 1

        # CONNECTS incoming: (br:Bridge)-[c]->(drop) → (br)-[c]->(keep), role 보존.
        # SEC-3: Concept 끝점(drop·keep)은 엄격 owner 스코프, Bridge 는 v2(F5)에서
        # owner_id 를 얻어 과도기 술어(bscope — 레거시 '' 포함)로 스코프한다.
        for row in store.run(
                "MATCH (b:Bridge)-[c:CONNECTS]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{bscope('b')} "
                "RETURN DISTINCT b.bridge_id AS other, c.role AS role", {"id": drop_id, **op}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (b:Bridge)-[:CONNECTS]->(k:Concept) "
                f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                "RETURN count(*) AS c",
                {"b": other, "k": keep_id, **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("role") is not None:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k) SET e.role = $role",
                    {"b": other, "k": keep_id, "role": row["role"], **op})
            else:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k)", {"b": other, "k": keep_id, **op})
            repointed += 1

        # drop 삭제(부착 엣지 포함) — title/props 버림(keep 우선).
        # SEC-3: 최종 파괴 연산도 owner 스코프(벨트&서스펜더 — _exists 게이트로 이미 소유
        # 확인됐지만, 파괴 연산은 이중 가드). owner 불일치면 아무것도 안 지운다.
        _write_tombstone(store, drop_id, "Concept", owner)   # drop 도 전파(S4)
        store.run(f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} DETACH DELETE c",
                  {"id": drop_id, **op})
        base["repointed"] = repointed
        base["skipped"] = skipped
        base["dropped"] = drop_id
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


# ── owner 전체 purge (계정 삭제 캐스케이드, B2 §5.9) ──────────────────────────
# 계정(Vinc-homepage)이 하드삭제될 때 호스티드 그래프에 남는 회원 지식을 함께 지운다.
# 계정 서비스는 B2 경계상 MCP DB 에 직접 못 닿으므로, 계정이 Ed25519 로 서명한 삭제
# 요청을 MCP `/internal/purge-owner` 가 검증해(account 공개키) 사이드카의 이 함수로
# 프록시한다. owner_id 가 있는 라벨(스키마 파생 — Concept/Episode/Record/Entity/
# LearnerPattern)의 소유 노드를 부착 엣지와 함께 DETACH DELETE 한다. 멱등.
def _purge_labels() -> list[str]:
    """owner 스코프 삭제 대상 라벨 — 스키마 SSoT + 소유 툼스톤. 지연 임포트(백엔드
    중립 유지). owner_scoped_labels 는 owner_id 열 보유 라벨만 돌려주므로 여기 전부
    ``n.owner_id = $o`` 필터가 성립한다. `_Tombstone`(메타)도 owner_id 를 가져 포함."""
    from store.embedded_schema import owner_scoped_labels
    return owner_scoped_labels() + ["_Tombstone"]


def purge_owner(owner: str, *, cfg=None, store=None) -> dict:
    """``owner`` 소유의 **전 노드**를 부착 엣지와 함께 비가역 DETACH DELETE.

    계정 삭제 캐스케이드 전용(§5.9). 소유 스코프이므로 SEC-1/SEC-2 의 owner 격리가
    전제 — 소유가 안 갈리면 남의 노드가 지워질 수 있다. 라벨별 삭제 건수를 돌려줘
    호출자(계정)가 감사할 수 있게 한다. **owner 가 비었으면 거부**(빈 술어로 전삭제
    사고 방지). Bridge/Domain/Chunk 는 owner_id 가 없어 소유로 못 가르는 잔여 —
    [[vinc-owner-isolation-sec1]] 의 알려진 한계와 동일(원격엔 MCP 쓰기로만 생기고
    Bridge 는 현재 소유 스탬프 미부여)."""
    cfg = cfg or load_config()
    base = {"kind": "purge", "executed": False, "owner": owner}
    if not owner or not isinstance(owner, str):
        base["error"] = base["note"] = "refusing to purge without an owner"
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    deleted: dict[str, int] = {}
    try:
        for label in _purge_labels():
            cnt = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c", {"o": owner})
            n = int(cnt[0].get("c")) if cnt else 0
            if n:
                store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o DETACH DELETE n", {"o": owner})
            deleted[label] = n
        base["executed"] = True
        base["deleted"] = deleted
        base["total"] = sum(deleted.values())
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base
