# -*- coding: utf-8 -*-
"""Query planners + executors for recall / search / decide.

A QueryPlan is a (cypher, params) pair plus metadata. Plans are pure and
testable. ``execute`` runs a plan against Neo4j (driver imported lazily); when
no driver/password is available it returns the plan with ``executed=False`` so
callers — and tests — get a deterministic result without a live database.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import json
import math
import os
import re

from store.config import StoreConfig, load_config
from store.backend import open_store
from store import pkns
from store.envflag import truthy
from engines import domain_hierarchy

from . import graphmd
from vinc_rag import retrieval as _retrieval

# 바닥 보조 함수는 store 로, 검색 코어는 vinc_rag 로 옮겼다(VIN-243). 같은 이름을 여기서
# 다시 내보내므로 호출부와 `q.X` 는 그대로다. 근거: [[vinc.plan.team-split]]
from store.querybase import (  # noqa: E402,F401  (재수출)
    QueryPlan,
    _KO_SUFFIXES,
    _MON,
    _MONTHS,
    _Q_TIME,
    _SCOPE_VALUE,
    _STOP_TOKENS,
    _VALIDITY_EMPTY,
    _classify_chunk_error,
    _pad_date,
    _props_to_obj,
    _strip_ko_suffix,
    _today,
    _validity_cols,
    domain_counts_plan,
    domain_subtree_where,
    domains,
    execute,
    holds_during,
    normalize_query_tokens,
    query_time,
    resolve_as_of,
    time_scope,
    validity_state,
)
from vinc_rag.retrieval import (  # noqa: E402,F401  (재수출)
    CHUNK_RRF_K,
    NODE_SCAN_CAP,
    PREF_WEIGHTS,
    RECALL_ORDER,
    SEARCH_POOL_CAP,
    UNSTAMPED_RUNTIME_NOTE,
    _ARM_LABEL,
    _FIELD_RANK,
    _FTS_REMNANT,
    _ID_PREFIX_TOKENS,
    _RUNTIME_STAMP_OWNERS,
    _SEM_GAP,
    _UnscopedChunkSearch,
    _VECTOR_REBUILDING,
    _VECTOR_REMNANT,
    _VECTOR_WIDTH_MISMATCH,
    _attach_doc_titles,
    _chunk_arms,
    _chunk_count,
    _domains_for_ids,
    _encoder_runtime_warning,
    _fuse_node_hits,
    _graph_arm_search,
    _id_token,
    _node_fallback_search,
    _node_sem_rank_on,
    _node_semantic_arm_on,
    _query_fingerprint,
    _recall_fingerprint,
    _recall_refusal,
    _row_validity,
    _schedule_vector_index,
    _stored_runtimes,
    _summary_row,
    _validity_for_ids,
    _visible,
    cursor_decode,
    cursor_encode,
    recall,
    recall_plan,
    rrf_fuse,
    score_preference,
)


def _drop_unstarted_episodes(result: dict, query: str, *, as_of, mode, historical) -> None:
    """as_of 기간 뒤에 일어난 사건을 node_hits 에서 뺀다(감사 F14).

    사건의 날짜는 그 시작이고 끝나지 않는다 — `api.temporal_read` 가 neighbors·graph·
    recall 을 그렇게 거른다. 검색 코어는 사건을 구간 없음으로 읽어 늘 남겼고, 그래서 같은
    as_of 요청에서 검색만 그 사건을 냈다. 날짜는 검색이 이미 단 시간 메타(content_time)에
    있어 스토어를 다시 읽지 않는다. 날짜가 없으면 감추지 않는다.
    감춘 행은 다른 구간 필터와 같은 자리에 센다(validity_filtered·validity_best_dropped)
    — brief 의 동점 가드가 같은 규칙으로 본다."""
    if mode == "current" or not (as_of or "").strip() or str(result.get("error") or "").startswith(
            ("invalid_", "stale_cursor")):
        return
    try:
        from store.search_temporal import mode_scope
        _, scope, info = mode_scope(mode, historical, as_of, query)
    except ValueError:
        return
    if scope is None:
        return
    kept, best = [], tuple(result.get("validity_best_dropped") or (0, 0))
    hidden = 0
    for hit in result.get("node_hits") or []:
        date = str((hit.get("temporal") or {}).get("content_time") or "")
        if (hit.get("kind") == "episode" or str(hit.get("id") or "").startswith("episode:")) \
                and date and not holds_during(date, "", scope):
            hidden += 1
            score = (int(hit.get("hit_tokens") or 0), int(hit.get("title_tokens") or 0))
            best = max(best, score)
            continue
        kept.append(hit)
    if not hidden:
        return
    result["node_hits"] = kept
    result["validity_filtered"] = int(result.get("validity_filtered") or 0) + hidden
    result["validity_best_dropped"] = list(best)
    result.setdefault("validity_note",
                      f"hid nodes whose claim did not hold during {info['as_of']}; "
                      "drop as_of for today's view")
    if isinstance(result.get("node_window"), dict):
        result["node_window"]["returned"] = len(kept)


def _doc_cite_ids(store, owner: str | None, doc_ids) -> dict[str, str]:
    """문서 키와 인용할 노드 id는 다르다. 읽을 수 있는 미러만 인용한다."""
    ids = {f"concept:doc.{doc_id}" for doc_id in doc_ids if doc_id}
    if not ids or store is None:
        return {}
    where = "c.concept_id IN $ids"
    params = {"ids": pkns.keys(owner, sorted(ids))}
    if owner:
        where += " AND c.owner_id = $owner"
        params["owner"] = owner
    rows = store.run(f"MATCH (c:Concept) WHERE {where} "
                     "RETURN c.concept_id AS id", params) or []
    return {nid[len("concept:doc."):]: nid for row in rows
            if (nid := pkns.strip(str(row.get("id") or ""))) in ids}


def _cite_doc_rows(rows: list[dict], cites: dict[str, str]) -> list[dict]:
    # 행은 보존한다. 미러가 없을 때 doc 키를 노드 id로 꾸미지 않는다.
    return [{**row, "cite_id": cites.get(str(row.get("doc_id") or "")),
             **({"cite_unavailable": "document mirror missing or unreadable"}
                if str(row.get("doc_id") or "") not in cites else {})}
            for row in rows]


def search(query: str, *, query_embedding: list[float] | None = None,
           top_k: int = 5, filters: dict | None = None, owner: str | None = None,
           cursor: str | None = None, detail: str = "summary",
           historical: bool = False, as_of: str | None = None,
           mode: str | None = None, known_at: str | None = None,
           query_runtime: str | None = None, cfg=None, store=None) -> dict:
    """검색 순위와 페이지는 그대로 두고 공개 응답에 정확한 인용 id를 붙인다.

    as_of 기간이 있으면 그 기간이 끝난 뒤에 시작한 사건(Episode)을 node_hits 에서 뺀다
    (:func:`_drop_unstarted_episodes`). 다른 종류의 구간 판정은 검색 코어가 이미 한다."""
    result = _retrieval.search(
        query, query_embedding=query_embedding, top_k=top_k, filters=filters,
        owner=owner, cursor=cursor, detail=detail, historical=historical,
        as_of=as_of, mode=mode, known_at=known_at, query_runtime=query_runtime, cfg=cfg, store=store)
    result["node_hits"] = [{**hit, "cite_id": hit.get("id") or None}
                           for hit in result.get("node_hits") or []]
    _drop_unstarted_episodes(result, query, as_of=as_of, mode=mode, historical=historical)
    rows = result.get("rows") or []
    if not rows:
        return result
    owns = store is None
    try:
        if owns:
            store = open_store(cfg or load_config())
        cites = _doc_cite_ids(store, owner, [row.get("doc_id") for row in rows])
        result["rows"] = _cite_doc_rows(rows, cites)
    except Exception as exc:
        result["rows"] = _cite_doc_rows(rows, {})
        result["citation_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns and store is not None:
            try:
                store.close()
            except Exception as exc:
                result.setdefault("citation_error", f"{type(exc).__name__}: {exc}")
    return result


# ── Plan builders (pure) ─────────────────────────────────────────────────────

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

def decide_plan(topic: str, *, depth: int = 2, owner: str | None = None) -> QueryPlan:
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
    # PK 네임스페이싱 — `$topic` 은 **id 열과 title 열에 동시에** 쓰인다. 하나로
    # 합성하면 제목 매치가 죽는다(제목은 논리 텍스트다). 그래서 id 열용 물리 키를
    # `$tkey` 로 따로 싣는다. 근거: [[vinc.plan.node-pk-namespacing]] §7
    params = {"topic": topic, "tkey": pkns.key(owner, topic)}
    cypher = (
        "MATCH (n) WHERE n.concept_id = $tkey OR n.bridge_id = $tkey "
        "OR n.title = $topic "
        f"MATCH path = (n)-[* TRAIL 1..{depth}]-(m) "
        "RETURN n, relationships(path) AS rels, m "
        "LIMIT 100"
    )
    return QueryPlan("decide", cypher, params, note="policy/concept relation traversal")


# ── Executor ─────────────────────────────────────────────────────────────────
# 드라이버 취득은 store.backend.open_store 로 일원화(설계 §4-1). 여기서는 백엔드를
# 열고 plan.cypher 를 store.run 으로 실행할 뿐 — 드라이버를 직접 열지 않는다.

# ── Public entry points ──────────────────────────────────────────────────────

def domains_plan(*, owner: str | None = None) -> QueryPlan:
    """Discover the learning domains present in the store — enumerated from the
    data, never a fixed list. Domains are whatever the learner chose to study; you
    find out *what* someone has learned by asking the graph, not a config enum.
    Optionally scoped to one ``owner``. Returns ``{domain, episodes}`` rows.

    Episode 전용 — View L0(:func:`view_l0`)가 Concept 축과 별도로 이 플랜을 그대로
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


def domain_names_plan(*, owner: str | None = None) -> QueryPlan:
    """소유자의 **주제 이름 집합**(개수·집계 없음).

    :func:`domain_counts_plan` 과 같은 3개 라벨을 보되 카운트를 세지 않는다 —
    무료 등급 주제 상한이 **매 적재마다** 도는 경로라 집계·정렬이 순수 낭비다.

    라벨 집합을 `domains()` 와 **일부러 일치**시켰다(Episode·Concept·Record·Decision).
    상한이 다른 집합을 세면 사용자가 `vinc_domains` 로 4개를 보면서 "5개 상한에
    걸렸다"는 말을 듣게 된다 — 그 불일치가 곧 지원 문의가 된다."""
    if owner:
        where_e = "WHERE e.owner_id = $owner AND e.domain IS NOT NULL"
        where_c = "WHERE c.owner_id = $owner AND c.domain IS NOT NULL"
        where_r = "WHERE r.owner_id = $owner AND r.domain IS NOT NULL"
        where_d = "WHERE d.owner_id = $owner AND d.domain IS NOT NULL"
        params: dict = {"owner": owner}
    else:
        where_e = "WHERE e.domain IS NOT NULL"
        where_c = "WHERE c.domain IS NOT NULL"
        where_r = "WHERE r.domain IS NOT NULL"
        where_d = "WHERE d.domain IS NOT NULL"
        params = {}
    cypher = (
        f"MATCH (e:Episode) {where_e} RETURN DISTINCT e.domain AS domain "
        "UNION ALL "
        f"MATCH (c:Concept) {where_c} RETURN DISTINCT c.domain AS domain "
        "UNION ALL "
        f"MATCH (r:Record) {where_r} RETURN DISTINCT r.domain AS domain "
        "UNION ALL "
        f"MATCH (d:Decision) {where_d} RETURN DISTINCT d.domain AS domain"
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


#: `domain`(단수, 하위호환)과 `domains`(복수)를 하나의 목록으로 접는다. 빈 문자열과
#: 중복은 떨어뜨리되 **순서는 유지**한다 — 응답이 스코프를 에코할 때 흔들리지 않게.
def normalize_domains(domain: str | None = None,
                      domains: list[str] | None = None) -> list[str]:
    raw = list(domains or ([] if domain is None else [domain]))
    out: list[str] = []
    for d in raw:
        d = (d or "").strip()
        if d and d not in out:
            out.append(d)
    return out


#: 추적열 투영 한 자리(W3) — 읽기 표면이 `rev`·`updated_at` 을 함께 낸다. 쓰기가
#: 버전을 손에 쥐려면 읽기가 먼저 그것을 줘야 하는데, 여태 어느 읽기 경로도 주지
#: 않았다. **투영만이고 강제는 아니다**(expected_rev 는 첫 실측 충돌이 방아쇠).
#: 근거: [[vinc.plan.mcp-agent-surface §6]] · 세 열의 의미는 [[vinc.spec.dataplane §6.1]].
#:
#: ⚠ 이 두 열은 `store/embedded_schema.py` 의 `_TRACK_COLS` 가 붙는 라벨에만 있다
#: (Concept·Episode·Record·Bridge·Entity·Decision). **Domain 에는 없다** — 도메인
#: 좌표 투영에 옮겨 붙이면 Binder 가 죽고 그 조회 전체가 함께 죽는다(이 파일이
#: pinned·content_time·one_liner 에서 이미 세 번 겪은 자리).
#:
#: 열 목록을 여기 한 벌만 두는 이유: 두 벌이 되는 순간 "저장이 안 됐다"와 "투영이
#: 빠뜨렸다"가 구분되지 않는다(`_KEPT_FIELDS` 전례).
def _track_cols(alias: str, prefix: str = "") -> str:
    return (f"coalesce({alias}.rev, 0) AS {prefix}rev, "
            f"coalesce({alias}.updated_at, '') AS {prefix}updated_at")


def _stamp_validity(base: dict, valid_from, valid_until) -> None:
    """노드 **자신**의 유효구간과 파생 상태를 상세에 싣는다. 부착 목록(links.items)만
    구간을 갖고 노드가 안 가지면, id 로 직접 연 historical 기록이 현재형 사실로
    읽힌다(라이브 재현: `record:fact-engines-orphans`). `superseded_by` 는 props 의
    임시 인코딩(§8 항목 4)이라 같은 자리에서 꺼내 준다 — 후속으로 건너갈 유일한 길."""
    base["valid_from"] = str(valid_from or "")
    base["valid_until"] = str(valid_until or "")
    base["validity"] = validity_state(base["valid_from"], base["valid_until"])
    props = base.get("props") if isinstance(base.get("props"), dict) else {}
    base["superseded_by"] = str(props.get("superseded_by") or "")


def graph_plan(*, owner: str, domain: str | None = None,
               domains: list[str] | None = None) -> QueryPlan:
    """The learning knowledge-graph for an owner: Concept nodes + their relations
    (a row per concept, plus a row per outgoing Concept→Concept edge). ``domain``
    (free/open) optionally narrows to one domain. Shaped into nodes/edges by
    :func:`shape_graph`."""
    where = ["c.owner_id = $owner"]
    params: dict = {"owner": owner}
    doms = normalize_domains(domain, domains)
    if doms:
        where.append(domain_subtree_where(["c.domain"], doms, params))
    clause = "WHERE " + " AND ".join(where)
    # 관계 타입은 `label(r)` — 임베디드에 `type()` 이 없다(Neo4j 도 label 을 받아
    # 백엔드 중립). ⚠ evidence/confidence 투영은 공통 CC 읽기 계약 안에서만 된다 —
    # CONNECTS/ATTACHES_TO/TYPED 가 후보인 축에 붙이면 Binder 오류다.
    # ⚠ owner 는 **먼 끝점에도** 건다 — 시작점만 스코프하면 남의 증거 문장이
    # 클라이언트까지 간다: [[record:an-unlabelled-hop-binds-only-tables-that-share-the-projected-columns]].
    cypher = (
        f"MATCH (c:Concept) {clause} "
        f"OPTIONAL MATCH (c)-[r:{_CONCEPT_READ_PATTERN}]->(d:Concept) WHERE d.owner_id = $owner "
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
        # v14 — 엣지 출생. 공유·동기화가 이 투영에서 값을 집어 나른다(안 나르면
        # 받는 쪽에서 그 엣지가 그 기기 시각으로 다시 태어난다). 화면은 아직 안 쓴다.
        "r.created AS edge_created, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts, "
        # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드. **Concept 에만**
        # 있는 열이라(Decision·Record·Episode·Bridge 에는 없다) 여기 두 자리와
        # 축 플랜의 개념 팔에서만 뽑는다. 다른 라벨에 옮겨 붙이면 Binder 오류다.
        "coalesce(c.one_liner, '') AS one_liner, "
        "coalesce(d.one_liner, '') AS target_one_liner, "
        # W3 — 양 끝 다 싣는다. 한쪽만 실으면 target 으로만 등장한 노드가 영구히
        # 버전 없이 남고, 그건 "안 바뀌었다"와 구분되지 않는다.
        + _track_cols("c") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("graph", cypher, params, note="learning knowledge-graph (concepts + relations)")


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
              content_ts=None, one_liner=None, rev=None, updated_at=None,
              valid_from=None, valid_until=None):
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label or nid, "domain": dom or "",
                          "kind": kind or "concept", "degree": 0,
                          "props": _props_to_obj(props), "tags": list(tags or []),
                          "ts": ts or "", "content_ts": content_ts or "",
                          # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드.
                          # 열은 **Concept 에만** 있으므로 나머지 종류에서는 빈
                          # 문자열이고, 그건 결손이 아니라 사실이다.
                          "one_liner": one_liner or "",
                          # W3 — 낙관적 동시성의 읽기 절반. 쓰기 쪽은 아직 이 값을
                          # 요구하지 않는다(D4: 투영만).
                          "rev": rev or 0, "updated_at": updated_at or "",
                          # W13 — Record 만 값을 갖는다(축 플랜 중 record 팔 전용 열).
                          # 다른 종류의 빈 문자열은 결손이 아니라 사실이다(R2 동형).
                          "valid_from": valid_from or "",
                          "valid_until": valid_until or ""}
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
            # one_liner 도 같다: 개념이 축 플랜(열 없음)에서 먼저 도착하면 빈 채로
            # 들어오는데, 뒤이어 개념 플랜의 행이 값을 들고 온다.
            if one_liner and not nodes[nid].get("one_liner"):
                nodes[nid]["one_liner"] = one_liner
            # W3 — 같은 규칙. 투영 없는 행(있다면)이 먼저 닿아도 버전이 0/빈 채로
            # 굳지 않는다. 진짜 rev 0 은 채울 것이 없으므로 그대로 0 이다.
            if rev and not nodes[nid].get("rev"):
                nodes[nid]["rev"] = rev
            if updated_at and not nodes[nid].get("updated_at"):
                nodes[nid]["updated_at"] = updated_at
            # W13 — 같은 규칙. Record 는 개념 플랜의 target 으로 먼저 도착할 수
            # 있고(record→concept 의 relates_to 는 없지만 ids 모드 합류가 있다),
            # 그때 구간이 빈 채로 굳으면 무효화가 그 조회에서만 안 보인다.
            if valid_from and not nodes[nid].get("valid_from"):
                nodes[nid]["valid_from"] = valid_from
            if valid_until and not nodes[nid].get("valid_until"):
                nodes[nid]["valid_until"] = valid_until
        return nodes[nid]

    edges: list[dict] = []
    for r in rows:
        nid = r.get("id")
        if not nid:
            continue
        _node(nid, r.get("label"), r.get("domain"), r.get("props"), r.get("tags"),
              r.get("kind"), r.get("domain2"), r.get("ts"), r.get("content_ts"),
              r.get("one_liner"), r.get("rev"), r.get("updated_at"),
              # W13 — 유효구간은 record 축 행만 싣는다(매개자 자리라 source 측만).
              r.get("valid_from"), r.get("valid_until"))
        tgt = r.get("target")
        if tgt:
            # C5: target 은 target_label(=title)로 만든다 — 예전엔 id 를 label 로 넘겨
            # target-only 로 등장한 노드가 항상 id 노출로 폴백됐다(_node 의
            # `label or nid` 폴백은 title 이 정말 없을 때만 쓰여야 한다).
            _node(tgt, r.get("target_label"), r.get("target_domain"),
                  r.get("target_props"), r.get("target_tags"), r.get("target_kind"),
                  ts=r.get("target_ts"), content_ts=r.get("target_content_ts"),
                  one_liner=r.get("target_one_liner"), rev=r.get("target_rev"),
                  updated_at=r.get("target_updated_at"),
                  # 기록끼리의 SUPERSEDES 는 도착점도 Record 다. 옛 기록의 구간이
                  # 빠지면 은퇴한 기록이 그래프에서 유효한 것처럼 보인다.
                  valid_from=r.get("target_valid_from"),
                  valid_until=r.get("target_valid_until"))
            src_dom, tgt_dom = r.get("domain"), r.get("target_domain")
            # cross_domain: 끝점 차이 규칙 유지 — 브릿지는 domain=source_domain 이라
            # 원측(source) 팔은 in-cluster, 반대측 팔만 cross(기하 정합, 특례 불요).
            cross = bool(src_dom and tgt_dom and src_dom != tgt_dom)
            edge = {"source": nid, "target": tgt,
                    "type": r.get("rel") or "RELATED_TO", "cross_domain": cross}
            if r.get("role"):
                edge["role"] = r["role"]
            # E-G6 — 관계 증거·신뢰도(선택 열). 상류 미저작 시 키를 붙이지 않는다(role 과 동형).
            if r.get("confidence") is not None:
                edge["confidence"] = float(r["confidence"])
            if r.get("evidence"):
                edge["evidence"] = r["evidence"]
            edges.append(edge)
            nodes[nid]["degree"] += 1
            nodes[tgt]["degree"] += 1
    return {"nodes": list(nodes.values()), "edges": edges}


def _derive_bridge_domains(shaped: dict) -> None:
    """Bridge 의 `domain` 은 **저작물이 아니라 파생값**이다 — CONNECTS 로 닿는 끝점
    도메인들의 가장 깊은 공통 조상(`vinc/security` + `vinc/engineering` → `vinc`).

    저장하지 않고 /graph 응답에서만 계산한다. 저장하면 연결이 바뀌는 순간 썩는데,
    브릿지의 소속은 정의상 그 연결이 결정하기 때문이다. 여태 노드 domain 은 선언
    필드 `source_domain` 이었고 라이브에서는 그게 **전부 비어 있어서**(v2 owner
    백필이 건너뛰어지며 domain 도 같이 남았다) 세 관점 모두가 브릿지 전량을
    "도메인 없음" 한 칸에 몰아넣었다. 근거: [[record:bridge-layout-clump-2026-08-22]].

    CONNECTS 가 하나도 없으면 미분류(`""`)로 남는다 — 지어낼 근거가 없다.
    도메인이 빈 끝점은 계산에서 뺀다(`_shared_prefix` 규약): 미분류 이웃 하나가
    나머지 끝점의 공통 조상을 뿌리까지 끌어내리면 안 된다.

    `domain2`(선언된 target_domain)는 상세 레일 계약이라 손대지 않는다."""
    nodes = shaped.get("nodes") or []
    doms = {n.get("id"): n.get("domain") or "" for n in nodes}
    bridges = {n.get("id") for n in nodes if n.get("kind") == "bridge"}
    if not bridges:
        return
    ends: dict[str, list[str]] = {}
    for e in shaped.get("edges") or []:
        if (e.get("type") or "") != "CONNECTS":
            continue
        s, t = e.get("source"), e.get("target")
        # 방향은 늘 Bridge→Concept 이지만 양쪽을 다 본다 — 어느 축 플랜이 실어 오든
        # 같은 답이 나오게(투영이 뒤집혀도 조용히 미분류가 되지 않게).
        if s in bridges and t in doms:
            ends.setdefault(s, []).append(t)
        if t in bridges and s in doms:
            ends.setdefault(t, []).append(s)
    for n in nodes:
        if n.get("id") in bridges:
            n["domain"] = "/".join(_shared_prefix(
                [_domain_path(doms.get(t, "")) for t in ends.get(n["id"], ())]))


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
# 시각(없으면 갱신), Episode 는 사건 날짜, Entity 는 갱신 시각, Bridge 는 생성 시각을 읽지만
# 그 열은 쓰이지 않아 사실상 갱신 시각이다([[record:bridge-created-is-read-but-never-written]]).
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
    치면 시각 없는 레거시 행이 전부 밀려나고, 새것으로 치면 거꾸로 올라온다 — 둘 다
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
    단위로 그대로여야 한다(VIN-195 수락 기준)."""
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
              "label(r) AS type, r.confidence AS confidence")
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


def subgraph_ids_plan(seed: str, *, depth: int = 2, owner: str | None = None,
                      seed_label: str = "Concept") -> QueryPlan:
    """Seed+depth 순회 — View L2 노드 포커스와 MCP `vinc_graph` 의 공유 플랜(D16,
    MCP-SKELETON §10 공백 해소). seed 에서 depth 이내에 닿는 Concept id 를 수집한다.

    **G1(2026-08-03, ladybug 0.18.0 실측): 순회 의미론 `TRAIL` → `SHORTEST`.**
    (아래 지연 수치는 0.18.0 의 것이다. 0.20.4 에서는 다시 재지 않았다.)
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
    owner=None(로컬 단일 사용자)이면 기존 동작.

    ``seed_label`` — **시드 한 칸만** 라벨을 바꾼다(기본 Concept, 그때 플랜 문자열은
    종전과 바이트 동일). 도달 집합은 여전히 Concept 이다. 결정 시드가 여기서 0행을
    내서 `vinc_graph` 가 결정에서 퍼지지 않았다(감사 §5-6). 공유 팩·동기화·decide 는
    이 인자를 안 넘기므로 결과가 그대로다.
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4"""
    depth = max(1, min(int(depth), 4))
    # PK 네임스페이싱 — `$seed` 는 노드 PK 바인딩이라 물리 키다(반환 id 는 읽기 훅이
    # 논리로 되돌린다). 근거: [[vinc.plan.node-pk-namespacing]] §7
    params: dict = {"seed": pkns.key(owner, seed)}
    seed_pk = _SEMANTIC_PK[seed_label]
    n_where, m_where = f"n.{seed_pk} = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:{seed_label}) WHERE {n_where} "
        f"MATCH (n)-[* SHORTEST 1..{depth}]-(m:Concept){m_where} "
        "RETURN DISTINCT m.concept_id AS id"
    )
    return QueryPlan("subgraph_ids", cypher, params,
                     note=f"seed+depth reach set (depth={depth}, SHORTEST)")


def subgraph_hops_plan(seed: str, *, depth: int = 2, owner: str | None = None,
                       seed_label: str = "Concept") -> QueryPlan:
    """:func:`subgraph_ids_plan` 과 같은 도달 집합에 **홉 수**를 붙인다.

    노드 예산(`max_nodes`)을 받은 :func:`graph` 만 쓴다. 예산을 넘으면 깊이를 층
    단위로 줄이려면 각 노드가 몇 홉에 있는지 알아야 해서다. SHORTEST 는 (시드, m)
    쌍마다 경로 하나만 펴므로 행도 m 마다 하나다. 예산 없는 호출(View L2·공유 팩·
    동기화)은 이 플랜을 타지 않는다 — 그 결과는 바이트 단위로 그대로다.

    ⚠ 무라벨 순회라 IN_DOMAIN·PARENT_OF 도 홉이다. 그래서 depth 2 는 시드와 **같은
    주제의 개념 전부**에 닿고(Domain 노드 경유), depth 4 면 주제 트리를 타고 형제
    주제의 개념까지 번진다(임베디드 스토어 실측, 2026-09-24). 허브의 depth 2+ 가 무거운 까닭 하나이고, 예산이 층 단위로 깊이를
    줄이는 것이 그 비용을 끊는다(순회 의미 자체는 공유 플랜이라 여기서 안 바꾼다).
    근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]"""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": pkns.key(owner, seed)}
    seed_pk = _SEMANTIC_PK[seed_label]
    n_where, m_where = f"n.{seed_pk} = $seed", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " WHERE m.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (n:{seed_label}) WHERE {n_where} "
        f"MATCH (n)-[r* SHORTEST 1..{depth}]-(m:Concept){m_where} "
        "RETURN m.concept_id AS id, min(length(r)) AS hops"
    )
    return QueryPlan("subgraph_hops", cypher, params,
                     note=f"seed+depth reach set with hop counts (depth={depth})")


def decision_seed_rows_plan(seed: str, *, owner: str | None = None,
                            other: str = "Concept", outgoing: bool = True,
                            rels: str = "PREREQUISITE_OF|DERIVES_FROM|INHERITS_FROM"
                            ) -> QueryPlan:
    """결정 시드 자신의 의미층 엣지(PREREQUISITE_OF·DERIVES_FROM·INHERITS_FROM) 한 방향·
    한 상대 라벨. :func:`decide`가 결정 시드의 로컬 관계를 읽는 플랜이다.
    :func:`graph`는 공통 읽기 계약의 decision_relation_rows_plan을 사용한다.

    ``rels`` 는 :func:`decide` 만 바꾼다 — 거기엔 결정 축 플랜이 안 돌아서 ABOUT 도
    이 플랜으로 싣는다(결정 축 플랜은 owner 필수라 로컬 무소유 호출에서 못 쓴다).

    행 모양은 graph_rows 와 같다(id=출발, target=도착). Decision 에는 one_liner·
    content_time 열이 없어 그 자리는 리터럴이다 — 열 없는 라벨에 옮겨 붙이면 Binder
    가 조회 전체를 죽인다(decision_rows_plan 경고와 같은 자리).
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4"""
    params: dict = {"seed": pkns.key(owner, seed)}
    s_where, o_where = "s.decision_id = $seed", ""
    if owner:
        s_where += " AND s.owner_id = $owner"
        o_where = " WHERE o.owner_id = $owner"
        params["owner"] = owner
    o_pk = _SEMANTIC_PK[other]
    o_kind = other.lower()
    o_ol = "coalesce(o.one_liner, '')" if other == "Concept" else "''"
    o_ct = "coalesce(o.content_time, '')" if other == "Concept" else "''"
    s_cols = ("s.decision_id AS {i}, s.title AS {p}label, s.domain AS {p}domain, "
              "s.props AS {p}props, coalesce(s.tags, []) AS {p}tags, "
              "'decision' AS {p}kind, coalesce(s.created, s.updated_at, '') AS {p}ts, "
              "'' AS {p}content_ts, '' AS {p}one_liner, ")
    o_cols = (f"o.{o_pk} AS {{i}}, o.title AS {{p}}label, o.domain AS {{p}}domain, "
              "o.props AS {p}props, coalesce(o.tags, []) AS {p}tags, "
              f"'{o_kind}' AS {{p}}kind, coalesce(o.created, o.updated_at, '') AS {{p}}ts, "
              f"{o_ct} AS {{p}}content_ts, {o_ol} AS {{p}}one_liner, ")
    src, dst = (s_cols, o_cols) if outgoing else (o_cols, s_cols)
    src_a, dst_a = ("s", "o") if outgoing else ("o", "s")
    if outgoing:
        pattern = f"(s)-[r:{rels}]->(o:{other})"
    else:
        pattern = f"(o:{other})-[r:{rels}]->(s)"
    cypher = (
        f"MATCH (s:Decision) WHERE {s_where} "
        f"MATCH {pattern}{o_where} "
        "RETURN " + src.format(p="", i="id") + dst.format(p="target_", i="target")
        + "label(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        + _track_cols(src_a) + ", " + _track_cols(dst_a, "target_")
    )
    return QueryPlan("graph_decision_seed", cypher, params,
                     note=f"decision seed edges ({'out' if outgoing else 'in'} {other})")


def graph_rows_for_ids_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합의 Concept 행 + 나가는 엣지(§4-2 — 엣지의 서브그래프 외부 여과는
    파이썬 shape 단계에서). `graph_plan` 과 동일 투영.

    ⚠ owner 필수성(SEC-1): id 집합이 이미 owner-scoped 여도 **엣지 대상 d 는 별개**다 —
    필터가 없으면 남의 노드가 target_label/target_props 로 노출된다. 소유자를 주면
    행(c)과 엣지 대상(d)을 모두 제한한다."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    c_where, d_where = "c.concept_id IN $ids", ""
    if owner:
        c_where += " AND c.owner_id = $owner"
        d_where = " WHERE d.owner_id = $owner"
        params["owner"] = owner
    cypher = (
        f"MATCH (c:Concept) WHERE {c_where} "
        f"OPTIONAL MATCH (c)-[r:{_CONCEPT_READ_PATTERN}]->(d:Concept){d_where} "
        "RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "c.props AS props, c.tags AS tags, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        # v7(D72) — 내용 시점을 **별도 열로** 나른다(ts 에 접지 않는다). 클라이언트가
        # 둘 다 받아 두면 축 전환에 재조회가 필요 없다. Concept 만 이 열을 가지므로
        # 다른 라벨의 플랜에 그대로 옮겨 붙이면 Binder 오류다(위 경고와 같은 규칙).
        "coalesce(c.content_time, '') AS content_ts, "
        "label(r) AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        # v14 — 엣지 출생. 공유·동기화가 이 투영에서 값을 집어 나른다(안 나르면
        # 받는 쪽에서 그 엣지가 그 기기 시각으로 다시 태어난다). 화면은 아직 안 쓴다.
        "r.created AS edge_created, "
        "d.concept_id AS target, d.domain AS target_domain, "
        "d.title AS target_label, d.props AS target_props, d.tags AS target_tags, "
        "coalesce(d.created, d.updated_at, '') AS target_ts, "
        "coalesce(d.content_time, '') AS target_content_ts, "
        # R2 — 쓸 수는 있는데 어떤 읽기 도구로도 못 읽던 필드. **Concept 에만**
        # 있는 열이라(Decision·Record·Episode·Bridge 에는 없다) 여기 두 자리와
        # 축 플랜의 개념 팔에서만 뽑는다. 다른 라벨에 옮겨 붙이면 Binder 오류다.
        "coalesce(c.one_liner, '') AS one_liner, "
        "coalesce(d.one_liner, '') AS target_one_liner, "
        # W3 — 양 끝 다 싣는다. 한쪽만 실으면 target 으로만 등장한 노드가 영구히
        # 버전 없이 남고, 그건 "안 바뀌었다"와 구분되지 않는다.
        + _track_cols("c") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("graph", cypher, params, note="subgraph rows for id set")


# ── View 축 플랜(F2 — Concept 단일 축 해소) ─────────────────────────────────
# 부착 축 3종(Bridge·Episode·Record)을 별도 순수 플랜으로 두고 파이썬 셰이핑에서
# 합류한다(UNION ALL 메가플랜 대신 — 이종 투영의 NULL 패딩 회피 + 플랜별 강등:
# 미마이그레이션 스토어에서 새 축만 조용히 빈 행이 되고 Concept 축은 산다).
# 공통 계약: **비-OPTIONAL 매치** — 부착 없는 항목은 렌더 대상이 아니다(View =
# 연결 뷰; 미부착 Episode/Record 의 표면은 Records/L0 집계). domain(L1 렌즈)과
# ids(L2 ego 후조회)는 상호 배제. ids 모드는 **어느 한쪽 끝점**이 집합에 들면
# 매치 — concept 시드에선 팔이 kept 개념에 닿을 때(접착제), episode:/record:/
# bridge: 시드에선 자기 부착이 나온다(고립 폴백 대신 depth-1 이웃 렌더).

def bridge_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Bridge 노드 + CONNECTS 팔(F2). 노드 domain = source_domain(단일 결정값),
    target_domain 은 domain2 로 병기(상세 레일용). L1 소속은 선언 필드(source/
    target_domain)를 신뢰 — 실 CONNECTS 끝점과 어긋난 브릿지는 렌즈에서 빠질 수
    있다(L2 에선 보임, v1 수용). owner: Bridge 과도기 술어 + 도착점 엄격.

    도착점은 의미층 라벨마다 한 팔이다(개념·결정). 개념만 보면 결정에 걸린 다리가
    vinc_neighbors 에는 보이고 그래프에는 안 보인다(감사 F2, 2026-10-16)."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = [_bridge_owner_pred("b"), "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(
            ["b.source_domain", "b.target_domain"], doms, params))
    if ids:
        params["ids"] = pkns.keys(owner, ids)
    arms = []
    for t_label, t_pk in _SEMANTIC_PK.items():
        arm_where = list(where)
        if ids:
            arm_where.append(f"(c.{t_pk} IN $ids OR b.bridge_id IN $ids)")
        # one_liner·content_time 은 Concept 에만 있는 열이라 다른 라벨은 리터럴로 맞춘다.
        concept = t_label == "Concept"
        arms.append(
            f"MATCH (b:Bridge)-[r:CONNECTS]->(c:{t_label}) "
            f"WHERE {' AND '.join(arm_where)} "
            "RETURN b.bridge_id AS id, b.title AS label, b.source_domain AS domain, "
            "b.target_domain AS domain2, b.props AS props, 'bridge' AS kind, "
            "coalesce(b.created, b.updated_at, '') AS ts, "
            "'CONNECTS' AS rel, r.role AS role, "
            f"c.{t_pk} AS target, c.domain AS target_domain, c.title AS target_label, "
            f"c.props AS target_props, c.tags AS target_tags, '{t_label.lower()}' AS target_kind, "
            "coalesce(c.created, c.updated_at, '') AS target_ts, "
            # F10 — 개념의 내용 시점을 이 축에서도 싣는다. 빠지면 다리로만 닿은 개념이
            # 내용 시점 없이 굳는다.
            + ("coalesce(c.content_time, '') AS target_content_ts, "
               "coalesce(c.one_liner, '') AS target_one_liner, " if concept else
               "'' AS target_content_ts, '' AS target_one_liner, ")
            # W3 — 매개자(b)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
            # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
            + _track_cols("b") + ", " + _track_cols("c", "target_"))
    return QueryPlan("graph_axis_bridge", " UNION ALL ".join(arms), params,
                     note="bridge axis rows (F2)")


def episode_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                      ids: list[str] | None = None) -> QueryPlan:
    """Episode 노드 + ABOUT 팔(F2/F4). L1 소속은 (자기 domain OR 부착 개념 domain)
    — 렌즈 밖 에피소드가 렌즈 안 개념에 부착된 경우가 정확히 F2 가 숨기던 연결이라
    OR 가 의도다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["e.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["e.domain", "c.domain"], doms, params))
    if ids:
        where.append("(c.concept_id IN $ids OR e.episode_id IN $ids)")
        params["ids"] = pkns.keys(owner, ids)
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
        "coalesce(c.created, c.updated_at, '') AS target_ts, "
        # F10 — 내용 시점도 같은 이유로 싣는다(도착점이 개념이라 열이 있다).
        "coalesce(c.content_time, '') AS target_content_ts, "
        # R2 — 같은 이유로 개념 팔의 one_liner 도 싣는다(Episode 쪽에는 열이 없다).
        "coalesce(c.one_liner, '') AS target_one_liner, "
        # W3 — 매개자(e)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
        # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
        + _track_cols("e") + ", " + _track_cols("c", "target_")
    )
    return QueryPlan("graph_axis_episode", cypher, params, note="episode axis rows (F2/F4)")


def record_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                     ids: list[str] | None = None) -> QueryPlan:
    """Record 노드 + ATTACHES_TO 팔(F2). 엣지 질화자 r.type 은 role 슬롯으로 투영
    (CONNECTS.role 과 단일 선택 열 공유 — shape_graph 계약).

    도착점은 의미층 라벨마다 한 팔이다(개념·결정). 근거·신뢰도(v16 실열)도 싣는다 —
    빠지면 vinc_graph 와 그것을 쓰는 내보내기가 부착의 근거를 잃는다(감사 F2·F2b)."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["rc.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["rc.domain", "c.domain"], doms, params))
    if ids:
        params["ids"] = pkns.keys(owner, ids)
    arms = []
    for t_label, t_pk in _SEMANTIC_PK.items():
        arm_where = list(where)
        if ids:
            arm_where.append(f"(c.{t_pk} IN $ids OR rc.record_id IN $ids)")
        # one_liner·content_time 은 Concept 에만 있는 열이라 다른 라벨은 리터럴로 맞춘다.
        concept = t_label == "Concept"
        arms.append(
            f"MATCH (rc:Record)-[r:ATTACHES_TO]->(c:{t_label}) "
            f"WHERE {' AND '.join(arm_where)} "
            "RETURN rc.record_id AS id, rc.title AS label, rc.domain AS domain, "
            "rc.props AS props, coalesce(rc.tags, []) AS tags, 'record' AS kind, "
            "coalesce(rc.created, rc.updated_at, '') AS ts, "
            # v7(D72) — Record 도 content_time 을 얻었다(Concept 과 같은 축). Bridge·Entity
            # 축 플랜에는 열이 없으므로 절대 옮겨 붙이지 않는다 — 붙이면 모든 레벨의
            # /graph 가 Binder 로 죽는다.
            "coalesce(rc.content_time, '') AS content_ts, "
            "'ATTACHES_TO' AS rel, r.type AS role, "
            "r.evidence AS evidence, r.confidence AS confidence, "
            f"c.{t_pk} AS target, c.domain AS target_domain, c.title AS target_label, "
            f"c.props AS target_props, c.tags AS target_tags, '{t_label.lower()}' AS target_kind, "
            "coalesce(c.created, c.updated_at, '') AS target_ts, "
            # R2 — 개념 팔에서만 뽑는다. 매개자(Bridge·Episode·Record·Decision)에는
            # 이 열이 없어 shape_graph 가 빈 문자열로 둔다(결손이 아니라 사실).
            + ("coalesce(c.content_time, '') AS target_content_ts, "
               "coalesce(c.one_liner, '') AS target_one_liner, " if concept else
               "'' AS target_content_ts, '' AS target_one_liner, ")
            # W3 — 매개자(rc)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
            # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
            + _track_cols("rc") + ", " + _track_cols("c", "target_") + ", "
            # W13 — 유효구간은 **Record 팔에서만** 뽑는다(도착점에는 열이 없다).
            + _validity_cols("rc"))
    return QueryPlan("graph_axis_record", " UNION ALL ".join(arms), params,
                     note="record axis rows (F2)")


def decision_rows_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
                       ids: list[str] | None = None) -> QueryPlan:
    """Decision 노드 + ABOUT 팔(v12). 결정이 **무엇을 두고** 내려졌는지가 그 팔이다.

    Episode 축과 같은 형태다(둘 다 ABOUT 으로 개념에 붙는다). L1 소속도 같은 이유로
    OR — 렌즈 밖 결정이 렌즈 안 개념을 두고 내려진 경우가 정확히 F2 가 숨기던 연결이다.

    ⚠ `content_ts` 를 **투영하지 않는다**. Decision 에는 그 열이 없다(라이브 실측에서
    68/68 이 비어 있어 만들지 않았다). 열 없는 라벨에 옮겨 붙이면 모든 레벨의 /graph
    가 Binder 로 죽는다 — Record 축 주석이 경고하는 바로 그 자리다. 도착점(Concept)
    쪽은 열이 있으므로 target_content_ts 는 싣는다."""
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    where = ["d.owner_id = $owner", "c.owner_id = $owner"]
    params: dict = {"owner": owner}
    if doms:
        where.append(domain_subtree_where(["d.domain", "c.domain"], doms, params))
    if ids:
        where.append("(c.concept_id IN $ids OR d.decision_id IN $ids)")
        params["ids"] = pkns.keys(owner, ids)
    cypher = (
        "MATCH (d:Decision)-[r:ABOUT]->(c:Concept) "
        f"WHERE {' AND '.join(where)} "
        "RETURN d.decision_id AS id, d.title AS label, d.domain AS domain, "
        "d.props AS props, coalesce(d.tags, []) AS tags, 'decision' AS kind, "
        "coalesce(d.created, d.updated_at, '') AS ts, "
        "'ABOUT' AS rel, r.evidence AS evidence, r.confidence AS confidence, "
        "c.concept_id AS target, c.domain AS target_domain, c.title AS target_label, "
        "c.props AS target_props, c.tags AS target_tags, 'concept' AS target_kind, "
        "coalesce(c.created, c.updated_at, '') AS target_ts, "
        "coalesce(c.content_time, '') AS target_content_ts, "
        # R2 — 개념 팔에서만 뽑는다. 매개자(Bridge·Episode·Record·Decision)에는
        # 이 열이 없어 shape_graph 가 빈 문자열로 둔다(결손이 아니라 사실).
        "coalesce(c.one_liner, '') AS target_one_liner, "
        # W3 — 매개자(d)도 vinc_graph 의 노드로 나가므로 함께 싣는다.
        # one_liner 와 달리 이 두 열은 추적 라벨 전부에 있어 리터럴 맞춤이 필요 없다.
        + _track_cols("d") + ", " + _track_cols("c", "target_")
    )
    return QueryPlan("graph_axis_decision", cypher, params, note="decision axis rows (v12)")


def decision_relation_rows_plan(*, owner: str, domain: str | None = None,
                                domains: list[str] | None = None,
                                ids: list[str] | None = None) -> QueryPlan:
    """기존 축이 읽지 않는 관계를 전체/주제/시드 그래프에 보충한다.

    이름과 달리 Decision 관계만이 아니다. 개념끼리(graph_plan)와 ABOUT→Concept
    (사건·결정 축)를 뺀 공통 읽기 계약의 나머지 전부다 — 결정의 의미 관계, 사건→결정
    ABOUT, 그리고 기록끼리의 SUPERSEDES(감사 F2, 2026-10-16). 이름을 그대로 둔 것은
    호출부와 테스트가 이 이름으로 부르기 때문이다.

    끝점 조합은 스키마 파생 계약을 사용한다. 렌즈 소속은 기존 부착 축처럼 어느 한
    끝점의 domain으로 판정한다. ids 모드는 어느 한 끝점이 기존 도달 집합에 속한
    관계만 한 번 합류한다. 이웃을 다시 순회하지 않으므로 Concept 도달 집합과 예산은
    그대로다.
    """
    doms = normalize_domains(domain, domains)
    params: dict = {"owner": owner}
    pairs: dict[tuple, list[str]] = {}
    for pair, rels in _GRAPH_READ_PAIRS.items():
        sl, _sp, dl, _dp = pair
        if (sl, dl) == ("Concept", "Concept"):
            continue
        selected = [rel for rel in rels if not (rel == "ABOUT" and dl == "Concept")]
        if selected:
            pairs[pair] = selected
    if ids is not None:
        params["ids"] = pkns.keys(owner, ids)

    def cols(alias: str, label: str, pk: str, prefix: str = "") -> str:
        nid = "target" if prefix else "id"
        time_col = "date" if label == "Episode" else "created"
        ol = f"coalesce({alias}.one_liner, '')" if label == "Concept" else "''"
        ct = (f"coalesce({alias}.content_time, '')" if label in ("Concept", "Record")
              else "''")
        # 유효구간은 Record 열이다. 다른 라벨은 리터럴로 맞춘다(UNION 모양).
        vf, vu = ((f"coalesce({alias}.valid_from, '')", f"coalesce({alias}.valid_until, '')")
                  if label == "Record" else ("''", "''"))
        return (
            f"{alias}.{pk} AS {nid}, {alias}.title AS {prefix}label, "
            f"{alias}.domain AS {prefix}domain, {alias}.props AS {prefix}props, "
            f"coalesce({alias}.tags, []) AS {prefix}tags, '{label.lower()}' AS {prefix}kind, "
            f"coalesce({alias}.{time_col}, {alias}.updated_at, '') AS {prefix}ts, "
            f"{ct} AS {prefix}content_ts, {ol} AS {prefix}one_liner, "
            f"{vf} AS {prefix}valid_from, {vu} AS {prefix}valid_until, "
            + _track_cols(alias, prefix)
        )

    arms = []
    for (sl, sp, dl, dp), rels in pairs.items():
        where = ["a.owner_id = $owner", "b.owner_id = $owner"]
        if ids is not None:
            where.append(f"(a.{sp} IN $ids OR b.{dp} IN $ids)")
        elif doms:
            where.append(domain_subtree_where(["a.domain", "b.domain"], doms, params))
        arms.append(
            f"MATCH (a:{sl})-[r:{'|'.join(rels)}]->(b:{dl}) "
            f"WHERE {' AND '.join(where)} RETURN "
            + cols("a", sl, sp) + ", " + cols("b", dl, dp, "target_") + ", "
            "label(r) AS rel, r.evidence AS evidence, r.confidence AS confidence"
        )
    return QueryPlan("graph_decision_relations", " UNION ALL ".join(arms), params,
                     note="decision relations outside the existing attachment axes")


_GRAPH_AXIS_PLANS = (bridge_rows_plan, episode_rows_plan, record_rows_plan,
                     decision_rows_plan)
_GRAPH_READ_PLANS = _GRAPH_AXIS_PLANS + (decision_relation_rows_plan,)


# ── entity-시드 L2(F1) — TYPED 는 Entity↔Entity 폐쇄 성분 ────────────────────
# Entity 는 domain 열이 없고(테마 귀속) 어떤 레거시 rel 테이블에도 닿지 않으므로
# L1/개념시드 L2 에는 **구조적으로** 등장하지 않는다. 가시성은 entity: 시드 전용.

def entity_subgraph_ids_plan(seed: str, *, depth: int = 2,
                             owner: str | None = None) -> QueryPlan:
    """entity-시드 순회 — subgraph_ids_plan 과 동형(TRAIL 가드, depth 1..4 클램프).
    Entity 는 신규 테이블(레거시 '' 없음)이라 엄격 owner."""
    depth = max(1, min(int(depth), 4))
    params: dict = {"seed": pkns.key(owner, seed)}
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
    params: dict = {"ids": pkns.keys(owner, ids)}
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
        "'entity' AS target_kind, "
        # W3 — Entity 도 추적 라벨이다(`_TRACKED_LABELS`). 개방층 노드만 버전이
        # 안 보이면 그 축에서만 쓰기 확인이 안 된다.
        + _track_cols("e") + ", " + _track_cols("d", "target_")
    )
    return QueryPlan("entity_graph", cypher, params, note="entity rows for id set (TYPED)")


#: 의미층 라벨 → PK. 관측·부착 플랜이 "상대편으로 올 수 있는 것"을 여기서 편다.
#: (store.embedded_schema._SEMANTIC_LABELS 와 같은 집합 — 그쪽은 스키마, 여기는 조회.)
_SEMANTIC_PK: dict[str, str] = {"Concept": "concept_id", "Decision": "decision_id"}
_SEMANTIC_LABELS = frozenset(_SEMANTIC_PK)

#: 주제 아래 설 수 있는 종 **전부**. `_SEMANTIC_PK` 보다 넓다 — 좌측 트리의 종 레일이
#: 기록·사건까지 세우기 때문이다. Bridge 와 개방 타입(Entity)이 빠진 것은 취향이 아니라
#: 스키마다: `IN_DOMAIN` 이 이 넷에만 걸리고(store/embedded_schema.py), Bridge 는
#: `CROSSES` 로 Domain 에 붙어 source_domain/target_domain 을 따로 갖는다(그래서
#: fragment ingest 가 Bridge 의 domain 을 저장하지 않는다고 경고한다). 개방 타입은
#: domain 을 아예 보존하지 않는다. **없는 줄을 레일에 세우지 않으려면 이 목록이 정본이다.**
_TREE_PK: dict[str, str] = {
    "Concept": "concept_id", "Decision": "decision_id",
    "Record": "record_id", "Episode": "episode_id",
}
#: 화면이 보내는 종 이름 → 라벨. 소문자 단수형이 `NavNode.kind` 와 같은 값이다.
_TREE_KIND_LABEL: dict[str, str] = {lbl.lower(): lbl for lbl in _TREE_PK}

_SEED_TABLES = {   # seed id 접두사 → (라벨, PK 열) — C4 선조회용 (+ entity, F1)
    "concept": ("Concept", "concept_id"),
    "episode": ("Episode", "episode_id"),
    "record": ("Record", "record_id"),
    "bridge": ("Bridge", "bridge_id"),
    "entity": ("Entity", "entity_id"),
    "decision": ("Decision", "decision_id"),   # v12 — 없으면 아래 폴백이 Entity 로
    # 보내 시드·삭제·이웃 조회가 **조용히 빈 결과**를 낸다(미지 접두사 = 개방층).
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


# 고립 시드가 실어 오는 열 — 라벨마다 **있는 것만** 고른다. 없는 열을 RETURN 하면
# Binder 예외로 조회가 통째로 죽는다(Bridge 에 `domain` 이 없는 것과 같은 자리).
# ts 표현은 각 축 플랜의 coalesce 와 같은 것을 쓴다 — 같은 노드가 연결 유무에 따라
# 다른 시간을 말하면 안 된다.
_SEED_FIELDS: dict[str, dict[str, str]] = {
    "Concept": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                "ts": "coalesce(n.created, n.updated_at, '')",
                "content_ts": "coalesce(n.content_time, '')"},
    "Record": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
               "ts": "coalesce(n.created, n.updated_at, '')",
               "content_ts": "coalesce(n.content_time, '')"},
    "Decision": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                 "ts": "coalesce(n.created, n.updated_at, '')"},
    "Episode": {"domain": "n.domain", "tags": "n.tags", "props": "n.props",
                "ts": "coalesce(n.date, n.updated_at, '')"},
    "Bridge": {"props": "n.props", "ts": "coalesce(n.created, n.updated_at, '')"},
    "Entity": {"tags": "n.tags", "ts": "coalesce(n.updated_at, '')"},
}


def seed_lookup_plan(seed: str, *, owner: str | None = None) -> QueryPlan:
    """seed 노드 존재 선조회(C4) — '노드 없음'과 '노드는 있으나 연결 0(고립)'을
    구분하기 위한 조회. id 접두사(`concept:`·`episode:`·
    `record:`·`bridge:`·`entity:`)로 테이블을 판별하고, 접두사가 없거나 미지의
    접두사면 Concept 로 시도한다. `graph()` 는 순회 결과가 비었을 때만 이 플랜을
    쓴다 — 순회가 이미 노드를 찾았다면(=시드가 확실히 존재) 굳이 다시 묻지 않는다.

    owner(SEC-1): Concept/Episode/Record/Entity 는 엄격 owner 로 제한한다. Bridge 는
    v2(F5)에서 owner_id 를 얻어 과도기 술어(:func:`_bridge_owner_pred`)로 필터한다
    — 구 '필터 불가' 예외(존재 여부 노출)는 해소됐다. 미지 접두사는 Entity 로
    프로브한다(:func:`_seed_table` — 개방층 id 는 임의 접두사)."""
    label, pk = _seed_table(seed)
    params: dict = {"seed": pkns.key(owner, seed)}
    where = f"n.{pk} = $seed"
    if owner:
        where += (f" AND {_bridge_owner_pred('n')}" if label == "Bridge"
                  else " AND n.owner_id = $owner")
        params["owner"] = owner
    # ⚠ id·title 만 싣던 시절, 고립 노드는 `vinc_graph` 에서 **domain·tags·props·ts 가
    # 전부 빈** 모습으로 답했다 — 아래 graph() 가 없는 값을 빈 값으로 지어냈기 때문이다.
    # 값이 실제로 저장돼 있어도 그렇게 보이므로, 방금 쓴 노드를 읽어 확인하는 절차가
    # (그래프에 근거를 옮길 때 쓰는 그 절차가) "쓰기가 실패했다"는 오진을 낳았다.
    # 2026-08-18 에 실제로 두 번 그렇게 오진했다. 이제 있는 열은 실어 온다.
    extra = _SEED_FIELDS.get(label, {})
    cols = ", ".join([f"n.{pk} AS id", "n.title AS title"]
                     + [f"{expr} AS {alias}" for alias, expr in extra.items()])
    cypher = f"MATCH (n:{label}) WHERE {where} RETURN {cols}"
    return QueryPlan("seed_lookup", cypher, params, note=f"seed existence probe ({label})")


def node_plan(node_id: str, *, owner: str) -> QueryPlan:
    """정확한 id의 저장 필드만 읽는다. 이웃·발췌·배치는 조회하지 않는다."""
    from store.embedded_schema import node_table_specs

    if node_id.startswith("domain:"):
        name = node_id[len("domain:"):]
        # Domain은 무소유 구조다. 소유자가 쓰는 주제와 그 조상만 정확히 읽는다.
        # 근거: [[vinc.spec.dataplane §2.1]]
        branches = [
            f"MATCH (n:{label}) WHERE n.owner_id = $owner "
            "AND (n.domain = $name OR starts_with(n.domain, $prefix)) "
            "RETURN $name AS name"
            for label in ("Concept", "Record", "Episode", "Decision")
        ]
        return QueryPlan("node", " UNION ALL ".join(branches),
                         {"owner": owner, "name": name, "prefix": name + "/"})
    label, pk = _seed_table(node_id)
    # 열 목록은 스키마를 따른다. 종류에 없는 props나 유효구간을 만들어 내지 않는다.
    # 근거: [[vinc.spec.dataplane §2.2]]
    columns = node_table_specs()[label]["cols"]
    fields = ", ".join(f"n.{col} AS {'id' if col == pk else col}" for col in columns)
    if node_id.startswith("concept:doc."):
        from store.search_temporal import FIELDS
        dates = ", ".join(f"d.{key} AS _doc_{key}" for key in FIELDS)
        return QueryPlan("node", f"MATCH (n:{label}) WHERE n.{pk} = $node "
                         "AND n.owner_id = $owner OPTIONAL MATCH (d:_DocMeta) "
                         "WHERE d.doc_id = $doc AND d.owner_id = $owner "
                         f"RETURN {fields}, d.doc_id AS _doc_id, {dates}",
                         {"node": pkns.key(owner, node_id), "owner": owner,
                          "doc": node_id[len("concept:doc."):]})
    return QueryPlan("node", f"MATCH (n:{label}) WHERE n.{pk} = $node "
                     f"AND n.owner_id = $owner RETURN {fields}",
                     {"node": pkns.key(owner, node_id), "owner": owner})


def node(node_id: str, *, owner: str, cfg=None, store=None) -> dict:
    """한 노드를 저장된 모양으로 돌려준다. 없는 노드와 다른 소유자의 노드는 같다."""
    result = execute(node_plan(node_id, owner=owner), cfg, store=store)
    rows = result.pop("rows", [])
    if not result.get("executed"):
        return result
    result["found"] = bool(rows)
    result["node"] = None
    if rows:
        stored = dict(rows[0])
        doc_meta = None
        if "_doc_id" in stored:
            from store.search_temporal import FIELDS
            present = stored.pop("_doc_id") is not None
            values = {key: stored.pop("_doc_" + key, None) for key in FIELDS}
            if present:
                doc_meta = values
        if node_id.startswith("domain:"):
            stored = {"id": node_id, "kind": "domain", "name": stored["name"]}
        else:
            stored["kind"] = stored.get("type") or _seed_table(node_id)[0].lower()
        if "props" in stored:
            stored["props"] = _props_to_obj(stored["props"])
        from store.search_temporal import metadata
        # A domain is a namespace container, not dated evidence. Keep its exact read shape.
        # Other kinds retain their stored top-level columns; normalized dates are nested.
        if stored.get("kind") != "domain":
            stored["temporal"] = metadata(doc_meta if doc_meta is not None else stored)
            # Exact reads retain their single-query, neighbor-independent contract.
            # Context/brief/search assess lineage; this endpoint exposes stored dates only.
            stored["temporal"]["claim_status"] = "unassessed"
            stored["temporal"]["claim_assessment_scope"] = "stored_fields"
        result["node"] = stored
    return result


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
#
# ⚠ 2026-08-24 — `INHERITS_FROM` 이 빠져 있었다. 위 문장이 `DERIVES_FROM` 만 논해서
# 그때 함께 안 옮긴 것으로 보인다. 관례가 둘을 같은 모양으로 규정한다: "A inherits
# from B" 도 "A derives from B" 와 같이 **B 가 위**다. 빠져 있는 동안 rank 배치는
# 상속 사슬만 거꾸로 세우고 있었고, 같은 데이터를 좌측 트리와 반대로 읽는 그
# 2026-08-02 의 병이 한 관계에만 남아 있던 셈이다.
#
# ⚠ 이 표를 고치면 **기존 지도의 뜻이 바뀐다**(상속을 가진 그래프의 x 축 순서).
# 그래서 앱의 인과 원뿔(`app/src/spotlight.ts` `UPSTREAM_REVERSED`)과 **같은 값**을
# 유지해야 한다 — 한쪽만 옮기면 하이라이트가 배치를 반박한다.
# 근거: [[record:inherits-from-now-reverses-with-derives-from]]
_REVERSED_RELS = ("DERIVES_FROM", "INHERITS_FROM")


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
                     layout: str = "radial", topic_marks: bool = False,
                     cone: float = 1.0) -> list[dict]:
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
    ⚠ Concept 에는 `pinned` 열이 없어(embedded_schema) 개념 축에서는 무동작이다.

    ``topic_marks``: **주제 자신을 마크로 낸다**(hub 전용, 2026-08-14). 기본은 꺼짐
    이고 그것이 요지다 — 이 함수의 계약은 "준 노드를 배치한다"이지 "노드를 만든다"
    가 아니라서, 켜는 것은 지도 payload 를 조립하는 호출부의 결정이어야 한다.
    (테스트가 이 경계를 잡아 줬다: 기본으로 켰더니 배치 계약을 검사하는 네 건이
    입력보다 많은 노드를 받고 깨졌다.)"""
    import math
    GOLDEN = 2.399963229728653
    layout = _LAYOUT_ALIAS.get(layout, layout)
    if layout not in LAYOUTS:
        layout = "hub"
    adj: dict[str, set] = {}
    directed: dict[str, set] = {}
    #: Bridge 좌석의 재료(item 12) — CONNECTS 로 닿는 반대편. 방향은 늘
    #: Bridge→Concept 이지만 양쪽에 실어 둔다(어느 투영으로 와도 같은 답).
    connects: dict[str, set] = {}
    for e in edges or []:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)
        rel = e.get("type") or ""
        if rel == "CONNECTS":
            connects.setdefault(s, set()).add(t)
            connects.setdefault(t, set()).add(s)
        if rel in _DIRECTED_RELS:
            # 상류 → 하류로 정규화한다(위 _REVERSED_RELS 주석 참조).
            if rel in _REVERSED_RELS:
                directed.setdefault(t, set()).add(s)
            else:
                directed.setdefault(s, set()).add(t)
    if layout == "rank":
        # 순서 프리셋에는 주제 마크를 안 낸다. rank 의 좌표는 "무엇이 무엇보다
        # 먼저인가"를 말하는데 주제에는 그 자리가 없다 — 억지로 앉히면 없는 뜻을
        # 지어내는 것이 된다. 담는 관계는 hub 가 답한다.
        _global_rank(nodes, directed, math)
    else:
        # 원뿔 반각 = 기본값 × 사용자 배율. 상한이 π/2 인 이유: 반각이 90°를 넘으면
        # 원뿔이 부모의 **뒤쪽**까지 열려 자식이 부모보다 중심에 가까워진다. 그러면
        # "이 방향으로 계속 뻗어 나간다"는 이 배치의 문장 자체가 깨진다.
        cone0 = min(_CONE0 * max(_CONE_MIN, min(_CONE_MAX, cone)), math.pi / 2)
        _global_hub(nodes, math, GOLDEN, topic_marks, cone0,
                    connects=connects, adj=adj)
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
    out = list(_shared_prefix(paths))
    # 접두가 경로 전체와 같아지면(도메인이 하나뿐) 마지막 조각은 가지로 남긴다 —
    # 안 그러면 가지가 0개가 되어 전부 뿌리에 뭉친다.
    if len(out) == min(len(p) for p in real):
        out = out[:-1]
    return tuple(out)


def _shared_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    """경로들이 공유하는 접두 = **가장 깊은 공통 조상**, 손대지 않은 그대로.

    `_common_prefix` 와 갈라 둔 이유: 저쪽은 배치용이라 "접두가 경로 전체와 같으면
    마지막 조각을 되돌려준다"는 가지 보존 규칙이 붙는다. 조상을 묻는 쪽
    (`_derive_bridge_domains`)에는 그 규칙이 틀린 답을 준다 — `vinc/security` 하나만
    있으면 조상은 `vinc/security` 자신이지 `vinc` 가 아니다.

    빈 경로(도메인 없는 노드)는 계산에서 뺀다 — `_common_prefix` 와 같은 규약이다."""
    real = [p for p in paths if p]
    if not real:
        return ()
    out: list[str] = []
    for i in range(min(len(p) for p in real)):
        seg = real[0][i]
        if not all(p[i] == seg for p in real):
            break
        out.append(seg)
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
    """가지 안 순서 — **가지가 이름순으로 먼저, 그다음 연결 많은 개념**.

    앞에 오는 것이 원뿔/부채꼴에서 축에 가까운 자리를 받는다. 개념은 degree
    내림차순이라 가지 안에서 "중심순"이 그대로 산다(D32).
    반환 원소는 (weight, kind, key, payload) — kind 로 재귀/배치를 가른다."""
    #: 가지 순서는 **이름**이다. 무게로 세우면 노드 하나가 들어와 순위가 뒤집힐 때
    #: 그 아래 전부가 자리를 옮긴다 — 실측으로 이것 하나가 순번 띠를 통째로 무효화했다.
    subs = sorted(b.kids.values(), key=lambda k: k.seg)
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
#: 사용자 배율의 허용 범위(2026-08-16). 1.0 이 여태의 그림이다. 아래로는 0.3 —
#: 그보다 좁으면 가지가 한 줄기로 뭉쳐 위계가 안 읽힌다. 위로는 1.4 인데 실효 상한은
#: π/2 라(위 주석) 1.37 부터는 더 열려도 그대로다. 그 둘을 굳이 맞추지 않는 이유는
#: 상한의 근거가 다르기 때문이다: 하나는 취향의 끝이고 하나는 기하의 끝이다.
_CONE_MIN, _CONE_MAX = 0.3, 1.4


def _mean_angle(angles: list[float], math):
    """각의 평균은 **단위벡터의 평균**이다 — 0 과 2π 가 같은 각이라 산술평균은
    0.1 과 6.2 의 중간을 3.15(정반대)로 답한다. 합이 사실상 0 이면(정확히 갈라선
    이웃들) 선호가 없다는 뜻이라 None 을 돌려준다."""
    if not angles:
        return None
    cx = sum(math.cos(a) for a in angles)
    cy = sum(math.sin(a) for a in angles)
    if cx * cx + cy * cy < 1e-12:
        return None
    return math.atan2(cy, cx)


def _arc_gap(a: float, b: float, math) -> float:
    """두 각 사이의 원둘레 거리(0..π). 감싸기를 넘어서도 짧은 쪽을 잰다."""
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def _cluster(items: list[dict], radius: float, math, center_w: float = 0.0,
             adj: dict | None = None) -> list:
    """무리 안 배치 — **규칙적 동심 고리 + 크기에 비례한 간격**. (dx, dy) 목록을 돌려준다.

    두 번 고쳐서 여기까지 왔다(둘 다 발주자 지적).
      ① 황금각 필로택시스는 고르지만 **규칙적으로 보이지 않는다**(2026-08-15). 눈이
         잡을 격자가 없어 잘 흩어진 무작위로 읽힌다. 동심 고리는 고르기를 유지하면서
         "고리"와 "등각"이라는 두 규칙을 눈에 보이게 만든다.
      ② 등각만으로는 **큰 노드가 뭉쳐 보인다**(2026-08-15). 마크 반지름은 √연결수에
         비례하는데(D32) 자리는 균등하니, 연결 많은 것끼리 이웃하면 그 둘만 붙는다.

    ②의 처방이 이 함수의 요지다. 절대 px 는 서버가 모르지만 **상대 비율**은 화면과
    같으므로(둘 다 √연결수), 이웃 두 마크의 평균 크기에 비례해 각 간격을 나눈다.
    큰 것 옆은 더 벌어지고 작은 것끼리는 촘촘해진다. 고리 반경도 같은 요구량으로
    정한다 — 큰 것이 많이 앉은 고리는 그만큼 바깥으로 밀린다.

    ⚠ **중심 자리는 비운다.** 거기에는 이미 그 주제의 마크가 서 있다(D86) — 잎을
    하나 앉히면 마크와 정확히 겹치고, 하필 연결이 가장 많은 것이 거기 가므로 가장
    큰 마크 둘이 포개진다. 처음에 7개 이상일 때 중심을 쓰게 짰다가 실측에서 이걸
    봤다. 대신 마크의 크기(`center_w`)를 첫 고리의 요구량에 더해, 마크가 클수록
    첫 고리가 바깥으로 밀리게 한다.

    ``adj``(2026-08-22, item 13): 고리의 **슬롯 기하는 그대로 두고 누가 어디 앉는지만**
    고른다. degree 순으로 앉히다가, 이미 앉은 그래프 이웃이 있으면 그 이웃들의 평균
    각에 가장 가까운 **빈 슬롯**을 집는다. 이웃이 없으면 순서대로 앞 슬롯이므로
    **내부 엣지가 없는 무리는 좌표가 한 톨도 안 바뀐다**. 고리 반경과 크기 비례
    간격(2026-08-15 두 수정)은 이 앞에서 이미 정해져 있어 손대지 않는다.

    순수 함수라 결정론이다(D17)."""
    n = len(items)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]
    #: 화면 크기 축과 같은 것 — 반지름 ∝ √연결수. 1.0 은 고립 노드의 바닥 크기.
    w = [1.0 + math.sqrt(max(0.0, float(it.get("degree") or 0))) for it in items]
    out: list = [(0.0, 0.0)] * n
    rest = n
    rings = 1
    while 3 * rings * (rings + 1) < rest:
        rings += 1
    tot = rings * (rings + 1) // 2
    # 1단계 — 고리 멤버와 각 고리의 **요구량**(간격 합). 반경은 이걸로 정한다.
    plan, start, demand = [], 0, []
    for k in range(1, rings + 1):
        cnt = rest * (k * (k + 1) // 2) // tot - start
        if cnt <= 0:
            continue
        mem = list(range(start, start + cnt))
        gaps = [(w[mem[j]] + w[mem[(j + 1) % cnt]]) / 2 for j in range(cnt)]
        plan.append((mem, gaps))
        # 첫 고리만 마크 크기를 함께 진다(위 ⚠ 주석).
        demand.append((sum(gaps) or 1.0) + (1.0 + math.sqrt(max(0.0, center_w))
                                            if not plan[:-1] else 0.0))
        start += cnt
    # 2단계 — 고리 반경은 **두 요구 중 큰 쪽**이다.
    #   · 각(angular): 그 고리의 간격 합을 둘레로 담으려면 최소 이만큼 필요하다
    #   · 지름(radial): 앞 고리와 마크 크기만큼은 떨어져야 한다
    # 누적 비율만 쓰던 판이 여기서 틀렸다(실측): 큰 것이 앉은 안쪽 고리가 짧아,
    # 가장 큰 마크들이 오히려 가장 좁게 붙었다. 이제 묶이는 것은 **고리 사이 거리**다.
    radii, prev_r, prev_w = [], 0.0, 1.0 + math.sqrt(max(0.0, center_w))
    for mem, gaps in plan:
        mw = max(w[m] for m in mem)
        angular = (sum(gaps) or 1.0) / (2 * math.pi)
        radial = prev_r + 0.55 * (prev_w + mw) / 2
        rr = max(angular, radial)
        radii.append(rr)
        prev_r, prev_w = rr, mw
    scale = radius / (radii[-1] or 1.0)
    idx = {str(it.get("id")): i for i, it in enumerate(items)}
    seated: dict[int, float] = {}          # 이미 앉은 항목 → 중심 기준 각
    for ring_i, ((mem, gaps), rr0) in enumerate(zip(plan, radii)):
        rr = rr0 * scale
        gsum = sum(gaps) or 1.0
        # 고리마다 반 칸 돌린다. 안 돌리면 모든 고리의 첫 자리가 θ=0 에 서서
        # 중심을 지나는 **한 줄**이 생긴다(실측: 한 덩어리의 세 마크가 y 가 같았다).
        off = math.pi / len(mem) if len(mem) > 1 else 0.0
        slots, acc = [], 0.0
        for g in gaps:
            slots.append(off * (ring_i % 2) + 2 * math.pi * acc / gsum)
            acc += g
        free = list(range(len(slots)))
        for m in mem:
            pick = 0                       # 이웃이 없으면 앞 슬롯 = 예전 그대로
            if adj:
                # 이웃 id 를 정렬해 읽는다 — adj 값은 set 이라 순회 순서가
                # 프로세스마다 다르고, 평균의 덧셈 순서까지 흔들리면 순수성이 깨진다.
                aim = _mean_angle(
                    [seated[idx[nb]] for nb in sorted(adj.get(str(items[m].get("id")), ()))
                     if nb in idx and idx[nb] in seated], math)
                if aim is not None:
                    pick = min(range(len(free)),
                               key=lambda k: (round(_arc_gap(slots[free[k]], aim, math), 9), k))
            th = slots[free.pop(pick)]
            seated[m] = th
            out[m] = (rr * math.cos(th), rr * math.sin(th))
    return out


def _shell(depth: int) -> float:
    if depth < len(_R_BRANCH):
        return _R_BRANCH[depth]
    return _R_BRANCH[-1] + 0.16 * (depth - len(_R_BRANCH) + 1)


def _global_hub(nodes: list[dict], math, GOLDEN, topic_marks: bool = False,
                cone0: float = _CONE0, connects: dict | None = None,
                adj: dict | None = None) -> None:
    """Hub — **중심에서 터져 나가는 위계**(D74, 구 동심 배치 개정).

    뿌리(이 그래프의 공통 도메인 = 지금은 `vinc`)가 원점이고, 도메인이 사방으로
    뻗고, 그 도메인이 향한 **그 방향으로** 하위 개념이 다시 퍼진다. 폭발의 잔해가
    한 방향으로 계속 날아가는 그림이다.

    구 배치는 degree 하나로 동심원을 그렸다. 그때는 도메인이 평평한 이름이라 위계가
    없었지만 지금은 `vinc/architecture` 처럼 경로다 — 좌표가 그 구조를 안 실으면
    "무엇이 무엇 아래인가"를 화면이 답하지 못한다. 중심성은 버리지 않는다: 가지
    **안에서** degree 순으로 축에 가까운 자리를 준다(D32 는 이제 가지 단위로 산다).

    3D: 자식 방향 = 부모 방향 주위 **원뿔** 안. 극각 θ ∝ √(형제 순번 비율)이고
    방위각은 황금각이라 어느 개수에서도 겹치지 않는다. 순번은 degree 내림차순이라
    연결 많은 것이 축 가까이 앉는다(D32).

    2D: 같은 트리를 **부채꼴**로 — 자식마다 같은 폭의 각도 구간을 나눠 갖고 그
    구간 안에서 다시 나눈다. 원뿔의 평면 대응물이라 3D 와 같은 이야기를 한다.
    투영이 아니므로 z 평탄화에 따른 겹침이 **원리적으로** 생기지 않는다.

    ⚠ 띠도 부채꼴도 **무게가 아니라 순번**으로 나눈다. 무게로 나누면 어디에 노드
    하나가 들어와도 형제 전부의 좌표가 바뀌어 지도가 매번 다시 깔린다 — 결정론이긴
    해도 삽입에 안정적이지 않다. 대가는 D74 의 무게 비례 인코딩이고(가지 크기는
    주제 마크 크기로 남는다), 얻는 것은 삽입이 그 가지 안에서 끝나는 것이다.
    근거·기각안: [[decision:the-hub-layout-gives-up-weight-proportional-spread-so-that-adding-a-node-moves-nothing]]
    실측: [[record:the-layout-becomes-insertion-stable-when-sibling-bands-come-from-rank-and-branches-are-ordered-by-name-2026-09-19]]

    Bridge 는 이 트리의 잎이 **아니다**(item 12) — 소속이 저작되지 않고 연결로
    정해지므로 끝점 가지들의 평균 방향에 따로 앉힌다(`seat_bridges`). 트리에서
    빼는 이유: 잎으로 두면 가지 무게에 셈해져 다른 노드 자리를 밀어 놓고 정작 그
    자리는 쓰지 않는다. ``connects`` 가 없으면(엣지 없는 호출) 예전 그대로다.

    순수 함수(난수·시간 무의존, D17)."""
    #: Bridge id → CONNECTS 반대편 중 **이 배치에 실제로 있는 것**. 비면 미분류라
    #: 보통 잎처럼 뿌리에 남는다(지어낼 방향이 없다).
    ends: dict[str, list[str]] = {}
    if connects:
        present = {n.get("id") for n in nodes}
        for n in nodes:
            if n.get("kind") != "bridge":
                continue
            arms = sorted(t for t in connects.get(n.get("id")) or () if t in present)
            if arms:
                ends[str(n.get("id"))] = arms
    tree_nodes = [n for n in nodes if str(n.get("id")) not in ends]
    root = _build_tree(tree_nodes)
    base = _common_prefix([_domain_path(n.get("domain") or "") for n in tree_nodes])
    #: 가지 기하 — 경로 → (3D 단위 방향, 껍질 반경, 2D 각). Bridge 좌석의 재료다.
    seats: dict[tuple, tuple] = {}
    #: 이번 배치에서 낼 주제 마크. 다 돌고 나서 한 번에 붙인다 — 도는 중에 넣으면
    #: 트리를 만든 그 목록이 순회 중에 자란다.
    marks: list[dict] = []

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

    def place(b: _Branch, depth: int, d3, a0: float, a1: float, cone: float,
              path: tuple = ()) -> None:
        seats[path] = (d3, _shell(depth), (a0 + a1) / 2, a1 - a0)
        # 주제 자신을 **마크로 낸다**(2026-08-14). 여태 가지는 좌표 계산에만 쓰이고
        # 화면에는 잎만 나갔다 — 그래서 "vinc 아래 design" 이라는 구조가 지도에서
        # 위치로만 암시되고 짚을 것이 없었다. id 는 저장소의 진짜 주제 노드와 같은
        # `domain:<경로>` 라 클릭·추적이 그대로 해석된다(GM-12 · D16 의 L0→L1).
        # 반경은 이 가지 자신의 껍질이고 뿌리는 _shell(0)=0 이라 정확히 원점이다.
        if path and topic_marks:
            mark = {
                "id": "domain:" + "/".join(path),
                "label": path[-1],
                "domain": "/".join(path[:-1]),
                "kind": "domain",
                "degree": b.weight,      # 아래에 달린 총수 = 크기 인코딩(√연결수와 같은 축)
                "props": {}, "tags": [], "ts": "",
            }
            rb = _shell(depth)
            mark["x"] = round(d3[0] * rb, 6)
            mark["y"] = round(d3[1] * rb, 6)
            mark["z"] = round(d3[2] * rb, 6)
            mark["x2"] = round(math.cos((a0 + a1) / 2) * rb, 6)
            mark["y2"] = round(math.sin((a0 + a1) / 2) * rb, 6)
            marks.append(mark)
        kids = _ordered_children(b)
        r_here = _shell(depth)
        span = a1 - a0
        # 2D — 이 가지 자신의 자리. 잎은 **그 점 둘레에 뭉친다**(아래 주석).
        bx2 = math.cos((a0 + a1) / 2) * r_here
        by2 = math.sin((a0 + a1) / 2) * r_here
        # 이 가지의 잎을 **한 번에** 배치한다 — 간격이 이웃 크기에 달려 있어
        # (_cluster) 하나씩 독립으로는 정할 수 없다.
        leafnodes = [p2 for _w, k, _k2, p2 in kids if k == "node"]
        disc = min(_R_LEAF * 0.85, 0.28 * max(r_here, 0.3) * span)
        spots = _cluster(leafnodes, disc, math, float(b.weight), adj=adj)
        leaf_i = 0
        for i, (_w, kind, _key, payload) in enumerate(kids):
            mid = (i + 0.5) / len(kids)            # 순번 비율(0~1) — 띠·부채꼴·반경 램프
            # 3D — 뿌리는 구면 전체로, 그 아래는 부모 방향 주위 원뿔 안으로.
            # (원뿔에서는 순번이 앞쪽 = 축 근처일수록 θ 가 작다 = 연결 많은 것이 안쪽.)
            nd3 = (sphere(mid, i) if depth == 0
                   else spray(d3, cone * math.sqrt(mid), i * GOLDEN))
            # 2D — 부채꼴: 형제 수로 등분한다.
            b0 = a0 + span * (i / len(kids))
            b1 = a0 + span * ((i + 1) / len(kids))
            if kind == "branch":
                place(payload, depth + 1, nd3, b0, b1, cone * 0.58, path + (payload.seg,))
                continue
            n = payload
            # 잎 — 자기 가지 껍질 **바깥**으로. 순번이 뒤일수록 더 멀리(중심순 유지).
            rr = r_here + _R_LEAF * (0.30 + 0.70 * math.sqrt(mid))
            n["x"] = round(nd3[0] * rr, 6)
            n["y"] = round(nd3[1] * rr, 6)
            n["z"] = round(nd3[2] * rr, 6)
            # 2D 는 잎을 자기 가지 둘레에 **뭉친다**(3D 는 깊이가 이미 갈라 주므로
            # 안 건드린다). 배치는 황금각 필로택시스 + √ 반경 램프라 개수와 무관하게
            # 고르고 결정론이며, 중심순은 그대로다.
            # ⚠ 원반 크기는 **가지의 각도 폭에 맞춘다**(상수 금지) — 그리고 계수는
            # 넉넉해야 한다: 간신히 안 닿는 값은 화면에서 한 덩어리로 읽힌다.
            # 근거·실측: [[record:planar-leaves-cluster-because-arms-read-as-a-vortex]].
            dx, dy = spots[leaf_i]
            leaf_i += 1
            n["x2"] = round(bx2 + dx, 6)
            n["y2"] = round(by2 + dy, 6)

    def seat_bridges() -> None:
        """Bridge 좌석(item 12) — 끝점이 **속한 가지**들의 평균 방향, 반경은 그
        가지 껍질들의 평균. `vinc/security` 와 `vinc/engineering` 을 잇는 브릿지는
        두 가지 사이를 가리키며 그 껍질 높이에 선다.

        같은 가지 짝을 잇는 브릿지가 여럿이면 그 좌석 둘레에서 `_cluster` 의 고리
        규칙으로 벌린다 — 겹친 둘은 화면에서 하나가 사라진 것과 같다. 짝 안 순서는
        잎과 같은 (degree 내림, id)라 결정론이다(D32).

        ⚠ 끝점이 **한 가지 안**에만 있는 브릿지는 좌석이 그 가지의 주제 마크와
        같은 점이 된다(마크도 `d3 × _shell(depth)` 에 선다). 평면 쪽은
        `_dedupe_planar` 가 마지막에 밀고, 3D 는 그대로 겹친다 — 계획이 정한 반경을
        지키는 편을 택했다. 근거: [[record:bridge-layout-clump-2026-08-22]].

        입력이 그래프뿐이라 순수 함수다(D17)."""
        if not ends:
            return
        dom_of = {str(n.get("id")): n.get("domain") or "" for n in nodes}

        def key_of(dom: str) -> tuple:
            """도메인 → 실재하는 가지 경로. 트리에 없는 깊이는 위로 접어 올린다."""
            p = _domain_path(dom)
            while p and p not in seats:
                p = p[:-1]
            return p if p in seats else base

        groups: dict[tuple, list[dict]] = {}
        for n in nodes:
            arms = ends.get(str(n.get("id")))
            if arms:
                groups.setdefault(
                    tuple(sorted({key_of(dom_of.get(t, "")) for t in arms})), []).append(n)
        for k in sorted(groups):
            grp = sorted(groups[k], key=lambda n: (-int(n.get("degree") or 0),
                                                   str(n.get("id"))))
            arms3 = [seats[p] for p in k]
            sx, sy, sz = (sum(s[0][i] for s in arms3) for i in range(3))
            L = math.sqrt(sx * sx + sy * sy + sz * sz)
            # 정반대로 갈라진 가지들은 합이 0 이다 — 그런 짝에는 '사이'라는 방향이
            # 실제로 없으므로 첫 가지 방향을 쓴다(결정론이 목적).
            d3 = (sx / L, sy / L, sz / L) if L > 1e-9 else arms3[0][0]
            rr = sum(s[1] for s in arms3) / len(arms3)
            cx = sum(math.cos(s[2]) for s in arms3)
            cy = sum(math.sin(s[2]) for s in arms3)
            # 2D 각도 평균도 **단위벡터의 평균**이다(0 과 2π 가 같은 각이라 산술평균은 틀린다).
            ang = math.atan2(cy, cx) if cx * cx + cy * cy > 1e-18 else arms3[0][2]
            (ux, uy, uz), (vx, vy, vz) = basis(d3)
            # 벌림 폭은 **잎 원반과 같은 규칙**이다(`place` 의 disc) — 반경은 이
            # 좌석의 것을, 각도 폭은 끝점 가지들의 평균을 쓴다. 상한을 그대로
            # 쓰면(0.85) 가지 껍질만 한 폭이 되어 브릿지가 가지 밖으로 튄다(실측).
            disc = min(_R_LEAF * 0.85,
                       0.28 * max(rr, 0.3) * (sum(s[3] for s in arms3) / len(arms3)))
            for n, (ox, oy) in zip(grp, _cluster(grp, disc, math)):
                n["x"] = round(d3[0] * rr + ux * ox + vx * oy, 6)
                n["y"] = round(d3[1] * rr + uy * ox + vy * oy, 6)
                n["z"] = round(d3[2] * rr + uz * ox + vz * oy, 6)
                n["x2"] = round(math.cos(ang) * rr + ox, 6)
                n["y2"] = round(math.sin(ang) * rr + oy, 6)

    # 뿌리의 3D 기준 방향은 +z 로 잡고 첫 원뿔을 거의 반구까지 연다(_CONE0).
    # 2D 는 뿌리에서 한 바퀴(2π)를 통째로 나눠 준다.
    #
    # 뿌리의 경로는 벗겨 낸 **공통 접두**다(_common_prefix). 전부 `vinc/*` 면 `vinc`
    # 하나가 원점에 서고, 주제가 둘 이상 갈리면(예: `vinc/*` 와 `neo4j/*`) 접두가
    # 비어 뿌리 마크가 없다 — 그때는 이름 붙일 하나의 뿌리가 실제로 없기 때문이다.
    place(root, 0, (0.0, 0.0, 1.0), 0.0, 2 * math.pi, cone0, base)
    seat_bridges()           # 가지 기하(seats)가 다 찬 뒤라야 평균을 낼 수 있다
    nodes.extend(marks)      # 배치가 끝난 뒤 한 번에(marks 선언부 주석)


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


def _order_graph_result(shaped: dict) -> None:
    """계산된 값은 그대로 두고 그래프 응답의 배열 순서만 고정한다."""
    shaped["nodes"].sort(key=lambda n: n["id"])
    # 같은 끝점·타입의 관계도 role/evidence/confidence 등 전체 값으로 동률을 푼다.
    shaped["edges"].sort(key=lambda e: (
        e["source"], e["target"], e["type"],
        json.dumps(e, sort_keys=True, ensure_ascii=False, separators=(",", ":"))))


def graph(*, owner: str, domain: str | None = None, seed: str | None = None,
          depth: int = 2, layout: str = "radial", basis: str = BASIS_RECORDED,
          domains: list[str] | None = None, cone: float = 1.0,
          max_nodes: int | None = None, select: str = "layers",
          cfg=None, store=None) -> dict:
    """지식 그래프 — 기본은 owner(±domain) 스코프 전체(View L1), ``seed`` 지정 시
    seed+depth ego 서브그래프(View L2 = MCP `vinc_graph` 동일 경로). 노드에는
    결정론 레이아웃 좌표(x,y,z)가 포함된다(D17) — ``layout`` 프리셋은 D34
    (radial·circle·tree·organic·rank·dendro, 미지 값 radial 강등).

    F2(2026-07-19): Concept 단일 축 해소 — 축 플랜 3종(Bridge·Episode·Record)이
    합류해 부착 노드·엣지가 렌더된다. L2 에선 매개자(브릿지 등)가 keep 집합에
    편입돼 '보이지 않는 접착제'(kept 개념 둘이 엣지 없이 떠 있던 문제)가 사라진다.
    entity: 시드는 전용 순회(TYPED 폐쇄 성분 — :func:`entity_subgraph_ids_plan`).

    decision: 시드는 시드 칸만 Decision 으로 바꿔 같은 도달 집합 플랜을 탄다(개념
    시드와 같은 모양으로 퍼진다). 의미 관계는 :func:`decision_relation_rows_plan`이
    기존 도달 집합에 한 번 합류한다(새 이웃을 다시 순회하지 않는다).

    ``max_nodes`` — 노드 예산(개념 도달 집합 기준). **주는 호출만** 영향을 받는다:
    도달 집합이 예산을 넘으면 깊이를 층 단위로 줄여(최소 1) 그 층까지만 싣고
    ``depth_budget`` 으로 무엇을 줄였는지 말한다. 층 중간을 자르지 않는 이유는 "몇
    홉까지 다 봤다"는 말이 참으로 남아야 해서다. 주지 않으면(View L2·공유 팩·동기화)
    플랜도 결과도 종전과 같다. 예산 값은 MCP 핸들러가 정한다.
    ``select="prize"`` 면 같은 경우에 깊이를 두고 개념을 ppr 로 고른다(:func:`_prize_pick`,
    단위는 같다 — 시드 + 개념 ``max_nodes``). 무엇을 했는지는 ``selection`` 이 말하고,
    노드의 ``ppr_recent`` 도 이때만 붙는다.
    근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]"""
    if seed:
        # SEC-1: seed 갈래도 owner 스코프를 지킨다. 여태 owner 를 받고도 쓰지 않아
        # 호스티드에서 남의 서브그래프가 그대로 보였다(vinc_graph 는 seed 필수).
        # entity 판정은 접두사 라우팅(_seed_table) — 개방층 id 는 임의 접두사(F1).
        seed_label = _seed_table(seed)[0]
        entity_seed = seed_label == "Entity"
        decision_seed = seed_label == "Decision"
        budget: dict | None = None
        if entity_seed:
            ids_res = execute(entity_subgraph_ids_plan(seed, depth=depth, owner=owner),
                              cfg, store=store)
        elif max_nodes and int(depth) > 1:
            ids_res = execute(subgraph_hops_plan(
                seed, depth=depth, owner=owner,
                seed_label="Decision" if decision_seed else "Concept"), cfg, store=store)
        elif decision_seed:
            ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner,
                                                seed_label="Decision"), cfg, store=store)
        else:
            ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner),
                              cfg, store=store)
        if not ids_res.get("executed"):
            return {"kind": "graph", "executed": False, "seed": seed, "depth": depth,
                    "nodes": [], "edges": [], "note": ids_res.get("note", "")}
        reach_rows = [r for r in ids_res.get("rows", []) if r.get("id")]
        # `select="prize"`: 층 자르기가 깊이를 줄일 바로 그 경우(시드 뺀 개념 도달 집합이
        # 예산을 넘을 때)에, 깊이는 두고 개념을 상금(ppr)으로 고른다. 단위는 층 자르기와 같다
        # (시드 + 개념 `max_nodes`). 고른 뒤에는 아래 행 조회·축 합류가 층 자르기와 똑같이
        # 고른 개념에만 돈다 — 부착(레코드·에피소드·브릿지·결정)은 예산 밖이다.
        # 결정 시드는 층 자르기로 간다: 결정↔개념 엣지는 순위 그래프(개념↔개념)에 없고, 시드
        # 자신의 엣지가 고리 전체를 예산 밖에서 다시 싣는다(아래 의미 관계 합류).
        # 개념으로 이어진 후보가 예산 안일 때도 층 자르기로 간다(:func:`_prize_pick`).
        selection: dict | None = None
        prized = False
        if (select == "prize" and max_nodes and int(depth) > 1 and not entity_seed
                and len(reach_rows) > int(max_nodes)):
            picked: list[str] | None = None
            if decision_seed:
                why = "prize selection ranks from concept seeds only"
            elif len(reach_rows) > PRIZE_REACH_CAP:
                why = (f"reach {len(reach_rows)} is over the prize-selection cap of "
                       f"{PRIZE_REACH_CAP}")
            else:
                picked, why = _prize_pick(seed, reach_rows, int(max_nodes), owner=owner,
                                          cfg=cfg, store=store)
            if picked:
                keep_ids = set(picked)
                selection = {"method": "prize", "max_nodes": int(max_nodes),
                             "reach": len(reach_rows), "kept": len(keep_ids - {seed}),
                             "note": why}
                reach_rows = [r for r in reach_rows if r["id"] in keep_ids]
                prized = True
            else:
                selection = {"method": "layers", "requested": "prize",
                             "note": f"{why}, so depth was cut by layer"}
        if max_nodes and int(depth) > 1 and not entity_seed and not prized:
            want = max(1, min(int(depth), 4))
            hops = {r["id"]: int(r.get("hops") or 1) for r in reach_rows}
            served = 1
            for k in range(1, want + 1):
                if sum(1 for h in hops.values() if h <= k) <= int(max_nodes):
                    served = k
                else:
                    break
            if served < want:
                budget = {"requested_depth": want, "served_depth": served,
                          "reach_at_requested": len(hops),
                          "reach_served": sum(1 for h in hops.values() if h <= served),
                          "max_nodes": int(max_nodes)}
                reach_rows = [r for r in reach_rows if hops[r["id"]] <= served]
                depth = served
        ids = sorted({seed, *(r["id"] for r in reach_rows)})
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
            for plan_fn in _GRAPH_READ_PLANS:
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
                return {"kind": "graph", "executed": False, "seed": seed, "depth": depth,
                        "nodes": [], "edges": [], "note": seed_res.get("note", "")}
            seed_row = next((r for r in seed_res.get("rows", []) if r.get("id") == seed), None)
            found = seed_row is not None
            if found:
                kind = seed.split(":", 1)[0] if ":" in seed else "concept"
                shaped["nodes"] = [{"id": seed, "label": seed_row.get("title") or seed,
                                    "domain": seed_row.get("domain") or "",
                                    "kind": kind, "degree": 0,
                                    "props": _props_to_obj(seed_row.get("props")),
                                    "tags": list(seed_row.get("tags") or []),
                                    "ts": seed_row.get("ts") or "",
                                    "content_ts": seed_row.get("content_ts") or ""}]
        # E-G2 — 시드 관점 개인화 랭킹(ppr). layout 전에 부여(순수 메타, 좌표 무관).
        # `ppr_recent` 는 `prize` 를 요청했을 때만 싣는다(기본 출력은 그대로).
        attach_ppr(shaped, [seed], recent=select == "prize")
        # 브릿지 소속은 파생값이다 — 배치보다 **먼저** 확정해야 hub·rank 가 같은
        # 도메인을 읽는다(_derive_bridge_domains 주석).
        _derive_bridge_domains(shaped)
        # 씨앗이 있는 화면(L2, 한 노드의 이웃)에는 주제 마크를 안 낸다. 거기서의
        # 주어는 그 노드이고 담는 관계는 지도 **전체**를 볼 때의 뼈대라, 초점 화면에
        # 끼우면 ego 이웃이 아닌 것이 이웃인 척한다(테스트 넷이 이걸 잡았다).
        shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                           cone=cone)
        used = _apply_basis(shaped, basis)
        _order_graph_result(shaped)
        if not found:
            cands = near_nodes(store, seed, owner=owner)
            return {"kind": "graph", "executed": executed, "seed": seed, "depth": depth,
                    "found": False, "basis": used,
                    "error": f"unknown seed_id: {seed}",
                    "hint": ("seed_id is an exact, type-prefixed node id "
                             "(e.g. `concept:view`). "
                             + ("Did you mean: " + ", ".join(c["id"] for c in cands)
                                + "? " if cands else "")
                             + "Use vinc_search to find the id first."),
                    **({"candidates": cands} if cands else {}), **shaped}
        return {"kind": "graph", "executed": executed, "seed": seed, "depth": depth,
                "found": found, "basis": used,
                **({"depth_budget": budget} if budget else {}),
                **({"selection": selection} if selection else {}), **shaped}
    doms = normalize_domains(domain, domains)
    res = execute(graph_plan(owner=owner, domains=doms), cfg, store=store)
    rows = list(res.get("rows", []))
    # F2 — 부착 축 합류(L1/전체 뷰). 고정 순서 concat: Concept 축 선행 = 노드 dedup
    # 의 선착 우선이 Concept 투영을 이기게(결정론).
    for plan_fn in _GRAPH_READ_PLANS:
        ax = execute(plan_fn(owner=owner, domains=doms), cfg, store=store)
        if ax.get("executed"):
            rows += ax.get("rows", [])
    shaped = shape_graph(rows)
    _derive_bridge_domains(shaped)
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                       topic_marks=True, cone=cone)
    used = _apply_basis(shaped, basis)
    _order_graph_result(shaped)
    return {"kind": "graph", "executed": res.get("executed", False),
            "basis": used, **shaped}


def graph_preview(*, owner, domains=None, layout="hub", cone=1.0, basis="recorded", cfg=None, store=None):
    """A bounded real Concept subgraph, read inside the continuation's transaction.

    This is explicitly partial: no full-population metrics or fabricated edges.
    Other axes arrive with the unchanged final graph response.
    """
    where, params = ["c.owner_id = $owner"], {"owner": owner}
    doms = normalize_domains(None, domains)
    if doms:
        where.append(domain_subtree_where(["c.domain"], doms, params))
    rows = execute(QueryPlan("graph_preview_nodes",
        "MATCH (c:Concept) WHERE " + " AND ".join(where) +
        " RETURN c.concept_id AS id, c.title AS label, c.domain AS domain, "
        "coalesce(c.created, c.updated_at, '') AS ts, "
        "coalesce(c.content_time, '') AS content_ts ORDER BY c.concept_id LIMIT 128",
        params), cfg, store=store)
    if not rows.get("executed"):
        return {"executed": False}
    nodes = list(rows.get("rows", []))
    if not nodes:
        return {"executed": True, "nodes": [], "edges": []}
    ids = pkns.keys(owner, [n["id"] for n in nodes])
    edges = execute(QueryPlan("graph_preview_edges",
        "MATCH (c:Concept)-[r]->(d:Concept) WHERE c.owner_id = $owner AND d.owner_id = $owner "
        "AND c.concept_id IN $ids AND d.concept_id IN $ids "
        "RETURN c.concept_id AS id, d.concept_id AS target, label(r) AS rel "
        "ORDER BY c.concept_id, d.concept_id, label(r) LIMIT 512",
        {"owner": owner, "ids": ids}), cfg, store=store)
    if not edges.get("executed"):
        return {"executed": False}
    shaped = shape_graph(nodes + list(edges.get("rows", [])))
    shaped["nodes"] = layout_positions(shaped["nodes"], shaped["edges"], layout,
                                       topic_marks=False, cone=cone)
    used = _apply_basis(shaped, basis)
    _order_graph_result(shaped)
    return {"kind": "graph_preview", "executed": True, "partial": True, "basis": used, **shaped}


def domain_ties_plan(*, owner: str) -> QueryPlan:
    """View L0 결합 행렬(D16) — 도메인이 다른 Concept 간 직접 엣지를 도메인 쌍으로 집계."""
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


def view_l0(*, owner: str, cfg=None, store=None) -> dict:
    """View L0 Domain View 데이터(D16) — 도메인별 레코드 수(Episode ∪ Concept
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
# View Totals 관점의 데이터 원천. 두 플랜(출처 축·연결 축)을 각각 UNION ALL 로
# 가져와 **순수 파이썬에서 접는다**(`domain_counts_plan` → `domains` 와 동형) —
# Cypher 안에서 EXISTS{}·서브쿼리로 영리하게 굴지 않는다(임베디드 방언 이식성).
# 고아 판정을 `component_diagnosis` 에 맡기지 않는 것도 의도다 — 진단은 스토어를
# 두 번 더 읽고 강등 계약을 갖는데, 원장은 항상 그려져야 한다.

def ledger_origin_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
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
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if doms:
        # 원장은 지도와 **같은 모집단**을 세야 한다 — 지표바가 지도와 다른 수를
        # 말하면 화면이 스스로를 반박한다. 그래서 같은 하위 트리 판정을 쓴다.
        and_scope = " AND " + domain_subtree_where(["c.domain"], doms, params)
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = pkns.keys(owner, ids)
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


def ledger_link_plan(*, owner: str, domain: str | None = None, domains: list[str] | None = None,
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
    doms = normalize_domains(domain, domains)
    assert not (doms and ids), "domain and ids are mutually exclusive"
    params: dict = {"owner": owner}
    and_scope = ""
    if doms:
        # 원장은 지도와 **같은 모집단**을 세야 한다 — 지표바가 지도와 다른 수를
        # 말하면 화면이 스스로를 반박한다. 그래서 같은 하위 트리 판정을 쓴다.
        and_scope = " AND " + domain_subtree_where(["c.domain"], doms, params)
    if ids:
        and_scope = " AND c.concept_id IN $ids"
        params["ids"] = pkns.keys(owner, ids)
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
        # 부착 축은 결정에도 닿는다(F2). 원장 모집단은 개념이라 개념 끝점만 모은다.
        members.update(r["target"] for r in ax.get("rows", [])
                       if r.get("target") and r.get("target_kind", "concept") == "concept")
    return {"executed": True, "ids": sorted(members)}


#: 문서 목록의 응답별 상한. sources_offset 으로 다음 창을 읽는다.
_NODE_SOURCES_CAP = 50


def node_context(**kwargs) -> dict:
    """상세·요약도 검색과 같은 시간 메타를 보존한다."""
    from store import search_temporal as temporal
    store = kwargs.get("store")
    owns = store is None
    if owns:
        store = open_store(kwargs.get("cfg") or load_config())
    if store is None:
        return _node_context(**kwargs)
    try:
        res = _node_context(**{**kwargs, "store": store})
        if res.get("executed") and res.get("node_kind") != "domain":
            try:
                owner, nid = kwargs.get("owner"), kwargs["node"]
                source = temporal.node_sources(store, [nid], owner=owner).get(nid, res)
                temporal.stamp(res, source)
                candidates = [{"id": nid, "kind": source.get("kind", res.get("node_kind"))}]
                temporal.finish_nodes(store, candidates, owner=owner)
                res["temporal"] = candidates[0]["temporal"]
                excerpts = res.get("excerpts") or []
                docs = temporal.doc_sources(store, [r.get("doc_id") for r in excerpts], owner=owner)
                for row in excerpts:
                    temporal.stamp(row, docs.get(row.get("doc_id"), {}))
                temporal.finish_docs(store, excerpts, owner=owner)
                items = (res.get("links") or {}).get("items") or []
                temporal.finish_nodes(store, items, owner=owner)
                for item in items:
                    # F14 — 결정의 구간은 props 에 있어 관계 행의 두 칸이 비어 나갔다.
                    # 시간 메타가 이미 읽은 값으로 채운다(Record 는 열에서 이미 채웠다).
                    if str(item.get("id") or "").startswith("decision:"):
                        t = item.get("temporal") or {}
                        item["valid_from"] = str(t.get("valid_from") or "")
                        item["valid_until"] = str(t.get("valid_until") or "")
            except Exception as exc:
                temporal.stamp(res, res)
                res["partial"] = True
                res["temporal_error"] = f"{type(exc).__name__}: {exc}"
            res["validity_basis"] = "legacy_visibility"
            res["claim_validity"] = res["temporal"]["validity"]
        return res
    finally:
        if owns:
            store.close()


def _node_context(*, owner: str, node: str, limit: int = 4,
                 with_evidence: bool = False, links_offset: int = 0,
                 sources_offset: int = 0, cfg=None, store=None) -> dict:
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

    반환: {executed, node, node_kind, title, domain, one_liner, rev, updated_at, props,
           valid_from, valid_until, validity('current'|'historical'|'future'), superseded_by,
           excerpts: [{doc_id, doc_title, section, excerpt, method}], arm,
           links: {degree(=out+in), out, in, attached(=degree 에 안 든 나머지),
                   total(=degree+attached · vinc_neighbors 의 count 와 같은 연결수),
                   offset, truncated, next_offset(더 있을 때만),
                   items: [{id, title, domain, type, dir, evidence, confidence, note,
                            valid_from, valid_until}]}}
           ``items`` 는 `links_offset` 부터 최대 `_NODE_LINKS_CAP` 줄이다. `truncated`
           가 참이면 `next_offset` 이 **이어 읽을 자리**를 준다 — 그것을 따라가면
           이웃 전체에 닿는다(`_window`). `total` 은 창과 무관하게 언제나
           이웃 전체 수다.

           sources: {total, offset, truncated, next_offset(더 있을 때만),
                     items: [{doc_id, title, mentions}]}
           sources_offset 부터 최대 50개씩 읽으며 next_offset 으로 이어 간다.
           이 노드에 딸린 **문서 전부**. ``excerpts`` 와 다른 계약이다 — 저쪽은 청크를
           `limit` 개로 자르므로 거기 보이는 문서는 "이 노드의 문서"가 아니라 "상위 몇
           청크가 우연히 속한 문서"다(실측: 문서 여섯 중 발췌에 뜬 것은 넷). 소스
           선택기는 이 목록을 써야 한다. MENTIONS 대상은 Concept/Decision 이며
           그 밖의 캐리어는 현재 스키마상 소스 목록이 비어 있다.
           ``note`` 는 부착(Record ATTACHES_TO)에서만 채워진다(카드 본문). 개념
           관계(out/in)와 Bridge/Episode 부착은 항상 빈 문자열.
           ``valid_from``/``valid_until`` 도 같은 신분(W13) — Record 부착만 값을
           갖고, 값이 있으면 그 기록의 유효구간이다(반개구간·YYYY[-MM[-DD]]).

    ``props`` 는 노드의 자유 속성(dict)이다. Episode 도 이 열을 갖고 여기서 읽는다
    (감사 F11 — 예전에는 고르지 않아 늘 {} 였다). Entity 는 열이 없어 {} 다.

    ``concept:doc.*`` 미러의 유효구간은 Concept 열이 아니라 그 문서(`_DocMeta`)의
    것이다(감사 F13). Concept 에는 구간 열이 없어서, 은퇴한 문서의 미러가 current 로
    읽혔다. :func:`node_plan` 이 같은 자리를 이미 그렇게 읽는다.

    Entity(개방 타입)는 제목·태그와 TYPED 관계만 낸다(감사 F12, node_kind="entity").
    발췌 FTS 폴백이 실패하면 ``excerpt_error`` 에 사유를 싣는다(조용히 삼키지 않는다).

    ``rev``/``updated_at`` 은 그 노드의 현재 버전이다(W3, 투영만) — 고쳐 쓰기 전에
    무엇을 읽었는지 붙들어 두라고 낸다. 쓰기 경로는 아직 이 값을 요구하지 않는다.
    근거: [[vinc.plan.mcp-agent-surface §6]]."""
    cfg = cfg or load_config()
    base: dict = {"kind": "node-context", "executed": False, "node": node,
                  "one_liner": "", "domain": "", "props": {}, "excerpts": [],
                  "arm": "none",
                  # 이 노드에 딸린 문서 전부(발췌 캡과 다른 계약). 못 읽었을 때도
                  # 키는 있다 — 없는 키와 0 은 다른 말이다.
                  "sources": {"total": 0, **_window([], sources_offset, _NODE_SOURCES_CAP)},
                  # W3 — 못 읽었을 때도 키는 있다. 없는 키와 0 은 다른 말이다.
                  "rev": 0, "updated_at": "",
                  # 노드 자신의 유효구간(부착 목록의 것과 다르다). 못 읽었으면 빈 값.
                  "valid_from": "", "valid_until": "", "validity": "",
                  "superseded_by": ""}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        lim = max(1, min(int(limit), 12))
        if node.startswith(domain_hierarchy.ID_PREFIX):
            return _domain_context(store, node, owner=owner, offset=links_offset, cfg=cfg)
        if _seed_table(node)[0] == "Entity":
            return _entity_context(store, node, base, owner=owner,
                                   with_evidence=with_evidence, offset=links_offset)
        if _seed_table(node)[0] not in (*_SEMANTIC_LABELS, "Record", "Episode"):
            exact = execute(node_plan(node, owner=owner), cfg, store=store)
            if not exact.get("executed"):
                return {**base, "status": "query_failed", "error": exact.get("error") or exact.get("note")}
            found = bool(exact.get("rows"))
            return {**base, "status": "unsupported_kind" if found else "unavailable",
                    "found": found, "note": "node context unavailable for this kind"}
        # ① 노드 메타 — 의미층(개념·결정) 우선, 없으면 Episode(타임라인 클릭 대상).
        #
        # v12 — 라벨을 id 접두사에서 꺼낸다. 여기 Concept 이 박혀 있으면 결정을
        # 클릭했을 때 `executed:False` + "unknown node" 가 나온다(라이브 탐침에서
        # 실제로 그랬다). Decision 에는 one_liner 열이 없어 그 자리는 '' 로 둔다 —
        # props 폴백은 아래 주석대로 호출부가 한다.
        meta_label, meta_pk = _seed_table(node)
        if meta_label not in _SEMANTIC_LABELS:
            meta_label, meta_pk = "Concept", "concept_id"
        ol_expr = "c.one_liner" if meta_label == "Concept" else "''"
        rows = store.run(
            f"MATCH (c:{meta_label}) WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            f"RETURN c.title AS title, {ol_expr} AS ol, c.props AS props, "
            "c.domain AS domain, "
            + _track_cols("c"),
            # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 응답의 `node` 는 논리
            # 그대로다(사용자가 준 값). 근거: [[vinc.plan.node-pk-namespacing]] §7
            {"id": pkns.key(owner, node), "owner": owner})
        if rows:
            # `props` 를 함께 싣는다(D73). `one_liner` 는 **쓰기 경로가 없는 열**이라
            # (`api/fragment.py` 가 SET 하지 않는다 — MCP 적재분 전량이 빈 값) 이것만
            # 돌려주면 상세 패널·묻기 답변이 통째로 빈다. 사람이 쓴 한두 문장 요약은
            # 실제로는 props 에 들어와 있고(검색은 이미 본다 — matched_in:'props'),
            # 조회만 그것을 안 주고 있었다. 호출부가 one_liner → props 순으로 고른다.
            base.update(executed=True,
                        node_kind="decision" if meta_label == "Decision" else "concept",
                        title=rows[0].get("title") or node,
                        domain=rows[0].get("domain") or "",
                        one_liner=rows[0].get("ol") or "",
                        rev=rows[0].get("rev") or 0,
                        updated_at=rows[0].get("updated_at") or "",
                        props=_props_to_obj(rows[0].get("props")))
            # Decision 의 구간은 props 에 있다(§8 항목 5 임시 인코딩). Concept 은
            # 구간이 없고, 미기재는 current 다.
            _p = base["props"] if meta_label == "Decision" else {}
            _stamp_validity(base, _p.get("valid_from"), _p.get("valid_until"))
            if meta_label == "Concept" and node.startswith("concept:doc."):
                # 문서 미러의 구간은 문서의 것이다(F13). 문서가 없으면 개념 규칙 그대로다.
                # 구간 열이 없는 옛 스토어(v22 이전)에서는 이 조회만 실패하고 개념 규칙으로
                # 남는다 — 발췌·관계까지 함께 잃으면 안 된다. 사유는 싣는다.
                try:
                    dm = store.run(
                        "MATCH (d:_DocMeta) WHERE d.doc_id = $doc AND d.owner_id = $owner "
                        "RETURN d.valid_from AS vf, d.valid_until AS vu, d.superseded_by AS sb",
                        {"doc": node[len("concept:doc."):], "owner": owner}) or []
                except Exception as exc:
                    dm = []
                    base["doc_validity_error"] = f"{type(exc).__name__}: {exc}"
                if dm:
                    _stamp_validity(base, dm[0].get("vf"), dm[0].get("vu"))
                    base["superseded_by"] = str(dm[0].get("sb") or "")
        else:
            ep = store.run(
                "MATCH (e:Episode) WHERE e.episode_id = $id AND e.owner_id = $owner "
                "RETURN e.title AS title, e.summary AS s, e.domain AS domain, "
                "e.props AS props, "
                + _track_cols("e"),
                {"id": pkns.key(owner, node), "owner": owner})
            if not ep:
                # Record — 의미층도 Episode 도 아닌 세 번째 실체. 위 ① 이 라벨을
                # Concept 으로 접어 버려서 `record:` id 는 여기까지 흘러와
                # "unknown node" 로 답했다. 존재하는 노드를 없다고 말하는 것이라
                # 조인 읽기(brief)가 Record 를 통째로 못 열었다 —
                # [[record:brief-returns-none-when-its-winner-is-a-record-2026-08-30]]
                # 이 그래프의 사실형 제목은 대부분 Record 에 있어서, 가장 싸게
                # 얻을 수 있는 답을 정확히 못 얻고 있었다.
                if _seed_table(node)[0] == "Record":
                    rc = store.run(
                        "MATCH (r:Record) WHERE r.record_id = $id "
                        "AND r.owner_id = $owner "
                        "RETURN r.title AS title, r.props AS props, "
                        "r.domain AS domain, "
                        + _track_cols("r") + ", " + _validity_cols("r"),
                        {"id": pkns.key(owner, node), "owner": owner})
                    if rc:
                        # one_liner 열이 없다 — 카드 본문은 props 다(부착 목록이
                        # `note` 를 뽑아 쓰는 그 자리와 같은 값). 호출부의
                        # one_liner → props 폴백이 그것을 집는다.
                        base.update(executed=True, node_kind="record",
                                    title=rc[0].get("title") or node,
                                    domain=rc[0].get("domain") or "",
                                    rev=rc[0].get("rev") or 0,
                                    updated_at=rc[0].get("updated_at") or "",
                                    props=_props_to_obj(rc[0].get("props")))
                        _stamp_validity(base, rc[0].get("valid_from"),
                                        rc[0].get("valid_until"))
                        # MENTIONS 는 Chunk→Concept 이라 Record 는 대상이 아니다.
                        # 관계는 있다(ATTACHES_TO). 여기서 links 를 비워 두면
                        # vinc_neighbors 가 양쪽 끝에서 보여 주는 부착을 이 표면만
                        # "연결 0"이라 답해, 두 도구가 같은 그래프를 두고 어긋난다.
                        base["links"] = _node_links(store, node, owner=owner,
                                                    with_evidence=with_evidence,
                                                    offset=links_offset)
                        return base
                base["status"] = "unavailable"
                base["found"] = False
                base["note"] = f"unknown node: {node}"
                return base
            base.update(executed=True, node_kind="episode",
                        title=ep[0].get("title") or node,
                        domain=ep[0].get("domain") or "",
                        one_liner=ep[0].get("s") or "",
                        rev=ep[0].get("rev") or 0,
                        updated_at=ep[0].get("updated_at") or "",
                        # F11 — 사건도 props 열이 있다. 안 고르면 저장된 값이 안 보인다.
                        props=_props_to_obj(ep[0].get("props")))
            _stamp_validity(base, "", "")   # 사건은 구간이 없다 — 꼴만 맞춘다
            # Episode 는 MENTIONS 대상이 아니다(발췌 없음 — 요약이 곧 설명). 그렇다고
            # **관계까지 없는 것은 아니다**: ABOUT 이 사건을 개념·결정에 잇는다. 여기서
            # 일찍 빠져나가면서 그 둘을 함께 건너뛰고 있었고, 사건이 해소 후보가 되면서
            # (2026-09-09) 확정된 사건만 관계 없이 답하게 됐다. 바로 위 Record 갈래가
            # 같은 사유로 이미 링크를 채운다.
            base["arm"] = "none"
            base["excerpts"] = []
            base["links"] = _episode_links(store, node, owner=owner,
                                           with_evidence=with_evidence,
                                           offset=links_offset)
            return base
        # ② MENTIONS 역참조 — 정본 발췌.
        ment = store.run(
            f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{meta_label}) "
            f"WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            "AND (ch.owner_id = $owner OR ch.owner_id = '') "
            "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
            "ch.section_title AS section, ch.text AS text, m.method AS method "
            # 링크 방법이 강한 순(title > stem > source)으로 먼저 정렬한다. source 는
            # 파일 제공처(provenance)라 그 파일의 **모든** 청크가 걸리므로, 문서 순서만으로
            # 자르면 창이 앞 문서 머리말로 채워지고 정작 이 노드를 말하는 청크가 밀려난다.
            # doc_id·chunk_index 는 동순위 타이브레이크로 남겨 결과를 결정론으로 유지할 것.
            # 근거: [[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]
            "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
            f"ch.doc_id, ch.chunk_index LIMIT {lim}",
            {"id": pkns.key(owner, node), "owner": owner}) or []
        arm = "mentions" if ment else "none"
        # ③ FTS 폴백 — 링크 없는 개념도 제목으로 관련 정본을 찾는다(증강).
        # B7 — 전문 검색을 **소유자로 좁혀** 묻는다. 전역 상위 몇 개를 받아 남의 행을
        # 버리면 공유 허브에서 남의 청크가 창을 채우고 내 발췌가 빈다.
        if not ment and base["title"]:
            ids, fts_error = _owner_fts_ids(store, base["title"], lim, owner=owner)
            if fts_error:
                base["excerpt_error"] = fts_error
            if ids:
                ment = store.run(
                    "MATCH (ch:Chunk) WHERE ch.chunk_id IN $ids "
                    "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                    "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, "
                    "ch.section_title AS section, ch.text AS text, "
                    "'fts' AS method", {"ids": ids, "owner": owner}) or []
                arm = "fts" if ment else "none"
        # ④-0 이 노드에 딸린 **문서 전부**. 발췌와 다른 계약이다: 발췌는 청크를
        # `lim` 개로 자르므로 그 넷이 한 문서에서 나오면 **문서도 하나로 보인다** —
        # 노드가 열 개 문서에 언급돼 있어도 그렇다. 소스 선택기가 그것을 전체 목록으로
        # 읽으면 사용자는 있는 줄도 모르는 문서를 고를 수 없다.
        # 근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §4]].
        srows = store.run(
            f"MATCH (ch:Chunk)-[:MENTIONS]->(c:{meta_label}) "
            f"WHERE c.{meta_pk} = $id AND c.owner_id = $owner "
            "AND (ch.owner_id = $owner OR ch.owner_id = '') "
            "RETURN ch.doc_id AS doc_id, count(ch.chunk_id) AS n",
            {"id": pkns.key(owner, node), "owner": owner}) or []
        # ④ 문서 제목 조인(_DocMeta) + 발췌 정리(공백 접기, 240자).
        # 제목은 발췌에 뜬 문서와 소스 목록 **양쪽**에 필요하므로 한 번에 받는다.
        # `_DocMeta.doc_id` 는 소유자로 네임스페이싱되지 않으므로 청크와 같은 과도기 술어
        # (owner OR '')를 건다 — 빠지면 같은 doc_id 를 가진 남의 문서 제목이 샌다(VIN-233).
        doc_ids = sorted({r["doc_id"] for r in ment if r.get("doc_id")})
        all_ids = sorted({str(r.get("doc_id") or "") for r in srows
                          if r.get("doc_id")} | set(doc_ids))
        titles: dict[str, str] = {}
        if all_ids:
            for r in store.run(
                    "MATCH (d:_DocMeta) WHERE d.doc_id IN $ids "
                    "AND (d.owner_id = $owner OR d.owner_id = '') "
                    "RETURN d.doc_id AS id, d.title AS t",
                    {"ids": all_ids, "owner": owner}) or []:
                titles[r["id"]] = r.get("t") or ""
        # 제목 → id 순서로 창을 자른다. 발췌 상한과 독립적으로 끝까지 읽을 수 있다.
        _src = sorted(({"doc_id": str(r.get("doc_id") or ""),
                        "title": titles.get(str(r.get("doc_id") or ""), "")
                        or str(r.get("doc_id") or ""),
                        "mentions": int(r.get("n") or 0)} for r in srows
                       if r.get("doc_id")),
                      key=lambda s: (s["title"], s["doc_id"]))
        base["sources"] = {"total": len(_src),
                           **_window(_src, sources_offset, _NODE_SOURCES_CAP)}
        base["excerpts"] = [{
            "doc_id": r.get("doc_id") or "",
            "doc_title": titles.get(r.get("doc_id") or "", "") or (r.get("doc_id") or ""),
            "section": r.get("section") or "",
            "excerpt": " ".join((r.get("text") or "").split())[:240],
            "method": r.get("method") or "",
        } for r in ment]
        try:
            cites = _doc_cite_ids(store, owner, all_ids)
        except Exception as exc:
            cites = {}
            base["citation_error"] = f"{type(exc).__name__}: {exc}"
        base["excerpts"] = _cite_doc_rows(base["excerpts"], cites)
        base["sources"]["items"] = _cite_doc_rows(base["sources"]["items"], cites)
        base["arm"] = arm
        base["links"] = _node_links(store, node, owner=owner,
                                    with_evidence=with_evidence,
                                    offset=links_offset)
        return base
    except Exception as exc:
        base["status"] = "query_failed"
        base["note"] = base["error"] = f"{type(exc).__name__}: {exc}"
        return base
    finally:
        if owns:
            store.close()


# 상세 레일 관계 목록 상한 — 허브(차수 30+)에서 레일이 끝없이 길어지지 않게.
# 잘렸는지는 `truncated` 로 밝힌다(조용한 축소 금지).
_NODE_LINKS_CAP = 24


def _domain_context(store, node_id: str, *, owner: str, offset: int, cfg=None) -> dict:
    """공유 좌표의 존재는 정확한 소유 범위 조회로, 구조는 기존 이웃 조회로 읽는다."""
    base = {"kind": "node-context", "executed": False, "node": node_id,
            "node_kind": "domain"}
    domain = domain_hierarchy.parse_id(node_id)
    if not domain or not owner:
        return {**base, "status": "unavailable", "found": False}
    exact = node(domain_hierarchy.make_id(domain), owner=owner, cfg=cfg, store=store)
    if not exact.get("executed"):
        return {**base, "status": "query_failed", "error": exact.get("error") or exact.get("note")}
    if not exact.get("found"):
        # 남의 빈 공유 좌표와 없는 좌표는 구분하지 않는다.
        return {**base, "status": "unavailable", "found": False}
    ring = _domain_neighbors(node_id, owner=owner, limit=None, cfg=cfg, store=store)
    if not ring.get("executed") or ring.get("error"):
        return {**base, "status": "query_failed", "error": ring.get("error") or ring.get("note")}
    rows = ring["neighbors"]
    children = [r for r in rows if r["rel"] == "PARENT_OF"]
    members = [r for r in rows if r["rel"] == "IN_DOMAIN"]
    counts = store.run(domain_hierarchy.scoped_counts_cypher(),
                       {"owner": owner, "domain": domain, "prefix": domain + "/"}) or []
    direct = sum(int(r.get("total") or 0) for r in counts if r.get("domain") == domain)
    subtree = sum(int(r.get("total") or 0) for r in counts)
    expected_children = {domain + "/" + r["domain"][len(domain) + 1:].split("/")[0]
                         for r in counts if r.get("domain", "").startswith(domain + "/")}
    parent_path = domain.rpartition("/")[0]
    parent = ({"id": domain_hierarchy.make_id(parent_path), "title": parent_path,
               "domain": parent_path} if parent_path else None)
    topic = {"path": domain, "parent": parent,
             "child_topics": len(children), "direct_nodes": len(members),
             "subtree_nodes": subtree, "descendant_nodes": subtree - direct,
             "structure": {"total": len(rows), **_window(rows, offset)}}
    from api import topic_description
    topic["description"] = topic_description.read(store, owner=owner, domain=domain)
    # 열은 분류의 정본, 엣지는 파생물이다. 불일치는 자동 수리하지 않는다.
    if direct != len(members) or expected_children != {r["title"] for r in children}:
        topic["consistency"] = {"status": "membership_drift", "column_direct_nodes": direct,
                                "column_child_topics": len(expected_children)}
    return {**base, "executed": True, "found": True, "status": "ok",
            "title": domain.split("/")[-1], "domain": domain, "topic": topic}


def _window(items: list[dict], offset: int, cap: int = _NODE_LINKS_CAP) -> dict:
    """목록의 **한 창**과 이어 읽을 자리. 관계와 소스가 같은 말을 하게 묶는다.

    `truncated` 는 "이 창 뒤에 더 있나"다. `offset` 이 0 이면 종전 뜻(전체가 상한보다
    큼)과 값이 같으므로 이미 이 응답을 읽던 화면은 바뀌지 않는다.

    왜 커서가 아니라 오프셋인가: 이 목록의 정렬은 결정론이고(제목·id) 규모가 수백이다.
    커서는 인코딩·만료·무효화를 다 지어야 하는데 여기서는 그 대가로 얻는 것이 없다.
    (점수로 줄 세우고 페이지가 많은 `vinc_search` 는 사정이 달라서 커서를 쓴다.)

    ⚠ 상한만 있고 이어 읽을 자리가 없으면 **보이는 것이 전부인지 알 길이 없다** —
    25개 중 24개를 내면서 `total:25` 만 말하던 자리가 그랬다(실측 2026-09-20).
    근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §6]]."""
    off = max(0, int(offset or 0))
    page = items[off:off + cap]
    more = len(items) > off + len(page)
    out: dict = {"items": page, "truncated": more, "offset": off}
    if more:
        out["next_offset"] = off + len(page)
    return out


def _owner_fts_ids(store, text: str, limit: int, *, owner: str | None
                   ) -> tuple[list[str], str]:
    """발췌 폴백의 전문 검색 — 소유자로 좁힌 청크 id 와 실패 사유(없으면 "").

    검색의 청크 팔과 같은 규칙이다: 소유자를 걸 수 없는 스토어에는 묻지 않고(fail-closed),
    색인이 없으면 청크가 있을 때 한 번 세우고 다시 묻는다. 그 밖의 실패는 사유를 돌려준다
    — 조용히 삼키면 "발췌 없음"과 "못 읽음"이 같아 보인다."""
    filters = {"owner_id": owner} if owner else None
    if owner and not getattr(store, "scoped_chunk_search", False):
        return [], ("owner_scope_unsupported: this store cannot filter full-text "
                    "search by owner")
    try:
        hits = store.fulltext_search(text, limit, filters) or []
    except Exception as exc:
        kind = _classify_chunk_error(exc)
        if kind != "index_missing":
            return [], f"{kind}: {type(exc).__name__}: {exc}"
        if getattr(store, "fts_index_remnant", False):
            return [], f"fts_index_remnant: {_FTS_REMNANT}"
        builder = getattr(store, "ensure_search_indexes", None)
        if builder is None:
            return [], f"index_missing: {type(exc).__name__}: {exc}"
        if _chunk_count(store) == 0:
            return [], ""          # 청크가 없으면 찾을 발췌도 없다. 실패가 아니다
        try:
            builder(dim=None)
            hits = store.fulltext_search(text, limit, filters) or []
        except Exception as exc2:
            return [], f"index_build_failed: {type(exc2).__name__}: {exc2}"
    return [h.get("id") for h in hits if h.get("id")], ""


def _entity_links(store, node: str, *, owner: str, with_evidence: bool = False,
                  offset: int = 0) -> dict:
    """Entity 시드의 TYPED 관계(양방향). `subtype` 이 저작된 타입이다. TYPED 에는
    근거·신뢰도 열이 없어 evidence 는 False, confidence 는 None 이다."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    try:
        rows = store.run(_typed_neighbors_cypher(owner), {"node": pkns.key(owner, node),
                                                          "owner": owner}) or []
    except Exception:
        return empty
    order = {"out": 0, "in": 1}
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": "", "type": "TYPED", "subtype": r.get("subtype") or "",
        "dir": r.get("dir") or "", "evidence": False, "confidence": None, "note": "",
        "valid_from": "", "valid_until": "",
        **({"evidence_text": ""} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (order.get(r.get("dir") or "", 9),
                                           str(r.get("subtype") or ""),
                                           str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    out_n = sum(1 for i in items if i["dir"] == "out")
    return {"degree": len(items), "out": out_n, "in": len(items) - out_n, "attached": 0,
            "total": len(items), "executed": True, **_window(items, offset)}


def _entity_context(store, node: str, base: dict, *, owner: str,
                    with_evidence: bool = False, offset: int = 0) -> dict:
    """Entity(개방 타입) 노드의 상세. 열이 제목·타입·태그뿐이라 그것과 TYPED 관계만 낸다.
    이것이 없을 때 brief 는 Entity 를 열지 못했다(감사 F12)."""
    rows = store.run(
        "MATCH (n:Entity) WHERE n.entity_id = $id AND n.owner_id = $owner "
        "RETURN n.title AS title, n.type AS type, coalesce(n.tags, []) AS tags, "
        + _track_cols("n"), {"id": pkns.key(owner, node), "owner": owner}) or []
    if not rows:
        return {**base, "status": "unavailable", "found": False,
                "note": f"unknown node: {node}"}
    out = {**base, "executed": True, "node_kind": "entity",
           "title": rows[0].get("title") or node, "entity_type": rows[0].get("type") or "",
           "tags": list(rows[0].get("tags") or []),
           "rev": rows[0].get("rev") or 0, "updated_at": rows[0].get("updated_at") or ""}
    _stamp_validity(out, "", "")   # 개방 타입은 구간이 없다 — 꼴만 맞춘다
    out["links"] = _entity_links(store, node, owner=owner, with_evidence=with_evidence,
                                 offset=offset)
    return out


def _conf(value) -> float | None:
    """관계 신뢰도. 저장되지 않은 값은 None 으로 둔다 — 0.0 은 저작된 값이다(감사 F8)."""
    return None if value is None else float(value)


def _record_links(store, node: str, *, owner: str,
                  with_evidence: bool = False, offset: int = 0) -> dict:
    """Record 시드의 관계 목록 — 그 기록이 **붙어 있는** 개념·결정(ATTACHES_TO)과
    기록끼리의 대체(SUPERSEDES, 양방향).

    :func:`_node_links` 의 거울상이다. 저쪽은 "무엇이 이 노드에 붙어 있나"를 묻고
    여기서는 "이 노드가 무엇에 붙어 있나"를 묻는다. 방향이 반대라 같은 쿼리를 재사용할
    수 없다. 부착의 `dir` 은 'out' 으로 낸다 — 엣지가 Record 에서 나가기 때문이고, 화면의
    "나감" 줄과 뜻이 같다. SUPERSEDES 는 저장된 방향 그대로 out(이 기록이 대체한다)·
    in(이 기록이 대체됐다)이다. 빠지면 total 이 vinc_neighbors 의 count 보다 작다(감사 F2).

    evidence/confidence 는 v16 부터 ATTACHES_TO 실열이다 — 그전에는 열이 없어
    항상 False 로 냈고 그것이 결측이 아니라 사실이었다
    ([[record:graph-md-requires-evidence-that-ingest-refuses-to-store]]).
    실패는 :func:`_node_links` 와 같은 태도로 빈 요약 강등."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    seed = "rc.record_id = $id AND rc.owner_id = $owner AND o.owner_id = $owner "
    params = {"id": pkns.key(owner, node), "owner": owner}
    try:
        rows = store.run(" UNION ALL ".join(
            f"MATCH (rc:Record)-[r:ATTACHES_TO]->(o:{ol}) WHERE {seed}"
            f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
            "'ATTACHES_TO' AS type, 'out' AS dir, "
            "r.evidence AS ev, r.confidence AS conf, " + _VALIDITY_EMPTY
            for ol, opk in _SEMANTIC_PK.items()), params) or []
    except Exception:
        return empty
    # 대체 상대도 Record 라 그 구간을 함께 싣는다(W13 과 같은 신분). 따로 묻는 이유:
    # SUPERSEDES 표가 없는 옛 스토어에서 이 팔이 Binder 로 죽어도 부착은 남아야 한다
    # (neighbors 의 record_supersedes_plan 도 따로 돈다). 못 읽었으면 표식을 단다.
    sup_failed = False
    try:
        rows += store.run(" UNION ALL ".join(
            f"MATCH {pat} WHERE {seed}"
            "RETURN o.record_id AS id, o.title AS title, o.domain AS domain, "
            f"'SUPERSEDES' AS type, '{d}' AS dir, "
            "r.evidence AS ev, r.confidence AS conf, " + _validity_cols("o")
            for pat, d in (("(rc:Record)-[r:SUPERSEDES]->(o:Record)", "out"),
                           ("(o:Record)-[r:SUPERSEDES]->(rc:Record)", "in"))), params) or []
    except Exception:
        sup_failed = True
    order = {"out": 0, "in": 1}
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": r.get("domain") or "", "type": r.get("type") or "",
        "dir": r.get("dir") or "",
        "evidence": bool((r.get("ev") or "").strip()),
        "confidence": _conf(r.get("conf")), "note": "",
        "valid_from": r.get("valid_from") or "", "valid_until": r.get("valid_until") or "",
        **({"evidence_text": (r.get("ev") or "").strip()} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (order.get(r.get("dir") or "", 9),
                                           str(r.get("type") or ""),
                                           str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    out_n = sum(1 for i in items if i["dir"] == "out")
    return {"degree": len(items), "out": out_n, "in": len(items) - out_n, "attached": 0,
            "total": len(items), "executed": True,
            **({"supersession_unavailable": True} if sup_failed else {}),
            **_window(items, offset)}


def _episode_links(store, node: str, *, owner: str,
                   with_evidence: bool = False, offset: int = 0) -> dict:
    """Episode 시드의 관계 목록 — 그 사건이 **무엇에 대한 것인가**(ABOUT).

    :func:`_record_links` 와 같은 모양이다. 부착 엣지가 시드에서 **나가므로**
    "무엇이 이 노드에 붙어 있나"를 묻는 :func:`_node_links` 의 팔들은 구조적으로 0행을
    낸다. 게다가 Episode 는 `_SEMANTIC_LABELS` 가 아니라 그쪽에서 Concept 으로 접히는데,
    그러면 **다른 테이블을 조회하고 조용히 빈 목록**을 낸다.

    이 함수가 없던 동안 `node_context` 의 Episode 갈래는 링크를 아예 안 실었다. 사건이
    해소 후보가 아니던 시절에는 눈에 띄지 않았지만, 2026-09-09 에 후보로 올리면서
    **확정된 사건만 관계 없이 답하는** 상태가 됐다. 이 스토어 실측: 사건 160건에 ABOUT
    256개(개념 226 · 결정 30)이고 관계가 0인 사건은 둘뿐이다.
    근거: [[record:episode-can-never-be-a-brief-candidate-2026-09-09]]"""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    try:
        rows = store.run(" UNION ALL ".join(
            f"MATCH (e:Episode)-[r:ABOUT]->(o:{ol}) "
            "WHERE e.episode_id = $id AND e.owner_id = $owner "
            "AND o.owner_id = $owner "
            f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
            "r.evidence AS ev, r.confidence AS conf "
            for ol, opk in _SEMANTIC_PK.items()),
            {"id": pkns.key(owner, node), "owner": owner}) or []
    except Exception:
        return empty
    items = [{
        "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
        "domain": r.get("domain") or "", "type": "ABOUT", "dir": "out",
        "evidence": bool((r.get("ev") or "").strip()),
        "confidence": _conf(r.get("conf")), "note": "",
        "valid_from": "", "valid_until": "",
        **({"evidence_text": (r.get("ev") or "").strip()} if with_evidence else {}),
    } for r in sorted(rows, key=lambda r: (str(r.get("title") or ""),
                                           str(r.get("id") or "")))]
    return {"degree": len(items), "out": len(items), "in": 0, "attached": 0,
            "total": len(items), "executed": True, **_window(items, offset)}


def _node_links(store, node: str, *, owner: str,
                with_evidence: bool = False, offset: int = 0) -> dict:
    """개념의 연결 요약 + 관계 목록(:func:`node_context` ④). 조회 전용.

    개념·결정 사이 공통 읽기 계약의 관계는 양방향으로 세고, 부착(Bridge 매개·Episode
    ABOUT·Record ATTACHES_TO)은 따로 센다 — 화면이 "연결 N · → 나감 · ← 들어옴"과
    "부착 N"을 다른 줄에 싣기 때문이다.

    그래서 ``degree`` 는 **out + in 뿐**이고 ``attached`` 는 거기 안 든 나머지다.
    ``total`` 이 그 둘을 합친 수 — 그 노드의 연결수이고 vinc_neighbors 의 ``count``
    와 같다. 이 셋은 세 갈래(:func:`_record_links`·:func:`_episode_links` 포함)에서
    같은 뜻이다: 기록·사건은 부착 축이 구조적으로 0이라 total 이 degree 와 같다.

    SEC-1: 양 끝을 owner 로 건다. Bridge 만 과도기 술어(:func:`_bridge_owner_pred`).
    실패는 조용히 빈 요약으로 강등한다 — 관계 목록은 증강이고, 미마이그레이션
    스토어(E-G6 열 부재)에서 상세 패널 자체가 죽으면 안 된다."""
    empty = {"degree": 0, "out": 0, "in": 0, "attached": 0, "total": 0, "items": [],
             "truncated": False, "executed": False}
    # v12 — 시드 라벨을 접두사에서 꺼낸다. 상대편(o)은 의미층 둘을 다 본다.
    # 박아 두면 결정의 상세 패널이 "연결 0"으로 뜬다(관계는 있는데 안 보인다).
    _sl, _spk = _seed_table(node)
    if _sl == "Record":
        # Record 시드는 반대 방향이다 — 부착은 Record→(Concept|Decision) 이라
        # 아래 팔들(무엇이 이 노드에 붙어 있나)이 구조적으로 0행을 낸다. 그대로
        # 두면 부착이 있는 기록이 "연결 0"으로 답해 vinc_neighbors 와 어긋난다
        # (그쪽은 "모든 관계는 양쪽 끝에서 보인다"를 계약으로 말한다).
        return _record_links(store, node, owner=owner, with_evidence=with_evidence,
                             offset=offset)
    if _sl not in _SEMANTIC_LABELS:
        _sl, _spk = "Concept", "concept_id"
    rows: list[dict] = []
    try:
        arms = []
        for (sl, sp, dl, dp), rels in _GRAPH_READ_PAIRS.items():
            if sl not in _SEMANTIC_LABELS or dl not in _SEMANTIC_LABELS:
                continue
            for outgoing in (True, False):
                if (sl if outgoing else dl) != _sl:
                    continue
                ol, opk = (dl, dp) if outgoing else (sl, sp)
                pattern = (f"(c:{_sl})-[r:{'|'.join(rels)}]->(o:{ol})" if outgoing
                           else f"(o:{ol})-[r:{'|'.join(rels)}]->(c:{_sl})")
                arms.append(
                    f"MATCH {pattern} "
                    f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND o.owner_id = $owner "
                    f"RETURN o.{opk} AS id, o.title AS title, o.domain AS domain, "
                    f"label(r) AS type, '{'out' if outgoing else 'in'}' AS dir, "
                    "r.evidence AS ev, r.confidence AS conf")
        cc = " UNION ALL ".join(arms)
        rows = store.run(cc, {"id": pkns.key(owner, node), "owner": owner}) or []
    except Exception:
        return empty

    attached: list[dict] = []
    try:
        attached = store.run(
            # 부착 3종 — 각 rel 테이블의 열이 달라 evidence/confidence 는 ABOUT ·
            # ATTACHES_TO(v16)만 실열 투영하고 CONNECTS 는 상수로 맞춘다(UNION ALL 은
            # 컬럼 형태가 같아야 한다).
            # raw_props: Record 카드의 본문(props.note 또는 props.one_liner)이 여기
            # 있다 — 여태 id/title/domain 만 주고 그 문장은 버렸다(no-llm-chat 연구
            # §3.3, I6 미개통 원인). 나머지 둘은 빈 문자열로 맞춘다.
            # W13 — 유효구간 둘은 Record 팔만 실열(열이 Record 전용). 다른 두 팔은
            # 리터럴 자리 맞춤 — 한 팔이라도 빠지면 UNION 전체가 Binder 로 죽는다.
            f"MATCH (b:Bridge)-[:CONNECTS]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND {_bridge_owner_pred('b')} "
            # CONNECTS 에는 신뢰도 열이 없다. 0.0 리터럴은 저작된 값으로 읽히므로 NULL 이다.
            "RETURN b.bridge_id AS id, b.title AS title, b.source_domain AS domain, "
            "'CONNECTS' AS type, 'att' AS dir, '' AS ev, CAST(NULL AS DOUBLE) AS conf, "
            "'' AS raw_props, "
            + _VALIDITY_EMPTY + " "
            "UNION ALL "
            f"MATCH (e:Episode)-[r:ABOUT]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND e.owner_id = $owner "
            "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
            "'ABOUT' AS type, 'att' AS dir, r.evidence AS ev, r.confidence AS conf, "
            "'' AS raw_props, " + _VALIDITY_EMPTY + " "
            "UNION ALL "
            f"MATCH (rc:Record)-[r2:ATTACHES_TO]->(c:{_sl}) "
            f"WHERE c.{_spk} = $id AND c.owner_id = $owner AND rc.owner_id = $owner "
            "RETURN rc.record_id AS id, rc.title AS title, rc.domain AS domain, "
            "'ATTACHES_TO' AS type, 'att' AS dir, r2.evidence AS ev, "
            "r2.confidence AS conf, rc.props AS raw_props, " + _validity_cols("rc"),
            {"id": pkns.key(owner, node), "owner": owner}) or []
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
        # Record 카드 본문 — note 우선, 없으면 one_liner(두 이름이 실제로 쓰인다).
        props = _props_to_obj(r.get("raw_props")) if r.get("raw_props") else {}
        item = {
            "id": r.get("id") or "", "title": r.get("title") or (r.get("id") or ""),
            "domain": r.get("domain") or "", "type": r.get("type") or "",
            "dir": r.get("dir") or "",
            # 근거는 **있는지 없는지**가 화면의 관심사다(문장 전문은 edge-context 가 준다).
            "evidence": bool((r.get("ev") or "").strip()),
            # 저장되지 않은 신뢰도는 None 이다(감사 F8). 정렬은 brief 가 맨 뒤로 보낸다.
            "confidence": _conf(r.get("conf")),
            "note": props.get("note") or props.get("one_liner") or "",
            # W13 — Record 카드만 값을 갖는다(note 와 같은 신분). 무효화된 기록이
            # 카드에서 유효한 것과 똑같이 보이면 이 열은 없는 것과 같다.
            "valid_from": r.get("valid_from") or "",
            "valid_until": r.get("valid_until") or "",
        }
        if with_evidence:
            # W1 — 에이전트는 "근거가 있다"가 아니라 **그 문장**을 인용해야 한다.
            # 화면은 불리언만 쓰므로 키 자체를 그때만 단다: 기본 호출의 페이로드는
            # 한 바이트도 안 변한다. 근거: [[vinc.plan.mcp-agent-surface §4]]
            item["evidence_text"] = (r.get("ev") or "").strip()
        items.append(item)
    # `degree` 는 out + in 이고 `attached` 는 **거기 안 든 나머지**다. 둘이 한 dict 에
    # 나란히 나가면서 어느 쪽도 그 사실을 말하지 않아, 받는 쪽이 degree 를 그 노드의
    # 연결수로 읽었다. 실측 2026-09-14: concept:node-resolution-accuracy 가
    # degree 9 · attached 50 으로 답하는 동안 vinc_neighbors 는 count 59 를 냈고(85%
    # 과소), 그 수를 합산한 연결성 결론이 나갔다가 철회됐다. 그래서 총수를 **직접
    # 싣는다** — 받는 쪽이 더해서 복원할 것을 남겨 두지 않는다.
    # 근거: [[record:brief-links-degree-counts-only-the-core-layer-and-understates-a-59-relation-node-as-9-2026-09-14]]
    return {"degree": len(rows), "out": out_n, "in": in_n, "attached": len(attached),
            "total": len(rows) + len(attached), "executed": True,
            **_window(items, offset)}


# ── vinc_brief — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기 ──────────────
#
# 생성은 없다. 재료가 곧 답이고, 해소 규칙은 데스크탑 Ask 흐름
# (`app/src/ask.ts` 의 resolve·answerFor·pickBody)에서 그대로 옮겨 왔다. 임의
# 임계값을 두지 않는 것이 요점이다 — 1위가 2위를 **엄격히** 이길 때만 확정하고,
# 비기면 후보를 돌려주며 되묻는다. 근거: [[vinc.plan.mcp-agent-surface §4]]

#: brief 가 내부 search 에 넘기는 top_k. 청크 페이지만이 아니라 **node_hits 도 이 수로
#: 잘린다**(노드 팔의 top_k 가 같은 값이다). 그래서 `VINC_BRIEF_EXPOSE` 를 5 보다 크게
#: 줘도 되묻기 후보는 5 를 넘지 않는다. 값은 그대로 둔다 — 넓히면 brief 계약이 바뀐다
#: (감사 D12, 2026-10-16).
_BRIEF_TOP_K = 5
#: 되묻을 때 실을 후보 수. 3 에서 5 로 늘렸다 — 같은 풀의 앞자리를 더 보여 줄 뿐이라
#: 보이던 정답이 가려지지 않고, 풀 안의 정답이 전부 보인다(G0 43 → 47/60). 앱의 ask.ts 는
#: brief 를 부르지 않고 따로 3 개를 보여 준다. `VINC_BRIEF_EXPOSE` 로 프로세스마다 바꾼다.
#: 근거: [[decision:brief-shows-five-candidates-when-it-asks-back-and-keeps-the-list-beside-resolved-off-2026-09-29]]
_BRIEF_CANDIDATES = 5
_BRIEF_BODY_KEYS = ("note", "summary", "one_liner", "description", "desc")
_BRIEF_MIN_PROP_BODY = 12     # props 값을 문장으로 볼 최소 길이 — ask.ts 와 같다
_BRIEF_FALLTHROUGH = 3        # 1위가 안 열릴 때 시도할 후보 수 상한(왕복 비용 유계)


#: 질문 속 노드 id — brief 직참조의 탐지 대상. bridge 는 node_context 가 읽지
#: 못해 넣으면 항상 실패해 fell_through 소음만 늘므로 뺀다(랭킹 경로가 받는다).
#: 끝 문자를 글자·숫자로 조여 문장부호(`concept:foo.` 의 마침표)가 id 에 안 붙는다.
#: 슬러그는 **유니코드**다. 한국어 슬러그 개념이 있고, ASCII 로 조이면 첫 한글에서
#: 잘린 접두를 열어 버린다 — 그 접두가 실재하면 조용히 **다른 노드**다.
#: 근거: [[record:brief-direct-id-pattern-was-ascii-only-2026-09-12]]
_BRIEF_ID_RE = re.compile(
    r"\b(?:concept|record|decision|episode):[^\W_](?:[\w.-]*[^\W_])?")
#: 위 매치의 ASCII 접두 — 되물림 후보. 한국어 문장은 조사를 id 에 붙여 쓰는데
#: (`concept:foo는`) 문법으로는 한국어 슬러그와 못 가르므로 스토어가 가른다: 긴 쪽이
#: 안 열리면 이 접두를 열고, 그 대체는 `fell_through` 로 밝힌다.
_BRIEF_ID_ASCII_RE = re.compile(
    r"(?:concept|record|decision|episode):[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
#: 개방 타입(Entity) id 후보 — 접두가 사용자 저작 타입이라 열거할 수 없다(`theory:x`).
#: 문법만으로는 `note:foo` 같은 문장 조각과 못 가르므로 **스토어에 실재할 때만** 연다
#: (:func:`_brief_entity_ids`). 없으면 무시하고 랭킹으로 간다 — 위 네 접두와 달리
#: "없다"고 답하지 않는다. 근거: 감사 F12(2026-10-16).
_BRIEF_OPEN_ID_RE = re.compile(r"(?<![\w/.:-])([A-Za-z][\w-]*):[^\W_](?:[\w.-]*[^\W_])?")
_BRIEF_NOT_OPEN = frozenset(("concept", "record", "decision", "episode", "domain",
                             "bridge", "http", "https", "ftp", "mailto", "file", "urn"))


def _brief_query_ids(query: str, cap: int = _BRIEF_FALLTHROUGH) -> list[str]:
    """질문에 그대로 들어 있는 노드 id — 등장 순서, 중복 제거. 매치마다 바로 뒤에
    그 ASCII 접두를 되물림 후보로 둔다(매치와 다를 때만).

    `cap` 은 **언급** 수 상한이다(변형 수가 아니다). 변형으로 자르면 `A는 … B는` 이
    `[A는, A, B는]` 에서 잘려 실재하는 B 가 시도조차 안 된다. 새 후보를 하나도 못 보태는
    언급(앞 언급의 접두와 같은 id)은 상한을 쓰지 않는다. 왕복은 언급당 둘까지다."""
    seen: set[str] = set()
    out: list[str] = []
    mentions = 0
    for m in _BRIEF_ID_RE.findall(query or ""):
        a = _BRIEF_ID_ASCII_RE.match(m)
        new = [c for c in dict.fromkeys((m, a.group(0) if a else ""))
               if c and c not in seen]
        if not new:
            continue
        if mentions >= cap:
            break
        mentions += 1
        seen.update(new)
        out.extend(new)
    return out


def _brief_entity_ids(store, query: str, *, owner: str,
                      cap: int = _BRIEF_FALLTHROUGH) -> list[str]:
    """질문 속 개방 타입 id 중 이 소유자의 Entity 로 실재하는 것만, 등장 순서로."""
    cands = []
    for m in _BRIEF_OPEN_ID_RE.finditer(query or ""):
        if m.group(1).lower() not in _BRIEF_NOT_OPEN and m.group(0) not in cands:
            cands.append(m.group(0))
        if len(cands) >= cap:
            break
    if not cands:
        return []
    try:
        rows = store.run("MATCH (n:Entity) WHERE n.entity_id IN $ids AND n.owner_id = $owner "
                         "RETURN n.entity_id AS id",
                         {"ids": pkns.keys(owner, cands), "owner": owner}) or []
    except Exception:
        return []      # Entity 표가 없는 스토어 — 직참조 후보가 없을 뿐 랭킹은 그대로 돈다
    found = {pkns.strip(str(r.get("id") or "")) for r in rows}
    return [c for c in cands if c in found]


def brief_reads_by_id(query: str) -> bool:
    """brief 가 이 질문을 id 직참조로만 답하는가 — 그렇다면 검색을 돌지 않는다.
    `/brief` 는 이것을 보고 질의 임베딩을 건너뛴다. 직참조 갈래가 검색으로 넘어가게
    바뀌면 여기도 함께 바꿀 것 — 안 그러면 그 검색이 의미 팔 없이 돈다.

    개방 타입(Entity) id 는 여기서 참이 되지 않는다. 실재를 스토어로 가려야 하는데 이
    판정은 스토어 없이 돈다. 그 질문은 임베딩을 한 번 더 구할 뿐 답은 같다."""
    return bool(_brief_query_ids(query))


def _brief_material(base: dict, cand: dict, ctx: dict) -> dict:
    """확정된 노드의 답변 재료를 base 에 채운다 — 랭킹 경로와 id 직참조 경로가
    같은 꼴을 내도록 한 자리로 모은 꼬리. cand 는 검색 후보(랭킹 경로) 또는
    {"id": ...} 하나(직참조 경로)다."""
    nid = str(cand.get("id") or "")
    base["node"] = {"id": nid, "cite_id": nid,
                    "title": ctx.get("title") or cand.get("title") or nid,
                    "domain": cand.get("domain") or ctx.get("domain") or "",
                    "kind": ctx.get("node_kind") or cand.get("kind") or "",
                    # 노드 자신의 유효구간 — 직참조는 필터를 거치지 않으므로 여기가
                    # historical 을 말할 유일한 자리다.
                    "valid_from": ctx.get("valid_from") or "",
                    "valid_until": ctx.get("valid_until") or "",
                    "validity": ctx.get("validity") or "current",
                    "superseded_by": ctx.get("superseded_by") or ""}
    from store.search_temporal import stamp
    stamp(base["node"], ctx)
    if "temporal" in ctx:
        base["node"]["temporal"] = cand.get("temporal") or ctx["temporal"]
    base["node"]["validity_basis"] = "legacy_visibility"
    base["node"]["claim_validity"] = base["node"]["temporal"]["validity"]
    for key in ("created_at", "updated_at", "content_time", "temporal_error"):
        if key in ctx:
            base["node"][key] = ctx[key]
    # 알림은 증거 유효성(claim_validity)에서 낸다(감사 F13). 레거시 validity 는 구간이
    # 없는 노드를 current 로 읽어서, 은퇴한 문서의 미러가 알림 없이 나갔다. unknown 은
    # "날짜를 모른다"이지 "현재가 아니다"가 아니므로 알리지 않는다.
    claim = base["node"]["claim_validity"]
    if claim in ("historical", "future"):
        _sb = base["node"]["superseded_by"] or str(
            (base["node"].get("temporal") or {}).get("superseded_by") or "")
        base.setdefault("validity_note",
                        f"this claim is {claim}, not current"
                        + (f"; superseded by {_sb}" if _sb else ""))
    body = _brief_body(ctx)
    base["one_liner"] = body["text"]
    base["body_src"] = body["src"]
    base["body_cite"] = body["cite"]
    base["excerpts"] = list(ctx.get("excerpts") or [])
    links = ctx.get("links") if isinstance(ctx.get("links"), dict) else {}
    items = list(links.get("items") or [])
    items = [{**item, "cite_id": item.get("id") or None} for item in items]
    # 근거로서 강한 것부터: 신뢰도 → 근거 문장 보유 → node_context 의 결정론 순서
    # (안정 정렬이라 동점이면 그 순서가 그대로 남는다). ask.ts answerFor 와 같다.
    # 저장되지 않은 신뢰도(None)는 0.0 보다도 뒤다 — 0.0 은 저작된 값이다(감사 F8).
    items.sort(key=lambda it: (it.get("confidence") is None,
                               -float(it.get("confidence") or 0.0),
                               0 if it.get("evidence") else 1))
    base["links"] = {**links, "items": items}
    base["grounds"] = _brief_grounds(nid, base["excerpts"], items)
    base["cite_grounds"] = sorted({nid} | {
        row["cite_id"] for row in base["excerpts"] + items if row.get("cite_id")})
    if ctx.get("citation_error"):
        base["citation_error"] = ctx["citation_error"]
    # 노드는 읽었는데 그 뒤(발췌·문서 제목·관계)가 실패했으면 **반쪽**이라고 말한다.
    # node_context 는 그때도 executed 를 세우므로, 표식이 없으면 빈 관계·빈 발췌가 "연결
    # 없는 노드"로 읽힌다. 최상위 `error` 는 쓰지 않는다 — REST v1 이 해소된 답을 500 으로 바꾼다.
    why = []
    if ctx.get("error"):
        why.append(f"reading around the node failed: {ctx['error']}; "
                   "excerpts and links may be missing")
    if ctx.get("excerpt_error"):
        why.append(f"excerpts could not be searched: {ctx['excerpt_error']}")
    if links.get("executed") is False:
        why.append("links could not be read, so an empty list does not mean "
                   "the node has no relations")
    if why:
        base["partial"] = True
        base["partial_note"] = "; ".join(why)
    return base


def _brief_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _brief_norm(s) -> str:
    return str(s or "").strip().lower()


def _brief_strict_on() -> bool:
    """엄격 판정을 켤 것인가. **기본은 켜짐**이고 `VINC_BRIEF_STRICT=off` 가 옛 규칙으로
    되돌린다(비상구 — 켠 채로 두는 것이 맞다는 근거는 아래에 있다).

    옛 규칙은 토큰 하나 차이면 확신했다. 실측(2026-09-09, 정답 66문항 · 1365노드):
    답 가능 60건 중 **46건을 확신하고 그중 36건이 틀렸다**. 확신 46번에 정답 10번은
    답이 아니라 소음이고, 지식 도구에서 그것은 되묻는 것보다 나쁘다.

    엄격 판정은 확신 15건 · 그중 정답 8건 · **오답 7건**이다. 맞는 확신 둘과 노출 R@1
    한 건을 내주고 확신 오답 29건을 지운다. 의미 팔까지 켜면 확신 20 · 정답 13 · 오답 7
    이고 노출 R@1 이 0.1667 에서 0.2833 으로 오른다 — 두 변경은 **따로 재면 둘 다 안
    오르고 함께여야 오른다**(팔은 후보 1위를 9건에서 17건으로 올려 놓는데, 옛 판정이
    그 위에 확신 오답을 얹고 목록을 감췄다).
    근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]"""
    return str(os.environ.get("VINC_BRIEF_STRICT", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 확신에 필요한 토큰 차. 옛 값 1 은 **토큰 하나 차이면 확신한다**는 뜻이라 사실상
#: 문턱이 없었다. 이 값을 내리기 전에 :func:`_brief_strict_on` 의 수치를 볼 것.
_STRICT_MARGIN = 2


def _brief_sem_promote_on() -> bool:
    """되묻기 1위를 의미 우위로 확정할 것인가. **기본은 꺼짐**(`VINC_BRIEF_SEM_PROMOTE`).
    켜는 것은 규칙과 시험지의 저자가 다른 골드(VIN-57)가 같은 이득을 낼 때 결정으로 한다."""
    return truthy(os.environ.get("VINC_BRIEF_SEM_PROMOTE"))


#: 승격에 필요한 의미 우위: 의미 팔 원시 목록에서 1위가 나머지보다 가까운 거리(`sem_lead`,
#: search 가 단다). `_SEM_GAP`(0.010, 순서만 바꾼다)과 다른 값이다. 그 값을 확신 문턱으로
#: 쓰면 답 불가를 대부분 확정한다. 옮기려면 다시 잴 것.
#: 근거: [[decision:gate-d-reopens-on-the-semantic-arms-own-lead-behind-a-flag-left-off-2026-09-29]]
_SEM_PROMOTE = 0.049


def _brief_rank(rows: list[dict]) -> list[dict]:
    """해소 순위. 확정 판정과 되묻기 목록이 같은 순서를 쓰도록 한 자리에 둔다.

    안정 정렬이라 동점이면 사이드카가 준 순서(RRF)를 그대로 존중한다.
    `arms` 가 없는 호출부(직접 만든 행·구 테스트)는 전부 0 이라 순서가 안 바뀐다."""
    strict = _brief_strict_on()
    return sorted(rows, key=lambda h: (
        # 의미 팔이 자기 기준으로 단호했던 노드를 먼저 놓는다(`_SEM_GAP` 주석).
        # **순서만** 바꾼다 — `_brief_resolve` 의 `decisive` 는 여전히 어휘 여유를 요구한다.
        -1 if (strict and h.get("sem_decisive")) else 0,
        -len(h.get("arms") or []) if strict else 0,
        -_brief_int(h.get("hit_tokens")),
        -_brief_int(h.get("title_tokens"))))


def _brief_resolve(query: str, hits: list[dict]) -> tuple[str, list[dict]]:
    """node_hits → (상태, 고른 것). resolved 면 1건, choose 면 후보 여럿.

    ⚠ 그래프·의미 팔만 올라온 경우(hit_tokens 가 전부 0)는 확정하지 않는다 — 후보가
    **하나뿐이어도** 그렇다. 청크 인접이나 벡터 거리는 "관련 있다"는 뜻이지 "이것을
    물었다"는 뜻이 아니다. 이 갈래가 없던 시절 이 표면은 후보를 id 알파벳순으로 집어
    질문과 무관한 답을 냈다(ask.ts 2026-08-03 재설계).

    제목이 질문과 글자 그대로 같으면(이름을 댄 경우) 랭킹 앞에서 그 노드로 확정한다.
    같은 제목이 둘 이상이면 어느 쪽을 물었는지 모르므로 되묻는다(doc 미러 제목 충돌).
    근거: [[vinc.plan.rag-code-review-fixes-2026-09-29 §7]]

    **의미 우위 승격**(`VINC_BRIEF_SEM_PROMOTE`, 기본 꺼짐)은 위 ⚠ 의 유일한 예외다.
    랭킹 갈래에서 choose 가 될 1위가 의미 팔 원시 목록의 나머지보다 `_SEM_PROMOTE` 이상
    가까우면 토큰이 0 이어도 확정한다. 이름 댄 갈래(같은 제목 둘 포함)와 단독 후보 갈래는
    그 앞에서 돌아오므로 승격되지 않는다. 단독 후보에는 앞설 나머지가 없어 `sem_lead` 도
    안 붙는다. 유효구간 동점 가드와 답변 가능성 게이트는 brief 에서 이 뒤에 그대로 돈다.

    **엄격 판정**(`VINC_BRIEF_STRICT`, 기본 켜짐)은 순서에 `arms` 를 먼저 넣고 문턱을
    `_STRICT_MARGIN` 으로 올린다. 여러 팔이 같은 노드를 올렸다는 것은 어휘와 의미가
    함께 가리켰다는 뜻이라, 토큰 하나를 더 맞힌 것보다 강한 증거다.
    ⚠ **융합 점수(RRF)로 정렬하는 안은 기각됐다.** 팔을 켜면 좋아 보이지만 끄면 나빠진다
    (오답확정 37 → 44) — 팔이 둘뿐일 때 RRF 순위는 토큰 순서보다 못하다. 판정은 팔의
    기본값과 무관하게 모두에게 적용되므로 팔에 의존하는 규칙은 쓸 수 없다.
    근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]"""
    rows = [h for h in hits if isinstance(h, dict) and h.get("id")]
    if not rows:
        return "none", []
    q = _brief_norm(query)
    # 이름을 댄 경우는 한 행만 돌려준다 — 폴스루가 다른 이름의 노드로 넘어가면 안 된다.
    named = [h for h in rows if _brief_norm(h.get("title")) == q]
    if len(named) == 1:
        return "resolved", named
    if named:
        return "choose", _brief_rank(named)[:_brief_expose_n()]
    if len(rows) == 1:
        return ("resolved" if _brief_int(rows[0].get("hit_tokens")) > 0
                else "choose"), rows
    ranked = _brief_rank(rows)
    top, second = ranked[0], ranked[1]
    margin = _STRICT_MARGIN if _brief_strict_on() else 1   # 1 이면 현행과 글자 그대로 같다
    decisive = _brief_int(top.get("hit_tokens")) > 0 and (
        _brief_int(top.get("hit_tokens")) - _brief_int(second.get("hit_tokens")) >= margin
        or _brief_int(top.get("title_tokens"))
        - _brief_int(second.get("title_tokens")) >= margin)
    # 의미 우위 승격(플래그 뒤): 되묻기가 될 자리에서만 돈다. 1위가 의미 팔의 1위가
    # 아니면 `sem_lead` 가 없다(그 노드보다 가까운 것이 있으니 우위가 음수다).
    lead = top.get("sem_lead")
    promoted = (not decisive and _brief_sem_promote_on()
                and isinstance(lead, (int, float)) and lead >= _SEM_PROMOTE)
    # 확정이어도 **순위 전체**를 돌려준다. 1위가 상세에서 안 열릴 때 호출부가 다음
    # 후보로 넘어갈 수 있어야 하기 때문이고, 그 폴스루는 밝혀서 한다(brief 참조).
    return (("resolved", ranked) if decisive or promoted
            else ("choose", ranked[:_brief_expose_n()]))


def _brief_body(ctx: dict) -> dict:
    """본문 고르기 — one_liner → props → 정본 발췌. ask.ts pickBody 의 이식.

    one_liner 만 보면 답이 통째로 빌 수 있다(그 열이 생기기 전에 쓰인 개념은 여전히
    빈 값이다). 마지막 폴백인 발췌는 생성이 아니라 **인용**이라 출처를 함께 낸다.
    ⚠ ask.ts 와 한 곳 다르다: 같은 길이의 props 값이 여럿일 때 ask.ts 는 객체 순서에
    기대지만, 여기서는 키 이름으로 갈라 같은 질문이 늘 같은 답을 내게 했다."""
    ol = str(ctx.get("one_liner") or "").strip()
    if ol:
        return {"text": ol, "src": "one_liner", "cite": ""}
    props = ctx.get("props")
    props = props if isinstance(props, dict) else {}
    for k in _BRIEF_BODY_KEYS:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return {"text": v.strip(), "src": "props", "cite": ""}
    rest = sorted(((k, v.strip()) for k, v in props.items()
                   if isinstance(v, str) and len(v.strip()) > _BRIEF_MIN_PROP_BODY),
                  key=lambda kv: (-len(kv[1]), kv[0]))
    if rest:
        return {"text": rest[0][1], "src": "props", "cite": ""}
    for e in ctx.get("excerpts") or []:
        text = str(e.get("excerpt") or "").strip()
        if text:
            where = str(e.get("doc_title") or e.get("doc_id") or "")
            sec = str(e.get("section") or "")
            return {"text": text, "src": "doc",
                    "cite": (where + " · " + sec) if sec else where}
    return {"text": "", "src": "none", "cite": ""}


def _brief_grounds(node_id: str, excerpts: list[dict], items: list[dict]) -> list[str]:
    """모든 주장이 딛고 선 id — 노드·정본 문서·관계 상대. 정렬해 결정론으로 둔다.

    축소기가 발췌를 덜어내도 이 목록은 남으므로, 잘린 재료를 doc_id 로 다시 가져올
    수 있다."""
    out = {node_id} if node_id else set()
    out |= {str(e.get("doc_id") or "") for e in excerpts}
    out |= {str(i.get("id") or "") for i in items}
    return sorted(x for x in out if x)


def _brief_answerability_on() -> bool:
    """답변 가능성 게이트를 켤 것인가. **기본은 꺼짐**(`VINC_BRIEF_ANSWERABILITY`).

    끄고 켤 수 있어야 하는 이유: 이 문턱은 정답 81문항 중 **확정 18건** 위에서 골랐고,
    답 불가 21문항 중 15문항을 이 세션에 내가 썼다. 즉 시험지 절반이 이 신호를 편들
    수 있다. 기본값을 바꾸기 전에 남이 쓴 문항이 늘어나야 한다."""
    return truthy(os.environ.get("VINC_BRIEF_ANSWERABILITY"))


def _brief_choose_material_on() -> bool:
    """choose 에서 첫 후보 둘레로 답변 재료를 모을 것인가. **기본은 꺼짐**
    (`VINC_BRIEF_CHOOSE_MATERIAL`). 꺼져 있으면 응답은 이 플래그가 없던 때와 같다.
    근거: [[decision:option-c-assembly-is-built-behind-a-flag-left-off-2026-09-13]]"""
    return truthy(os.environ.get("VINC_BRIEF_CHOOSE_MATERIAL"))


def _brief_expose_n() -> int:
    """되묻기(choose)에 싣는 후보 수. 기본은 `_BRIEF_CANDIDATES`.

    `VINC_BRIEF_EXPOSE` 는 노출을 바꿔 재기 위한 손잡이다(X1). 기본값을 바꾸는 것은
    결정 노드로만 한다. 잘못된 값은 기본으로 읽는다.
    근거: [[vinc.plan.rag-eval-v2-0-9-6]]"""
    raw = str(os.environ.get("VINC_BRIEF_EXPOSE", "")).strip()
    try:
        return max(1, int(raw)) if raw else _BRIEF_CANDIDATES
    except ValueError:
        return _BRIEF_CANDIDATES


def _brief_list_resolved_on() -> bool:
    """확정(resolved)에도 순위 목록을 `candidates` 로 곁에 실을 것인가. **기본은 꺼짐**
    (`VINC_BRIEF_LIST_RESOLVED`). 확정이 틀렸을 때 그 뒤에 가려진 정답을 부르는 쪽이
    볼 수 있게 한다(X1). status 와 node 는 그대로다 — 목록이 답을 바꾸지 않는다.
    근거: [[vinc.plan.rag-eval-v2-0-9-6]]"""
    return truthy(os.environ.get("VINC_BRIEF_LIST_RESOLVED"))


def _brief_candidate_rows(picked: list[dict]) -> list[dict]:
    """노드 후보를 brief 가 내보내는 후보 꼴로 줄인다."""
    return [{
        "id": h.get("id") or "",
        "cite_id": h.get("id") or None,
        "title": h.get("title") or (h.get("id") or ""),
        "domain": h.get("domain") or "", "kind": h.get("kind") or "",
        "matched_in": h.get("matched_in") or "",
        "hit_tokens": _brief_int(h.get("hit_tokens")),
        "title_tokens": _brief_int(h.get("title_tokens")),
        **{k: h[k] for k in ("temporal", "created_at", "updated_at", "content_time",
                             "valid_from", "valid_until", "validity", "superseded_by") if k in h},
    } for h in picked]


#: 후보 재료로 옮겨 싣는 키 — 확정 답의 재료 꼴(:func:`_brief_material`)과 같다.
_BRIEF_MATERIAL_KEYS = ("node", "one_liner", "body_src", "body_cite", "excerpts",
                        "links", "grounds", "cite_grounds", "citation_error",
                        "validity_note", "partial", "partial_note")


def _brief_candidate_material(cand: dict, **kw) -> dict:
    """choose 의 첫 후보 둘레 재료. 그 후보의 **정확한 제목으로 다시 묻는다** — 계약이
    호출부에 시키는 되묻기 그대로다. 다시 물은 답이 **그 후보로** 확정될 때만 재료를
    싣고(landed), 아니면 못 실었다고 밝힌다. 다른 노드의 재료를 이 후보의 것으로 내면
    안 된다.

    ⚠ 제목 임베딩은 넘기지 않는다 — 이 층에는 인코더가 없다. 제목 완전일치는 팔과
    무관하게 확정하므로(:func:`_brief_resolve`) 갈릴 수 있는 곳은 같은 제목이 둘인
    경우이고, 그것은 landed=False 로 드러난다."""
    cid = str(cand.get("id") or "")
    again = brief(str(cand.get("title") or cid), _reask=True, **kw)
    out: dict = {"candidate": cid}
    if again.get("status") == "resolved" and (again.get("node") or {}).get("id") == cid:
        out["landed"] = True
        out.update({k: again[k] for k in _BRIEF_MATERIAL_KEYS if k in again})
    else:
        out["landed"] = False
        out["status"] = again.get("status") or "none"
        out["note"] = "asking again by this candidate's exact title did not resolve to it"
    return out


#: 제목이 안 먹은 부분 중 **검색이 가져온 어디에도 없는** 토큰의 비율이 이 값을 넘으면
#: 확신하지 않는다. 실측으로 고른 자리다(2026-09-10, 정답 81문항 · 확신 18건):
#:
#:     문턱   확신  정답  오답  답불가확정
#:     꺼짐    18     8     7      3
#:     0.34     9     5     4      0
#:     0.67    14     7     7      0
#:
#: 0.34 는 오답 확정도 셋 줄이지만 그것은 **이 게이트의 일이 아니라 랭킹의 일**이고
#: (`_brief_strict_on` 이 한 것), 그 대가로 맞는 확신을 둘 더 잃는다. 0.67 은 목적
#: (답 불가에 확신을 주지 않는다)을 최소 비용으로 달성한다 — 맞는 확신 하나.
#:
#: ⚠ 잃는 그 하나는 **형태 변화** 때문이다: 질문의 `sent` 가 코퍼스의 `send` 와 표면이
#: 달라 고아로 보인다. 이것이 이 게이트의 알려진 천장이다.
#:
#: ⚠ **"어간 분석을 붙이면 된다"는 처방이 아니다 — 기각됐다(2026-09-10).** 한때 이 자리에
#: 그렇게 적혀 있었으나 재 본 적이 없는 추측이었다. 붙이면 표면이 뭉쳐 고아율이 함께
#: 낮아지므로 게이트가 무뎌질 수 있고, 그 방향은 측정되지 않았다. 그리고 이 리포에는
#: 반대 방향의 실측이 이미 있다 — 한국어 전문 검색이 지는 원인은 어간 처리가 아니었다
#: (`scripts/r1c_ko_column.py`). 천장을 넘으려면 새 측정이 필요하고, 그 측정 전에는
#: 이 문장을 처방으로 읽지 말 것.
_ORPHAN_BLOCK = 0.67


def _brief_orphan_residual(query: str, picked_title, res: dict) -> float:
    """질문에서 확정 노드의 제목이 안 먹은 부분 중, **아무 데서도 못 본** 토큰의 비율.

    어떤 토큰이 검색이 가져온 후보 제목과 청크 발췌 어디에도 없다면, 그 노드가 아니라
    **어느 노드도** 질문의 그 부분을 다루지 못한다. 주제는 맞는데 물은 것이 없는 자리다.

    코퍼스 어휘 전량과 대조하는 것이 정확하지만 질의당 402ms 다(실측). 이미 가져온
    것만 보는 이 근사는 공짜이고, 운용 문턱(0.34~0.50)에서 정확판과 **결과가 같았다**.
    근거: [[record:answerability-is-a-residual-coverage-question-2026-09-10]]"""
    qt = normalize_query_tokens(query)
    if not qt:
        return 0.0
    tt = set(normalize_query_tokens(str(picked_title or "")))
    resid = [t for t in qt if t not in tt]
    if not resid:
        return 0.0
    seen: set = set()
    for h in res.get("node_hits") or []:
        seen |= set(normalize_query_tokens(str(h.get("title") or ""), cap=64))
    for r in res.get("rows") or []:
        seen |= set(normalize_query_tokens(str(r.get("snippet") or ""), cap=256))
    return sum(1 for t in resid if t not in seen) / len(resid)


_BRIEF_SEMANTIC_NONE_DISTANCE = 0.42970955319294174


def _brief_semantic_none(res: dict) -> bool:
    """Reject distant, exclusively zero-token semantic candidates on a healthy arm.

    VIN-327: the owner approved the measured cutoff with zero answerable losses
    on the pinned evaluation set. Lexical/graph matches and unavailable measurements
    keep their existing resolution behavior. See [[vinc.manual.rag]].
    """
    hits = res.get("node_hits") or []
    if not hits or any(res.get(k) for k in (
            "error", "node_error", "vector_rebuilding")):
        return False
    # Legacy vectors lack runtime stamps but the semantic arm still ran. The
    # pinned evaluation counts this specific notice separately from failures.
    note = str(res.get("vector_note") or "")
    if note.replace(UNSTAMPED_RUNTIME_NOTE, "").strip("; "):
        return False
    if res.get("encoder_runtime_warning") and note != UNSTAMPED_RUNTIME_NOTE:
        return False
    if any(type(h.get("hit_tokens")) is not int or h["hit_tokens"] != 0
           for h in hits):
        return False
    # Lexical/graph evidence is outside semantic-only abstention.
    if any(set(h.get("arms") or []) != {"semantic"} for h in hits):
        return False
    distances = [h.get("distance") for h in hits]
    if any(type(d) not in (int, float) or not math.isfinite(d)
           or not 0.0 <= d <= 2.0 for d in distances):
        return False
    return min(distances) > _BRIEF_SEMANTIC_NONE_DISTANCE


def _brief_current_uncertain(store, *, owner: str, answer: dict, hits: list[dict]) -> bool:
    """current 모드에서 기권할 것인가 — 답(1위)과, 답과 **주장 키가 같은** 후보만 본다(B5).

    주장 키(entity·predicate·scope)가 다른 후보는 다른 주장이라 그 날짜가 이 답의 현재
    상태를 흔들지 않는다. 키를 못 읽으면 기권한다(모르면 고르지 않는다)."""
    from store import search_temporal as temporal

    def shaky(h: dict) -> bool:
        t = h.get("temporal") or {}
        return (t.get("validity") == "unknown"
                or t.get("claim_status") in ("conflict", "cycle", "unknown"))

    if shaky(answer):
        return True
    rest = [h for h in hits if isinstance(h, dict) and h.get("id")
            and h.get("id") != answer.get("id")]
    if not rest:
        return False
    try:
        sources = temporal.node_sources(store, [answer.get("id")] + [h["id"] for h in rest],
                                        owner=owner)
    except Exception:
        return True
    key = temporal.claim_key(sources.get(answer.get("id"), {}))
    if key is None:
        return False
    return any(shaky(h) for h in rest
               if temporal.claim_key(sources.get(h["id"], {})) == key)


def _brief_holds(ctx: dict, scope) -> bool:
    """직참조 노드가 as_of 기간에 성립했나. 사건은 날짜가 시작이고 끝나지 않는다 —
    `api.temporal_read` 와 검색이 같은 판정을 쓴다(감사 F14)."""
    from store.search_temporal import overlaps_scope
    t = ctx.get("temporal") or ctx
    if (ctx.get("node_kind") or ctx.get("kind")) == "episode":
        return holds_during(t.get("content_time") or "", "", scope)
    return overlaps_scope(t.get("valid_from"), t.get("valid_until"), scope)


def _brief_known(row: dict, known_scope) -> bool | None:
    """known_at 기간이 끝나기 전에 저장됐나. 저장 시각을 모르면 None(감추지 않는다)."""
    from store.search_temporal import instant
    created = instant((row.get("temporal") or row).get("created_at"))
    if created is None:
        return None
    return created < instant(known_scope[1])


def _brief_excerpt_kept(row: dict, *, mode, historical: bool, as_of_scope) -> bool:
    """발췌 한 줄을 그 문서의 유효구간으로 판정한다. 검색이 청크를 거르는 규칙과 같다
    (`vinc_rag.retrieval._drop_retired_doc_rows`, current 는 계보까지)."""
    from store.search_temporal import overlaps_scope
    t = row.get("temporal") or {}
    vf, vu = t.get("valid_from") or "", t.get("valid_until") or ""
    if mode == "current":
        return (t.get("validity") not in ("historical", "future")
                and t.get("claim_status") != "superseded")
    if as_of_scope is not None:
        return overlaps_scope(vf, vu, as_of_scope)
    if historical:
        return True
    return validity_state(vf, vu) != "historical"


def _brief_scope_material(ctx: dict, store, base: dict, *, owner: str, mode,
                          historical: bool, as_of_scope, known_scope) -> tuple[dict, str]:
    """확정 재료(발췌·관계)를 요청의 시간 규칙으로 거른다. 걸러진 ctx 사본과, 감춘 것이
    있으면 알림 문장을 돌려준다(알림은 노드 자신의 알림 뒤에 단다).

    - 발췌(B2): 문서 유효구간 — 기본은 은퇴한 문서, as_of 는 그 기간, current 는 현재·계보.
      known_at 은 문서가 처음 저장된 시각. 검색이 감추는 청크를 brief 가 인용하면 안 된다.
    - 관계(F14·B1): as_of 와 known_at 을 `api.temporal_read.Gate` 로 판정한다 —
      vinc_neighbors 가 같은 요청에서 감추는 노드를 여기서도 감춘다. 수(out·in·attached·
      total)도 감춘 만큼 줄인다. 잘린 창 뒤(next_offset)의 관계는 여기서 판정하지 않는다.
    감춘 수는 validity_filtered(구간)와 known_at_filtered(저장 시각)에 더한다."""
    ctx = dict(ctx)
    hidden_validity = hidden_known = 0
    unknown: set[str] = set()
    kept = []
    for row in ctx.get("excerpts") or []:
        if not _brief_excerpt_kept(row, mode=mode, historical=historical,
                                   as_of_scope=as_of_scope):
            hidden_validity += 1
            continue
        if known_scope is not None:
            known = _brief_known(row, known_scope)
            if known is None:
                unknown.add("doc:" + str(row.get("doc_id") or ""))
            elif not known:
                hidden_known += 1
                continue
        kept.append(row)
    ctx["excerpts"] = kept
    links = ctx.get("links")
    if (isinstance(links, dict) and links.get("items")
            and (as_of_scope is not None or known_scope is not None)):
        from . import temporal_read
        gate = temporal_read.Gate(store, [i.get("id") for i in links["items"]], owner=owner,
                                  as_of_scope=as_of_scope, known_scope=known_scope)
        links = dict(links)
        items = []
        for item in links["items"]:
            if gate.keep(item.get("id")):
                items.append(item)
                continue
            d = item.get("dir")
            if d == "att":
                links["attached"] = int(links.get("attached") or 0) - 1
            elif d in ("out", "in"):
                links[d] = int(links.get(d) or 0) - 1
                links["degree"] = int(links.get("degree") or 0) - 1
            links["total"] = int(links.get("total") or 0) - 1
        links["items"] = items
        ctx["links"] = links
        hidden_validity += gate.hidden_validity
        hidden_known += gate.hidden_known
        unknown |= gate.unknown_known
    note = ""
    if hidden_validity:
        base["validity_filtered"] = int(base.get("validity_filtered") or 0) + hidden_validity
        note = ("hid excerpts or relations outside the requested time scope"
                if as_of_scope is not None or mode == "current" else
                "hid excerpts of documents whose validity ended; "
                "pass historical=true to include them")
    if known_scope is not None:
        base["known_at_filtered"] = int(base.get("known_at_filtered") or 0) + hidden_known
        base["known_at_unknown"] = int(base.get("known_at_unknown") or 0) + len(unknown)
    return ctx, note


def _brief_answer(base: dict, cand: dict, ctx: dict, store, *, owner: str, mode,
                  historical: bool, as_of_scope, known_scope) -> dict:
    """시간 규칙으로 거른 재료로 답을 채운다. 직참조와 랭킹 경로가 같은 꼬리를 쓴다."""
    ctx, note = _brief_scope_material(ctx, store, base, owner=owner, mode=mode,
                                      historical=historical, as_of_scope=as_of_scope,
                                      known_scope=known_scope)
    out = _brief_material(base, cand, ctx)
    if note:
        out.setdefault("validity_note", note)
    return out


def brief(query: str, *, owner: str, query_embedding: list[float] | None = None,
          max_excerpts: int = 3, historical: bool = False, as_of: str | None = None,
          mode: str | None = None, known_at: str | None = None,
          cfg=None, store=None, _reask: bool = False,
          query_runtime: str | None = None) -> dict:
    """질문 하나 → 답변 재료 한 벌(조인 읽기, 조회 전용).

    질문에 노드 id 가 그대로 들어 있으면 그 노드를 **직참조**한다(검색·랭킹 생략,
    resolved_via="id"). 아니면 search 로 후보를 찾아 해소하고, 확정되면
    node_context 로 본문·발췌·관계를 한 번에 붙인다. 확정이 안 되면
    status="choose" 로 후보를 돌려주지 **추측하지 않는다**.

    반환: {kind, executed, query, status: resolved|choose|none, node:{id,title,
           domain,kind}, one_liner, body_src, body_cite, excerpts:[...],
           links:{degree(=out+in),out,in,attached,total(=degree+attached, 연결수),
           items,truncated} (items 는 evidence 불리언과
           evidence_text 문장을 함께 싣는다), candidates:[...], grounds:[id...],
           resolved_via("id" — 직참조로 확정된 경우에만),
           fell_through/unreadable(안 열린 후보·질문 속 id 의 공개),
           search_error(청크 팔이 실패한 사유 — 해소는 노드 팔로 계속한다),
           partial/partial_note(노드는 읽었는데 발췌·관계를 못 읽었을 때),
           candidate_material(`VINC_BRIEF_CHOOSE_MATERIAL` 을 켠 choose 에서만)}.

    시간 인자(`as_of`·`mode`·`historical`·`known_at`)는 여기서 전부 적용한다 —
    해소(검색), 직참조 노드, 발췌, 관계 모두에. 사이드카가 뒤에서 다시 거르지 않는다
    (감사 B1·B2·F14, 2026-10-16). `known_at` 은 저장 시각 컷오프이고 과거 본문을
    복원하지 않는다(검색과 같다).
    근거: [[vinc.plan.mcp-agent-surface §4]]"""
    cfg = cfg or load_config()
    base: dict = {"kind": "brief", "executed": False, "query": query,
                  "status": "none", "candidates": [], "grounds": []}
    # Direct-ID reads bypass search by design, but an explicit time request must
    # still be validated and applied before the early return.
    try:
        from store.search_temporal import mode_scope
        historical, direct_scope, direct_scope_info = mode_scope(mode, historical, as_of, query)
    except ValueError as exc:
        base["note"] = base["error"] = f"invalid_temporal_mode: {exc}" if mode else f"invalid_as_of: {exc}"
        return base
    known_scope = time_scope(known_at) if (known_at or "").strip() else None
    if (known_at or "").strip() and known_scope is None:
        base["note"] = base["error"] = f"invalid_time_scope: invalid known_at {known_at!r}"
        return base
    if direct_scope_info is not None:
        base["time_scope"] = direct_scope_info
    if known_scope is not None:
        base["time_scope"] = {**(base.get("time_scope") or {}), "known_at": {
            "value": known_at.strip(), "from": known_scope[0], "until": known_scope[1]}}
    # 재료(발췌·관계)를 거를 as_of 기간. current 는 기간이 아니라 상태로 판정한다.
    as_of_scope = direct_scope if mode != "current" else None
    rule = {"owner": owner, "mode": mode, "historical": historical,
            "as_of_scope": as_of_scope, "known_scope": known_scope}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        # 질문이 노드 id 를 그대로 담고 있으면 검색·랭킹을 거치지 않고 그 노드를
        # 연다 — 재측정 체인 arm 의 v1.1 수정("묻는 쪽이 이미 준 id 를 검색하는
        # 것은 낭비")을 도구 안으로 옮긴 것. 태스크 C 가 잰 실패가 정확히 이
        # 모양이었다: id 가 질문에 있는데 긴 제목의 기록이 랭킹을 이겨 **조용한
        # 오답**이 났다. 랭킹 자체는 여기서 건드리지 않는다 — id 없는 질의의
        # 동점 처리(제목 언어 갭)는 열린 결정으로 남는다:
        # [[record:title-language-gap-survives-the-vector-arm-2026-08-30]]
        unread_ids: list[dict] = []
        out_of_scope_ids: list[str] = []
        known_out: list[str] = []
        # 개방 타입 id 는 네 접두 id 가 없을 때만, 실재하는 것만 연다(F12).
        qids = _brief_query_ids(query) or _brief_entity_ids(store, query, owner=owner)
        for qid in qids:
            got = node_context(owner=owner, node=qid, limit=max_excerpts,
                               with_evidence=True, store=store)
            if got.get("executed"):
                if mode == "current" and got.get("temporal", {}).get("validity") in ("future", "historical"):
                    out_of_scope_ids.append(qid)
                    continue
                if mode != "current" and direct_scope is not None and not _brief_holds(
                        got, direct_scope):
                    out_of_scope_ids.append(qid)
                    continue
                if known_scope is not None:
                    # B1 — 컷오프 뒤에 저장된 노드는 그때 그래프에 없었다. 저장 시각을
                    # 모르면 감추지 않고 센다(검색과 같다).
                    known = _brief_known(got, known_scope)
                    if known is False:
                        known_out.append(qid)
                        continue
                    if known is None:
                        base["known_at_unknown"] = int(base.get("known_at_unknown") or 0) + 1
                base["executed"] = True
                base["status"] = "resolved"
                if mode == "current" and (got.get("temporal", {}).get("validity") == "unknown" or got.get("temporal", {}).get("claim_status") in ("conflict", "cycle", "superseded", "unknown")):
                    base["status"] = "choose"
                    base["candidates"] = _brief_candidate_rows([{"id": qid, "kind": got.get("node_kind"), "title": got.get("title"), "temporal": got.get("temporal")}])
                    base["validity_note"] = "the current claim is uncertain; no current state was chosen"
                    return base
                base["resolved_via"] = "id"
                # 앞선 후보가 안 열려 ASCII 접두나 다음 id 로 열었으면 그 대체를
                # 밝힌다 — 잘린 접두는 우연히 실재하는 다른 노드일 수 있다.
                if unread_ids:
                    base["fell_through"] = list(unread_ids)
                if known_scope is not None:
                    base.setdefault("known_at_filtered", 0)
                return _brief_answer(base, {"id": qid}, got, store, **rule)
            unread_ids.append({"id": qid,
                               "note": got.get("note") or ("node not found: " + qid)})
        # 질문이 준 id 가 하나도 안 열리면 **없다고 답한다**(status="none" + unreadable).
        # 랭킹으로 넘어가면 다른 노드가 resolved 로 나오고, 호출부는 그것을 자기가
        # 물은 노드로 읽는다 — fell_through 한 줄로는 그 대체를 막지 못했다.
        # 접두 되물림(위 루프)은 여전히 id 경로 안에서의 대체라 그대로 둔다.
        # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
        if unread_ids:
            base["executed"] = True
            base["unreadable"] = list(unread_ids)
            base["note"] = unread_ids[0]["note"]
            return base
        if out_of_scope_ids or known_out:
            base["executed"] = True
            if out_of_scope_ids:
                base["validity_filtered"] = len(out_of_scope_ids)
                base["validity_note"] = (
                    "the referenced node is not current" if mode == "current" else
                    f"the referenced node did not hold during {direct_scope_info['as_of']}")
            if known_out:
                base["known_at_filtered"] = len(known_out)
                base["known_at_node_note"] = (
                    f"the referenced node was recorded after {known_at.strip()}; "
                    "the graph did not hold it then")
            return base
        res = search(query, query_embedding=query_embedding, top_k=_BRIEF_TOP_K,
                     owner=owner, store=store, historical=historical,
                     as_of=as_of, mode=mode, known_at=known_at,
                     query_runtime=query_runtime)
        # executed 는 노드 팔 기준이다 — 청크 팔만 죽어도 노드 팔은 owner 스코프 결과를 낸다.
        base["executed"] = bool(res.get("executed") or res.get("node_hits"))
        # 벡터 팔의 상태도 함께 나른다. 두 표면이 같은 임베딩 창구를 쓰는데 `/search`
        # 만 강등을 말하면 같은 고장이 도구에 따라 보이거나 안 보인다. 규칙은 `/search`
        # 와 같다 — **검색이 실제로 돈 경우에만** 싣는다(id 직참조는 이 줄에 오기 전에
        # 반환하므로 안 돈 팔에 이름이 붙지 않는다. 게이트① 동등성은 q.brief 한 곳에서
        # 세우는 것으로 지킨다 — HTTP 계층에서 붙이면 CLI 와 갈린다).
        for k in ("note", "node_error",
                  "arms", "vector_note", "vector_rebuilding", "time_scope",
                  "encoder_runtime_warning",
                  "validity_filtered", "validity_note", "validity_best_dropped",
                  "temporal_error", "partial", "mode", "claim_assessment_scope"):
            if res.get(k):
                base[k] = res[k]
        if known_scope is not None:
            # B1 — 검색이 known_at 으로 감춘 수를 그대로 잇는다(0 도 값이다: 거르기가 돌았다).
            base["known_at_filtered"] = int(res.get("known_at_filtered") or 0)
            base["known_at_unknown"] = int(res.get("known_at_unknown") or 0)
        if mode == "current" and base.get("validity_note"):
            # D15 — current 는 as_of 를 받지 않는다. 검색의 알림이 기간 문장("drop as_of")을
            # 달고 와도 여기서는 상태로 말한다.
            base["validity_note"] = ("hid claims that are not current; "
                                     "use mode=historical to include them")
        # 검색의 `error` 는 청크 팔 실패다(FTS 잔해·확장 미가용·색인 부재 …). 같은 검색의
        # 노드 팔 결과로 해소를 계속하고 사유는 `search_error` 로 싣는다 — 최상위 `error` 는
        # REST v1 이 HTTP 500 으로 바꾸므로 해소된 답에 쓸 수 없다. 노드 팔도 빈손일 때만
        # 여기서 돌아온다. 근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
        if res.get("error"):
            base["search_error"] = res["error"]
            if not res.get("node_hits"):
                return base
        if _brief_semantic_none(res):
            base["note"] = "no sufficiently close semantic candidate"
            return base
        status, picked = _brief_resolve(query, res.get("node_hits") or [])
        # B5 — current 의 기권은 답(1위)과 그 주장 키를 공유하는 후보만 본다. 예전에는
        # 후보 **아무나** 날짜를 모르면 기권했고, 개념은 구간이 없어 늘 unknown 이라 개념
        # 후보 하나가 다른 주장의 답을 막았다.
        if mode == "current" and picked and _brief_current_uncertain(
                store, owner=owner, answer=picked[0], hits=res.get("node_hits") or []):
            status = "choose"
            base["validity_note"] = "current claim evidence is conflicting or unknown; no current state was chosen"
        # D11 — current 에서 동점이던 노드가 은퇴했으면 남은 노드로 확정한다. 의도된 뜻이다:
        # current 는 "지금 성립하는 것"을 묻고, 은퇴한 노드는 그 질문의 경쟁자가 아니다.
        # 아래 동점 가드는 일반 모드에서 감춘 행의 점수(validity_best_dropped)를 보는데,
        # current 에서는 finish_nodes 가 은퇴한 노드를 감추며 그 점수를 남기지 않으므로
        # 가드가 서지 않는다. 그 차이가 이 뜻을 구현한다(감사 D11, 2026-10-16).
        # 🔴 **유효구간 필터가 동점을 깨서 확신을 만들면 안 된다.** 지운 행이 남은 1위와
        # 같은 점수였다면, 원래 이 질문은 "모르겠다"(choose)였다. 그 자리에서 확정을
        # 주면 필터가 **보이는 것**이 아니라 **답**을 바꾼 것이고, 그건 §4.2 가 의도한
        # 바가 아니다. 실측(2026-09-09): 이 갈래가 없는 동안 답 불가 질문 6건 중 1건이
        # 확정됐다(정직성 0.0 → 0.1667). 동점이던 Record 둘 중 하나가 걸러지면서 남은
        # 하나가 단독 승자가 된 모양이었다.
        # 근거: [[record:the-validity-filter-broke-a-tie-and-manufactured-confidence-2026-09-09]]
        if status == "resolved" and picked and base.get("validity_filtered"):
            _bd = tuple(base.get("validity_best_dropped") or (0, 0))
            _w = (_brief_int(picked[0].get("hit_tokens")),
                  _brief_int(picked[0].get("title_tokens")))
            if _bd >= _w:
                status = "choose"
                # 시점 읽기(as_of)에서도 같은 가드다 — 그 기간 밖이라 감춘 것이 동점이었으면
                # 확정하지 않는다(계획의 "tie guard applies unchanged"). 알림은 경우마다
                # 다르다(D15): current 는 as_of 를 받지 않으므로 "drop as_of" 라고 말하지 않는다.
                _ts = base.get("time_scope") or {}
                if mode == "current":
                    base["validity_note"] = (
                        "a claim that is not current tied with the leading candidate, "
                        "so this was not resolved; use mode=historical to see it")
                elif _ts.get("from"):
                    base["validity_note"] = (
                        "a claim that did not hold during {} tied with the leading candidate, "
                        "so this was not resolved; drop as_of to see it".format(_ts["as_of"]))
                else:
                    base["validity_note"] = (
                        "a claim that is no longer current tied with the leading candidate, "
                        "so this was not resolved; pass historical=true to see it")
        # 🔴 **주제를 찾은 것과 질문에 답하는 것은 다르다.** 질문이 노드 제목을 그대로
        # 인용하고 그 **속성**을 물으면(“…는 문장은 누가 썼나”) 제목이 통째로 맞아
        # 토큰 여유가 넘치고, 확신 문턱은 그것을 못 거른다 — 랭킹 문제가 아니기 때문이다.
        # 실측(2026-09-10, 답 불가 21문항): 이 갈래가 없으면 '주제는 있는데 속성이 없는'
        # 7건 중 3건을 확신했다. 이 게이트는 그 셋을 전부 막고(답불가 확정 0.1429 → 0.0000)
        # 맞는 확신 하나를 잃는다. **노출 R@1 은 안 변한다** — 강등된 질문도 후보 목록에
        # 정답을 1위로 싣기 때문이다.
        # ⚠ 기본은 꺼져 있다 — 이유는 :func:`_brief_answerability_on` 에 있다.
        if status == "resolved" and picked and _brief_answerability_on():
            _orphan = _brief_orphan_residual(query, picked[0].get("title"), res)
            if _orphan >= _ORPHAN_BLOCK:
                status = "choose"
                base["answerability_note"] = (
                    "the topic matched but the question asks for something this graph "
                    "does not hold, so this was not resolved")
        base["status"] = status
        if status == "choose":
            # 되묻기 목록은 고른 것을 앞에 두고 나머지 풀을 순위대로 채워 노출 수에서
            # **한 번** 자른다. 강등(유효구간 동점·답변 가능성)은 확정용 목록을 그대로
            # 넘기므로 여기서 안 자르면 노출보다 길고, 제목 완전일치가 강등되면 한 줄뿐이다.
            _seen = {h.get("id") for h in picked}
            picked = picked + [h for h in _brief_rank(
                [h for h in res.get("node_hits") or [] if isinstance(h, dict) and h.get("id")])
                if h.get("id") not in _seen]
            base["candidates"] = _brief_candidate_rows(picked[:_brief_expose_n()])
            # 플래그가 켜져 있으면 첫 후보의 재료를 **따로 한 키에** 싣는다. status 는
            # choose, candidates 는 그대로다 — 후보의 재료가 확정 답으로 읽히면 안 된다.
            # `_reask` 는 되묻기 자신이 다시 되묻지 않게 막는다(같은 제목이 또 choose 면
            # 끝이 없다). 축소기는 이 키 안의 발췌·관계를 확정 답과 같은 순서로 던다.
            # 근거: [[decision:option-c-assembly-is-built-behind-a-flag-left-off-2026-09-13]]
            if base["candidates"] and not _reask and _brief_choose_material_on():
                base["candidate_material"] = _brief_candidate_material(
                    base["candidates"][0], owner=owner, max_excerpts=max_excerpts,
                    historical=historical, mode=mode, as_of=as_of, known_at=known_at,
                    cfg=cfg, store=store)
            return base
        if status == "none":
            return base
        # 1위가 상세에서 안 열리면 다음 후보로 넘어간다. 그 전에는 곧장
        # status="none" 을 냈는데, 계약상 none 은 "아무것도 안 맞았다"라서 **맞았는데
        # 못 읽었다**를 안 맞은 것처럼 보고했다. 관계마다 근거 문장을 함께 받는다 —
        # 에이전트는 그것을 인용해야 한다.
        #
        # ⚠ 폴스루는 밝혀서 한다. 조용히 다음 후보를 답하면 이 표면은 "질문에 맞는
        # 노드"가 아니라 "열리기만 하는 노드"를 답하게 되고, 그것은 이 도구가 임의
        # 임계값을 거부하며 지키려던 성질을 정반대로 뒤집는다.
        hit = ctx = None
        unread: list[dict] = []
        for cand in picked[:_BRIEF_FALLTHROUGH]:
            cid = str(cand.get("id") or "")
            if not cid:
                continue
            got = node_context(owner=owner, node=cid, limit=max_excerpts,
                               with_evidence=True, store=store)
            if got.get("executed"):
                hit, ctx = cand, got
                break
            unread.append({"id": cid,
                           "note": got.get("note") or ("node not found: " + cid)})
        if ctx is None:
            base["status"] = "none"
            base["note"] = (unread[0]["note"] if unread
                            else "node not found: " + str(picked[0].get("id") or ""))
            if unread:
                base["unreadable"] = unread
            return base
        if unread:
            base["fell_through"] = unread
        out = _brief_answer(base, hit, ctx, store, **rule)
        if _brief_list_resolved_on():
            # 확정은 그대로 두고 순위 목록을 곁에 싣는다. 첫 자리는 순위 1위다.
            out["candidates"] = _brief_candidate_rows(picked[:_brief_expose_n()])
        return out
    finally:
        if owns:
            store.close()


# edge_context 허용 관계 — 흐름 뷰가 선으로 그리는 5종. etype 은 Cypher 에
# 보간되므로 **화이트리스트 검증이 보안 경계다**(임의 라벨 주입 차단).
# ATTACHES_TO 도 v16 부터 evidence 열을 갖지만 여기 없는 것은 의도다 — 부착은
# 흐름 뷰의 선이 아니라 상세 패널 카드로 그려지고, 그 근거는 node_context 의
# links.items[].evidence_text 가 이미 나른다. 선으로 승격할 때 함께 열 것.
_EDGE_CTX_TYPES = ("PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM",
                   "RELATED_TO", "ABOUT")


def edge_context(*, owner: str, src: str, dst: str, etype: str,
                 cfg=None, store=None) -> dict:
    """엣지→정본 컨텍스트(D45-V 1단계) — 흐름 뷰에서 선(관계)을 눌렀을 때
    "왜 A 가 B 로 이어지는가"에 답한다. 조회 전용.

    반환: 관계의 evidence(원문 구절)·confidence + 양끝 노드 메타(title·one_liner/
    summary)와 대표 정본 발췌 1건씩(MENTIONS 첫 건 — 개념·결정; Episode 는
    summary가 곧 설명). RELATED_TO는 무방향 매치(저장 방향 무관)다.
    관계별 끝점 조합은 graph·node_context와 같은 스키마 파생 읽기 계약을 따른다.
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
        arrow = "-" if etype == "RELATED_TO" else "->"
        arms = []
        # 스키마 파생 끝점 계약을 공유한다. Decision에는 one_liner 열이 없다.
        for (sl, sp, dl, dp), rels in _GRAPH_READ_PAIRS.items():
            if etype not in rels:
                continue
            def detail(alias: str, label: str) -> str:
                column = {"Concept": "one_liner", "Episode": "summary"}.get(label)
                return f"{alias}.{column}" if column else "''"
            arms.append(
                f"MATCH (a:{sl})-[r:{etype}]{arrow}(b:{dl}) "
                f"WHERE a.{sp} = $s AND b.{dp} = $d "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "RETURN r.evidence AS ev, r.confidence AS cf, "
                f"a.title AS at, {detail('a', sl)} AS ad, "
                f"b.title AS bt, {detail('b', dl)} AS bd, "
                f"'{sl}' AS src_label, '{dl}' AS dst_label")
        rows = store.run(" UNION ALL ".join(arms),
                         {"s": pkns.key(owner, src), "d": pkns.key(owner, dst),
                          "owner": owner}) or []
        if not rows:
            base["note"] = "unknown edge (또는 소유 스코프 밖)"
            return base
        r = rows[0]
        def _mention_excerpt(cid: str, label: str) -> dict | None:
            if label not in _SEMANTIC_PK:
                return None
            m = store.run(
                f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{label}) "
                f"WHERE c.{_SEMANTIC_PK[label]} = $id AND c.owner_id = $owner "
                "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                "RETURN ch.doc_id AS doc_id, ch.text AS text "
                "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
                "ch.doc_id, ch.chunk_index LIMIT 1",
                {"id": pkns.key(owner, cid), "owner": owner}) or []
            if not m:
                return None
            return {"doc_id": m[0].get("doc_id") or "",
                    "excerpt": " ".join((m[0].get("text") or "").split())[:240]}
        base.update(
            executed=True,
            evidence=r.get("ev") or "",
            confidence=float(r.get("cf") or 0.0),
            src={"id": src, "title": r.get("at") or src, "one_liner": r.get("ad") or "",
                 "excerpt": _mention_excerpt(src, r["src_label"])},
            dst={"id": dst, "title": r.get("bt") or dst, "one_liner": r.get("bd") or "",
                 "excerpt": _mention_excerpt(dst, r["dst_label"])})
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


def nav_tree(*, owner: str, nodes: bool = False, kind: str | None = None,
             domain: str | None = None, domains: list[str] | None = None,
             cfg=None, store=None) -> dict:
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

    ``nodes=True`` 면 주제마다 ``nodes: [{id, title, kind, depth}]`` 를 함께 싣는다
    (좌측 트리의 노드 갈래). **기본은 False 이고 그때 응답은 종전과 글자까지 같다** —
    제목은 문서보다 수가 훨씬 많고(개념 1400 대 문서 215 인 그래프가 실재한다) 이
    응답은 웹에서 허브를 거치므로, 안 쓰는 화면까지 그 무게를 지게 두지 않는다.

노드 모집단의 기본은 위 ``concepts`` 수를 세는 것과 **같은 집합**이다(`_SEMANTIC_PK`,
    즉 Concept 과 Decision). 주제 머리가 "개념 N개"라고 말하면 그 갈래에 정확히 N줄이
    있어야 한다 — 다른 집합을 세면 화면이 스스로를 반박한다.

    ``kind`` 를 주면 모집단이 **그 종 하나**가 된다(`concept|decision|record|episode`).
    그때 각 주제에 ``count`` 가 함께 실리고 그것이 그 갈래의 줄 수다 — 위 계약을 깨지
    않고 지키는 방법이 이것이다: 세는 집합과 그리는 집합이 여전히 같고, 무엇을 세는지가
    인자로 드러난다. ``concepts`` 는 뜻이 바뀌지 않는다(늘 Concept + Decision).

    **종을 통째로 싣는 인자는 없다.** 기록이 이 그래프에서 가장 큰 종이고 이 응답은
    웹에서 계정 프록시를 건너므로, 화면은 레일에서 고른 한 종만 가져간다. 레일이
    개수를 알기 위해 필요한 것은 목록이 아니라 집계뿐이라 그것은 ``counts.kinds``
    (종별 수)가 따로 준다 — 쿼리 하나이고 제목을 싣지 않는다.

    ⚠ 주제 아래 설 수 있는 종은 **넷뿐이다**(`_TREE_PK`). Bridge 와 개방 타입은
    스키마가 `IN_DOMAIN` 을 주지 않는다 — 레일에 그 줄을 세우면 영원히 빈 갈래가 된다.

    ``domain``/``domains`` 는 모집단을 그 **하위 트리**로 좁힌다(정확일치 OR `d + "/"`
    접두 — 판정은 `domain_subtree_where` 한 벌이다). 목록·개수·문서·미분류가 **같은
    술어**를 지난다: 세는 집합과 그리는 집합이 어긋나면 화면이 스스로를 반박한다.
    스코프가 걸리면 `unfiled`(주제 없는 문서)는 비는 것이 정답이다 — 주제 스코프에
    주제 없는 문서는 들지 않는다. 응답은 `counts.scope` 로 무엇으로 좁혔는지 에코한다.

    ⚠ 이 인자가 없던 동안 좌측 레일의 Agent 갈래는 **아무것도 거르지 못했다**:
    `lnb.ts` 가 `domain: "vinc/agent/roles"` 를 선언해도 실어 보낼 자리가 없어
    모든 Concept 이 그 갈래에 섰다(실측 2026-09-20, 예약 주제 밖 `vinc/agentic` 과
    무관한 `work` 까지 6/6 이 나왔다). 근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §3]].
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
    want = _TREE_KIND_LABEL.get((kind or "").strip().lower()) if kind else None
    if kind and not want:
        base["note"] = f"unknown kind: {kind}"
        return base
    # 주제 스코프 — 목록·개수·문서가 모두 이 한 벌을 지난다.
    scope = normalize_domains(domain, domains)
    dom_params: dict = {"owner": owner}
    dom_where = ""
    if scope:
        dom_where = " AND " + domain_subtree_where(["c.domain"], scope, dom_params)

    def _in_scope(dom: str) -> bool:
        """문서 주제가 스코프 안인가. Cypher 쪽 `domain_subtree_where` 와 같은 판정."""
        return not scope or any(dom == d or dom.startswith(d.rstrip("/") + "/")
                                for d in scope)
    try:
        # v12 — 결정도 주제 아래 선다. 빼면 좌측 트리에서 결정이 통째로 안 보이고,
        # 그 주제의 개수도 실제보다 작게 나온다(빠진 것이 있다는 표시조차 없다).
        # 제목은 노드 갈래를 요청했을 때만 싣는다 — 안 그러면 기본 호출이 그래프
        # 전체의 제목을 끌고 온다(이 응답이 웹에서는 허브를 건넌다).
        title_col = ", c.title AS title" if nodes else ""
        # 모집단 — 기본은 종전(Concept + Decision), `kind` 를 주면 그 하나뿐이다.
        # 넷을 한꺼번에 싣는 분기는 두지 않는다(위 계약: 목록은 한 종씩).
        pop = {want: _TREE_PK[want]} if want else dict(_SEMANTIC_PK)
        crows = store.run(
            " UNION ALL ".join(
                f"MATCH (c:{lbl}) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
                f"AND c.domain <> ''{dom_where} "
                f"RETURN c.{pk} AS id, c.domain AS domain{title_col}, "
                f"'{lbl.lower()}' AS kind"
                for lbl, pk in pop.items()),
            dom_params) or []
        # 레일의 개수 — 제목을 싣지 않는 집계 하나. 종을 고르는 손잡이가 "없는 것"과
        # "아직 안 가져온 것"을 구별할 수 있어야 하므로 0 도 키로 남긴다.
        kind_counts: dict[str, int] = {}
        for lbl, pk in _TREE_PK.items():
            rows = store.run(
                f"MATCH (c:{lbl}) WHERE c.owner_id = $owner AND c.domain IS NOT NULL "
                f"AND c.domain <> ''{dom_where} RETURN count(c.{pk}) AS n",
                dom_params) or []
            kind_counts[lbl.lower()] = int((rows[0] or {}).get("n") or 0) if rows else 0
        by_domain: dict[str, dict[str, str]] = {}
        cdom: dict[str, str] = {}
        cmeta: dict[str, tuple[str, str]] = {}     # id → (title, kind), nodes=True 일 때만
        for r in crows:
            cid, dom = r.get("id") or "", (r.get("domain") or "").strip()
            if not cid or not dom:
                continue
            cdom[cid] = dom
            by_domain.setdefault(dom, {})[cid] = dom
            if nodes:
                cmeta[cid] = (r.get("title") or "", r.get("kind") or "concept")

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
                # 스코프가 걸렸으면 주제 없는 문서는 그 스코프에 들지 않는다.
                if not scope:
                    unfiled.append(row)
                continue
            if not _in_scope(topic):
                continue
            groups.setdefault(topic, []).append(
                {**row, "node_id": node, "node_title": d.get("primary_concept_title") or "",
                 "depth": int(depths.get(node, 0))})

        out = []
        for dom in sorted(groups):
            rows = sorted(groups[dom],
                          key=lambda r: (r["depth"], r["title"], r["doc_id"]))
            g = {"domain": dom, "concepts": len(by_domain.get(dom, {})),
                 "docs": rows}
            # `kind` 로 물었을 때의 주제 머리 숫자. `concepts` 와 이름을 나눈 이유는
            # 그 열의 뜻(Concept + Decision)을 바꾸지 않으려는 것이다.
            if want:
                g["count"] = len(by_domain.get(dom, {}))
            if nodes:
                # 문서와 **같은 정렬 규칙**(깊이 → 제목 → id). 두 갈래를 오갈 때
                # 줄 순서의 뜻이 달라지면 토글이 목록을 뒤섞는 것으로 읽힌다.
                g["nodes"] = sorted(
                    ({"id": cid, "title": cmeta.get(cid, ("", ""))[0],
                      "kind": cmeta.get(cid, ("", "concept"))[1],
                      "depth": int(depths.get(cid, 0))}
                     for cid in by_domain.get(dom, {})),
                    key=lambda r: (r["depth"], r["title"], r["id"]))
            out.append(g)
        out.sort(key=lambda g: (-len(g["docs"]), -g["concepts"], g["domain"]))
        # 스코프가 걸리면 문서 수도 **실은 줄**만 센다 — 머리의 숫자와 갈래의 줄
        # 수가 어긋나면 화면이 스스로를 반박한다.
        counts = {"domains": len(out),
                  "docs": sum(len(g["docs"]) for g in out) if scope else len(docs),
                  "unfiled": len(unfiled), "kinds": kind_counts}
        if want:
            counts["kind"] = want.lower()
        if scope:
            counts["scope"] = list(scope)
        base.update(executed=True, domains=out, unfiled=unfiled, counts=counts)
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
           depth: int = 2, by: str = "domain",
           domains: list[str] | None = None, cfg=None, store=None) -> dict:
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
        owner=owner, domain=domain, domains=domains, seed=seed, depth=depth,
        cfg=cfg, store=store)
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
            {"ids": pkns.keys(owner, ids), "owner": owner}) or []
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
                     depth: int, cfg, store, domains: list[str] | None = None):
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
    o = execute(ledger_origin_plan(owner=owner, domains=normalize_domains(domain, domains),
                                   ids=ids), cfg, store=store)
    if not o.get("executed"):
        return None, scope, o
    lk = execute(ledger_link_plan(owner=owner, domains=normalize_domains(domain, domains),
                                  ids=ids), cfg, store=store)
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
            {"ids": pkns.keys(owner, ids), "owner": owner}) or []
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
    return QueryPlan("bridge_neighbors", cypher,
                     {"node": pkns.key(owner, node), "owner": owner},
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


# ── 잇기 제안 점수 산식 (F11) ────────────────────────────────────────────────
# 세 신호를 쓰고 각 항의 근거를 `signals` 로 함께 돌려준다:
#   태그   Σ 2·ln(N/df)/ln(N)  — 표준 IDF. 나만 가진 태그 ≈2, 전부가 가진 태그 0.
#   도메인 +1.0                — 약한 신호라 약한 가중.
#   이웃   1.5·min(공통 이웃, 4) — 후보는 직접 연결이 이미 제외돼 있으므로 공통
#          이웃 = 2홉 근접이다. **동률을 실제로 깨는 것은 이 항이다.**
# ⚠ 태그를 희소성 없이 세는 옛 산식으로 되돌리지 말 것 — 점수가 포화돼 limit 이
# 사실상 id 사전순으로 자른다: [[record:tie-suggestions-were-tied-not-ranked]].
_TIE_TAG_MAX = 2.0
_TIE_DOMAIN_W = 1.0
_TIE_LINK_W = 1.5
_TIE_LINK_CAP = 4
TIE_SCORE_FORMULA = ("score = sum(2·ln(N/df) / ln(N) for shared tags) "
                     "+ 1.0 (same domain) + 1.5·min(common neighbors, 4)")


def _tag_weights(rows: list[dict]) -> dict[str, float]:
    """태그별 희소성 가중치(IDF). ``rows`` 는 owner 의 전체 개념 메타."""
    import math
    n = len(rows)
    df: dict[str, int] = {}
    for r in rows:
        for t in set(r.get("tags") or []):
            df[t] = df.get(t, 0) + 1
    if n <= 1:
        return {t: _TIE_TAG_MAX for t in df}
    den = math.log(n)
    out = {}
    for t, d in df.items():
        # d ≥ n(전 개념이 가진 태그)이면 0 — 정보가 없다. 음수로 내려가진 않는다.
        out[t] = max(0.0, _TIE_TAG_MAX * math.log(n / max(1, d)) / den)
    return out


def suggest_ties(*, owner: str, node: str, limit: int = 5, cfg=None, store=None) -> dict:
    """고아(저연결) 노드의 잇기 후보 — D25(임베딩 유사도)의 **전 단계** 결정론 근사
    (D32). 점수는 태그 희소성 + 도메인 일치 + 그래프 거리 2홉이고, 산식은
    :data:`TIE_SCORE_FORMULA` 로 응답에 함께 공개한다(F11). 각 후보는 산출 근거를
    ``reason``(지배 신호 이름)·``signals``(항별 값)로 싣는다 — merge_candidates 의
    ``reason`` 과 같은 방식이다. 이미 연결된 노드는 제외. 조회 전용 — 확정(엣지 생성)은
    fragment MERGE(POST /ingest/fragment) 경유로 단일 쓰기 표면(R4)을 유지한다."""
    meta = execute(concept_meta_plan(owner=owner), cfg, store=store)
    if not meta.get("executed"):
        return {"kind": "suggest_ties", "executed": False, "node": node,
                "suggestions": [], "total": 0, "truncated": False,
                "formula": TIE_SCORE_FORMULA, "note": meta.get("note", "")}
    rows = meta.get("rows", [])
    me = next((r for r in rows if r.get("id") == node), None)
    if me is None:
        cands = near_nodes(store, node, owner=owner)
        # F4 봉투 통일 — total·truncated·formula 를 **결과가 있을 때만** 싣던 것을
        # 고친다. 계약에 적힌 필드가 상황에 따라 사라지면 호스트는 그것을 옵셔널로
        # 취급할 수밖에 없고, 그러면 truncated 같은 안전 표식이 무의미해진다.
        return {"kind": "suggest_ties", "executed": True, "found": False,
                "node": node, "suggestions": [], "total": 0, "truncated": False,
                "formula": TIE_SCORE_FORMULA,
                "note": f"unknown concept: {node}",
                "error": f"unknown concept: {node}",
                "hint": ("Node ids are exact and type-prefixed (e.g. `concept:view`). "
                         + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                            if cands else "")
                         + "Use vinc_search to find the id first."),
                **({"candidates": cands} if cands else {})}
    g = execute(graph_plan(owner=owner), cfg, store=store)
    linked = {node}
    # F11 — 인접표를 **전 노드**로 만든다(예전엔 seed 의 이웃만 모았다). 후보의
    # 이웃까지 알아야 "공통 이웃 수"(2홉 근접)를 잴 수 있다. 무방향으로 둔다 —
    # 잇기 제안에 방향은 의미가 없다.
    adj: dict[str, set[str]] = {}
    for r in g.get("rows", []):
        a, b = r.get("id"), r.get("target")
        if not a or not b:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
        if a == node:
            linked.add(b)
        elif b == node:
            linked.add(a)
    # F3 — 접착제 인지: 브릿지 매개로 이미 이어진 개념도 linked 에 합류시켜 기존
    # 글루를 중복 제안하지 않는다(graph_plan 은 CC 직접 엣지만 본다).
    bn = execute(bridge_neighbors_plan(node, owner=owner), cfg, store=store)
    if bn.get("executed"):
        linked.update(r["id"] for r in bn.get("rows", []) if r.get("id"))
    my_tags = set(me.get("tags") or [])
    my_domain = me.get("domain") or ""
    my_nbrs = adj.get(node, set())
    weights = _tag_weights(rows)
    out = []
    for r in rows:
        cid = r.get("id")
        if not cid or cid in linked:
            continue
        shared = sorted(my_tags & set(r.get("tags") or []))
        tag_s = sum((weights.get(t, _TIE_TAG_MAX) for t in shared), 0.0)
        dom_s = _TIE_DOMAIN_W if my_domain and r.get("domain") == my_domain else 0.0
        common = sorted(my_nbrs & adj.get(cid, set()))
        link_s = _TIE_LINK_W * min(len(common), _TIE_LINK_CAP)
        score = tag_s + dom_s + link_s
        if score <= 0:
            continue
        # 지배 신호 = reason. 동점이면 정보량이 큰 쪽부터(이웃 > 태그 > 도메인).
        reason = max((("shared_neighbors", link_s), ("shared_tags", tag_s),
                      ("same_domain", dom_s)), key=lambda kv: kv[1])[0]
        out.append({"id": cid, "title": r.get("title") or cid,
                    "domain": r.get("domain") or "", "shared_tags": shared,
                    "score": round(score, 4), "reason": reason,
                    "signals": {"tags": round(tag_s, 4), "domain": dom_s,
                                "neighbors": round(link_s, 4),
                                "common_neighbors": len(common)},
                    "shared_neighbors": common[:3]})
    out.sort(key=lambda s: (-s["score"], s["id"]))   # 결정론 순서
    n = max(1, int(limit))
    return {"kind": "suggest_ties", "executed": True, "node": node,
            "suggestions": out[:n], "total": len(out),
            # 잘렸다는 사실을 말하지 않으면 호스트는 "후보가 이게 전부"로 읽는다.
            "truncated": len(out) > n, "formula": TIE_SCORE_FORMULA}


# ── 단일 홉 관측 창구 (E-G5) — SoG Observe-then-Navigate 경량 프리미티브 ────────
# 리서치 SoG(Search-on-Graph)의 "단일 홉 관측 창구"를 경량 MCP 표면으로 제공한다.
# 상류 모델이 depth-2 ego 전체(vinc_graph 최소 단위) 대신 한 홉씩 관측·항해하게 해
# 토큰을 아낀다(-69.9% 근거). 조회 전용 — 개념의 1-hop 관계망을 rel 타입별로 요약.

def neighbors_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """개념의 1-hop 이웃(무방향) — rel 타입·이웃 id/title/domain. `label(r)`(임베디드
    타입 함수 §6-2 M4). owner 지정 시 양 끝 스코프.

    ⚠ 이 플랜은 **의미층끼리만** 본다(개념·결정). 부착 축(Record·Episode·Bridge)은
    :func:`node_attachments_plan` 이 따로 낸다 — 아래 그 함수의 주석 참조.

    v12 — 시드 라벨은 id 접두사에서 꺼내고(`_seed_table`), 상대편은 개념·결정을 모두
    본다. 라벨을 문자열로 박아 두면 결정에서 출발한 관측이 **빈 결과**가 되고, 그건
    "이웃이 없다"와 구분되지 않는다."""
    params: dict = {"node": pkns.key(owner, node)}
    s_label, s_pk = _seed_table(node)
    if s_label not in _SEMANTIC_LABELS:
        s_label, s_pk = "Concept", "concept_id"   # 하위호환: 접두사 없는 id 는 개념
    n_where, m_where = f"n.{s_pk} = $node", ""
    if owner:
        n_where += " AND n.owner_id = $owner"
        m_where = " AND m.owner_id = $owner"
        params["owner"] = owner
    # R2 — `one_liner` 은 **Concept 에만** 있는 열이다. UNION 의 두 팔이 같은 모양을
    # 가져야 하므로 Decision 팔은 리터럴로 채운다: 열이 없는 라벨에 `m.one_liner` 를
    # 쓰면 Binder 가 죽고, 그러면 이웃 조회 전체가 죽는다(이 파일이 pinned·
    # content_time 에서 이미 두 번 겪은 자리).
    # F15 — 방향을 싣는다. 저장 방향마다 한 팔이고(out: 시드가 출발, in: 시드가 도착),
    # 같은 상대·같은 관계가 양쪽에 다 있으면 neighbors() 가 "both" 로 합친다.
    cypher = " UNION ALL ".join(
        f"MATCH {pat} WHERE {n_where}{m_where} "
        f"RETURN label(r) AS rel, '{d}' AS dir, m.{m_pk} AS id, m.title AS title, "
        "m.domain AS domain, "
        + (f"coalesce(m.one_liner, '') AS one_liner"
           if m_label == "Concept" else "'' AS one_liner")
        # W3 — 의미층 둘 다 추적 라벨이라 리터럴 맞춤 없이 그대로 뽑는다.
        + ", " + _track_cols("m")
        for m_label, m_pk in _SEMANTIC_PK.items()
        for pat, d in ((f"(n:{s_label})-[r]->(m:{m_label})", "out"),
                       (f"(n:{s_label})<-[r]-(m:{m_label})", "in")))
    return QueryPlan("neighbors", cypher, params, note="single-hop observation (SoG)")


def _typed_neighbors_cypher(owner: str | None) -> str:
    """Entity 시드의 TYPED 이웃(양방향). 저작 타입은 `subtype` 으로 낸다. Entity 에는
    도메인·한줄 설명 열이 없어 리터럴로 맞춘다. 근거: 감사 F12(2026-10-16)."""
    n_scope = " AND n.owner_id = $owner" if owner else ""
    m_scope = " AND m.owner_id = $owner" if owner else ""
    return " UNION ALL ".join(
        f"MATCH {pat} WHERE n.entity_id = $node{n_scope}{m_scope} "
        f"RETURN 'TYPED' AS rel, r.type AS subtype, '{d}' AS dir, m.entity_id AS id, "
        "m.title AS title, '' AS domain, '' AS one_liner, " + _track_cols("m")
        for pat, d in (("(n:Entity)-[r:TYPED]->(m:Entity)", "out"),
                       ("(n:Entity)<-[r:TYPED]-(m:Entity)", "in")))


def entity_neighbors_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """Entity 시드의 1-hop(TYPED, 양방향). :func:`neighbors_plan` 은 시드를 개념으로
    접어 Entity 를 못 본다 — 이것이 없을 때 개방 타입 노드는 관계가 있어도 이웃 0개였다."""
    params: dict = {"node": pkns.key(owner, node)}
    if owner:
        params["owner"] = owner
    return QueryPlan("entity_neighbors", _typed_neighbors_cypher(owner), params,
                     note="entity TYPED neighbours (F12)")


#: 부착 축의 라벨 → (PK 열, 엣지 이름, `domain` 자리에 쓸 열).
#: Bridge 는 `domain` 열이 없어 `source_domain` 을 쓴다 — 라벨마다 열이 달라서,
#: 다른 축의 투영을 그대로 옮겨 붙이면 Binder 로 죽는다.
_ATTACH_AXIS: dict[str, tuple[str, str, str]] = {
    "Record": ("record_id", "ATTACHES_TO", "domain"),
    "Episode": ("episode_id", "ABOUT", "domain"),
    "Bridge": ("bridge_id", "CONNECTS", "source_domain"),
}


def node_attachments_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """부착 축의 1-hop — Record(ATTACHES_TO)·Episode(ABOUT)·Bridge(CONNECTS). F14.

    **양방향이다.** 시드가 개념·결정이면 거기 붙은 Record·Episode·Bridge 를 내고,
    시드가 그 셋 중 하나이면 그것이 붙어 있는 개념·결정을 낸다.

    이게 왜 따로 필요한가: :func:`neighbors_plan` 의 패턴은 양 끝이 의미층이라
    다른 라벨이 **필터가 아니라 매치 모양에서** 빠진다. 그래서 `vinc_neighbors` 는
    실측상 `concept:vinc-mcp` 의 1-hop 이웃 15개 중 7개(RELATED_TO)만 보였다.
    빠진 8개가 하필 **사실이 사는 곳**이다 — Record(pitfall·fact) 6 · Episode 1 ·
    Bridge 1. 도구 설명은 "graph 대신 neighbors 로 한 홉씩 걸으라"고 권하므로,
    그 조언을 따른 모델은 Record 를 영영 못 본다. 무음 누락이다(F6 과 같은 계열).

    역방향이 이어서 필요한 이유(2026-08-22 실측): 부착 축은 한쪽으로만 풀려 있어서
    **Record·Episode 시드는 관계가 있는데도 이웃 0개**를 냈다 — 그러면 응답이
    "관계 없음"과 구분되지 않고, 실제로 그 hint("아직 관계가 없다")까지 붙었다.
    같은 무음 누락의 반대편이다(도구 계약은 [[vinc.canon.mcp-contract]]).

    UNION 은 열 타입이 같아야 하므로 rel·id·title·domain 에 W3 의 추적열 둘,
    W13 의 유효구간 둘을 더한 여덟 열만 투영한다 — 추적열은 셋 다 추적 라벨이라
    모양이 맞고, 유효구간은 Record 에만 있어 나머지 두 팔을 리터럴로 맞춘다
    (한 팔이라도 빠뜨리면 UNION 전체가 Binder 로 죽는다)."""
    params: dict = {"node": pkns.key(owner, node)}
    s_label, _s_pk = _seed_table(node)
    if owner:
        params["owner"] = owner
    if s_label in _ATTACH_AXIS:
        return QueryPlan("node_attachments", _attach_reverse_cypher(s_label, owner),
                         params, note="attachment axis, reverse (F14)")
    # v12 — 부착 **대상**도 의미층 둘 중 하나다(결정에도 기록·사건·다리가 붙는다).
    # 라벨을 박아 두면 결정의 부착이 통째로 안 보인다 — 라이브 실측 20건이 그 자리다.
    c_label, c_pk = (s_label, _s_pk) if s_label in _SEMANTIC_LABELS else ("Concept", "concept_id")
    c_scope = " AND c.owner_id = $owner" if owner else ""
    parts = []
    for label, (pk, rel, dom) in _ATTACH_AXIS.items():
        # 부착 노드의 owner: Record·Episode 는 엄격, Bridge 는 과도기 술어(축 플랜 관례).
        a_scope = ""
        if owner:
            a_scope = (f" AND {_bridge_owner_pred('a')}" if label == "Bridge"
                       else " AND a.owner_id = $owner")
        # F15 — 부착 엣지는 부착 노드에서 시드로 온다. 시드 쪽에서 보면 in 이다.
        parts.append(f"MATCH (a:{label})-[:{rel}]->(c:{c_label}) "
                     f"WHERE c.{c_pk} = $node{c_scope}{a_scope} "
                     f"RETURN '{rel}' AS rel, 'in' AS dir, a.{pk} AS id, a.title AS title, "
                     f"a.{dom} AS domain, " + _track_cols("a") + ", "
                     # W13 — 유효구간은 Record 팔만 실열, 나머지는 자리 맞춤.
                     + (_validity_cols("a") if label == "Record" else _VALIDITY_EMPTY))
    return QueryPlan("node_attachments", " UNION ALL ".join(parts), params,
                     note="record/episode/bridge attachments (F14)")


def record_supersedes_plan(node: str, *, owner: str | None = None) -> QueryPlan:
    """Record 시드의 SUPERSEDES 이웃(양방향). 방향은 `vinc_edges` 가 준다."""
    params: dict = {"node": pkns.key(owner, node)}
    scope = ""
    if owner:
        params["owner"] = owner
        scope = " AND a.owner_id = $owner AND m.owner_id = $owner"
    arms = [f"MATCH {pat} WHERE a.record_id = $node{scope} "
            f"RETURN 'SUPERSEDES' AS rel, '{d}' AS dir, m.record_id AS id, m.title AS title, "
            "m.domain AS domain, '' AS one_liner, " + _track_cols("m") + ", "
            + _validity_cols("m")
            for pat, d in (("(a:Record)-[:SUPERSEDES]->(m:Record)", "out"),
                           ("(m:Record)-[:SUPERSEDES]->(a:Record)", "in"))]
    return QueryPlan("record_supersedes", " UNION ALL ".join(arms), params,
                     note="record supersession axis")


def _attach_reverse_cypher(s_label: str, owner: str | None) -> str:
    """부착 노드(Record·Episode·Bridge) 시드 → 그것이 붙어 있는 개념·결정.

    시드 쪽 owner 술어는 라벨 규칙 그대로(Bridge 만 과도기), 반대쪽은 엄격이다."""
    s_pk, rel, _dom = _ATTACH_AXIS[s_label]
    s_scope = ""
    if owner:
        s_scope = (f" AND {_bridge_owner_pred('a')}" if s_label == "Bridge"
                   else " AND a.owner_id = $owner")
    m_scope = " AND m.owner_id = $owner" if owner else ""
    return " UNION ALL ".join(
        f"MATCH (a:{s_label})-[:{rel}]->(m:{m_label}) "
        f"WHERE a.{s_pk} = $node{s_scope}{m_scope} "
        f"RETURN '{rel}' AS rel, 'out' AS dir, m.{m_pk} AS id, m.title AS title, "
        "m.domain AS domain, "
        + ("coalesce(m.one_liner, '') AS one_liner"
           if m_label == "Concept" else "'' AS one_liner")
        + ", " + _track_cols("m")            # W3
        for m_label, m_pk in _SEMANTIC_PK.items())


def _domain_neighbors(node: str, *, owner: str | None, limit: int | None,
                      cfg=None, store=None) -> dict:
    """`domain:` 좌표의 1-hop (E-1). :func:`neighbors` 와 **같은 반환 모양**이라
    상류가 분기 없이 그대로 걸어 내려갈 수 있다.

    ⚠ ``owner`` 없이는 **실행하지 않는다**(fail-closed). 도메인 노드에는 소유가
    없어서, 소유 술어가 빠지면 자식 목록이 남의 하위 도메인 이름을 그대로 흘린다.
    다른 조회는 owner=None 이 "로컬 단일 사용자"라 무해하지만 여기는 아니다."""
    domain = domain_hierarchy.parse_id(node)
    base = {"kind": "neighbors", "executed": False, "node": node,
            "neighbors": [], "by_rel": {}, "count": 0}
    if not owner:
        base.update(executed=True, found=False,
                    error="owner required — 도메인 좌표는 무소유 공유 구조라 "
                          "스코프 없이 펼치면 남의 하위 도메인이 노출된다")
        return base
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    try:
        params = {"domain": domain, "owner": owner}
        members = store.run(domain_hierarchy.members_cypher(), params) or []
        kids = store.run(domain_hierarchy.child_domains_cypher(), params) or []
        # W3 — 여기 행에는 `rev`/`updated_at` 이 없다. 도메인은 추적 라벨이 아니고
        # (`_TRACK_COLS` 밖), 멤버 행은 `engines/domain_hierarchy.py` 의 질의에서
        # 오므로 이 패키지의 투영 범위(api/query.py) 밖이다. 없는 것은 결손이지
        # 사실이 아니므로 0 으로 채우지 않는다 — 지어낸 버전이 제일 나쁘다.
        # F15 — 방향: 자식 주제는 이 좌표에서 나가고(PARENT_OF), 멤버는 들어온다(IN_DOMAIN).
        rows = [{"rel": "PARENT_OF", "dir": "out", "id": domain_hierarchy.make_id(c),
                 "title": c, "domain": domain}
                for c in sorted({r["child"] for r in kids if r.get("child")})]
        rows += [{"rel": "IN_DOMAIN", "dir": "in", "id": r.get("id"),
                  "title": r.get("title") or r.get("id"),
                  "domain": r.get("domain") or ""}
                 for r in members if r.get("id")]
        rows.sort(key=lambda r: (str(r["rel"]), str(r["id"])))
        by_rel: dict[str, int] = {}
        for r in rows:
            by_rel[r["rel"]] = by_rel.get(r["rel"], 0) + 1
        out = {"kind": "neighbors", "executed": True, "found": bool(rows),
               "node": node, "neighbors": rows if limit is None else rows[:max(1, int(limit))],
               "by_rel": by_rel, "count": len(rows)}
        if not rows:
            out["hint"] = (f"No nodes of yours are under {domain!r}. Domains come "
                           "from the `domain` field on your nodes — set it (e.g. "
                           f"'{domain}/subtopic') and the hierarchy follows.")
        return out
    finally:
        if owns:
            store.close()


def neighbors(node: str, *, owner: str | None = None, limit: int = 50,
              cfg=None, store=None) -> dict:
    """E-G5 — 개념의 1-hop 관계망 요약(조회 전용). 반환 {executed, node,
    neighbors:[{rel,dir,id,title,domain,one_liner,rev,updated_at,valid_from,valid_until}],
    by_rel:{rel:count}, count}. 결정론 순서
    (rel, id). 상류 SoG 항해의 관측 표면.

    ``dir`` 은 저장된 방향이다: out(시드가 출발), in(시드가 도착), both(같은 관계가
    양쪽에 다 있다 — 한 줄로 센다). Entity(개방 타입) 시드는 TYPED 관계를 내고,
    그 행의 ``subtype`` 이 저작된 타입이다(감사 F12·F15).

    ``node`` 가 ``domain:`` 접두면 **도메인 좌표**를 잡는다(E-1) — 자식 도메인
    (`PARENT_OF`)과 거기 직접 속한 노드(`IN_DOMAIN`)를 같은 모양으로 낸다. 그래서
    `domain:vinc` 에서 시작해 한 홉씩 아래로 걸어 내려갈 수 있다."""
    if domain_hierarchy.parse_id(node):
        return _domain_neighbors(node, owner=owner, limit=limit, cfg=cfg, store=store)
    entity_seed = _seed_table(node)[0] == "Entity"
    # F12 — Entity 시드는 TYPED 만 갖는다. 아래 두 플랜은 시드를 개념으로 접어 0행을 낸다.
    res = execute(entity_neighbors_plan(node, owner=owner) if entity_seed
                  else neighbors_plan(node, owner=owner), cfg, store=store)
    if not res.get("executed"):
        return {"kind": "neighbors", "executed": False, "node": node,
                "neighbors": [], "by_rel": {}, "count": 0, "note": res.get("note", "")}
    rows = list(res.get("rows", []))
    if not entity_seed:
        # F14 — 부착 축(Record·Episode·Bridge)을 합류시킨다. 이게 없을 때 이 도구는
        # 개념끼리의 RELATED_TO 만 보여 줬고, 사실이 사는 Record 는 통째로 안 보였다.
        # 부착 축이 못 뜨더라도(구 스토어·확장 부재) 개념 축 결과는 그대로 낸다.
        att = execute(node_attachments_plan(node, owner=owner), cfg, store=store)
        rows += list(att.get("rows", [])) if att.get("executed") else []
    if node.startswith("record:"):
        # 대체 축 — Record 끼리의 SUPERSEDES. 위 두 플랜은 양 끝 중 하나가 의미층이라
        # Record→Record 를 모양에서 놓친다. 이게 없으면 옛 기록에서 후속을 못 걷는다.
        sup = execute(record_supersedes_plan(node, owner=owner), cfg, store=store)
        if sup.get("executed"):
            rows += list(sup.get("rows", []))
    seen: dict = {}
    out: list[dict] = []
    by_rel: dict[str, int] = {}
    for r in sorted(rows, key=lambda r: (str(r.get("rel")), str(r.get("id")),
                                         str(r.get("subtype") or ""), str(r.get("dir") or ""))):
        # 같은 관계·같은 상대는 한 줄이다(count 의 뜻은 그대로). 양쪽 방향이 다 있으면
        # 그 줄의 dir 이 both 가 된다. TYPED 는 저작 타입이 다르면 다른 관계다.
        key = (r.get("rel"), r.get("id"), r.get("subtype") or "")
        if not r.get("id"):
            continue
        if key in seen:
            row = seen[key]
            if r.get("dir") and row.get("dir") != r.get("dir"):
                row["dir"] = "both"
            continue
        row = {"rel": r.get("rel"), "dir": r.get("dir") or "", "id": r.get("id"),
               "title": r.get("title") or r.get("id"), "domain": r.get("domain") or "",
               # R2 — Concept 만 이 열을 갖는다. 부착 축(Record·Episode·Bridge)은
               # 키 자체가 없고, 그때 빈 문자열은 결손이 아니라 사실이다.
               "one_liner": r.get("one_liner") or "",
               # W3 — 걸어가다 만난 노드의 버전. `domain:` 좌표 응답만 이 키가
               # 없다(도메인은 추적 라벨이 아니다 — 위 _domain_neighbors).
               "rev": r.get("rev") or 0,
               "updated_at": r.get("updated_at") or "",
               # W13 — Record 부착만 값을 갖는다(열이 Record 전용 — 부착 축
               # 정방향 팔만 싣고, 의미층·역방향 행은 키가 없어 '' 로 남는다.
               # 그 빈 문자열은 결손이 아니라 사실이다).
               "valid_from": r.get("valid_from") or "",
               "valid_until": r.get("valid_until") or "",
               **({"subtype": r["subtype"]} if r.get("subtype") else {})}
        seen[key] = row
        out.append(row)
        by_rel[r.get("rel")] = by_rel.get(r.get("rel"), 0) + 1
    if not out:
        # F7 — 여태 여기가 `{executed: true, count: 0}` 뿐이었다. **없는 노드**와
        # **이웃이 없는 노드**가 응답으로 구분되지 않았다. 둘을 가른다.
        #
        # ⚠ 그 판정을 `near_nodes`(제목·props 유사도 검색)로 했었다. 유사도는 존재의
        # 증거가 아니다: 슬러그 id 는 제 산문 제목 안에 글자 그대로 나타나는 일이 드물어
        # (`episode:2026-08-07-mcp-hardening-f1-f2` 같은 것) 후보가 0이 되고, 그러면
        # **실재하는 노드가 `unknown node` 로 보고됐다.** 부착 엣지가 하나라도 있으면
        # 위 축 플랜이 잡아 주므로 증상은 **엣지 0인 Episode·Record·Bridge** 에만 났고,
        # 그래서 오래 안 보였다. 정본: [[record:neighbors-unknown-node-for-edgeless-attachments-2026-08-28]].
        #
        # 존재는 PK 로 묻는다. `seed_lookup_plan` 이 정확히 그 조회이고 — 여섯 테이블을
        # 접두사로 가르고, PK 네임스페이싱과 owner 스코프(Bridge 과도기 술어 포함)를
        # 이미 처리한다 — `graph()` 는 같은 갈래에서 진작 그것을 쓰고 있었다(1895행).
        # `neighbors()` 만 안 받았다. `near_nodes` 는 "혹시 이것을 찾으셨나요"에만 남긴다.
        probe = execute(seed_lookup_plan(node, owner=owner), cfg, store=store)
        if probe.get("executed"):
            exact = any(r.get("id") == node for r in probe.get("rows", []))
            # 없을 때만 후보를 뽑는다 — 있으면 쓸 데가 없고, 유사도 검색은 공짜가 아니다.
            cands = [] if exact else near_nodes(store, node, owner=owner)
        else:
            # 존재 조회 실패는 유사도 후보 유무로 대체할 수 없다.
            return {"kind": "neighbors", "executed": False, "node": node,
                    "neighbors": [], "by_rel": {}, "count": 0,
                    "note": probe.get("note") or "existence lookup unavailable"}
        return {"kind": "neighbors", "executed": True, "found": exact, "node": node,
                "neighbors": [], "by_rel": {}, "count": 0,
                "error": None if exact else f"unknown node: {node}",
                "hint": ("This node exists but has no relations yet — "
                         "vinc_suggest_ties proposes candidates, vinc_ingest adds edges."
                         if exact else
                         "Node ids are exact and type-prefixed (e.g. `concept:view`). "
                         + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                            if cands else "")
                         + "Use vinc_search to find the id first."),
                **({"candidates": cands} if cands and not exact else {})}
    _lim = max(1, int(limit))
    # F6 — `count` 가 전체 수라 비교로 알아낼 수는 있었지만, 그건 받는 쪽이 **먼저
    # 의심해야** 성립한다. 잘렸다는 사실은 명시로 말한다.
    return {"kind": "neighbors", "executed": True, "found": True, "node": node,
            "neighbors": out[:_lim], "by_rel": by_rel, "count": len(out),
            "neighbors_truncated": len(out) > _lim}


def node_edges(node: str | None = None, *, nodes: list[str] | None = None,
               owner: str | None = None, type: str | None = None,
               dir: str = "both", limit: int = 500, cfg=None, store=None) -> dict:
    """그래프 배치 없이 저장된 방향·근거·신뢰도를 읽는다.

    끝점 키와 관계 열은 스키마를 따른다. 양쪽 저작 노드의 소유자를 검사하고,
    전역 Domain은 그 소유자의 노드와 조상에 연결된 범위만 보여준다.
    조회가 없는 노드나 엣지를 만들어 내지 않는다.
    근거: [[vinc.spec.dataplane §5]] · [[vinc.canon.mcp-contract]].
    """
    from store.embedded_schema import node_table_specs, rel_table_specs

    base = {"kind": "edges", "executed": False, "edges": [], "count": 0}
    ids = [node] if node is not None else nodes
    if (not owner or (node is not None and nodes is not None)
            or not isinstance(ids, list) or not 1 <= len(ids) <= 100
            or any(not isinstance(n, str) or not n.strip() or "\x1f" in n for n in ids)
            or dir not in ("out", "in", "both")
            or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000
            or (type is not None and (not isinstance(type, str) or not type.strip()))):
        return {**base, "error": "invalid argument: provide owner, either node or 1..100 nodes, "
                "dir out/in/both, and limit 1..5000"}
    ids = list(dict.fromkeys(ids))
    specs, rels = node_table_specs(), rel_table_specs()
    grouped: dict[str, list[str]] = {}
    for nid in ids:
        domain = domain_hierarchy.parse_id(nid)
        label, _ = ("Domain", "name") if domain else _seed_table(nid)
        grouped.setdefault(label, []).append(domain if domain else pkns.key(owner, nid))
    params = {"owner": owner, **{label: values for label, values in grouped.items()}}
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        return {**base, "note": "no reachable store"}

    def read(cypher, args):
        return execute(QueryPlan("edges", cypher, args), cfg, store=store)

    try:
        visible: set[str] = set()
        if "Domain" in grouped:
            # Domain은 무소유 좌표다. 소유한 노드를 기준으로 범위를 좁혀
            # 전역 루트 아래 다른 소유자의 하위 주제를 노출하지 않는다.
            arms = [f"MATCH (n:{r['from']})-[:{r['label']}]->(d:Domain) "
                    "WHERE n.owner_id = $owner RETURN d.name AS domain"
                    for r in rels if r["to"] == "Domain"
                    and "owner_id" in specs[r["from"]]["cols"]]
            res = read(" UNION ALL ".join(arms), {"owner": owner})
            if not res["executed"]:
                return {**base, "error": res.get("error"), "note": res.get("note", "")}
            for row in res["rows"]:
                parts = str(row["domain"]).split("/")
                visible.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        params["visible"] = sorted(visible)
        probes = []
        for label in grouped:
            pk = specs[label]["pk"]
            scope = "n.name IN $visible" if label == "Domain" else "n.owner_id = $owner"
            probes.append(f"MATCH (n:{label}) WHERE n.{pk} IN ${label} AND {scope} "
                          f"RETURN n.{pk} AS id, '{label}' AS label")
        res = read(" UNION ALL ".join(probes), params)
        if not res["executed"]:
            return {**base, "error": res.get("error"), "note": res.get("note", "")}
        found = {"domain:" + r["id"] if r["label"] == "Domain" else pkns.strip(r["id"])
                 for r in res["rows"]}
        missing = [nid for nid in ids if nid not in found]
        arms = []
        for rel in rels:
            src, dst, label = rel["from"], rel["to"], rel["label"]
            # type은 물리 관계 표, subtype은 TYPED·ATTACHES_TO에 저장된 저작 타입이다.
            if type and type != label and "type" not in rel["props"]:
                continue
            incident = []
            if dir in ("out", "both") and src in grouped:
                incident.append(f"a.{specs[src]['pk']} IN ${src}")
            if dir in ("in", "both") and dst in grouped:
                incident.append(f"b.{specs[dst]['pk']} IN ${dst}")
            if not incident:
                continue
            guards = ["(" + " OR ".join(incident) + ")"]
            for alias, endpoint in (("a", src), ("b", dst)):
                if "owner_id" in specs[endpoint]["cols"]:
                    guards.append(f"{alias}.owner_id = $owner")
                elif endpoint == "Domain" and src == dst == "Domain":
                    guards.append(f"{alias}.name IN $visible")
            if type and type != label:
                guards.append("r.type = $type")
                params["type"] = type
            evidence = "r.evidence" if "evidence" in rel["props"] else "CAST(NULL AS STRING)"
            confidence = "r.confidence" if "confidence" in rel["props"] else "CAST(NULL AS DOUBLE)"
            subtype = "r.type" if "type" in rel["props"] else "CAST(NULL AS STRING)"
            # F15 — 엣지 출생 시각과 CONNECTS 의 역할. 어느 읽기 도구도 내지 않던 값이다.
            created = "r.created" if "created" in rel["props"] else "CAST(NULL AS STRING)"
            role = "r.role" if "role" in rel["props"] else "CAST(NULL AS STRING)"
            arms.append(f"MATCH (a:{src})-[r:{label}]->(b:{dst}) WHERE "
                        + " AND ".join(guards)
                        + f" RETURN a.{specs[src]['pk']} AS source, b.{specs[dst]['pk']} AS target, "
                        f"'{src}' AS source_label, '{dst}' AS target_label, '{label}' AS type, "
                        f"{evidence} AS evidence, {confidence} AS confidence, {subtype} AS subtype, "
                        f"{created} AS created, {role} AS role")
        res = read(" UNION ALL ".join(arms), params) if arms else {"executed": True, "rows": []}
        if not res["executed"]:
            return {**base, "error": res.get("error"), "note": res.get("note", "")}
        edges = [{"from": "domain:" + r["source"] if r["source_label"] == "Domain"
                  else pkns.strip(r["source"]),
                  "to": "domain:" + r["target"] if r["target_label"] == "Domain"
                  else pkns.strip(r["target"]), "type": r["type"],
                  "evidence": r["evidence"], "confidence": r["confidence"],
                  **({"subtype": r["subtype"]} if r["subtype"] else {}),
                  **({"role": r["role"]} if r["role"] else {}),
                  "created": r["created"] or None,
                  "derived": r["type"] in ("IN_DOMAIN", "PARENT_OF", "MENTIONS")}
                 for r in res["rows"]]
        edges.sort(key=lambda e: (e["from"], e["to"], e["type"],
                                  str(e.get("subtype", "")), str(e["evidence"]),
                                  str(e["confidence"])))
        from .belief_contract import annotate
        annotate(edges[:limit], store=store, owner=owner)
        return {**base, "executed": True, "nodes": ids, "missing_nodes": missing,
                **({"node": node, "found": node in found} if node is not None else {}),
                "edges": edges[:limit], "count": len(edges),
                "edges_truncated": len(edges) > limit}
    finally:
        if owns:
            store.close()


# ── 최단 경로 증거 체인 (E-G7) — 분산 증거 단편화 해소 ─────────────────────────
# 리서치 최단 경로 — 두 개념 사이 물리적으로 연결된 가장 조밀한 엣지 체인을 인출해
# 상류 모델에 증거로 준다(추측 없는 인과 증빙). kuzu `* ALL SHORTEST`(실측 확인) —
# 동률 최단 경로 전부. TRAIL ego 순회(목표 미지정)와 달리 **목표쌍 지정형**.

def shortest_path_plan(a_id: str, b_id: str, *, max_depth: int = 6,
                       owner: str | None = None) -> QueryPlan:
    """두 개념 간 최단 경로(들). `* ALL SHORTEST 1..d`(엔드포인트 owner 스코프 —
    교차소유자 엣지 부재라 중간 홉 스코프 불요). nodes(p) 시퀀스 + 길이 반환."""
    depth = max(1, min(int(max_depth), 8))
    params: dict = {"a": pkns.key(owner, a_id), "b": pkns.key(owner, b_id)}
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
# 비용 = 1 - confidence 로 다익스트라. 미기입(0.0)이 홉 하나와 같은 값이라
# **증거 없는 그래프에서 홉 최단으로 정확히 퇴화한다**(하위호환·설명가능).
# -log(c) 형은 c=0 에서 발산해 미기입 엣지를 통행 불가로 만들므로 쓰지 않는다.
# ⚠ 엔진 WSHORTEST 로 옮기지 말 것(검토 후 기각) — 저장 컬럼만 받아 파생열 드리프트
# 의무가 생기고, 패턴의 모든 rel 테이블에 그 열이 없으면 크래시한다:
# [[record:weighted-path-is-computed-in-python-not-by-the-engine]].

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
    params: dict = {"seed": pkns.key(owner, seed)}
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

#: 이 표면은 **개념 층만** 걷는다(`shortest_path_plan` 의 패턴이 `(:Concept)` 이다).
#: 그래서 record·decision·episode id 를 주면 노드가 멀쩡히 있어도 매치가 0이고,
#: 여태 그 답이 `found:false` 였다 — "안 이어져 있다"와 구분이 안 됐다. 실제로
#: 그 답을 연결 부재로 읽고 "노드들이 섬이다"라고 보고한 적이 있다(2026-09-07).
#: 접두만 본다: 알려진 비-개념 접두일 때만 말하므로 정상 개념을 거짓으로 막지 않는다.
_NON_CONCEPT_PREFIXES = ("record:", "decision:", "episode:", "domain:", "bridge:")


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


def _concept_only_refusal(base: dict, wrong: list[str]) -> dict:
    """개념이 아닌 끝점은 **연결 부재가 아니라 층 부재**다. 이걸 안 가르면 읽는 쪽이
    빈 답을 "안 이어져 있다"로 읽는다."""
    base["error"] = ("this tool walks the concept layer only: "
                     + ", ".join(wrong) + " is not a concept id")
    base["hint"] = ("Shortest paths are computed between `concept:` nodes. "
                    "A record, decision or episode is attached to concepts "
                    "rather than sitting on the path graph, so it can never "
                    "be an endpoint here even when it exists and is well "
                    "connected. Use vinc_neighbors on that id to see what it "
                    "attaches to, then path between those concepts.")
    return base


def _unknown_node_refusal(base: dict, missing: list[str], *, owner, store) -> dict:
    base["error"] = "unknown node: " + ", ".join(missing)
    cands = [c for x in missing for c in near_nodes(store, x, owner=owner)]
    base["hint"] = ("Node ids are exact and type-prefixed (e.g. `concept:view`). "
                    + ("Did you mean: "
                       + ", ".join(dict.fromkeys(c["id"] for c in cands)) + "? "
                       if cands else "")
                    + "Use vinc_search to find the id first.")
    if cands:
        base["candidates"] = cands[:3]
    return base


def _probe_node(x: str, *, owner, cfg, store) -> dict | None:
    """PK 로 노드 하나를 묻는다. 반환: 행(dict) · 없으면 None · 프로브가 못 돌았으면
    ``{"_unknown": True}``(모르는 것이지 없는 것이 아니다)."""
    probe = execute(seed_lookup_plan(x, owner=owner), cfg, store=store)
    if not probe.get("executed"):
        return {"_unknown": True, "note": probe.get("note", "")}
    return next((r for r in probe.get("rows", []) if r.get("id") == x), None)


def _same_endpoint_path(x: str, *, weight: str, owner, cfg, store) -> dict:
    """a == b — 길이 0 경로 하나(노드 자신)로 답한다. hops·confidence 두 갈래 공통.

    ⚠ 엔진 패턴이 `1..d` 라 자기 자신까지의 경로는 매치가 0이고, 여태 이 입력이
    `no path within max_depth` 로 답했다 — 노드가 멀쩡히 있는데 "안 이어져 있다"고
    말한 셈이다. 거절은 그대로 둔다: 개념이 아니거나 없는 id 는 a != b 일 때와 같은
    문장으로 거절한다(같은 id 두 번이 거절을 우회하는 길이 되면 안 된다).
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]"""
    base = {"kind": "shortest_path", "executed": True, "found": False,
            "a": x, "b": x, "length": None, "paths": [], "count": 0, "weight": weight}
    if weight == WEIGHT_CONFIDENCE:
        base["cost"] = None
    if x.lower().startswith(_NON_CONCEPT_PREFIXES):
        return _concept_only_refusal(base, [x])
    row = _probe_node(x, owner=owner, cfg=cfg, store=store)
    if row is not None and row.get("_unknown"):
        base["executed"] = False
        base["note"] = row.get("note", "")
        return base
    if row is None:
        return _unknown_node_refusal(base, [x], owner=owner, store=store)
    if _seed_table(x)[0] != "Concept":
        # 개방층(Entity) 노드 — 있지만 개념이 아니다.
        return _concept_only_refusal(base, [x])
    path = {"ids": [x], "titles": [row.get("title") or x], "length": 0}
    if weight == WEIGHT_CONFIDENCE:
        path["cost"] = 0.0
        base["cost"] = 0.0
    base.update(found=True, length=0, paths=[path], count=1)
    return base


def shortest_path(a_id: str, b_id: str, *, max_depth: int = 6,
                  owner: str | None = None, weight: str = WEIGHT_HOPS,
                  cfg=None, store=None) -> dict:
    """E-G7/E-G8 — 두 개념 간 최단 경로 증거 체인(조회 전용). 반환 {executed, found,
    a, b, length, paths:[{ids,titles}], count, weight}. 경로 부재 시 found=False.

    ``weight``: ``"hops"``(기본, E-G7 — 엔진 ALL SHORTEST, 동률 전부) 또는
    ``"confidence"``(E-G8 — 파이썬 다익스트라, 비용 = 1-confidence). 가중 갈래는
    ``cost``(경로 비용 합)를 추가로 싣는다. 미지 값은 조용히 ``hops`` 로 강등하고
    응답의 ``weight`` 가 **실제로 적용한 축**을 에코한다(`_apply_basis` 와 같은 태도).

    ``a == b`` 는 길이 0 경로 하나(노드 자신)로 답한다(:func:`_same_endpoint_path`)."""
    confidence = (weight or "").strip().lower() == WEIGHT_CONFIDENCE
    if a_id == b_id:
        return _same_endpoint_path(a_id, weight=WEIGHT_CONFIDENCE if confidence
                                   else WEIGHT_HOPS, owner=owner, cfg=cfg, store=store)
    if confidence:
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
    # 최단만 남긴다. 설명이 "ALL shortest paths (ties included)"를 약속하는데 행 안에
    # 더 긴 경로가 섞여 나온 적이 있다 — 계약은 여기서 지킨다(엔진 의미론에 기대지 않는다).
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
    if paths:
        paths = [p for p in paths if p["length"] == paths[0]["length"]]
    base.update(found=bool(paths), paths=paths, count=len(paths),
                length=paths[0]["length"] if paths else None)
    if not paths:
        # F7 — 여태 `found:false` 뿐이라 **끝점이 없는 것**과 **길이 없는 것**이
        # 구분되지 않았다. 둘은 다음 행동이 완전히 다르다: 앞은 id 를 고쳐야 하고
        # 뒤는 depth 를 늘리거나 엣지를 이어야 한다. 어느 끝이 문제인지 짚어 준다.
        #
        # 순서: **층(접두) → 존재(PK) → depth**. 층 판정이 먼저다 — 개념 층 밖 id 는
        # 있든 없든 여기서 끝점이 될 수 없으므로, 없는 `record:x` 에 "unknown node"
        # 라고 답하면 id 만 고치면 될 것처럼 읽힌다. 문서화된 거절은 개념 전용이다.
        # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
        wrong = [x for x in (a_id, b_id) if x.lower().startswith(_NON_CONCEPT_PREFIXES)]
        if wrong:
            return _concept_only_refusal(base, wrong)
        # ⚠ **존재는 PK 로 묻는다.** 이 판정이 `near_nodes`(제목·props **유사도** 검색)
        # 였고, 유사도는 존재의 증거가 아니다: 슬러그 id 는 제 산문 제목 안에 글자
        # 그대로 안 나타나는 일이 흔해서 후보가 0이 되고, 그러면 **실재하는 노드가
        # `unknown node` 로 보고된다.** 라이브 실측(2026-09-08): `concept:hybrid-search`
        # 는 이웃 28개를 가진 멀쩡한 개념인데 이 표면만 "unknown node" 라고 답했다.
        # `neighbors()` 가 정확히 같은 결함을 3860행대에서 이미 고쳤는데(그 주석이
        # 이 처방을 적어 놓았다) **형제 함수만 안 받았다.**
        # 근거: [[record:path-called-an-existing-concept-unknown-2026-09-08]]
        # 프로브가 못 돌았으면 **모르는 것**이지 없는 것이 아니다 — 조회 실패를
        # 부재로 단언하지 않는다(`neighbors()` 와 같은 태도).
        missing = [x for x in (a_id, b_id)
                   if _probe_node(x, owner=owner, cfg=cfg, store=store) is None]
        if missing:
            return _unknown_node_refusal(base, missing, owner=owner, store=store)
        base["error"] = f"no path within max_depth={max_depth}"
        base["hint"] = ("Both nodes exist but nothing connects them within this "
                        "depth. Raise `max_depth`, or use vinc_neighbors on each "
                        "end to see what they do connect to.")
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
    """타이틀 정규화 → (정규 문자열, 토큰셋). 소문자·영숫자/한글 토큰. 결정론.

    ⚠ 여기 **길이≥2 토큰만 유의어로 치는 필터가 있었고, 그게 F10 오탐의 최대 원인**
    이었다(실측). 짧은 토큰이 곧 잡음이라는 가정이 거꾸로였다:

      · "웹 Vinc" → `웹` 이 버려져 토큰셋이 `{vinc}` 하나가 된다. 그러면 vinc 를
        포함한 **모든** 제목의 부분집합이라 subset 규칙이 전부 후보로 올린다.
        22개념 표본에서 이 노드 하나가 후보쌍 20개 중 10개(50%)를 만들었고,
        라이브 계정 그래프(개념 185)에서도 상위 30쌍 중 12건(40%)이 이 쌍이었다.
      · "SEC-1"/"SEC-3" → 변별점인 `1`·`3` 이 버려져 두 결정이 같은 토큰이 된다.

    즉 필터가 **일반어(vinc)는 남기고 변별어(웹·1·3)를 버리고** 있었다. 토큰을 전부
    유의어로 두면 표본 후보쌍이 37 → 14(−62%)로 줄고, 진짜 양성 4종(iPaaS/ipaas ·
    transformer ⊂ transformer architecture · chunk-ssot ⊂ D45 · LadybugDB ⊂ D14)은
    그대로 남는다. 토큰 추가는 부분집합 관계를 **좁히기만** 하므로(더 보수적) 새
    오탐을 만들지 않는다."""
    toks = [t for t in _MERGE_TOKEN_RE.findall((title or "").lower())]
    return ("".join(toks), frozenset(toks))


# ── 구조적 비중복 쌍 (F10-a) ──────────────────────────────────────────────────
# **한쪽이라도 doc 미러면 후보에서 뺀다.** doc 미러와 그것이 비추는 주제 개념은 사본
# 둘이 아니라 **층 둘**이고, 제목이 같은 것은 미러가 문서 제목을 물려받기 때문이다.
# 이건 id 명명 관례지 엔진 구조가 아니라, 관례가 없는 그래프에서는 자동 무동작이고
# 끄는 스위치(`include_structural`)를 둔다. **무음 억제 금지** — 몇 쌍을 뺐는지 숫자로
# 남긴다. 근거·실측: [[record:fact-doc-concept-is-a-layer]] ·
# [[record:fact-merge-excludes-any-doc-pair]].
# ⚠ 결정용 `dec-*` 접두 규칙을 되살리지 말 것 — 결정은 이제 별도 라벨이고 이 조회는
# `MATCH (c:Concept)` 이라 애초에 안 긁어온다(타입이 생기면 관례는 물러난다).
_DOC_CONCEPT_PREFIX = "concept:doc."


def _structural_role(cid: str) -> str:
    """개념 id 의 구조 역할 — ``"doc"`` | ``""``(보통)."""
    return "doc" if cid.startswith(_DOC_CONCEPT_PREFIX) else ""


def _is_structural_nonduplicate(a_id: str, b_id: str) -> bool:
    """이 쌍이 **의도된 비중복**인가 — 한쪽이라도 문서 미러 개념이면 그렇다.

    doc↔doc(문서가 둘이면 개념도 둘)과 doc↔주제(층이 둘)를 같은 규칙으로 뺀다.
    근거는 위 주석 — 두 경우 모두 제목이 닮은 것이 명명 규칙의 결과다."""
    return "doc" in (_structural_role(a_id), _structural_role(b_id))


def rejection_pair_key(owner: str, a_id: str, b_id: str) -> str:
    """기각 기억의 키 — ``owner|min|max``. 정렬해 (a,b)·(b,a) 를 한 행으로 접는다."""
    lo, hi = sorted((a_id or "", b_id or ""))
    return f"{owner or ''}|{lo}|{hi}"


def _rejected_set(pairs) -> set[tuple[str, str]]:
    """기각쌍 → 정렬된 튜플 집합(순서 무관 조회용)."""
    out: set[tuple[str, str]] = set()
    for p in pairs or ():
        if isinstance(p, dict):
            a, b = p.get("a_id") or "", p.get("b_id") or ""
        else:
            a, b = (list(p) + ["", ""])[:2]
        if a and b:
            lo, hi = sorted((a, b))
            out.add((lo, hi))
    return out


def merge_candidates_detail(rows: list[dict], *, min_score: float = 0.6,
                            limit: int = 20, include_structural: bool = False,
                            rejected=None) -> dict:
    """:func:`merge_candidates` 의 상세형 — 억제 내역까지 돌려준다(순수, I/O 없음).

    반환 ``{"candidates": […], "suppressed": {"structural": n, "rejected": m}}``.
    억제를 **숫자로** 남기는 이유: 기본 필터가 진짜 중복을 먹었을 때 호출자가 그
    사실을 알 방법이 없으면 그건 무음 고장이다. 숫자가 0 이 아니면 호출자는
    ``include_structural`` 로 되열어 확인할 수 있다."""
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
    rej = _rejected_set(rejected)
    out = []
    n_struct = n_rej = 0
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
                    # ⚠ 여기가 `max(0.8, jac)` 였다. 부분집합이면 겹침 정도와 무관하게
                    # 0.8 바닥이 깔려서, 실측 20쌍 중 19쌍이 **같은 점수 0.8** 이었다.
                    # 점수가 한 값이면 정렬은 id 사전순과 다를 게 없고 호출자는 무엇을
                    # 먼저 볼지 고를 수 없다. 겹침에 단조인 [0.8,1.0] 로 편다.
                    score, reason = 0.8 + 0.2 * jac, "subset"
                elif jac >= min_score:
                    score, reason = jac, "token_overlap"
            if not reason:
                continue
            if not include_structural and _is_structural_nonduplicate(a["id"], b["id"]):
                n_struct += 1
                continue
            if (a["id"], b["id"]) in rej:   # prepped 가 id 정렬이라 a<b 보장
                n_rej += 1
                continue
            out.append({"a_id": a["id"], "a_title": a["title"],
                        "b_id": b["id"], "b_title": b["title"],
                        "score": round(score, 4), "reason": reason,
                        "same_domain": bool(a["domain"] and a["domain"] == b["domain"])})
    out.sort(key=lambda c: (not c["same_domain"], -c["score"], c["a_id"], c["b_id"]))
    return {"candidates": out[:max(1, int(limit))],
            "suppressed": {"structural": n_struct, "rejected": n_rej},
            "total": len(out)}


def merge_candidates(rows: list[dict], *, min_score: float = 0.6,
                     limit: int = 20, include_structural: bool = False,
                     rejected=None) -> list[dict]:
    """중복 개념 후보쌍을 결정론적으로 산출(순수 — I/O 없음). ``rows``: [{id, title,
    domain}]. 규칙(강→약): 정규 타이틀 완전일치(exact_title, 1.0) · 부분집합
    (subset, 0.8+0.2·자카드) · 토큰 자카드 ≥min_score(token_overlap). 반환은
    (동일도메인 우선, 점수 내림차순, id 사전순) 정렬 리스트.

    기본으로 **의도된 비중복 쌍**(한쪽이라도 `concept:doc.*` — 문서끼리 · 문서↔주제,
    :func:`_is_structural_nonduplicate`)과 **기각 기억**(``rejected``)을 뺀다.
    ``include_structural=True`` 로 구조 필터를 끄면 예전 동작이다. 억제 건수가
    필요하면 :func:`merge_candidates_detail`."""
    return merge_candidates_detail(rows, min_score=min_score, limit=limit,
                                   include_structural=include_structural,
                                   rejected=rejected)["candidates"]


# ── 기각 기억 (F10-b) — "이 둘은 다른 것" 판정을 적어 둔다 ────────────────────
# merge_candidates 는 결정론이라 같은 데이터에 늘 같은 답을 낸다. 그래서 사용자가
# 오탐 하나를 기각해도 다음 호출에 그 쌍이 또 1등으로 올라온다 — 목록이 영원히 같은
# 잡음으로 채워진다. 판정을 저장해 그 쌍만 뺀다. **읽기 도구의 상태를 사용자가 고칠
# 수 있게** 하는 최소 장치이고, 병합(비가역) 대신 기각(가역)을 권하는 안전판이기도 하다.
#
# 저장 위치는 `_MergeRejection` 메타 테이블([[vinc.spec.dataplane §2.5]]).
# owner 스코프는 **키와 열 양쪽**에 건다 — SEC-1 이 준 교훈은 한 층만 맞으면 새는
# 것이었다([[vinc.spec.dataplane §5]]).

def _merge_rejection_rows(store, owner: str | None) -> list[dict]:
    """owner 의 기각 목록(원시 행). 테이블이 없는 구 스토어면 빈 목록으로 강등한다 —
    기각 기억이 없다고 후보 조회 자체가 죽으면 안 된다."""
    if store is None:
        return []
    try:
        return store.run(
            "MATCH (r:_MergeRejection) WHERE r.owner_id = $o "
            "RETURN r.a_id AS a_id, r.b_id AS b_id, r.rejected_at AS rejected_at, "
            "r.note AS note ORDER BY a_id, b_id", {"o": owner or ""})
    except Exception:
        return []


def list_merge_rejections(*, owner: str | None = None, cfg=None, store=None) -> dict:
    """기각된 병합 후보쌍 목록(조회 전용). 반환 {executed, rejections:[…], count}."""
    owns = store is None
    if owns:
        store = open_store(cfg or load_config())
    try:
        if store is None:
            return {"kind": "merge_rejections", "executed": False, "rejections": [],
                    "count": 0, "note": "no reachable store"}
        rows = _merge_rejection_rows(store, owner)
        return {"kind": "merge_rejections", "executed": True,
                "rejections": [dict(r) for r in rows], "count": len(rows)}
    finally:
        if owns and store is not None:
            store.close()


def reject_merge_pair(*, owner: str, a_id: str, b_id: str, note: str = "",
                      undo: bool = False, cfg=None, store=None) -> dict:
    """병합 후보쌍을 기각(또는 ``undo=True`` 로 기각 취소). 멱등.

    owner 는 **필수**다. 없이 쓰면 남의 그래프 쌍을 기각해 상대의 제안 목록을 조용히
    비울 수 있다(SEC-3 이 쓰기 표면에서 잡았던 것과 같은 구멍)."""
    base = {"kind": "merge_reject", "executed": False,
            "a_id": a_id, "b_id": b_id, "undo": bool(undo)}
    if not owner or not isinstance(owner, str):
        base["error"] = base["note"] = "refusing to record a rejection without an owner"
        return base
    if not a_id or not b_id or a_id == b_id:
        base["error"] = base["note"] = "two distinct node ids are required"
        return base
    owns = store is None
    if owns:
        store = open_store(cfg or load_config())
    try:
        if store is None:
            base["error"] = base["note"] = "no reachable store"
            return base
        key = rejection_pair_key(owner, a_id, b_id)
        lo, hi = sorted((a_id, b_id))
        if undo:
            store.run("MATCH (r:_MergeRejection) WHERE r.pair_key = $k DELETE r", {"k": key})
        else:
            import datetime as _dt
            ts = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")
            store.run(
                "MERGE (r:_MergeRejection {pair_key: $k}) SET r.owner_id = $o, "
                "r.a_id = $a, r.b_id = $b, r.rejected_at = $ts, r.note = $n",
                {"k": key, "o": owner, "a": lo, "b": hi, "ts": ts, "n": str(note or "")})
        base["executed"] = True
        base["pair_key"] = key
        base["count"] = len(_merge_rejection_rows(store, owner))
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns and store is not None:
            store.close()
    return base


def suggest_merge_candidates(*, owner: str | None = None, min_score: float = 0.6,
                             limit: int = 20, include_structural: bool = False,
                             cfg=None, store=None) -> dict:
    """E-G4 — owner 개념의 중복 병합 후보(조회 전용). 확정은 vinc_merge_nodes 경유
    (자동 병합 금지 THESIS T1).

    기본으로 **의도된 비중복 쌍**(한쪽이라도 문서 개념 — 문서끼리 · 문서↔주제)과
    **기각 기억**을 뺀다(F10). 뺀 건수는 ``suppressed`` 로 노출한다 — 필터가 진짜
    중복을 먹었는지 호출자가 볼 수 있어야 하고, ``include_structural=True`` 로 구조
    필터를 되열 수 있다.

    반환 {executed, candidates:[…], count, total, suppressed:{structural, rejected}}."""
    where = "WHERE c.owner_id = $owner " if owner else ""
    params = {"owner": owner} if owner else None
    plan = QueryPlan("merge_candidates",
                     "MATCH (c:Concept) " + where +
                     "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain",
                     params or {}, note="concept dup candidates (read-only)")
    # ⚠ 스토어를 **여기서** 소유한다. `execute(store=None)` 은 자기 커넥션을 열고
    # 닫으므로, 기각 조회에 넘길 store 가 None 으로 남아 필터가 조용히 무동작이 된다.
    # 두 조회가 같은 커넥션을 봐야 한다.
    owns = store is None
    if owns:
        store = open_store(cfg or load_config())
    try:
        res = execute(plan, cfg, store=store)
        if not res.get("executed"):
            return {"kind": "merge_candidates", "executed": False,
                    "candidates": [], "count": 0, "total": 0,
                    "suppressed": {"structural": 0, "rejected": 0},
                    "note": res.get("note", "")}
        det = merge_candidates_detail(
            res.get("rows", []), min_score=min_score, limit=limit,
            include_structural=include_structural,
            rejected=_merge_rejection_rows(store, owner))
    finally:
        if owns and store is not None:
            store.close()
    return {"kind": "merge_candidates", "executed": True,
            "candidates": det["candidates"], "count": len(det["candidates"]),
            "total": det["total"], "suppressed": det["suppressed"]}


# ── 구조 진단 (E-G3) — 약연결 성분 · Leiden 커뮤니티 · 조회 전용 ─────────────────
# 계산은 `api/graph_algo.py`(순수 파이썬)가 한다. 2026-09-28 까지는 엔진 algo 확장에
# 맡겼는데, ladybug 0.20 확장이 번들되지 않은 공유 라이브러리(networkit·arrow)를 물어
# 허브에서 세 진단이 전부 꺼졌다. 옮기면서 Louvain 을 Leiden 으로 바꿨다.
# 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24 §2.3]] (원인), 문헌 조사 §10 레버 1·2
# 투영은 여기서 소유자로 거른다(엔진 투영은 전체 표만 됐다). 강등 계약은 그대로다:
# 스토어를 못 읽으면 executed=False + note 이고 크래시하지 않는다.

def _owner_concept_ids(store, owner: str | None) -> set[str] | None:
    """owner 의 **의미층** 노드 id 집합(스코프 교차용). owner=None(로컬)이면 None(전체).

    v12 — Decision 을 함께 센다. 알고리즘 프로젝션이 의미층 관계 4종의 끝점을 전부
    투영하므로(store.embedded_backend._algo_projection) 결과 행에 결정이 섞여 오는데,
    이 집합이 개념만 담으면 그 행들이 **말없이** 스코프 밖으로 떨어진다 — 결정은
    컴포넌트에도 고아 목록에도 영영 안 나오고, 그 사실을 알릴 자리도 없다."""
    if not owner:
        return None
    rows = store.run("MATCH (c:Concept) WHERE c.owner_id = $owner "
                     "RETURN c.concept_id AS id", {"owner": owner})
    ids = {r["id"] for r in rows if r.get("id")}
    drows = store.run("MATCH (d:Decision) WHERE d.owner_id = $owner "
                      "RETURN d.decision_id AS id", {"owner": owner})
    return ids | {r["id"] for r in drows if r.get("id")}


#: 알고리즘 결과가 **두 스페이스의 노드를 같은 id 로** 물고 온 것들.
#:
#: 🔴 owner 스코프를 논리 id 문자열 집합과의 교차로 걸기 때문에 생긴다
#: (`_owner_concept_ids`). 한 소유자 안에서 id 는 기본키라 유일하니 교차는 안전해
#: 보이는데, 같은 슬러그를 가진 남의 노드가 문자열이 같다는 이유로 통과한다. 새
#: 스페이스는 전부 같은 기본 역할 넷으로 시작하므로 드문 일이 아니다.
#:
#: 어느 쪽이 내 것인지는 알 방법이 없다 — 알고리즘 행에 소유자가 없고, owner 만
#: 투영하는 길은 엔진이 막는다(cypher 투영 그래프는 WCC 를 Binder 가 거절한다).
#: 그래서 **고르지 않고 갈라 낸다.** 세 진단이 같은 함수를 쓴다.
#: 근거: [[record:one-logical-id-in-two-spaces-reads-as-two-orphans-2026-09-10]]
def _ambiguous_algo_ids(rows, scope: set[str] | None) -> list[str]:
    """한 id 가 서로 다른 그룹 값으로 두 번 이상 나오면 그것은 두 스페이스의 노드다."""
    seen: dict = {}
    for r in rows or []:
        cid = r.get("id")
        if not cid or (scope is not None and cid not in scope):
            continue
        seen.setdefault(cid, set()).add(r.get("value"))
    return sorted(cid for cid, vals in seen.items() if len(vals) > 1)


#: 성분 진단이 걷는 관계 — 의미층을 **실제로 잇는** 관계 전부. 엔진 시절의
#: `EmbeddedBackend._ALGO_REL_TABLES` 와 같은 목록이고 같은 이유다: ABOUT·ATTACHES_TO 를
#: 빼면 결정·사건·기록이 거짓 고아로 나온다(2026-08-09, 결정 67건 중 43건).
ALGO_REL_TABLES = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM",
                   "INHERITS_FROM", "ABOUT", "ATTACHES_TO")


def _algo_graph(store, owner: str | None, rel_tables: tuple) -> tuple[list[str], list[tuple]]:
    """진단용 투영 — 관계 표의 끝점 라벨에 속한 노드 전부와 그 관계들. 소유자가 있으면
    **양 끝이 모두 그 소유자**인 것만 담는다. 라벨·PK·끝점 쌍은 스키마에서 파생한다
    (손으로 적은 목록이 낡아 진단을 통째로 죽인 일이 있다 — `_algo_projection` 주석).

    id 는 논리 id 로 돌아온다(네임스페이싱 스토어의 물리 키는 조회 계층이 벗긴다)."""
    from store import embedded_schema as _es
    pks = {lbl: spec["pk"] for lbl, spec in _es.node_table_specs().items()}
    want = set(rel_tables)
    pairs = [(sp["label"], sp["from"], sp["to"]) for sp in _es.rel_table_specs()
             if sp["label"] in want and sp["from"] in pks and sp["to"] in pks]
    labels: list[str] = []
    for _rel, a, b in pairs:
        for lbl in (a, b):
            if lbl not in labels:
                labels.append(lbl)
    params = {"o": owner} if owner else None
    nodes: list[str] = []
    for lbl in labels:
        where = "WHERE n.owner_id = $o " if owner else ""
        for r in store.run(f"MATCH (n:{lbl}) {where}RETURN n.{pks[lbl]} AS id", params) or []:
            if r.get("id"):
                nodes.append(pkns.strip(r["id"]))
    edges: list[tuple] = []
    for rel, a, b in pairs:
        where = "WHERE x.owner_id = $o AND y.owner_id = $o " if owner else ""
        for r in store.run(f"MATCH (x:{a})-[:{rel}]->(y:{b}) {where}"
                           f"RETURN x.{pks[a]} AS a, y.{pks[b]} AS b", params) or []:
            if r.get("a") and r.get("b"):
                edges.append((pkns.strip(r["a"]), pkns.strip(r["b"])))
    return nodes, edges


def _run_algo(store, algo: str, *, rel_tables: tuple | None = None,
              owner: str | None = None, graph_out: dict | None = None):
    """진단 알고리즘을 돌린다 — (rows, None) 또는 (None, note). rows 는 엔진 시절과 같은
    `[{id, value}]` 모양이라 소비자 셋이 그대로 읽는다.

    ``algo``: ``weakly_connected_components`` 또는 ``leiden``. ``rel_tables`` 미지정이면
    :data:`ALGO_REL_TABLES`. 커뮤니티는 호출부가 RELATED_TO 만 넘긴다 — 개념 사이의
    느슨한 연관이 군집의 뼈대이고, 방향 관계는 도메인 축과 따로 가는 '실측 군집'에
    섞이지 않게 둔다(엔진 시절의 선택을 그대로 잇는다)."""
    from . import graph_algo as ga
    fns = {"weakly_connected_components": ga.weakly_connected_components,
           "leiden": ga.leiden}
    fn = fns.get(algo)
    if fn is None:
        return None, f"algo_unsupported: {algo!r} (지원: {sorted(fns)})"
    try:
        nodes, edges = _algo_graph(store, owner, tuple(rel_tables or ALGO_REL_TABLES))
        assign = fn(nodes, edges)
        if graph_out is not None:
            # 품질 지표가 같은 투영을 다시 읽지 않게 넘겨준다.
            graph_out.update(nodes=nodes, edges=edges, assign=assign)
    except Exception as exc:
        return None, f"{_classify_chunk_error(exc)}: {type(exc).__name__}: {exc}"
    return [{"id": n, "value": v} for n, v in assign.items()], None


# ── 자르면 잘렸다고 말한다 (F6) ──────────────────────────────────────────────
# 실측(2026-08-08): `vinc_diagnose(components)` 가 `size:173` 인 컴포넌트의 members 를
# **50개만 싣고 아무 표시도 안 했다.** 무음 절단은 이 리포가 반복해 물린 형태다 —
# 받는 쪽은 그게 전부인 줄 알고, 그 위에서 내린 판단이 조용히 틀린다.
#
# 원칙: 자른 자리마다 ① 상한을 **호출자가 정할 수 있게** 하고 ② 잘렸다는 사실과
# ③ 전체 개수를 함께 싣는다. `size` 처럼 전체 수가 이미 있으면 그것으로 갈음한다.
MEMBER_LIMIT_DEFAULT = 50
MEMBER_LIMIT_MAX = 1000


def _clamp_limit(val, default: int, cap: int) -> int:
    """호출자가 준 상한을 안전 범위로 접는다. 0·음수·비정수는 기본값."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, cap)) if n > 0 else default


def domain_root_diagnosis(*, owner: str, cfg=None, store=None,
                          member_limit: int | None = None) -> dict:
    """Read-only steward census of topic roots; multiple roots are not an error.

    Count the four authored kinds that carry a topic, not derived Domain nodes
    or Bridges. Run only on request, never on the ingest or layout path.
    """
    if not owner:
        return {"kind": "domain_root_diagnosis", "executed": False,
                "note": "an owner is required"}
    census = domains(owner=owner, cfg=cfg, store=store)
    if not census.get("executed"):
        return dict(census, kind="domain_root_diagnosis")
    counts: dict[str, int] = {}
    assigned, unassigned = [], 0
    for row in census.get("rows", []):
        path = _domain_path(row["domain"]) if row["domain"] != "(none)" else ()
        if not path:
            unassigned += row["total"]
            continue
        root = path[0]
        counts[root] = counts.get(root, 0) + row["total"]
        assigned.append(dict(row, root=root))
    total = sum(counts.values())
    roots = [{"root": root, "nodes": n, "share": n / total if total else 0}
             for root, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    # A tie is not broken alphabetically into a fabricated dominant root.
    largest = roots[0]["root"] if roots and (len(roots) == 1 or
              roots[0]["nodes"] > roots[1]["nodes"]) else None
    outside = [r for r in assigned if largest and r["root"] != largest]
    outside.sort(key=lambda r: (-r["total"], r["domain"]))
    limit = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
    return {"kind": "domain_root_diagnosis", "executed": True,
            "counted_kinds": ["concept", "decision", "record", "episode"],
            "roots": roots, "largest_root": largest, "assigned_nodes": total,
            "unassigned_nodes": unassigned,
            "outside_largest_root": outside[:limit],
            "outside_domain_count": len(outside),
            "outside_node_count": sum(r["total"] for r in outside),
            "members_truncated": len(outside) > limit,
            "note": "Review unexpected topic roots; multiple roots can be intentional. "
                    "This census does not reject writes or change the layout."}


def component_diagnosis(*, owner: str | None = None, cfg=None, store=None,
                        member_limit: int | None = None) -> dict:
    """E-G3 WCC 구조 진단 — 연결 컴포넌트 수·크기 분포 + 고아(단일 노드) 개념 목록.
    "연결 안 된 섬 n개"(상태 화면)와 suggest_ties 후보 소스의 근거. 조회 전용.

    반환 {executed, components:[{group, size, members, members_truncated}],
    orphans:[id…], component_count, orphan_count, ambiguous, ambiguous_count}.
    스토어를 못 읽으면 executed=False + note.

    ``ambiguous`` — **두 스페이스에 같은 슬러그로 존재해 어느 쪽이 내 것인지 못 가리는
    id**. 컴포넌트에도 고아에도 안 넣는다. 왜 생기는지는 아래 계산 자리의 주석에 있다.

    ``member_limit`` — 컴포넌트당 실을 구성원 수(기본 50, 상한 1000). 여태 50 으로
    **말없이** 잘라서, `size:173` 인 컴포넌트도 50개만 보이고 그게 전부인 줄 알기
    쉬웠다(F6). 이제 자르면 `members_truncated:true` 로 말한다 — 전체 수는 `size` 가
    이미 나른다."""
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
        rows, note = _run_algo(store, "weakly_connected_components", owner=owner)
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        # 2026-09-10 실측: `concept:agent-steward` 가 크기 1 컴포넌트로 **네 번** 나왔고
        # 그 수가 그 슬러그를 가진 스페이스 수와 맞았다. 사유는 `_ambiguous_algo_ids`.
        ambiguous = _ambiguous_algo_ids(rows, scope)
        drop = set(ambiguous)
        groups: dict = {}
        for r in rows:
            cid, grp = r.get("id"), r.get("value")
            if not cid or cid in drop or (scope is not None and cid not in scope):
                continue
            groups.setdefault(grp, []).append(cid)
        mlim = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        comps = [{"group": g, "size": len(m), "members": sorted(m)[:mlim],
                  "members_truncated": len(m) > mlim}
                 for g, m in groups.items()]
        comps.sort(key=lambda c: (-c["size"], str(c["group"])))   # 큰 컴포넌트 우선
        orphans = sorted(m[0] for m in groups.values() if len(m) == 1)
        base.update(executed=True, components=comps, orphans=orphans,
                    component_count=len(comps), orphan_count=len(orphans),
                    ambiguous=ambiguous, ambiguous_count=len(ambiguous))
        if ambiguous:
            base["ambiguous_note"] = (
                "these ids exist in more than one space, so this diagnosis cannot tell "
                "which node is yours and leaves them out of components and orphans")
    finally:
        if owns:
            store.close()
    return base


#: 경로처럼 생긴 토큰 — 슬래시가 하나 이상 있고 짧은 확장자로 끝난다. `and/or` 나
#: 날짜를 잡지 않고 `api/sync.py` · `app/src/surfaces/view3d.ts` 를 잡는 최소 모양이다.
#: URL 은 아래에서 먼저 지우고 찾는다(`https://vincs.io/docs/x.html` 이 경로로 보인다).
_PATH_TOKEN = re.compile(r"(?<![\w/.-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{1,5}(?![\w/])")
_URLISH = re.compile(r"\w+://\S+")
#: 주제가 될 수 없는 경로 — 가상환경·의존성·빌드 산출물. 실측(2026-08-25): 닻 없는
#: 45건 중 하나가 `./.venv/Scripts/python.exe` 였는데 그건 **명령**이지 주장의 대상이
#: 아니다. 이 목록은 줄이라고 있는 것이라, 줄일 수 없는 항목이 남으면 아무도 안 줄인다.
_NOT_A_SUBJECT = re.compile(
    r"(^|/)(\.venv|venv|node_modules|dist|build|__pycache__|\.git|target)(/|$)")
#: 주장 진단이 훑는 텍스트 필드(props 안). 노트 이름이 두 벌 쓰인다(note·ko).
_CLAIM_TEXT_KEYS = ("note", "ko", "state", "summary")


def _path_hint(props: dict, title: str) -> str:
    """이 기록이 코드를 두고 말하고 있나 — 그렇다면 처음 나온 경로 토큰."""
    parts = [title] + [str(props.get(k) or "") for k in _CLAIM_TEXT_KEYS]
    text = _URLISH.sub(" ", "\n".join(parts))
    # 첫 매치가 아니라 **주제가 될 수 있는 첫 매치**다. 노트가 명령줄과 파일을 함께
    # 담는 일이 흔해서, 먼저 나온 쪽이 `.venv` 이면 진짜 주제를 놓친다.
    for m in _PATH_TOKEN.finditer(text):
        if not _NOT_A_SUBJECT.search(m.group(0)):
            return m.group(0)
    return ""


def claim_diagnosis(*, owner: str | None = None, cfg=None, store=None,
                    limit: int | None = None) -> dict:
    """R5 — **주장 진단**: 같은 파일을 두고 어긋난 기록 짝, 그리고 코드를 말하면서
    닻이 없는 기록. [[vinc.ops.graph-claim-decay]] §7.3.

    §6.1 이 "조인 키는 만들었는데 조인을 돌리는 것이 없다"고 지목한 그 러너다. R4 가
    `props.subject_path` 를 쓰기 시점에 정규화해 두었으므로 여기서는 문자열 동치로
    묶으면 된다 — 그 정규화가 없으면 이 함수는 **건강해 보이면서 0건**을 낸다.

    반환 {executed, conflicts:[{subject_path, newer:{…}, older:{…}}], conflict_count,
    unanchored:[{id,title,path_hint}], unanchored_count, unanchored_truncated,
    anchored_count, scanned}.

    **어느 쪽이 옳은지 판정하지 않는다.** 두 측정이 다른 날짜에 나왔다는 사실만
    내놓는다. 판정은 경로를 열어 보는 사람의 몫이고, 그 열어 보기가 이 도구가
    대신할 수 없는 잔여다(§7.5).

    짝짓기는 **가장 최근 것 대 나머지**다(n² 가 아니다). 물어야 할 질문이 "이 옛
    주장이 아직 서 있나"이지 "옛 주장 둘이 서로 맞나"가 아니기 때문이다.

    기존 세 kind 가 그래프 **구조**(연결성·군집)를 읽는 데 반해 이것은 props **내용**을
    읽는다. 같은 문이지만 이웃한 코드가 아니라서 `component_diagnosis` 를 넓히지 않고
    따로 둔다."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "claim_diagnosis", "executed": False, "conflicts": [],
            "stale": [], "stale_count": 0,
            "conflict_count": 0, "unanchored": [], "unanchored_count": 0,
            "unanchored_truncated": False, "anchored_count": 0, "scanned": 0}
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        where = "WHERE r.owner_id = $owner " if owner else ""
        rows = store.run(
            f"MATCH (r:Record) {where}"
            "RETURN r.record_id AS id, r.title AS title, r.props AS props, "
            "r.valid_until AS valid_until",
            {"owner": owner} if owner else {})
        lim = _clamp_limit(limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        stale = _stale_claims(store, rows, owner=owner)
        by_path: dict[str, list[dict]] = {}
        unanchored: list[dict] = []
        for r in rows:
            props = _props_to_obj(r.get("props"))
            rid = pkns.strip(r.get("id") or "")
            title = r.get("title") or rid
            sp = str(props.get("subject_path") or "").strip()
            if sp:
                by_path.setdefault(sp, []).append({
                    "id": rid, "title": title,
                    "measured_at": str(props.get("measured_at") or ""),
                    "measured_ref": str(props.get("measured_ref") or "")})
                continue
            hint = _path_hint(props, title)
            if hint:
                unanchored.append({"id": rid, "title": title, "path_hint": hint})
        conflicts = []
        for sp, items in by_path.items():
            if len(items) < 2:
                continue
            # 빈 measured_at 은 정렬에서 맨 뒤다 — 시간을 못 대는 주장이 "가장 최근"
            # 자리를 차지하면 짝의 방향이 뒤집힌다.
            items.sort(key=lambda i: (i["measured_at"] or "", i["id"]), reverse=True)
            newest = items[0]
            for other in items[1:]:
                if other["measured_at"] == newest["measured_at"]:
                    continue          # 같은 날의 두 측정은 어긋남이 아니다
                conflicts.append({"subject_path": sp, "newer": newest, "older": other})
        conflicts.sort(key=lambda c: (c["newer"]["measured_at"], c["subject_path"]),
                       reverse=True)
        unanchored.sort(key=lambda u: (u["path_hint"], u["id"]))
        base.update(executed=True,
                    conflicts=conflicts[:lim], conflict_count=len(conflicts),
                    unanchored=unanchored[:lim], unanchored_count=len(unanchored),
                    unanchored_truncated=len(unanchored) > lim,
                    anchored_count=sum(len(v) for v in by_path.values()),
                    stale=stale[:lim], stale_count=len(stale),
                    stale_truncated=len(stale) > lim,
                    scanned=len(rows))
    finally:
        if owns:
            store.close()
    return base


_SHA = re.compile(r"[0-9a-f]{7,40}")


def _path_covers(subject: str, changed: str) -> bool:
    """주장의 경로가 바뀐 파일을 덮는가. 같은 파일이거나 그 디렉터리 아래다."""
    return changed == subject or changed.startswith(subject.rstrip("/") + "/")


def _stale_claims(store, record_rows: list[dict], *, owner: str | None) -> list[dict]:
    """측정 뒤에 경로가 바뀐 **현재** 주장. 변경 근거는 `props.files` 를 가진 Episode 다.

    판정하지 않는다. "이 주장을 잰 뒤에 그 파일이 바뀌었다"는 사실만 낸다. 같은 커밋
    (`measured_ref` 와 `head_sha` 가 접두로 겹침)에서 잰 것은 낡지 않았다. 측정과 같은 날의 변경은 `same_day` 로 표시해 낸다. 날짜가 없는 쪽은 비교하지
    않는다(없는 시간을 지어내지 않는다). 주장마다 가장 최근 변경 하나만 붙인다.
    근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] 를 잇는
    그래프 문서 vinc.plan.spec-drift-graphiti-0-9-8-2026-10-11 §3."""
    from .fragment import _norm_subject_path
    where = "WHERE e.owner_id = $owner " if owner else ""
    changes = []
    for e in store.run(f"MATCH (e:Episode) {where}"
                       "RETURN e.episode_id AS id, e.title AS title, e.date AS date, "
                       "e.props AS props", {"owner": owner} if owner else {}):
        props = _props_to_obj(e.get("props"))
        files = props.get("files")
        date = str(e.get("date") or props.get("date") or "")
        if not isinstance(files, list) or not files or not date:
            continue
        changes.append({"id": pkns.strip(e.get("id") or ""), "title": e.get("title") or "",
                        "date": date, "head_sha": str(props.get("head_sha") or ""),
                        "files": [_norm_subject_path(f) for f in files if isinstance(f, str)]})
    if not changes:
        return []
    today = _dt.date.today().isoformat()
    out = []
    for r in record_rows:
        until = str(r.get("valid_until") or "")
        if until and until <= today:
            continue                       # 은퇴한 주장은 낡을 수 없다
        props = _props_to_obj(r.get("props"))
        sp = str(props.get("subject_path") or "").strip()
        at = str(props.get("measured_at") or "")
        ref = str(props.get("measured_ref") or "")
        if not sp or not at:
            continue
        # 같은 커밋인지는 접두로 본다 — 측정 기록은 짧은 sha 를, 변경 기록은 긴 sha 를
        # 흔히 싣는다. 날짜가 같으면 하루 안의 순서를 알 수 없으므로 감추지 않고
        # `same_day` 로 표시해 낸다(조용히 빠지면 같은 날 머지가 주장을 영영 못 건드린다).
        def same_commit(sha: str) -> bool:
            # 둘 다 커밋 해시(7~40자 16진)일 때만 접두로 맞춘다. 브랜치 이름 같은 다른
            # 참조는 글자 그대로 같아야 같다 — `main` 이 `main-fix` 의 접두가 되면 안 된다.
            if not (ref and sha):
                return False
            if _SHA.fullmatch(ref) and _SHA.fullmatch(sha):
                return sha.startswith(ref) or ref.startswith(sha)
            return sha == ref
        hits = [c for c in changes
                if c["date"] >= at and not same_commit(c["head_sha"])
                and any(_path_covers(sp, f) for f in c["files"])]
        if not hits:
            continue
        latest = max(hits, key=lambda c: (c["date"], c["id"]))
        rid = pkns.strip(r.get("id") or "")
        out.append({"claim": {"id": rid, "title": r.get("title") or rid, "subject_path": sp,
                              "measured_at": at, "measured_ref": ref},
                    "change": {k: latest[k] for k in ("id", "title", "date", "head_sha")},
                    "changes_since": len(hits),
                    "same_day": latest["date"] == at})
    out.sort(key=lambda x: (x["change"]["date"], x["claim"]["id"]), reverse=True)
    return out


def communities(*, owner: str | None = None, cfg=None, store=None,
                member_limit: int | None = None) -> dict:
    """E-G3 Leiden 커뮤니티 검출 — 개념 군집(도메인 축과 별개의 '실측 군집' 렌즈).
    각 커뮤니티에 지배 도메인·크기·대표(사전순 첫 id)를 붙여 **테마 proposed 제안**의
    근거로 쓴다(재가는 사용자 — T1/T3, 여기서는 쓰기 없음). 조회 전용.

    반환 {executed, communities:[{community, size, dominant_domain, members,
    members_truncated}], community_count}. ``member_limit`` 은 component_diagnosis
    와 같다(기본 50 · 상한 1000 · 자르면 말한다 — F6). 미배정(-1, 고립)은 별도 집계에서 제외. 스토어를 못 읽으면
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
        # RELATED_TO 단일 투영(개념 관계의 주축: wikilink 추론이 전부 RELATED_TO 로
        # 강등돼 밀도 최고). 엔진 Louvain 이 단일 엣지 표만 받던 시절의 선택인데, 방향
        # 관계를 섞지 않는 '실측 군집'이라는 뜻이 있어 계산을 옮긴 뒤에도 잇는다.
        _g: dict = {}
        rows, note = _run_algo(store, "leiden", rel_tables=("RELATED_TO",), owner=owner,
                               graph_out=_g)
        if rows is None:
            base["note"] = note
            return base
        # 커뮤니티마다 품질(연결·밀도·전도율·모듈러리티 몫). 판정은 안 한다 — 숫자만 싣고
        # 어떤 군집을 믿을지는 읽는 쪽이 정한다. 문헌 조사 §9 "Community quality" 경고의 답.
        from . import graph_algo as _ga
        _quality = _ga.community_quality(_g.get("nodes") or [], _g.get("edges") or [],
                                         _g.get("assign") or {})
        scope = _owner_concept_ids(store, owner)
        # 컴포넌트 쪽과 같은 자리다 — 두 스페이스에 사는 슬러그는 커뮤니티 둘에 들어가
        # `size` 를 부풀린다. 고르지 않고 갈라 낸다(`_ambiguous_algo_ids`).
        ambiguous = _ambiguous_algo_ids(rows, scope)
        drop = set(ambiguous)
        # 도메인 부착(지배 도메인 산정) — owner 스코프 개념 도메인 맵.
        dwhere = "WHERE c.owner_id = $owner " if owner else ""
        dparams = {"owner": owner} if owner else None
        drows = store.run("MATCH (c:Concept) " + dwhere +
                          "RETURN c.concept_id AS id, c.domain AS domain", dparams)
        domain_of = {r["id"]: (r.get("domain") or "") for r in drows}
        groups: dict = {}
        for r in rows:
            cid, com = r.get("id"), r.get("value")
            if not cid or com == -1 or cid in drop \
                    or (scope is not None and cid not in scope):
                continue   # -1 = 미배정(고립) — 커뮤니티 아님
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
            _ml = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
            out.append({"community": com, "size": len(members),
                        "dominant_domain": dominant,
                        "quality": {k: v for k, v in (_quality.get(com) or {}).items()
                                    if k != "size"},
                        "members": sorted(members)[:_ml],
                        "members_truncated": len(members) > _ml})
        out.sort(key=lambda c: (-c["size"], str(c["community"])))
        base.update(executed=True, communities=out, community_count=len(out),
                    algorithm="leiden", ambiguous=ambiguous, ambiguous_count=len(ambiguous))
        if ambiguous:
            base["ambiguous_note"] = (
                "these ids exist in more than one space, so this diagnosis cannot tell "
                "which node is yours and leaves them out of the communities")
    finally:
        if owns:
            store.close()
    return base


#: community_context 의 대표(발췌 동봉) 상한 — 커뮤니티가 크면 전원에게 발췌를
#: 붙이는 비용이 MENTIONS 조회 N회로 뛴다. "대표"라는 말 자체가 전량이 아니라는
#: 뜻이라, 작게 못박는다(사용자가 조를 필요는 없다 — no-llm-chat 연구 §4처럼
#: '모델 없이' 답하는 broad 질문의 답은 소수의 근거면 된다).
COMMUNITY_REPRESENTATIVE_CAP = 5


def community_context(*, owner: str | None = None, node: str, cfg=None, store=None,
                      member_limit: int | None = None) -> dict:
    """E-G3 Leiden 커뮤니티 → **하나의 시드가 속한 군집**의 답변 재료(unified-kg-rag
    비교 §4 — "wire Louvain into retrieval", 모델 없는 `global` 전략의 절반. 2026-09-28
    부터 Leiden 이다 — 끊긴 군집이 답변 재료로 나가지 않는다). 조회
    전용, 문장을 생성하지 않는다.

    `communities()`(전체 커뮤니티 목록, id만)와 달리 **시드 하나가 속한 커뮤니티 하나**
    를 골라 그 구성원에 title·one_liner·발췌를 붙인다 — "이 영역엔 뭐가 있나" 같은
    넓은 질문에 node_context 가 못 주는 답을 준다. 대표(``representatives``, 최대
    :data:`COMMUNITY_REPRESENTATIVE_CAP`)만 발췌를 동봉한다 — MENTIONS 언급 수가
    가장 많은 순(문서화가 가장 잘 된 노드가 그 영역을 대표한다는 결정론적 기준).
    나머지 구성원은 id·title만 ``members``에 실린다(``member_limit`` — component_
    diagnosis/communities 와 같은 규약, 잘리면 ``members_truncated``).

    반환 {executed, node, community, size, quality, dominant_domain,
    representatives:[{id, title, one_liner, excerpt}],
    members:[{id, title}], members_truncated}. 시드가 없거나 고립(-1)이면
    executed=False + note. 스토어를 못 읽을 때도 마찬가지."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "community-context", "executed": False, "node": node,
            "community": None, "size": 0, "dominant_domain": "",
            "representatives": [], "members": [], "members_truncated": False}
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        _g: dict = {}
        rows, note = _run_algo(store, "leiden", rel_tables=("RELATED_TO",), owner=owner,
                               graph_out=_g)
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        # 🔴 **여기가 셋 중 가장 조용한 자리였다.** `assign` 은 논리 id 로 키를 잡는
        # dict 라, 같은 슬러그가 두 스페이스에 있으면 **마지막 행이 이긴다** — 씨앗의
        # 커뮤니티로 남의 스페이스 값을 집어 그 위에서 대표와 발췌를 만들고, 아무 표시도
        # 없다. 컴포넌트 쪽은 중복이 눈에 보이기라도 했다.
        # 씨앗이 모호하면 **답하지 않는다**: 고르는 것은 추측이다.
        ambiguous = set(_ambiguous_algo_ids(rows, scope))
        if node in ambiguous:
            base["note"] = (f"{node} exists in more than one space, so this cannot tell "
                            "which node is yours")
            base["ambiguous"] = True
            return base
        assign = {r["id"]: r["value"] for r in rows
                  if r.get("id") and r["id"] not in ambiguous}
        com = assign.get(node)
        if com is None or com == -1 or (scope is not None and node not in scope):
            base["note"] = f"unknown or isolated node: {node}"
            return base
        members = [cid for cid, c in assign.items()
                  if c == com and (scope is None or cid in scope)]

        dwhere = "WHERE c.owner_id = $owner " if owner else ""
        dparams = {"owner": owner} if owner else None
        drows = store.run("MATCH (c:Concept) " + dwhere +
                          "RETURN c.concept_id AS id, c.title AS title, "
                          "c.domain AS domain, c.owner_id AS owner_id", dparams) or []
        title_of = {r["id"]: (r.get("title") or r["id"]) for r in drows}
        domain_of = {r["id"]: (r.get("domain") or "") for r in drows}
        # node_context 는 owner=None(비스코프)을 안 받는다 — 시드별 실제 owner_id 로
        # 부른다(unscoped 로컬 모드에서도 발췌가 나오게. 통상 단일 소유자 스토어라
        # 전원이 같은 값이지만, 명시 owner 가 있으면 그 값이 항상 이긴다).
        owner_of = {r["id"]: (r.get("owner_id") or "") for r in drows}
        dom_counts: dict = {}
        for cid in members:
            d = domain_of.get(cid, "")
            if d:
                dom_counts[d] = dom_counts.get(d, 0) + 1
        dominant = max(dom_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] \
            if dom_counts else ""

        mrows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) WHERE c.concept_id IN $ids "
            # 무스코프 로컬에서는 구성원마다 owner 가 다를 수 있어 **각자의 owner** 로
            # 합성한다(바로 위 node_context 호출이 쓰는 그 규칙과 같다).
            "RETURN c.concept_id AS id, count(ch) AS n",
            {"ids": [pkns.key(owner or owner_of.get(cid, ""), cid)
                     for cid in members]}) or []
        mentions = {r["id"]: int(r.get("n") or 0) for r in mrows}
        ranked = sorted(members, key=lambda cid: (-mentions.get(cid, 0), cid))

        reps = []
        for cid in ranked[:COMMUNITY_REPRESENTATIVE_CAP]:
            ctx = node_context(owner=owner or owner_of.get(cid, ""), node=cid,
                               cfg=cfg, store=store)
            excerpt = (ctx.get("excerpts") or [{}])[0].get("excerpt", "")
            reps.append({"id": cid, "title": title_of.get(cid, cid),
                        "one_liner": ctx.get("one_liner") or "", "excerpt": excerpt})

        mlim = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        rest = ranked[COMMUNITY_REPRESENTATIVE_CAP:]
        member_list = [{"id": cid, "title": title_of.get(cid, cid)}
                       for cid in sorted(rest)[:max(0, mlim - len(reps))]]
        # 답변 재료로 나가는 군집이라 품질을 함께 싣는다 — `communities()` 와 같은 숫자
        from . import graph_algo as _ga
        _quality = _ga.community_quality(_g.get("nodes") or [], _g.get("edges") or [],
                                         _g.get("assign") or {}).get(com) or {}
        base.update(executed=True, community=com, size=len(members), algorithm="leiden",
                    quality={k: v for k, v in _quality.items() if k != "size"},
                    dominant_domain=dominant, representatives=reps,
                    members=member_list,
                    members_truncated=len(members) > len(reps) + len(member_list))
    finally:
        if owns:
            store.close()
    return base



# ── 실패 응답의 다음 행동 (F7) ───────────────────────────────────────────────
# 실패는 **무엇**만이 아니라 **다음**을 말해야 한다: `error`(무엇) + `hint`(다음) +
# `candidates`(어디로). 그 전에는 없는 노드 조회가 `{"executed": true}` 만 답해
# 성공과 구분되지 않았다([[vinc.plan.mcp-hardening]]).

def near_nodes(store, needle: str, *, owner: str | None = None,
               limit: int = 3) -> list[dict]:
    """놓친 id/제목에 가까운 노드 후보 — 이미 있는 폴백 검색을 그대로 쓴다.

    새 색인을 만들지 않는다. `_node_fallback_search` 가 제목·요약·props 를 토큰 OR 로
    훑고 점수로 좁히는 일을 이미 한다. 접두사(`concept:`)는 떼고 넣는다 — 사용자가
    `concept:없는것` 을 줬을 때 접두사까지 검색어에 남으면 전부 똑같이 걸린다."""
    probe = needle.split(":", 1)[1] if ":" in needle else needle
    if not probe.strip():
        return []
    try:
        rows = _node_fallback_search(store, probe, limit=max(1, limit), owner=owner)
    except Exception:                                             # noqa: BLE001
        return []                      # 힌트를 만들다 본 응답을 죽이지 않는다
    return [{"id": r.get("id"), "title": r.get("title") or "",
             "domain": r.get("domain") or ""} for r in rows[:limit]]


def miss(kind: str, *, error: str, hint: str, **extra) -> dict:
    """실패 봉투 한 벌(F4) — `executed` 는 참이다. **조회는 돌았고 답이 없을 뿐**이라,
    거짓으로 두면 호출부가 스토어 장애와 구분하지 못한다(그 구분이 이 리포의 계약)."""
    out = {"kind": kind, "executed": True, "found": False,
           "error": error, "hint": hint}
    out.update({k: v for k, v in extra.items() if v not in (None, [], {})})
    return out


def decide_topic_resolve_plan(topic: str, *, owner: str | None = None,
                              label: str = "Concept") -> QueryPlan:
    """decide 토픽 → seed id 해석(A1.9 A5). id 정확일치 또는 title 대소문자 무시
    일치. decide_plan 의 `title=$topic`(완전일치) 대비 title 은 관대.

    ``label`` 은 의미층 라벨(:data:`_SEMANTIC_PK`)이다. 기본 Concept 일 때 플랜
    문자열은 종전과 같다. :func:`decide` 가 Concept 다음에 Decision 으로 다시 묻는다.

    ⚠ owner(SEC-1): 소유자를 주면 그 스코프 안에서만 해석한다. 없으면 남의 개념을
    시드로 잡아 서브그래프 전체를 되돌려줬다. OR 조건은 반드시 괄호로 묶는다 —
    안 그러면 owner AND 가 뒤 절에만 붙어 필터가 무력화된다."""
    # `$topic` 은 title 비교에 그대로 쓰이므로 논리로 남기고, id 열에는 물리 키를
    # 따로 바인딩한다(decide_plan 과 같은 이유).
    params: dict = {"topic": topic, "tkey": pkns.key(owner, topic)}
    pk = _SEMANTIC_PK[label]
    where = (f"(c.{pk} = $tkey "
             "OR lower(coalesce(c.title,'')) = lower($topic))")
    if owner:
        where += " AND c.owner_id = $owner"
        params["owner"] = owner
    cypher = f"MATCH (c:{label}) WHERE {where} RETURN c.{pk} AS id LIMIT 1"
    return QueryPlan("decide_resolve", cypher, params,
                     note=f"resolve topic → {label.lower()} seed")


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

    ``conflicts`` 는 :func:`_decide_conflicts` 가 구조만으로 판별한다. 크로스도메인은
    **별도 필드**(`cross_domain`)로 나갔다 — 여태 그것이 conflicts 자리를 독점해서,
    이 도구가 "상충"이라고 부르는 것은 언제나 도메인이 다른 연결뿐이었다."""
    base = {"kind": "decide", "executed": False, "topic": topic, "depth": depth,
            "found": False, "nodes": [], "edges": [], "conflicts": [],
            "cross_domain": []}
    # SEC-1: 토픽 해석·순회·행조회 전부 owner 스코프. 없으면 남의 개념을 시드로
    # 잡아 서브그래프가 통째로 노출됐다(vinc_decide 는 owner 를 보내지도 않았다).
    res = execute(decide_topic_resolve_plan(topic, owner=owner), cfg, store=store)
    if not res.get("executed"):
        base["note"] = res.get("note", "")
        return base
    base["executed"] = True
    seed_label = "Concept"
    seed = next((r["id"] for r in res.get("rows", []) if r.get("id")), None)
    if not seed:
        # 결정도 토픽이다(v12 부터 제 테이블). Concept 만 물어서 결정 id 가 자기 자신을
        # 첫 후보로 내밀며 "없다"고 답했다(2026-09-29 실측). 개념이 먼저다 — 제목이
        # 겹치면 종전대로 개념이 시드다.
        dres = execute(decide_topic_resolve_plan(topic, owner=owner, label="Decision"),
                       cfg, store=store)
        seed = next((r["id"] for r in dres.get("rows", []) if r.get("id")), None)
        seed_label = "Decision"
    if not seed:
        # F7 — `topic` 은 **정확 일치**다(concept_id · decision_id · title). 부분 일치가
        # 아니라서 `view` 는 안 맞고 `concept:view` 나 정확한 제목이라야 맞는다.
        # 그 계약이 어디에도 안 적혀 있었고, 실패 응답도 그것을 말하지 않았다.
        cands = near_nodes(store, topic, owner=owner)
        base["note"] = f"topic not found: {topic}"
        base["error"] = f"topic not found: {topic}"
        base["hint"] = ("`topic` matches exactly on a concept or decision id "
                        "(e.g. `concept:view`), or a full concept or decision title "
                        "— not a substring. "
                        + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                           if cands else "")
                        + "Use vinc_search to find the id first.")
        if cands:
            base["candidates"] = cands
        return base
    base["found"] = True
    ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner,
                                        seed_label=seed_label), cfg, store=store)
    ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
    rows_res = execute(graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
    rows = list(rows_res.get("rows", []))
    if seed_label == "Decision":
        # graph_rows 는 Concept 전용이라 결정 시드는 제 행이 없다. 존재 조회로 노드를
        # 먼저 세우고(엣지 없는 결정도 nodes 에 선다) 시드의 ABOUT·의미층 엣지를 싣는다.
        # 다른 결정은 keep 밖이라 아래 ego 경계에서 걸러진다(nodes 는 개념 + 토픽).
        sr = execute(seed_lookup_plan(seed, owner=owner), cfg, store=store)
        rows += [{**r, "label": r.get("title"), "kind": "decision"}
                 for r in sr.get("rows", [])]
        for plan in (decision_seed_rows_plan(
                         seed, owner=owner,
                         rels="ABOUT|PREREQUISITE_OF|DERIVES_FROM|INHERITS_FROM"),
                     decision_seed_rows_plan(seed, owner=owner, outgoing=False)):
            rows += execute(plan, cfg, store=store).get("rows", [])
    shaped = shape_graph(rows)
    keep = set(ids)   # ego 경계(depth+1 유출 제거) — graph() 와 동일 정책
    shaped["edges"] = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
    attach_ppr(shaped, [seed])   # E-G2 — 토픽(시드) 관점 개인화 랭킹
    _order_graph_result(shaped)
    dec_res = execute(decisions_about_plan(ids, owner=owner), cfg, store=store)
    decisions = list(dec_res.get("rows", [])) if dec_res.get("executed") else []
    conflicts, cut = _decide_conflicts(shaped["edges"], decisions)
    out = {**base, "nodes": shaped["nodes"], "edges": shaped["edges"],
           "conflicts": conflicts,
           # 크로스도메인은 상충이 아니라 **연결의 성질**이다. 자기 필드로 나간다.
           "cross_domain": [{"source": e["source"], "target": e["target"],
                             "type": e["type"]}
                            for e in shaped["edges"] if e.get("cross_domain")]}
    if cut:
        out["conflicts_truncated"] = True
    return out


def decisions_about_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합의 개념에 ABOUT 으로 걸린 결정 — decide 의 상충 판정이 쓰는 추가 조회.

    ⚠ :func:`graph_rows_for_ids_plan` 은 Concept 전용이라 **결정은 decide 의
    서브그래프에 한 번도 실린 적이 없다.** 그래서 "이미 내려진 결정과 부딪히는가"를
    도구 설명이 약속하고도 구조적으로 답할 수 없었다. 결정을 nodes 에 합류시키지
    않고 여기서 따로 읽는 이유는, decide 의 nodes/edges 계약(Concept 서브그래프)을
    바꾸지 않고 상충만 채우기 위해서다."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    where = "c.concept_id IN $ids"
    if owner:
        where += " AND c.owner_id = $owner AND d.owner_id = $owner"
        params["owner"] = owner
    cypher = (f"MATCH (d:Decision)-[:ABOUT]->(c:Concept) WHERE {where} "
              "RETURN d.decision_id AS id, d.title AS title, c.concept_id AS subject")
    return QueryPlan("decisions_about", cypher, params,
                     note="decisions attached to the id set")


#: 방향이 곧 의미인 엣지. RELATED_TO 는 무방향이라(역방향이 같은 간선으로 접힌다)
#: 여기 없다 — 넣으면 정상 관계가 전부 모순으로 보고된다.
_DIRECTED_RELS = frozenset({"PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"})
#: 한 응답이 싣는 상충 상한. 넘치면 `conflicts_truncated` 로 말한다(F6 — 잘린 것을
#: 말하지 않으면 받는 쪽은 "이게 전부"로 읽는다).
_CONFLICT_CAP = 50


def _decide_conflicts(edges: list[dict],
                      decisions: list[dict]) -> tuple[list[dict], bool]:
    """구조만으로 판별되는 상충(순수 함수 — 모델 판단 없음, 편들지 않는다).

      ① ``decisions_on_same_subject`` — 같은 개념을 두고 내려진 결정이 둘 이상.
         결정을 하나 더 쓰기 전에 읽어야 하는 바로 그것이다.
      ② ``opposite_direction`` — 한 쌍에 방향이 반대인 유향 엣지가 둘 다 있다.
         방향이 의미인 관계에서 이건 둘 중 하나가 틀렸다는 뜻이다.
      ③ ``duplicate_decision_title`` — 제목이 같은 결정 둘. 같은 것을 두 번
         결정했거나, 하나가 다른 하나를 대체하는데 그렇게 적히지 않았다.

    반환 ``(conflicts, truncated)``. 순서는 결정론이다 — 같은 그래프를 두 번 물으면
    같은 순서로 답해야 한다."""
    out: list[dict] = []

    # ① 같은 주제에 걸린 결정들.
    by_subject: dict[str, set[str]] = {}
    titles: dict[str, str] = {}
    for r in decisions:
        did, subj = str(r.get("id") or ""), str(r.get("subject") or "")
        if not did or not subj:
            continue
        by_subject.setdefault(subj, set()).add(did)
        titles[did] = str(r.get("title") or "")
    for subj, dec_ids in sorted(by_subject.items()):
        for a, b in itertools.combinations(sorted(dec_ids), 2):
            out.append({"source": a, "target": b, "type": "ABOUT",
                        "reason": "decisions_on_same_subject", "subject": subj})

    # ② 같은 쌍 위의 역방향 유향 엣지. 쌍당 한 번만 낸다(정렬로 고정).
    directed: dict[tuple[str, str], set[str]] = {}
    for e in edges:
        t = str(e.get("type") or "")
        if t in _DIRECTED_RELS and e.get("source") and e.get("target"):
            directed.setdefault((str(e["source"]), str(e["target"])), set()).add(t)
    for (s, t), fwd in sorted(directed.items()):
        back = directed.get((t, s))
        if back and s < t:
            out.append({"source": s, "target": t, "type": sorted(fwd)[0],
                        "reason": "opposite_direction",
                        "reverse_type": sorted(back)[0]})

    # ③ 제목이 같은 결정. 공백·대소문자만 정규화한다(그 이상은 판단이다).
    by_title: dict[str, set[str]] = {}
    for did, title in titles.items():
        key = " ".join(title.split()).casefold()
        if key:
            by_title.setdefault(key, set()).add(did)
    for key, dec_ids in sorted(by_title.items()):
        if len(dec_ids) < 2:
            continue
        for a, b in itertools.combinations(sorted(dec_ids), 2):
            out.append({"source": a, "target": b, "type": "",
                        "reason": "duplicate_decision_title", "title": titles[a]})

    return out[:_CONFLICT_CAP], len(out) > _CONFLICT_CAP


# ── 삭제 / 병합 (T3c C3 — 오염 정리 쓰기 표면) ────────────────────────────────
# 실사용 사고 ③: 쓰기 표면이 MERGE 단독이라 중복·빈 껍데기 노드(concept:ipaas 등)가
# 생겨도 되돌릴 수단이 없어 오염이 영구화됐다. delete/merge 는 그 정리 수단이다 —
# 둘 다 **비가역**. 사이드카에서는 잡 큐(run_sync)로 직렬화된다(단일 writer, R4).

# owner_id 열 보유 = 스코프 가드로 안전하게 삭제할 수 있는 라벨. _SEED_TABLES 의
# 5종과 정확히 같다(전부 owner_id + _TRACKED_LABELS = 동기화 전파 대상).
# ⚠ 여기 없으면 delete 가 "refused"로 거부된다 — Bridge(v2)·Entity(F1)가 소유
# 스탬프를 얻은 뒤에도 이 집합이 3종에 머물러 있어 앱에서 브릿지를 못 지웠다(S1
# 실측). 확장 시 _NODE_PK(툼스톤)도 같이 넓혀야 KeyError 가 안 난다.
_OWNER_SCOPED_LABELS = {"Concept", "Episode", "Record", "Bridge", "Entity", "Decision"}
_NODE_PK = {"Concept": "concept_id", "Episode": "episode_id", "Record": "record_id",
            "Bridge": "bridge_id", "Entity": "entity_id", "Decision": "decision_id"}


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
        # ⚠ 리터럴 표를 두지 말 것. v12 가 Decision 을 늘렸을 때 여기만 빠져서, 도메인을
        # 개명하면 결정은 `domain` 열도 IN_DOMAIN 엣지도 옛 도메인에 남았다(v13 이
        # 백필 표는 고쳤지만 이 표는 못 봤다). DOMAIN_LABELS 가 `domain` 열을 가진
        # 라벨의 정본이므로 그것을 돈다 — 다음 의미층은 저절로 따라온다.
        for label, pk in domain_hierarchy.DOMAIN_LABELS.items():
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $o AND n.domain = $old "
                f"RETURN n.{pk} AS id, n.rev AS rev", {"o": owner, "old": old}) or []
            for r in rows:
                st = revstamp.make_stamp(store, {"rev": r.get("rev"), "owner": owner})
                store.run(
                    f"MATCH (n:{label}) WHERE n.{pk} = $id AND n.owner_id = $o "
                    "SET n.domain = $new, n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                    # 위 조회가 돌려준 id 는 읽기 훅이 되돌린 **논리** 슬러그다 —
                    # 다시 바인딩할 때 합성해야 한다(PK 네임스페이싱).
                    {"id": pkns.key(owner, r["id"]), "o": owner, "new": new,
                     "ua": st["updated_at"], "rev": st["rev"],
                     "seq": st["sync_seq"]})
                # 계층(E-1, v11)도 따라와야 한다. 안 옮기면 노드의 `domain` 열은 새
                # 도메인인데 IN_DOMAIN 은 옛 도메인을 가리켜, 열 기준 집계(/domains)와
                # 그래프 기준 조회가 서로 다른 답을 준다. `new` 가 비면 엣지만 지운다
                # (= 미분류로 내림, 위 계약과 동형).
                domain_hierarchy.link_node(store, label, pkns.key(owner, r["id"]), new)
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
                     {"id": pkns.key(owner, node_id)})
    if not rows:
        return
    own = owner or rows[0].get("owner") or "user:local"
    # PK 네임스페이싱 — 툼스톤 PK 도 노드 id 라 같이 합성한다(감사 M3: 안 하면 두
    # 계정이 같은 슬러그를 지울 때 **툼스톤 행 하나를 공유**해 진 쪽의 삭제가 전파를
    # 멈춘다). owner 는 위에서 노드에서 승계한 값이라 무스코프 삭제에서도 맞는다.
    store.run("MERGE (t:_Tombstone {node_id: $id}) SET t.label = $l, t.owner_id = $o, "
              "t.deleted_at = $da, t.rev = $rev, t.sync_seq = $seq, t.origin = 'local'",
              {"id": pkns.key(own, node_id), "l": label, "o": own,
               "da": revstamp.hlc_now(store), "rev": int(rows[0].get("rev") or 0),
               "seq": revstamp.next_seq(store, own)})
_CC_RELS = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM", "RETURNS_TO")

# 동기화 대상 엣지 rel → **끝점 (src 라벨, src PK, dst 라벨, dst PK) 후보들**. 엣지
# 삭제 표면(delete_edge)과 sync 의 엣지 툼스톤 적용이 공유하는 단일 정의. fragment
# 타입명(relates_to·about…)도 여기로 정규화된다.
#
# ⚠ **손으로 적은 표를 두지 말 것.** 의미층이 늘면 rel 하나가 끝점 조합 여럿을 갖게
# 되고, 표가 낡으면 엣지 삭제가 **오류 없이 0건 매치**로 끝난다(툼스톤은 남고 엣지는
# 그대로 = 기기마다 다른 그래프). 스키마에서 파생한다
# ([[record:rel-tables-take-multiple-endpoint-pairs]]).
def _derive_sync_edge_rels() -> dict[str, tuple[tuple[str, str, str, str], ...]]:
    from store import embedded_schema as _es
    pks = {lbl: spec["pk"] for lbl, spec in _es.node_table_specs().items()}
    out: dict[str, list[tuple[str, str, str, str]]] = {}
    for spec in _es.rel_table_specs():
        rel, src, dst = spec["label"], spec["from"], spec["to"]
        if rel not in _SYNCED_EDGE_RELS or src not in pks or dst not in pks:
            continue
        out.setdefault(rel, []).append((src, pks[src], dst, pks[dst]))
    return {k: tuple(v) for k, v in out.items()}


_SYNCED_EDGE_RELS = _CC_RELS + ("ABOUT", "ATTACHES_TO", "SUPERSEDES")
SYNC_EDGE_RELS: dict[str, tuple[tuple[str, str, str, str], ...]] = _derive_sync_edge_rels()


def _graph_read_pairs() -> dict[tuple[str, str, str, str], tuple[str, ...]]:
    """graph/node/edge context의 끝점 계약. 표면별 분류와 whitelist는 별도로 유지한다.

    sync 계약의 스키마 파생 쌍 중 의미 관계·SUPERSEDES와 ABOUT만 읽는다. RETURNS_TO는 기존
    Concept 관계이고, ATTACHES_TO/CONNECTS는 계속 부착 카드 경로가 담당한다.
    """
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for rel in _CC_RELS + ("ABOUT", "SUPERSEDES"):
        for pair in SYNC_EDGE_RELS[rel]:
            # 기록끼리의 SUPERSEDES 도 읽는다. 쓰기는 같은 종끼리만 받으므로(fragment)
            # 의미층 밖에서는 이 한 쌍만 실재한다. 빠지면 vinc_graph·vinc_brief 가
            # vinc_neighbors 보다 관계를 적게 센다(감사 F2).
            if ((pair[0] in _SEMANTIC_LABELS and pair[2] in _SEMANTIC_LABELS)
                    or rel == "ABOUT"
                    or (rel == "SUPERSEDES" and pair[0] == pair[2] == "Record")):
                groups.setdefault(pair, []).append(rel)
    return {pair: tuple(rels) for pair, rels in groups.items()}


_GRAPH_READ_PAIRS = _graph_read_pairs()
_CONCEPT_READ_PATTERN = "|".join(_GRAPH_READ_PAIRS[("Concept", "concept_id", "Concept", "concept_id")])
_FRAGMENT_EDGE_TO_REL = {   # fragment 엣지 타입명 → rel (api/fragment 와 동형)
    "relates_to": "RELATED_TO", "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM", "inherits_from": "INHERITS_FROM",
    "about": "ABOUT",
    "returns_to": "RETURNS_TO",
    "supersedes": "SUPERSEDES",
}


def sync_edge_spec(rel: str):
    """Record 삭제는 저작 타입도 보존해 다른 타입의 관계를 지우지 않는다."""
    carrier, subtype = rel, None
    if rel.startswith("ATTACHES_TO:"):
        carrier, subtype = rel.split(":", 1)
        if subtype not in {"relates_to", "prerequisite_of", "derives_from", "inherits_from"}:
            return None
    pairs = SYNC_EDGE_RELS.get(carrier)
    return (carrier, subtype, pairs) if pairs else None


def _write_edge_tombstone(store, src: str, rel: str, dst: str, owner: str | None) -> None:
    """엣지 삭제 표식(S4 확장) — 엣지는 타임스탬프가 없어 노드보다 단순하다:
    (src, rel, dst) 키와 삭제 시각(HLC)·sync_seq 만 기록. owner 는 src 노드의
    소유를 승계(무스코프 삭제 시)한다 — 노드 툼스톤과 같은 규칙."""
    from . import revstamp
    own = owner
    if not own:
        # 끝점 후보를 훑어 **id 가 실제로 있는 라벨**에서 소유를 읽는다. 라벨을 하나로
        # 가정하면 결정 끝점에서 소유가 'user:local' 로 떨어져 남의 그래프에 툼스톤이
        # 앉는다.
        for sl, sp, _dl, _dp in sync_edge_spec(rel)[2]:
            rows = store.run(f"MATCH (n:{sl}) WHERE n.{sp} = $id RETURN n.owner_id AS o",
                             {"id": pkns.key(owner, src)})
            if rows:
                own = rows[0].get("o")
                break
        own = own or "user:local"
    store.run("MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = $r, "
              "t.dst = $d, t.owner_id = $o, t.deleted_at = $da, t.sync_seq = $seq, "
              "t.origin = 'local'",
              # `ekey` 는 논리 `src|REL|dst` 를 짓고 한 번만 합성한다(pkns.ekey).
              # ⚠ `src`/`dst` **열**은 논리로 남긴다 — 이 행은 동기화 와이어로 그대로
              # 나가고, 와이어에는 U+001F 가 있어서는 안 된다(계약 테스트가 고정한다).
              {"k": pkns.ekey(own, src, rel, dst), "s": src, "r": rel, "d": dst, "o": own,
               "da": revstamp.hlc_now(store), "seq": revstamp.next_seq(store, own)})


def delete_edge(src: str, edge_type: str, dst: str, *, owner: str | None = None,
                cfg=None, store=None) -> dict:
    """엣지 1개를 삭제하고 **엣지 툼스톤**을 남긴다(S4 확장 — 삭제 전파). 대상은
    Record→Concept|Decision 부착은 원 타입으로 고르거나 ATTACHES_TO로 고른다.
    원 타입 선택의 툼스톤도 타입을 보존한다. CONNECTS·TYPED는 범위 밖이다.

    ``edge_type`` 은 fragment 타입명(relates_to·about…)과 rel 명(RELATED_TO…) 둘 다
    허용. RELATED_TO 는 의미상 무방향 — **양방향 물리 엣지를 모두** 지운다.
    owner(SEC-3): 주어지면 양 끝점을 owner_id 로 스코프 — 남의 엣지는 못 지운다."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    rel = _FRAGMENT_EDGE_TO_REL.get((edge_type or "").strip().lower(),
                                    (edge_type or "").strip().upper())
    subtype = None
    if src.startswith("record:") and (edge_type or "").strip().lower() in {
            "relates_to", "prerequisite_of", "derives_from", "inherits_from"}:
        subtype, rel = edge_type.strip().lower(), "ATTACHES_TO"
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
    scope_a = scope_b = ""
    params: dict = {"s": pkns.key(owner, src), "d": pkns.key(owner, dst)}
    qualifier = " AND r.type = $subtype" if subtype is not None else ""
    if subtype is not None:
        params["subtype"] = subtype
    if owner:
        scope_a, scope_b = " AND a.owner_id = $owner", " AND b.owner_id = $owner"
        params["owner"] = owner
    try:
        pairs = [(src, dst)]
        if rel == "RELATED_TO":   # 무방향 의미 — 역방향 물리 엣지도 대상
            pairs.append((dst, src))
        deleted = 0
        # 끝점 라벨 조합 전수 — 안 맞는 조합은 MATCH 가 비어 no-op 이다.
        for sl, sp, dl, dp in SYNC_EDGE_RELS[rel]:
            for s_, d_ in pairs:
                p = {**params, "s": pkns.key(owner, s_), "d": pkns.key(owner, d_)}
                cnt = store.run(
                    f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                    f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b}{qualifier} "
                    "RETURN count(r) AS c", p)
                if cnt and int(cnt[0].get("c") or 0):
                    store.run(
                        f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                        f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b}{qualifier} DELETE r", p)
                    deleted += int(cnt[0].get("c") or 0)
        base["executed"] = True
        if not deleted:
            base["note"] = "edge not found"
            return base
        base["found"] = True
        base["deleted"] = {"edges": deleted}
        # 툼스톤은 정준 방향(요청 방향) 1개 — apply 측이 RELATED_TO 를 양방향 삭제한다.
        marker = rel + ":" + subtype if subtype is not None else rel
        _write_edge_tombstone(store, src, marker, dst, owner)
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def _drop_node_shadows(store, node_id: str, owner: str | None, physical_id: str) -> None:
    """노드 하나를 지울 때 함께 가야 하는 그림자 둘 — 검색 미러(NodeVec) 행과 3-way
    기준(_SyncBase) 행. 둘 다 노드 표 밖이라 DETACH DELETE 가 못 미치고, 미러 행은
    다음 기동의 전체 재구축까지 의미 팔에 남아 지운 노드가 검색 후보로 계속 나왔다
    (2026-09-26 갭 분석 §6, 판정 D11). 실패는 삼키지 않고 호출자의 예외 경로로 올린다."""
    from . import node_mirror
    store.run("MATCH (v:NodeVec) WHERE v.vec_id = $vid DELETE v",
              {"vid": node_mirror.vec_id(owner or "", node_id)})
    store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id DELETE b", {"id": physical_id})


def _delete_preview_edges(store, node_id: str, label: str, pk: str,
                          owner: str | None) -> list[dict]:
    from store import embedded_schema as es
    nodes = es.node_table_specs()
    seen, edges = set(), []
    for spec in es.rel_table_specs():
        for direction in ("out", "in"):
            own_end, other_end = (("from", "to") if direction == "out" else ("to", "from"))
            if spec[own_end] != label:
                continue
            other_label = spec[other_end]
            other_pk = nodes[other_label]["pk"]
            pattern = (f"(n:{label})-[r:{spec['label']}]->(o:{other_label})" if direction == "out" else
                       f"(o:{other_label})-[r:{spec['label']}]->(n:{label})")
            cols = nodes[other_label]["cols"]
            has_owner = "owner_id" in cols
            visible = "o.owner_id = $owner" if owner and has_owner else "true"
            subtype = "r.type" if spec["label"] in {"ATTACHES_TO", "TYPED"} else "''"
            params = {"id": pkns.key(owner, node_id), "owner": owner or ""}
            scope = " AND n.owner_id = $owner" if owner else ""
            rows = store.run(f"MATCH {pattern} WHERE n.{pk} = $id{scope} "
                             f"RETURN id(r) AS ek, o.{other_pk} AS other, {visible} AS visible, "
                             f"{subtype} AS subtype", params)
            for row in rows:
                key = json.dumps(row["ek"], sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                other = pkns.strip(str(row["other"])) if row.get("visible") else None
                edge = {"from": node_id if direction == "out" else other,
                        "to": other if direction == "out" else node_id,
                        "type": spec["label"], "dir": direction}
                if row.get("subtype"):
                    edge["subtype"] = row["subtype"]
                if other is None:
                    edge["endpoint_unavailable"] = True
                edges.append(edge)
    return sorted(edges, key=lambda e: json.dumps(e, sort_keys=True))


def delete_node(node_id: str, *, owner: str | None = None, cfg=None, store=None,
                dry_run: bool = False) -> dict:
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
    if type(dry_run) is not bool:
        base["error"] = "dry_run must be a boolean"
        if owns and store is not None:
            store.close()
        return base
    if dry_run:
        base["dry_run"] = True
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    # E-1 — 도메인 좌표는 **삭제할 수 있는 것이 아니다.** `Domain` 은 무소유 전역
    # 구조라 지우면 그 이름을 쓰는 남의 노드가 좌표를 잃는다. 사용자가 원하는 것은
    # "내 것들에서 이 도메인을 떼는 것"이고, 그건 이미 rename_domain(new='') 이다.
    # 일반 "unknown id prefix" 로 흘리면 그 안내가 안 나간다.
    _dom = domain_hierarchy.parse_id(node_id)
    if _dom:
        base["executed"] = True
        base["note"] = (f"refused: domains are a shared coordinate, not a deletable "
                        f"node ({node_id}). To clear it from your own nodes use "
                        f"domain rename with an empty target (old={_dom!r}, new='').")
        if owns:
            store.close()
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
        # PK 네임스페이싱 — 이 dict 는 존재확인·차수·DETACH DELETE 세 곳이 나눠 쓴다.
        params: dict = {"id": pkns.key(owner, node_id)}
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
            f"MATCH (n:{label})-[r]-() WHERE n.{pk} = $id{scope} RETURN count(DISTINCT r) AS c", params)
        edges = int(deg[0].get("c")) if deg else 0
        if dry_run:
            preview = _delete_preview_edges(store, node_id, label, pk, owner)
            if len(preview) != edges:
                base.update(executed=False, error="delete preview changed during read; try again")
                return base
            # 봉투 상한을 넘는 목록을 일부만 보여 주지 않는다. 실제 삭제는 하지 않는다.
            if len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) > 48000:
                base.update(executed=False, error="delete preview exceeds response budget",
                            edge_count=edges, preview_complete=False)
                return base
            base["preview"] = {"node": 1, "edges": preview, "edge_count": edges,
                               "complete": True}
            return base
        _write_tombstone(store, node_id, label, owner)   # 삭제 전파용 표식(S4)
        store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} DETACH DELETE n", params)
        _drop_node_shadows(store, node_id, owner, params["id"])
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
        # PK 네임스페이싱 — 이 함수의 모든 `$id`/`$s`/`$d`/`$r`/`$k`/`$b` 는 노드 PK
        # 바인딩이다. 반대로 조회가 돌려주는 `other` 는 읽기 훅이 되돌린 **논리**
        # 슬러그이므로, `keep_id`/`drop_id` 와의 비교는 논리끼리 그대로 맞는다 —
        # 합성은 **다시 바인딩할 때만** 한다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        def _k(v: str) -> str:
            return pkns.key(owner, v)

        def _exists(cid: str) -> bool:
            r = store.run(
                f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} "
                "RETURN count(c) AS c", {"id": _k(cid), **op})
            return bool(r and r[0].get("c"))

        base["executed"] = True
        if not (_exists(keep_id) and _exists(drop_id)):
            base["found"] = False
            base["note"] = "keep or drop not found"
            return base
        base["found"] = True

        repointed = skipped = 0

        # 반려 관계는 역할 권한 계약이다. 일반 중복 정리로 끝점과 근거를
        # 조용히 바꾸지 않는다. 구체적인 관계 변경을 먼저 검토해야 한다.
        from .role_contract import is_role_id
        if is_role_id(keep_id) or is_role_id(drop_id):
            role_returns = store.run(
                "MATCH (a:Concept)-[r:RETURNS_TO]->(b:Concept) "
                "WHERE (a.concept_id IN $ids OR b.concept_id IN $ids)"
                f"{oscope('a')}{oscope('b')} RETURN count(r) AS c",
                {"ids": [_k(keep_id), _k(drop_id)], **op})
            if role_returns and role_returns[0].get("c"):
                base["error"] = "role return paths require explicit review before merging nodes"
                return base

        def _cc_exists(rel: str, s: str, d: str) -> bool:
            r = store.run(
                f"MATCH (a:Concept)-[:{rel}]->(b:Concept) "
                f"WHERE a.concept_id = $s AND b.concept_id = $d{oscope('a')}{oscope('b')} "
                "RETURN count(*) AS c",
                {"s": _k(s), "d": _k(d), **op})
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
            return {row["other"] for row in store.run(cy, {"id": _k(drop_id), **op})
                    if row.get("other")}

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
                        f"MERGE (a)-[:{rel}]->(b)", {"s": _k(s), "d": _k(d), **op})
                    repointed += 1

        # ATTACHES_TO incoming: (rec:Record)-[a]->(drop) → (rec)-[a]->(keep), type 보존.
        # SEC-3: Record·drop·keep 모두 owner 스코프 — 남의 Record 를 내 keep 으로 못 옮긴다.
        for row in store.run(
                "MATCH (r:Record)-[a:ATTACHES_TO]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{oscope('r')} "
                "RETURN DISTINCT r.record_id AS other, a.type AS type",
                {"id": _k(drop_id), **op}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (r:Record)-[:ATTACHES_TO]->(k:Concept) "
                f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                "RETURN count(*) AS c",
                {"r": _k(other), "k": _k(keep_id), **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("type") is not None:
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "MERGE (r)-[e:ATTACHES_TO]->(k) SET e.type = $t",
                    {"r": _k(other), "k": _k(keep_id), "t": row["type"], **op})
            else:
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "MERGE (r)-[e:ATTACHES_TO]->(k)",
                    {"r": _k(other), "k": _k(keep_id), **op})
            repointed += 1

        # CONNECTS incoming: (br:Bridge)-[c]->(drop) → (br)-[c]->(keep), role 보존.
        # SEC-3: Concept 끝점(drop·keep)은 엄격 owner 스코프, Bridge 는 v2(F5)에서
        # owner_id 를 얻어 과도기 술어(bscope — 레거시 '' 포함)로 스코프한다.
        for row in store.run(
                "MATCH (b:Bridge)-[c:CONNECTS]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{bscope('b')} "
                "RETURN DISTINCT b.bridge_id AS other, c.role AS role",
                {"id": _k(drop_id), **op}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (b:Bridge)-[:CONNECTS]->(k:Concept) "
                f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                "RETURN count(*) AS c",
                {"b": _k(other), "k": _k(keep_id), **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("role") is not None:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k) SET e.role = $role",
                    {"b": _k(other), "k": _k(keep_id), "role": row["role"], **op})
            else:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k)",
                    {"b": _k(other), "k": _k(keep_id), **op})
            repointed += 1

        # drop 삭제(부착 엣지 포함) — title/props 버림(keep 우선).
        # SEC-3: 최종 파괴 연산도 owner 스코프(벨트&서스펜더 — _exists 게이트로 이미 소유
        # 확인됐지만, 파괴 연산은 이중 가드). owner 불일치면 아무것도 안 지운다.
        _write_tombstone(store, drop_id, "Concept", owner)   # drop 도 전파(S4)
        store.run(f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} DETACH DELETE c",
                  {"id": _k(drop_id), **op})
        _drop_node_shadows(store, drop_id, owner, _k(drop_id))
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
    지워질 때 문서 정체성 레지스트리도 함께 지운다. `_MergeRejection`(F10-b)도
    회원의 노드 id 쌍을 담으므로 계정 삭제에 함께 지워져야 한다."""
    from store.embedded_schema import owner_scoped_labels
    # `_EdgeTombstone` 도 owner_id 를 가지는데 메타 표라 파생 목록 밖이었고, 그래서 지운
    # 관계의 양 끝 id 가 계정 삭제 뒤에도 90일 GC 까지 남았다(2026-09-26 갭 분석, 그래프
    # vinc.research.privacy-compliance-gap-2026-09-26 §6, 판정 D11).
    return owner_scoped_labels() + ["_Tombstone", "_EdgeTombstone", "_DocMeta",
                                    "_DocTombstone", "_MergeRejection"]


def _purge_sync_base(store, owner: str) -> int:
    """`_SyncBase` 는 owner 열이 없다. 키가 `pkns.key(owner, node_id)` 라 네임스페이싱이
    켜진 스토어(허브)에서는 접두로 가를 수 있고, 꺼진 스토어(로컬, 소유자 하나)에서는
    가를 수 없어 건드리지 않는다 — 거기서는 소유가 하나라 잔여가 남의 것일 수 없다.
    이 표의 행은 기기가 밀어 올린 노드의 필드 스냅샷이라, 계정 삭제 뒤에 남으면
    제목·태그·props 가 이름 없는 채로 살아남는다(갭 분석 §6)."""
    if not pkns.enabled():
        return 0
    p = owner + pkns.SEP
    cnt = store.run(
        "MATCH (b:_SyncBase) WHERE b.node_id STARTS WITH $p RETURN count(b) AS c", {"p": p})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        store.run("MATCH (b:_SyncBase) WHERE b.node_id STARTS WITH $p DELETE b", {"p": p})
    return n


def _purge_sync_meta(store, owner: str) -> int:
    """`_SyncMeta` 의 소유자별 행(`seq:<owner>` 등)은 키 꼬리가 owner 다."""
    s = ":" + owner
    cnt = store.run(
        "MATCH (m:_SyncMeta) WHERE m.key ENDS WITH $s RETURN count(m) AS c", {"s": s})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        store.run("MATCH (m:_SyncMeta) WHERE m.key ENDS WITH $s DELETE m", {"s": s})
    return n


def _purge_owner_theme(cfg, owner: str) -> int:
    """어휘 파일(`<db>.themes.json`)의 소유자 주제(`owner-<sha256[:12]>`)를 지운다.
    파일이 없거나 주제가 없으면 0 이고 파일을 건드리지 않는다."""
    try:
        from store.theme_store import ThemeStore, owner_theme_id
        return 1 if ThemeStore(getattr(cfg, "data_dir", None)).delete_theme(
            owner_theme_id(owner)) else 0
    except Exception:
        return 0


def purge_owner(owner: str, *, cfg=None, store=None) -> dict:
    """``owner`` 소유의 **전 노드**를 부착 엣지와 함께 비가역 DETACH DELETE.

    계정 삭제 캐스케이드 전용(§5.9). 소유 스코프이므로 SEC-1/SEC-2 의 owner 격리가
    전제 — 소유가 안 갈리면 남의 노드가 지워질 수 있다. 라벨별 삭제 건수를 돌려줘
    호출자(계정)가 감사할 수 있게 한다. **owner 가 비었으면 거부**(빈 술어로 전삭제
    사고 방지). Domain 은 owner_id 가 없어 소유로 못 가르는 잔여 —
    [[vinc.spec.dataplane §5]] 의 알려진 한계(Bridge 는 v2, Chunk 는 v4 에서
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
        # owner 열이 없어 위 루프가 못 미치는 잔여 셋 — 3-way 기준(_SyncBase)·소유자별
        # 메타(_SyncMeta)·어휘 파일의 소유자 주제. 갭 분석 §6 의 "지웠다 뒤에 남는 것".
        deleted["_SyncBase"] = _purge_sync_base(store, owner)
        deleted["_SyncMeta"] = _purge_sync_meta(store, owner)
        deleted["theme"] = _purge_owner_theme(cfg, owner)
        base["executed"] = True
        base["deleted"] = deleted
        base["total"] = sum(deleted.values())
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


# ── 운영자 사용량 덤프의 그래프 쪽 절반(W1c) ─────────────────────────────────────
#: 소유자별로 세는 라벨과 응답 키. 앞의 넷은 `/domains` 가 세는 것과 같고 `nodes` 는
#: 그 넷의 합이다(운영자가 "노드 수"라고 부르는 값). Entity·Bridge 는 따로 싣는다.
_OWNER_STAT_LABELS = (("Concept", "concepts"), ("Record", "records"),
                      ("Episode", "episodes"), ("Decision", "decisions"),
                      ("Entity", "entities"), ("Bridge", "bridges"))
_OWNER_STAT_NODE_KEYS = ("concepts", "records", "episodes", "decisions")


def owner_stats(*, cfg=None, store=None) -> dict:
    """소유자 **전체**의 노드·문서 수 — `{owner: {nodes, concepts, …, docs}}`.

    ⚠ 이 모듈에서 owner 술어 없이 소유자를 가로지르는 조회는 이것뿐이다. 개수만
    돌려주고(제목·본문·도메인 없음) 부르는 곳도 하나다: 허브의 계정 서명 경로
    `/internal/usage`(`vinc_mcp.http_server.MCPHTTPApp.usage_request`). 웹 프록시
    허용목록(`_PROXY_OPS`)에 올리면 로그인한 누구나 남의 규모를 보게 된다.

    문서 수는 문서 목록과 같은 기준이다: 관례 문서(GRAPH.md)는 자료가 아니라 빼고 센다.
    owner 가 '' 인 행(소유 스탬프 이전의 레거시)도 그대로 싣는다. 버리면 합계가
    맞지 않고, 그 차이가 무엇인지 아무도 모르게 된다.
    근거: [[vinc.plan.backoffice-and-stats-2026-09-12]]"""
    cfg = cfg or load_config()
    base: dict = {"kind": "owner.stats", "executed": False}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base

    def slot(owner: str) -> dict:
        return owners.setdefault(owner, {**{k: 0 for _l, k in _OWNER_STAT_LABELS},
                                         "docs": 0})

    owners: dict[str, dict] = {}
    try:
        for label, key in _OWNER_STAT_LABELS:
            for row in store.run(f"MATCH (n:{label}) RETURN n.owner_id AS owner, "
                                 "count(*) AS c") or []:
                slot(row.get("owner") or "")[key] += int(row.get("c") or 0)
        for row in store.run("MATCH (d:_DocMeta) RETURN d.owner_id AS owner, "
                             "d.doc_id AS doc_id") or []:
            if not graphmd.is_conventions_doc_id(row.get("doc_id") or ""):
                slot(row.get("owner") or "")["docs"] += 1
        for s in owners.values():
            s["nodes"] = sum(s[k] for k in _OWNER_STAT_NODE_KEYS)
        base["executed"] = True
        base["owners"] = owners
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base
