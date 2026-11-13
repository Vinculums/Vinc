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
from store.embedded_schema import wiki_pairs_open
from store.envflag import truthy
from engines import domain_hierarchy

from . import graphmd
from vinc_rag import excerpt as _excerpt
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
    domains as _domain_rows,
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

# 나뉜 모듈들이 같이 읽는 바닥(스키마에서 온 관계·seed 표, 정확한 노드 읽기, 주제 수,
# 작은 보조 함수)은 query_base 에 있다. 같은 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는
# 그대로다.
from .query_base import (  # noqa: E402,F401  (재수출)
    BASIS_CONTENT,
    BASIS_RECORDED,
    SYNC_EDGE_RELS,
    WIKI_EDGE_RELS,
    _CC_RELS,
    _CONCEPT_READ_PATTERN,
    _DIRECTED_RELS,
    _DOC_CONCEPT_PREFIX,
    _FRAGMENT_EDGE_TO_REL,
    _GRAPH_READ_PAIRS,
    _OWNER_SCOPED_LABELS,
    _REVERSED_RELS,
    _SEED_TABLES,
    _SEMANTIC_LABELS,
    _SEMANTIC_PK,
    _SYNCED_EDGE_RELS,
    _WIKI_READ_PAIRS,
    _bridge_owner_pred,
    _derive_sync_edge_rels,
    _edge_cost,
    _graph_read_pairs,
    _seed_table,
    _stamp_validity,
    _topic_description_mirrors_by_domain,
    _track_cols,
    _wiki_read_pairs,
    domains,
    near_nodes,
    node,
    node_plan,
    normalize_domains,
)

# 지도 배치 기하(순수 함수)는 graph_layout 에 있다. 같은 이름을 여기서 다시 내보내므로
# 호출부와 `q.X` 는 그대로다.
from .graph_layout import (  # noqa: E402,F401  (재수출)
    LAYOUTS,
    _Branch,
    _CONE0,
    _CONE_MAX,
    _CONE_MIN,
    _LAYOUT_ALIAS,
    _R_BRANCH,
    _R_LEAF,
    _arc_gap,
    _build_tree,
    _cluster,
    _common_prefix,
    _dedupe_planar,
    _domain_order,
    _domain_path,
    _global_hub,
    _global_rank,
    _mean_angle,
    _ordered_children,
    _shared_prefix,
    _shell,
    layout_positions,
)

# 그래프 쓰기(개명·삭제·병합·소유자 삭제)와 소유자별 수는 graph_edit 에 있다. 같은 이름을
# 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .graph_edit import (  # noqa: E402,F401  (재수출)
    _NODE_PK,
    _OWNER_STAT_LABELS,
    _OWNER_STAT_NODE_KEYS,
    _delete_preview_edges,
    _drop_node_shadows,
    _purge_labels,
    _purge_owner_theme,
    _purge_sync_base,
    _purge_sync_meta,
    _write_edge_tombstone,
    _write_tombstone,
    delete_edge,
    delete_node,
    merge_nodes,
    owner_stats,
    purge_owner,
    rename_domain,
    sync_edge_spec,
)

# 지도 순위(시드 편향 ppr, 최근성, 상금 선택)는 graph_rank 에 있다. 같은 이름을 여기서 다시
# 내보내므로 호출부와 `q.X` 는 그대로다.
from .graph_rank import (  # noqa: E402,F401  (재수출)
    PPR_DEFAULT_WEIGHT,
    PPR_EDGE_WEIGHTS,
    PPR_RECENCY_HALF_LIFE_DAYS,
    PPR_RECENCY_WEIGHT,
    PRIZE_REACH_CAP,
    _node_date,
    _prize_pick,
    attach_ppr,
    concept_edge_ids_plan,
    ppr_recent,
    ppr_scores,
    prize_subgraph,
)

# 지도 읽기(L1·L2 그래프, 미리보기, 결합, L0 수, 원장 계획)는 graph_read 에 있다. 같은 이름을
# 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .graph_read import (  # noqa: E402,F401  (재수출)
    _GRAPH_AXIS_PLANS,
    _GRAPH_READ_PLANS,
    _SEED_FIELDS,
    _apply_basis,
    _derive_bridge_domains,
    _ledger_ego_ids,
    _mark_topic_descriptions,
    _order_graph_result,
    bridge_coupling_plan,
    bridge_rows_plan,
    bridge_ties_plan,
    concept_domains_plan,
    coupling,
    decision_relation_rows_plan,
    decision_rows_plan,
    decision_seed_rows_plan,
    direct_coupling_plan,
    domain_ties_plan,
    domains_plan,
    entity_rows_for_ids_plan,
    entity_subgraph_ids_plan,
    episode_rows_plan,
    graph,
    graph_plan,
    graph_preview,
    graph_rows_for_ids_plan,
    ledger_link_plan,
    ledger_origin_plan,
    record_rows_plan,
    seed_lookup_plan,
    shape_graph,
    subgraph_hops_plan,
    subgraph_ids_plan,
    view_l0,
)

# 한 홉 관측(neighbors·부착 축·대체 축·도메인 좌표)과 저장된 엣지 읽기(node_edges)는 neighbors 에 있다.
# 같은 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .neighbors import (  # noqa: E402,F401  (재수출)
    _ATTACH_AXIS,
    _attach_reverse_cypher,
    _domain_neighbors,
    _typed_neighbors_cypher,
    entity_neighbors_plan,
    neighbors,
    neighbors_plan,
    node_attachments_plan,
    node_edges,
    record_supersedes_plan,
)

# 두 개념 사이 최단 경로(홉·confidence 가중)와 끝점 거절은 paths 에 있다. 같은 이름을
# 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .paths import (  # noqa: E402,F401  (재수출)
    PATH_LIMIT,
    PATH_TIE_EPS,
    WEIGHT_CONFIDENCE,
    WEIGHT_HOPS,
    _NON_CONCEPT_PREFIXES,
    _concept_only_refusal,
    _node_seq,
    _probe_node,
    _same_endpoint_path,
    _unknown_node_refusal,
    _weighted_shortest_path,
    path_candidates_plan,
    shortest_path,
    shortest_path_plan,
    weighted_paths,
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
            # 축별 최댓값으로 모은다(검색 코어의 제목 팔과 같은 규칙, 동점 가드가 읽는다).
            best = (max(best[0], int(hit.get("hit_tokens") or 0)),
                    max(best[1], int(hit.get("title_tokens") or 0)))
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
           query_runtime: str | None = None, query_model: str | None = None,
           query_colbert: str | None = None, kinds=None,
           cfg=None, store=None) -> dict:
    """검색 순위와 페이지는 그대로 두고 공개 응답에 정확한 인용 id를 붙인다.

    as_of 기간이 있으면 그 기간이 끝난 뒤에 시작한 사건(Episode)을 node_hits 에서 뺀다
    (:func:`_drop_unstarted_episodes`). 다른 종류의 구간 검사는 검색 코어가 이미 한다."""
    result = _retrieval.search(
        query, query_embedding=query_embedding, top_k=top_k, filters=filters,
        owner=owner, cursor=cursor, detail=detail, historical=historical,
        as_of=as_of, mode=mode, known_at=known_at, query_runtime=query_runtime,
        query_model=query_model, query_colbert=query_colbert, cfg=cfg, store=store,
        **({"kinds": kinds} if kinds else {}))
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
      경로 수가 폭증한다(3노드 사이클서 *1..8 시 510경로 실측). 그래서 엣지를 다시 쓰지
      않는 순회여야 한다 → 동일 셋업 6경로. 질의는 GQL 수량 관계 `-[]-{1,d}`(기본이 엣지
      무재사용)로 쓰고 임베디드 어댑터가 ladybug 의 `* TRAIL` 로 보낸다
      (`store.embedded_backend.to_engine_cypher`)."""
    depth = max(1, min(int(depth), 4))
    # PK 네임스페이싱 — `$topic` 은 **id 열과 title 열에 동시에** 쓰인다. 하나로
    # 합성하면 제목 매치가 죽는다(제목은 논리 텍스트다). 그래서 id 열용 물리 키를
    # `$tkey` 로 따로 싣는다. 근거: [[vinc.plan.node-pk-namespacing]] §7
    params = {"topic": topic, "tkey": pkns.key(owner, topic)}
    cypher = (
        "MATCH (n) WHERE n.concept_id = $tkey OR n.bridge_id = $tkey "
        "OR n.title = $topic "
        f"MATCH path = (n)-[]-{{1,{depth}}}(m) "
        "RETURN n, relationships(path) AS rels, m "
        "LIMIT 100"
    )
    return QueryPlan("decide", cypher, params, note="policy/concept relation traversal")


# ── Executor ─────────────────────────────────────────────────────────────────
# 드라이버 취득은 store.backend.open_store 로 일원화(설계 §4-1). 여기서는 백엔드를
# 열고 plan.cypher 를 store.run 으로 실행할 뿐 — 드라이버를 직접 열지 않는다.

# ── Public entry points ──────────────────────────────────────────────────────

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
    (감사 F11). Entity 는 열이 없어 {} 다.

    ``concept:doc.*`` 미러의 유효구간은 Concept 열이 아니라 그 문서(`_DocMeta`)의
    것이다(감사 F13). Concept 에는 구간 열이 없어서, 은퇴한 문서의 미러가 current 로
    읽혔다. :func:`node_plan` 이 같은 자리를 이미 그렇게 읽는다.

    Entity(개방 타입)는 제목·태그와 TYPED 관계만 낸다(감사 F12, node_kind="entity").
    발췌 FTS 폴백이 실패하면 ``excerpt_error`` 에 사유를 싣는다(조용히 삼키지 않는다).

    ``rev``/``updated_at`` 은 그 노드의 현재 버전이다(W3). 고쳐 쓰기 전에 무엇을 읽었는지
    붙들어 두라고 낸다. 쓰기가 이 값을 ``expected_rev`` 로 실으면 어긋날 때 거절된다(선택).
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
                # 구간 열이 없는 v22 이전 스토어에서는 이 조회만 실패하고 개념 규칙으로
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
                # `IN $ids` 는 BM25 순위를 지키지 않는다 — 되돌리지 않으면 같은 노드의 발췌
                # 순서가 호출마다(REST 와 MCP 사이에서도) 달라진다.
                ment = _excerpt.by_rank(ment, ids)
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
            # 노드 토큰이 모인 자리의 240자(`VINC_EXCERPT_WINDOW=head` 면 맨 앞 240자).
            "excerpt": _excerpt.excerpt(r.get("text") or "", base["title"],
                                        base.get("one_liner") or ""),
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
    # 주제 설명의 미러는 그 주제의 노드가 아니다(설명은 아래 `description` 이 따로 싣는다).
    # 목록과 수에서 같이 빼야 열과 엣지의 대조(membership_drift)가 어긋나지 않는다
    mirrors = _topic_description_mirrors_by_domain(owner, cfg, store) or {}
    rows = [r for r in ring["neighbors"] if not (r["rel"] == "IN_DOMAIN" and r["id"] in mirrors)]
    children = [r for r in rows if r["rel"] == "PARENT_OF"]
    members = [r for r in rows if r["rel"] == "IN_DOMAIN"]
    counts = store.run(domain_hierarchy.scoped_counts_cypher(),
                       {"owner": owner, "domain": domain, "prefix": domain + "/"}) or []
    direct = sum(int(r.get("total") or 0) for r in counts if r.get("domain") == domain) \
        - sum(1 for d in mirrors.values() if d == domain)
    subtree = sum(int(r.get("total") or 0) for r in counts) \
        - sum(1 for d in mirrors.values() if d == domain or d.startswith(domain + "/"))
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
    ([[record:attaches-to-carries-evidence-since-v16]]).
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
    # SUPERSEDES 표가 없는 이전 판 스토어에서 이 팔이 Binder 로 죽어도 부착은 남아야 한다
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
                    f"type(r) AS type, '{'out' if outgoing else 'in'}' AS dir, "
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
    # 근거: [[episode:2026-09-14-brief-carries-the-connectivity-instead-of-leaving-it-to-be-summed]]
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
#: 잘린다**(노드 팔의 top_k 가 같은 값이다). 이 풀은 되묻기 목록에만 쓴다. 확정은 그 앞
#: `_BRIEF_RESOLVE_N` 개(융합 순서)로 해서, 풀을 넓혀도 어느 노드로 확정하는지는 5 일 때의
#: 규칙 그대로다. 풀을 넓히고 확정까지 넓은 풀로 하면 맞는 확정과 보이는 정답이 함께 줄었다.
#: 근거: [[vinc.spec.retrieval-knobs]] §2
_BRIEF_TOP_K = 10
_BRIEF_RESOLVE_N = 5

#: 학습 해소 규칙 — ColBERT 1위를 확정할지 정하는 로지스틱 모형. 상수는 v2 가 개발 풀
#: 3,140행으로 맞춰 고정한 그대로다(`frozen_v2.json`, sha256 bb417ece…). 특징 순서·정의를 바꾸면
#: 상수가 뜻을 잃는다(:func:`_brief_learned_features`). 문턱 아래는 되묻는다.
#: v2 는 `VINC_BRIEF_RULE=learned` 일 때만 돈다(기본은 production 규칙).
#: 근거: [[vinc.canon.rag-production-state]]
_LEARNED_MU = [0.4828262648622321, 0.023761766309001644, 0.26719745222929936, 4.985350318471338, 0.21818054291780406,
               0.7420382165605095, 0.39713375796178346, 0.038502569108280225, 0.49879280364817113, 0.3436305732484076]
_LEARNED_SD = [0.056948800738280285, 0.02434501544247338, 0.4424962990087704, 4.9540347754103236, 0.304360684759962,
               1.303480270826195, 2.7100763980136233, 0.05477666510201067, 0.15564735806801316, 0.47491957564119053]
_LEARNED_W = [0.653670720249066, 1.0327584901774332, -0.18126837306254473, -0.3745033858099314, 0.3405076195044011,
              -0.6251645103744707, 0.10649465847294845, 0.8194124383935194, -0.6231877383599321, 0.10145310307050287,
              -0.42554129869112434]
_LEARNED_T = 0.4855
#: v3 학습 규칙 — v2 의 열 특징에 유효구간 필터가 감춘 행에서 나온 넷을 더한 열넷. brief 경로 전체
#: (사전 순서 기권 → ColBERT 순서 위의 규칙, 토큰 동점 가드 없음)에서 맞춰 고정한 그대로다
#: (`frozen_v3.json`, sha256 c9a3fb2b…). 특징 순서·정의를 바꾸면 상수가 뜻을 잃는다
#: (:func:`_brief_v3_features`). 문턱 아래는 되묻는다.
#: 근거: [[decision:ship-brief-rule-v3-on-the-owners-reading-of-the-wrong-picks-2026-11-03]]
_V3_MU = [0.4890195805011388, 0.02600471254428717, 0.2799710249909453, 4.690329590727997, 0.22764569427916043,
          0.7576964867801521, 0.4469395146685983, 0.04225958312205718, 0.4878007591914889, 0.32560666425208257,
          1.932633103947845, 1.4154291923216227, 0.3487029348902844, 0.9974646867077146]
_V3_SD = [0.05659572299459152, 0.02600220280012217, 0.44898468910915157, 4.776808934344869, 0.30744768971516356,
          1.2894831272731022, 2.7083567306240948, 0.05636093631911328, 0.15418905886504955, 0.468601072751569,
          2.5222996592202773, 1.6853656409255495, 0.4304271895055913, 0.050288026202780646]
_V3_W = [0.4862374491844391, 1.062161570947516, -0.12316389848275623, -0.37911250114801065, 0.25814693177986686,
         -0.38898580913481623, 0.027189152410250123, 0.8091480763571104, -0.5459152296028742, 0.2354164923841601,
         -0.1228823968080043, 0.35268237679848913, 0.09248826361012848, 0.1539361828003368, -0.02308750100000047]
_V3_T = 0.5397
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
#: "없다"고 답하지 않는다. 근거: 감사 F12.
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
    검사는 스토어 없이 돈다. 그 질문은 임베딩을 한 번 더 구할 뿐 답은 같다."""
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
    # 알림은 증거 유효성(claim_validity)에서 낸다(감사 F13). 기존 validity 필드는 구간이
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
    근거: [[record:the-confidence-bar-was-one-token-and-that-was-the-defect-2026-09-10]]"""
    return str(os.environ.get("VINC_BRIEF_STRICT", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 확신에 필요한 토큰 차. 옛 값 1 은 **토큰 하나 차이면 확신한다**는 뜻이라 사실상
#: 문턱이 없었다. 이 값을 내리기 전에 :func:`_brief_strict_on` 의 수치를 볼 것.
_STRICT_MARGIN = 2


def _brief_sem_promote_on() -> bool:
    """되묻기 1위를 의미 우위로 확정할 것인가. **기본은 켜짐**이고
    `VINC_BRIEF_SEM_PROMOTE=off` 가 토큰 규칙만 쓰던 동작으로 되돌린다(측정용 비상구).
    끄면 brief 의 맞는 확정이 절반 아래로 줄고, 틀린 노드 확정은 줄지 않는다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    return str(os.environ.get("VINC_BRIEF_SEM_PROMOTE", "")).strip().lower() not in (
        "0", "off", "false", "no")


#: 승격에 필요한 의미 우위: 의미 팔 원시 목록에서 1위가 나머지보다 가까운 거리(`sem_lead`,
#: search 가 단다). 유효구간 동점 가드가 지운 행과 견줄 때도 같은 값을 쓴다. `_SEM_GAP`
#: (0.010, 순서만 바꾼다)과 다른 값이다. 내리면 틀린 노드 확정이 늘어난다. 옮기려면 다시 잴 것.
#: 0.12 에서 0.10 으로 옮긴 것은 봉인 셋 7을 운영 search 출력 재현으로 채점한 결과다(2026-10-24).
#: 근거: [[vinc.spec.retrieval-knobs]] §2
_SEM_PROMOTE = 0.10

#: 한글 없는 질문(어느 노드든), 또는 한글 있는 질문 + 한글 없는 노드의 승격 문턱. 언어를 건너면 거리가
#: 넓어져(정답 거리 중앙값 en2ko 0.419, ko 0.328) 같은 1위인데도 우위가 `_SEM_PROMOTE` 밑에 머문다.
#: 영어 질문은 영어 노드에도 이 문턱을 쓴다: 1위가 정답인데 우위가 0.06~0.10 에 머문 행이 많았다.
#: 한글 질문 + 한국어 노드는 `_SEM_PROMOTE` 그대로다. 0.06 으로 내리면 맞는 확정은 늘지만 그래프에 없는
#: 사실을 묻는 질문의 확정이 등록 한도보다 늘어 채택하지 않았다.
#: 근거: [[vinc.plan.brief-korean-on-korean-bar-sealed13-2026-10-26]]
#: `_brief_xlang_lead` 가 쓰는 조건에서만 `_SEM_PROMOTE` 자리에 들어가고, 유효구간 동점 가드의 거리는
#: 그대로 `_SEM_PROMOTE` 다. 내리면 틀린 노드 확정이 는다. 옮기려면 다시 잴 것.
#: 근거: [[vinc.spec.retrieval-knobs-brief]]
_SEM_PROMOTE_XLANG = 0.06

_HANGUL = re.compile("[가-힣]")


def retrieval_overrides() -> list[str]:
    """측정상 크게 나빠지는 비상구 가운데 지금 열린 것(환경변수 이름). 없으면 빈 목록.

    `VINC_BRIEF_STRICT`·`VINC_NODE_SEM_RANK`·`VINC_NODE_SEMANTIC_ARM` 은 둘 다 켜야 오르는
    한 쌍이 셋으로 묶인 것이라, 하나라도 끄면 1위 정답과 오답 확정이 함께 무너진다.
    `VINC_BRIEF_SEM_PROMOTE` 를 끄면 brief 의 맞는 확정이 절반 아래로 준다.
    `/health` 가 이 목록을 실어 꺼진 채 배포된 것이 밖에서 보이게 한다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    out = []
    if not _brief_strict_on():
        out.append("VINC_BRIEF_STRICT")
    if not _brief_sem_promote_on():
        out.append("VINC_BRIEF_SEM_PROMOTE")
    if not _retrieval._node_sem_rank_on():
        out.append("VINC_NODE_SEM_RANK")
    if not _retrieval._node_semantic_arm_on():
        out.append("VINC_NODE_SEMANTIC_ARM")
    return out


def retrieval_switches() -> dict:
    """노드 재채점과 brief 규칙의 **실제** 값. `/health` 가 늘 싣는다 — 운영 값은 저장소 변수에만
    있어서 밖에서 확인할 길이 없었다. ColBERT 는 설정이 on 이어도 numpy 가 없으면 꺼지므로
    환경변수가 아니라 :func:`vinc_rag.colbert.rescore_on` 을 읽는다.
    근거: [[vinc.canon.rag-production-state]]"""
    from vinc_rag import colbert as _colbert
    return {"VINC_NODE_COLBERT": "on" if _colbert.rescore_on() else "off",
            "VINC_BRIEF_RULE": _brief_rule()}


def _brief_token_decisive(top: dict, second: dict) -> bool:
    """1위가 2위를 토큰으로 갈랐는가 — 엄격 규칙이면 `_STRICT_MARGIN`, 아니면 1."""
    margin = _STRICT_MARGIN if _brief_strict_on() else 1   # 1 이면 현행과 글자 그대로 같다
    return _brief_int(top.get("hit_tokens")) > 0 and (
        _brief_int(top.get("hit_tokens")) - _brief_int(second.get("hit_tokens")) >= margin
        or _brief_int(top.get("title_tokens"))
        - _brief_int(second.get("title_tokens")) >= margin)


def _brief_hidden_ties(query: str, top: dict, hidden) -> bool:
    """유효구간 필터가 감춘 행이 남았다면 1위의 토큰 확정이 서지 않았는가.

    비교는 **확정에 쓴 규칙 그대로**다. `hidden` 은 감춘 행들의 축별 최댓값
    `(hit_tokens, title_tokens)` 이라, 거기에 이기면 감춘 모든 행에 이긴다.
    - 이름을 댄 확정(제목이 질문과 같음)은 같은 제목이 둘이면 되묻는다. 감춘 행이 두 축
      모두 1위 이상이면 같은 제목일 수 있으므로 비긴 것으로 본다.
    - 그 밖의 확정은 `_brief_token_decisive` 다. 튜플 하나로 사전순 비교하면 hit 가 하나 높은
      행이 제목으로 크게 앞선 확정을 되돌리고, 제목이 높은 행은 놓친다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    h_hit = _brief_int(hidden[0]) if len(hidden) > 0 else 0
    h_title = _brief_int(hidden[1]) if len(hidden) > 1 else 0
    if _brief_norm(top.get("title")) == _brief_norm(query):
        return (h_hit >= _brief_int(top.get("hit_tokens"))
                and h_title >= _brief_int(top.get("title_tokens")))
    return not _brief_token_decisive(top, {"hit_tokens": h_hit, "title_tokens": h_title})


def _brief_semantic_tie_clear(picked: list[dict], res: dict) -> bool:
    """의미 우위 승격으로 확정된 1위가 유효구간 동점 가드를 통과하는가.

    가드는 지운 행과 1위를 **확정에 쓴 잣대로** 견준다. 토큰으로 가른 확정은 토큰 점수로
    견주고(이 함수는 False), 승격(토큰으로는 못 가르고 의미 우위로 확정)은 의미 거리로
    견준다: 의미 팔이 지운 행 중 가장 가까운 것이 1위보다 `_SEM_PROMOTE` 안쪽이면, 그 행이
    남았을 때 우위가 없었으므로 확정하지 않는다. 의미 팔이 아무것도 지우지 않았으면 우위는
    필터가 만든 것이 아니다. 엄격 규칙이 꺼져 있으면 순서가 토큰으로 정해지므로 토큰 비교를 쓴다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    if len(picked) < 2 or not _brief_strict_on() or _brief_token_decisive(picked[0], picked[1]):
        return False
    top = picked[0]
    try:
        top_d = float(top.get("distance"))
    except (TypeError, ValueError):
        return False
    dropped = res.get("validity_best_dropped_distance")
    if dropped is None:
        return True
    try:
        return float(dropped) - top_d >= _SEM_PROMOTE
    except (TypeError, ValueError):
        return False


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


def _brief_rule() -> str:
    """`VINC_BRIEF_RULE` 의 값. `learned`(v2)·`v3` 만 학습 규칙이고, 그 밖의 값과 빈 값은 production 이다."""
    v = (os.environ.get("VINC_BRIEF_RULE") or "production").strip().lower()
    return v if v in ("learned", "v3") else "production"


def _brief_rule_learned() -> bool:
    """`VINC_BRIEF_RULE=learned` — ColBERT 순서 위의 v2 학습 규칙. 기본은 production(기존 규칙)."""
    return _brief_rule() == "learned"


def _brief_rule_v3() -> bool:
    """`VINC_BRIEF_RULE=v3` — brief 경로 전체로 다시 맞춘 v3 학습 규칙(:func:`_brief_resolve_v3`)."""
    return _brief_rule() == "v3"


def _brief_rule_colbert() -> bool:
    """학습 규칙(v2·v3)이면 ColBERT 로 다시 선 후보 위에서 해소한다. 기존 규칙은 그 순서를 안 본다."""
    return _brief_rule() != "production"


def _brief_learned_features(query: str, hits: list[dict]) -> list[float] | None:
    """연구 `stage3.py` `feats` 와 같은 열 개. hits 는 ColBERT 순서이고 행마다 `colbert`·`prod_rank` 가 있다."""
    if not hits or "colbert" not in hits[0]:
        return None
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0  # noqa: E731
    h1, h2 = hits[0], (hits[1] if len(hits) > 1 else {})
    s1, s2 = num(h1.get("colbert")), (num(h2.get("colbert")) if h2 else 0.0)
    rank = int(h1.get("prod_rank") or 21)
    return [s1, s1 - s2, float(rank == 1), float(rank),
            num(h1.get("hit_tokens")) / max(num(h1.get("token_total")), 1.0),
            num(h1.get("title_tokens")), num(h1.get("hit_tokens")) - num(h2.get("hit_tokens")),
            num(h1.get("sem_lead")), num(h1.get("distance")) if h1.get("distance") is not None else 1.0,
            float(bool(_HANGUL.search(query or "")))]


def _brief_learned_p(x: list[float]) -> float:
    z = sum(((xi - m) / sd) * w for xi, m, sd, w in zip(x, _LEARNED_MU, _LEARNED_SD, _LEARNED_W)) + _LEARNED_W[-1]
    return 1.0 / (1.0 + math.exp(-z))


def _brief_resolve_learned(query: str, hits: list[dict]) -> tuple[str, list[dict], float | None]:
    """학습 규칙으로 해소한다. 제목이 질문과 글자 그대로 같은 후보가 **하나**면 그것으로 확정하고(이름을
    댄 경우), 아니면 ColBERT 1위의 확률이 문턱 이상일 때만 확정한다. 나머지는 되묻는다.
    연구의 `decide` 와 같다(기존 규칙의 다른 갈래는 여기 없다 — 그 뒤의 가드는 brief 가 그대로 돈다)."""
    norm = lambda t: re.sub(r"\s+", " ", (t or "").strip().lower())  # noqa: E731
    named = [h for h in hits if norm(h.get("title")) == norm(query)]
    if len(named) == 1:
        return "resolved", named, None
    x = _brief_learned_features(query, hits)
    if x is None:
        return "choose", list(hits), None
    p = _brief_learned_p(x)
    return ("resolved", [hits[0]], p) if p >= _LEARNED_T else ("choose", list(hits), p)


def _brief_v3_prod_head(hits: list[dict]) -> list[dict]:
    """재채점 **전** 순서(`prod_rank`)의 앞 `_BRIEF_RESOLVE_N` 개. v3 의 의미만 기권은 이 목록으로 본다.
    `prod_rank` 가 없는 행은 뒤로 간다(안정 정렬)."""
    return sorted(hits, key=lambda h: h.get("prod_rank") or 99)[:_BRIEF_RESOLVE_N]


def _brief_v3_features(query: str, hits: list[dict], res: dict) -> list[float] | None:
    """v3 특징 열넷. 앞 열은 :func:`_brief_learned_features` 그대로(ColBERT 순서의 앞 다섯), 뒤 넷은
    유효구간 필터가 감춘 행과 ColBERT 1위의 차이다:
    감춘 행 최대 hit 토큰 − 1위 hit 토큰 · 감춘 행 최대 제목 토큰 − 1위 제목 토큰 ·
    감춘 행 최소 의미 거리 − 1위 거리(어느 쪽이든 없으면 1.0) · 감춘 행이 있었는가."""
    a = _brief_learned_features(query, hits)
    if a is None:
        return None
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    pick = hits[0]
    hb = res.get("validity_best_dropped") or [0, 0]
    d, hd = num(pick.get("distance")), num(res.get("validity_best_dropped_distance"))
    return a + [_brief_int(hb[0] if hb else 0) - _brief_int(pick.get("hit_tokens")),
                _brief_int(hb[1] if len(hb) > 1 else 0) - _brief_int(pick.get("title_tokens")),
                (hd - d) if (d is not None and hd is not None) else 1.0,
                float(bool(res.get("validity_filtered")))]


def _brief_v3_p(x: list[float]) -> float:
    z = sum(((xi - m) / sd) * w for xi, m, sd, w in zip(x, _V3_MU, _V3_SD, _V3_W)) + _V3_W[-1]
    return 1.0 / (1.0 + math.exp(-z))


def _brief_resolve_v3(query: str, hits: list[dict], res: dict) -> tuple[str, list[dict], float | None]:
    """v3 규칙으로 해소한다. `hits` 는 ColBERT 순서의 앞 `_BRIEF_RESOLVE_N` 개, `res` 는 search 응답이다.
    의미만 기권(:func:`_brief_v3_prod_head`)은 brief 가 이 앞에서 끝냈다. 특징을 못 만들면 되묻는다.
    제목이 질문과 같은 후보가 **하나**면 그것으로, 아니면 ColBERT 1위의 확률이 문턱 이상일 때만 확정한다.
    토큰 동점 가드는 이 확정에 돌지 않는다 — 그 정보는 뒤 네 특징에 들어 있다."""
    x = _brief_v3_features(query, hits, res)
    if x is None:
        return "choose", list(hits), None
    norm = lambda t: " ".join((t or "").strip().lower().split())  # noqa: E731
    named = [h for h in hits if norm(h.get("title")) == norm(query)]
    p = _brief_v3_p(x)
    if len(named) == 1:
        return "resolved", named, p
    return ("resolved", [hits[0]], p) if p >= _V3_T else ("choose", list(hits), p)


def _brief_list_rank(rows: list[dict]) -> list[dict]:
    """되묻기 목록의 순서. 의미 팔이 단호했던 노드가 먼저고, 그다음은 의미 거리가 가까운 순이며,
    거리가 없는 후보(제목·그래프 팔만)는 뒤에 융합 순서대로 둔다. 확정에는 쓰지 않는다.
    근거: [[vinc.spec.retrieval-knobs]] §2"""
    strict = _brief_strict_on()

    def dist(h):
        d = h.get("distance")
        return d if isinstance(d, (int, float)) and not isinstance(d, bool) else float("inf")

    return sorted(rows, key=lambda h: (-1 if (strict and h.get("sem_decisive")) else 0, dist(h)))


def _brief_mirror_text(store, owner, hit: dict) -> str | None:
    """후보 한 노드의 검색 미러 텍스트(`NodeVec.text`). 행이 없으면 None."""
    from . import node_mirror
    rows = store.run("MATCH (n:NodeVec) WHERE n.vec_id = $v RETURN n.text AS t LIMIT 1",
                     {"v": node_mirror.vec_id(owner or "", str(hit.get("id") or ""))}) or []
    return (rows[0].get("t") or None) if rows else None


def _brief_xlang_lead(query: str, top: dict, mirror_text=None) -> bool:
    """`_SEM_PROMOTE_XLANG` 을 쓸 자리인가: 한글 없는 질문이거나(노드 언어와 상관없이),
    한글 있는 질문인데 승격될 1위의 미러 텍스트에 한글이 없다.

    `mirror_text(h)` 는 노드의 미러 텍스트(제목 + 부가 본문 앞 300자, 에피소드는 600자)를 주거나 None 이다. 없거나
    읽기가 실패하면 제목으로 갈음한다(미러 행이 없는 후보도 같은 길이다). 한글 없는 질문은 미러를 읽지 않는다."""
    asked_korean = bool(_HANGUL.search(query or ""))
    if not asked_korean:
        return True
    text = None
    if mirror_text is not None:
        try:
            text = mirror_text(top)
        except Exception:
            text = None
    return asked_korean != bool(_HANGUL.search(text or str(top.get("title") or "")))


def _brief_resolve(query: str, hits: list[dict],
                   mirror_text=None) -> tuple[str, list[dict]]:
    """node_hits → (상태, 고른 것). resolved 면 1건, choose 면 후보 여럿.

    ⚠ 그래프·의미 팔만 올라온 경우(hit_tokens 가 전부 0)는 확정하지 않는다 — 후보가
    **하나뿐이어도** 그렇다. 청크 인접이나 벡터 거리는 "관련 있다"는 뜻이지 "이것을
    물었다"는 뜻이 아니다. 이 갈래가 없던 시절 이 표면은 후보를 id 알파벳순으로 집어
    질문과 무관한 답을 냈다(ask.ts 2026-08-03 재설계).

    제목이 질문과 글자 그대로 같으면(이름을 댄 경우) 랭킹 앞에서 그 노드로 확정한다.
    같은 제목이 둘 이상이면 어느 쪽을 물었는지 모르므로 되묻는다(doc 미러 제목 충돌).
    근거: [[vinc.plan.rag-code-review-fixes-2026-09-29 §7]]

    **의미 우위 승격**(`VINC_BRIEF_SEM_PROMOTE`, 기본 켜짐)은 위 ⚠ 의 유일한 예외다.
    랭킹 갈래에서 choose 가 될 1위가 의미 팔 원시 목록의 나머지보다 `_SEM_PROMOTE` 이상
    가까우면 토큰이 0 이어도 확정한다. 이름 댄 갈래(같은 제목 둘 포함)와 단독 후보 갈래는
    그 앞에서 돌아오므로 승격되지 않는다. 단독 후보에는 앞설 나머지가 없어 `sem_lead` 도
    안 붙는다. 유효구간 동점 가드와 답변 가능성 게이트는 brief 에서 이 뒤에 그대로 돈다.
    한글 없는 질문이거나 질문과 1위 노드의 언어가 다르면(한글 있는 질문 + 미러 텍스트에 한글 없음) 문턱은
    `_SEM_PROMOTE_XLANG` 이다.

    **엄격 판정**(`VINC_BRIEF_STRICT`, 기본 켜짐)은 순서에 `arms` 를 먼저 넣고 문턱을
    `_STRICT_MARGIN` 으로 올린다. 여러 팔이 같은 노드를 올렸다는 것은 어휘와 의미가
    함께 가리켰다는 뜻이라, 토큰 하나를 더 맞힌 것보다 강한 증거다.
    ⚠ **융합 점수(RRF)로 정렬하는 안은 기각됐다.** 팔을 켜면 좋아 보이지만 끄면 나빠진다
    (오답확정 37 → 44) — 팔이 둘뿐일 때 RRF 순위는 토큰 순서보다 못하다. 판정은 팔의
    기본값과 무관하게 모두에게 적용되므로 팔에 의존하는 규칙은 쓸 수 없다.
    근거: [[record:the-confidence-bar-was-one-token-and-that-was-the-defect-2026-09-10]]"""
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
    decisive = _brief_token_decisive(top, second)
    # 의미 우위 승격(플래그 뒤): 되묻기가 될 자리에서만 돈다. 1위가 의미 팔의 1위가
    # 아니면 `sem_lead` 가 없다(그 노드보다 가까운 것이 있으니 우위가 음수다).
    lead = top.get("sem_lead")
    promoted = (not decisive and _brief_sem_promote_on()
                and isinstance(lead, (int, float)) and lead >= _SEM_PROMOTE)
    # 질문과 1위 노드의 언어가 다르면 더 낮은 문턱을 쓴다. 미러 텍스트는 이 문턱에 걸릴
    # 때만 읽는다(`mirror_text` 는 호출부가 스토어를 쥐고 있을 때만 주는 지연 조회다).
    if (not decisive and not promoted and _brief_sem_promote_on()
            and isinstance(lead, (int, float)) and lead >= _SEM_PROMOTE_XLANG
            and _brief_xlang_lead(query, top, mirror_text)):
        promoted = True
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


def _brief_section_on() -> bool:
    """질문으로 문서 미러에 닿은 brief 에 `section` 을 실을 것인가(`VINC_BRIEF_SECTION`).
    기본은 켜짐이고 ``off``·``0``·``false``·``no`` 가 끈다(끄면 이 키가 없던 때와 같다).
    근거: [[vinc.research.answer-material-section-ai-corpus-2026-10-17]]"""
    v = str(os.environ.get("VINC_BRIEF_SECTION", "")).strip().lower()
    return v not in ("off", "0", "false", "no")


def _brief_pick_chunk(store, chunks: list[dict], *, query: str, query_embedding,
                      top_id: str = "") -> tuple[int, str]:
    """한 문서(또는 한 노드)의 청크 가운데 질문이 가리키는 것의 자리와 근거.

    질문 벡터에 가장 가까운 조각을 가진 청크(`vector`), 벡터가 없으면 검색 1위 청크(`rank`),
    그것도 아니면 질문 토큰이 가장 여러 가지 든 청크(`lexical`)다. `chunks` 는 {cid, text} 를
    순서대로 준 것이고 비어 있으면 안 된다."""
    ids = [str(c["cid"]) for c in chunks]
    if query_embedding is not None:
        dist = store.chunk_vector_distances(ids, query_embedding)
        if dist:
            return (min((i for i, cid in enumerate(ids) if cid in dist),
                        key=lambda i: (dist[ids[i]], i)), "vector")
    if top_id and top_id in ids:
        return ids.index(top_id), "rank"
    from store.lexical import find_tokens, lex_tokens
    toks = lex_tokens(query)
    return (max(range(len(chunks)), key=lambda i: (
        len({t for _, t in find_tokens(str(chunks[i].get("text") or ""), toks)}), -i)), "lexical")


def _brief_section(store, *, owner: str, query: str, rows: list[dict],
                   query_embedding, nodes: list[str]) -> dict | None:
    """질문 검색 1위 문서에서 **질문이 가리키는 절**. 그 문서의 미러가 ``nodes``(확정 노드나
    노출 후보)에 있을 때만 낸다 — 아니면 답과 무관한 문서의 본문이 실린다.

    노드 발췌는 노드를 뒷받침하는 창이라 문서 미러에서는 문서 제목이 나오는 자리를 보여 준다.
    이 절은 질문에 답하는 자리다. 가리키는 청크는 그 문서 청크 가운데 질문 벡터와 가장 가까운
    조각을 가진 것이고(`basis: vector`), 벡터가 없으면 검색 1위 청크(`rank`), 그것도 그 문서가
    아니면 질문 토큰이 가장 여러 가지 든 청크다(`lexical`). 절의 범위는 `section_span`.
    읽기 실패는 None 이다 — 이 키는 증강이라 답을 막지 않는다."""
    top = next((r for r in rows if isinstance(r, dict) and r.get("doc_id")), None)
    if top is None:
        return None
    doc_id = str(top["doc_id"])
    mirror = _DOC_CONCEPT_PREFIX + doc_id
    if mirror not in nodes:
        return None
    try:
        chunks = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d AND (c.owner_id = $o OR c.owner_id = '') "
            "RETURN c.chunk_id AS cid, c.section_title AS sec, c.text AS text "
            "ORDER BY c.chunk_index", {"d": doc_id, "o": owner}) or []
        if not chunks:
            return None
        ids = [str(c["cid"]) for c in chunks]
        at, basis = _brief_pick_chunk(store, chunks, query=query, query_embedding=query_embedding,
                                      top_id=str(top.get("id") or ""))
        lo, hi, text, cut = _excerpt.section_span(chunks, at)
    except Exception:
        return None
    return {"node_id": mirror, "cite_id": mirror, "doc_id": doc_id,
            "section": str(chunks[at].get("sec") or ""), "chunk_ids": ids[lo:hi + 1],
            "text": text, "truncated": cut, "basis": basis}


def _brief_attach_section(base: dict, store, *, owner: str, query: str, res: dict,
                          query_embedding, nodes: list[str]) -> None:
    """`section` 을 싣고 그 미러를 `grounds` 에 더한다(플래그가 꺼져 있으면 아무것도 안 한다)."""
    if not _brief_section_on():
        return
    sec = _brief_section(store, owner=owner, query=query, rows=res.get("rows") or [],
                         query_embedding=query_embedding, nodes=nodes)
    if sec is None:
        return
    base["section"] = sec
    for key in ("grounds", "cite_grounds"):
        if key in base and sec["node_id"] not in base[key]:
            base[key] = sorted(set(base[key]) | {sec["node_id"]})


def _brief_quotes_on() -> bool:
    """후보와 확정 노드에 `quotes`(글자 그대로의 인용 단위)를 실을 것인가(`VINC_BRIEF_QUOTES`).
    기본은 켜짐이고 ``off``·``0``·``false``·``no`` 가 끈다(끄면 이 키가 없던 때와 같다).
    근거: [[vinc.manual.rag]]"""
    v = str(os.environ.get("VINC_BRIEF_QUOTES", "")).strip().lower()
    return v not in ("off", "0", "false", "no")


#: 노드마다 싣는 인용 단위 수와, 개념의 MENTIONS 청크에서 고를 후보 수.
_BRIEF_QUOTES_N = 2
_BRIEF_QUOTE_POOL = 6


def _brief_node_quotes(store, *, owner: str, node_id: str, query: str,
                       query_embedding) -> list[dict]:
    """노드를 받치는 정본 문서에서 질문이 가리키는 인용 단위. 문서 미러는 그 문서의 청크에서,
    개념은 MENTIONS 로 닿는 청크(링크 방법이 강한 순)에서 질문 벡터에 가장 가까운 청크 하나를
    골라 그 안에서 단위를 낸다(`vinc_rag.excerpt.quote_units`).

    단위마다 `doc_id`·`source`(문서 제목)·`section`(절 제목)을 함께 싣는다 — 인용문이 어느
    문서의 어느 절에서 왔는지를 주장이 인용문 밖에서 끌어오지 않게 하려는 것이다. 다른 종류의
    노드나 읽기 실패는 빈 목록이다. 이 키는 증강이라 답을 막지 않는다."""
    try:
        if node_id.startswith(_DOC_CONCEPT_PREFIX):
            chunks = store.run(
                "MATCH (c:Chunk) WHERE c.doc_id = $d AND (c.owner_id = $o OR c.owner_id = '') "
                "RETURN c.chunk_id AS cid, c.doc_id AS doc_id, c.section_title AS sec, "
                "c.text AS text ORDER BY c.chunk_index",
                {"d": node_id[len(_DOC_CONCEPT_PREFIX):], "o": owner}) or []
        elif node_id.startswith("concept:"):
            label, pk = _seed_table(node_id)
            if label != "Concept":
                return []
            chunks = store.run(
                f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{label}) "
                f"WHERE c.{pk} = $id AND c.owner_id = $o "
                "AND (ch.owner_id = $o OR ch.owner_id = '') "
                "RETURN ch.chunk_id AS cid, ch.doc_id AS doc_id, ch.section_title AS sec, "
                "ch.text AS text "
                "ORDER BY CASE m.method WHEN 'title' THEN 0 WHEN 'stem' THEN 1 ELSE 2 END, "
                f"ch.doc_id, ch.chunk_index LIMIT {_BRIEF_QUOTE_POOL}",
                {"id": pkns.key(owner, node_id), "o": owner}) or []
        else:
            return []
        if not chunks:
            return []
        at, _ = _brief_pick_chunk(store, chunks, query=query, query_embedding=query_embedding)
        pick = chunks[at]
        units = _excerpt.quote_units(str(pick.get("text") or ""), query, n=_BRIEF_QUOTES_N)
        if not units:
            return []
        doc_id = str(pick.get("doc_id") or "")
        title = ""
        for r in store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d "
                           "AND (d.owner_id = $o OR d.owner_id = '') RETURN d.title AS t",
                           {"d": doc_id, "o": owner}) or []:
            title = str(r.get("t") or "")
            break
        return [{"doc_id": doc_id, "source": title or doc_id,
                 "section": str(pick.get("sec") or ""), **u} for u in units]
    except Exception:
        return []


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


#: choose 의 첫 후보 재료에 싣는 발췌 수 상한(`max_excerpts` 가 더 작으면 그쪽).
_BRIEF_CHOOSE_EXCERPTS = 2


def _brief_candidate_material(store, cand: dict, *, max_excerpts: int, owner: str, mode,
                              historical: bool, as_of_scope, known_scope) -> dict | None:
    """choose 의 첫 후보 재료 — 확정 답과 같은 이름(one_liner·body_src·body_cite·excerpts)으로,
    발췌는 같은 시간 규칙으로 거른다. 확정이 아니므로 `node`·`grounds`·관계는 싣지 않는다.
    후보가 안 열리면 None. 감춘 수는 choose 응답의 수에 더하지 않고 재료 안의 알림으로만 낸다.
    근거: [[vinc.plan.choose-cost-measurement-2026-10-19]]"""
    cid = str(cand.get("id") or "")
    if not cid:
        return None
    got = node_context(owner=owner, node=cid, limit=max(1, int(max_excerpts)), store=store)
    if not got.get("executed"):
        return None
    ctx, note = _brief_scope_material({**got, "links": {}}, store, {}, owner=owner, mode=mode,
                                      historical=historical, as_of_scope=as_of_scope,
                                      known_scope=known_scope)
    ctx["excerpts"] = list(ctx.get("excerpts") or [])[:min(_BRIEF_CHOOSE_EXCERPTS,
                                                          max(0, int(max_excerpts)))]
    body = _brief_body(ctx)
    out = {"candidate": cid, "cite_id": cid, "one_liner": body["text"],
           "body_src": body["src"], "body_cite": body["cite"], "excerpts": ctx["excerpts"]}
    if note:
        out["validity_note"] = note
    why = [f"reading around the node failed: {got['error']}" if got.get("error") else "",
           f"excerpts could not be searched: {got['excerpt_error']}"
           if got.get("excerpt_error") else ""]
    if any(why):
        out["partial"] = True
        out["partial_note"] = "; ".join(w for w in why if w)
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
    `api.temporal_read` 와 검색이 같은 규칙을 쓴다(감사 F14)."""
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
    """발췌 한 줄을 그 문서의 유효구간으로 가린다. 검색이 청크를 거르는 규칙과 같다
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
    - 관계(F14·B1): as_of 와 known_at 을 `api.temporal_read.Gate` 로 거른다 —
      vinc_neighbors 가 같은 요청에서 감추는 노드를 여기서도 감춘다. 수(out·in·attached·
      total)도 감춘 만큼 줄인다. 잘린 창 뒤(next_offset)의 관계는 여기서 거르지 않는다.
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
          query_runtime: str | None = None, query_model: str | None = None,
          query_colbert: str | None = None, kinds=None) -> dict:
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
           candidate_material(choose 에서만 — 첫 후보의 {candidate, cite_id, one_liner,
           body_src, body_cite, excerpts(최대 2)})}.
    확정 노드와 되묻기 후보는 `quotes`(글자 그대로의 인용 단위)를 달고 올 수 있다.

    시간 인자(`as_of`·`mode`·`historical`·`known_at`)는 여기서 전부 적용한다 —
    해소(검색), 직참조 노드, 발췌, 관계 모두에. 사이드카가 뒤에서 다시 거르지 않는다
    (감사 B1·B2·F14). `known_at` 은 저장 시각 컷오프이고 과거 본문을
    복원하지 않는다(검색과 같다).
    `kinds` 가 있으면 후보와 확정 노드를 그 종류로 한정한다(검색의 노드 팔이 자르기 전에 거른다).
    질문 속 id 가 다른 종류면 열지 않고 none 으로 답한다(as_of 밖의 id 와 같다).
    근거: [[vinc.plan.mcp-agent-surface §4]]"""
    cfg = cfg or load_config()
    base: dict = {"kind": "brief", "executed": False, "query": query,
                  "status": "none", "candidates": [], "grounds": []}
    try:
        kinds = _retrieval.parse_kinds(kinds)
    except ValueError as exc:
        base["note"] = base["error"] = f"invalid_kinds: {exc}"
        return base
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
    # 재료(발췌·관계)를 거를 as_of 기간. current 는 기간이 아니라 상태로 가린다.
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
        if kinds and qids:
            kept = [i for i in qids if i.split(":", 1)[0] in kinds]
            if not kept:
                base["executed"] = True
                base["kinds_filtered"] = len(qids)
                base["note"] = "the referenced node is not of the requested kinds"
                return base
            qids = kept
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
                    # B1 — 컷오프 뒤에 저장된 노드는 그 시점의 그래프에 없었다. 저장 시각을
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
                     query_runtime=query_runtime, query_model=query_model,
                     # 기존 규칙은 ColBERT 순서 위에서 잰 적이 없다 — 학습 규칙일 때만 재채점된 후보로 해소한다.
                     query_colbert=query_colbert if _brief_rule_colbert() else None,
                     **({"kinds": kinds} if kinds else {}))
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
        # 노드 의미 팔이 모델 불일치로 꺼졌으면 해소가 그 팔 없이 돌았다. 그 사실도 나른다.
        if (res.get("node_semantic") or {}).get("state") == "model_mismatch":
            base["node_semantic"] = res["node_semantic"]
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
        # 넓은 풀은 되묻기 목록용으로 따로 두고, 확정·기권·가드는 앞 `_BRIEF_RESOLVE_N` 개로 본다.
        pool = [h for h in res.get("node_hits") or [] if isinstance(h, dict) and h.get("id")]
        full = res.get("node_hits") or []
        res = {**res, "node_hits": full[:_BRIEF_RESOLVE_N]}
        rescored = (res.get("node_rescore") or {}).get("state") == "applied"
        # v3 는 의미만 기권을 재채점 **전** 순서의 앞 다섯으로 본다(재채점이 기권 대상을 바꾸지 않게).
        # 재채점이 안 된 질의는 기존 규칙 그대로 간다.
        v3 = _brief_rule_v3() and rescored and bool(full)
        learned = _brief_rule_learned() and rescored
        configured = _brief_rule()
        if configured != "production" and not (v3 or learned):
            # 학습 규칙이 설정돼 있어도 재채점이 안 된 질의는 기존 규칙이 답한다. 그 사실을 답에 싣는다:
            # 없으면 평가도 사람도 어느 규칙의 답인지 가를 수 없다(토큰이 빠진 노드 하나로도 재채점이 빠진다).
            # 근거: [[vinc.canon.rag-production-state]]
            _rs = res.get("node_rescore") or {}
            base["resolution"] = {"rule": "production", "configured": configured,
                                  "rescore": _rs.get("state") or "missing"}
            if _rs.get("reason"):
                base["resolution"]["reason"] = _rs["reason"]
        if _brief_semantic_none({**res, "node_hits": _brief_v3_prod_head(full)} if v3 else res):
            base["note"] = "no sufficiently close semantic candidate"
            return base
        if v3:
            status, picked, _p = _brief_resolve_v3(query, res.get("node_hits") or [], res)
            base["resolution"] = {"rule": "v3", "p": None if _p is None else round(_p, 4)}
        elif learned:
            # ColBERT 로 다시 선 후보 위의 학습 규칙. 재채점이 안 된 질의는 기존 규칙으로 간다.
            status, picked, _p = _brief_resolve_learned(query, res.get("node_hits") or [])
            base["resolution"] = {"rule": "learned", "p": None if _p is None else round(_p, 4)}
        else:
            status, picked = _brief_resolve(
                query, res.get("node_hits") or [],
                mirror_text=lambda h: _brief_mirror_text(store, owner, h))
        # B5 — current 의 기권은 답(1위)과 그 주장 키를 공유하는 후보만 본다. 후보 아무나
        # 보면 구간이 없어 늘 unknown 인 개념 후보 하나가 다른 주장의 답을 막는다.
        if mode == "current" and picked and _brief_current_uncertain(
                store, owner=owner, answer=picked[0], hits=res.get("node_hits") or []):
            status = "choose"
            base["validity_note"] = "current claim evidence is conflicting or unknown; no current state was chosen"
        # D11 — current 에서 동점이던 노드가 은퇴했으면 남은 노드로 확정한다. 의도된 뜻이다:
        # current 는 "지금 성립하는 것"을 묻고, 은퇴한 노드는 그 질문의 경쟁자가 아니다.
        # 아래 동점 가드는 일반 모드에서 감춘 행의 점수(validity_best_dropped)를 보는데,
        # current 에서는 finish_nodes 가 은퇴한 노드를 감추며 그 점수를 남기지 않으므로
        # 가드가 서지 않는다. 그 차이가 이 뜻을 구현한다(감사 D11).
        # 🔴 **유효구간 필터가 동점을 깨서 확신을 만들면 안 된다.** 지운 행이 남은 1위와
        # 같은 점수였다면, 원래 이 질문은 "모르겠다"(choose)였다. 그 자리에서 확정을
        # 주면 필터가 **보이는 것**이 아니라 **답**을 바꾼 것이고, 그건 §4.2 가 의도한
        # 바가 아니다. 실측(2026-09-09): 이 갈래가 없는 동안 답 불가 질문 6건 중 1건이
        # 확정됐다(정직성 0.0 → 0.1667). 동점이던 Record 둘 중 하나가 걸러지면서 남은
        # 하나가 단독 승자가 된 모양이었다.
        # 근거: [[record:the-validity-filter-broke-a-tie-and-manufactured-confidence-2026-09-09]]
        # v3 확정에는 돌지 않는다: 감춘 행의 점수는 v3 특징(:func:`_brief_v3_features`)에 이미 있다.
        if status == "resolved" and picked and base.get("validity_filtered") and not v3:
            _bd = tuple(base.get("validity_best_dropped") or (0, 0))
            if (_brief_hidden_ties(query, picked[0], _bd)
                    and not _brief_semantic_tie_clear(picked, res)):
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
            # 되묻기 목록은 넓은 풀 전체를 의미 순서(`_brief_list_rank`)로 세워 노출 수에서
            # **한 번** 자른다. 확정용 순위(팔 개수 → 토큰)는 의미 팔에만 걸린 후보를 맨 뒤로
            # 미는데, 넓은 풀에 새로 들어오는 정답은 대부분 그런 후보다.
            # 학습 규칙은 ColBERT 순서를 그대로 보인다 — 의미 순서로 다시 세우면 재채점을 되돌린다.
            picked = list(pool) if (learned or v3) else _brief_list_rank(pool)
            base["candidates"] = _brief_candidate_rows(picked[:_brief_expose_n()])
            if not _reask and _brief_quotes_on():
                for _c in base["candidates"]:
                    _q = _brief_node_quotes(store, owner=owner, node_id=str(_c.get("id") or ""),
                                            query=query, query_embedding=query_embedding)
                    if _q:
                        _c["quotes"] = _q
            if not _reask:
                _brief_attach_section(base, store, owner=owner, query=query, res=res,
                                      query_embedding=query_embedding,
                                      nodes=[str(c.get("id") or "") for c in base["candidates"]])
            # 첫 후보의 재료를 따로 한 키에 싣는다. status·candidates 는 그대로다 — 이 재료가
            # 확정 답으로 읽히면 안 된다(재료의 꼴과 이유는 `_brief_candidate_material`).
            if not _reask and base["candidates"]:
                _mat = _brief_candidate_material(store, base["candidates"][0],
                                                 max_excerpts=max_excerpts, **rule)
                if _mat is not None:
                    base["candidate_material"] = _mat
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
        if not _reask and _brief_quotes_on():
            _q = _brief_node_quotes(store, owner=owner, node_id=str(hit.get("id") or ""),
                                    query=query, query_embedding=query_embedding)
            if _q:
                out["quotes"] = _q
        if not _reask:
            _brief_attach_section(out, store, owner=owner, query=query, res=res,
                                  query_embedding=query_embedding,
                                  nodes=[str(hit.get("id") or "")])
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
        def _mention_excerpt(cid: str, label: str, title: str, detail: str) -> dict | None:
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
                    "excerpt": _excerpt.excerpt(m[0].get("text") or "", title, detail)}
        base.update(
            executed=True,
            evidence=r.get("ev") or "",
            confidence=float(r.get("cf") or 0.0),
            src={"id": src, "title": r.get("at") or src, "one_liner": r.get("ad") or "",
                 "excerpt": _mention_excerpt(src, r["src_label"], r.get("at") or "",
                                             r.get("ad") or "")},
            dst={"id": dst, "title": r.get("bt") or dst, "one_liner": r.get("bd") or "",
                 "excerpt": _mention_excerpt(dst, r["dst_label"], r.get("bt") or "",
                                             r.get("bd") or "")})
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
            # 유효기간이 닫힌 문서는 행에 그 날을 싣는다(없으면 키도 없다). 트리는 거르지 않는다:
            # 숨길지는 화면이 정한다(커뮤니티 스페이스는 내린 페이지를 숨긴다).
            if d.get("valid_until"):
                row["valid_until"] = d["valid_until"]
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
        # 비-개념 끝점은 연결 부재가 아니라 층 부재다(path 의 개념 전용 거절과 같은
        # 태도). suggest_ties 는 개념 메타로만 점수를 매기므로 결정·기록·사건은 여기서
        # 후보를 받을 수 없다 — "unknown concept" 로 답하면 id 만 고치면 될 것처럼
        # 읽힌다. 근거: [[vinc.manual.core]]
        if node.lower().startswith(_NON_CONCEPT_PREFIXES):
            return {"kind": "suggest_ties", "executed": True, "found": False,
                    "node": node, "suggestions": [], "total": 0, "truncated": False,
                    "formula": TIE_SCORE_FORMULA,
                    "error": f"suggest_ties walks the concept layer only: {node} is not a concept id",
                    "hint": ("Tie scores are computed between `concept:` nodes (tag overlap, "
                             "domain, shared neighbours). A record, decision or episode attaches "
                             "to concepts rather than being tied to them, so it is never a tie "
                             "endpoint here. Use vinc_neighbors on that id to see its attachments.")}
        # 후보에서 질문한 노드 자신은 뺀다 — 자기 자신을 잇자고 제안하지 않는다.
        cands = [c for c in near_nodes(store, node, owner=owner) if c.get("id") != node]
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


# ── ER 병합 후보 제안 (E-G4) — 조회 전용, 자동 병합 금지(THESIS T1) ────────────
# 리서치 ③(개체 식별 해소)의 **검출 단계만** 채택한다 — 자동 병합은 영구 금지
# (오병합 비가역·LLM 판정 반입 금지·"시스템은 제안만"). 여기서는 결정론 타이틀
# 유사로 중복 후보를 **제안**하고, 확정(엣지 재지향+삭제)은 기존 merge_nodes
# (비가역·사용자 지시·단일 쓰기 표면 R4) 경유. suggest_ties(D25 전 단계)와 동형의
# 조회-전용 계층. 실사용 사고(중복 개념 오염 concept:ipaas)의 예방 표면.
_MERGE_TOKEN_RE = re.compile(r"[0-9a-z가-힣]+")


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


def _topic_description_mirrors(store, owner: str | None) -> set[str]:
    """토픽 설명 문서의 미러 id(논리 id). 설명은 일부러 잇지 않고 미러의 domain 으로
    토픽에 속하므로(`api/topic_description.py`) 성분 진단은 이것을 고아로 세지 않는다.

    `_DocMeta.doc_id` 에는 소유자 접두가 없고 진단 행의 id 도 논리 id 라서, 접두 없이
    `concept:doc.` 만 붙여 맞춘다. 네임스페이싱을 켜도 꺼도 같은 집합이 나온다."""
    from api import topic_description
    params: dict = {"origin": topic_description.ORIGIN}
    where = ""
    if owner:
        where = "AND d.owner_id = $owner "
        params["owner"] = owner
    rows = store.run("MATCH (d:_DocMeta) WHERE d.origin = $origin " + where
                     + "RETURN d.doc_id AS doc_id", params) or []
    return {_DOC_CONCEPT_PREFIX + str(r["doc_id"]) for r in rows if r.get("doc_id")}


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
from store.embedded_schema import _LOUVAIN_PROJECTED_LABELS as _LOUVAIN_LABELS  # noqa: E402


def _algo_graph(store, owner: str | None, rel_tables: tuple,
                labels: tuple | None = None) -> tuple[list[str], list[tuple]]:
    """진단용 투영 — 관계 표의 끝점 라벨에 속한 노드 전부와 그 관계들. 소유자가 있으면
    **양 끝이 모두 그 소유자**인 것만 담는다. 라벨·PK·끝점 쌍은 스키마에서 파생한다
    (손으로 적은 목록이 낡아 진단을 통째로 죽인 일이 있다 — `_algo_projection` 주석).

    id 는 논리 id 로 돌아온다(네임스페이싱 스토어의 물리 키는 조회 계층이 벗긴다)."""
    from store import embedded_schema as _es
    pks = {lbl: spec["pk"] for lbl, spec in _es.node_table_specs().items()}
    want = set(rel_tables)
    # ``labels`` 를 주면 양 끝이 그 표인 쌍만 읽는다. 군집은 v23 에 넓어진 RELATED_TO 중
    # 개념끼리만 본다(`_LOUVAIN_PROJECTED_LABELS`).
    pairs = [(sp["label"], sp["from"], sp["to"]) for sp in _es.rel_table_specs()
             if sp["label"] in want and sp["from"] in pks and sp["to"] in pks
             and (labels is None or (sp["from"] in labels and sp["to"] in labels))]
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
              owner: str | None = None, graph_out: dict | None = None,
              labels: tuple | None = None):
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
        nodes, edges = _algo_graph(store, owner, tuple(rel_tables or ALGO_REL_TABLES), labels)
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
    orphans:[id…], component_count, orphan_count, ambiguous, ambiguous_count,
    topic_descriptions_excluded}. 스토어를 못 읽으면 executed=False + note.

    ``ambiguous`` — **두 스페이스에 같은 슬러그로 존재해 어느 쪽이 내 것인지 못 가리는
    id**. 컴포넌트에도 고아에도 안 넣는다. 왜 생기는지는 아래 계산 자리의 주석에 있다.

    ``topic_descriptions_excluded`` — 컴포넌트와 고아에서 뺀 토픽 설명 미러의 수. 설명
    문서는 일부러 잇지 않고 미러의 domain 으로 토픽에 속하므로 이을 대상이 아니다. 그래서
    고아 목록에는 실제로 이어야 할 것만 남는다(`_topic_description_mirrors`).

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
        mirrors = _topic_description_mirrors(store, owner)
        excluded: set[str] = set()
        groups: dict = {}
        # 성분의 실제 노드 수를 종류 불문으로 센다. 개념만 세면 레코드·사건·결정에만
        # (ATTACHES_TO·ABOUT 으로) 붙은 개념이 저 혼자인 것처럼 보여 거짓 고아가 된다 —
        # 그 개념을 고아로 지우면 쓰이고 있는 노드를 지운다. 미러는 일부러 안 잇는 토픽
        # 설명 노드라 크기에서 뺀다. 근거: [[vinc.manual.core]]
        full_size: dict = {}
        for r in rows:
            cid, grp = r.get("id"), r.get("value")
            if not cid or cid in drop or grp is None:
                continue
            if cid not in mirrors:
                full_size[grp] = full_size.get(grp, 0) + 1   # 개념·레코드·사건·결정 전부
            if scope is not None and cid not in scope:
                continue                   # 비-개념은 크기엔 세고 개념 목록엔 안 넣는다
            if cid in mirrors:
                excluded.add(cid)          # 토픽 설명은 빼고 수만 밝힌다
                continue
            groups.setdefault(grp, []).append(cid)
        mlim = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        comps = [{"group": g, "size": len(m), "members": sorted(m)[:mlim],
                  "members_truncated": len(m) > mlim}
                 for g, m in groups.items()]
        comps.sort(key=lambda c: (-c["size"], str(c["group"])))   # 큰 컴포넌트 우선
        # 고아 = 성분에 저 혼자뿐인 개념. 개념 목록상 홀로여도(len==1) 성분에 레코드·
        # 사건·결정이 함께 있으면(full_size>1) 고아가 아니다 — 들어오는 엣지도 연결이다.
        orphans = sorted(m[0] for g, m in groups.items()
                         if len(m) == 1 and full_size.get(g, 1) == 1)
        base.update(executed=True, components=comps, orphans=orphans,
                    component_count=len(comps), orphan_count=len(orphans),
                    ambiguous=ambiguous, ambiguous_count=len(ambiguous),
                    topic_descriptions_excluded=len(excluded))
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
    anchored_count, results_excluded, scanned}.

    **어느 쪽이 옳은지 판정하지 않는다.** 두 측정이 다른 날짜에 나왔다는 사실만
    내놓는다. 판정은 경로를 열어 보는 사람의 몫이고, 그 열어 보기가 이 도구가
    대신할 수 없는 잔여다(§7.5).

    짝짓기는 **가장 최근 것 대 나머지**다(n² 가 아니다). 물어야 할 질문이 "이 옛
    주장이 아직 서 있나"이지 "옛 주장 둘이 서로 맞나"가 아니기 때문이다.

    짝과 닻 없는 목록은 **현재 기록**만 본다. `valid_until` 이 오늘이거나 그 전인 기록은
    은퇴했거나 대체된 것이라 다시 잴 일도 닻을 달 일도 없다(`_retired_claim`, `stale` 과
    같은 기준). `anchored_count` 와 `scanned` 는 은퇴한 기록도 센다.

    `props.claim_kind` 가 ``"result"`` 인 기록은 한 시점의 실행 결과다. 같은 파일의 새
    측정과 다투지 않으므로 짝에서 통째로 빠진다(최신 쪽으로도 나머지 쪽으로도). 그렇게
    뺀 현재 기록의 수는 `results_excluded` 로 밝히고, `anchored_count` 와 `scanned` 는
    그대로 센다. 다른 값은 모르는 값으로 보고 보통 주장처럼 다룬다.

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
            "unanchored_truncated": False, "anchored_count": 0,
            "results_excluded": 0, "scanned": 0}
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
        today = _dt.date.today().isoformat()
        by_path: dict[str, list[dict]] = {}
        unanchored: list[dict] = []
        anchored = 0
        results = 0
        for r in rows:
            props = _props_to_obj(r.get("props"))
            rid = pkns.strip(r.get("id") or "")
            title = r.get("title") or rid
            retired = _retired_claim(r, today)
            sp = str(props.get("subject_path") or "").strip()
            if sp:
                anchored += 1
                if retired:
                    continue               # 은퇴한 주장은 짝을 짓지 않는다
                if props.get("claim_kind") == "result":
                    results += 1           # 시점 결과는 새 측정과 짝을 짓지 않는다
                    continue
                by_path.setdefault(sp, []).append({
                    "id": rid, "title": title,
                    "measured_at": str(props.get("measured_at") or ""),
                    "measured_ref": str(props.get("measured_ref") or "")})
                continue
            if retired:
                continue                   # 은퇴한 주장에는 닻을 달 일이 없다
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
                    anchored_count=anchored, results_excluded=results,
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


def _retired_claim(row: dict, today: str) -> bool:
    """유효구간이 오늘이나 그 전에 끝난 기록(은퇴한 주장). 대체된 주장도 대체당한
    기록의 `valid_until` 로 여기 온다. 대체 엣지는 그 값이 없으면 경고하고
    (`fragment._warn_supersede_interval`), 값이 없는 기록은 현재로 남는다.
    주장 진단의 목록들이 이 기준 하나를 쓴다."""
    until = str(row.get("valid_until") or "")
    return bool(until) and until <= today


def _stale_claims(store, record_rows: list[dict], *, owner: str | None) -> list[dict]:
    """측정 뒤에 경로가 바뀐 **현재** 주장. 변경 근거는 `props.files` 를 가진 Episode 다.

    판정하지 않는다. "이 주장을 잰 뒤에 그 파일이 바뀌었다"는 사실만 낸다. 같은 커밋
    (`measured_ref` 와 `head_sha` 가 접두로 겹침)에서 잰 것은 낡지 않았다. 측정과 같은 날의 변경은 `same_day` 로 표시해 낸다. 날짜가 없는 쪽은 비교하지
    않는다(없는 시간을 지어내지 않는다). 주장마다 가장 최근 변경 하나만 붙인다.

    `props.reviewed_at` 은 누군가 이 날짜까지의 변경을 살펴보고 주장이 아직 선다고 확인한
    날이다. 어떤 기록이든 `measured_at` 과 이 날짜 중 늦은 쪽 뒤의 변경만 센다. 확인일 당일의
    변경은 살펴본 것으로 친다. 날짜 검사는 `measured_at`·Episode `date` 와 같은
    `node_schema.normalize_time` 이고, 그 검사를 못 지난 값은 없는 것으로 본다. 확인일이
    `measured_at` 없는 기록을 대상으로 끌어들이지는 않는다.
    근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] 를 잇는
    그래프 문서 vinc.plan.spec-drift-graphiti-0-9-8-2026-10-11 §3."""
    from .fragment import _norm_subject_path
    from .node_schema import normalize_time
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
        if _retired_claim(r, today):
            continue                       # 은퇴한 주장은 낡을 수 없다
        props = _props_to_obj(r.get("props"))
        sp = str(props.get("subject_path") or "").strip()
        at = str(props.get("measured_at") or "")
        ref = str(props.get("measured_ref") or "")
        if not sp or not at:
            continue
        reviewed = normalize_time(props.get("reviewed_at"))
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
        # 확인일까지의 변경은 이미 살펴본 것이라 세지 않는다(당일 포함).
        hits = [c for c in changes
                if c["date"] >= at and not (reviewed and c["date"] <= reviewed)
                and not same_commit(c["head_sha"])
                and any(_path_covers(sp, f) for f in c["files"])]
        if not hits:
            continue
        latest = max(hits, key=lambda c: (c["date"], c["id"]))
        rid = pkns.strip(r.get("id") or "")
        claim = {"id": rid, "title": r.get("title") or rid, "subject_path": sp,
                 "measured_at": at, "measured_ref": ref}
        if reviewed:
            claim["reviewed_at"] = reviewed
        out.append({"claim": claim,
                    "change": {k: latest[k] for k in ("id", "title", "date", "head_sha")},
                    "changes_since": len(hits),
                    "same_day": latest["date"] == at})
    out.sort(key=lambda x: (x["change"]["date"], x["claim"]["id"]), reverse=True)
    return out


#: 결정 문이 한 번에 받는 경로 수. 경로 목록은 사이드카 GET 질의 문자열에 JSON 으로
#: 실리므로 요청 줄 상한(64KB) 안에 머물 만큼만 받는다. 더 큰 diff 는 부르는 쪽이 나눠 보낸다
#: (`vinc gate check` 가 그렇게 한다).
DECIDE_PATHS_MAX = 200
#: 같은 경로를 덮는 항목의 순서. 직접 닻이 먼저, 그 닻에서 한 홉 닿은 결정이 나중이다.
_GATE_MATCH_ORDER = {"exact": 0, "directory": 1, "related": 2}


def _gate_subjects(props: dict) -> list[str]:
    """노드가 가리키는 경로들(정규화). Record 는 쓰기 시점에 이미 접혀 있지만, Decision 은
    쓰기 경로가 접지 않으므로(`fragment._claim_props`) 여기서 같은 함수로 접는다. 결정은
    여러 경로를 다스릴 수 있어 목록도 받는다."""
    from .fragment import _norm_subject_path
    raw = props.get("subject_path")
    values = raw if isinstance(raw, list) else [raw]
    return [s for s in dict.fromkeys(_norm_subject_path(v) for v in values
                                     if isinstance(v, str)) if s]


def _gate_validity(valid_from: str, today: str) -> str:
    return "not_yet" if valid_from and valid_from[:10] > today else "current"


def decide_paths(paths, *, owner: str | None = None, cfg=None, store=None,
                 limit: int | None = None) -> dict:
    """결정 문(decision gate): 바뀐 경로마다 그 경로를 다스리는 **현재** 기록과 결정.

    모델 없이 결정론으로 돈다. 가부를 가리지 않는다: "이 경로에는 이 주장이 걸려 있다"만 내고,
    diff 가 그것을 어기는지는 읽는 사람이 연다(`claim_diagnosis` 와 같은 태도).
    근거: [[vinc.plan.agent-harness-work-order-2026-10-25 §2]] · [[vinc.ops.graph-claim-decay]] §7.3.

    맞춤은 `claim_diagnosis`·`_stale_claims` 와 같은 부품을 쓴다. 두 번째 맞춤기를 두지 않는다:
    - 경로는 `fragment._norm_subject_path` 로 접고(`repo:path`), 덮음은 `_path_covers` 다.
      같은 경로면 ``exact``, 노드의 경로가 바뀐 경로의 상위 디렉터리면 ``directory``.
    - 은퇴는 `_retired_claim` 이다. Record 는 열, Decision 은 `props.valid_until` 로 본다.
    - `props.claim_kind == "result"` 인 기록은 한 시점의 실행 결과라 다스리지 않는다(센다).

    직접 닿은 기록·결정에서 **한 홉** 닿은 현재 결정도 싣는다(``related``, `via` 는 그 닻).
    길은 Record→Decision `ATTACHES_TO` 와 결정↔결정 관계(`_GRAPH_READ_PAIRS`)다. 개념을
    건너는 두 홉은 따르지 않는다: 한 개념에 걸린 결정이 모두 딸려 와 목록이 소음이 된다.

    리포가 없는 경로는 리포가 있는 주장과 맞지 않는다(동명 파일 충돌을 막는 R4 의 이유 그대로).
    그런 입력은 `unqualified_paths` 로 밝힌다.

    반환 {executed, verdict: flag|pass, paths:[{path, governed_by:[...], count, truncated}],
    path_count, flagged_paths, governing_count, retired_excluded, results_excluded,
    unqualified_paths, scanned:{records, decisions}}."""
    from .fragment import _norm_subject_path, _split_subject
    base = {"kind": "decide_paths", "executed": False, "verdict": "pass", "paths": [],
            "path_count": 0, "flagged_paths": 0, "governing_count": 0,
            "retired_excluded": 0, "results_excluded": 0, "unqualified_paths": []}
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(p, str) or not p.strip() for p in paths)):
        base["error"] = "invalid paths: send a nonempty list of repo:path strings"
        return base
    touched = list(dict.fromkeys(_norm_subject_path(p) for p in paths))
    if len(touched) > DECIDE_PATHS_MAX:
        base["error"] = (f"invalid paths: at most {DECIDE_PATHS_MAX} paths per call; "
                         "split the list")
        return base
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = "no reachable store"
        return base
    try:
        today = _dt.date.today().isoformat()
        params = {"owner": owner} if owner else {}
        anchors: list[dict] = []
        retired = results = 0
        record_rows = store.run(
            "MATCH (r:Record) " + ("WHERE r.owner_id = $owner " if owner else "") +
            "RETURN r.record_id AS id, r.title AS title, r.props AS props, "
            "r.valid_from AS valid_from, r.valid_until AS valid_until", params) or []
        for r in record_rows:
            props = _props_to_obj(r.get("props"))
            subjects = _gate_subjects(props)
            if not subjects:
                continue
            if _retired_claim(r, today):
                retired += 1
                continue
            if props.get("claim_kind") == "result":
                results += 1
                continue
            rid = pkns.strip(r.get("id") or "")
            anchors.append({"id": rid, "kind": "record", "title": r.get("title") or rid,
                            "subjects": subjects,
                            "valid_from": str(r.get("valid_from") or ""),
                            "valid_until": str(r.get("valid_until") or ""),
                            "measured_at": str(props.get("measured_at") or ""),
                            "measured_ref": str(props.get("measured_ref") or "")})
        decision_rows = store.run(
            "MATCH (d:Decision) " + ("WHERE d.owner_id = $owner " if owner else "") +
            "RETURN d.decision_id AS id, d.title AS title, d.props AS props", params) or []
        for d in decision_rows:
            props = _props_to_obj(d.get("props"))
            subjects = _gate_subjects(props)
            if not subjects:
                continue
            if _retired_claim({"valid_until": props.get("valid_until")}, today):
                retired += 1
                continue
            did = pkns.strip(d.get("id") or "")
            anchors.append({"id": did, "kind": "decision", "title": d.get("title") or did,
                            "subjects": subjects,
                            "valid_from": str(props.get("valid_from") or ""),
                            "valid_until": str(props.get("valid_until") or "")})

        def item(node: dict, match: str, **extra) -> dict:
            out = {"id": node["id"], "kind": node["kind"], "title": node["title"],
                   "match": match, **extra,
                   "valid_from": node["valid_from"], "valid_until": node["valid_until"],
                   "validity": _gate_validity(node["valid_from"], today)}
            for k in ("measured_at", "measured_ref"):
                if node.get(k):
                    out[k] = node[k]
            return out

        hits: dict[str, dict[str, dict]] = {p: {} for p in touched}
        for p in touched:
            for a in anchors:
                subject = next((s for s in a["subjects"] if _path_covers(s, p)), None)
                if subject is not None:
                    hits[p][a["id"]] = item(a, "exact" if subject == p else "directory",
                                            subject_path=subject)
        related, related_note = _gate_related_decisions(
            store, sorted({i for h in hits.values() for i in h}), owner=owner, today=today)
        for p in touched:
            # 직접 닻을 가까운 순(exact → directory, 같으면 id)으로 훑어 via 를 정한다.
            # 목록을 먼저 굳히므로 여기서 더한 related 는 다시 닻이 되지 않는다(한 홉).
            for via in sorted(hits[p], key=lambda i: (_GATE_MATCH_ORDER[hits[p][i]["match"]], i)):
                for dec in related.get(via, []):
                    if dec["id"] not in hits[p]:
                        hits[p][dec["id"]] = item(dec, "related", via=via,
                                                  relation=dec["relation"])
        lim = _clamp_limit(limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        out_paths, governing = [], set()
        for p in touched:
            # 순서: 맞춤의 가까움 → 결정 먼저 → 최근 측정 먼저 → id. 정렬 세 번(안정 정렬).
            rows = sorted(hits[p].values(), key=lambda x: x["id"])
            rows.sort(key=lambda x: x.get("measured_at", ""), reverse=True)
            rows.sort(key=lambda x: (_GATE_MATCH_ORDER[x["match"]], x["kind"] != "decision"))
            governing.update(x["id"] for x in rows)
            out_paths.append({"path": p, "governed_by": rows[:lim], "count": len(rows),
                              "truncated": len(rows) > lim})
        flagged = sum(1 for x in out_paths if x["count"])
        base.update(executed=True, verdict="flag" if flagged else "pass", paths=out_paths,
                    path_count=len(touched), flagged_paths=flagged,
                    governing_count=len(governing), retired_excluded=retired,
                    results_excluded=results,
                    unqualified_paths=[p for p in touched if not _split_subject(p)[0]],
                    scanned={"records": len(record_rows), "decisions": len(decision_rows)})
        if related_note:
            base["related_note"] = related_note
        if base["unqualified_paths"]:
            base["unqualified_note"] = ("these paths name no repository, so they only match "
                                        "claims that name none either; send repo:path")
    finally:
        if owns:
            store.close()
    return base


def _gate_related_decisions(store, ids: list[str], *, owner: str | None,
                            today: str) -> tuple[dict[str, list[dict]], str]:
    """닻 id → 한 홉 닿은 현재 결정들. 결과는 (표, 실패 문장). 한 팔이 실패해도 직접 닻은
    그대로 낸다: 이웃 결정은 증강이고, 잃은 것은 문장으로 말한다."""
    out: dict[str, list[dict]] = {}
    if not ids:
        return out, ""
    rels = "|".join(_GRAPH_READ_PAIRS.get(
        ("Decision", "decision_id", "Decision", "decision_id"), ()))
    own = " AND s.owner_id = $owner AND d.owner_id = $owner" if owner else ""
    ret = ("RETURN s.{spk} AS via, d.decision_id AS id, d.title AS title, d.props AS props, "
           "{rel} AS relation")
    arms = [("MATCH (s:Record)-[e:ATTACHES_TO]->(d:Decision) "
             f"WHERE s.record_id IN $ids{own} "
             + ret.format(spk="record_id", rel="e.type"))]
    if rels:
        arms.append(f"MATCH (s:Decision)-[e:{rels}]->(d:Decision) "
                    f"WHERE s.decision_id IN $ids{own} "
                    + ret.format(spk="decision_id", rel="type(e)"))
        arms.append(f"MATCH (d:Decision)-[e:{rels}]->(s:Decision) "
                    f"WHERE s.decision_id IN $ids{own} "
                    + ret.format(spk="decision_id", rel="type(e)"))
    params: dict = {"ids": pkns.keys(owner, ids)}
    if owner:
        params["owner"] = owner
    failed = 0
    for cypher in arms:
        try:
            rows = store.run(cypher, params) or []
        except Exception:      # noqa: BLE001 — 증강 팔 하나의 실패가 문 전체를 죽이지 않는다
            failed += 1
            continue
        for row in rows:
            props = _props_to_obj(row.get("props"))
            if _retired_claim({"valid_until": props.get("valid_until")}, today):
                continue
            did = pkns.strip(row.get("id") or "")
            via = pkns.strip(row.get("via") or "")
            if not did or did == via:
                continue
            out.setdefault(via, []).append({
                "id": did, "kind": "decision", "title": row.get("title") or did,
                "relation": str(row.get("relation") or "").lower(),
                "valid_from": str(props.get("valid_from") or ""),
                "valid_until": str(props.get("valid_until") or "")})
    for via, decs in out.items():
        seen: dict[str, dict] = {}
        for dec in sorted(decs, key=lambda x: (x["id"], x["relation"])):
            seen.setdefault(dec["id"], dec)
        out[via] = list(seen.values())
    note = (f"{failed} of {len(arms)} relation lookups failed; decisions one hop from the "
            "matched claims may be missing" if failed else "")
    return out, note


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
                               labels=_LOUVAIN_LABELS,
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
                               labels=_LOUVAIN_LABELS,
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
    keep = set(ids)   # ego 경계(depth+1 유출 제거) — graph() 와 동일 정책
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
    elif owner:
        # 개념 시드도 부착 축을 합류시킨다. graph() 의 접착제 후조회와 같은 플랜·같은
        # ids 모드다. 없으면 graph_rows 의 개념↔개념 엣지만 나와, 개념에 ATTACHES_TO 로
        # 붙은 레코드와 ABOUT 으로 걸린 결정이 nodes·edges 에서 통째로 빠진다(결정은
        # conflicts 로만 비치고, decide 의 "무엇이 걸려 있나"가 빈 답이 된다). 축 플랜은
        # owner 필수라(소유자 격리) owner 가 있을 때만 돈다 — 무소유 로컬 모드는 개념
        # 축만 싣는다. 같은 물리 엣지는 한 플랜에서만 오므로(graph_rows=개념↔개념, 축
        # 플랜=ABOUT·ATTACHES_TO·CONNECTS·나머지) 이중 계상이 없다.
        # 근거: [[vinc.manual.core]]
        for plan_fn in _GRAPH_READ_PLANS:
            ax = execute(plan_fn(owner=owner, ids=ids), cfg, store=store)
            if not ax.get("executed"):
                continue
            for r in ax.get("rows", []):
                rows.append(r)
                if r.get("id"):
                    keep.add(r["id"])
                if r.get("target"):
                    keep.add(r["target"])
    shaped = shape_graph(rows)
    shaped["edges"] = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
    attach_ppr(shaped, [seed])   # E-G2 — 토픽(시드) 관점 개인화 랭킹
    _order_graph_result(shaped)
    dec_res = execute(decisions_about_plan(ids, owner=owner), cfg, store=store)
    decisions = list(dec_res.get("rows", [])) if dec_res.get("executed") else []
    decisions, retired = _current_decisions(decisions, _dt.date.today().isoformat())
    conflicts, cut = _decide_conflicts(shaped["edges"], decisions)
    out = {**base, "nodes": shaped["nodes"], "edges": shaped["edges"],
           "conflicts": conflicts,
           # 크로스도메인은 상충이 아니라 **연결의 성질**이다. 자기 필드로 나간다.
           "cross_domain": [{"source": e["source"], "target": e["target"],
                             "type": e["type"]}
                            for e in shaped["edges"] if e.get("cross_domain")]}
    if cut:
        out["conflicts_truncated"] = True
    if retired:
        # 뺀 것을 말한다(F6). 받는 쪽이 "결정이 하나뿐"으로 읽지 않게.
        out["retired_decisions_excluded"] = retired
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
    # props 는 은퇴 여부를 보려고 싣는다. Decision 은 유효구간 열이 없고 `props.valid_until` 에 둔다.
    cypher = (f"MATCH (d:Decision)-[:ABOUT]->(c:Concept) WHERE {where} "
              "RETURN d.decision_id AS id, d.title AS title, c.concept_id AS subject, "
              "d.props AS props")
    return QueryPlan("decisions_about", cypher, params,
                     note="decisions attached to the id set")


def _current_decisions(rows: list[dict], today: str) -> tuple[list[dict], int]:
    """은퇴한 결정(`props.valid_until` 이 오늘이나 그 전)을 상충 검사 입력에서 뺀다.

    시간 정책은 대체된 결정을 지우지 말고 남기라고 한다. 그런데 남긴 결정이 검사에
    그대로 들어가면 대체할 때마다 대체된 결정과 새 결정이 `decisions_on_same_subject` 로
    영원히 보고된다 — 정책을 지킬수록 신호가 소음이 된다. 기준은 `decide_paths` 와 같은
    :func:`_retired_claim` 하나다. 반환 ``(현재 결정, 뺀 수)``."""
    keep: list[dict] = []
    retired = 0
    for r in rows:
        props = _props_to_obj(r.get("props")) or {}
        until = props.get("valid_until") if isinstance(props, dict) else None
        if _retired_claim({"valid_until": until}, today):
            retired += 1
            continue
        keep.append(r)
    return keep, retired


#: 한 응답이 싣는 상충 상한. 넘치면 `conflicts_truncated` 로 말한다(F6 — 잘린 것을
#: 말하지 않으면 받는 쪽은 "이게 전부"로 읽는다).
_CONFLICT_CAP = 50


def _decide_conflicts(edges: list[dict],
                      decisions: list[dict]) -> tuple[list[dict], bool]:
    """구조만으로 판별되는 상충(순수 함수 — 모델 판단 없음, 편들지 않는다).

      ① ``decisions_on_same_subject`` — 같은 개념을 두고 내려진 결정이 둘 이상.
         결정을 하나 더 쓰기 전에 읽어야 하는 바로 그것이다.
      ② ``opposite_direction`` — 한 쌍 사이에 **흐름**이 양쪽으로 다 있다.
         방향이 의미인 관계에서 이건 둘 중 하나가 틀렸다는 뜻이다.
      ④ ``cycle`` — 셋 이상의 개념이 흐름으로 고리를 이룬다(강연결요소).
      ③ ``duplicate_decision_title`` — 제목이 같은 결정 둘. 같은 것을 두 번
         결정했거나, 하나가 다른 하나를 대체하는데 그렇게 적히지 않았다.

    **흐름**은 저장 방향이 아니다. `DERIVES_FROM`·`INHERITS_FROM` 은 흐름과 반대로
    저장되므로(:data:`_REVERSED_RELS`, 레이아웃·좌측 트리와 같은 규약) 뒤집어 읽는다.
    저장 방향 그대로 비교하면 "A 가 B 의 선행, B 는 A 에서 파생"(일치)을 상충으로
    내고 "A 가 B 의 선행, A 는 B 에서 파생"(모순)은 놓친다. ②의 ``type`` 은 흐름
    source→target 을 실은 관계, ``reverse_type`` 은 그 반대 흐름을 실은 관계다.
    자기 고리(A→A)는 내지 않는다.

    싣는 순서는 구조 상충(②·④) → ③ → ① 이다. ①은 결정 수의 제곱으로 늘어
    상한(:data:`_CONFLICT_CAP`)을 혼자 채우므로, 드물고 확실한 상충이 그 뒤에서
    잘리지 않게 맨 뒤에 둔다.

    반환 ``(conflicts, truncated)``. 순서는 결정론이다 — 같은 그래프를 두 번 물으면
    같은 순서로 답해야 한다."""
    structural = _flow_conflicts(edges)
    by_title_out: list[dict] = []
    same_subject: list[dict] = []

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
            same_subject.append({"source": a, "target": b, "type": "ABOUT",
                                 "reason": "decisions_on_same_subject", "subject": subj})

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
            by_title_out.append({"source": a, "target": b, "type": "",
                                 "reason": "duplicate_decision_title", "title": titles[a]})

    out = structural + by_title_out + same_subject
    return out[:_CONFLICT_CAP], len(out) > _CONFLICT_CAP


def _flow_conflicts(edges: list[dict]) -> list[dict]:
    """유향 관계를 흐름 방향으로 맞춘 뒤 ②(양방향 쌍)와 ④(셋 이상의 고리)를 낸다.

    순수 함수다. 순서는 정렬로 고정한다: ②는 (source, target) 순, ④는 고리 구성원의
    최소 id 순."""
    # 흐름 (u→v) 마다 그 흐름을 실은 저장 관계 이름들.
    flow: dict[tuple[str, str], set[str]] = {}
    for e in edges:
        rel = str(e.get("type") or "")
        s, t = str(e.get("source") or ""), str(e.get("target") or "")
        if rel not in _DIRECTED_RELS or not s or not t or s == t:
            continue
        u, v = (t, s) if rel in _REVERSED_RELS else (s, t)
        flow.setdefault((u, v), set()).add(rel)

    out: list[dict] = []
    # ② 쌍당 한 번(s < t).
    for (s, t), fwd in sorted(flow.items()):
        back = flow.get((t, s))
        if back and s < t:
            out.append({"source": s, "target": t, "type": sorted(fwd)[0],
                        "reason": "opposite_direction",
                        "reverse_type": sorted(back)[0]})

    # ④ 크기 3 이상의 강연결요소. 2짜리는 ②가 이미 말했다.
    succ: dict[str, list[str]] = {}
    for (u, v) in flow:
        succ.setdefault(u, []).append(v)
        succ.setdefault(v, [])
    for k in succ:
        succ[k].sort()
    for comp in _strongly_connected(succ):
        if len(comp) < 3:
            continue
        members = sorted(comp)
        path = _shortest_cycle(members[0], succ, set(members))
        out.append({"source": path[0], "target": path[1],
                    "type": sorted(flow[(path[0], path[1])])[0],
                    "reason": "cycle", "nodes": path, "members": members})
    return out


def _strongly_connected(succ: dict[str, list[str]]) -> list[list[str]]:
    """반복형 Tarjan. 서브그래프가 depth 4 까지 커질 수 있어 재귀 한도를 피한다.
    노드는 정렬 순으로 방문해 결과가 결정론이다."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comps: list[list[str]] = []
    counter = 0
    for root in sorted(succ):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, i = work[-1]
            nbrs = succ.get(node, [])
            if i < len(nbrs):
                work[-1] = (node, i + 1)
                nxt = nbrs[i]
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                comps.append(comp)
    comps.sort(key=lambda c: min(c))
    return comps


def _shortest_cycle(start: str, succ: dict[str, list[str]], within: set[str]) -> list[str]:
    """`start` 에서 나가 `start` 로 돌아오는 가장 짧은 고리(구성원 안에서만, BFS).
    반환은 `start` 로 시작하고 되돌아오는 마지막 `start` 는 뺀 노드 열이다.
    강연결요소 안이라 고리는 반드시 있다."""
    prev: dict[str, str] = {}
    queue = [start]
    seen = {start}
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        for nxt in succ.get(node, []):
            if nxt not in within:
                continue
            if nxt == start:
                path = [node]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = node
                queue.append(nxt)
    return [start]   # 도달 불가(강연결요소면 일어나지 않는다)
