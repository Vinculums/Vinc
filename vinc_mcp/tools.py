# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

읽기 5(search·recall·graph·decide·domains) + 보조 1(suggest_ties) + 쓰기 1
(ingest — 단일 write 표면). 핸들러는 계약 검증 후 브리지 호출뿐(스켈레톤 §4).
owner 미지정 시 브리지 기본 owner(user:local)를 주입한다 — MCP 호스트는
로컬 소유자 개념을 모른다.
"""
from __future__ import annotations

from .bridge import Bridge

# F1 개방층: type enum 을 제거했다 — 서버가 라우팅 SSoT 이며 이제 *모든* 타입을
# 영속한다(5종 전용 테이블 / 그 외 Entity·TYPED 캐리어). 스키마 수준 거부는
# 개방층을 호스트에서 차단하는 오류였다. 라우팅 행렬은 description 으로 안내.
_FRAGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "description": "Nodes to MERGE by exact id (reuse ids found via vinc_search/vinc_recall first — read-before-write).",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Canonical MERGE key, type-prefixed (e.g. concept:margiela)."},
                    "type": {"type": "string",
                             "description": "record|concept|domain|bridge|episode route to dedicated "
                                            "tables; ANY other type persists to the open Entity carrier "
                                            "and is folded into the theme vocabulary as proposed."},
                    "title": {"type": "string",
                              "description": "Required for open (non-legacy) types."},
                    "domain": {"type": "string", "description": "Free-form domain string (no fixed list). Not persisted on open-type nodes."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "props": {"type": "object", "description": "Free-form extension properties (legacy types only — not persisted on open-type nodes)."},
                },
                "required": ["id", "type"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string",
                             "description": "relates_to|prerequisite_of|derives_from|inherits_from = "
                                            "concept→concept; connects_via = bridge→concept; "
                                            "about = episode→concept; record→concept preserves the "
                                            "type on ATTACHES_TO; between two open-type nodes ANY "
                                            "type persists via the TYPED carrier."},
                    "role": {"type": "string",
                             "description": "Optional CONNECTS role (connects_via only) — participates "
                                            "in the merge key; different roles create parallel edges."},
                },
                "required": ["from", "to", "type"],
            },
        },
        "owner": {"type": "string", "description": "Owner scope to stamp (defaults to the local owner)."},
        "theme_id": {"type": "string",
                     "description": "Theme to fold open-type names into (optional — defaults to the "
                                    "active theme, creating 'default' lazily)."},
    },
    "required": ["nodes"],
}


def _owner(args: dict, bridge: Bridge) -> str:
    return args.get("owner") or bridge.owner


def _t_search(args, bridge):
    # SEC-1: owner 를 반드시 실어 보낸다. 여태 미전송이라 forced_owner 스탬핑이
    # 무의미했고 node_hits 가 남의 Concept/Record 를 그대로 돌려줬다.
    return bridge.request("GET", "/search", params={
        "query": args["query"], "top_k": args.get("limit", 20),
        "owner": _owner(args, bridge)})


def _t_recall(args, bridge):
    return bridge.request("GET", "/recall", params={
        "owner": _owner(args, bridge), "domain": args.get("domain"),
        "tags": ",".join(args["tags"]) if args.get("tags") else None,
        "limit": args.get("limit", 50)})


def _t_graph(args, bridge):
    return bridge.request("GET", "/graph", params={
        "owner": _owner(args, bridge), "seed": args["seed_id"],
        "depth": args.get("depth", 2)})


def _t_decide(args, bridge):
    # SEC-1: owner 미전송이었다 — 토픽 해석부터 남의 개념을 시드로 잡을 수 있었다.
    return bridge.request("GET", "/decide", params={
        "topic": args["topic"], "depth": args.get("depth", 2),
        "owner": _owner(args, bridge)})


def _t_domains(args, bridge):
    return bridge.request("GET", "/domains", params={"owner": _owner(args, bridge)})


def _t_suggest(args, bridge):
    return bridge.request("GET", "/suggest-ties", params={
        "owner": _owner(args, bridge), "node": args["node"],
        "limit": args.get("limit", 5)})


def _t_ingest(args, bridge):
    fragment = {"nodes": args.get("nodes") or [], "edges": args.get("edges") or [],
                "owner": _owner(args, bridge)}
    if args.get("theme_id"):   # F1 — 개방층 어휘 편입 대상 테마(선택)
        fragment["theme_id"] = args["theme_id"]
    return bridge.request("POST", "/ingest/fragment", body=fragment)


def _t_delete(args, bridge):
    # SEC-3: forced_owner(원격)를 body 로 실어 사이드카가 owner 스코프 삭제를 하게
    # 한다. args["owner"] 는 call() 에서 원격이면 forced_owner 로 덮여 있고(멀티테넌트
    # 봉쇄), 로컬 stdio 면 없음 → owner=None → 무스코프(단일유저 현행). bridge.owner
    # 폴백을 **쓰지 않는다**: 로컬을 실수로 스코프해 자기 노드 삭제가 깨지지 않게.
    return bridge.request("POST", "/delete",
                          body={"node": args["node_id"], "owner": args.get("owner")})


def _t_merge(args, bridge):
    # SEC-3: 위와 동일 — owner 를 실어 남의 concept 병합·재지향·삭제를 막는다.
    return bridge.request("POST", "/merge-nodes",
                          body={"keep": args["keep_id"], "drop": args["drop_id"],
                                "owner": args.get("owner")})


def _t_delete_edge(args, bridge):
    # SEC-3: vinc_delete 동형 — 원격은 forced_owner 가 args["owner"] 를 덮고,
    # 로컬 stdio 는 미전송 → 무스코프(단일유저 현행).
    return bridge.request("POST", "/delete-edge",
                          body={"from": args["from_id"], "to": args["to_id"],
                                "type": args["type"], "owner": args.get("owner")})


TOOLS: list[dict] = [
    {
        "name": "vinc_search",
        "description": "Hybrid full-text + vector search over the local Vinc store. "
                       "Returns two result sets: `rows` = text-chunk matches (BM25/vector), "
                       "and `node_hits` = Concept/Record matches on their title/one_liner/props "
                       "(each with id, title, kind, domain, matched_in). `node_hits` is ALWAYS "
                       "populated by a title-substring scan, so concepts and records are findable "
                       "even in a store with zero indexed text chunks. Call this before "
                       "vinc_ingest to find existing node ids to reuse. If the backend search "
                       "fails, a top-level `error` string says why (e.g. index_missing vs "
                       "extension_unavailable) instead of silently returning empty.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "owner": {"type": "string"},
        }, "required": ["query"]},
        "run": _t_search,
    },
    {
        "name": "vinc_recall",
        "description": "List recent episodes in an owner scope, optionally narrowed by domain or tags.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "domain": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50},
        }}, "run": _t_recall,
    },
    {
        "name": "vinc_graph",
        "description": "Subgraph around a seed node (seed + depth traversal) — nodes and edges.",
        "inputSchema": {"type": "object", "properties": {
            "seed_id": {"type": "string"},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "owner": {"type": "string"},
        }, "required": ["seed_id"]}, "run": _t_graph,
    },
    {
        "name": "vinc_decide",
        "description": "Deterministic decision-support traversal for a topic (nodes, edges, conflicts).",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string"},
            "depth": {"type": "integer", "default": 2},
            "owner": {"type": "string"},
        }, "required": ["topic"]}, "run": _t_decide,
    },
    {
        "name": "vinc_domains",
        "description": "List the owner's domains discovered from data (open set, not a fixed list).",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
        }}, "run": _t_domains,
    },
    {
        "name": "vinc_suggest_ties",
        "description": "Suggest link candidates for a node by tag/domain proximity (read-only).",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
            "owner": {"type": "string"},
        }, "required": ["node"]}, "run": _t_suggest,
    },
    {
        "name": "vinc_ingest",
        "description": "MERGE an authored graph fragment (nodes + edges) into the store by exact id. "
                       "The primary write surface (see vinc_delete / vinc_merge_nodes for cleanup). "
                       "Reuse existing ids (read-before-write). Node types record|concept|domain|"
                       "bridge|episode route to dedicated tables; ANY other node type persists to "
                       "the open Entity carrier and is folded into the theme vocabulary as proposed "
                       "(returned in proposed_types for ratification). Edges: relates_to|"
                       "prerequisite_of|derives_from|inherits_from = concept→concept (relates_to "
                       "dedupes the reverse direction); connects_via = bridge→concept (optional "
                       "role); about = episode→concept; record→concept preserves the type on "
                       "ATTACHES_TO; between two open-type nodes ANY edge type persists via the "
                       "TYPED carrier. Only malformed nodes (missing id/title) and unsupported "
                       "(from,to) pairs between legacy types are skipped — reported in warnings "
                       "with partial:true.",
        "inputSchema": _FRAGMENT_SCHEMA,
        "run": _t_ingest,
    },
    {
        "name": "vinc_delete",
        "description": "Irreversibly delete ONE node and all its attached edges by exact id "
                       "(DETACH DELETE). node_id must be an exact, type-prefixed id "
                       "(e.g. concept:ipaas, record:memo, episode:s1, bridge:b1); the prefix "
                       "selects the table. Unknown prefixes return found:false (no-op). Use to "
                       "remove polluted or duplicate shell nodes. This is irreversible — confirm "
                       "the exact id via vinc_search/vinc_graph first. Returns "
                       "{executed, found, deleted:{node, edges}}; found:false if the id is absent.",
        "inputSchema": {"type": "object", "properties": {
            "node_id": {"type": "string",
                        "description": "Exact type-prefixed node id to delete (e.g. concept:ipaas)."},
        }, "required": ["node_id"]},
        "run": _t_delete,
    },
    {
        "name": "vinc_delete_edge",
        "description": "Delete ONE relationship between two nodes (the nodes stay). "
                       "Covers the synced relation types: relates_to | prerequisite_of | "
                       "derives_from | inherits_from between concepts, and about "
                       "(episode→concept). relates_to is undirected — both physical "
                       "directions are removed. The deletion is recorded with an edge "
                       "tombstone so it propagates to synced devices instead of "
                       "resurrecting. Irreversible (re-create via vinc_ingest if needed). "
                       "Returns {executed, found, deleted:{edges}}; found:false if no "
                       "such edge.",
        "inputSchema": {"type": "object", "properties": {
            "from_id": {"type": "string", "description": "Exact source node id (e.g. concept:a, episode:s1)."},
            "to_id": {"type": "string", "description": "Exact target node id (e.g. concept:b)."},
            "type": {"type": "string",
                     "description": "relates_to | prerequisite_of | derives_from | inherits_from | about"},
        }, "required": ["from_id", "to_id", "type"]},
        "run": _t_delete_edge,
    },
    {
        "name": "vinc_merge_nodes",
        "description": "Irreversibly merge two concepts: repoint every edge of drop_id onto keep_id, "
                       "then delete drop_id (its title/props are discarded — keep wins). "
                       "CONCEPT-ONLY in this version: both ids must be concept:* (other type pairs "
                       "are rejected with an error). Self-loop and already-present edges are skipped "
                       "(RELATED_TO is treated as undirected). Use to fold a duplicate concept shell "
                       "into the canonical one. This is irreversible — confirm both exact ids first. "
                       "Returns {executed, found, repointed, skipped, dropped}.",
        "inputSchema": {"type": "object", "properties": {
            "keep_id": {"type": "string", "description": "Concept id to keep (survivor)."},
            "drop_id": {"type": "string", "description": "Concept id to merge in and delete."},
        }, "required": ["keep_id", "drop_id"]},
        "run": _t_merge,
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}

# ── 도구 안전 주석 (MCP annotations) ─────────────────────────────────────────
# Anthropic 원격 MCP 디렉토리 제출 요건 — readOnly/destructive 표식은 **선택이 아니라
# 필수**이며 누락 시 거부된다. 호스트가 이걸로 확인 프롬프트·자동승인 정책을 정한다.
# ⚠ 주석은 **힌트**다(스펙 명시) — 신뢰 경계가 아니다. 실제 강제는 서버측
# forced_owner 스탬핑·쿼터가 한다(http_server.resolve_auth).
#
# destructiveHint 는 스펙상 **기본값이 true** 라, 파괴적이지 않은 쓰기(ingest)는
# 명시적으로 false 를 줘야 호스트가 과하게 겁주지 않는다.
# openWorldHint=false: 전부 닫힌 도메인(내 로컬 스토어 또는 내 계정 그래프)만 만진다.
_ANNOTATIONS: dict[str, dict] = {
    "vinc_search": {"title": "Search knowledge", "readOnlyHint": True},
    "vinc_recall": {"title": "Recall episodes", "readOnlyHint": True},
    "vinc_graph": {"title": "Explore the graph", "readOnlyHint": True},
    "vinc_decide": {"title": "Decision support", "readOnlyHint": True},
    "vinc_domains": {"title": "List domains", "readOnlyHint": True},
    "vinc_suggest_ties": {"title": "Suggest links", "readOnlyHint": True},
    # 쓰기지만 가산적·멱등(exact-id MERGE) — 기존 데이터를 지우지 않는다.
    "vinc_ingest": {"title": "Save to the graph", "readOnlyHint": False,
                    "destructiveHint": False, "idempotentHint": True},
    # 비가역 — 호스트가 반드시 확인을 받게 한다.
    "vinc_delete": {"title": "Delete a node (irreversible)", "readOnlyHint": False,
                    "destructiveHint": True, "idempotentHint": True},
    "vinc_delete_edge": {"title": "Delete a relationship (irreversible)",
                         "readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": True},
    "vinc_merge_nodes": {"title": "Merge two concepts (irreversible)",
                         "readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": False},
}

# A1.9 A2 — 데스크탑 커넥터 1개 통합: 모든 도구가 target 으로 로컬/원격을 고른다.
# 원본 스키마는 깨끗이 두고 specs() 에서 주입한다(9개 스키마 반복 편집 회피).
_TARGET_PROP = {"target": {
    "type": "string", "enum": ["local", "remote"], "default": "local",
    "description": "Which store to act on: 'local' (this machine's Vinc, the default) or "
                   "'remote' (your account graph on mcp.vincs.app). Remote needs a key "
                   "(VINC_REMOTE_MCP_TOKEN); its owner is fixed by that key."}}


def specs() -> list[dict]:
    out = []
    for t in TOOLS:
        sch = t["inputSchema"]
        props = {**sch.get("properties", {}), **_TARGET_PROP}
        spec = {"name": t["name"], "description": t["description"],
                "inputSchema": {**sch, "properties": props}}
        ann = _ANNOTATIONS.get(t["name"])
        if ann:
            spec["annotations"] = dict(ann)
        out.append(spec)
    return out


def call(name: str, args: dict | None, bridge: Bridge,
         forced_owner: str | None = None) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = dict(args or {})                     # 복사 — 호출자 dict 불변, target 제거
    target = (args.pop("target", None) or "local").lower()
    # owner 강제 스탬핑(B-B2): 원격 인증경로가 소유자를 해석했으면 클라이언트가 보낸
    # owner 를 **덮어쓴다** → 한 회원의 토큰으로 남의 스코프에 못 닿는다(멀티테넌트
    # 봉쇄). forced_owner 없음(로컬 stdio/정적)이면 기존 단일유저 동작 유지.
    if forced_owner:
        args["owner"] = forced_owner
    for req in tool["inputSchema"].get("required", []):
        if args.get(req) in (None, ""):
            raise ValueError(f"missing required argument: {req}")
    if target == "remote":
        from . import remote                    # 지연 import — 로컬 경로엔 부담 0
        return remote.call(name, args)
    return tool["run"](args, bridge)
