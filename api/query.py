# -*- coding: utf-8 -*-
"""Query planners + executors for recall / search / decide.

A QueryPlan is a (cypher, params) pair plus metadata. Plans are pure and
testable. ``execute`` runs a plan against Neo4j (driver imported lazily); when
no driver/password is available it returns the plan with ``executed=False`` so
callers — and tests — get a deterministic result without a live database.
"""
from __future__ import annotations

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
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label"
    )
    return QueryPlan("graph", cypher, params, note="learning knowledge-graph (concepts + relations)")


def shape_graph(rows: list[dict]) -> dict:
    """Turn graph rows into ``{nodes, edges}`` (pure). Nodes de-duplicated by id
    with degree counted; edges mark ``cross_domain`` when endpoints differ in
    domain. Domain is a free/open string — never interpreted against a list."""
    nodes: dict[str, dict] = {}

    def _node(nid, label, dom):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label or nid, "domain": dom or "",
                          "kind": "concept", "degree": 0}
        return nodes[nid]

    edges: list[dict] = []
    for r in rows:
        nid = r.get("id")
        if not nid:
            continue
        _node(nid, r.get("label"), r.get("domain"))
        tgt = r.get("target")
        if tgt:
            # C5: target 은 target_label(=title)로 만든다 — 예전엔 id 를 label 로 넘겨
            # target-only 로 등장한 노드가 항상 id 노출로 폴백됐다(_node 의
            # `label or nid` 폴백은 title 이 정말 없을 때만 쓰여야 한다).
            _node(tgt, r.get("target_label"), r.get("target_domain"))
            src_dom, tgt_dom = r.get("domain"), r.get("target_domain")
            cross = bool(src_dom and tgt_dom and src_dom != tgt_dom)
            edges.append({"source": nid, "target": tgt,
                          "type": r.get("rel") or "RELATED_TO", "cross_domain": cross})
            nodes[nid]["degree"] += 1
            nodes[tgt]["degree"] += 1
    return {"nodes": list(nodes.values()), "edges": edges}


def subgraph_ids_plan(seed: str, *, depth: int = 2) -> QueryPlan:
    """Seed+depth 순회 — Atlas L2 노드 포커스와 MCP `vinc_graph` 의 공유 플랜(D16,
    MCP-SKELETON §10 공백 해소). `decide_plan` 과 동일한 TRAIL 가드(엣지 무재사용,
    §6-2 M3)로 seed 에서 depth 이내에 닿는 Concept id 를 수집한다."""
    depth = max(1, min(int(depth), 4))
    cypher = (
        "MATCH (n:Concept) WHERE n.concept_id = $seed "
        f"MATCH (n)-[* TRAIL 1..{depth}]-(m:Concept) "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("subgraph_ids", cypher, {"seed": seed},
                     note=f"seed+depth traversal (depth={depth})")


def graph_rows_for_ids_plan(ids: list[str]) -> QueryPlan:
    """id 집합의 Concept 행 + 나가는 엣지(§4-2 — 엣지의 서브그래프 외부 여과는
    파이썬 shape 단계에서). `graph_plan` 과 동일 투영."""
    cypher = (
        "MATCH (c:Concept) WHERE c.concept_id IN $ids "
        "OPTIONAL MATCH (c)-[r]->(d:Concept) "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label"
    )
    return QueryPlan("graph", cypher, {"ids": ids}, note="subgraph rows for id set")


_SEED_TABLES = {   # seed id 접두사 → (라벨, PK 열) — C4 선조회용
    "concept": ("Concept", "concept_id"),
    "episode": ("Episode", "episode_id"),
    "record": ("Record", "record_id"),
    "bridge": ("Bridge", "bridge_id"),
}


def seed_lookup_plan(seed: str) -> QueryPlan:
    """seed 노드 존재 선조회(C4) — '노드 없음'과 '노드는 있으나 연결 0(고립)'을
    구분하기 위한 최소 조회(id·title 만). id 접두사(`concept:`·`episode:`·
    `record:`·`bridge:`)로 테이블을 판별하고, 접두사가 없거나 미지의 접두사면
    Concept 로 시도한다. `graph()` 는 순회 결과가 비었을 때만 이 플랜을 쓴다 —
    순회가 이미 노드를 찾았다면(=시드가 확실히 존재) 굳이 다시 묻지 않는다."""
    prefix = seed.split(":", 1)[0] if ":" in seed else ""
    label, pk = _SEED_TABLES.get(prefix, ("Concept", "concept_id"))
    cypher = f"MATCH (n:{label}) WHERE n.{pk} = $seed RETURN n.{pk} AS id, n.title AS title"
    return QueryPlan("seed_lookup", cypher, {"seed": seed}, note=f"seed existence probe ({label})")


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
    (shell·radial·circle·tree·organic, 미지 값 shell 강등)."""
    if seed:
        ids_res = execute(subgraph_ids_plan(seed, depth=depth), cfg, store=store)
        if not ids_res.get("executed"):
            return {"executed": False, "seed": seed, "depth": depth, "nodes": [], "edges": [],
                    "note": ids_res.get("note", "")}
        ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
        res = execute(graph_rows_for_ids_plan(ids), cfg, store=store)
        shaped = shape_graph(res.get("rows", []))
        keep = set(ids)   # 엣지의 depth+1 유출 대상 제거(ego 경계)
        shaped["edges"] = [e for e in shaped["edges"] if e["target"] in keep and e["source"] in keep]
        shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
        executed = res.get("executed", False)
        found = True
        if not shaped["nodes"]:
            # C4: 순회·행 조회가 둘 다 비었다 — '시드 자체가 없음'과 '시드는 있으나
            # 연결이 0(고립)'이 여태 구분 불가했다(실사용에서 실재 episode 시드가
            # 빈 응답을 받아 '데이터 없음'으로 오판됨). 이 갈래에서만 선조회한다 —
            # Concept 시드가 실재하면 위 graph_rows_for_ids_plan 이 이미 자기 자신을
            # 한 행(고립 시 rel/target=NULL)으로 돌려주므로 shaped["nodes"] 가 비지
            # 않는다; 여기 도달하는 건 시드가 아예 없거나 Concept 밖 타입(episode/
            # record/bridge, 그 순회는 범위 밖)일 때뿐이다.
            seed_res = execute(seed_lookup_plan(seed), cfg, store=store)
            if not seed_res.get("executed"):
                return {"executed": False, "seed": seed, "depth": depth, "nodes": [], "edges": [],
                        "note": seed_res.get("note", "")}
            seed_row = next((r for r in seed_res.get("rows", []) if r.get("id") == seed), None)
            found = seed_row is not None
            if found:
                kind = seed.split(":", 1)[0] if ":" in seed else "concept"
                shaped["nodes"] = [{"id": seed, "label": seed_row.get("title") or seed,
                                    "domain": "", "kind": kind, "degree": 0}]
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout)
        return {"executed": executed, "seed": seed, "depth": depth, "found": found, **shaped}
    res = execute(graph_plan(owner=owner, domain=domain), cfg, store=store)
    shaped = shape_graph(res.get("rows", []))
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


def bridge_ties_plan() -> QueryPlan:
    """L0 결합 행렬의 Bridge 축 — 실데이터(weg-9000 허브)에서 개념 간 엣지는 전부
    도메인 내부이고 크로스도메인은 :Bridge 가 1급으로 담는다(2026-07-10 실측).
    Bridge 는 소유 스탬프가 없는 구조 노드라 owner 필터 없음."""
    cypher = (
        "MATCH (b:Bridge) "
        "WHERE b.source_domain IS NOT NULL AND b.target_domain IS NOT NULL "
        "RETURN b.source_domain AS source, b.target_domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("bridge_ties", cypher, {}, note="bridge tie matrix (L0)")


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


def bridge_coupling_plan(*, a: str, b: str) -> QueryPlan:
    """Compare 뷰(D22) — Bridge 매개 개념쌍: (cA)←CONNECTS─(Bridge)─CONNECTS→(cB),
    cA.domain=$a · cB.domain=$b. weg-9000 실데이터에서 직접 엣지는 0 이고 결합은
    Bridge 가 담당(실측) — 매개쌍이 주 신호다."""
    cypher = (
        "MATCH (x:Concept)<-[:CONNECTS]-(br:Bridge)-[:CONNECTS]->(y:Concept) "
        "WHERE x.domain = $a AND y.domain = $b "
        "RETURN x.concept_id AS a_id, x.title AS a_title, "
        "y.concept_id AS b_id, y.title AS b_title, "
        "br.bridge_id AS via, br.title AS via_title"
    )
    return QueryPlan("coupling_bridge", cypher, {"a": a, "b": b},
                     note="bridge-mediated concept pairs")


def coupling(*, owner: str, a: str, b: str, cfg=None, store=None) -> dict:
    """두 도메인의 개념 결합쌍(D22) — 직접 엣지 ∪ Bridge 매개. 쌍 단위 dedup,
    직접 결합 우선 정렬."""
    d = execute(direct_coupling_plan(owner=owner, a=a, b=b), cfg, store=store)
    br = execute(bridge_coupling_plan(a=a, b=b), cfg, store=store)
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
    b = execute(bridge_ties_plan(), cfg, store=store)
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


def _node_fallback_search(store, query: str, *, limit: int = 20) -> list[dict]:
    """Concept·Record 타이틀/요약/props 폴백 검색(T3b C1 **핵심**) — FTS·벡터 결과와
    **무관하게 항상** 수행한다. Concept/Record 는 Chunk 인덱스에 안 들어가(제목·요약을
    가진 노드가 여태 검색 자체가 불가) Chunk 0개 스토어에선 검색이 통째로 비었다 —
    실사용 사고의 직접 원인. 이 폴백이 그 갈래를 해소한다.

    Kùzu 0.11.3 실측: `lower(coalesce(x,'')) CONTAINS lower($q)` 대소문자 무시 매치.
    반환 행: {id, title, kind('concept'|'record'), domain, matched_in('title'|
    'one_liner'|'props')}. ⚠ UNION ALL 은 전체 결과 LIMIT 를 무시하므로(실측) 상한은
    파이썬 슬라이스로 건다. 빈 쿼리는 모든 노드를 삼키므로 스킵([])."""
    q = (query or "").strip()
    if not q:
        return []
    cypher = (
        "MATCH (c:Concept) "
        "WHERE (lower(coalesce(c.title,'')) CONTAINS lower($q) "
        "OR lower(coalesce(c.one_liner,'')) CONTAINS lower($q) "
        "OR lower(coalesce(c.props,'')) CONTAINS lower($q)) "
        "RETURN c.concept_id AS id, c.title AS title, 'concept' AS kind, c.domain AS domain, "
        "CASE WHEN lower(coalesce(c.title,'')) CONTAINS lower($q) THEN 'title' "
        "WHEN lower(coalesce(c.one_liner,'')) CONTAINS lower($q) THEN 'one_liner' "
        "ELSE 'props' END AS matched_in "
        "UNION ALL "
        "MATCH (r:Record) "
        "WHERE (lower(coalesce(r.title,'')) CONTAINS lower($q) "
        "OR lower(coalesce(r.props,'')) CONTAINS lower($q)) "
        "RETURN r.record_id AS id, r.title AS title, 'record' AS kind, r.domain AS domain, "
        "CASE WHEN lower(coalesce(r.title,'')) CONTAINS lower($q) THEN 'title' "
        "ELSE 'props' END AS matched_in"
    )
    rows = store.run(cypher, {"q": q})
    # 결정론 순서: 타이틀 매치 우선(가장 강한 신호), 그 다음 id. 상한은 파이썬에서.
    rows.sort(key=lambda r: (0 if r.get("matched_in") == "title" else 1, str(r.get("id"))))
    return rows[:max(1, int(limit))]


def search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, cfg=None, store=None) -> dict:
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
        # (3) 노드 폴백 — FTS/벡터와 무관하게 항상. 실패해도 Chunk 경로를 막지 않는다.
        try:
            base["node_hits"] = _node_fallback_search(store, query, limit=top_k)
        except Exception as exc:
            base["node_hits"] = []
            base["node_error"] = f"{type(exc).__name__}: {exc}"

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
    finally:
        if owns:
            store.close()
    return base


def decide(topic: str, *, depth: int = 2, cfg=None, store=None) -> dict:
    return execute(decide_plan(topic, depth=depth), cfg, store=store)


# ── 삭제 / 병합 (T3c C3 — 오염 정리 쓰기 표면) ────────────────────────────────
# 실사용 사고 ③: 쓰기 표면이 MERGE 단독이라 중복·빈 껍데기 노드(concept:ipaas 등)가
# 생겨도 되돌릴 수단이 없어 오염이 영구화됐다. delete/merge 는 그 정리 수단이다 —
# 둘 다 **비가역**. 사이드카에서는 잡 큐(run_sync)로 직렬화된다(단일 writer, R4).

_OWNER_SCOPED_LABELS = {"Concept", "Episode", "Record"}   # owner_id 열 보유(스코프 가드)
_CC_RELS = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")  # Concept↔Concept


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

        def _exists(cid: str) -> bool:
            r = store.run("MATCH (c:Concept) WHERE c.concept_id = $id RETURN count(c) AS c",
                          {"id": cid})
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
                "WHERE a.concept_id = $s AND b.concept_id = $d RETURN count(*) AS c",
                {"s": s, "d": d})
            return bool(r and r[0].get("c"))

        def _others(rel: str, incoming: bool) -> set:
            if incoming:
                cy = (f"MATCH (x:Concept)-[:{rel}]->(d:Concept) WHERE d.concept_id = $id "
                      "RETURN DISTINCT x.concept_id AS other")
            else:
                cy = (f"MATCH (d:Concept)-[:{rel}]->(x:Concept) WHERE d.concept_id = $id "
                      "RETURN DISTINCT x.concept_id AS other")
            return {row["other"] for row in store.run(cy, {"id": drop_id}) if row.get("other")}

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
                        "WHERE a.concept_id = $s AND b.concept_id = $d "
                        f"MERGE (a)-[:{rel}]->(b)", {"s": s, "d": d})
                    repointed += 1

        # ATTACHES_TO incoming: (rec:Record)-[a]->(drop) → (rec)-[a]->(keep), type 보존.
        for row in store.run(
                "MATCH (r:Record)-[a:ATTACHES_TO]->(d:Concept) WHERE d.concept_id = $id "
                "RETURN DISTINCT r.record_id AS other, a.type AS type", {"id": drop_id}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (r:Record)-[:ATTACHES_TO]->(k:Concept) "
                "WHERE r.record_id = $r AND k.concept_id = $k RETURN count(*) AS c",
                {"r": other, "k": keep_id})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("type") is not None:
                store.run(
                    "MATCH (r:Record),(k:Concept) WHERE r.record_id = $r AND k.concept_id = $k "
                    "MERGE (r)-[e:ATTACHES_TO]->(k) SET e.type = $t",
                    {"r": other, "k": keep_id, "t": row["type"]})
            else:
                store.run(
                    "MATCH (r:Record),(k:Concept) WHERE r.record_id = $r AND k.concept_id = $k "
                    "MERGE (r)-[e:ATTACHES_TO]->(k)", {"r": other, "k": keep_id})
            repointed += 1

        # CONNECTS incoming: (br:Bridge)-[c]->(drop) → (br)-[c]->(keep), role 보존.
        for row in store.run(
                "MATCH (b:Bridge)-[c:CONNECTS]->(d:Concept) WHERE d.concept_id = $id "
                "RETURN DISTINCT b.bridge_id AS other, c.role AS role", {"id": drop_id}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (b:Bridge)-[:CONNECTS]->(k:Concept) "
                "WHERE b.bridge_id = $b AND k.concept_id = $k RETURN count(*) AS c",
                {"b": other, "k": keep_id})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("role") is not None:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) WHERE b.bridge_id = $b AND k.concept_id = $k "
                    "MERGE (b)-[e:CONNECTS]->(k) SET e.role = $role",
                    {"b": other, "k": keep_id, "role": row["role"]})
            else:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) WHERE b.bridge_id = $b AND k.concept_id = $k "
                    "MERGE (b)-[e:CONNECTS]->(k)", {"b": other, "k": keep_id})
            repointed += 1

        # drop 삭제(부착 엣지 포함) — title/props 버림(keep 우선).
        store.run("MATCH (c:Concept) WHERE c.concept_id = $id DETACH DELETE c", {"id": drop_id})
        base["repointed"] = repointed
        base["skipped"] = skipped
        base["dropped"] = drop_id
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base
