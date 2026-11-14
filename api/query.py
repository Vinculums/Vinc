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
    domain_names,
    domain_names_plan,
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
    _store_scope,
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
    _BRIEF_RULES,
    _BRIEF_SEMANTIC_NONE_DISTANCE,
    _BRIEF_TOP_K,
    _BriefRule,
    _HANGUL,
    _SEM_PROMOTE,
    _SEM_PROMOTE_XLANG,
    _STRICT_MARGIN,
    _V3_MU,
    _V3_SD,
    _V3_T,
    _V3_W,
    _brief_answer,
    _brief_attach_section,
    _brief_body,
    _brief_by_id,
    _brief_candidate_material,
    _brief_candidate_rows,
    _brief_current_uncertain,
    _brief_entity_ids,
    _brief_excerpt_kept,
    _brief_expose_n,
    _brief_finish_choose,
    _brief_finish_resolved,
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
    _brief_pick_chunk,
    _brief_query_ids,
    _brief_quotes_on,
    _brief_rank,
    _brief_rank_path,
    _brief_resolve,
    _brief_resolve_by_production,
    _brief_resolve_by_v3,
    _brief_resolve_v3,
    _brief_rule,
    _brief_rule_in_force,
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

# 잇기 제안(`suggest_ties` 와 그 점수 산식, 메타·브리지 이웃 계획)은 ties 에 있다. 같은 이름을 여기서 다시
# 내보내므로 호출부와 `q.X` 는 그대로다.
from .ties import (  # noqa: E402,F401  (재수출)
    TIE_SCORE_FORMULA,
    _TIE_DOMAIN_W,
    _TIE_LINK_CAP,
    _TIE_LINK_W,
    _TIE_TAG_MAX,
    _tag_weights,
    bridge_neighbors_plan,
    concept_meta_plan,
    suggest_ties,
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
