# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

**24종** — 읽기 16(search·brief·recall·graph·decide·domains·suggest_ties·neighbors·
path·merge_candidates·merge_rejections·diagnose·doc_get·doc_list·conventions·whoami) + 쓰기 8
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
                                                 "props.one_liner are both absent. It reads back: "
                                                 "vinc_neighbors and vinc_graph return it, so you can "
                                                 "check what you wrote. Only Concept has the column, so "
                                                 "it comes back empty on every other kind."},
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
                    # v15(W13) — content_time 과 같은 이유로 계약에 적는다: 서버는
                    # 받는데 계약이 침묵하면 상류는 props 에 넣고, 그 자리는 아무
                    # 읽기도 구간으로 취급하지 않는다.
                    "valid_from": {"type": "string",
                                   "description": "Record only — when the claim this record makes became "
                                                  "true. Same YYYY[-MM[-DD]] contract as content_time; "
                                                  "empty string clears it. Omitting the field on a resend "
                                                  "preserves the stored value."},
                    "valid_until": {"type": "string",
                                    "description": "Record only — when the claim stopped being true "
                                                   "(half-open: the claim does not hold at this instant). "
                                                   "Setting it is how a record is invalidated without "
                                                   "deleting it — resend just {id, type, valid_until}. "
                                                   "Same format contract as valid_from; empty string "
                                                   "clears it (the claim holds again)."},
                    "props": {"type": "object", "description": "Free-form extension properties (legacy types only — not persisted on open-type nodes). Three keys are reserved on a **record** that states something about code, and only there: `subject_path` (WHICH repo and WHICH file, as repo:path, for example vinc:api/sync.py; the bare path alone cannot be compared because this graph holds records about three repositories), `measured_at` (YYYY[-MM[-DD]], when you looked) and `measured_ref` (branch or commit you looked at). They are what lets two claims about the same file be compared instead of both standing as assertions; `subject_path` is normalized on write so different spellings of one path land on one key."},
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
                                    "active theme, creating 'default' lazily). On the hosted graph this field "
                                    "is IGNORED and the server derives the theme from your account: "
                                    "the theme file has no owner column, so a chosen theme would fold "
                                    "your proposed types into someone else's vocabulary. It still "
                                    "applies to a local store."},
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
    # `url` — 사람이 화면에서 복사한 주소를 그대로 받는다(D: 선택이 주소가 된다).
    # 인자로 받는 이유: 스페이스와 달리 **빠뜨리면 요청 자체가 성립하지 않아** 조용히
    # 딴 데 가는 일이 없다. 근거: [[decision:a-selection-becomes-an-address-you-paste]]
    seed = (args.get("seed_id") or "").strip()
    depth = args.get("depth")
    if not seed and args.get("url"):
        from . import viewurl
        got = viewurl.parse(str(args["url"]))
        if got["kind"] == "node":
            seed = got["seed"]
        elif got["kind"] == "ids" and got["ids"]:
            # 임의 선택은 시드가 여럿이다. 이 도구는 하나만 받으므로 첫 개를 쓰고
            # **그렇게 했다고 말한다** — 조용히 고르면 나머지를 본 줄 안다.
            seed = got["ids"][0]
        elif got["kind"] in ("domains", "doc", "all"):
            return {"kind": "graph", "executed": False, "seed": "",
                    "note": f"that url selects a {got['kind']}, not one node — "
                            f"vinc_graph needs a seed; use vinc_search or vinc_doc_get"}
        if depth is None and got["depth"]:
            depth = got["depth"]
        if not seed:
            return {"kind": "graph", "executed": False, "seed": "",
                    "note": "could not read a node out of that url"}
    if not seed:
        return {"kind": "graph", "executed": False, "seed": "",
                "note": "seed_id or url is required"}
    out = bridge.request("GET", "/graph", params={
        "owner": _owner(args, bridge), "seed": seed,
        "depth": depth if depth is not None else 2})
    if (not (args.get("seed_id") or "").strip()) and args.get("url")             and isinstance(out, dict):
        out.setdefault("from_url", seed)
    return out


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


# ⚠ 삭제 3종은 읽기 도구와 **같은 규칙**을 쓴다: `_owner(args, bridge)`. 스키마에 없는
# `args["owner"]` 를 직접 읽던 시절 그 값은 항상 None 이라 **무스코프 삭제**였다
# ([[vinc.ops.risk-audit-2026-08-05]]). 로컬을 스코프해도 안전한 근거는 같은 스토어에서
# 읽기 도구가 이미 엄격 술어로 정상 동작한다는 것이다 — 스탬프가 없으면 읽기부터
# 비어 있었을 테니, 삭제만 넓게 열어 둘 이유가 없다.
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


def _t_whoami(args, bridge):
    """어느 그래프에 붙어 있고, 어디로 갈 수 있는가.

    ⚠ `spaces` 는 **계정에 묻는다**(브리지가 아니라). 어느 팀에 속하는지는 계정의
    사실이고 그래프에는 그 열이 없다. 못 물으면 개인 하나만 답하되 `spaces_source`
    로 그 사실을 말한다 — 빈 목록과 "못 물었다"가 같은 모양이면 에이전트가 "팀이
    없구나"로 읽고 사람에게 그렇게 말한다.

    이 도구가 왜 생겼나(`vinc_conventions` 가 이미 신원을 싣는데도):
    [[decision:the-space-is-an-argument-and-the-default-is-personal]]"""
    owner = _owner(args, bridge)
    scope = ("team" if owner.startswith("team:")
             else "personal" if owner.startswith("user:") else "unscoped")
    out = {"owner": owner, "scope": scope,
           "how": "pass space:'<id>' to any tool to act in that space; omit it for "
                  "your personal graph"}
    if scope != "personal":
        # 팀 주소로 붙었으면 그 팀에 고정돼 있다(인자가 무시되는 자리). 목록을 물을
        # 사람(publicId)도 여기서는 안 보인다 — 그 세션은 이미 스페이스를 골랐다.
        out["spaces_source"] = "not_applicable"
        return out
    from . import entitlement
    rows = entitlement.list_spaces(owner.split(":", 1)[1])
    out["spaces"] = [{"id": "personal", "name": "Personal", "role": "admin"}] + (rows or [])
    out["spaces_source"] = "account" if rows is not None else "unavailable"
    return out


def _t_doc_delete(args, bridge):
    return bridge.request("POST", "/doc/delete", body={
        "id": args["doc_id"], "owner": _owner(args, bridge)})


def _t_brief(args, bridge):
    # W1 — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기(생성 없음).
    return bridge.request("GET", "/brief", params={
        "owner": _owner(args, bridge), "query": args["query"],
        "max_excerpts": args.get("max_excerpts", 3)})


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
                       "empty. Right after a document write the vector index is being rebuilt, "
                       "so this answers from full text alone and says so in `vector_note` with "
                       "`arms` reading \"fts\"; the rebuild runs in the background and the call "
                       "never waits for it, so semantic matches come back on a later search.",
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
        "name": "vinc_brief",
        "description": "One question in, joined answer material out — the deterministic read that "
                       "replaces a vinc_search + vinc_graph + vinc_doc_get chain. It resolves the "
                       "question to ONE node by strict ranking, then returns that node's description, "
                       "its canonical document excerpts and its typed relations in a single call. "
                       "Returns {kind, executed, query, status, node:{id,title,domain,kind}, one_liner, "
                       "body_src, body_cite, excerpts:[{doc_id,doc_title,section,excerpt,method}], "
                       "links:{degree,out,in,attached,items:[{id,title,domain,type,dir,evidence,"
                       "confidence,note}],truncated}, candidates:[...], grounds:[id...]}. "
                       "READ `status` FIRST: 'resolved' means one node won strictly, so node/excerpts/"
                       "links are filled; 'choose' means the leading candidates TIED and nothing was "
                       "guessed — pick one from `candidates` (each carries hit_tokens/title_tokens, the "
                       "numbers that tied) and ask again by its exact title; 'none' means the question "
                       "matched nothing. NOTHING IS GENERATED: `one_liner` is quoted, never written — "
                       "`body_src` says whether it came from the one_liner column, a props key, or a "
                       "document excerpt, and `body_cite` names the document when it is an excerpt. "
                       "`grounds` lists every id the material rests on, so an answer built from this "
                       "call can cite its sources; it stays complete even when the result was shrunk to "
                       "fit the size limit. `links.items[].evidence` is a BOOLEAN (does this relation "
                       "carry a source sentence) and `evidence_text` is that sentence when one exists, "
                       "so a relation can be QUOTED rather than merely counted. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "The question, in natural language."},
            "max_excerpts": {"type": "integer", "default": 3, "minimum": 1, "maximum": 12,
                             "description": "How many canonical document excerpts to "
                                            "attach to a resolved node."},
            "owner": {"type": "string"},
        }, "required": ["query"]},
        "run": _t_brief,
    },
    {
        "name": "vinc_recall",
        "description": "Read back the episode timeline — what was recorded as having HAPPENED, newest first. "
                       "Returns {kind, executed, rows:[{id,title,date,owner,domain,tenant,workspace,summary,"
                       "pinned,tags,access_count,last_access}]}. `tenant`/`workspace` and `access_count`/"
                       "`last_access` are reserved columns, not settable through vinc_ingest: the first pair "
                       "comes only from the learn-log batch import, the second only from the preference-touch "
                       "signal (neither is an MCP tool). An episode written through vinc_ingest alone reads "
                       "these four back empty — that is expected, not a bug. "
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
                       "depth, found, basis, nodes:[{id,label,kind,domain,degree,props,tags,rev,updated_at,"
                       "ppr,…layout}], "
                       "edges:[{source,target,type,cross_domain}]}. Nodes carry View ranking and layout fields "
                       "(ppr, x/y/z), so a depth-2 pull is heavy. If you only need the immediate ring, "
                       "vinc_neighbors returns the same relations without them and is far cheaper; to walk step "
                       "by step, prefer neighbors and choose the next node yourself. Requires an EXACT node id — "
                       "if you only have a phrase, run vinc_search first. depth 2 is the working default, 4 the "
                       "ceiling. `rev` and `updated_at` are the node's current version, so you can hold what "
                       "you read before you overwrite it; nothing on the write path demands them yet. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "seed_id": {"type": "string",
                        "description": "Exact node id. Give this OR `url`, not neither."},
            "url": {"type": "string",
                    "description": "A View address copied from the app, whole URL or just "
                                   "the #view/... fragment. It carries what the person had "
                                   "selected: a node (#view/trace/<id>), a topic lens "
                                   "(#view/<a,b>), an explicit set (#view/ids/<a,b,c>) or a "
                                   "document (#view/doc/<id>), with an optional /d<n> depth "
                                   "and a trailing /as/<viewpoint> this tool ignores. When "
                                   "both are given, seed_id wins and the url's seed is "
                                   "ignored; `depth` given explicitly beats the url's."},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "owner": {"type": "string"},
        }}, "run": _t_graph,
    },
    {
        "name": "vinc_decide",
        "description": "Assemble the evidence around a topic for a decision: the relevant nodes, the edges among "
                       "them, and — the point of the tool — the CONFLICTS a decision has to resolve. Returns "
                       "{kind, executed, topic, depth, found, nodes, edges, "
                       "conflicts:[{source,target,type,reason}], cross_domain:[{source,target,type}]}. "
                       "A conflict is one of three things the graph states about itself: "
                       "`decisions_on_same_subject` (two decisions ABOUT the same concept, with `subject`), "
                       "`opposite_direction` (a directed relation runs both ways on one pair, with "
                       "`reverse_type`), `duplicate_decision_title` (two decisions carrying the same title). "
                       "Edges that merely cross a domain boundary are NOT conflicts and are listed separately "
                       "in `cross_domain`. Traversal is deterministic (graph structure "
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
                       "total}]}, largest total first. Nodes with NO domain are counted too, as the single "
                       "row '(none)' — an untopiced node is a count here, not an absence, so this is also "
                       "how you find what still needs a topic. The free-tier topic cap does not count that "
                       "row. This list is FLAT: it aggregates the `domain` string "
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
        "description": "Observe ONE node's immediate (1-hop) relation network — a lightweight "
                       "navigation primitive (E-G5, Search-on-Graph style). Returns {neighbors:"
                       "[{rel,id,title,domain,one_liner,rev,updated_at}], by_rel:{type:count}, count}. "
                       "`rev` and `updated_at` are that neighbour's current version, carried so a walk can "
                       "note what it read; a `domain:` coordinate answers with child domains and members "
                       "instead, and those rows have no version because a domain is not a stored node. "
                       "`one_liner` is "
                       "the concept's own description; only Concept carries that column, so it "
                       "is an empty string on records, episodes, bridges and decisions, and that "
                       "emptiness is a fact rather than a gap. Use this to walk the "
                       "graph one hop at a time (observe → pick next node) instead of pulling a whole "
                       "depth-2 subgraph via vinc_graph — far fewer tokens per step. "
                       "Every relation is visible from BOTH ends: seeding on a concept or decision "
                       "returns the records (ATTACHES_TO), episodes (ABOUT) and bridges (CONNECTS) "
                       "hanging off it, and seeding on a record, episode or bridge returns the "
                       "concepts and decisions it hangs off. count:0 therefore means the node "
                       "really has no relations yet, not that you started from the wrong end. "
                       "Also accepts a TOPIC address `domain:<name>` (e.g. domain:vinc), which returns "
                       "that topic's child topics (rel PARENT_OF, ids `domain:…`) and the nodes filed "
                       "directly under it (rel IN_DOMAIN) — same shape, so you can walk down a topic "
                       "tree the same way. Topics come from the `domain` field on nodes: writing "
                       "'vinc/design' files that node under 'vinc' automatically, no edges to author. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Exact node id to observe (e.g. "
                                                      "concept:duality, record:pricing-note), "
                                                      "or a topic address (e.g. domain:vinc)."},
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
                       "unavailable (offline unbundled). kind='claims' is the exception: it uses "
                       "no algo extension and always answers. It returns "
                       "{conflicts:[{subject_path,newer,older}], conflict_count, "
                       "unanchored:[{id,title,path_hint}], unanchored_count, anchored_count, "
                       "scanned}. `conflicts` pairs the most recent measurement of a file with "
                       "every earlier one that carries a different measured_at, so the question "
                       "it poses is 'does this older claim still stand'. It does NOT decide "
                       "which side is right; open the path and look. `unanchored` lists records "
                       "that name a file in their text but carry no props.subject_path, so they "
                       "cannot be compared with anything: that count is meant to be driven down "
                       "by adding the field. Both lists are capped by member_limit.",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["components", "communities", "community_context", "claims"],
                     "description": "components (WCC orphans/islands) | communities (Louvain "
                                    "clusters) | community_context (one seed's cluster + "
                                    "representative excerpts, requires `node`) | claims "
                                    "(records that disagree about one file, and records about "
                                    "code with no file named)."},
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
                       "missing:true) — those two are worth following even then. The domain "
                       "list and the ratified vocabulary are recomputed from the store on every "
                       "call, so they are current even when the file itself was written months "
                       "ago; the built date in the header is a label saying when the prose was "
                       "last generated, not when these numbers were taken. This is the "
                       "only way to see the ratified vocabulary: proposing an unlisted type is "
                       "allowed, but it lands as proposed and waits for a human. "
                       "THE RESPONSE ALSO TELLS YOU WHERE YOU ARE: `owner` is the graph you "
                       "are connected as, `scope` is 'personal' or 'team' (or 'unscoped' "
                       "for a local single-user store), and `target` echoes local versus "
                       "remote. Read them before your first write. Use vinc_whoami when you "
                       "want that answer on its own, or the list of spaces you can reach; "
                       "this tool carries it too so the first door already tells you. "
                       "On a hosted connector the space is your personal graph unless you "
                       "say otherwise: pass `space` to any tool to act in a team instead "
                       "(vinc_whoami lists the ids and your role in each). Connecting at a "
                       "team address `/t/<team-uuid>/mcp` still works and pins every call in "
                       "this session to that team, ignoring the argument; both API keys and "
                       "OAuth are accepted there.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "lang": {"type": "string", "enum": ["en", "ko"],
                     "description": "Language of the default template (ignored when the "
                                    "owner has written their own)."},
        }},
        "run": _t_conventions,
    },
    {
        "name": "vinc_whoami",
        "description": "Which graph am I connected to, and which others can I reach? "
                       "Returns {owner, scope, spaces, spaces_source}. `scope` is 'personal' "
                       "or 'team' ('unscoped' for a local single-user store). `spaces` lists "
                       "every space this credential can act in — 'personal' plus each team "
                       "you are an active member of, each with the `id` you pass as `space` and "
                       "your `role` there (viewer reads, editor writes, admin also deletes and "
                       "merges). Omitting `space` acts on your personal graph, so a write with "
                       "no space lands there even when you meant a team; this is how you get "
                       "the id to say otherwise. `spaces_source` says where the list came from: "
                       "'account' means it is complete, 'unavailable' means the team list could "
                       "not be fetched and only personal is shown (an empty team list and a "
                       "failed lookup are NOT the same answer), and 'not_applicable' means you "
                       "connected at a team address, which pins this session to that team and "
                       "ignores the `space` argument entirely. Read-only, and free of the graph "
                       "— it asks the account, not your store.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
        }},
        "run": _t_whoami,
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
                       "only what actually landed. An id owned by someone else is reported "
                       "separately in `id_conflicts`, because it is the one skip a retry cannot "
                       "fix: node ids are a single global namespace, so that slug is not "
                       "available to you at all. Pick a different id rather than retrying. "
                       "The result also carries `stored` — each node READ BACK as it now sits in "
                       "the graph (id, kind, and the fields that kind actually keeps). Diff it "
                       "against what you sent instead of trusting the counts: a field a kind has "
                       "no column for is absent from `stored`, and a domain you sent appears there "
                       "in its normalized form. `stored_truncated` means the read-back hit its size "
                       "budget and that many nodes were left out (they were still written). "
                       "Call vinc_conventions once before your first write "
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
                       "truth — no external file is needed. Returns {doc_id, created, chunks, "
                       "replaced, content_hash, skipped, warnings}. Unchanged text is a no-op "
                       "(skipped=true). READ `created`: it is the only field that separates "
                       "editing a document from making a second one, because a fresh write and "
                       "an unchanged re-write both report replaced 0. A document's own front "
                       "matter often carries a doc_id: line and THAT IS NOT ITS STORE ID; "
                       "passing the declared value here does not edit that document, it creates "
                       "another one with the same title. When the body declares an id different "
                       "from the one being written to, `warnings` says so.",
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
                       "each candidate is MERGEd as concept:{slug} with status='proposed' and the "
                       "DOCUMENT'S OWN TOPIC as its domain (the front-matter `domain:` line if "
                       "present, else the document's derived topic). A document with neither "
                       "refuses apply with an error saying to give it a topic first — applying "
                       "topic-less concepts only multiplies what has no topic. This document's "
                       "MENTIONS are then relinked. Existing concepts are matched, never "
                       "overwritten — a title or one_liner you already refined is safe, and an "
                       "empty content_time or domain on a matched concept is filled in. "
                       "Headings that are document structure words (method, provenance, verdict "
                       "...), clause-shaped sentences, or longer than six words yield no "
                       "candidates in the deterministic mode. "
                       "The deterministic extraction cleans what it reads: a heading loses its "
                       "section number or letter prefix (\"3.1 Identity\" becomes \"Identity\", "
                       "which is also what the slug is derived from) and its inline markdown "
                       "marks, and the one_liner runs on to the end of the first sentence instead "
                       "of stopping at the line break. A heading with nothing readable under it "
                       "yields no candidate at all, so every candidate you get back carries a "
                       "one_liner. "
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
# 원격 디렉토리 제출 요건이라 누락하면 거부된다. 호스트가 이걸로 확인 프롬프트를 정한다.
# ⚠ 주석은 **힌트**지 신뢰 경계가 아니다 — 강제는 서버측 forced_owner 스탬핑이 한다
# ([[vinc.canon.mcp-contract]]). destructiveHint 는 기본값이 true 라 파괴적이지 않은
# 쓰기는 명시적으로 false 를 줘야 하고, openWorldHint 는 전부 false 다(닫힌 도메인).
_ANNOTATIONS: dict[str, dict] = {
    "vinc_search": {"title": "Search knowledge", "readOnlyHint": True},
    "vinc_recall": {"title": "Recall episodes", "readOnlyHint": True},
    "vinc_graph": {"title": "Explore the graph", "readOnlyHint": True},
    "vinc_decide": {"title": "Decision support", "readOnlyHint": True},
    "vinc_domains": {"title": "List domains", "readOnlyHint": True},
    # 관례(GRAPH.md) — 조회 전용. 쓰기 전에 읽는 것이 이 도구의 존재 이유다.
    "vinc_conventions": {"title": "Read the graph conventions", "readOnlyHint": True},
    # 스페이스 목록 — 조회 전용이고 **그래프를 안 읽는다**(계정에 묻는다). 등급이
    # viewer 인 것이 중요하다: 열람자가 자기가 어디 있는지 못 물으면 그 역할은
    # 스페이스를 고를 수 없다.
    "vinc_whoami": {"title": "Which graph am I in", "readOnlyHint": True},
    "vinc_suggest_ties": {"title": "Suggest links", "readOnlyHint": True},
    # E-G3/G4/G5/G7 — 전부 조회 전용(진단·제안·관측·경로). 쓰기 아님.
    "vinc_diagnose": {"title": "Diagnose graph structure", "readOnlyHint": True},
    "vinc_merge_candidates": {"title": "Suggest merge candidates", "readOnlyHint": True},
    # 기각은 쓰기지만 **가역**이다(undo). 병합과 달리 파괴적이지 않다.
    "vinc_merge_reject": {"title": "Reject a merge pair", "readOnlyHint": False,
                          "destructiveHint": False, "idempotentHint": True},
    "vinc_merge_rejections": {"title": "List rejected merge pairs", "readOnlyHint": True},
    # W1 — 질문 하나로 답변 재료를 모으는 조인 읽기. 조회 전용이다.
    "vinc_brief": {"title": "Brief on a question", "readOnlyHint": True},
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


#: 호스티드 프로파일의 스페이스 선택자. 주소 대신 인자로도 스페이스를 고른다.
#:
#: ⚠ **안 실으면 개인이다.** 그래서 팀에 쓰려던 호출이 오류 없이 개인 그래프에
#: 쌓일 수 있고, 그 침묵을 셋이 덮는다 — initialize 의 instructions · 쓰기 응답의
#: 정체 스탬프(`_IDENTITY_TOOLS`) · `vinc_whoami`. 셋 중 하나를 걷어내려면 기본값을
#: 함께 봐야 한다.
#: 근거(왜 뒤집었나·A안을 왜 골랐나):
#: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
_SPACE_PROP = {"space": {
    "type": "string",
    "description": "Which space this call acts on: omit (or 'personal') for your personal "
                   "graph, or a team's id for that team's shared graph. Call vinc_whoami to "
                   "see the ids you can reach and your role in each. Your role there decides "
                   "what you may do, and a space you are not an active member of answers the "
                   "same way as one that does not exist. Ignored when you connected at a team "
                   "address (/t/<team-uuid>/mcp) — there the address already fixed the space."}}


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
        else:
            # `allow_target=False` 는 이 파일에서 곧 **호스티드 프로파일**이다(위
            # 독스트링: 허브만 False 로 부른다). `space` 는 계정이 있는 곳에서만
            # 뜻이 있으므로 같은 갈림에 얹는다 — 로컬 stdio 에는 고를 스페이스가 없다.
            props.update(_SPACE_PROP)
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
def _shrink_brief_result(out: dict, budget: int) -> dict | None:
    """vinc_brief 결과를 예산 안으로 줄인다 — **발췌를 먼저, 관계를 나중에** 덜어낸다.

    관계는 그 노드의 근거 지도라 마지막까지 남긴다. 발췌는 `grounds` 에 doc_id 가
    남으므로 vinc_doc_get 으로 되찾을 수 있다 — 덜어내도 길이 끊기지 않는다는 뜻이다.
    줄일 수 없으면 None 을 돌려 기존 거부 경로로 보낸다(조용한 절단보다 거부가 정직하다).
    근거: [[vinc.plan.mcp-agent-surface §4]]"""
    if not isinstance(out, dict):
        return None

    def _size() -> int:
        return len(json.dumps(out, ensure_ascii=False))

    ex = out.get("excerpts")
    links = out.get("links")
    items = links.get("items") if isinstance(links, dict) else None
    dropped_ex = dropped_ln = 0
    if isinstance(ex, list):
        while ex and _size() > budget:
            ex.pop()
            dropped_ex += 1
    if isinstance(items, list):
        while items and _size() > budget:
            items.pop()
            dropped_ln += 1
    if _size() > budget:
        return None
    if dropped_ex or dropped_ln:
        out["shrunk"] = {"excerpts_dropped": dropped_ex, "links_dropped": dropped_ln}
        note = ("shrunk to fit the result size limit: dropped %d excerpt(s) then %d "
                "link(s); `grounds` still lists every id" % (dropped_ex, dropped_ln))
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + note
    return out


SHRINKERS = {"vinc_search": _shrink_search_result,
             # W1 — brief 는 조밀한 이웃에서 예산을 넘길 수 있다. 정책으로
             # 강등한다: 발췌부터 덜고 관계는 마지막까지 남긴다.
             "vinc_brief": _shrink_brief_result}


def call(name: str, args: dict | None, bridge: Bridge,
         forced_owner: str | None = None,
         entitlements: dict | None = None,
         allow_target: bool = True) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = dict(args or {})                     # 복사 — 호출자 dict 불변, target 제거
    target = (args.pop("target", None) or "local").lower()
    # `space` 는 **라우팅 메타데이터**지 도구 인자가 아니다. 판정은 이미 HTTP 층에서
    # 끝났으므로(멤버십·역할) 여기서는 벗기기만 한다.
    #
    # ⚠ 새는 자리는 사이드카가 아니라 **원격 전달**이다. 사이드카 핸들러는 params 를
    # 명시로 조립하니 모르는 키가 흘러갈 길이 없는데, `remote.call` 은 `owner`·`target`
    # 만 빼고 **전부 넘긴다**. 안 벗기면 데스크탑에서 `target:"remote"` 로 부른 호출이
    # 스키마에 광고하지도 않은 스페이스 선택을 허브에서 하게 된다 — 그 표면이 열려야
    # 한다면 데스크탑 스키마에 `space` 를 싣는 것이 순서고, 여기로 몰래 들어오면 안 된다.
    args.pop("space", None)
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
        # 어휘 테마도 **서버가 정한다.** 테마 파일에는 소유 열이 없어서, 클라이언트가
        # theme_id 를 고를 수 있으면 남의 어휘에 자기 타입이 proposed 로 꽂히고(감사
        # 2026-08-18 M1 · [[record:risk-themes-global]]), 아무도 안 고르면 전원이 활성
        # 테마 하나에 고인다. 소유자에서 파생하면 열을 안 늘리고 둘 다 닫힌다.
        # 웹 평면은 이미 이 값을 벗기고 있었는데, 인증된 MCP 경로가 그 자리를 안 지나서
        # 그대로 통과했다. 여기가 두 평면이 만나는 자리다 — owner 를 강제하는 그 줄 옆.
        # ⚠ 이 도구가 theme_id 를 받을 때만 넣는다. 안 받는 도구의 args 를 늘리면
        #   스키마와 어긋난 값이 실려 다닌다.
        if "theme_id" in (tool["inputSchema"].get("properties") or {}):
            from store.theme_store import owner_theme_id
            args["theme_id"] = owner_theme_id(forced_owner)
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
        out = remote.call(name, args)
    else:
        out = tool["run"](args, bridge)
    return _stamp_identity(name, out, target, allow_target)


#: 정체(어느 그래프에 썼는가)를 싣는 도구 셋. **전부에 달지 않는다** — 조회 스무 개에
#: 같은 두 줄이 붙으면 잡음이고 잡음은 안 읽힌다. 첫 쓰기 전에 부르는 자리 하나와
#: 쓰기 둘이면 오배치가 조용한 곳을 덮는다:
#: [[decision:identity-rides-on-the-first-door-not-a-whoami]].
#: 도구 → 팀 역할 등급. **주석표에서 유도한다** — 세 번째 표를 만들지 않는다.
#:
#: op 표(`_EDITOR_OPS`/`_ADMIN_OPS`)로는 못 짠다: 그 표는 **프록시 op** 로 키가 잡혀
#: 있는데 도구 22개 중 6개(neighbors·path·merge_candidates·merge_reject·
#: merge_rejections·diagnose)는 프록시 op 가 아예 없다. 대조 실측(2026-08-25): op 로
#: 매핑되는 16개에서 주석 유도 등급과 두 표가 **불일치 0** 이고, 유도는 고아 6개까지
#: 덮는다.
#:
#: 주석이 없으면 **admin** 이다(fail-closed) — 새 도구가 등급 없이 들어와도 조용히
#: 열리지 않는다. `tests/test_mcp_space_address.py` 가 전 도구의 주석 실재를 못 박는다.
def tool_tier(name: str) -> str:
    """`viewer` · `editor` · `admin`. 모르는 도구는 admin(닫힘)."""
    a = _ANNOTATIONS.get(name)
    if not a:
        return "admin"
    if a.get("destructiveHint"):
        return "admin"
    if a.get("readOnlyHint"):
        return "viewer"
    return "editor"


#: 그 역할이 그 등급을 할 수 있나. 모르는 역할은 거부(fail-closed) — 계정의
#: `roleAllows` 와 같은 태도이고, 거기와 어긋나면 그 op 만 조용히 갈린다.
_TIER_ORDER = {"viewer": 0, "editor": 1, "admin": 2}


def role_allows(role: str, tool: str) -> bool:
    have = _TIER_ORDER.get((role or "").strip())
    need = _TIER_ORDER[tool_tier(tool)]
    return have is not None and have >= need


_IDENTITY_TOOLS = frozenset({"vinc_conventions", "vinc_ingest", "vinc_doc_put"})


def _stamp_identity(name: str, out: dict, target: str, allow_target: bool = True) -> dict:
    """응답에 `target` 을 얹는다. `owner` 는 **손대지 않는다** — 쓴 쪽(사이드카·허브)이
    자기가 실제로 스탬핑한 값을 이미 싣고 오고, 여기서 클라이언트가 아는 값으로
    덮으면 호스티드에서 거짓말이 된다(거기서는 키가 owner 를 고정하고 인자는 무시된다).
    자기 실수에 동의해 주는 답이 제일 나쁘다.

    ⚠ **자기가 곧 원격인 서버는 이 필드를 안 찍는다**(`allow_target=False` — 허브
    프로파일). 라이브에서 물린 자리다(2026-08-17 배포 직후 실측): 허브도 이 깔때기를
    도는데 거기서 target 은 언제나 "local" 이라, 원격으로 간 호출이 `"target":
    "local"` 을 받아 왔다. 그 값을 "remote" 로 덮는 것은 **새 클라이언트**뿐이고,
    데스크탑 업데이트는 사용자 클릭이라 구 클라이언트 창이 한동안 간다.

    위 독스트링이 경계하던 바로 그 고장을 이 필드가 만들고 있었다 — 계정 그래프에
    썼는지 확인하려고 target 을 본 세션이 반대 답을 듣고 로컬에 다시 쓸 수 있다.
    고침의 방향은 더 정확한 값을 지어내는 것이 아니라 **아예 답하지 않는 것**이다:
    호출자의 local/remote 개념은 호출자만 안다."""
    if allow_target and name in _IDENTITY_TOOLS and isinstance(out, dict):
        out["target"] = target
    return out
