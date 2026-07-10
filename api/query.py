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
    Optionally scoped to one ``owner``. Returns ``{domain, episodes}`` rows."""
    where = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL" if owner else "WHERE e.domain IS NOT NULL"
    params: dict = {"owner": owner} if owner else {}
    cypher = (
        f"MATCH (e:Episode) {where} "
        "RETURN e.domain AS domain, count(*) AS episodes "
        "ORDER BY episodes DESC"
    )
    return QueryPlan("domains", cypher, params, note="open-domain discovery (data-driven, not a fixed list)")


def domains(*, owner: str | None = None, cfg=None, store=None) -> dict:
    return execute(domains_plan(owner=owner), cfg, store=store)


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
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain"
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
            _node(tgt, tgt, r.get("target_domain"))
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
        "label(r) AS rel, d.concept_id AS target, d.domain AS target_domain"
    )
    return QueryPlan("graph", cypher, {"ids": ids}, note="subgraph rows for id set")


def layout_positions(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """결정론 레이아웃(D17) — **순수 함수**, 난수·시간 무의존이라 같은 그래프는 항상
    같은 좌표(플랜 순수성 계약과 동형). 도메인 클러스터를 단위원 둘레에 정렬 배치하고,
    클러스터 내부는 (degree DESC, id) 순 아르키메데스 나선으로 편다. 힘-지향 정밀화는
    후속(사이드카 잡)이며 UI 는 본 좌표를 소비만 한다."""
    import math
    by_domain: dict[str, list[dict]] = {}
    for n in nodes:
        by_domain.setdefault(n.get("domain") or "", []).append(n)
    domains = sorted(by_domain)
    d_count = max(len(domains), 1)
    for di, dom in enumerate(domains):
        # 클러스터 중심 — 도메인 1개면 원점, 복수면 단위원 둘레.
        if d_count == 1:
            cx, cy = 0.0, 0.0
        else:
            ang = 2 * math.pi * di / d_count
            cx, cy = math.cos(ang), math.sin(ang)
        members = sorted(by_domain[dom], key=lambda n: (-int(n.get("degree") or 0), str(n.get("id"))))
        for i, n in enumerate(members):
            r = 0.08 * math.sqrt(i)            # 나선 반경(허브가 중심)
            a = i * 2.399963229728653          # golden angle — 균등 산개
            n["x"] = round(cx + r * math.cos(a), 6)
            n["y"] = round(cy + r * math.sin(a), 6)
    return nodes


def graph(*, owner: str, domain: str | None = None, seed: str | None = None,
          depth: int = 2, cfg=None, store=None) -> dict:
    """지식 그래프 — 기본은 owner(±domain) 스코프 전체(Atlas L1), ``seed`` 지정 시
    seed+depth ego 서브그래프(Atlas L2 = MCP `vinc_graph` 동일 경로). 노드에는
    결정론 레이아웃 좌표(x,y)가 포함된다(D17)."""
    if seed:
        ids_res = execute(subgraph_ids_plan(seed, depth=depth), cfg, store=store)
        if not ids_res.get("executed"):
            return {"executed": False, "nodes": [], "edges": [],
                    "note": ids_res.get("note", "")}
        ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
        res = execute(graph_rows_for_ids_plan(ids), cfg, store=store)
        shaped = shape_graph(res.get("rows", []))
        keep = set(ids)   # 엣지의 depth+1 유출 대상 제거(ego 경계)
        shaped["edges"] = [e for e in shaped["edges"] if e["target"] in keep and e["source"] in keep]
        shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"])
        return {"executed": res.get("executed", False), "seed": seed, "depth": depth, **shaped}
    res = execute(graph_plan(owner=owner, domain=domain), cfg, store=store)
    shaped = shape_graph(res.get("rows", []))
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"])
    return {"executed": res.get("executed", False), **shaped}


def domain_ties_plan(*, owner: str) -> QueryPlan:
    """Atlas L0 결합 행렬(D16) — 도메인이 다른 Concept 간 엣지를 도메인 쌍으로 집계."""
    cypher = (
        "MATCH (a:Concept)-[r]->(b:Concept) "
        "WHERE a.owner_id = $owner AND b.owner_id = $owner AND a.domain <> b.domain "
        "RETURN a.domain AS source, b.domain AS target, count(*) AS ties "
        "ORDER BY ties DESC"
    )
    return QueryPlan("domain_ties", cypher, {"owner": owner}, note="cross-domain tie matrix (L0)")


def atlas_l0(*, owner: str, cfg=None, store=None) -> dict:
    """Atlas L0 Domain Atlas 데이터(D16) — 도메인별 레코드 수(`/domains` 집계) +
    도메인 간 결합 행렬. UI 는 이 응답만으로 L0 를 그린다(그래프 로직 UI 반입 금지)."""
    d = execute(domains_plan(owner=owner), cfg, store=store)
    t = execute(domain_ties_plan(owner=owner), cfg, store=store)
    return {"executed": bool(d.get("executed")),
            "domains": d.get("rows", []), "ties": t.get("rows", [])}


def search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, cfg=None, store=None) -> dict:
    """하이브리드 검색 — BM25(FTS) + (선택)벡터 암을 RRF 로 융합(§4-2).

    벡터 암은 **쿼리 임베딩 주입형**(§3-1): 호출부(사이드카)가 모델로 쿼리를
    임베딩해 ``query_embedding`` 으로 넘긴다 → 검색 코드는 임베딩 모델과 무관.
    미주입 시 BM25 단독. 인덱스·확장 부재 시 executed=False 로 강등(무 크래시).

    ``store`` 주입(사이드카 §11 P2): read 뷰를 넘기면 재사용(열지도 닫지도 않음)."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "search", "executed": False, "rows": []}
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        arms = [store.fulltext_search(query, top_k, filters)]   # BM25 암
        if query_embedding is not None:
            arms.append(store.vector_search(query_embedding, top_k, filters))
        rows = (rrf_fuse(arms, top_k=top_k) if len(arms) > 1 else arms[0][:top_k])
        base["executed"] = True
        base["rows"] = rows
    except Exception as exc:
        base["note"] = f"search 실행 실패 — {type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def decide(topic: str, *, depth: int = 2, cfg=None, store=None) -> dict:
    return execute(decide_plan(topic, depth=depth), cfg, store=store)
