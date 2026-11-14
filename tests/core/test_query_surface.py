# -*- coding: utf-8 -*-
"""`api.query` 의 이름 표면과 최상위 상태 한 벌.

잠그는 것:
- `dir(api.query)` 에서 던더 이름을 뺀 목록이 아래 :data:`SURFACE` 와 같다. 이 모듈을 여러 파일로
  나눌 때 옮긴 이름은 `api/query.py` 에서 다시 내보내야 한다. 빠지면 `q.X` 로 부르는 호출부가 깨지고,
  이름을 패치하는 테스트와 평가 하네스는 아무것도 바꾸지 못한 채 조용히 돈다.
- 최상위에서 같은 이름을 두 번 묶지 않는다. 뒤의 묶음이 조용히 이기므로 두 벌이 갈라져도 아무도 모른다.

표면은 새 인터프리터에서 잰다. 앞서 돈 테스트가 모듈에 붙인 이름이 섞이지 않게 하려는 것이다.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: `api.query` 가 내놓는 이름 전부(던더 제외, 정렬). 이름을 옮기면 다시 내보내 이 목록을 지킨다.
#: 이름을 일부러 더하거나 지우는 변경만 이 목록을 고친다.
SURFACE = (
    "ALGO_REL_TABLES", "BASIS_CONTENT", "BASIS_RECORDED", "CHUNK_RRF_K",
    "COMMUNITY_REPRESENTATIVE_CAP", "DECIDE_PATHS_MAX", "LAYOUTS", "MEMBER_LIMIT_DEFAULT",
    "MEMBER_LIMIT_MAX", "NODE_SCAN_CAP", "PATH_LIMIT", "PATH_TIE_EPS", "PPR_DEFAULT_WEIGHT",
    "PPR_EDGE_WEIGHTS", "PPR_RECENCY_HALF_LIFE_DAYS", "PPR_RECENCY_WEIGHT", "PREF_WEIGHTS",
    "PRIZE_REACH_CAP", "QueryPlan", "RECALL_ORDER", "SEARCH_POOL_CAP", "SYNC_EDGE_RELS",
    "StoreConfig", "TIE_SCORE_FORMULA", "UNSTAMPED_RUNTIME_NOTE", "WEIGHT_CONFIDENCE",
    "WEIGHT_HOPS", "WIKI_EDGE_RELS", "_ARM_LABEL", "_ATTACH_AXIS", "_BRIEF_BODY_KEYS",
    "_BRIEF_CANDIDATES", "_BRIEF_CHOOSE_EXCERPTS", "_BRIEF_FALLTHROUGH", "_BRIEF_ID_ASCII_RE",
    "_BRIEF_ID_RE", "_BRIEF_MIN_PROP_BODY", "_BRIEF_NOT_OPEN", "_BRIEF_OPEN_ID_RE",
    "_BRIEF_QUOTES_N", "_BRIEF_QUOTE_POOL", "_BRIEF_RESOLVE_N", "_BRIEF_RULES", "_BRIEF_SEMANTIC_NONE_DISTANCE",
    "_BRIEF_TOP_K", "_Branch", "_BriefRule", "_CC_RELS", "_CLAIM_TEXT_KEYS", "_CONCEPT_READ_PATTERN", "_CONE0",
    "_CONE_MAX", "_CONE_MIN", "_CONFLICT_CAP", "_DEPTH_CAP", "_DIRECTED_RELS",
    "_EDGE_CTX_TYPES", "_FIELD_RANK", "_FRAGMENT_EDGE_TO_REL",
    "_FTS_REMNANT", "_GATE_MATCH_ORDER", "_GRAPH_AXIS_PLANS", "_GRAPH_READ_PAIRS",
    "_GRAPH_READ_PLANS", "_HANGUL", "_ID_PREFIX_TOKENS", "_KO_SUFFIXES", "_LAYOUT_ALIAS",
    "_LEDGER_MARKS", "_LOUVAIN_LABELS",
    "_MERGE_TOKEN_RE", "_MON", "_MONTHS", "_NODE_LINKS_CAP", "_NODE_PK", "_NODE_SOURCES_CAP",
    "_NON_CONCEPT_PREFIXES", "_NOT_A_SUBJECT", "_OWNER_SCOPED_LABELS",
    "_OWNER_STAT_LABELS", "_OWNER_STAT_NODE_KEYS", "_PATH_TOKEN", "_Q_TIME", "_REVERSED_RELS",
    "_RUNTIME_STAMP_OWNERS", "_R_BRANCH", "_R_LEAF", "_SCOPE_VALUE", "_SEED_FIELDS", "_SEED_TABLES",
    "_SEMANTIC_LABELS", "_SEMANTIC_PK", "_SEM_GAP", "_SEM_PROMOTE", "_SEM_PROMOTE_XLANG", "_SHA",
    "_STOP_TOKENS", "_STRICT_MARGIN", "_SYNCED_EDGE_RELS", "_TIE_DOMAIN_W", "_TIE_LINK_CAP",
    "_TIE_LINK_W", "_TIE_TAG_MAX", "_TREE_KIND_LABEL", "_TREE_PK", "_URLISH",
    "_UnscopedChunkSearch", "_V3_MU", "_V3_SD", "_V3_T", "_V3_W", "_VALIDITY_EMPTY",
    "_VECTOR_REBUILDING", "_VECTOR_REMNANT", "_VECTOR_WIDTH_MISMATCH", "_WIKI_READ_PAIRS",
    "_algo_graph", "_ambiguous_algo_ids", "_apply_basis", "_arc_gap", "_attach_doc_titles",
    "_attach_reverse_cypher", "_bridge_owner_pred", "_brief_answer",
    "_brief_attach_section", "_brief_body", "_brief_by_id", "_brief_candidate_material",
    "_brief_candidate_rows", "_brief_current_uncertain", "_brief_entity_ids", "_brief_excerpt_kept",
    "_brief_expose_n", "_brief_finish_choose", "_brief_finish_resolved",
    "_brief_grounds", "_brief_hidden_ties", "_brief_holds", "_brief_int", "_brief_known",
    "_brief_learned_features", "_brief_list_rank", "_brief_list_resolved_on",
    "_brief_material", "_brief_mirror_text", "_brief_node_quotes", "_brief_norm",
    "_brief_pick_chunk", "_brief_query_ids", "_brief_quotes_on",
    "_brief_rank", "_brief_rank_path", "_brief_resolve", "_brief_resolve_by_production", "_brief_resolve_by_v3",
    "_brief_resolve_v3", "_brief_rule", "_brief_rule_in_force", "_brief_scope_material",
    "_brief_section", "_brief_section_on", "_brief_sem_promote_on", "_brief_semantic_none",
    "_brief_semantic_tie_clear", "_brief_strict_on", "_brief_token_decisive", "_brief_v3_features",
    "_brief_v3_p", "_brief_v3_prod_head", "_brief_xlang_lead", "_build_tree", "_chunk_arms",
    "_chunk_count", "_cite_doc_rows", "_clamp_limit", "_classify_chunk_error", "_cluster",
    "_common_prefix", "_concept_in_mark", "_concept_only_refusal", "_conf", "_current_decisions",
    "_decide_conflicts", "_dedupe_planar", "_delete_preview_edges", "_derive_bridge_domains",
    "_derive_sync_edge_rels", "_doc_cite_ids", "_domain_context", "_domain_depths",
    "_domain_neighbors", "_domain_order", "_domain_path", "_domain_rows", "_domains_for_ids",
    "_drop_node_shadows", "_drop_unstarted_episodes", "_dt", "_edge_cost",
    "_encoder_runtime_warning", "_entity_context", "_entity_links", "_episode_links", "_excerpt",
    "_flow_conflicts", "_fuse_node_hits", "_gate_related_decisions", "_gate_subjects",
    "_gate_validity", "_global_hub", "_global_rank", "_graph_arm_search", "_graph_read_pairs",
    "_id_token", "_is_structural_nonduplicate", "_ledger_by_concept", "_ledger_classify",
    "_ledger_ego_ids", "_mark_topic_descriptions", "_mean_angle", "_merge_rejection_rows",
    "_node_context", "_node_date", "_node_fallback_search", "_node_links", "_node_sem_rank_on",
    "_node_semantic_arm_on", "_node_seq", "_norm_title", "_order_graph_result", "_ordered_children",
    "_owner_concept_ids", "_owner_fts_ids", "_pad_date", "_path_covers", "_path_hint",
    "_prize_pick", "_probe_node", "_props_to_obj", "_purge_labels", "_purge_owner_theme",
    "_purge_sync_base", "_purge_sync_meta", "_query_fingerprint", "_recall_fingerprint",
    "_recall_refusal", "_record_links", "_rejected_set", "_retired_claim", "_retrieval",
    "_row_validity", "_run_algo", "_same_endpoint_path", "_schedule_vector_index", "_seed_table",
    "_shared_prefix", "_shell", "_shortest_cycle", "_stale_claims", "_stamp_validity",
    "_store_scope", "_stored_runtimes", "_strip_ko_suffix", "_strongly_connected",
    "_structural_role",
    "_summary_row", "_tag_weights", "_today", "_topic_description_mirrors",
    "_topic_description_mirrors_by_domain", "_track_cols", "_typed_neighbors_cypher",
    "_unknown_node_refusal", "_validity_cols", "_validity_for_ids", "_visible",
    "_weighted_shortest_path", "_wiki_read_pairs", "_window", "_write_edge_tombstone",
    "_write_tombstone", "annotations", "attach_ppr", "bridge_coupling_plan",
    "bridge_neighbors_plan", "bridge_rows_plan", "bridge_ties_plan", "brief", "brief_reads_by_id",
    "claim_diagnosis", "communities", "community_context", "component_diagnosis",
    "concept_domains_plan", "concept_edge_ids_plan", "concept_meta_plan", "coupling",
    "cursor_decode", "cursor_encode", "decide", "decide_paths", "decide_plan",
    "decide_topic_resolve_plan", "decision_relation_rows_plan", "decision_rows_plan",
    "decision_seed_rows_plan", "decisions_about_plan", "delete_edge", "delete_node",
    "direct_coupling_plan", "docs_in_range", "domain_counts_plan", "domain_hierarchy",
    "domain_names", "domain_names_plan", "domain_root_diagnosis", "domain_subtree_where",
    "domain_ties_plan", "domains", "domains_plan", "edge_context", "entity_neighbors_plan",
    "entity_rows_for_ids_plan", "entity_subgraph_ids_plan", "episode_rows_plan", "execute", "graph",
    "graph_plan", "graph_preview", "graph_rows_for_ids_plan", "graphmd", "holds_during",
    "itertools", "json", "layout_positions", "learner_model", "learner_model_plan", "ledger",
    "ledger_drilldown", "ledger_link_plan", "ledger_origin_plan", "list_merge_rejections",
    "load_config", "math", "merge_candidates", "merge_candidates_detail", "merge_nodes", "nav_tree",
    "near_nodes", "neighbors", "neighbors_plan", "node", "node_attachments_plan", "node_context",
    "node_edges", "node_plan", "normalize_domains", "normalize_query_tokens", "open_store", "os",
    "owner_stats", "path_candidates_plan", "pkns", "ppr_recent", "ppr_scores", "prize_subgraph",
    "promote_candidates", "promote_candidates_plan", "purge_owner", "query_time", "re", "recall",
    "recall_plan", "record_rows_plan", "record_supersedes_plan", "reject_merge_pair",
    "rejection_pair_key", "rename_domain", "resolve_as_of", "retrieval_overrides",
    "retrieval_switches", "rrf_fuse", "score_preference", "search", "seed_lookup_plan",
    "shape_graph", "shortest_path", "shortest_path_plan", "subgraph_hops_plan", "subgraph_ids_plan",
    "suggest_merge_candidates", "suggest_ties", "sync_edge_spec", "time_scope", "truthy",
    "validity_state", "view_l0", "weighted_paths", "wiki_pairs_open",
)

_PROBE = ("import json, api.query as q; print(json.dumps(sorted("
          "n for n in dir(q) if not (n.startswith('__') and n.endswith('__')))))")


def _surface() -> list[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_the_pinned_list_is_sorted_and_has_no_repeats():
    assert list(SURFACE) == sorted(set(SURFACE))


def test_api_query_keeps_every_pinned_name_and_adds_none():
    got, want = set(_surface()), set(SURFACE)
    missing, extra = sorted(want - got), sorted(got - want)
    assert not missing, (f"api.query 에서 사라진 이름 {missing}. 다른 모듈로 옮겼다면 "
                         "api/query.py 에서 다시 내보낸다.")
    assert not extra, (f"api.query 에 새로 생긴 이름 {extra}. 일부러 더한 것이면 SURFACE 에 넣는다.")


def _names(target: ast.expr):
    """대입 대상이 묶는 이름. `X[k] = v`·`X.a = v` 는 X 를 다시 묶지 않으므로 세지 않는다."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _names(elt)
    elif isinstance(target, ast.Starred):
        yield from _names(target.value)


def _bound(stmt: ast.stmt):
    """최상위 문장 하나가 모듈에 묶는 이름들. 복합문(if·try) 안은 보지 않는다."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield stmt.name
    elif isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            yield from _names(target)
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        yield stmt.target.id
    elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for alias in stmt.names:
            yield alias.asname or alias.name.split(".")[0]


def test_no_top_level_name_is_bound_twice():
    tree = ast.parse((ROOT / "api" / "query.py").read_text(encoding="utf-8"))
    lines: dict[str, list[int]] = {}
    for stmt in tree.body:
        for name in _bound(stmt):
            lines.setdefault(name, []).append(stmt.lineno)
    twice = {name: at for name, at in lines.items() if len(at) > 1}
    assert not twice, f"api/query.py 최상위에서 두 번 묶인 이름(이름: 줄): {twice}. 한 벌만 둔다."
