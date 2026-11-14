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

# 검색 파사드(search, 인용 id)와 노드 상세(node_context, 관계·문서 목록)는 node_read 에 있다. 같은
# 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .node_read import (  # noqa: E402,F401  (재수출)
    _NODE_LINKS_CAP,
    _NODE_SOURCES_CAP,
    _cite_doc_rows,
    _conf,
    _doc_cite_ids,
    _domain_context,
    _drop_unstarted_episodes,
    _entity_context,
    _entity_links,
    _episode_links,
    _node_context,
    _node_links,
    _owner_fts_ids,
    _record_links,
    _window,
    node_context,
    search,
)

# 브리프의 해소 규칙(직참조·플래그·순위·확정과 본문·근거·노출 수)은 brief 에 있다. 같은 이름을
# 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .brief import (  # noqa: E402,F401  (재수출)
    _BRIEF_BODY_KEYS,
    _BRIEF_CANDIDATES,
    _BRIEF_CHOOSE_EXCERPTS,
    _BRIEF_FALLTHROUGH,
    _BRIEF_ID_ASCII_RE,
    _BRIEF_ID_RE,
    _BRIEF_MIN_PROP_BODY,
    _BRIEF_NOT_OPEN,
    _BRIEF_OPEN_ID_RE,
    _BRIEF_QUOTES_N,
    _BRIEF_QUOTE_POOL,
    _BRIEF_RESOLVE_N,
    _BRIEF_SEMANTIC_NONE_DISTANCE,
    _BRIEF_TOP_K,
    _HANGUL,
    _ORPHAN_BLOCK,
    _SEM_PROMOTE,
    _SEM_PROMOTE_XLANG,
    _STRICT_MARGIN,
    _V3_MU,
    _V3_SD,
    _V3_T,
    _V3_W,
    _brief_answer,
    _brief_answerability_on,
    _brief_attach_section,
    _brief_body,
    _brief_candidate_material,
    _brief_candidate_rows,
    _brief_current_uncertain,
    _brief_entity_ids,
    _brief_excerpt_kept,
    _brief_expose_n,
    _brief_grounds,
    _brief_hidden_ties,
    _brief_holds,
    _brief_int,
    _brief_known,
    _brief_learned_features,
    _brief_list_rank,
    _brief_list_resolved_on,
    _brief_material,
    _brief_mirror_text,
    _brief_node_quotes,
    _brief_norm,
    _brief_orphan_residual,
    _brief_pick_chunk,
    _brief_query_ids,
    _brief_quotes_on,
    _brief_rank,
    _brief_resolve,
    _brief_resolve_v3,
    _brief_rule,
    _brief_rule_colbert,
    _brief_rule_v3,
    _brief_scope_material,
    _brief_section,
    _brief_section_on,
    _brief_sem_promote_on,
    _brief_semantic_none,
    _brief_semantic_tie_clear,
    _brief_strict_on,
    _brief_token_decisive,
    _brief_v3_features,
    _brief_v3_p,
    _brief_v3_prod_head,
    _brief_xlang_lead,
    brief,
    brief_reads_by_id,
    retrieval_overrides,
    retrieval_switches,
)

# 보기 화면의 읽기(관계 선의 근거, 좌측 문서 트리, 기간 항목, 원장과 드릴다운)는 nav 에 있다. 같은
# 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .nav import (  # noqa: E402,F401  (재수출)
    _DEPTH_CAP,
    _EDGE_CTX_TYPES,
    _LEDGER_MARKS,
    _TREE_KIND_LABEL,
    _TREE_PK,
    _concept_in_mark,
    _domain_depths,
    _ledger_by_concept,
    _ledger_classify,
    docs_in_range,
    edge_context,
    ledger,
    ledger_drilldown,
    nav_tree,
)

# 병합 후보 제안과 기각 기억(제목 정규화, 구조적 비중복, 기각 표 읽기·쓰기)은 merge_candidates 에 있다.
# 같은 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .merge_candidates import (  # noqa: E402,F401  (재수출)
    _MERGE_TOKEN_RE,
    _is_structural_nonduplicate,
    _merge_rejection_rows,
    _norm_title,
    _rejected_set,
    _structural_role,
    list_merge_rejections,
    merge_candidates,
    merge_candidates_detail,
    reject_merge_pair,
    rejection_pair_key,
    suggest_merge_candidates,
)

# 구조 진단(주제 뿌리·연결 성분·주장)과 Leiden 커뮤니티 읽기는 diagnose 에 있다. 같은 이름을 여기서 다시
# 내보내므로 호출부와 `q.X` 는 그대로다.
from .diagnose import (  # noqa: E402,F401  (재수출)
    ALGO_REL_TABLES,
    COMMUNITY_REPRESENTATIVE_CAP,
    MEMBER_LIMIT_DEFAULT,
    MEMBER_LIMIT_MAX,
    _CLAIM_TEXT_KEYS,
    _LOUVAIN_LABELS,
    _NOT_A_SUBJECT,
    _PATH_TOKEN,
    _SHA,
    _URLISH,
    _algo_graph,
    _ambiguous_algo_ids,
    _clamp_limit,
    _owner_concept_ids,
    _path_covers,
    _path_hint,
    _retired_claim,
    _run_algo,
    _stale_claims,
    _topic_description_mirrors,
    claim_diagnosis,
    communities,
    community_context,
    component_diagnosis,
    domain_root_diagnosis,
)

# 결정 읽기(경로를 덮는 결정 `decide_paths`, 주제 둘레의 결정 지원 `decide`, 상충 검사)는 decide_read 에 있다.
# 같은 이름을 여기서 다시 내보내므로 호출부와 `q.X` 는 그대로다.
from .decide_read import (  # noqa: E402,F401  (재수출)
    DECIDE_PATHS_MAX,
    _CONFLICT_CAP,
    _GATE_MATCH_ORDER,
    _current_decisions,
    _decide_conflicts,
    _flow_conflicts,
    _gate_related_decisions,
    _gate_subjects,
    _gate_validity,
    _shortest_cycle,
    _strongly_connected,
    decide,
    decide_paths,
    decide_topic_resolve_plan,
    decisions_about_plan,
)


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
