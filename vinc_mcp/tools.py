# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

**19종** — 읽기 13(search·recall·graph·decide·domains·suggest_ties·neighbors·
path·merge_candidates·diagnose·doc_get·doc_list·conventions) + 쓰기 6(ingest·delete·
delete_edge·merge_nodes·doc_put·doc_delete). "단일 write 표면(ingest 하나)"은
SEC-3(삭제·병합)과 D45(문서 정본 doc_*)를 거치며 깨진 낡은 서술이었다 —
아래 TOOLS 리스트가 정본이고 이 독스트링은 그 요약일 뿐이다.

주제·어휘(themes·types)의 **뮤테이션**(재가·거부·개명)은 HTTP 전용이며 MCP 에 노출하지
않는다 — 어시스턴트는 제안만 하고 자기 제안을 스스로 재가할 수 없다(의도된 비대칭).
읽기 쪽은 `vinc_conventions` 가 관례 문서와 함께 재가된 어휘를 실어 나른다(GM-1):
그 전까지 어시스턴트는 어휘를 **쓰고 나서** `proposed_types` 로만 알 수 있었다.
핸들러는 계약 검증 후 브리지 호출뿐(스켈레톤 §4). owner 미지정 시 브리지
기본 owner(user:local)를 주입한다 — MCP 호스트는 로컬 소유자 개념을 모른다.
"""
from __future__ import annotations

import base64
import json

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
                    "evidence": {"type": "string",
                                 "description": "Optional source sentence backing this relation (E-G6) — "
                                                "concept↔concept edges and about only."},
                    "confidence": {"type": "number",
                                   "description": "Optional relation confidence 0.0–1.0 (E-G6)."},
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
    # GM-14: cursor/detail 은 그대로 통과(None 은 Bridge 가 걸러 미전송) — 해석은
    # 사이드카 한 곳에서만 한다(CLI·HTTP·MCP 동등성).
    return bridge.request("GET", "/search", params={
        "query": args["query"], "top_k": args.get("limit", 20),
        "cursor": args.get("cursor"), "detail": args.get("detail"),
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


def _t_neighbors(args, bridge):
    # E-G5 단일 홉 관측(SoG) — 개념의 1-hop 관계망(조회 전용).
    return bridge.request("GET", "/neighbors", params={
        "owner": _owner(args, bridge), "node": args["node"],
        "limit": args.get("limit", 50)})


def _t_path(args, bridge):
    # E-G7/E-G8 최단 경로 증거 체인 — 홉 최단(기본) 또는 confidence 가중(조회 전용).
    return bridge.request("GET", "/path", params={
        "owner": _owner(args, bridge), "a": args["a"], "b": args["b"],
        "max_depth": args.get("max_depth", 6),
        "weight": args.get("weight", "hops")})


def _t_merge_candidates(args, bridge):
    # E-G4 병합 후보(조회 전용) — 확정은 vinc_merge_nodes 경유(자동 병합 금지 T1).
    return bridge.request("GET", "/merge-candidates", params={
        "owner": _owner(args, bridge), "limit": args.get("limit", 20),
        "min_score": args.get("min_score", 0.6)})


def _t_diagnose(args, bridge):
    # E-G3 구조 진단(조회 전용) — kind=components(WCC 고아·컴포넌트) | communities
    # (Louvain 군집). algo 확장 부재 시 사이드카가 executed=False 로 강등.
    return bridge.request("GET", "/diagnose", params={
        "owner": _owner(args, bridge), "kind": args.get("kind", "components")})


def _t_ingest(args, bridge):
    fragment = {"nodes": args.get("nodes") or [], "edges": args.get("edges") or [],
                "owner": _owner(args, bridge)}
    if args.get("theme_id"):   # F1 — 개방층 어휘 편입 대상 테마(선택)
        fragment["theme_id"] = args["theme_id"]
    # 주제 상한(AP-1 §3.4). call() 이 서버 해석값으로 이미 덮어썼으므로 여기 있는
    # 값은 신뢰할 수 있다. 이 핸들러가 fragment 를 **새로 조립**하기 때문에 명시로
    # 실어야 한다 — args 를 그대로 흘려보내는 구조가 아니다.
    if isinstance(args.get("_entitlements"), dict):
        fragment["_entitlements"] = args["_entitlements"]
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


# ── 문서 정본(Chunk=SSoT, D45 P4) — 편집 표면 도구 ────────────────────────────
def _t_doc_get(args, bridge):
    return bridge.request("GET", "/doc", params={
        "id": args["doc_id"], "owner": _owner(args, bridge)})


def _t_doc_list(args, bridge):
    return bridge.request("GET", "/docs", params={"owner": _owner(args, bridge)})


def _t_doc_put(args, bridge):
    # 편집 왕복: doc_id 있으면 갱신(재청킹), 없으면 신규(반환 doc_id). SEC-1: owner 강제.
    return bridge.request("POST", "/doc/put", body={
        "owner": _owner(args, bridge), "text": args["text"],
        "id": args.get("doc_id"), "title": args.get("title"),
        "origin": args.get("origin")})


def _t_conventions(args, bridge):
    # 관례(GRAPH.md) — 쓰기 전에 읽는 채널. 사이드카가 문서본/기본본을 알아서 고른다.
    return bridge.request("GET", "/conventions", params={
        "owner": _owner(args, bridge), "lang": args.get("lang")})


def _t_doc_delete(args, bridge):
    return bridge.request("POST", "/doc/delete", body={
        "id": args["doc_id"], "owner": _owner(args, bridge)})


TOOLS: list[dict] = [
    {
        "name": "vinc_search",
        "description": "Hybrid full-text + vector search over the local Vinc store. "
                       "Returns two result sets: `rows` = text-chunk matches (BM25/vector) "
                       "carrying an excerpt (`snippet`, `truncated`, `doc_id`) rather than the "
                       "full chunk text — fetch the whole document with vinc_doc_get(doc_id); "
                       "pass detail:'full' only when you truly need raw chunk text. "
                       "`node_hits` = Concept/Record matches on their title/one_liner/props "
                       "(each with id, title, kind, domain, matched_in), ALWAYS populated by a "
                       "title-substring scan, so concepts and records are findable even in a "
                       "store with zero indexed text chunks. Responses are paginated: "
                       "`total_count`, `has_more` and `next_cursor` describe the result window; "
                       "pass `cursor` to continue where the last page ended (an invalid or "
                       "expired cursor errors explicitly instead of restarting from page one). "
                       "Call this before vinc_ingest to find existing node ids to reuse. If the "
                       "backend search fails, a top-level `error` string says why (e.g. "
                       "index_missing vs extension_unavailable) instead of silently returning "
                       "empty.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "cursor": {"type": "string",
                       "description": "Opaque next_cursor from a previous page."},
            "detail": {"type": "string", "enum": ["summary", "full"],
                       "default": "summary"},
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
        "name": "vinc_neighbors",
        "description": "Observe ONE concept's immediate (1-hop) relation network — a lightweight "
                       "navigation primitive (E-G5, Search-on-Graph style). Returns {neighbors:"
                       "[{rel,id,title,domain}], by_rel:{type:count}, count}. Use this to walk the "
                       "graph one hop at a time (observe → pick next node) instead of pulling a whole "
                       "depth-2 subgraph via vinc_graph — far fewer tokens per step. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Exact concept id to observe (e.g. concept:duality)."},
            "limit": {"type": "integer", "default": 50},
            "owner": {"type": "string"},
        }, "required": ["node"]}, "run": _t_neighbors,
    },
    {
        "name": "vinc_path",
        "description": "Find the shortest relation path(s) between two concepts — the physically "
                       "connected edge chain, returned as an ordered id/title sequence. Use this to "
                       "give a model concrete evidence of HOW two concepts connect instead of "
                       "guessing. Two distance measures, chosen with `weight`: 'hops' (default, "
                       "E-G7) counts edges and returns ALL shortest paths (ties included); "
                       "'confidence' (E-G8) treats each edge's recorded confidence as closeness "
                       "(cost = 1 - confidence) and returns the best-evidenced chain, which may be "
                       "LONGER in hops but better supported — each path then also carries `cost` "
                       "(lower is stronger). With no confidence recorded anywhere, 'confidence' "
                       "degenerates exactly to 'hops'. Returns {found, length, weight, "
                       "paths:[{ids,titles,length,[cost]}], count}. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "a": {"type": "string", "description": "Exact source concept id."},
            "b": {"type": "string", "description": "Exact target concept id."},
            "max_depth": {"type": "integer", "default": 6, "description": "Max path length (1–8)."},
            "weight": {"type": "string", "enum": ["hops", "confidence"], "default": "hops",
                       "description": "Distance measure: edge count, or evidence strength."},
            "owner": {"type": "string"},
        }, "required": ["a", "b"]}, "run": _t_path,
    },
    {
        "name": "vinc_merge_candidates",
        "description": "Suggest duplicate-concept merge candidates for the owner (read-only, E-G4). "
                       "Deterministic title similarity (exact normalized title, token Jaccard, or "
                       "subset) — NO automatic merge, NO LLM judgement (similarity merge is "
                       "permanently forbidden; the system only proposes). Returns {candidates:"
                       "[{a_id,a_title,b_id,b_title,score,reason,same_domain}], count}, same-domain "
                       "pairs first. Confirm a real duplicate by calling vinc_merge_nodes with the "
                       "chosen keep/drop ids (irreversible).",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20},
            "min_score": {"type": "number", "default": 0.6,
                          "description": "Token-Jaccard threshold (0-1) for token_overlap pairs."},
            "owner": {"type": "string"},
        }}, "run": _t_merge_candidates,
    },
    {
        "name": "vinc_diagnose",
        "description": "Structural diagnosis of the owner's concept graph (read-only, E-G3). "
                       "kind='components' (default) runs weakly-connected-components: returns "
                       "{components:[{group,size,members}], orphans:[id…], component_count, "
                       "orphan_count} — the disconnected 'islands' and singleton concepts that "
                       "want linking (feeds vinc_suggest_ties). kind='communities' runs Louvain: "
                       "returns {communities:[{community,size,dominant_domain,members}]} — the "
                       "measured clusters (distinct from the domain axis), a basis for proposing "
                       "themes (ratified by the user, never auto-written). Delegates to the native "
                       "algo extension; executed:false with a note if the extension is unavailable "
                       "(offline unbundled).",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["components", "communities"],
                     "description": "components (WCC orphans/islands) | communities (Louvain clusters)."},
            "owner": {"type": "string"},
        }}, "run": _t_diagnose,
    },
    {
        "name": "vinc_conventions",
        "description": "Read this graph's house style BEFORE your first write in a session. "
                       "Returns the owner's GRAPH.md: node kinds, relation vocabulary and "
                       "direction, how big one concept should be, the domains that already "
                       "exist, the ratified type vocabulary, and what not to do. If the owner "
                       "has not written one, a default is returned with the domain list and "
                       "ratified vocabulary filled in from their store (source:\"default\", "
                       "missing:true) — those two are worth following even then. This is the "
                       "only way to see the ratified vocabulary: proposing an unlisted type is "
                       "allowed, but it lands as proposed and waits for a human.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "lang": {"type": "string", "enum": ["en", "ko"],
                     "description": "Language of the default template (ignored when the "
                                    "owner has written their own)."},
        }},
        "run": _t_conventions,
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
                       "with partial:true. Call vinc_conventions once before your first write "
                       "in a session: it carries this owner's node/relation conventions and "
                       "their existing domains, so your writes match what is already there.",
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
    # ── 문서 정본(Chunk=SSoT, D45 P4) — 원문 텍스트 직접 CRUD ──────────────────
    {
        "name": "vinc_doc_put",
        "description": "Store or edit a document's canonical text (Chunk=SSoT). Omit doc_id to "
                       "create (the returned doc_id identifies it); pass doc_id to edit in place "
                       "(re-chunked losslessly, stale chunks removed). The store IS the source of "
                       "truth — no external file is needed. Returns {doc_id, chunks, replaced, "
                       "content_hash, skipped}. Unchanged text is a no-op (skipped=true).",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Full document text (the canonical content)."},
            "doc_id": {"type": "string", "description": "Existing doc to edit; omit to create."},
            "title": {"type": "string"},
            "origin": {"type": "string", "description": "Provenance label only (not a join key)."},
        }, "required": ["text"]},
        "run": _t_doc_put,
    },
    {
        "name": "vinc_doc_get",
        "description": "Reconstruct a document's canonical text from its chunks, with a `verified` "
                       "flag (offset-contiguous + content-hash match). Returns {text, title, "
                       "verified, chunks, updated_at}.",
        "inputSchema": {"type": "object", "properties": {
            "doc_id": {"type": "string"},
        }, "required": ["doc_id"]},
        "run": _t_doc_get,
    },
    {
        "name": "vinc_doc_list",
        "description": "List the owner's documents (doc_id, title, origin, chunk count, updated_at).",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
        }},
        "run": _t_doc_list,
    },
    {
        "name": "vinc_doc_delete",
        "description": "Delete a document — its chunks and identity — with deletion propagated to "
                       "other devices on sync. Irreversible; confirm the exact doc_id first. "
                       "Returns {executed, deleted_chunks, deleted_meta}.",
        "inputSchema": {"type": "object", "properties": {
            "doc_id": {"type": "string"},
        }, "required": ["doc_id"]},
        "run": _t_doc_delete,
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
    # 관례(GRAPH.md) — 조회 전용. 쓰기 전에 읽는 것이 이 도구의 존재 이유다.
    "vinc_conventions": {"title": "Read the graph conventions", "readOnlyHint": True},
    "vinc_suggest_ties": {"title": "Suggest links", "readOnlyHint": True},
    # E-G3/G4/G5/G7 — 전부 조회 전용(진단·제안·관측·경로). 쓰기 아님.
    "vinc_diagnose": {"title": "Diagnose graph structure", "readOnlyHint": True},
    "vinc_merge_candidates": {"title": "Suggest merge candidates", "readOnlyHint": True},
    "vinc_neighbors": {"title": "Observe a node's neighbors", "readOnlyHint": True},
    "vinc_path": {"title": "Shortest path between concepts", "readOnlyHint": True},
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
    # 문서 정본(D45 P4). get/list 는 읽기; put 은 비파괴 쓰기(내용 해시 멱등 —
    # 같은 텍스트 재-put 은 no-op); delete 는 비가역(청크+정체성 DETACH).
    "vinc_doc_get": {"title": "Read a document (canonical text)", "readOnlyHint": True},
    "vinc_doc_list": {"title": "List documents", "readOnlyHint": True},
    "vinc_doc_put": {"title": "Save or edit a document", "readOnlyHint": False,
                     "destructiveHint": False, "idempotentHint": True},
    "vinc_doc_delete": {"title": "Delete a document (irreversible)", "readOnlyHint": False,
                        "destructiveHint": True, "idempotentHint": True},
}

# A1.9 A2 — 데스크탑 커넥터 1개 통합: 모든 도구가 target 으로 로컬/원격을 고른다.
# 원본 스키마는 깨끗이 두고 specs() 에서 주입한다(9개 스키마 반복 편집 회피).
_TARGET_PROP = {"target": {
    "type": "string", "enum": ["local", "remote"], "default": "local",
    "description": "Which store to act on: 'local' (this machine's Vinc, the default) or "
                   "'remote' (your account graph on mcp.vincs.io). Remote needs a key "
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


#: 주제 상한이 걸리는 도구 — 새 주제를 만들 수 있는 쓰기 표면(AP-1 §3.3).
#: 조회·삭제·병합은 주제를 **늘릴 수 없어** 대상이 아니다.
CAPPED_TOOLS = ("vinc_ingest",)


# ── 결과 크기 축소기(GM-14) ──────────────────────────────────────────────────
# 봉투(rpc._result_text)가 상한 초과 결과를 **거부하는 대신** 여기 등록된 도구는
# 꼬리 행을 덜어 페이지로 만든다. vinc_mcp 는 api 패키지를 임포트하지 않으므로
# (독립 브리지 계약) 커서 코덱을 여기 미러한다 — 두 사본의 왕복 호환은
# tests/test_search_pagination.py 의 교차 계약 테스트가 고정한다.

def _cursor_encode(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _shrink_search_result(out: dict, budget: int) -> dict | None:
    """vinc_search 결과를 예산 안으로 줄인다 — 실패(줄일 수 없음)면 None.

    사이드카가 준 `page`(offset·fingerprint) 좌표로 next_cursor 를 다시 써서,
    덜어낸 꼬리를 다음 페이지로 이어 읽을 수 있게 한다. `page` 가 없으면(구
    사이드카) 축소를 포기하고 기존 거부 경로로 돌려보낸다."""
    if not isinstance(out, dict):
        return None
    rows = out.get("rows")
    page = out.get("page")
    if not isinstance(rows, list) or not rows or not isinstance(page, dict):
        return None
    if not page.get("fingerprint"):
        return None
    dropped: list = []
    while rows and len(json.dumps(out, ensure_ascii=False)) > budget:
        dropped.insert(0, rows.pop())
    if not rows:
        return None                     # 행 1개조차 예산을 넘는다 — 거부가 정직하다
    if dropped:
        out["has_more"] = True
        out["next_cursor"] = _cursor_encode(
            {"v": 1, "off": int(page.get("offset") or 0) + len(rows),
             "qh": page.get("fingerprint"),
             "eid": str(dropped[0].get("id") or ""),
             "tc": out.get("total_count")})
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + \
            "page shrunk to fit the result size limit; continue with next_cursor"
    return out


#: 봉투가 참조하는 도구별 축소기 레지스트리. 미등재 도구는 기존 거부 유지
#: (vinc_graph 등 — JSON 을 중간에서 자르면 깨진 구문이 된다).
SHRINKERS = {"vinc_search": _shrink_search_result}


def call(name: str, args: dict | None, bridge: Bridge,
         forced_owner: str | None = None,
         entitlements: dict | None = None) -> dict:
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
    # 자격(주제 상한)도 owner 와 같은 취급 — 클라이언트가 보낸 값을 신뢰하지 않고
    # 서버가 해석한 값으로 **덮어쓴다**. 자격이 없으면 키 자체를 지운다(위조 차단).
    args.pop("_entitlements", None)
    if entitlements and name in CAPPED_TOOLS:
        args["_entitlements"] = dict(entitlements)
    for req in tool["inputSchema"].get("required", []):
        if args.get(req) in (None, ""):
            raise ValueError(f"missing required argument: {req}")
    if target == "remote":
        from . import remote                    # 지연 import — 로컬 경로엔 부담 0
        return remote.call(name, args)
    return tool["run"](args, bridge)
