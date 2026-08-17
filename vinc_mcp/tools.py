# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

**22종** — 읽기 14(search·recall·graph·decide·domains·suggest_ties·neighbors·
path·merge_candidates·merge_rejections·diagnose·doc_get·doc_list·conventions) + 쓰기 8
(ingest·delete·delete_edge·merge_nodes·merge_reject·doc_put·doc_extract·doc_delete).
"단일 write 표면(ingest 하나)"은
SEC-3(삭제·병합)과 D45(문서 정본 doc_*)를 거치며 깨진 낡은 서술이었다 —
아래 TOOLS 리스트가 정본이고 이 독스트링은 그 요약일 뿐이다.
⚠ 이 숫자는 F10-b 로 merge_reject 가 늘었을 때 19 인 채로 남아 어느 집계에도 안
들어갔다("TOOLS 가 정본"이라는 방어 문장을 단 채로 틀렸다). 그래서 이제
tests/test_tool_contract.py 가 이 독스트링의 수와 len(TOOLS) 를 대조한다.

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

# vinc_mcp 는 api 를 임포트하지 않으므로(독립 브리지 계약) 허용 쌍 문장을 여기
# 미러한다. 정본은 `api/fragment.py` 의 `_ALLOWED_PAIRS_HINT` 이고, 서버가 거부할 때
# warnings 에 싣는 것도 그 문장이다. 두 사본의 일치는 tests/test_tool_contract.py 의
# 교차 계약 테스트가 고정한다 — 커서 코덱 미러(아래 _cursor_encode)와 같은 방식.
#
# ⚠ 손으로 요약하지 말 것. v12 가 Decision 을 끝점으로 늘렸을 때 이 자리들이 전부
# "concept→concept" 인 채로 남아, 어시스턴트가 결정 엣지를 relates_to 로 쓰고
# partial:true 로 조용히 반쯤 성공하는 상태가 됐다. 계약이 대안을 말하지 않으니
# 스스로 고칠 근거도 없었다.
_ALLOWED_PAIRS_HINT = (
    "allowed pairs (concept and decision are interchangeable endpoints, "
    "except relates_to which stays concept→concept): "
    "relates_to = concept→concept; "
    "prerequisite_of|derives_from|inherits_from = concept|decision → concept|decision; "
    "record → concept|decision for any of those four; "
    "connects_via = bridge → concept|decision; "
    "about = episode|decision → concept|decision; "
    "any-type = open-entity→open-entity (TYPED carrier)"
)

# F1 개방층: type enum 을 제거했다 — 서버가 라우팅 SSoT 이며 이제 *모든* 타입을
# 영속한다(6종 전용 테이블 / 그 외 Entity·TYPED 캐리어). 스키마 수준 거부는
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
                             "description": "record|concept|domain|bridge|episode|decision route to dedicated "
                                            "tables; ANY other type persists to the open Entity carrier "
                                            "and is folded into the theme vocabulary as proposed."},
                    "title": {"type": "string",
                              "description": "Required for open (non-legacy) types."},
                    "domain": {"type": "string",
                               "description": "Free-form domain string (no fixed list). Not persisted "
                                              "on open-type nodes. SLASHES BUILD THE TOPIC HIERARCHY: "
                                              "'vinc/design' files this node under 'vinc' and creates "
                                              "the chain automatically (max depth 8) — no edges to "
                                              "author. CASE IS NOT FOLDED, so 'Design/Tokens' and "
                                              "'design/tokens' become two separate topic trees; reuse "
                                              "an existing string from vinc_domains verbatim."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    # F9 — 이 둘은 서버가 오래 top-level 로 받아 Episode 의 전용 열에
                    # 넣고 있었는데 계약에 없었다. 그래서 상류는 날짜를 실을 자리를
                    # props 밖에 못 찾았고, 실측상 에피소드 16/55 가 date 열이 빈 채
                    # props.date 만 가진 상태가 됐다(정렬 기준 열이 비면 vinc_recall 의
                    # 최신순이 무의미해진다). 승격은 서버가 하지만(api/fragment),
                    # 애초에 제자리에 실을 수 있도록 계약에도 적는다.
                    "date": {"type": "string",
                             "description": "Episode only — when it happened (YYYY-MM-DD). "
                                            "This is the column vinc_recall sorts by; "
                                            "putting it in props instead still works but "
                                            "relies on server-side promotion."},
                    "summary": {"type": "string",
                                "description": "Episode only — one-paragraph summary returned by vinc_recall."},
                    "one_liner": {"type": "string",
                                  "description": "Concept only — the one or two sentences node_context "
                                                 "and Ask quote as the concept's description. Putting it "
                                                 "in props.one_liner or props.note instead still works "
                                                 "(server-side promotion) but only when this field and "
                                                 "props.one_liner are both absent."},
                    # D72 — 두 시간축의 저작 가능한 쪽. `date` 와 같은 이유로 계약에
                    # 적는다: 서버는 이제 받는데 계약이 안 알려 주면 상류는 props 에
                    # 넣고, 그 자리는 정렬·필터가 안 읽는다.
                    "content_time": {"type": "string",
                                     "description": "Concept and record only — when the thing this node "
                                                    "describes happened (the 1905 of a note about "
                                                    "relativity), as opposed to when you wrote it down. "
                                                    "Free text but only a YYYY[-MM[-DD]] prefix is stored, "
                                                    "because comparison and bucketing are lexicographic; "
                                                    "anything else is refused with a warning and the "
                                                    "stored value is kept. Empty string clears it."},
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
                             "description": _ALLOWED_PAIRS_HINT + ". A record edge preserves its "
                                            "type on ATTACHES_TO. An unsupported (from,to) pair is "
                                            "skipped with a warning and partial:true, not an error."},
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
        # ⚠ `_OWNER_PROP`(아래)와 **같은 문구를 써야 한다.** 여기만 "defaults to the
        # local owner" 로 끝나 있어서, 호스티드에서 owner 를 실으면 먹힌다고 읽혔다.
        "owner": {"type": "string",
                  "description": "Owner scope to stamp. Ignored on the hosted graph (the account "
                                 "key fixes it); locally it defaults to this machine's owner."},
        "theme_id": {"type": "string",
                     "description": "Theme to fold open-type names into (optional — defaults to the "
                                    "active theme, creating 'default' lazily). Themes are GLOBAL, "
                                    "not per-owner, so naming someone else's theme folds your "
                                    "proposed types into their vocabulary. Omit it unless you know "
                                    "the theme is yours."},
    },
    "required": ["nodes"],
}


#: 쓰기·삭제 도구의 owner 파라미터 선언. 읽기 도구는 예전부터 `owner` 를 스키마에
#: 실었는데 삭제 3종만 빠져 있었다 — 핸들러는 읽는데 계약에는 없는 상태였다
#: (위험 감사 5.5). 원격에서는 이 값이 무시되고 키에서 해석한 owner 로 덮인다.
_OWNER_PROP = {"owner": {
    "type": "string",
    "description": "Owner scope. Ignored on the hosted graph (the account key fixes it); "
                   "locally it defaults to this machine's owner."}}


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
    # F3: `debug` 를 줘야만 내부 실행 계획(plan.cypher)이 실린다. 기본은 안 실린다 —
    # 여태 매 응답에 Cypher 전문과 owner UUID 가 딸려 왔다(실측 17.9KB 중 상당부).
    return bridge.request("GET", "/recall", params={
        "owner": _owner(args, bridge), "domain": args.get("domain"),
        "tags": ",".join(args["tags"]) if args.get("tags") else None,
        "limit": args.get("limit", 50),
        "debug": "true" if args.get("debug") else None})


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
    return bridge.request("GET", "/domains", params={
        "owner": _owner(args, bridge),
        "debug": "true" if args.get("debug") else None})


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
        "min_score": args.get("min_score", 0.6),
        "include_structural": "true" if args.get("include_structural") else None})


def _t_merge_reject(args, bridge):
    # F10-b 기각 기억 — 병합(비가역)과 짝을 이루는 가역 쓰기. undo 로 취소된다.
    return bridge.request("POST", "/merge-reject", body={
        "owner": _owner(args, bridge), "a": args["a"], "b": args["b"],
        "note": args.get("note") or "", "undo": bool(args.get("undo"))})


def _t_merge_rejections(args, bridge):
    # F10-b 기각 **목록**(조회 전용). vinc_merge_reject 로 쓸 수는 있는데 읽을 수가
    # 없어서, 어시스턴트가 이미 기각된 쌍을 다시 제안하는 짝 결손이 있었다.
    return bridge.request("GET", "/merge-rejections", params={"owner": _owner(args, bridge)})


def _t_doc_extract(args, bridge):
    # D45-V 정본→개념 파생. 웹 전용이라 문서 워크플로의 절반이 어시스턴트에게
    # 닫혀 있었다(문서는 쓸 수 있는데 거기서 개념을 못 뽑는다).
    return bridge.request("POST", "/doc/extract", body={
        "owner": _owner(args, bridge), "id": args["doc_id"],
        "apply": bool(args.get("apply")), "candidates": args.get("candidates")})


def _t_diagnose(args, bridge):
    # E-G3 구조 진단(조회 전용) — kind=components(WCC 고아·컴포넌트) | communities
    # (Louvain 군집) | community_context(시드 하나의 군집 + 대표 발췌). algo 확장
    # 부재 시 사이드카가 executed=False 로 강등.
    params = {"owner": _owner(args, bridge), "kind": args.get("kind", "components")}
    if args.get("member_limit") is not None:   # F6 — 미지정이면 서버 기본(50)
        params["member_limit"] = args["member_limit"]
    if args.get("node"):   # community_context 전용 — 다른 kind 는 무시(핸들러가 안 읽는다)
        params["node"] = args["node"]
    return bridge.request("GET", "/diagnose", params=params)


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


# ⚠ 삭제 3종은 오래 `args.get("owner")` 를 **직접** 읽었다. 그런데 그 `owner` 는
# 어느 스키마에도 선언돼 있지 않았고(호스트가 넣을 방법이 없다), 로컬 stdio 에는
# forced_owner 도 없어서 값이 항상 None 이었다 — 즉 **무스코프 삭제**였다. 계약에
# 없는 파라미터에 스코프가 걸려 있었으니, 문서를 읽고 안전하다고 판단할 방법도
# 없었다(위험 감사 5.5).
#
# 이제 읽기 도구와 **같은 규칙**을 쓴다: `_owner(args, bridge)`. 원격은 call() 이
# forced_owner 로 덮어 멀티테넌트를 봉쇄하고, 로컬은 bridge.owner 로 스코프된다.
# 로컬을 스코프해도 안전한 근거: 같은 로컬 스토어에서 `_t_search`·`_t_recall` 등
# 읽기 도구가 이미 `_owner(args, bridge)` 로 **엄격 술어**(`owner_id = $owner`)를
# 태워 정상 동작한다. 그 노드들이 그 owner 로 스탬프돼 있다는 뜻이다. 스탬프가
# 없는 스토어라면 읽기부터 비어 있었을 것이므로, 삭제만 넓게 열어 둘 이유가 없다.
def _t_delete(args, bridge):
    return bridge.request("POST", "/delete",
                          body={"node": args["node_id"], "owner": _owner(args, bridge)})


def _t_merge(args, bridge):
    return bridge.request("POST", "/merge-nodes",
                          body={"keep": args["keep_id"], "drop": args["drop_id"],
                                "owner": _owner(args, bridge)})


def _t_delete_edge(args, bridge):
    return bridge.request("POST", "/delete-edge",
                          body={"from": args["from_id"], "to": args["to_id"],
                                "type": args["type"], "owner": _owner(args, bridge)})


# ── 문서 정본(Chunk=SSoT, D45 P4) — 편집 표면 도구 ────────────────────────────
def _t_doc_get(args, bridge):
    return bridge.request("GET", "/doc", params={
        "id": args["doc_id"], "owner": _owner(args, bridge)})


def _t_doc_list(args, bridge):
    # F5 — 어시스턴트 표면의 기본은 **요약 30건**이다. 전량·full 은 실측 51,537B
    # (약 12,900토큰)라 목록 한 번이 컨텍스트를 삼킨다. 전문이 필요하면 doc_id 로
    # vinc_doc_get 을 부르는 것이 원래 계약이다.
    return bridge.request("GET", "/docs", params={
        "owner": _owner(args, bridge),
        "limit": args.get("limit", 30),
        "cursor": args.get("cursor"),
        "detail": args.get("detail", "summary")})


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
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
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
        "description": "Read back the episode timeline — what was recorded as having HAPPENED, newest first. "
                       "Returns {kind, executed, rows:[{id,title,date,domain,tags,summary,pinned,owner}]}. "
                       "Episodes ONLY: Concept and Record nodes never appear here — use vinc_search or "
                       "vinc_graph for those. Use it to resume context ('what was I doing here'), to check "
                       "whether an event is already recorded before writing a duplicate, or to ground a decision "
                       "in prior work. Narrow with `domain` or `tags`. This is time-ordered recall, NOT "
                       "relevance search — for 'find anything about X' use vinc_search, which also covers "
                       "concepts and document text. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "domain": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            "debug": {"type": "boolean", "default": False,
                      "description": "Include the internal query plan (Cypher + params) "
                                     "in the response. Off by default — it is large and "
                                     "exposes internals; turn it on only to diagnose."},
        }}, "run": _t_recall,
    },
    {
        "name": "vinc_graph",
        "description": "Pull the neighborhood around ONE known node as a whole subgraph: every node within "
                       "`depth` hops of the seed, plus the edges among them. Returns {kind, executed, seed, "
                       "depth, found, basis, nodes:[{id,label,kind,domain,degree,props,tags,ppr,…layout}], "
                       "edges:[{source,target,type,cross_domain}]}. Nodes carry View ranking and layout fields "
                       "(ppr, x/y/z), so a depth-2 pull is heavy. If you only need the immediate ring, "
                       "vinc_neighbors returns the same relations without them and is far cheaper; to walk step "
                       "by step, prefer neighbors and choose the next node yourself. Requires an EXACT node id — "
                       "if you only have a phrase, run vinc_search first. depth 2 is the working default, 4 the "
                       "ceiling. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "seed_id": {"type": "string"},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "owner": {"type": "string"},
        }, "required": ["seed_id"]}, "run": _t_graph,
    },
    {
        "name": "vinc_decide",
        "description": "Assemble the evidence around a topic for a decision: the relevant nodes, the edges among "
                       "them, and — the point of the tool — the CONFLICTS a decision has to resolve. Returns "
                       "{kind, executed, topic, depth, found, nodes, edges, "
                       "conflicts:[{source,target,type,reason}]}. Traversal is deterministic (graph structure "
                       "only, no model judgement); it surfaces the material and never picks a side. `topic` must "
                       "match a node id or a full title EXACTLY — on a miss you get found:false with "
                       "`candidates` and a `hint`, so run vinc_search first if you only have a phrase. Use "
                       "before recording a DEC, or to check whether a proposed decision collides with one "
                       "already made. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string"},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "owner": {"type": "string"},
        }, "required": ["topic"]}, "run": _t_decide,
    },
    {
        "name": "vinc_domains",
        "description": "List the domains actually present in this owner's graph, with per-kind node counts. "
                       "Returns {kind, executed, rows:[{domain, episodes, concepts, records, decisions, "
                       "total}]}, largest total first. This list is FLAT: it aggregates the `domain` string "
                       "column, so 'vinc' and 'vinc/design' appear as two separate rows even though they are "
                       "parent and child in the topic hierarchy — walk the hierarchy itself with "
                       "vinc_neighbors('domain:<name>'). The domain set is OPEN and inferred from stored "
                       "data, not a fixed vocabulary — so call this before writing to reuse an existing "
                       "domain string instead of coining a near-duplicate ('vinc/design' vs 'vinc/Design', "
                       "which case sensitivity makes two different topics). It is also the cheapest way to see "
                       "the coarse shape of a graph you have not read before. For naming and relation "
                       "conventions as well, vinc_conventions returns this plus more in one call. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "debug": {"type": "boolean", "default": False,
                      "description": "Include the internal query plan (Cypher + params) "
                                     "in the response. Off by default."},
        }}, "run": _t_domains,
    },
    {
        "name": "vinc_suggest_ties",
        "description": "Propose which OTHER concepts this node probably should be linked to but is not yet — "
                       "computed from shared tags and domain proximity, never from a language model. Returns "
                       "{kind, executed, node, suggestions:[{id,title,domain,shared_tags,score,reason,signals,"
                       "shared_neighbors}], total, truncated, formula}, best score first, with "
                       "already-linked nodes excluded. The score is three measured signals added "
                       "up, and `formula` states the exact expression the server used: rare "
                       "shared tags count for more than common ones (IDF-weighted, so a tag every "
                       "node carries adds nothing), same domain adds a flat amount, and shared "
                       "neighbours add the most. `signals` breaks the score into those parts and "
                       "`shared_neighbors` names the concepts both already link to — quote those "
                       "when proposing the tie instead of asserting a bare number. Use it to close the gaps vinc_diagnose finds "
                       "(orphans and small islands), or right after writing a new concept. This tool never "
                       "writes: the suggestions are for the user to ratify, and the edge itself is created with "
                       "vinc_ingest. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string"},
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 50},
            "owner": {"type": "string"},
        }, "required": ["node"]}, "run": _t_suggest,
    },
    {
        "name": "vinc_neighbors",
        "description": "Observe ONE concept's immediate (1-hop) relation network — a lightweight "
                       "navigation primitive (E-G5, Search-on-Graph style). Returns {neighbors:"
                       "[{rel,id,title,domain}], by_rel:{type:count}, count}. Use this to walk the "
                       "graph one hop at a time (observe → pick next node) instead of pulling a whole "
                       "depth-2 subgraph via vinc_graph — far fewer tokens per step. "
                       "Also accepts a TOPIC address `domain:<name>` (e.g. domain:vinc), which returns "
                       "that topic's child topics (rel PARENT_OF, ids `domain:…`) and the nodes filed "
                       "directly under it (rel IN_DOMAIN) — same shape, so you can walk down a topic "
                       "tree the same way. Topics come from the `domain` field on nodes: writing "
                       "'vinc/design' files that node under 'vinc' automatically, no edges to author. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Exact concept id to observe (e.g. "
                                                      "concept:duality), or a topic address "
                                                      "(e.g. domain:vinc)."},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500,
                      "description": "Max neighbors returned. If cut, `neighbors_truncated` is true."},
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
            "max_depth": {"type": "integer", "default": 6, "minimum": 1, "maximum": 8,
                          "description": "Max path length in hops."},
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
                       "[{a_id,a_title,b_id,b_title,score,reason,same_domain}], count, total, "
                       "suppressed:{structural,rejected}}, same-domain pairs first. Pairs where "
                       "EITHER side is a document mirror concept (concept:doc.*) are excluded BY "
                       "DEFAULT, because they are duplicates in name only: a document concept and "
                       "the topical concept it mirrors are two layers, not two copies (the topical "
                       "one carries ABOUT/ATTACHES_TO/RELATED_TO, the document one carries "
                       "INHERITS_FROM to other documents), and two documents are two concepts even "
                       "when their titles nest. `suppressed` reports how many "
                       "were held back so the exclusion is never silent; set include_structural to "
                       "see them. Pairs the user has already rejected (vinc_merge_reject) are also "
                       "excluded and counted there. Confirm a real duplicate by calling "
                       "vinc_merge_nodes with the chosen keep/drop ids (irreversible).",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            "include_structural": {"type": "boolean", "default": False,
                                   "description": "Also return the structurally-excluded pairs "
                                                  "(any pair touching a concept:doc.* mirror)."},
            "min_score": {"type": "number", "default": 0.6, "minimum": 0.0, "maximum": 1.0,
                          "description": "Token-Jaccard threshold (0-1) for token_overlap pairs."},
            "owner": {"type": "string"},
        }}, "run": _t_merge_candidates,
    },
    {
        "name": "vinc_merge_reject",
        "description": "Record that two concepts are NOT duplicates, so vinc_merge_candidates "
                       "stops proposing the pair. This is the safe half of the merge workflow: "
                       "vinc_merge_nodes is irreversible, this is not — pass undo:true to forget "
                       "the rejection. Use it when you review candidates and decide a pair is a "
                       "genuine distinction; without it the same pair returns every time and the "
                       "list never converges. Order of a/b does not matter (the pair is stored "
                       "sorted). Returns {kind, executed, a_id, b_id, undo, pair_key, count} where "
                       "`count` is how many rejections this owner now has. Writes.",
        "inputSchema": {"type": "object", "properties": {
            "a": {"type": "string", "description": "Exact id of one concept."},
            "b": {"type": "string", "description": "Exact id of the other concept."},
            "note": {"type": "string",
                     "description": "Why they are distinct — kept for the next reviewer."},
            "undo": {"type": "boolean", "default": False,
                     "description": "Forget a previous rejection instead of recording one."},
            **_OWNER_PROP,
        }, "required": ["a", "b"]}, "run": _t_merge_reject,
    },
    {
        "name": "vinc_merge_rejections",
        "description": "List the concept pairs this owner has already rejected as non-duplicates "
                       "(read-only, the read half of vinc_merge_reject). Returns {kind, executed, "
                       "rejections:[{a_id, b_id, note, rejected_at}], count}. Call it before "
                       "reviewing vinc_merge_candidates to see what was already decided and why — "
                       "vinc_merge_candidates suppresses these pairs silently and only reports how "
                       "MANY it held back, so this is the only way to see WHICH pairs and read the "
                       "reasoning left for the next reviewer. Also the way to find a pair to undo: "
                       "call vinc_merge_reject with undo:true and the ids from here. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
        }}, "run": _t_merge_rejections,
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
                       "themes (ratified by the user, never auto-written). kind='community_context' "
                       "(requires `node`) answers a broad 'what is over in this area' question with "
                       "no generation: takes ONE seed concept id, finds the Louvain community it "
                       "belongs to, and returns {community, size, dominant_domain, "
                       "representatives:[{id,title,one_liner,excerpt}], members:[{id,title}], "
                       "members_truncated} — up to 5 representatives (ranked by how many chunks "
                       "mention them) carry a quoted source excerpt, the rest are id/title only. "
                       "executed:false with a note if the seed is unknown or isolated (Louvain -1). "
                       "Member id lists are capped by `member_limit` (default 50): `size` always "
                       "reports the TRUE total, and a group whose list was cut carries "
                       "members_truncated:true — raise member_limit to see the rest. Delegates to "
                       "the native algo extension; executed:false with a note if the extension is "
                       "unavailable (offline unbundled).",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["components", "communities", "community_context"],
                     "description": "components (WCC orphans/islands) | communities (Louvain "
                                    "clusters) | community_context (one seed's cluster + "
                                    "representative excerpts, requires `node`)."},
            "node": {"type": "string",
                     "description": "Seed concept id — required, and only used, when "
                                    "kind='community_context'."},
            "member_limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 1000,
                             "description": "Max member ids listed per component/community. "
                                            "`size` is always the true total; when the list is "
                                            "cut, that group carries members_truncated:true."},
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
                       "bridge|episode|decision route to dedicated tables; ANY other node type "
                       "persists to the open Entity carrier and is folded into the theme vocabulary "
                       "as proposed (returned in proposed_types for ratification). Edges — "
                       + _ALLOWED_PAIRS_HINT +
                       ". relates_to dedupes the reverse direction; connects_via takes an optional "
                       "role; a record edge preserves its type on ATTACHES_TO. Nodes are skipped "
                       "when malformed (no id, no type, an Entity with no title) or when the id "
                       "is ALREADY OWNED BY SOMEONE ELSE on the hosted graph; edges are skipped "
                       "on an unsupported (from,to) pair AND when an endpoint node was itself "
                       "skipped, since an edge cannot outlive its endpoint. Everything skipped is "
                       "reported in warnings with partial:true, so read them — `merged` counts "
                       "only what actually landed. Call vinc_conventions once before your first write "
                       "in a session: it carries this owner's node/relation conventions and "
                       "their existing domains, so your writes match what is already there.",
        "inputSchema": _FRAGMENT_SCHEMA,
        "run": _t_ingest,
    },
    {
        "name": "vinc_delete",
        "description": "Irreversibly delete ONE node and all its attached edges by exact id "
                       "(DETACH DELETE). node_id must be an exact, type-prefixed id "
                       "(e.g. concept:ipaas, decision:d45, record:memo, episode:s1, bridge:b1); "
                       "the prefix "
                       "selects the table. Unknown prefixes return found:false (no-op). Use to "
                       "remove polluted or duplicate shell nodes. This is irreversible — confirm "
                       "the exact id via vinc_search/vinc_graph first. Returns "
                       "{executed, found, deleted:{node, edges}}; found:false if the id is absent.",
        "inputSchema": {"type": "object", "properties": {
            "node_id": {"type": "string",
                        "description": "Exact type-prefixed node id to delete (e.g. concept:ipaas)."},
            **_OWNER_PROP,
        }, "required": ["node_id"]},
        "run": _t_delete,
    },
    {
        "name": "vinc_delete_edge",
        "description": "Delete ONE relationship between two nodes (the nodes stay). "
                       "Covers the SYNCED relation types only: relates_to | prerequisite_of | "
                       "derives_from | inherits_from | about. CONNECTS (connects_via), "
                       "ATTACHES_TO (record edges) and TYPED (open-entity edges) are NOT "
                       "deletable here — they are outside the sync set. Endpoints follow the "
                       "same rule as vinc_ingest: concept and decision are interchangeable "
                       "(so derives_from covers all four concept/decision combinations, and "
                       "about is episode|decision → concept|decision), except relates_to which "
                       "stays concept→concept. relates_to is undirected — both physical "
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
            **_OWNER_PROP,
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
            **_OWNER_PROP,
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
        "name": "vinc_doc_extract",
        "description": "Derive concept nodes FROM a stored document's canonical text (D45-V). This "
                       "is the second half of the document workflow: vinc_doc_put stores the text, "
                       "this turns it into concepts bound to it by MENTIONS, so the document joins "
                       "the search and view graph instead of sitting as inert text. "
                       "apply:false (the DEFAULT) only proposes: it returns the candidates and "
                       "writes NOTHING, which is the call to make first. apply:true writes them: "
                       "each candidate is MERGEd as concept:{slug} with status='proposed' and an "
                       "empty domain (topic assignment is a later edit), and this document's "
                       "MENTIONS are relinked. Existing concepts are matched, never overwritten — "
                       "a title or one_liner you already refined is safe. "
                       "Pass `candidates` to apply a list you curated instead of the deterministic "
                       "heading-based extraction; only title and one_liner are trusted and the id "
                       "is always re-derived server-side. Returns {executed, doc_id, candidates, "
                       "applied:{created, matched}, mentions}. Writes only when apply:true.",
        "inputSchema": {"type": "object", "properties": {
            "doc_id": {"type": "string", "description": "Exact id of a document you own."},
            "apply": {"type": "boolean", "default": False,
                      "description": "false (default) proposes and writes nothing; true writes "
                                     "the concepts and relinks MENTIONS."},
            "candidates": {"type": "array", "description":
                           "Optional curated candidates to apply instead of the deterministic "
                           "extraction. Only title and one_liner are read; ids are re-derived.",
                           "items": {"type": "object", "properties": {
                               "title": {"type": "string"},
                               "one_liner": {"type": "string"},
                           }, "required": ["title"]}},
            **_OWNER_PROP,
        }, "required": ["doc_id"]},
        "run": _t_doc_extract,
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
        "description": "List the owner's documents, newest first. Paginated: `limit` (default 30) "
                       "with `cursor`/`has_more`/`next_cursor` to continue. `detail` defaults to "
                       "'summary' (doc_id, title, updated_at, chunks) — pass 'full' only when you "
                       "need the derived topics and primary concept, which cost a scan of every "
                       "document's MENTIONS. Fetch a document's text with vinc_doc_get(doc_id).",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            "cursor": {"type": "string",
                       "description": "Opaque next_cursor from a previous page."},
            "detail": {"type": "string", "enum": ["summary", "full"], "default": "summary"},
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
    # 기각은 쓰기지만 **가역**이다(undo). 병합과 달리 파괴적이지 않다.
    "vinc_merge_reject": {"title": "Reject a merge pair", "readOnlyHint": False,
                          "destructiveHint": False, "idempotentHint": True},
    "vinc_merge_rejections": {"title": "List rejected merge pairs", "readOnlyHint": True},
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
    # apply=false 는 읽기지만 apply=true 는 쓰기다. 주석은 **도구 단위**라 더 강한
    # 쪽으로 선언한다 — 호스트가 쓰기를 읽기로 오인하는 것이 그 반대보다 나쁘다.
    # 가산적이고 멱등이다(기존 개념은 matched 로 보존, 같은 문서 재실행은 같은 결과).
    "vinc_doc_extract": {"title": "Derive concepts from a document", "readOnlyHint": False,
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


def specs(*, allow_target: bool = True) -> list[dict]:
    """도구 목록. ``allow_target=False`` 면 `target` 프로퍼티를 주입하지 않는다.

    **왜 프로파일로 가르나**(위험 감사 5.5). `target` 은 데스크탑 커넥터 하나로
    로컬·원격을 고르라고 만든 것이다(A1.9 A2). 그런데 주입이 무조건이라 **허브가
    내보내는 목록에도** 실렸고, 허브에서 `target:"remote"` 가 오면 허브가 자기
    자신 밖으로 아웃바운드를 시도한다 — 서버가 스스로에게 클라이언트가 되는,
    의도한 적 없는 경로다. 허브(`vinc_mcp.http_server`)는 False 로 부른다."""
    out = []
    for t in TOOLS:
        sch = t["inputSchema"]
        props = {**sch.get("properties", {})}
        if allow_target:
            props.update(_TARGET_PROP)
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
         entitlements: dict | None = None,
         allow_target: bool = True) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = dict(args or {})                     # 복사 — 호출자 dict 불변, target 제거
    target = (args.pop("target", None) or "local").lower()
    # 스키마에서 뺀 것만으로는 부족하다 — 클라이언트는 선언되지 않은 필드도 보낼 수
    # 있다. 허브 프로파일에서는 실행 경로에서도 막는다(위험 감사 5.5).
    if not allow_target and target != "local":
        raise ValueError(
            "target is not available on this server: this endpoint IS the remote "
            "graph. Drop the target argument.")
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
