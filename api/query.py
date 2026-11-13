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
    _LEARNED_MU,
    _LEARNED_SD,
    _LEARNED_T,
    _LEARNED_W,
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
    _brief_learned_p,
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
    _brief_resolve_learned,
    _brief_resolve_v3,
    _brief_rule,
    _brief_rule_colbert,
    _brief_rule_learned,
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
