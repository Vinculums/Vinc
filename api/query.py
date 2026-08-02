# -*- coding: utf-8 -*-
"""Query planners + executors for recall / search / decide.

A QueryPlan is a (cypher, params) pair plus metadata. Plans are pure and
testable. ``execute`` runs a plan against Neo4j (driver imported lazily); when
no driver/password is available it returns the plan with ``executed=False`` so
callers — and tests — get a deterministic result without a live database.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field

from store.config import StoreConfig, load_config
from store.backend import open_store

from . import graphmd


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


# ── 검색 커서 페이지네이션 (GM-14) ──────────────────────────────────────────
# 서버는 요청 사이 상태를 들지 않는다 — 페이지마다 같은 질의를 다시 돌리고, 안정
# 정렬(점수 내림차순 + id 오름차순) 위의 오프셋으로 이어 읽는다. 커서는 그 오프셋에
# 질의 지문(qh)과 기대 id(eid)를 묶은 것이다: 지문이 어긋나면 다른 질의의 커서
# (invalid), 기대 id 가 어긋나면 결과가 그새 변한 것(stale)이다. **조용한 첫 페이지
# 폴백 금지** — 어시스턴트가 같은 페이지를 무한히 읽게 만든다.
#
# 서명(HMAC)은 두지 않는다: 커서는 로컬 신뢰 경계(베어러 토큰) 안에서만 돌고,
# owner 는 프록시가 강제 스탬핑하므로 위조 커서가 할 수 있는 최악은 자기 질의의
# 페이지를 어긋나게 읽는 것뿐이며 그것도 qh/eid 가 잡는다.

#: 페이지네이션 후보 풀 상한(암별 LIMIT 이자 total_count 의 상한). 200을 넘는
#: 순회는 검색이 아니라 열람이다 — 그쪽은 vinc_doc_list/vinc_recall 이 담당한다.
SEARCH_POOL_CAP = 200


def _query_fingerprint(query: str, owner: str | None, filters: dict | None,
                       detail: str) -> str:
    basis = json.dumps([query, owner or "", sorted((filters or {}).items()),
                        detail], ensure_ascii=False)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def cursor_encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def cursor_decode(s: str) -> dict:
    """무효 커서는 ValueError("invalid_cursor: …") — 호출부가 명시 오류로 표면화한다."""
    try:
        pad = "=" * (-len(s) % 4)
        data = json.loads(base64.urlsafe_b64decode((s + pad).encode("ascii")))
    except Exception:
        raise ValueError("invalid_cursor: not a vinc search cursor") from None
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("invalid_cursor: unknown cursor version")
    return data


def _summary_row(r: dict) -> dict:
    """청크 행의 요약형 — 전문(text) 대신 발췌(snippet)를 싣는다(GM-14 요약·상세 분리).
    발췌 240자·공백 정규화는 node_context 의 기존 관례(§excerpts)와 같다. 전문은
    doc_id 로 vinc_doc_get 이 돌려준다(chunk id 규약 `{doc_id}#{n:05d}`)."""
    flat = " ".join(str(r.get("text") or "").split())
    out = {k: v for k, v in r.items() if k != "text"}
    out["snippet"] = flat[:240]
    out["truncated"] = len(flat) > 240
    rid = str(r.get("id") or "")
    out["doc_id"] = rid.rsplit("#", 1)[0] if "#" in rid else ""
    return out


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


def domain_names_plan(*, owner: str | None = None) -> QueryPlan:
    """소유자의 **주제 이름 집합**(개수·집계 없음).

    :func:`domain_counts_plan` 과 같은 3개 라벨을 보되 카운트를 세지 않는다 —
    무료 등급 주제 상한이 **매 적재마다** 도는 경로라 집계·정렬이 순수 낭비다.

    라벨 집합을 `domains()` 와 **일부러 일치**시켰다(Episode·Concept·Record).
    상한이 다른 집합을 세면 사용자가 `vinc_domains` 로 4개를 보면서 "5개 상한에
    걸렸다"는 말을 듣게 된다 — 그 불일치가 곧 지원 문의가 된다."""
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
        f"MATCH (e:Episode) {where_e} RETURN DISTINCT e.domain AS domain "
        "UNION ALL "
        f"MATCH (c:Concept) {where_c} RETURN DISTINCT c.domain AS domain "
        "UNION ALL "
        f"MATCH (r:Record) {where_r} RETURN DISTINCT r.domain AS domain"
    )
    return QueryPlan("domain_names", cypher, params,
                     note="주제 이름 집합 (상한 검사용 — 집계 없음)")


def domain_names(*, owner: str | None = None, cfg=None, store=None) -> set[str] | None:
    """소유자가 지금 쓰는 주제 이름 집합. 스토어에 못 닿으면 **None**.

    None 과 빈 집합은 다르다 — 빈 집합은 "주제가 없다"(상한 검사 통과)이고 None 은
    "모른다"다. 모르는 채로 상한을 판정하면 스토어 장애가 곧 전면 쓰기 거부가 된다.
    호출부가 이 구분을 보고 결정해야 한다."""
    res = execute(domain_names_plan(owner=owner), cfg, store=store)
    if not res.get("executed"):
        return None
    return {d for d in (row.get("domain") for row in res.get("rows", [])) if d}


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
    # D43: ts(라벨별 시간열 단일화) + 엣지 evidence/confidence. Concept→Concept 무라벨
    # `-[r]->` 이 바인딩할 수 있는 rel 테이블은 CC 4종뿐이고 전부 E-G6 열을 가져
    # (store.embedded_schema._EVIDENCE_RELS) 바인더가 통과한다 — CONNECTS/ATTACHES_TO/
    # TYPED 가 후보인 플랜(bridge/record/entity 축)에 같은 열을 붙이면 Binder 오류다.
    # ⚠ owner 필수성(SEC-1): 시작점 c 만 스코프하면 **엣지 대상 d 는 무방비**다 —
    # `graph_rows_for_ids_plan` 과 동일 규칙으로 먼 끝점도 제한한다. 특히 D43 이
    # r.evidence/r.confidence/target_ts 를 투영하면서 남의 증거 문장·신뢰도·시간이
    # 클라이언트(Flow 툴팁)까지 도달할 수 있게 됐다.
    cypher = (
        f"MATCH (c:Concept) {clause} "
        "OPTIONAL MATCH (c)-[r]->(d:Concept) WHERE d.owner_id = $owner "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        # v7(D72) — 내용 시점을 **별도 열로** 나른다(ts 에 접지 않는다). 클라이언트가
        # 둘 다 받아 두면 축 전환에 재조회가 필요 없다. Concept 만 이 열을 가지므로
        # 다른 라벨의 플랜에 그대로 옮겨 붙이면 Binder 오류다(위 경고와 같은 규칙).
        "coalesce(c.content_time, '') AS content_ts, "
        # ⚠ `pinned` 를 여기 넣지 말 것. **Concept 테이블에 그 열이 없다**
        # (`store/embedded_schema.py` — pinned 는 Episode 의 열이다). 넣으면 Binder
        # 오류로 그래프 플랜 전체가 죽는다(2026-08-02 실측: 22 테스트 동시 실패).
        # 레이아웃 정렬의 `pinned` 키가 무동작인 것은 이 때문이고, D32 의 "pin 이
        # 허브 승격 1순위"는 개념 축에서 **아직 구현된 적이 없다**(별건).
        "label(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts"
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
    우선 — 호출부가 Concept 축을 먼저 넣는 고정 순서로 결정론을 보장한다.

    D43(2026-07-22): 노드에 ``ts``(라벨별 시간열 — 플랜이 coalesce 로 단일화해
    ``ts``/``target_ts`` 로 투영), 엣지에 ``confidence``/``evidence``(E-G6 선택 열).
    ⚠ ``ts`` 계약: ``updated_at`` 은 ISO-ms UTC 라 사전식 정렬이 시간순과 일치하지만,
    ``created``/``date`` 는 자유형식이고 자주 ``''`` 다(``Record.created`` 는 오늘
    쓰는 코드가 없다). 따라서 ``ts`` 는 **정당하게 빈 문자열일 수 있고**, 소비처는
    가정하지 말고 강등해야 한다(날짜 없는 노드를 제외하거나 별도 표기).

    ⚠ dedup 예외 — ``ts`` 는 **빈 값일 때만 나중 행이 채운다**(그 외 필드는 선착
    우선 불변). 같은 노드가 여러 축에서 도착할 때 먼저 온 행이 시간열을 안 실었으면
    (또는 정말 비었으면) 뒤 행의 비지 않은 ``ts`` 로 승격한다 — 없으면 축 플랜으로만
    도착한 개념이 영구히 무날짜로 굳는다."""
    nodes: dict[str, dict] = {}

    def _node(nid, label, dom, props=None, tags=None, kind=None, domain2=None, ts=None,
              content_ts=None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label or nid, "domain": dom or "",
                          "kind": kind or "concept", "degree": 0,
                          "props": _props_to_obj(props), "tags": list(tags or []),
                          "ts": ts or "", "content_ts": content_ts or ""}
            if domain2:
                nodes[nid]["domain2"] = domain2
        else:
            # 빈 시간열만 나중 행이 채운다 — 나머지 필드는 선착 우선 불변.
            # content_ts(D72)도 같은 규칙: 열이 없는 축 플랜(Bridge·Entity)에서
            # 먼저 도착한 노드가 영구히 무시점으로 굳지 않게 한다.
            if ts and not nodes[nid]["ts"]:
                nodes[nid]["ts"] = ts
            if content_ts and not nodes[nid].get("content_ts"):
                nodes[nid]["content_ts"] = content_ts
        return nodes[nid]

    edges: list[dict] = []
    for r in rows:
        nid = r.get("id")
        if not nid:
            continue
        _node(nid, r.get("label"), r.get("domain"), r.get("props"), r.get("tags"),
              r.get("kind"), r.get("domain2"), r.get("ts"), r.get("content_ts"))
        tgt = r.get("target")
        if tgt:
            # C5: target 은 target_label(=title)로 만든다 — 예전엔 id 를 label 로 넘겨
            # target-only 로 등장한 노드가 항상 id 노출로 폴백됐다(_node 의
            # `label or nid` 폴백은 title 이 정말 없을 때만 쓰여야 한다).
            _node(tgt, r.get("target_label"), r.get("target_domain"),
                  r.get("target_props"), r.get("target_tags"), r.get("target_kind"),
                  ts=r.get("target_ts"), content_ts=r.get("target_content_ts"))
            src_dom, tgt_dom = r.get("domain"), r.get("target_domain")
            # cross_domain: 끝점 차이 규칙 유지 — 브릿지는 domain=source_domain 이라
            # 원측(source) 팔은 in-cluster, 반대측 팔만 cross(기하 정합, 특례 불요).
            cross = bool(src_dom and tgt_dom and src_dom != tgt_dom)
            edge = {"source": nid, "target": tgt,
                    "type": r.get("rel") or "RELATED_TO", "cross_domain": cross}
            if r.get("role"):
                edge["role"] = r["role"]
            # E-G6 — 관계 증거·신뢰도(선택 열). 상류 미저작 시 키를 붙이지 않는다(role 과 동형).
            if r.get("confidence"):
                edge["confidence"] = float(r["confidence"])
            if r.get("evidence"):
                edge["evidence"] = r["evidence"]
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
    MCP-SKELETON §10 공백 해소). seed 에서 depth 이내에 닿는 Concept id 를 수집한다.

    **G1(2026-08-03, ladybug 0.18.0 실측): 순회 의미론 `TRAIL` → `SHORTEST`.**
    이 플랜이 원하는 것은 *도달 집합*(DISTINCT id)이지 경로 열거가 아니다. TRAIL 은
    엣지 무재사용 경로를 **전부 펼친 뒤** DISTINCT 로 접으므로 조밀 ego 에서 그 일을
    두 번 한다. SHORTEST 는 (시작,끝) 쌍당 한 경로만 확장해 같은 집합을 더 싸게 낸다.
    실측(노드 200·평균차수 12, 같은 스토어): depth2 9.6→3.8ms · depth3 5.5→4.1ms ·
    depth4 13.4→4.5ms. **결과 차집합은 시드 자신 하나뿐**이다(TRAIL 은 depth≥3 에서
    사이클로 시드에 되돌아와 시드를 행으로 낸다 — 실측 차집합 `TRAIL-only=['n0']`).
    호출부 4곳(:func:`graph` · :func:`ego_ids` · :func:`decide` 토픽 · share 팩)이
    전부 `sorted({seed, *rows})` 로 시드를 명시 합류시키므로 **도달 집합은 동일**하고
    순수 비용 절감이다. M3(§6-2)이 막으려던 것은 기본 WALK 의 경로 폭증인데
    SHORTEST 는 애초에 폭증하지 않으므로 가드 목적도 그대로 유지된다.
    ⚠ `decide_plan`(경로 자체를 소비)은 TRAIL 을 유지한다 — 거긴 열거가 목적이다.

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
        f"MATCH (n)-[* SHORTEST 1..{depth}]-(m:Concept){m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("subgraph_ids", cypher, params,
                     note=f"seed+depth reach set (depth={depth}, SHORTEST)")


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
        "coalesce(c.created, c.updated_at, '') AS ts, "
        # v7(D72) — 내용 시점을 **별도 열로** 나른다(ts 에 접지 않는다). 클라이언트가
        # 둘 다 받아 두면 축 전환에 재조회가 필요 없다. Concept 만 이 열을 가지므로
        # 다른 라벨의 플랜에 그대로 옮겨 붙이면 Binder 오류다(위 경고와 같은 규칙).
        "coalesce(c.content_time, '') AS content_ts, "
        "label(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts"
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
        "coalesce(b.created, b.updated_at, '') AS ts, "
        "'CONNECTS' AS rel, r.role AS role, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        "coalesce(c.created, c.updated_at, '') AS target_ts"
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
        "coalesce(e.date, e.updated_at, '') AS ts, "
        # ABOUT 은 E-G6 열을 가진 rel 테이블이라 evidence/confidence 투영이 안전하다.
        "'ABOUT' AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        # D43: 부착 개념의 시간열도 투영한다 — 축 플랜으로만 도착하는 개념
        # (episode:/record:/bridge: 시드 L2)이 영구 무날짜가 되던 결함(Timeline 이
        # 전량을 '날짜 없음'으로 강등)의 절반. 나머지 절반은 shape_graph 의 ts 승격.
        "coalesce(c.created, c.updated_at, '') AS target_ts"
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
        "coalesce(rc.created, rc.updated_at, '') AS ts, "
        # v7(D72) — Record 도 content_time 을 얻었다(Concept 과 같은 축). 여기 두 끝은
        # Record·Concept 이라 둘 다 열이 있다. Bridge·Entity 축 플랜에는 열이 없으므로
        # 절대 옮겨 붙이지 않는다 — 붙이면 모든 레벨의 /graph 가 Binder 로 죽는다.
        "coalesce(rc.content_time, '') AS content_ts, "
        "'ATTACHES_TO' AS rel, r.type AS role, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        "coalesce(c.created, c.updated_at, '') AS target_ts, "
        "coalesce(c.content_time, '') AS target_content_ts"
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
        # Entity 는 created/date 열이 없다 — updated_at 만이 시간 원천.
        "coalesce(e.updated_at, '') AS ts, "
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


# 사용자 선택 프리셋 정본 — **2종**(발주자 지시 2026-08-02, D34 개정).
#   hub  : 연결이 많을수록 중심, 적을수록 바깥(동심 배치). "무엇이 중심인가".
#   rank : 전역 위상 순서를 x 에 인코딩. "무엇이 무엇보다 먼저인가".
# 구 6종(radial·circle·tree·organic·dendro·shell)은 이 둘의 변주라 내렸다.
# 옛 이름은 `_LAYOUT_ALIAS` 가 hub 로 접어 받는다(저장값·딥링크 무해).
LAYOUTS = ("hub", "rank")
_LAYOUT_ALIAS = {"radial": "hub", "circle": "hub", "tree": "hub",
                 "organic": "hub", "dendro": "hub", "shell": "hub"}
_DIRECTED_RELS = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")
# ⚠ 방향 정합(2026-08-02 수정). `DERIVES_FROM(a→b)` 은 "a 가 b 에서 나왔다"이므로
# **b 가 상류**다. 좌측 문서 트리(D69)는 이미 뒤집어 적재하는데(`_nav_depth` 주석:
# "뒤집기를 빠뜨리면 트리 전체가 조용히 뒤집힌다") 레이아웃은 안 뒤집어, 같은
# 데이터를 트리는 "원본이 위", rank 는 "파생물이 왼쪽"으로 **반대로** 읽고 있었다.
# 여기서 트리 쪽에 맞춘다 — 그쪽에만 명시 주석과 테스트가 있었다.
_REVERSED_RELS = ("DERIVES_FROM",)


def _global_rank(nodes: list[dict], directed: dict[str, set], math) -> None:
    """Rank(D50 + **D74 위계**) — 전역 위상 순서를 좌표에 인코딩한다.
    x = 랭크(선행 → 후행) · y = 열 안 정렬 · z = 도메인 슬랩(3D 전용).

    **위계를 어떻게 실었는가**(발주자 "rank 도 같은 방법이 되는지 조사" 응답):
    도메인으로 **클러스터를 만들지는 않는다** — 원 주석의 사유("계보는 도메인을
    가로지르므로 클러스터 안에서 랭크를 매기면 흐름이 끊긴다")는 지금도 맞다.
    대신 층(x)은 손대지 않고 **열 안 순서**만 도메인으로 묶는다. 층 읽기는 그대로
    두고 같은 도메인이 열을 가로질러 비슷한 높이에 서므로, 도메인 안 계보는 짧은
    가로선이 되고 도메인을 넘는 계보는 **사선으로 드러난다** — 그게 이 프리셋에서
    보고 싶은 것이다. 3D 는 거기에 z 를 더해 도메인마다 자기 깊이의 판을 준다
    (hub 의 원뿔이 여기서는 나란한 슬랩이 된다). 2D 는 z 를 안 읽으므로 같은
    그림에서 깊이만 빠진다.

    **왜 필요한가**: 2D 관점의 좌표는 여태 아무 의미도 인코딩하지 않았다(도메인을
    원 둘레에 배치했을 뿐). 흐름 관점은 좌표에 의미가 있지만 방향 엣지 3종만 그려
    나머지 관계가 안 보인다. 이 프리셋은 둘을 합친다 — 좌표는 흐름의 뜻을 갖고,
    렌더러(sigma)는 모든 엣지를 그린다.

    층화는 최장경로(Kahn 위상정렬). 사이클에 갇혀 큐에 못 든 노드는 확정된 상류의
    다음 층으로 민다(첫 방문 승리) — `mountFlow` 의 층화와 같은 규칙이라 흐름 뷰와
    2D 가 같은 랭크를 보여준다. 순수 함수(난수·시간 무의존, D17)."""
    ids = [n["id"] for n in nodes]
    inset = set(ids)
    out: dict[str, list[str]] = {}
    indeg = {i: 0 for i in ids}
    for s, targets in directed.items():
        if s not in inset:
            continue
        for t in sorted(targets):
            if t in inset:
                out.setdefault(s, []).append(t)
                indeg[t] += 1
    layer = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    settled = set(queue)
    qi = 0
    while qi < len(queue):
        u = queue[qi]; qi += 1
        for v in out.get(u, ()):
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                settled.add(v); queue.append(v)
    for i in ids:                     # 사이클 잔여 — 확정 상류 다음 층으로
        if i in settled:
            continue
        mx = -1
        for s, targets in directed.items():
            if i in targets and s in settled:
                mx = max(mx, layer[s])
        layer[i] = mx + 1
        settled.add(i)
    rows: dict[int, list[dict]] = {}
    for n in nodes:
        rows.setdefault(layer[n["id"]], []).append(n)
    cols = max(len(rows), 1)
    # 종횡비 — 열은 고정 간격으로 넓게, 행은 **가장 붐비는 열이 정해진 높이에 맞게**
    # 적응한다. 뿌리 열은 대개 수십 개라, 행 간격을 고정하면 세로로만 길어져 캔버스가
    # 세로 스크롤이 된다(실측). 폭 = 0.5×열, 높이 = 3.0 고정 → 열이 늘수록 가로로 눕는다.
    tallest = max((len(r) for r in rows.values()), default=1)
    y_step = 3.0 / max(tallest - 1, 1)
    band = _domain_order(nodes)
    # 도메인 슬랩 간격 — 판이 하나뿐이면 0(평면으로 남는다), 여럿이면 [-0.6, 0.6].
    slabs = max(len(set(band.values())), 1)
    z_step = 0.0 if slabs < 2 else 1.2 / (slabs - 1)
    for lv, row in rows.items():
        # 열 안 정렬 = (도메인 띠, pinned, degree, id). 도메인이 하나뿐이면 첫 키가
        # 상수라 정렬은 예전 그대로(허브 우선, D32)로 접힌다 — 하위호환.
        row.sort(key=lambda n: (band.get(n.get("domain") or "", 0),
                                -int(bool(n.get("pinned"))),
                                -int(n.get("degree") or 0), str(n.get("id"))))
        k = len(row)
        x = (lv - (cols - 1) / 2) * 0.5
        for j, n in enumerate(row):
            n["x"] = round(x, 6)
            # ⚠ y 는 **위로 갈수록 크다**(2026-08-02 수정). 두 렌더러가 같은 규약이다:
            # three.js 는 +y 가 위, sigma 는 클립 y 를 `(1 - y) * h / 2` 로 뒤집어
            # 역시 +y 가 위다. 그래서 `j - (k-1)/2` 로 두면 1순위(j=0)가 **맨 아래**로
            # 가서 "허브 우선"이 화면에서 뒤집혀 읽혔다(실측: 같은 열에서 degree
            # 16 이 최하단, 14 가 최상단). 부호를 뒤집어 1순위를 맨 위에 세운다.
            n["y"] = round(((k - 1) / 2 - j) * y_step, 6)
            n["z"] = round((band.get(n.get("domain") or "", 0)
                            - (slabs - 1) / 2) * z_step, 6)
            # 평면 정본은 x·y 그대로 — rank 는 애초에 평면 문법이라 접을 것이 없다.
            n["x2"], n["y2"] = n["x"], n["y"]


def layout_positions(nodes: list[dict], edges: list[dict],
                     layout: str = "radial") -> list[dict]:
    """결정론 배치(D17 + D34 프리셋 + **D74 위계 폭발**) — 순수 함수. 난수·시간
    무의존이라 같은 그래프는 언제 그려도 같은 좌표다(플랜 순수성 계약과 동형).

    **좌표를 두 벌 낸다**(D74, 발주자 지시 2026-08-02):
      ``x``/``y``/``z`` = 3D 정본 · ``x2``/``y2`` = 2D 정본.
    2D 는 3D 를 **투영한 것이 아니다**. z 를 눌러 평탄화하면 z 로만 갈라져 있던
    노드들이 평면에서 같은 점이 되는데(원뿔 배치에서는 흔하다), 사후 보정으로
    떼어내면 위치의 뜻이 흐려진다. 그래서 같은 트리를 **평면 문법으로 다시 배치**
    한다(원뿔 → 부채꼴). 겹침은 고쳐지는 것이 아니라 **생기지 않는다** —
    부채꼴은 서로 겹치지 않고 부채꼴 안에서는 각도 슬롯이 전부 다르다.
    (그래도 exact 중복이 남을 수 있는 경계는 `_dedupe_planar` 가 마지막에 막는다.)

    내부 정렬은 (pinned DESC, degree DESC, id) — pin 이 허브 승격 1순위(D32).
    ⚠ Concept 에는 `pinned` 열이 없어(embedded_schema) 개념 축에서는 무동작이다."""
    import math
    GOLDEN = 2.399963229728653
    layout = _LAYOUT_ALIAS.get(layout, layout)
    if layout not in LAYOUTS:
        layout = "hub"
    adj: dict[str, set] = {}
    directed: dict[str, set] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        rel = e.get("type") or ""
        if rel in _DIRECTED_RELS:
            # 상류 → 하류로 정규화한다(위 _REVERSED_RELS 주석 참조).
            if rel in _REVERSED_RELS:
                directed.setdefault(t, set()).add(s)
            else:
                directed.setdefault(s, set()).add(t)
    if layout == "rank":
        _global_rank(nodes, directed, math)
    else:
        _global_hub(nodes, math, GOLDEN)
    _dedupe_planar(nodes)
    return nodes


# ── 도메인 위계(D74) ──────────────────────────────────────────────────────────
# `domain` 은 문자열이지만 **경로**다: "vinc/architecture" 는 vinc 아래 architecture.
# 여태 코드 어디에도 이 문자열을 가르는 곳이 없어 도메인이 전부 평평한 이름이었고,
# 그래서 "Vinc 아래 design" 과 "다른 프로젝트의 design" 이 같은 칸을 썼다.
# 배치는 이 경로를 읽는 **첫 소비처**다(트리·범례·필터는 이 함수를 재사용한다).
def _domain_path(dom: str) -> tuple[str, ...]:
    """도메인 문자열 → 경로 조각. 빈 값·앞뒤 슬래시·중복 슬래시를 모두 흡수한다."""
    return tuple(p for p in (dom or "").split("/") if p)


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    """모든 도메인이 공유하는 접두 = 이 그래프의 **뿌리**.

    전부 `vinc/*` 면 뿌리는 `vinc` 이고 가지는 architecture·design·… 이 된다.
    접두를 안 벗기면 뿌리에 자식이 하나뿐이라 "한 점에서 한 점으로" 가는 헛가지가
    생긴다(폭발이 아니라 막대). 도메인이 없는 노드는 이 계산에서 빼고(그것들은
    어차피 뿌리에 직접 붙는다) 남은 것이 없으면 접두도 없다."""
    real = [p for p in paths if p]
    if not real:
        return ()
    out: list[str] = []
    for i in range(min(len(p) for p in real)):
        seg = real[0][i]
        if all(p[i] == seg for p in real):
            out.append(seg)
        else:
            break
    # 접두가 경로 전체와 같아지면(도메인이 하나뿐) 마지막 조각은 가지로 남긴다 —
    # 안 그러면 가지가 0개가 되어 전부 뿌리에 뭉친다.
    if len(out) == min(len(p) for p in real):
        out = out[:-1]
    return tuple(out)


class _Branch:
    """도메인 경로 트리의 마디. 자식(하위 도메인) + 자기 몫의 노드를 든다."""
    __slots__ = ("seg", "kids", "items", "weight")

    def __init__(self, seg: str):
        self.seg = seg
        self.kids: dict[str, "_Branch"] = {}
        self.items: list[dict] = []
        self.weight = 0

    def child(self, seg: str) -> "_Branch":
        b = self.kids.get(seg)
        if b is None:
            b = self.kids[seg] = _Branch(seg)
        return b


def _build_tree(nodes: list[dict]) -> _Branch:
    """노드들을 도메인 경로 트리에 담는다. 가지 무게 = 그 아래 노드 총수."""
    paths = [_domain_path(n.get("domain") or "") for n in nodes]
    base = _common_prefix(paths)
    root = _Branch("")
    for n, p in zip(nodes, paths):
        cur = root
        for seg in p[len(base):]:
            cur = cur.child(seg)
        cur.items.append(n)

    def weigh(b: _Branch) -> int:
        b.weight = len(b.items) + sum(weigh(k) for k in b.kids.values())
        return b.weight
    weigh(root)
    return root


def _ordered_children(b: _Branch) -> list:
    """가지 안 순서 — **무거운 가지 먼저, 그다음 연결 많은 개념**.

    무거운 것이 먼저 오면 원뿔/부채꼴에서 축에 가까운 자리를 받는다(주 분사구).
    개념은 degree 내림차순이라 가지 안에서도 "중심순"이 그대로 산다(D32).
    반환 원소는 (weight, kind, key, payload) — kind 로 재귀/배치를 가른다."""
    subs = sorted(b.kids.values(), key=lambda k: (-k.weight, k.seg))
    items = sorted(b.items, key=lambda n: (-int(bool(n.get("pinned"))),
                                           -int(n.get("degree") or 0), str(n.get("id"))))
    return ([(k.weight, "branch", k.seg, k) for k in subs]
            + [(1, "node", str(n.get("id")), n) for n in items])


def _domain_order(nodes: list[dict]) -> dict[str, int]:
    """도메인 문자열 → 띠 번호. **hub 의 가지 순서와 같은 순서**다.

    두 프리셋이 도메인을 다른 순서로 세우면 관점을 바꿀 때마다 "무엇이 먼저인가"가
    달라 보인다. 순서의 원천을 트리 한 곳에 두고 둘 다 여기서 읽는다."""
    root = _build_tree(nodes)
    order: dict[str, int] = {}

    def walk(b: _Branch) -> None:
        for _w, kind, _key, payload in _ordered_children(b):
            if kind == "branch":
                walk(payload)
            else:
                dom = payload.get("domain") or ""
                if dom not in order:
                    order[dom] = len(order)
    walk(root)
    return order


# 반경 계단 — 가지 깊이마다 한 겹씩 밖으로. 잎은 자기 가지 껍질 바깥에 흩어진다.
_R_BRANCH = (0.0, 0.52, 0.82, 1.02)     # 뿌리 · 도메인 · 하위 · 그 아래
_R_LEAF = 0.5                            # 잎이 가지 껍질 밖으로 더 나가는 폭
_CONE0 = 1.15                            # 뿌리에서 도메인으로 퍼지는 반각(rad)


def _shell(depth: int) -> float:
    if depth < len(_R_BRANCH):
        return _R_BRANCH[depth]
    return _R_BRANCH[-1] + 0.16 * (depth - len(_R_BRANCH) + 1)


def _global_hub(nodes: list[dict], math, GOLDEN) -> None:
    """Hub — **중심에서 터져 나가는 위계**(D74, 구 동심 배치 개정).

    뿌리(이 그래프의 공통 도메인 = 지금은 `vinc`)가 원점이고, 도메인이 사방으로
    뻗고, 그 도메인이 향한 **그 방향으로** 하위 개념이 다시 퍼진다. 폭발의 잔해가
    한 방향으로 계속 날아가는 그림이다.

    구 배치는 degree 하나로 동심원을 그렸다. 그때는 도메인이 평평한 이름이라 위계가
    없었지만 지금은 `vinc/architecture` 처럼 경로다 — 좌표가 그 구조를 안 실으면
    "무엇이 무엇 아래인가"를 화면이 답하지 못한다. 중심성은 버리지 않는다: 가지
    **안에서** degree 순으로 축에 가까운 자리를 준다(D32 는 이제 가지 단위로 산다).

    3D: 자식 방향 = 부모 방향 주위 **원뿔** 안. 극각 θ ∝ √(누적 무게 비율)이라
    캡 면적이 무게에 비례하고(고른 밀도), 방위각은 황금각이라 어느 개수에서도
    겹치지 않는다. 무거운 가지가 축 가까이 = 주 분사구.

    2D: 같은 트리를 **부채꼴**로 — 자식마다 무게 비례 각도 구간을 나눠 갖고 그
    구간 안에서 다시 나눈다. 원뿔의 평면 대응물이라 3D 와 같은 이야기를 한다.
    투영이 아니므로 z 평탄화에 따른 겹침이 **원리적으로** 생기지 않는다.

    순수 함수(난수·시간 무의존, D17)."""
    root = _build_tree(nodes)

    def basis(d):
        """d 에 수직인 정규직교 두 축 — 원뿔 안 방위각을 세울 좌표계."""
        ax, ay, az = d
        # d 와 가장 안 나란한 축을 골라 외적 — 축이 나란하면 0 벡터가 나온다.
        hx, hy, hz = (1.0, 0.0, 0.0) if abs(ax) < 0.9 else (0.0, 1.0, 0.0)
        ux, uy, uz = ay * hz - az * hy, az * hx - ax * hz, ax * hy - ay * hx
        ul = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        ux, uy, uz = ux / ul, uy / ul, uz / ul
        vx, vy, vz = ay * uz - az * uy, az * ux - ax * uz, ax * uy - ay * ux
        return (ux, uy, uz), (vx, vy, vz)

    def spray(d, theta: float, phi: float):
        """부모 방향 d 를 축으로 극각 θ · 방위각 φ 만큼 튼 단위 벡터."""
        (ux, uy, uz), (vx, vy, vz) = basis(d)
        ct, st = math.cos(theta), math.sin(theta)
        cp, sp = math.cos(phi), math.sin(phi)
        return (d[0] * ct + (ux * cp + vx * sp) * st,
                d[1] * ct + (uy * cp + vy * sp) * st,
                d[2] * ct + (uz * cp + vz * sp) * st)

    def sphere(mid: float, i: int):
        """무게 비례 피보나치 구면점 — 뿌리에서만 쓴다.

        뿌리의 자식(도메인)은 **사방으로** 나가야 폭발이 된다. 원뿔로 뿌리면 한쪽
        반구에만 몰려 "부채"가 되지 한 점에서 터진 그림이 아니다. z 를 무게 비례로
        띠 나누고 방위각을 황금각으로 돌리면 개수와 무관하게 고르게 덮인다."""
        z = 1.0 - 2.0 * mid
        rr = math.sqrt(max(0.0, 1.0 - z * z))
        ang = i * GOLDEN
        return (math.cos(ang) * rr, math.sin(ang) * rr, z)

    def place(b: _Branch, depth: int, d3, a0: float, a1: float, cone: float) -> None:
        kids = _ordered_children(b)
        total = max(sum(w for w, *_ in kids), 1)
        r_here = _shell(depth)
        span = a1 - a0
        acc = 0
        for i, (w, kind, _key, payload) in enumerate(kids):
            mid = (acc + w / 2) / total            # 누적 무게 비율(0~1)
            frac = (i + 0.5) / len(kids)           # 순번 비율 — 반경 램프에 쓴다
            # 3D — 뿌리는 구면 전체로, 그 아래는 부모 방향 주위 원뿔 안으로.
            # (원뿔에서는 무게가 앞쪽 = 축 근처일수록 θ 가 작다 = 주 분사구.)
            nd3 = (sphere(mid, i) if depth == 0
                   else spray(d3, cone * math.sqrt(mid), i * GOLDEN))
            # 2D — 부채꼴: 무게 비례로 구간을 나눠 갖는다.
            b0 = a0 + span * (acc / total)
            b1 = a0 + span * ((acc + w) / total)
            ang = (b0 + b1) / 2
            acc += w
            if kind == "branch":
                place(payload, depth + 1, nd3, b0, b1, cone * 0.58)
                continue
            n = payload
            # 잎 — 자기 가지 껍질 **바깥**으로. 순번이 뒤일수록 더 멀리(중심순 유지).
            rr = r_here + _R_LEAF * (0.30 + 0.70 * math.sqrt(frac))
            n["x"] = round(nd3[0] * rr, 6)
            n["y"] = round(nd3[1] * rr, 6)
            n["z"] = round(nd3[2] * rr, 6)
            n["x2"] = round(math.cos(ang) * rr, 6)
            n["y2"] = round(math.sin(ang) * rr, 6)

    # 뿌리의 3D 기준 방향은 +z 로 잡고 첫 원뿔을 거의 반구까지 연다(_CONE0).
    # 2D 는 뿌리에서 한 바퀴(2π)를 통째로 나눠 준다.
    place(root, 0, (0.0, 0.0, 1.0), 0.0, 2 * math.pi, _CONE0)


def _dedupe_planar(nodes: list[dict]) -> None:
    """평면 좌표의 **exact 중복**을 마지막에 막는다(D74 요구: 시스템이 회피할 것).

    부채꼴 배치는 원리적으로 겹치지 않지만, 경계 조건이 남는다: 잎이 하나뿐인
    가지가 여럿이면 각도 구간이 달라도 반경 램프가 같아 좌표가 근접하고, rank 처럼
    격자에 앉히는 프리셋은 애초에 같은 칸이 나올 수 있다. 여기서는 **똑같은 점**만
    본다 — 가까운 것은 정보(밀집)이지만 똑같은 것은 노드 하나가 사라진 것이다.

    떼어내기는 결정론이다: 같은 좌표를 받은 것들을 id 순으로 세우고 황금각 사다리로
    미세하게 민다(원점이면 반경으로, 아니면 그 자리의 접선 방향으로). 크기가 아주
    작아 배치의 뜻을 바꾸지 않는다."""
    import math
    GOLDEN = 2.399963229728653
    seen: dict[tuple, list[dict]] = {}
    for n in nodes:
        x = n.get("x2", n.get("x", 0.0)) or 0.0
        y = n.get("y2", n.get("y", 0.0)) or 0.0
        seen.setdefault((round(x, 6), round(y, 6)), []).append(n)
    for (x, y), group in seen.items():
        if len(group) < 2:
            continue
        for k, n in enumerate(sorted(group, key=lambda m: str(m.get("id")))[1:]):
            eps = 0.012 * (1 + k // 8)
            ang = (k + 1) * GOLDEN
            n["x2"] = round(x + math.cos(ang) * eps, 6)
            n["y2"] = round(y + math.sin(ang) * eps, 6)


# 시간축(D72). `recorded` = 기록 시각(현행 ts), `content` = 문서 내용이 가리키는 시점.
BASIS_RECORDED = "recorded"
BASIS_CONTENT = "content"
_BASES = (BASIS_RECORDED, BASIS_CONTENT)


def _apply_basis(shaped: dict, basis: str) -> str:
    """`content` 축이면 노드의 `ts` 를 `content_ts` 로 갈아 끼운다.

    **Cypher 를 분기하지 않는 이유**: 시간열을 조건절에 넣는 순간 플랜 수만큼 Binder
    함정(§4)이 늘어난다. 투영은 두 열을 항상 싣고, 어느 쪽을 쓸지는 여기서 정한다.
    클라이언트도 두 열을 다 받으므로 축 전환에 재조회가 필요 없다.

    돌려주는 값은 **실제로 적용한 축**이다 — 미지 값은 조용히 recorded 로 강등하고
    그 사실을 응답이 에코한다(화면이 무엇을 셌는지 밝힐 수 있게)."""
    if basis != BASIS_CONTENT:
        return BASIS_RECORDED
    for n in shaped.get("nodes", []):
        n["ts"] = n.get("content_ts") or ""
    return BASIS_CONTENT


def graph(*, owner: str, domain: str | None = None, seed: str | None = None,
          depth: int = 2, layout: str = "radial", basis: str = BASIS_RECORDED,
          cfg=None, store=None) -> dict:
    """지식 그래프 — 기본은 owner(±domain) 스코프 전체(Atlas L1), ``seed`` 지정 시
    seed+depth ego 서브그래프(Atlas L2 = MCP `vinc_graph` 동일 경로). 노드에는
    결정론 레이아웃 좌표(x,y,z)가 포함된다(D17) — ``layout`` 프리셋은 D34
    (radial·circle·tree·organic·rank·dendro, 미지 값 radial 강등).

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
                                    "props": {}, "tags": [], "ts": ""}]   # A4 형태 정합
        # E-G2 — 시드 관점 개인화 랭킹(ppr). layout 전에 부여(순수 메타, 좌표 무관).
        attach_ppr(shaped, [seed])
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout)
        used = _apply_basis(shaped, basis)
        return {"executed": executed, "seed": seed, "depth": depth, "found": found,
                "basis": used, **shaped}
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
    used = _apply_basis(shaped, basis)
    return {"executed": res.get("executed", False), "basis": used, **shaped}


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


# ── 원장 롤업(D43) — 출처 × 주제 × 연결상태 ──────────────────────────────────
# Atlas Totals 관점의 데이터 원천. 두 플랜(출처 축·연결 축)을 각각 UNION ALL 로
# 가져와 **순수 파이썬에서 접는다**(`domain_counts_plan` → `domains` 와 동형) —
# Cypher 안에서 EXISTS{}·서브쿼리로 영리하게 굴지 않는다(임베디드 방언 이식성).
# 고아 판정을 `component_diagnosis`(native algo 확장 위임)에 맡기지 않는 것도
# 의도다 — algo 는 오프라인 번들에서 executed=False 로 강등되는데, 원장은 항상
# 그려져야 한다.

def ledger_origin_plan(*, owner: str, domain: str | None = None,
                       ids: list[str] | None = None) -> QueryPlan:
    """원장 출처 축 — 개념 전량('all') + 에피소드 부착('episodic') + 문서 부착
    ('record': 구 Record ATTACHES_TO **또는** D45 문서의 Chunk MENTIONS) 표식 행. 한 개념이 여러 에피소드에 부착되면 같은 표식이 여러 번
    오지만 접기(:func:`ledger`)가 불리언이라 무해하다.

    owner 규칙(SEC-1): **엣지 패턴의 양 끝을 모두** 필터한다 — 소유자 스코프 id
    집합만으론 반대편 끝이 안전해지지 않는다. Episode/Record/Concept 는 엄격
    ``= $owner``. 모든 UNION 분기가 ``$owner`` 를 바인딩한다(Kùzu 는 미사용
    파라미터를 거부 — §6 방언). ``domain``/``ids`` 는 절을 낼 때만 params 에 넣고,
    같은 이유로 낸 절은 **모든 분기에** 붙는다.

    ``ids``(D43 ego 원장): 개념 c 를 시드 주변 집합으로 제한한다 — ``domain`` 렌즈와
    상호 배제(축 플랜 관례). 부착 주체(Episode/Record)는 집합으로 제한하지 않는다 —
    부착은 전역 사실이라 ego 밖 에피소드가 ego 개념에 부착돼도 출처다."""
    assert not (domain and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if domain:
        and_scope = " AND c.domain = $domain"
        params["domain"] = domain
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = ids
    cypher = (
        f"MATCH (c:Concept) WHERE c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, "
        "coalesce(c.created, c.updated_at, '') AS ts, 'all' AS mark "
        "UNION ALL "
        "MATCH (e:Episode)-[:ABOUT]->(c:Concept) "
        f"WHERE e.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'episodic' AS mark "
        "UNION ALL "
        "MATCH (rc:Record)-[:ATTACHES_TO]->(c:Concept) "
        f"WHERE rc.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'record' AS mark "
        # D54 — D45 문서(Chunk=SSoT)도 '파일에서'로 센다. 문서를 넣는 경로가 둘인데
        # (구 Record + 신 _DocMeta/Chunk) 원장이 구 축만 보고 있어, 앱에서 문서를
        # 넣거나 팩에 documents 를 실어 와도 출처가 영원히 "없음"이었다(실측: 개념
        # 74개 전부 unsourced). 같은 'record' 칸에 접는다 — 화면 문구가 "파일에서"라
        # 두 물리 표현이 같은 뜻이고, 새 칸을 만들면 카피·범례가 함께 늘어난다.
        "UNION ALL "
        "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
        f"WHERE ch.owner_id = $owner AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, '' AS ts, 'record' AS mark"
    )
    return QueryPlan("ledger", cypher, params, note="ledger origin axis (concept × source)")


def ledger_link_plan(*, owner: str, domain: str | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """원장 연결 축 — 개념 대 개념 직접 엣지('cc') + Bridge 매개 팔('bridge') 행.
    각 행이 차수 1 을 뜻하고, ``domain``/``other`` 가 다르면 그 개념은 관문
    (gateway)이다. 무방향 ``-[r]-`` 라 한 엣지가 양쪽 끝에서 각각 한 번 세어진다.

    owner 규칙: Concept 양끝 엄격. **Bridge 만 예외** — 과도기 술어
    (:func:`_bridge_owner_pred`, ``owner OR ''``)를 쓴다. 엄격 등호는 레거시
    미소유 행(v2 이전)을 가진 기존 로컬 사용자의 결합을 조용히 소거하는 회귀다.
    owner_id 열이 없는 Domain/Chunk/SkillExecution 은 롤업에 합류시키지 않는다.

    ``ids``(D43 ego 원장): 제한은 **c 쪽에만** 건다 — 먼 끝점 o 는 owner 스코프만
    유지하고 ego 로 좁히지 않는다(전역 차수·관문: 엣지가 ego 를 떠나도 그 구성원의
    연결이다). domain 렌즈가 o 를 안 건드리는 기존 태도와 동형이라 SEC-1 양끝
    스코프는 그대로다. ``domain`` 과 상호 배제, 모든 UNION 분기에 절을 붙인다."""
    assert not (domain and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if domain:
        and_scope = " AND c.domain = $domain"
        params["domain"] = domain
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = ids
    cypher = (
        "MATCH (c:Concept)-[r]-(o:Concept) "
        "WHERE c.owner_id = $owner AND o.owner_id = $owner "
        f"AND o.concept_id <> c.concept_id{and_scope} "
        "RETURN c.concept_id AS id, c.domain AS domain, o.domain AS other, 'cc' AS mark "
        "UNION ALL "
        "MATCH (b:Bridge)-[:CONNECTS]->(c:Concept) "
        f"WHERE {_bridge_owner_pred('b')} AND c.owner_id = $owner{and_scope} "
        "RETURN c.concept_id AS id, b.source_domain AS domain, "
        "b.target_domain AS other, 'bridge' AS mark"
    )
    return QueryPlan("ledger", cypher, params, note="ledger link axis (concept × coupling)")


def _ledger_ego_ids(seed: str, *, owner: str, depth: int = 2,
                    cfg=None, store=None) -> dict:
    """ego 원장(D43)의 모집단 — 시드 주변 Concept id 집합. L2 서브그래프와 **같은
    순회 기계**를 재사용해 두 관점의 모집단이 일치한다(:func:`graph` 시드 갈래와
    동형):

    - Concept 시드: :func:`subgraph_ids_plan`(depth 1..4 클램프, TRAIL 가드) 도달
      개념 ∪ 시드 자신(graph() 997 의 시드 합류와 동일).
    - episode:/record:/bridge: 시드: 순회가 :Concept 미매치라 0행 — 축 플랜(ids
      모드)의 부착 끝점(target = Concept id)만 모은다. L2 keep 집합의 Concept
      부분집합과 동일. 플랜별 강등은 graph() 와 같은 태도로 건너뛴다(미마이그레이션
      스토어 자가 처리 — 순회가 이미 스토어 도달을 증명했다).
    - entity: 시드(미지 접두사 포함): TYPED 폐쇄 성분이라 Concept 층에 구조적으로
      닿지 않는다 — 항상 빈 ego.

    성공 시 ``{"executed": True, "ids": [...]}``, 순회 강등 시 execute 딕셔너리를
    그대로 돌려준다(호출부가 응답 전체를 강등)."""
    kind = _seed_table(seed)[0]
    if kind == "Entity":
        return {"executed": True, "ids": []}
    res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return res
    if kind == "Concept":
        return {"executed": True,
                "ids": sorted({seed, *(r["id"] for r in res.get("rows", []) if r.get("id"))})}
    members: set[str] = set()
    for plan_fn in _GRAPH_AXIS_PLANS:
        ax = execute(plan_fn(owner=owner, ids=[seed]), cfg, store=store)
        if not ax.get("executed"):
            continue
        members.update(r["target"] for r in ax.get("rows", []) if r.get("target"))
    return {"executed": True, "ids": sorted(members)}


def node_context(*, owner: str, node: str, limit: int = 4,
                 cfg=None, store=None) -> dict:
    """노드→정본 컨텍스트(D45-V) — 뷰에서 노드를 눌렀을 때 보여줄 "이건 무엇이고,
    어느 정본이 근거인가". 조회 전용.

    1) 노드 자체 설명: Concept 이면 one_liner, Episode 면 summary.
    2) 근거 발췌: (ch:Chunk)-[:MENTIONS]->(c) 역참조 — 이 개념을 언급하는 정본
       청크의 발췌 + 문서 제목(_DocMeta 조인). 청크 owner 는 과도기 술어
       (owner OR '' — v4 이전 파일 파생 레거시, Bridge F5 전례와 동일).
    3) MENTIONS 가 비면 제목 FTS 폴백(arm='fts') — 링크가 아직 없어도 "관련
       정본"을 준다. 확장 미가용이면 조용히 빈 발췌(arm='none', 실패 아님 —
       발췌는 증강이지 상세 패널의 성립 조건이 아니다).

    4) 연결 요약 + 관계 목록(``links``) — 상세 레일이 쓰는 **수치 정본**. 여태
       프론트가 "마지막으로 그린 그래프의 엣지 집합"(스테일 잔상)에서 세고, 드릴다운
       경로로 열면 degree 를 0 으로 박아 "연결 0개 · → 3 나감"처럼 자기모순을 냈다.
       세는 자리를 노드 자신으로 옮겨 어느 동선으로 열어도 같은 숫자가 나온다.

    반환: {executed, node, node_kind, title, one_liner, props,
           excerpts: [{doc_id, doc_title, section, excerpt, method}], arm,
           links: {degree, out, in, attached,
                   items: [{id, title, domain, type, dir, evidence, confidence}]}}

    ``props`` 는 개념의 자유 속성(dict). Episode 는 이 열이 없어 항상 {} 다."""
    cfg = cfg or load_config()
    base: dict = {"kind": "node-context", "executed": False, "node": node,
                  "one_liner": "", "props": {}, "excerpts": [], "arm": "none"}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        lim = max(1, min(int(limit), 12))
        # ① 노드 메타 — Concept 우선, 없으면 Episode(타임라인 뷰의 클릭 대상).
        rows = store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $owner "
            "RETURN c.title AS title, c.one_liner AS ol, c.props AS props",
            {"id": node, "owner": owner})
        if rows:
            # `props` 를 함께 싣는다(D73). `one_liner` 는 **쓰기 경로가 없는 열**이라
            # (`api/fragment.py` 가 SET 하지 않는다 — MCP 적재분 전량이 빈 값) 이것만
            # 돌려주면 상세 패널·묻기 답변이 통째로 빈다. 사람이 쓴 한두 문장 요약은
            # 실제로는 props 에 들어와 있고(검색은 이미 본다 — matched_in:'props'),
            # 조회만 그것을 안 주고 있었다. 호출부가 one_liner → props 순으로 고른다.
            base.update(executed=True, node_kind="concept",
                        title=rows[0].get("title") or node,
                        one_liner=rows[0].get("ol") or "",
                        props=_props_to_obj(rows[0].get("props")))
        else:
            ep = store.run(
                "MATCH (e:Episode) WHERE e.episode_id = $id AND e.owner_id = $owner "
                "RETURN e.title AS title, e.summary AS s", {"id": node, "owner": owner})
            if not ep:
                base["note"] = f"unknown node: {node}"
                return base
            base.update(executed=True, node_kind="episode",
                        title=ep[0].get("title") or node,
                        one_liner=ep[0].get("s") or "")
            return base   # Episode 는 MENTIONS 대상이 아니다 — 요약이 곧 설명.
        # ② MENTIONS 역참조 — 정본 발췌.
        ment = store.run(
            "MATCH (ch:Chunk)-[m:MENTIONS]->(c:Concept) "
            "WHERE c.concept_id = $id AND c.owner_id = $owner "
            "AND (ch.owner_id = $owner OR ch.owner_id = '') "
            "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
            "ch.section_title AS section, ch.text AS text, m.method AS method "
            f"ORDER BY ch.doc_id, ch.chunk_index LIMIT {lim}",
            {"id": node, "owner": owner}) or []
        arm = "mentions" if ment else "none"
        # ③ FTS 폴백 — 링크 없는 개념도 제목으로 관련 정본을 찾는다(증강, 실패 무해).
        if not ment and base["title"]:
            try:
                hits = store.fulltext_search(base["title"], lim) or []
                ids = [h.get("id") for h in hits if h.get("id")]
                if ids:
                    ment = store.run(
                        "MATCH (ch:Chunk) WHERE ch.chunk_id IN $ids "
                        "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                        "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
                        "ch.section_title AS section, ch.text AS text, "
                        "'fts' AS method", {"ids": ids, "owner": owner}) or []
                    arm = "fts" if ment else "none"
            except Exception:
                pass   # 인덱스/확장 부재 — 발췌 없이 반환(arm='none')
        # ④ 문서 제목 조인(_DocMeta) + 발췌 정리(공백 접기, 240자).
        doc_ids = sorted({r["doc_id"] for r in ment if r.get("doc_id")})
        titles: dict[str, str] = {}
        if doc_ids:
            for r in store.run(
                    "MATCH (d:_DocMeta) WHERE d.doc_id IN $ids "
                    "RETURN d.doc_id AS id, d.title AS t", {"ids": doc_ids}) or []:
                titles[r["id"]] = r.get("t") or ""
        base["excerpts"] = [{
            "doc_id": r.get("doc_id") or "",
            "doc_title": titles.get(r.get("doc_id") or "", "") or (r.get("doc_id") or ""),
            "section": r.get("section") or "",
            "excerpt": " ".join((r.get("text") or "").split())[:240],
            "method": r.get("method") or "",
        } for r in ment]
        base["arm"] = arm
        base["links"] = _node_links(store, node, owner=owner)
        return base
    except Exception as exc:
        base["note"] = base["error"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


# 상세 레일 관계 목록 상한 — 허브(차수 30+)에서 레일이 끝없이 길어지지 않게.
# 잘렸는지는 `truncated` 로 밝힌다(조용한 축소 금지).
_NODE_LINKS_CAP = 24


def _node_links(store, node: str, *, owner: str) -> dict:
    """개념의 연결 요약 + 관계 목록(:func:`node_context` ④). 조회 전용.

    개념↔개념(방향 3종 + RELATED_TO)은 양방향으로 세고, 부착(Bridge 매개·Episode
    ABOUT·Record ATTACHES_TO)은 따로 센다 — 화면이 "연결 N · → 나감 · ← 들어옴"과
    "부착 N"을 다른 줄에 싣기 때문이다.

    SEC-1: 양 끝을 owner 로 건다. Bridge 만 과도기 술어(:func:`_bridge_owner_pred`).
    실패는 조용히 빈 요약으로 강등한다 — 관계 목록은 증강이고, 미마이그레이션
    스토어(E-G6 열 부재)에서 상세 패널 자체가 죽으면 안 된다."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "items": [],
             "truncated": False, "executed": False}
    rows: list[dict] = []
    try:
        rows = store.run(
            # 개념↔개념 — 무라벨 `-[r]->` 가 바인딩할 수 있는 rel 은 CC 4종뿐이고
            # 전부 E-G6 열을 가져 evidence/confidence 투영이 안전하다(graph_plan 주석).
            "MATCH (c:Concept)-[r]->(o:Concept) "
            "WHERE c.concept_id = $id AND c.owner_id = $owner AND o.owner_id = $owner "
            "RETURN o.concept_id AS id, o.title AS title, o.domain AS domain, "
            "label(r) AS type, 'out' AS dir, r.evidence AS ev, r.confidence AS conf "
            "UNION ALL "
            "MATCH (o:Concept)-[r]->(c:Concept) "
            "WHERE c.concept_id = $id AND c.owner_id = $owner AND o.owner_id = $owner "
            "RETURN o.concept_id AS id, o.title AS title, o.domain AS domain, "
            "label(r) AS type, 'in' AS dir, r.evidence AS ev, r.confidence AS conf",
            {"id": node, "owner": owner}) or []
    except Exception:
        return empty
    attached: list[dict] = []
    try:
        attached = store.run(
            # 부착 3종 — 각 rel 테이블의 열이 달라 evidence/confidence 는 ABOUT 만
            # 투영하고 나머지는 상수로 맞춘다(UNION ALL 은 컬럼 형태가 같아야 한다).
            "MATCH (b:Bridge)-[:CONNECTS]->(c:Concept) "
            f"WHERE c.concept_id = $id AND c.owner_id = $owner AND {_bridge_owner_pred('b')} "
            "RETURN b.bridge_id AS id, b.title AS title, b.source_domain AS domain, "
            "'CONNECTS' AS type, 'att' AS dir, '' AS ev, 0.0 AS conf "
            "UNION ALL "
            "MATCH (e:Episode)-[r:ABOUT]->(c:Concept) "
            "WHERE c.concept_id = $id AND c.owner_id = $owner AND e.owner_id = $owner "
            "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
            "'ABOUT' AS type, 'att' AS dir, r.evidence AS ev, r.confidence AS conf "
            "UNION ALL "
            "MATCH (rc:Record)-[:ATTACHES_TO]->(c:Concept) "
            "WHERE c.concept_id = $id AND c.owner_id = $owner AND rc.owner_id = $owner "
            "RETURN rc.record_id AS id, rc.title AS title, rc.domain AS domain, "
            "'ATTACHES_TO' AS type, 'att' AS dir, '' AS ev, 0.0 AS conf",
            {"id": node, "owner": owner}) or []
    except Exception:
        attached = []   # 부착 축만 강등 — 개념 관계는 이미 얻었다
    out_n = sum(1 for r in rows if r.get("dir") == "out")
    in_n = len(rows) - out_n
    items = []
    # 나감 먼저, 그다음 들어옴, 마지막 부착 — 화면이 읽는 순서 그대로 결정론 정렬.
    order = {"out": 0, "in": 1, "att": 2}
    for r in sorted(rows + attached,
                    key=lambda r: (order.get(r.get("dir") or "", 9),
                                   str(r.get("type") or ""), str(r.get("title") or ""),
                                   str(r.get("id") or ""))):
        items.append({
            "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
            "domain": r.get("domain") or "", "type": r.get("type") or "",
            "dir": r.get("dir") or "",
            # 근거는 **있는지 없는지**가 화면의 관심사다(문장 전문은 edge-context 가 준다).
            "evidence": bool((r.get("ev") or "").strip()),
            "confidence": float(r.get("conf") or 0.0),
        })
    return {"degree": len(rows), "out": out_n, "in": in_n, "attached": len(attached),
            "items": items[:_NODE_LINKS_CAP],
            "truncated": len(items) > _NODE_LINKS_CAP, "executed": True}


# edge_context 허용 관계 — E-G6 evidence/confidence 보유 5종만. etype 은 Cypher 에
# 보간되므로 **화이트리스트 검증이 보안 경계다**(임의 라벨 주입 차단).
_EDGE_CTX_TYPES = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM",
                   "RELATED_TO", "ABOUT")


def edge_context(*, owner: str, src: str, dst: str, etype: str,
                 cfg=None, store=None) -> dict:
    """엣지→정본 컨텍스트(D45-V 1단계) — 흐름 뷰에서 선(관계)을 눌렀을 때
    "왜 A 가 B 로 이어지는가"에 답한다. 조회 전용.

    반환: 관계의 evidence(원문 구절)·confidence + 양끝 노드 메타(title·one_liner/
    summary)와 대표 정본 발췌 1건씩(MENTIONS 첫 건 — 개념만; Episode 는 summary 가
    곧 설명). RELATED_TO 는 무방향 매치(저장 방향 무관), ABOUT 은 Episode→Concept.
    evidence 가 빈 관계도 executed=True 로 돌려준다 — UI 는 Empty 상태로 그린다
    (클릭 불가 대신 "기록된 근거 없음"을 말한다, 조용한 실패 금지)."""
    cfg = cfg or load_config()
    base: dict = {"kind": "edge-context", "executed": False,
                  "src": {"id": src}, "dst": {"id": dst}, "type": etype}
    if etype not in _EDGE_CTX_TYPES:
        base["error"] = f"unsupported edge type: {etype}"
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        if etype == "ABOUT":
            rows = store.run(
                "MATCH (a:Episode)-[r:ABOUT]->(b:Concept) "
                "WHERE a.episode_id = $s AND b.concept_id = $d "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "RETURN r.evidence AS ev, r.confidence AS cf, "
                "a.title AS at, a.summary AS ad, b.title AS bt, b.one_liner AS bd",
                {"s": src, "d": dst, "owner": owner}) or []
        else:
            arrow = "-" if etype == "RELATED_TO" else "->"
            rows = store.run(
                f"MATCH (a:Concept)-[r:{etype}]{arrow}(b:Concept) "
                "WHERE a.concept_id = $s AND b.concept_id = $d "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "RETURN r.evidence AS ev, r.confidence AS cf, "
                "a.title AS at, a.one_liner AS ad, b.title AS bt, b.one_liner AS bd",
                {"s": src, "d": dst, "owner": owner}) or []
        if not rows:
            base["note"] = "unknown edge (또는 소유 스코프 밖)"
            return base
        r = rows[0]
        def _mention_excerpt(cid: str) -> dict | None:
            m = store.run(
                "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) WHERE c.concept_id = $id "
                "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                "RETURN ch.doc_id AS doc_id, ch.text AS text "
                "ORDER BY ch.doc_id, ch.chunk_index LIMIT 1",
                {"id": cid, "owner": owner}) or []
            if not m:
                return None
            return {"doc_id": m[0].get("doc_id") or "",
                    "excerpt": " ".join((m[0].get("text") or "").split())[:240]}
        base.update(
            executed=True,
            evidence=r.get("ev") or "",
            confidence=float(r.get("cf") or 0.0),
            src={"id": src, "title": r.get("at") or src, "one_liner": r.get("ad") or "",
                 "excerpt": None if etype == "ABOUT" else _mention_excerpt(src)},
            dst={"id": dst, "title": r.get("bt") or dst, "one_liner": r.get("bd") or "",
                 "excerpt": _mention_excerpt(dst)})
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


# ── 좌측 문서 트리(D69) ──────────────────────────────────────────────────────
# 깊이 = "이 주제 안에서 얼마나 안쪽 개념인가". 방향 관계 2종만 쓴다:
#   PREREQUISITE_OF(a→b) : a 를 먼저 알아야 b — a 가 **얕다**(그대로 적재)
#   DERIVES_FROM(a→b)    : a 는 b 에서 파생 — b 가 **얕다**(뒤집어 적재)
# 뒤집기를 빠뜨리면 트리 전체가 조용히 뒤집힌다(잎이 위로 온다) — 테스트로 못박음.
# RELATED_TO 는 대칭이라 깊이에 관여하지 않는다.
_DEPTH_CAP = 64          # 사이클·병리 구조의 상한. 화면이 무한 들여쓰기 되지 않게.


def _domain_depths(concepts: dict[str, str],
                   edges: list[tuple[str, str]]) -> dict[str, int]:
    """도메인 내 개념 → 깊이. 순수 함수(스토어 무관·결정론).

    ``concepts``: {concept_id: domain} — 한 도메인 분만 넘긴다.
    ``edges``: (얕은 쪽, 깊은 쪽) 로 **이미 정규화된** 같은 도메인 엣지.

    루트 = 들어오는 엣지가 없는 개념(엣지가 하나도 없는 개념 포함). 거기서 BFS 로
    층을 매긴다. ⚠ Kahn 위상 정렬을 쓰지 않는 이유: 순수 사이클 성분은 진입차수가
    0 인 노드가 없어 **영원히 안 벗겨진다**. BFS 는 visited 집합이 있어 사이클이
    있어도 반드시 끝나고, 그러고도 안 닿은 노드(= 사이클에만 속한 개념)는 맨
    아래 층에 id 순으로 둔다. 없는 계층을 지어내지 않으면서 목록에서 빠지지도
    않게 하는 타협이다."""
    out: dict[str, int] = {}
    children: dict[str, list[str]] = {}
    indeg: dict[str, int] = {cid: 0 for cid in concepts}
    for shallow, deep in edges:
        if shallow not in concepts or deep not in concepts or shallow == deep:
            continue
        children.setdefault(shallow, []).append(deep)
        indeg[deep] = indeg.get(deep, 0) + 1
    frontier = sorted(cid for cid in concepts if indeg.get(cid, 0) == 0)
    depth = 0
    while frontier and depth < _DEPTH_CAP:
        nxt: set[str] = set()
        for cid in frontier:
            if cid in out:
                continue
            out[cid] = depth
            nxt.update(children.get(cid, ()))
        frontier = sorted(c for c in nxt if c not in out)
        depth += 1
    if len(out) < len(concepts):        # 사이클에만 속한 나머지 — 맨 아래 한 층에.
        floor = min(max(out.values()) + 1 if out else 0, _DEPTH_CAP)
        for cid in sorted(concepts):
            out.setdefault(cid, floor)
    return out


def nav_tree(*, owner: str, cfg=None, store=None) -> dict:
    """좌측 문서 트리(D69) — 주제 → 그 주제의 문서, **개념 계층 깊이순**.

    문서는 주제 열을 갖지 않으므로(`_DocMeta`, D45) 주제도 착지 노드도 전부
    언급(`MENTIONS`)에서 유도한다 — `docstore.mention_tally` 를 그대로 쓴다.
    `모든 문서` 목록과 트리가 같은 문서를 다른 주제에 걸지 않게 원천이 하나다.

    반환::

        {executed, domains: [{domain, concepts, docs: [
            {doc_id, title, node_id, node_title, depth, updated_at}]}],
         unfiled: [{doc_id, title, updated_at}], counts: {...}}

    - 문서 정렬: `(depth, title, doc_id)` — 얕은 개념의 문서가 위.
    - 개념은 있는데 문서가 없는 주제도 **빈 그룹으로 싣는다**(주제가 목록에서
      사라지면 이름을 바꾸거나 문서를 붙일 방법이 없어진다).
    - 언급이 하나도 없는 문서는 `unfiled` — 없는 주제를 지어내지 않는다(D45-V).
    """
    from api import docstore as _ds

    cfg = cfg or load_config()
    base: dict = {"kind": "nav-tree", "executed": False, "domains": [], "unfiled": []}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        crows = store.run(
            "MATCH (c:Concept) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
            "AND c.domain <> '' RETURN c.concept_id AS id, c.domain AS domain",
            {"owner": owner}) or []
        by_domain: dict[str, dict[str, str]] = {}
        cdom: dict[str, str] = {}
        for r in crows:
            cid, dom = r.get("id") or "", (r.get("domain") or "").strip()
            if not cid or not dom:
                continue
            cdom[cid] = dom
            by_domain.setdefault(dom, {})[cid] = dom

        # 방향 엣지 2종을 한 번에. 양 끝 owner 가드(SEC-1).
        erows = store.run(
            "MATCH (a:Concept)-[:PREREQUISITE_OF]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            "RETURN a.concept_id AS shallow, b.concept_id AS deep "
            "UNION ALL "
            "MATCH (a:Concept)-[:DERIVES_FROM]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            "RETURN b.concept_id AS shallow, a.concept_id AS deep",   # ← 뒤집기
            {"owner": owner}) or []
        edges_by_domain: dict[str, list[tuple[str, str]]] = {}
        for r in erows:
            s, d = r.get("shallow") or "", r.get("deep") or ""
            dom = cdom.get(s, "")
            if not s or not d or not dom or cdom.get(d) != dom:
                continue        # 교차 주제 엣지는 그 주제의 계층이 아니다
            edges_by_domain.setdefault(dom, []).append((s, d))

        depths: dict[str, int] = {}
        for dom, members in by_domain.items():
            depths.update(_domain_depths(members, edges_by_domain.get(dom, [])))

        docs = _ds.list_docs(store, owner=owner).get("docs", [])
        groups: dict[str, list[dict]] = {dom: [] for dom in by_domain}
        unfiled: list[dict] = []
        for d in docs:
            topic = d.get("topic") or ""
            node = d.get("primary_concept_id") or ""
            row = {"doc_id": d.get("doc_id") or "", "title": d.get("title") or "",
                   "updated_at": d.get("updated_at") or ""}
            if not topic or not node:
                unfiled.append(row)
                continue
            groups.setdefault(topic, []).append(
                {**row, "node_id": node, "node_title": d.get("primary_concept_title") or "",
                 "depth": int(depths.get(node, 0))})

        out = []
        for dom in sorted(groups):
            rows = sorted(groups[dom],
                          key=lambda r: (r["depth"], r["title"], r["doc_id"]))
            out.append({"domain": dom, "concepts": len(by_domain.get(dom, {})),
                        "docs": rows})
        out.sort(key=lambda g: (-len(g["docs"]), -g["concepts"], g["domain"]))
        base.update(executed=True, domains=out, unfiled=unfiled,
                    counts={"domains": len(out), "docs": len(docs),
                            "unfiled": len(unfiled)})
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


def docs_in_range(*, owner: str, start: str, end: str, domain: str | None = None,
                  limit: int = 200, basis: str = BASIS_RECORDED,
                  cfg=None, store=None) -> dict:
    """기간(날짜 범위) 항목 질의 — 타임라인 기간 브러시(D45-V2 4단계). 조회 전용.

    타임라인이 그리는 점은 **날짜를 가진 Episode·Concept** 다. 브러시로 [start,end]
    (ISO, 반개구간 [start, end])를 선택하면 그 구간 항목을 돌려준다.

    ⚠ 시간 기준은 **차트와 글자 그대로 같아야 한다**(`coalesce(date|created,
    updated_at)` — :func:`graph_plan` 의 ts 투영). 여기만 `date`/`created` 단독을
    보던 시절, 팩으로 들어온 개념은 `created` 가 비어 차트는 점 42개를 그리는데
    같은 밴드를 누르면 "0개"가 떴다(실측). 모집단이 어긋나면 화면이 자기를 반박한다
    — D52 가 원장에서 고친 것과 같은 병이다. `updated_at` 이 내용 시점이 아니라는
    캐비앳은 유효하지만, 그건 **양쪽 모두**에 적용되는 한계이지 한쪽만 엄격해야 할
    이유가 아니다. 응답에 ``basis`` 를 에코해 화면이 무엇을 셌는지 밝힐 수 있게 한다.

    ISO 문자열은 사전순 = 시간순이라 문자열 비교로 안전하게 거른다(타임라인
    ts.slice 규약과 동형). 빈 값은 ``'' >= $s`` 가 거짓이라 자동 제외된다.
    ``domain`` 렌즈 선택. SEC-1 owner 스코프.

    ``basis``(D72) — 차트가 어느 축을 그리고 있는지 그대로 받는다. `content` 축에서는
    **Episode 를 세지 않는다**: 에피소드에는 내용 시점 열이 없어(있지도 않은 값을
    빈 문자열로 취급하면 전부 구간 밖) 모집단에서 정직하게 빠진다. 대신 Record 가
    합류한다 — Concept 과 같은 열을 얻었기 때문이다.

    반환: {executed, start, end, basis, items:[{id, kind, title, ts, domain}], total}."""
    cfg = cfg or load_config()
    on_content = basis == BASIS_CONTENT
    base: dict = {"kind": "docs-in-range", "executed": False,
                  "start": start, "end": end, "items": [],
                  "basis": BASIS_CONTENT if on_content else "authored-or-updated"}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        lim = max(1, min(int(limit), 1000))
        dclause = " AND e.domain = $domain" if domain else ""
        cclause = " AND c.domain = $domain" if domain else ""
        params = {"owner": owner, "s": start, "e": end}
        if domain:
            params["domain"] = domain
        rclause = " AND rc.domain = $domain" if domain else ""
        if on_content:
            # 내용 축 — 열을 가진 라벨만(Concept·Record). 빈 값은 `'' >= $s` 가 거짓이라
            # 자동으로 빠진다: 내용 시점을 안 적은 노드를 0 이나 오늘로 채우지 않는다.
            cypher = (
                "MATCH (c:Concept) WHERE c.owner_id = $owner "
                "AND coalesce(c.content_time, '') >= $s "
                f"AND coalesce(c.content_time, '') < $e{cclause} "
                "RETURN c.concept_id AS id, 'concept' AS kind, c.title AS title, "
                "coalesce(c.content_time, '') AS ts, c.domain AS domain "
                "UNION ALL "
                "MATCH (rc:Record) WHERE rc.owner_id = $owner "
                "AND coalesce(rc.content_time, '') >= $s "
                f"AND coalesce(rc.content_time, '') < $e{rclause} "
                "RETURN rc.record_id AS id, 'record' AS kind, rc.title AS title, "
                "coalesce(rc.content_time, '') AS ts, rc.domain AS domain")
        else:
            cypher = (
                "MATCH (e:Episode) WHERE e.owner_id = $owner "
                "AND coalesce(e.date, e.updated_at, '') >= $s "
                f"AND coalesce(e.date, e.updated_at, '') < $e{dclause} "
                "RETURN e.episode_id AS id, 'episode' AS kind, e.title AS title, "
                "coalesce(e.date, e.updated_at, '') AS ts, e.domain AS domain "
                "UNION ALL "
                "MATCH (c:Concept) WHERE c.owner_id = $owner "
                "AND coalesce(c.created, c.updated_at, '') >= $s "
                f"AND coalesce(c.created, c.updated_at, '') < $e{cclause} "
                "RETURN c.concept_id AS id, 'concept' AS kind, c.title AS title, "
                "coalesce(c.created, c.updated_at, '') AS ts, c.domain AS domain")
        rows = store.run(cypher, params) or []
        rows.sort(key=lambda r: (r.get("ts") or "", r.get("id") or ""))
        items = [{"id": r["id"], "kind": r.get("kind") or "", "title": r.get("title") or r["id"],
                  "ts": r.get("ts") or "", "domain": r.get("domain") or ""}
                 for r in rows[:lim]]
        base.update(executed=True, items=items, total=len(items),
                    truncated=len(rows) > lim)
        return base
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


def ledger(*, owner: str, domain: str | None = None, seed: str | None = None,
           depth: int = 2, by: str = "domain", cfg=None, store=None) -> dict:
    """원장(D43) — 도메인별 `{출처, 연결상태}` 롤업. 조회 전용.

    ``by="concept"``(D52) — **한 행이 개념 하나**가 되는 세밀 모드. 도메인을 눌러
    들어갔을 때 생키의 가운데 열이 그 도메인 하나로 쪼그라들던 문제를 푼다(스코프를
    좁혀도 알갱이가 그대로라 "더 깊이 안 들어가는" 것처럼 보였다). 행 값은 전부
    0/1 이고 라벨은 개념 제목(`label`), 키(`domain` 필드)는 개념 id 다 — 소비처가
    구간 클릭 대상을 그대로 쓸 수 있게 모양을 유지한다. 마크 판정은 드릴다운과 같은
    :func:`_concept_in_mark` 를 쓴다(수치 정합을 구조로 보장).

    반환 행: ``{domain, concepts, episodic, record, unsourced, connected,
    gateway, orphan}``, concepts 내림차순.

    스코프 우선순위 ``seed > domain > all``. ``seed`` 지정 시 ego 롤업 — 모집단은
    L2 서브그래프와 같은 순회로 얻은 시드 주변 개념(:func:`_ledger_ego_ids`)이고,
    구성원별 연결상태는 **전역**이다(엣지가 ego 를 떠나도 센다 —
    :func:`ledger_link_plan` ids 계약). 응답에 ``scope``("seed"|"domain"|"all")와
    ``population``(롤업에 실린 개념 총수)을 가산한다 — 구 소비처는 새 키를 무시하고,
    새 소비처는 scope 키 부재로 구 사이드카(seed 무시 → 주제 폴백)를 판별한다
    (버전 스큐 우아 강등). 빈 ego(미존재 시드·부착 0·entity 시드)는 DB 왕복 없이
    ``rows=[]``·population 0 으로 답한다 — ``IN $ids`` 에 빈 리스트를 바인딩하지
    않는다(방언 위험).

    출처 3분할(episodic/record/unsourced)과 연결 3분할(connected/gateway/orphan)은
    **각각 배타**라 두 합이 모두 정확히 concepts 다 — 소비처(Totals 누적 막대)가
    셋을 더해 행 합계로 쓴다. 한 개념이 에피소드와 레코드에 동시에 부착되면 우선순위
    ``episodic > record > unsourced`` 로 한 칸에만 센다(겹침을 그대로 흘리면 막대와
    총계가 부풀었다).

    도메인이 빈 개념은 롤업 키가 없어 제외한다(`domains_plan` 의
    ``domain IS NOT NULL`` 과 같은 태도).

    두 플랜 중 **하나라도 강등되면 응답 전체를 강등**한다 — 연결 축만 죽으면 차수가
    전부 0 이라 모든 개념이 고아로 보고되는데, ``executed: true`` 를 달고 나가면
    소비처가 그 거짓을 참으로 그린다. ego 순회 강등도 같은 태도다."""
    by_id, scope, degraded = _ledger_classify(
        owner=owner, domain=domain, seed=seed, depth=depth, cfg=cfg, store=store)
    if degraded is not None:
        return degraded
    if not by_id:      # 빈 ego(개념 구성원 없음) — DB 왕복 없이 빈 롤업
        plan = QueryPlan("ledger", "", {"owner": owner, "seed": seed or ""},
                         note="empty ego (no concept members)")
        return {"plan": plan.to_dict(), "executed": True, "rows": [],
                "scope": scope, "population": 0}

    if by == "concept":
        return _ledger_by_concept(by_id, owner=owner, scope=scope, cfg=cfg, store=store)
    agg: dict[str, dict] = {}
    for b in by_id.values():
        dom = b["domain"]
        if not dom:
            continue
        a = agg.setdefault(dom, {"domain": dom, "concepts": 0, "episodic": 0,
                                 "record": 0, "unsourced": 0, "connected": 0,
                                 "gateway": 0, "orphan": 0})
        a["concepts"] += 1
        if b["episodic"]:                       # 출처는 엄격 분할(episodic > record)
            a["episodic"] += 1
        elif b["record"]:
            a["record"] += 1
        else:
            a["unsourced"] += 1
        if b["degree"] == 0:
            a["orphan"] += 1
        if b["gateway"]:
            a["gateway"] += 1
        if b["degree"] > 0 and not b["gateway"]:
            a["connected"] += 1
    rows = sorted(agg.values(), key=lambda r: (-r["concepts"], r["domain"]))
    # D43 ego: 스코프 에코 + 모집단(행에 실린 개념 총수 — 행 합계와 항상 일치).
    return {"executed": True, "rows": rows, "scope": scope,
            "population": sum(r["concepts"] for r in rows)}


def _ledger_by_concept(by_id: dict, *, owner: str, scope: str, cfg, store) -> dict:
    """개념 단위 원장(D52) — 한 행 = 개념 하나. 값은 0/1 이고 세 열의 합은 여전히
    보존된다(출처 3분할·연결 3분할이 각각 배타이므로 행마다 각 축에서 정확히 하나가
    1 이다). 제목은 한 번의 왕복으로 채운다 — 없으면 id 를 그대로 라벨로 쓴다."""
    ids = [cid for cid, b in by_id.items() if b.get("domain")]
    if not ids:
        return {"executed": True, "rows": [], "scope": scope, "population": 0,
                "by": "concept"}
    owns = store is None
    if owns:
        store = open_store(cfg or load_config())
    try:
        got = store.run(
            "MATCH (c:Concept) WHERE c.concept_id IN $ids AND c.owner_id = $owner "
            "RETURN c.concept_id AS id, c.title AS title",
            {"ids": ids, "owner": owner}) or []
        title = {r["id"]: (r.get("title") or r["id"]) for r in got}
    finally:
        if owns and store is not None:
            store.close()
    rows = []
    for cid in ids:
        b = by_id[cid]
        row = {"domain": cid, "label": title.get(cid, cid), "topic": b["domain"],
               "concepts": 1}
        for mark in _LEDGER_MARKS:
            row[mark] = 1 if _concept_in_mark(b, mark) else 0
        rows.append(row)
    rows.sort(key=lambda r: (str(r["label"]).lower(), str(r["domain"])))
    return {"executed": True, "rows": rows, "scope": scope,
            "population": len(rows), "by": "concept"}


def _ledger_classify(*, owner: str, domain: str | None, seed: str | None,
                     depth: int, cfg, store):
    """원장 개념별 분류(by_id) — :func:`ledger` 집계와 :func:`ledger_drilldown`
    드릴다운이 **공유**한다(둘의 수치 정합을 구조로 보장 — 보고서 정정 2 대응:
    드릴다운 항목 수 == 원장 셀 값). 반환 ``(by_id, scope, degraded)``:
    degraded 가 not None 이면 그대로 반환할 강등 응답(두-플랜 규칙). by_id 는
    concept_id → {domain, ts, episodic, record, degree, gateway}. 빈 dict = 빈 ego."""
    scope = "domain" if domain else "all"
    ids: list[str] | None = None
    if seed:
        ego = _ledger_ego_ids(seed, owner=owner, depth=depth, cfg=cfg, store=store)
        if not ego.get("executed"):
            return None, scope, ego
        scope, ids, domain = "seed", ego["ids"], None
        if not ids:
            return {}, "seed", None
    o = execute(ledger_origin_plan(owner=owner, domain=domain, ids=ids), cfg, store=store)
    if not o.get("executed"):
        return None, scope, o
    lk = execute(ledger_link_plan(owner=owner, domain=domain, ids=ids), cfg, store=store)
    if not lk.get("executed"):
        return None, scope, lk
    by_id: dict[str, dict] = {}
    for row in o.get("rows", []):
        nid = row.get("id")
        if not nid:
            continue
        b = by_id.setdefault(nid, {"domain": "", "ts": "", "episodic": False,
                                   "record": False, "degree": 0, "gateway": False})
        mark = row.get("mark")
        if mark == "all":                       # 권위 도메인·시간은 개념 자신에게서
            b["domain"] = row.get("domain") or ""
            b["ts"] = row.get("ts") or ""
        elif mark == "episodic":
            b["episodic"] = True
        elif mark == "record":
            b["record"] = True
    for row in lk.get("rows", []):
        b = by_id.get(row.get("id"))
        if b is None:      # 출처 축에 없는 id = 스코프 밖 — 세지 않는다
            continue
        b["degree"] += 1
        src, other = row.get("domain") or "", row.get("other") or ""
        if src and other and src != other:
            b["gateway"] = True
    return by_id, scope, None


# 드릴다운 mark → by_id 분류 판정(원장 집계와 같은 배타 규칙). 출처 3분할·연결 3분할.
_LEDGER_MARKS = ("episodic", "record", "unsourced", "connected", "gateway", "orphan")


def _concept_in_mark(b: dict, mark: str) -> bool:
    if mark == "episodic":
        return b["episodic"]
    if mark == "record":
        return b["record"] and not b["episodic"]        # 엄격 분할(episodic > record)
    if mark == "unsourced":
        return not b["episodic"] and not b["record"]
    if mark == "orphan":
        return b["degree"] == 0
    if mark == "gateway":
        return b["gateway"]
    if mark == "connected":
        return b["degree"] > 0 and not b["gateway"]
    return False


def ledger_drilldown(*, owner: str, domain: str, mark: str, seed: str | None = None,
                     depth: int = 2, cfg=None, store=None) -> dict:
    """합계(Totals) 생키 구간 클릭 → 그 구간에 기여한 **개념 목록**(D45-V2 3단계).

    보고서 정정 2: 원장 롤업 단위는 문서가 아니라 개념이다 — 드릴다운 1홉은 개념
    목록이고, 문서는 개념→node_context 로 한 홉 더 간다. 항목 수는 원장의 해당
    셀 값과 **항상 일치**한다(_ledger_classify 공유). 조회 전용.

    반환: {executed, domain, mark, items:[{concept_id, title, one_liner}], total}."""
    cfg = cfg or load_config()
    base: dict = {"kind": "ledger-drilldown", "executed": False,
                  "domain": domain, "mark": mark, "items": []}
    if mark not in _LEDGER_MARKS:
        base["error"] = f"unknown mark: {mark}"
        return base
    by_id, _scope, degraded = _ledger_classify(
        owner=owner, domain=domain if not seed else None, seed=seed, depth=depth,
        cfg=cfg, store=store)
    if degraded is not None:
        degraded.setdefault("kind", "ledger-drilldown")
        return degraded
    ids = [cid for cid, b in (by_id or {}).items()
           if b["domain"] == domain and _concept_in_mark(b, mark)]
    if not ids:
        base["executed"] = True
        base["total"] = 0
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    try:
        rows = store.run(
            "MATCH (c:Concept) WHERE c.concept_id IN $ids AND c.owner_id = $owner "
            "RETURN c.concept_id AS id, c.title AS title, c.one_liner AS ol",
            {"ids": ids, "owner": owner}) or []
        title = {r["id"]: {"concept_id": r["id"], "title": r.get("title") or r["id"],
                           "one_liner": r.get("ol") or ""} for r in rows}
        items = [title.get(cid, {"concept_id": cid, "title": cid, "one_liner": ""})
                 for cid in sorted(ids)]
        base.update(executed=True, items=items, total=len(items))
        return base
    finally:
        if owns and store is not None:
            store.close()


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


# ── 가중 최단 경로 (E-G8) — confidence 를 거리로 읽는 증거 체인 ────────────────
# E-G7(홉 최단)의 자매. 홉 수가 같아도 증거 강도는 다르다 — "가장 짧은 연결"과
# "가장 믿을 만한 연결"은 다른 질문이고, 여태 vinc_path 는 앞의 것만 답했다.
# E-G6 이 엣지에 실어 둔 confidence(0~1, `docstore.backfill_evidence` 가 채운다)를
# 거리로 환산해 다익스트라를 돌린다. 비용 = 1 - confidence:
#   · confidence 1.0(확실) → 비용 0    = 공짜로 지나가는 엣지
#   · confidence 0.0(미기입) → 비용 1  = 홉 하나와 정확히 같은 값
# 후자가 핵심이다 — **증거가 하나도 없는 그래프에서 가중 모드는 홉 최단으로 정확히
# 퇴화한다**(하위호환·설명가능). -log(c) 형(확률 곱 최대화)은 c=0 에서 발산하는데
# 이 스키마의 기본값이 0.0 이라 미기입 엣지가 통행 불가가 된다 — 그래서 쓰지 않는다.
#
# ⚠ 엔진 WSHORTEST 를 쓰지 않은 이유(ladybug 0.18.0 실측 2026-08-03 — 검토 후 기각):
#   ① 가중 인자로 **저장 컬럼만** 받는다. `WSHORTEST(1.0 - confidence)` 는 파서 거부
#      (`Invalid input ... expected rule oC_SingleQuery`). 비용열을 새로 저장하면
#      confidence 를 쓰는 지점마다(fragment 엣지 MERGE·evidence 백필) 동기 유지
#      의무가 생기고, 한 곳만 빠뜨리면 조용히 틀린 경로가 나온다(파생열 드리프트).
#   ② 패턴에 등장하는 **모든 rel 테이블**에 그 컬럼이 있어야 한다. 하나라도 없으면
#      `RuntimeError: invalid unordered_map<K, T> key` 로 크래시한다(실측) — 무라벨
#      순회가 불가해 라벨 열거가 강제되고, rel 테이블이 늘 때마다 질의가 깨진다.
#   ③ 파이썬은 조회 시점에 confidence 를 읽으므로 드리프트가 **원천적으로 불가**하고,
#      §4-2("Cypher 내 점수식 없음 — 방언 이식성")와 `ppr_scores` 전례에 정합한다.
#   재개 임계: 후보 서브그래프 인메모리 적재가 지배 비용이 되면(owner 개념 1만+)
#   저장 비용열 + WSHORTEST 를 재상정한다. 그때 ①의 동기 의무가 대가다.
#   [실측 확인분] 라벨을 열거하면 WSHORTEST 는 owner 술어·홉 상한과 정상 병용된다.

PATH_TIE_EPS = 1e-9      #: 부동소수 동률 판정 폭(비용 비교 전용)
PATH_LIMIT = 25          #: 반환 경로 상한 — `shortest_path_plan` 의 LIMIT 25 와 동수


def _edge_cost(edge: dict) -> float:
    """엣지 통행 비용 = 1 - confidence(범위 밖·비수치·미기입은 0.0 신뢰도로 강등).
    순수 함수 — :func:`weighted_paths` 의 유일한 비용 정의 지점."""
    try:
        c = float(edge.get("confidence") or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    return 1.0 - min(1.0, max(0.0, c))


def weighted_paths(edges: list[dict], a_id: str, b_id: str, *, max_hops: int = 6,
                   limit: int = PATH_LIMIT) -> list[dict]:
    """confidence 가중 최단 경로(들) — 순수·결정론(난수·시간 무의존).

    ``edges``: :func:`shape_graph` 엣지 리스트(각 ``{source,target,[confidence]}``).
    무방향으로 읽고(관계의 증거 강도는 방향과 무관), 같은 쌍에 엣지가 여럿이면
    **가장 싼 것**(=가장 신뢰도 높은 것)만 남긴다. 반환 ``[{ids, cost, length}]`` —
    최소 비용 동률 전부, ``(cost, length, ids)`` 결정론 정렬, ``limit`` 상한.

    홉 상한이 있으므로 (노드, 홉수) 층 위에서 다익스트라를 돈다 — 홉을 무시하고
    최적화하면 "더 싸지만 상한을 넘는" 경로를 답으로 골라 상한 계약을 어긴다.
    경로는 **단순 경로**(노드 무반복)로 한정한다."""
    import heapq

    adj: dict[str, dict[str, float]] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t:
            continue
        w = _edge_cost(e)
        for u, v in ((s, t), (t, s)):
            m = adj.setdefault(u, {})
            if v not in m or w < m[v]:
                m[v] = w
    if a_id not in adj or b_id not in adj:
        return []
    hops_cap = max(1, min(int(max_hops), 8))
    INF = float("inf")
    best: dict[tuple[str, int], float] = {(a_id, 0): 0.0}
    pq: list[tuple[float, int, str]] = [(0.0, 0, a_id)]
    while pq:
        d, h, u = heapq.heappop(pq)
        if d > best.get((u, h), INF) + PATH_TIE_EPS or h >= hops_cap:
            continue
        for v in sorted(adj.get(u, {})):
            nd, nh = d + adj[u][v], h + 1
            if nd + PATH_TIE_EPS < best.get((v, nh), INF):
                best[(v, nh)] = nd
                heapq.heappush(pq, (nd, nh, v))
    reached = [c for (n, _h), c in best.items() if n == b_id]
    if not reached:
        return []
    opt = min(reached)
    # 최적 비용과 동률인 경로를 tight-edge 전방 DFS 로 열거한다(결정론 — 이웃 정렬
    # 순서 고정). `best` 층이 각 접두의 최적성을 증명하므로 가지치기가 정확하다.
    out: list[dict] = []
    seen_paths: set[tuple[str, ...]] = set()

    def _walk(u: str, h: int, c: float, path: list[str], visited: set[str]) -> None:
        if len(out) >= limit:
            return
        if u == b_id and abs(c - opt) <= PATH_TIE_EPS:
            key = tuple(path)
            if key not in seen_paths:
                seen_paths.add(key)
                out.append({"ids": list(path), "cost": round(c, 6), "length": len(path) - 1})
            return          # 단순 경로 — b 도달 후 연장하지 않는다
        if h >= hops_cap:
            return
        for v in sorted(adj.get(u, {})):
            if v in visited:
                continue
            nd, nh = c + adj[u][v], h + 1
            if abs(best.get((v, nh), INF) - nd) > PATH_TIE_EPS or nd > opt + PATH_TIE_EPS:
                continue    # 이 접두가 (v,nh) 의 최적이 아니거나 이미 최적 비용 초과
            visited.add(v); path.append(v)
            _walk(v, nh, nd, path, visited)
            path.pop(); visited.discard(v)

    _walk(a_id, 0, 0.0, [a_id], {a_id})
    out.sort(key=lambda p: (p["cost"], p["length"], p["ids"]))
    return out[:limit]


def path_candidates_plan(seed: str, *, depth: int = 6,
                         owner: str | None = None) -> QueryPlan:
    """가중 경로 후보 집합 — seed 에서 ``depth`` 홉 이내 Concept id(E-G8).

    :func:`subgraph_ids_plan` 과 같은 SHORTEST 도달집합 순회지만 상한이 다르다
    (경로 상한은 8까지 열려 있고 ego 뷰의 4 클램프와 무관하다). ``a`` 에서 ``depth``
    홉 이내에 없는 노드는 길이 ``depth`` 이하 경로에 **구조적으로** 못 오르므로
    이 집합이 다익스트라의 완전한 탐색 공간이다."""
    depth = max(1, min(int(depth), 8))
    params: dict = {"seed": seed}
    n_where, m_where = "n.concept_id = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:Concept) WHERE {n_where} "
        f"MATCH (n)-[* SHORTEST 1..{depth}]-(m:Concept){m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("path_candidates", cypher, params,
                     note=f"weighted-path candidate set (depth={depth})")


#: `weight` 인자 허용값 — hops = E-G7 홉 최단(기본), confidence = E-G8 가중 최단.
WEIGHT_HOPS = "hops"
WEIGHT_CONFIDENCE = "confidence"


def _weighted_shortest_path(a_id: str, b_id: str, *, max_depth: int, owner: str | None,
                            cfg, store) -> dict:
    """E-G8 가중 갈래 — 후보 집합 → 엣지 투영 → 파이썬 다익스트라.

    엣지는 :func:`graph_rows_for_ids_plan`(이미 evidence/confidence 를 투영한다)로
    받고 **후보 집합 안으로 여과**한다(ego 경계 유출 제거 — graph() 와 같은 정책)."""
    base = {"kind": "shortest_path", "executed": False, "found": False,
            "a": a_id, "b": b_id, "length": None, "paths": [], "count": 0,
            "weight": WEIGHT_CONFIDENCE, "cost": None}
    ids_res = execute(path_candidates_plan(a_id, depth=max_depth, owner=owner),
                      cfg, store=store)
    if not ids_res.get("executed"):
        base["note"] = ids_res.get("note", "")
        return base
    ids = sorted({a_id, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
    base["executed"] = True
    if b_id not in ids:
        return base          # depth 안에서 닿지 않는다 — found=False(경로 부재와 동일 계약)
    rows_res = execute(graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
    if not rows_res.get("executed"):
        base["executed"] = False
        base["note"] = rows_res.get("note", "")
        return base
    shaped = shape_graph(rows_res.get("rows", []))
    keep = set(ids)
    edges = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    title_of = {n["id"]: n.get("label") or n["id"] for n in shaped["nodes"]}
    paths = weighted_paths(edges, a_id, b_id, max_hops=max_depth)
    if not paths:
        return base
    base.update(found=True, count=len(paths), cost=paths[0]["cost"],
                length=paths[0]["length"],
                paths=[{"ids": p["ids"], "titles": [title_of.get(i, i) for i in p["ids"]],
                        "length": p["length"], "cost": p["cost"]} for p in paths])
    return base


def shortest_path(a_id: str, b_id: str, *, max_depth: int = 6,
                  owner: str | None = None, weight: str = WEIGHT_HOPS,
                  cfg=None, store=None) -> dict:
    """E-G7/E-G8 — 두 개념 간 최단 경로 증거 체인(조회 전용). 반환 {executed, found,
    a, b, length, paths:[{ids,titles}], count, weight}. 경로 부재 시 found=False.

    ``weight``: ``"hops"``(기본, E-G7 — 엔진 ALL SHORTEST, 동률 전부) 또는
    ``"confidence"``(E-G8 — 파이썬 다익스트라, 비용 = 1-confidence). 가중 갈래는
    ``cost``(경로 비용 합)를 추가로 싣는다. 미지 값은 조용히 ``hops`` 로 강등하고
    응답의 ``weight`` 가 **실제로 적용한 축**을 에코한다(`_apply_basis` 와 같은 태도)."""
    if (weight or "").strip().lower() == WEIGHT_CONFIDENCE:
        return _weighted_shortest_path(a_id, b_id, max_depth=max_depth, owner=owner,
                                       cfg=cfg, store=store)
    res = execute(shortest_path_plan(a_id, b_id, max_depth=max_depth, owner=owner),
                  cfg, store=store)
    base = {"kind": "shortest_path", "executed": res.get("executed", False),
            "found": False, "a": a_id, "b": b_id, "length": None,
            "paths": [], "count": 0, "weight": WEIGHT_HOPS}
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


#: 관례 문서의 chunk_id 접두(`docstore.chunk_id_of` 규약: `{doc_id}#{index:05d}`).
_CONV_PREFIX = graphmd.CONVENTIONS_DOC_ID + "#"


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
    # 관례(GRAPH.md) 청크는 검색 결과에서 뺀다 — 사용자가 찾는 것은 자기 자료지
    # 그 자료를 어떻게 넣기로 했는지의 규약이 아니다. chunk_id 가 `{doc_id}#{n}` 이라
    # 접두만 보면 되고(추가 질의 0), **융합 전에** 걸러야 상위 k 를 관례가 잠식하지 않는다.
    for a in arms:
        a[:] = [r for r in a if not str(r.get("id") or "").startswith(_CONV_PREFIX)]
    rows = (rrf_fuse(arms, top_k=top_k) if len(arms) > 1 else arms[0][:top_k])
    for r in rows:
        r["arms"] = membership.get(r.get("id"), [])
    return rows


# ── 질문 정규화 (A1.10) — 자연어 질문을 노드 검색이 탈 수 있는 토큰으로 ──────────
# 배경(2026-08-03 실측): A1.9 의 **토큰 AND** 는 "이름을 댄" 질의만 통과시킨다.
# 자연어 질문에는 조사와 의문사가 섞이는데 그것들은 어느 노드 필드에도 없으므로
# AND 가 항상 0건이 된다 — "낙관적 동시성 제어가 뭐야?"가 `concept:optimistic-locking`
# 을 **자기 제목으로도** 못 찾았다(토큰 '제어가'·'뭐야?' 때문). 그 결과 node_hits 가
# 그래프 암으로만 채워져 `matched_in` 이 영원히 'graph' 였고, 묻기(ask)의 제목 확정·
# 되묻기 분기가 통째로 죽은 채 **알파벳순 첫 후보**가 답으로 나갔다.
#: 의문사·정중어 — 어느 노드에도 없으면서 AND 를 깨뜨리기만 하는 말들.
_STOP_TOKENS = frozenset("""
뭐 뭐야 뭔가 뭔데 무엇 무엇인가 뭡니까 어떻게 어떤 어찌 왜 언제 어디 어디서 누가 누구
얼마나 몇 며칠 인가 인가요 일까 일까요 건가 건가요 하나요 되나요 하죠 알려줘 알려주세요
설명해줘 설명해주세요 정리해줘 보여줘 궁금해 궁금합니다 해줘 해주세요 주세요 대해 대해서
관해 관해서 무슨
what what's whats which who whom whose when where why how is are was were do does did
the a an of about on in to for me tell explain show give please summarize
""".split())
#: 한국어 조사·접미 — 명사 뒤에 붙어 토큰을 다르게 만든다(긴 것부터 벗긴다).
_KO_SUFFIXES = ("이라는", "라는", "에서는", "에게서", "으로는", "에서", "에게", "한테", "부터",
                "까지", "보다", "처럼", "으로", "이란", "라면", "이는", "과는", "와는", "은",
                "는", "이", "가", "을", "를", "의", "에", "도", "만", "로", "와", "과", "란", "야")


def _strip_ko_suffix(tok: str) -> str:
    """토큰 끝의 조사를 한 번 벗긴다 — 남는 어간이 2자 이상일 때만.
    '제어가'→'제어', '환불은'→'환불'. '국가'처럼 어간이 1자로 줄 것은 손대지 않는다.

    형태소 분석이 아니라 접미 절단이라 '전문가'→'전문' 같은 **과잉 절단**이 난다.
    그래도 무해한 이유: 매칭이 `CONTAINS`(부분 문자열)라 짧아진 어간은 원래 낱말을
    여전히 포함 매치한다('전문'은 '전문가'의 부분 문자열). 즉 과잉 절단은 재현율을
    넓히지 저격 대상을 놓치지 않는다 — 반대로 **절단 부족**이 이 함수가 생긴 이유다
    (조사 하나 때문에 토큰 AND 가 통째로 0건이 됐다). 사전 없는 결정론을 유지한다."""
    for suf in _KO_SUFFIXES:
        if len(tok) > len(suf) + 1 and tok.endswith(suf):
            return tok[: -len(suf)]
    return tok


def normalize_query_tokens(query: str, *, cap: int = 8) -> list[str]:
    """질문 → 검색 토큰(순수·결정론). 구두점 제거 → 소문자 → 조사 절단 → 불용어 제거.

    **불용어를 걷어낸 뒤 아무것도 안 남으면 걷어내기 전으로 되돌린다** — "뭐야" 한
    마디만 물었을 때 질의가 통째로 사라져 전 노드를 삼키는 것을 막는다(빈 토큰은
    호출부가 조기 반환한다). 조사 절단본과 원형을 **둘 다** 남기지는 않는다:
    중복 토큰은 매치 수 집계를 왜곡한다."""
    raw = [t for t in re.split(r"[\s,./?!;:()\[\]{}\"'`~]+", (query or "").lower()) if t]
    stemmed = [_strip_ko_suffix(t) for t in raw]
    kept = [t for t in stemmed if t and t not in _STOP_TOKENS]
    if not kept:                      # 전부 불용어였다 — 원형으로 되돌린다
        kept = [t for t in stemmed if t]
    out: list[str] = []
    for t in kept:                    # 순서 유지 dedup
        if t not in out:
            out.append(t)
    return out[:cap]


#: 매치 필드 강도 — 같은 토큰 수를 맞췄으면 제목이 이긴다.
_FIELD_RANK = {"title": 0, "one_liner": 1, "props": 2, "id": 3}
#: id 매치에서 무시할 토큰 — 노드 종류 접두사(`concept:` 등)는 모든 id 에 들어 있어
#: 그대로 세면 질의 하나가 전 노드를 같은 점수로 맞춘다. 접두사는 신호가 아니다.
_ID_PREFIX_TOKENS = frozenset(("concept", "episode", "record", "bridge", "entity"))
#: 부분 매치 스캔이 처리할 최대 행. 넘으면 잘린 사실을 호출부가 로그로 남긴다.
NODE_SCAN_CAP = 2000


def _node_fallback_search(store, query: str, *, limit: int = 20,
                          owner: str | None = None) -> list[dict]:
    """Concept·Record 타이틀/요약/props 폴백 검색(T3b C1 **핵심**) — FTS·벡터 결과와
    **무관하게 항상** 수행한다. Concept/Record 는 Chunk 인덱스에 안 들어가(제목·요약을
    가진 노드가 여태 검색 자체가 불가) Chunk 0개 스토어에선 검색이 통째로 비었다 —
    실사용 사고의 직접 원인. 이 폴백이 그 갈래를 해소한다.

    **A1.10 부분 매치 + 점수 랭킹**(자연어 질문 0건 해소, 2026-08-03). 구 A1.9 는
    **토큰 AND** 였다 — 모든 토큰이 어느 필드엔가 있어야 매치. 이름을 그대로 댄 질의만
    통과하고, 조사·의문사가 섞인 자연어 질문("낙관적 동시성 제어가 뭐야?")은 '제어가'·
    '뭐야' 때문에 **자기 제목조차 못 찾아** 항상 0건이었다. 이제:

      ① :func:`normalize_query_tokens` 로 구두점·조사·의문사를 걷어내고,
      ② **토큰 OR** 로 후보를 모은 뒤,
      ③ **파이썬에서 점수를 매긴다**(§4-2 — Cypher 안에 점수식을 두지 않는다):
         맞은 토큰 수(주) → 제목에서 맞은 토큰 수 → 필드 강도 → id.

    반환 행: {id, title, kind('concept'|'record'), domain, matched_in('title'|
    'one_liner'|'props'), hit_tokens, title_tokens, token_total} — 뒤 세 개가 신설이다.
    호출부(묻기 등)가 "얼마나 확실한 매치인가"를 **임의 임계값 없이** 판정하려면 맞은
    토큰 수가 필요하다. 구 소비처는 새 키를 무시하면 그만이다(가산 변경).

    ⚠ OR 로 넓힌 대신 랭킹이 좁힌다. UNION ALL 은 LIMIT 를 무시하므로(실측) 상한은
    파이썬 슬라이스이고, 스캔 폭주는 :data:`NODE_SCAN_CAP` 이 자른다.
    빈 쿼리·토큰 없음은 모든 노드를 삼키므로 스킵([])."""
    toks = normalize_query_tokens(query)
    if not toks:
        return []
    params: dict = {f"q{i}": t for i, t in enumerate(toks)}
    idx = range(len(toks))
    # SEC-1: 소유자 스코프. 없으면(로컬 단일 사용자) 기존 전역 검색.
    own_c = own_r = ""
    if owner:
        own_c, own_r = " AND c.owner_id = $owner", " AND r.owner_id = $owner"
        params["owner"] = owner

    def _any(fields: tuple[str, ...]) -> str:   # 토큰 하나라도, 필드 하나라도
        return "(" + " OR ".join(
            f"lower(coalesce({f},'')) CONTAINS $q{i}" for i in idx for f in fields) + ")"

    # id(슬러그)도 검색한다: GRAPH.md 관례가 `concept:optimistic-locking` 처럼 **뜻이
    # 담긴 영어 슬러그**를 강제하므로, 제목이 한국어인 개념을 영어 용어로 찾는 유일한
    # 경로다(실측: "optimistic locking" 이 자기 개념을 못 찾고 0건이었다).
    c_fields = ("c.title", "c.one_liner", "c.props", "c.concept_id")
    r_fields = ("r.title", "r.props", "r.record_id")
    cypher = (
        f"MATCH (c:Concept) WHERE {_any(c_fields)}{own_c} "
        "RETURN c.concept_id AS id, c.title AS title, 'concept' AS kind, "
        "c.domain AS domain, c.one_liner AS one_liner, c.props AS props_raw "
        "UNION ALL "
        f"MATCH (r:Record) WHERE {_any(r_fields)}{own_r} "
        "RETURN r.record_id AS id, r.title AS title, 'record' AS kind, "
        "r.domain AS domain, '' AS one_liner, r.props AS props_raw"
    )
    rows = store.run(cypher, params)[:NODE_SCAN_CAP]
    scored: list[dict] = []
    for r in rows:
        title = str(r.get("title") or "").lower()
        one = str(r.get("one_liner") or "").lower()
        props = str(r.get("props_raw") or "").lower()
        nid = str(r.get("id") or "").lower()
        n_title = sum(1 for t in toks if t in title)
        n_one = sum(1 for t in toks if t in one)
        n_props = sum(1 for t in toks if t in props)
        n_id = sum(1 for t in toks if t not in _ID_PREFIX_TOKENS and t in nid)
        hit = sum(1 for t in toks
                  if t in title or t in one or t in props
                  or (t not in _ID_PREFIX_TOKENS and t in nid))
        if not hit:
            continue
        where = ("title" if n_title else "one_liner" if n_one
                 else "props" if n_props else "id")
        scored.append({"id": r.get("id"), "title": r.get("title"), "kind": r.get("kind"),
                       "domain": r.get("domain") or "", "matched_in": where,
                       "hit_tokens": hit, "title_tokens": n_title,
                       "token_total": len(toks)})
    # 결정론 랭킹: 맞은 토큰 수 ↓ → 제목 토큰 수 ↓ → 필드 강도 ↑ → id ↑.
    scored.sort(key=lambda r: (-r["hit_tokens"], -r["title_tokens"],
                               _FIELD_RANK.get(r["matched_in"], 9), str(r["id"])))
    return scored[:max(1, int(limit))]


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
    # `hit_tokens`/`title_tokens` 를 0 으로 명시한다(A1.10): 그래프 암은 **질문 토큰이
    # 아니라 청크 인접으로** 올라온 후보다. 소비처가 두 암을 한 줄로 정렬할 때 키가
    # 없어 `undefined` 로 비교되면 조용히 뒤섞인다 — 0 은 "토큰으로는 안 맞았다"는
    # 명시적 진술이고, 그래서 토큰 매치가 하나라도 있는 후보가 항상 앞선다.
    out = [{"id": x["id"], "title": x.get("title") or x["id"], "kind": "concept",
            "domain": x.get("domain") or "", "matched_in": "graph",
            "hit_tokens": 0, "title_tokens": 0}
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
           cursor: str | None = None, detail: str = "summary",
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
    ``store`` 주입(사이드카 §11 P2): read 뷰를 넘기면 재사용(열지도 닫지도 않음).

    **커서 페이지네이션(GM-14)**: ``rows`` 는 안정 정렬(점수 내림차순 + id 오름차순)
    후보 풀(상한 :data:`SEARCH_POOL_CAP`)의 ``top_k`` 페이지다. ``total_count`` ·
    ``has_more`` · ``next_cursor`` 가 함께 실리고, 무효/실효 커서는 조용한 폴백 없이
    최상위 ``error``(invalid_cursor/stale_cursor) 로 답한다. 기본 ``detail="summary"``
    는 청크 전문 대신 발췌(snippet)를 싣는다 — 전문은 vinc_doc_get(doc_id)."""
    cfg = cfg or load_config()
    detail = "full" if (detail or "").strip().lower() == "full" else "summary"
    # 어느 팔이 돌았는지를 **결과에 남긴다**. 임베딩이 오래 write-only 였는데(호출부가
    # query_embedding 을 안 넘겨 vector_search 가 한 번도 실행되지 않았다) 에러가 없어
    # 아무도 몰랐다. 이 한 줄이 그 침묵을 깬다 — CLI·HTTP 양쪽이 같은 값을 본다
    # (HTTP 계층에서만 붙이면 §11 게이트① 의 CLI↔HTTP 동등성이 깨진다).
    base = {"kind": "search", "executed": False, "rows": [], "node_hits": [],
            "arms": "fts+vector" if query_embedding is not None else "fts",
            "total_count": 0, "has_more": False, "next_cursor": None}
    # 커서 검증은 스토어를 열기 전에 — 무효 커서에 조회 비용을 쓰지 않는다.
    qh = _query_fingerprint(query, owner, filters, detail)
    off, want_eid = 0, None
    if cursor:
        try:
            c = cursor_decode(cursor)
            if c.get("qh") != qh:
                raise ValueError(
                    "invalid_cursor: cursor was issued for a different query/scope")
            off = max(0, int(c.get("off") or 0))
            want_eid = c.get("eid")
        except (TypeError, ValueError) as exc:
            base["note"] = base["error"] = str(exc)
            return base
    owns = store is None
    if owns:
        store = open_store(cfg)
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
        # GM-14: top_k 가 아니라 **후보 풀**(SEARCH_POOL_CAP)을 가져온다 — 페이지는
        # 아래에서 안정 정렬 위의 오프셋으로 자른다.
        pool: list[dict] = []
        try:
            pool = _chunk_arms(store, query, query_embedding, SEARCH_POOL_CAP, filters)
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
                        pool = _chunk_arms(store, query, query_embedding, SEARCH_POOL_CAP, filters)
                        base["executed"] = True
                        base["note"] = "search index built on demand"
                    except Exception as exc2:
                        base["error"] = (f"index_build_failed: "
                                         f"{type(exc2).__name__}: {exc2}")
                        base["note"] = base["error"]

        # GM-14 페이지네이션 — 안정 정렬(점수 내림차순, 동점은 id 오름차순). 페이지
        # 사이에 결과가 흔들리면 안 되므로 정렬 키에 id 를 반드시 묶는다.
        pool.sort(key=lambda r: (-(float(r.get("rrf_score") or r.get("score") or 0.0)),
                                 str(r.get("id") or "")))
        base["total_count"] = len(pool)
        if cursor and (off >= len(pool)
                       or (want_eid is not None
                           and str(pool[off].get("id") or "") != str(want_eid))):
            # 결과가 커서 발급 시점과 달라졌다 — 조용히 어긋난 페이지를 주지 않는다.
            base["note"] = base["error"] = (
                "stale_cursor: results changed since this cursor was issued; "
                "restart from the first page")
            base["executed"] = False
            return base
        page = pool[off:off + max(1, int(top_k))]
        next_off = off + len(page)
        base["has_more"] = next_off < len(pool)
        if base["has_more"]:
            base["next_cursor"] = cursor_encode(
                {"v": 1, "off": next_off, "qh": qh,
                 "eid": str(pool[next_off].get("id") or ""), "tc": len(pool)})
        base["rows"] = page if detail == "full" else [_summary_row(r) for r in page]
        # MCP 봉투의 크기 안전망(shrinker)이 이어받을 좌표 — 꼬리 행을 덜어낸 뒤
        # 같은 지문으로 next_cursor 를 다시 쓸 수 있게 한다.
        base["page"] = {"offset": off, "limit": max(1, int(top_k)), "fingerprint": qh}

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

# owner_id 열 보유 = 스코프 가드로 안전하게 삭제할 수 있는 라벨. _SEED_TABLES 의
# 5종과 정확히 같다(전부 owner_id + _TRACKED_LABELS = 동기화 전파 대상).
# ⚠ 여기 없으면 delete 가 "refused"로 거부된다 — Bridge(v2)·Entity(F1)가 소유
# 스탬프를 얻은 뒤에도 이 집합이 3종에 머물러 있어 앱에서 브릿지를 못 지웠다(S1
# 실측). 확장 시 _NODE_PK(툼스톤)도 같이 넓혀야 KeyError 가 안 난다.
_OWNER_SCOPED_LABELS = {"Concept", "Episode", "Record", "Bridge", "Entity"}
_NODE_PK = {"Concept": "concept_id", "Episode": "episode_id", "Record": "record_id",
            "Bridge": "bridge_id", "Entity": "entity_id"}


def rename_domain(*, owner: str, old: str, new: str, store=None, cfg=None) -> dict:
    """도메인 일괄 변경/재배치(S2) — **owner 스코프 문자열 편집**.

    ⚠ 전역 `:Domain` 노드는 **건드리지 않는다**. Domain 은 `{name}` 뿐인 무소유
    구조 노드라 모든 사용자가 공유한다 — 지우거나 이름을 바꾸면 남의 그래프가
    깨진다. 실제 도메인 귀속은 Concept/Episode/Record 의 `domain` **문자열 필드**
    이고, 그게 `/domains` 집계의 출처다. 그래서 여기서는 내 노드의 그 필드만 바꾼다.

    - ``new`` 가 빈 문자열이면 **미분류로 내림**(= 사용자 체감의 "도메인 삭제":
      도메인이 목록에서 사라지고 노드는 남는다. 노드까지 지우려면 delete_node).
    - ``owner`` 필수 — 없으면 전 사용자 문서를 갈아버리므로 fail-closed 로 거부.
    - 변경된 노드마다 revstamp(updated_at/rev/sync_seq)를 찍어 다기기 동기화로
      전파한다(pin/tag 와 동형 — 안 찍으면 다른 기기가 옛 도메인을 유지한다).
    """
    base: dict = {"kind": "domain.rename", "executed": False,
                  "old": old, "new": new, "moved": 0}
    if not owner:
        base["error"] = "owner required — 무스코프 도메인 변경은 전 사용자에 영향(SEC-1)"
        return base
    if not old:
        base["error"] = "old domain required"
        return base
    if old == new:
        base.update(executed=True, note="no-op (same domain)")
        return base
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["error"] = "no reachable store"
        return base
    from . import revstamp
    try:
        moved = 0
        per_label: dict[str, int] = {}
        for label, pk in (("Concept", "concept_id"), ("Episode", "episode_id"),
                          ("Record", "record_id")):
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $o AND n.domain = $old "
                f"RETURN n.{pk} AS id, n.rev AS rev", {"o": owner, "old": old}) or []
            for r in rows:
                st = revstamp.make_stamp(store, {"rev": r.get("rev"), "owner": owner})
                store.run(
                    f"MATCH (n:{label}) WHERE n.{pk} = $id AND n.owner_id = $o "
                    "SET n.domain = $new, n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                    {"id": r["id"], "o": owner, "new": new, "ua": st["updated_at"],
                     "rev": st["rev"], "seq": st["sync_seq"]})
                moved += 1
            if rows:
                per_label[label] = len(rows)
        base.update(executed=True, moved=moved, by_label=per_label)
        if moved == 0:
            base["note"] = f"no nodes in domain {old!r} for this owner"
        return base
    finally:
        if owns:
            store.close()


def _write_tombstone(store, node_id: str, label: str, owner: str | None) -> None:
    """삭제 표식(S4) — 삭제 전파용. 하드삭제(DETACH DELETE)만으론 수신측이 삭제를
    영영 모른다. 삭제 직전 노드의 rev/updated_at 을 보존하고 새 deleted_at(HLC)·
    sync_seq 를 찍어 `_Tombstone` 에 남긴다. owner 스코프 라벨(=_TRACKED_LABELS 5종)
    만 — 그 외는 동기화 대상이 아니라 전파할 곳이 없다. 지연 import(순환 회피)."""
    if label not in _OWNER_SCOPED_LABELS:
        return
    from . import revstamp
    pk = _NODE_PK[label]
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
    """owner 스코프 삭제 대상 라벨 — 스키마 SSoT + 소유 메타. 지연 임포트(백엔드
    중립 유지). owner_scoped_labels 는 owner_id 열 보유 라벨만 돌려주므로 여기 전부
    ``n.owner_id = $o`` 필터가 성립한다(v4 부터 Chunk 포함 — D45 Chunk=SSoT).
    `_Tombstone`·`_DocMeta`(메타)도 owner_id 를 가져 포함 — 정본 텍스트(Chunk)가
    지워질 때 문서 정체성 레지스트리도 함께 지운다."""
    from store.embedded_schema import owner_scoped_labels
    return owner_scoped_labels() + ["_Tombstone", "_DocMeta", "_DocTombstone"]


def purge_owner(owner: str, *, cfg=None, store=None) -> dict:
    """``owner`` 소유의 **전 노드**를 부착 엣지와 함께 비가역 DETACH DELETE.

    계정 삭제 캐스케이드 전용(§5.9). 소유 스코프이므로 SEC-1/SEC-2 의 owner 격리가
    전제 — 소유가 안 갈리면 남의 노드가 지워질 수 있다. 라벨별 삭제 건수를 돌려줘
    호출자(계정)가 감사할 수 있게 한다. **owner 가 비었으면 거부**(빈 술어로 전삭제
    사고 방지). Domain 은 owner_id 가 없어 소유로 못 가르는 잔여 —
    [[vinc-owner-isolation-sec1]] 의 알려진 한계(Bridge 는 v2, Chunk 는 v4 에서
    소유 스탬프를 얻어 편입 — '' 소유 레거시 행만 잔여)."""
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
