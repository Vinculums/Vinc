# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

**27종** — 읽기 19(search·node·role·edges·brief·recall·graph·decide·domains·suggest_ties·neighbors·
path·merge_candidates·merge_rejections·diagnose·doc_get·doc_list·conventions·whoami) + 쓰기 8
(ingest·delete·delete_edge·merge_nodes·merge_reject·doc_put·doc_extract·doc_delete).
"단일 write 표면(ingest 하나)"은
SEC-3(삭제·병합)과 D45(문서 정본 doc_*)를 거치며 깨진 낡은 서술이었다 —
아래 TOOLS 리스트가 정본이고 이 독스트링은 그 요약일 뿐이다.
⚠ 이 숫자는 F10-b 로 merge_reject 가 늘었을 때 19 인 채로 남아 어느 집계에도 안
들어갔다("TOOLS 가 정본"이라는 방어 문장을 단 채로 틀렸다). 그래서 이제
tests/mcp/test_tool_contract.py 가 이 독스트링의 수와 len(TOOLS) 를 대조한다.

주제·어휘(themes·types)의 **뮤테이션**(재가·거부·개명)은 HTTP 전용이며 MCP 에 노출하지
않는다 — 어시스턴트는 제안만 하고 자기 제안을 스스로 재가할 수 없다(의도된 비대칭).
읽기 쪽은 `vinc_conventions` 가 관례 문서와 함께 재가된 어휘를 실어 나른다(GM-1):
그 전까지 어시스턴트는 어휘를 **쓰고 나서** `proposed_types` 로만 알 수 있었다.
핸들러는 계약 검증 후 브리지 호출뿐(스켈레톤 §4). owner 미지정 시 브리지
기본 owner(tenant:local)를 주입한다 — MCP 호스트는 로컬 소유자 개념을 모른다.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Callable

from .bridge import Bridge

# vinc_mcp 는 api 를 임포트하지 않으므로(독립 브리지 계약) 허용 쌍 문장을 여기
# 미러한다. 정본은 `api/fragment.py` 의 `_ALLOWED_PAIRS_HINT` 이고, 서버가 거부할 때
# warnings 에 싣는 것도 그 문장이다. 두 사본의 일치는 tests/mcp/test_tool_contract.py 의
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
    "returns_to = distinct role concepts, with nonempty evidence; "
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
                                                  "preserves the stored value. SET IT when this record "
                                                  "asserts a present state of the product, infra or "
                                                  "process, because the default read path now returns "
                                                  "current claims only. Leaving it empty is not an error "
                                                  "and does not hide the record — unstated reads as "
                                                  "current — but a record that will one day be replaced "
                                                  "cannot be retired without a start."},
                    "valid_until": {"type": "string",
                                    "description": "Record only — when the claim stopped being true "
                                                   "(half-open: the claim does not hold at this instant). "
                                                   "Setting it is how a record is invalidated without "
                                                   "deleting it — resend just {id, type, valid_until}. "
                                                   "Same format contract as valid_from; empty string "
                                                   "clears it (the claim holds again). This is the ONLY field that "
                                                   "removes a node from the default read: an unstated "
                                                   "valid_from does not. When you supersede a claim, "
                                                   "set this on the old one to the date the new one "
                                                   "starts, and do not delete it — a retired claim is "
                                                   "still a true description of what held then."},
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
                                                "kept on concept↔concept edges, about, and record edges "
                                                "(stored on ATTACHES_TO)."},
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
        "historical": "true" if args.get("historical") else None,
        "as_of": args.get("as_of"),
        "owner": _owner(args, bridge)})


def _t_recall(args, bridge):
    # F3: `debug` 를 줘야만 내부 실행 계획(plan.cypher)이 실린다. 기본은 안 실린다 —
    # 여태 매 응답에 Cypher 전문과 owner UUID 가 딸려 왔다(실측 17.9KB 중 상당부).
    return bridge.request("GET", "/recall", params={
        "owner": _owner(args, bridge), "domain": args.get("domain"),
        "tags": ",".join(args["tags"]) if args.get("tags") else None,
        "limit": args.get("limit", 50),
        # MCP 는 늘 페이지 모양으로 받는다(has_more·next_cursor). 사이드카의 다른
        # 호출자는 page 를 안 보내서 예전 모양 그대로다.
        # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S3.
        "page": "true", "cursor": args.get("cursor"),
        "debug": "true" if args.get("debug") else None})


#: vinc_graph depth 2 이상의 노드 예산(시드에서 닿는 개념 수). 넘으면 깊이를 층
#: 단위로 줄인다. 값의 크기: depth 1 결과가 노드당 ~780자(concept:signal-design 90노드
#: 69,990자)였고 개념 하나에 부착(record·episode)이 딸려 오므로, 이 수를 넘기면 결과
#: 대부분이 축소기에서 다시 덜려 나간다 — 받아도 못 싣는 층을 계산하지 않는다.
#: 근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]
GRAPH_NODE_BUDGET = 120


def _t_graph(args, bridge):
    review = args.get("review_mirror", False)
    if type(review) is not bool:
        raise ValueError("review_mirror must be a boolean")
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
            if review and len(got["ids"]) != 1:
                raise ValueError("review_mirror requires one exact document mirror seed")
            # 임의 선택은 시드가 여럿이다. 이 도구는 하나만 받으므로 첫 개를 쓰고
            # **그렇게 했다고 말한다** — 조용히 고르면 나머지를 본 줄 안다.
            seed = got["ids"][0]
        elif got["kind"] == "doc":
            # 문서 주소다. 다음 호출을 **그대로 적어 준다** — 스페이스까지. 이 호출은
            # 주소의 스페이스로 이미 왔지만 vinc_doc_get 에는 url 이 없어서, 안 적어
            # 주면 다음 호출이 개인으로 떨어진다.
            sp = viewurl.space_of(str(args["url"]))
            nxt = f"vinc_doc_get doc_id={got['doc']}"
            if sp and sp not in (viewurl.PERSONAL, viewurl.LOCAL):
                nxt += f" space={sp}"
            return {"kind": "graph", "executed": False, "seed": "",
                    "note": f"that url selects a doc, not one node: read it with {nxt}"}
        elif got["kind"] in ("domains", "all"):
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
    explicit = depth is not None
    if review and (not seed.startswith("concept:doc.")
                   or not seed[len("concept:doc."):] or "\x1f" in seed):
        raise ValueError("review_mirror requires one exact logical concept:doc.<doc_id> seed")
    want = depth if explicit else 2
    params = {"owner": _owner(args, bridge), "seed": seed, "depth": want}
    if review:
        params["review_mirror"] = True
    if args.get("select") in ("layers", "prize"):
        params["select"] = args["select"]
    # depth 2 이상에는 노드 예산을 붙인다. 사이드카는 도달 집합이 예산을 넘으면 깊이를
    # 층 단위로 줄이고(최소 1) `depth_budget` 으로 알린다 — 허브의 depth 2+ 가 Bridge
    # 60초에 걸려 죽던 것을 무거운 단계(행 조회·축 합류·배치) **전에** 끊는다.
    # 예산은 이 핸들러에만 있다: View L2·공유 팩·동기화는 이 인자를 안 보낸다.
    # 근거: [[decision:vinc-graph-node-budget-lives-in-the-mcp-handler-2026-09-24]]
    try:
        if int(want) > 1:
            params["max_nodes"] = GRAPH_NODE_BUDGET
    except (TypeError, ValueError):
        pass
    out = bridge.request("GET", "/graph", params=params)
    if (not (args.get("seed_id") or "").strip()) and args.get("url")             and isinstance(out, dict):
        out.setdefault("from_url", seed)
    if isinstance(out, dict) and isinstance(out.get("depth_budget"), dict):
        b = out["depth_budget"]
        why = ("depth was not given and this seed is a hub, so it got the lower default"
               if not explicit else "the requested depth was lowered")
        msg = (f"served depth {b.get('served_depth')} instead of "
               f"{b.get('requested_depth')}: depth {b.get('requested_depth')} reaches "
               f"{b.get('reach_at_requested')} concepts, over this tool's budget of "
               f"{b.get('max_nodes')}; {why}. Walk further with vinc_neighbors "
               f"node={seed}, or seed on one of the neighbours")
        out["depth_note"] = msg
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


def _t_edges(args, bridge):
    return bridge.request("GET", "/node-edges", params={
        "owner": _owner(args, bridge), "node": args.get("node"),
        "nodes": json.dumps(args["nodes"]) if "nodes" in args else None,
        "type": args.get("type"), "dir": args.get("dir", "both"),
        "limit": args.get("limit", 500)})


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
    # (Leiden 군집) | community_context(시드 하나의 군집 + 대표 발췌). 계산은 사이드카의
    # `api/graph_algo.py` 가 한다. 스토어를 못 읽으면 사이드카가 executed=False 로 강등.
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
    body = {"node": args["node_id"], "owner": _owner(args, bridge)}
    if "dry_run" in args:
        body["dry_run"] = args["dry_run"]
    return bridge.request("POST", "/delete", body=body)


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
        "origin": args.get("origin"), "topic": args.get("topic")})


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


def _t_node(args, bridge):
    return bridge.request("GET", "/node", params={
        "owner": _owner(args, bridge), "node_id": args["node_id"]})


def _t_role(args, bridge):
    from .roles import contract
    return contract(args.get("role"), bridge, forced_owner=_owner(args, bridge))


def _t_brief(args, bridge):
    # W1 — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기(생성 없음).
    out = bridge.request("GET", "/brief", params={
        "owner": _owner(args, bridge), "query": args["query"],
        "max_excerpts": args.get("max_excerpts", 3),
        "historical": "true" if args.get("historical") else None,
        "as_of": args.get("as_of")})
    from .roles import is_role_id
    if is_role_id(args["query"].strip()) and out.get("executed"):
        role = _t_role({**args, "role": args["query"].strip()}, bridge)
        if role.get("found") or not role.get("executed"):
            out = {**out, "role": role}
    return out


#: ChatGPT deep research / company knowledge 는 이름이 정확히 `search`·`fetch` 인
#: 읽기 쌍만 조회한다. 허브 프로파일에만 노출하고, 구현은 vinc_search / vinc_doc_get
#: (노드는 vinc_brief) 의 얇은 별칭이다.
#: 근거: [[vinc.canon.mcp-contract]]
_CITE_ORIGIN = (os.environ.get("VINC_CITE_ORIGIN") or "https://vincs.io").rstrip("/")
_NODE_PREFIXES = ("concept:", "record:", "decision:", "episode:", "domain:", "bridge:")


def _cite_url(kind: str, ident: str, owner: str = "") -> str:
    """인용용 URL. 비어 있으면 ChatGPT 가 출처로 만들지 않는다.

    앱이 복사하는 주소와 **같은 모양**이다: `/app/` 아래, 해시 앞에 `?space=`.
    전에는 사이트 루트(`/#view/…`)를 가리켰는데 루트는 `#view` 를 모르므로 인용을
    누르면 랜딩이 떴고, 스페이스가 없어 팀 결과도 개인 주소로 나갔다.
    근거: [[decision:a-node-address-names-its-space]]"""
    owner = str(owner or "")
    space = owner[len("team:"):] if owner.startswith("team:") else "personal"
    where = "doc" if kind == "doc" else "trace"
    return f"{_CITE_ORIGIN}/app/?space={space}#view/{where}/{ident}"


def _t_host_search(args, bridge):
    # 감사 §5 #19 의 세 가지를 여기서 닫는다.
    #  ① 제목은 문서 메타(`doc_title`, 사이드카가 단다)다. 발췌 첫 줄을 제목으로 쓰면
    #    "```mermaid …"·"--- doc_id: …" 가 인용 제목으로 나갔다. 구 사이드카처럼
    #    제목이 없으면 doc_id 로 대신한다 — 본문 조각을 제목으로 꾸미지 않는다.
    #  ② `limit` 은 문서와 노드를 **합친** 수다. 여태 양쪽에 따로 걸려 20 이 34 가 됐다.
    #    둘을 번갈아 채워, 한쪽이 상한을 다 먹어 다른 쪽이 통째로 빠지지 않게 한다.
    #  ③ fetch 가 못 여는 id 는 내지 않는다. fetch 는 brief 로 노드를 여는데 brief 는
    #    domain:·bridge: 를 못 연다 — 검색이 내고 fetch 가 NotFound 로 답하면 인용이 깨진다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S12, §5 P8
    fetchable = ("concept:", "record:", "decision:", "episode:")
    limit = int(args.get("limit", 20))
    raw = _t_search({"query": args["query"], "limit": limit,
                     "owner": args.get("owner")}, bridge)
    if not isinstance(raw, dict):
        return {"results": []}
    seen: set[str] = set()
    docs: list[dict] = []
    for row in raw.get("rows") or []:
        if not isinstance(row, dict):
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        title = str(row.get("doc_title") or "").strip()[:160] or doc_id
        docs.append({"id": f"doc:{doc_id}", "title": title,
                     "url": _cite_url("doc", doc_id, args.get("owner"))})
    nodes: list[dict] = []
    for hit in raw.get("node_hits") or []:
        if not isinstance(hit, dict):
            continue
        nid = str(hit.get("id") or "").strip()
        if not nid.startswith(fetchable) or nid in seen:
            continue
        seen.add(nid)
        title = str(hit.get("title") or nid).strip() or nid
        nodes.append({"id": nid, "title": title,
                      "url": _cite_url("node", nid, args.get("owner"))})
    results: list[dict] = []
    for i in range(max(len(docs), len(nodes))):
        results += docs[i:i + 1] + nodes[i:i + 1]
    return {"results": results[:limit]}


class NotFound(Exception):
    """없는 id 를 받은 읽기. ValueError·KeyError 가 **아니어야** 한다 — 그 둘은
    rpc 가 프로토콜 오류(-32602)로 올리고, 그 밖의 예외는 `isError:true` 인 도구
    결과로 강등한다(rpc.py `tools/call`). 빈 성공은 인용 가능한 답으로 읽힌다."""


def _fetch_doc_or_fail(ident: str, raw) -> dict:
    # /doc 이 오류를 내면(없는 id · 남의 문서 · 교체 중) 빈 text 로 성공을 꾸미지
    # 않고 도구 오류로 올린다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
    if not isinstance(raw, dict):
        raw = {}
    err = raw.get("error")
    if err or not raw.get("executed", True):
        hint = str(raw.get("hint") or "").strip()
        raise NotFound(f"{err or 'unknown doc_id'}: {ident}"
                       + (f". {hint}" if hint else ""))
    return raw


def _t_host_fetch(args, bridge):
    ident = str(args.get("id") or "").strip()
    if ident.startswith("doc:"):
        doc_id = ident[4:]
        raw = _fetch_doc_or_fail(ident, _t_doc_get(
            {"doc_id": doc_id, "owner": args.get("owner")}, bridge))
        title = str(raw.get("title") or doc_id)
        return {"id": ident, "title": title, "text": str(raw.get("text") or ""),
                "url": _cite_url("doc", doc_id, args.get("owner")),
                "metadata": {"kind": "document", "verified": raw.get("verified")}}
    if ident.startswith(_NODE_PREFIXES):
        raw = _t_brief({"query": ident, "owner": args.get("owner")}, bridge)
        if not isinstance(raw, dict):
            raw = {}
        node = raw.get("node") if isinstance(raw.get("node"), dict) else {}
        # fetch 의 id 는 정확한 id 다. 그 노드 자체가 열린 경우만 답한다 — 다른
        # 노드(랭킹 승자·접두 되물림)를 그 id 의 본문으로 내보내지 않는다.
        if raw.get("status") != "resolved" or node.get("id") != ident:
            raise NotFound(f"node not found: {ident}")
        title = str(node.get("title") or ident)
        parts = [str(raw.get("one_liner") or "").strip()]
        for ex in raw.get("excerpts") or []:
            if isinstance(ex, dict) and ex.get("excerpt"):
                parts.append(str(ex["excerpt"]))
        return {"id": ident, "title": title, "text": "\n\n".join(p for p in parts if p),
                "url": _cite_url("node", ident, args.get("owner")),
                "metadata": {"kind": node.get("kind") or "node",
                             "status": raw.get("status")}}
    raw = _fetch_doc_or_fail(ident, _t_doc_get(
        {"doc_id": ident, "owner": args.get("owner")}, bridge))
    title = str(raw.get("title") or ident)
    return {"id": ident, "title": title, "text": str(raw.get("text") or ""),
            "url": _cite_url("doc", ident, args.get("owner")),
            "metadata": {"kind": "document", "verified": raw.get("verified")}}


TOOLS: list[dict] = [
    {
        "name": "vinc_search",
        "description": "Hybrid full-text + vector search over the local Vinc store. "
                       "Returns two result sets: `rows` = text-chunk matches (BM25/vector) "
                       "carrying an excerpt (`snippet`, `truncated`, `doc_id`) rather than the "
                       "full chunk text — fetch the whole document with vinc_doc_get(doc_id); "
                       "pass detail:'full' only when you truly need raw chunk text. The "
                       "excerpt is the 240-character window of the chunk that holds the most "
                       "query terms; it starts with `…` when that is not the chunk's start, "
                       "and is the first 240 characters when nothing matches. "
                       "Each row and node hit carries `cite_id`, the existing node to cite; "
                       "document rows use their readable concept:doc.<doc_id> mirror. "
                       "A missing or unreadable mirror gives cite_id:null and cite_unavailable; "
                       "never construct a node citation from the document key yourself. "
                       "`keyword_gated:true` means the keyword list was left out of the "
                       "fusion because none of its top rows covered half of the query's "
                       "terms (typically a Korean question over English text), so the order "
                       "is the semantic arm's. "
                       "`node_hits` = Concept/Record matches on their title/one_liner/props "
                       "(each with id, title, kind, domain, matched_in), ALWAYS populated by a "
                       "title-substring scan, so concepts and records are findable even in a "
                       "store with zero indexed text chunks. Responses are paginated: "
                       "`total_count`, `has_more` and `next_cursor` describe the result window; "
                       "pass `cursor` to continue where the last page ended (an invalid or "
                       "expired cursor errors explicitly instead of restarting from page one). "
                       "The cursor pages `rows` only: `node_hits` come on the first page, and a "
                       "continuation page carries an empty list with `node_hits_omitted`. "
                       "`total_count_capped:true` means the candidate pool hit its ceiling, so "
                       "`total_count` is a lower bound, not the whole match count. "
                       "Call this before vinc_ingest to find existing node ids to reuse. If the "
                       "backend search fails, a top-level `error` string says why (e.g. "
                       "index_missing vs extension_unavailable) instead of silently returning "
                       "empty. `vector_note` appears when the semantic arm did not run OR "
                       "when `encoder_runtime_warning:true` reports unknown/mismatched "
                       "stored and query encoder versions; in that case the arm may have run "
                       "and results may be less accurate. Other notes have two causes to "
                       "distinguish. Right after a document write the "
                       "index is rebuilding: benign, `arms` reads \"fts\", the rebuild runs in "
                       "the background and the call never waits for it, so semantic matches return "
                       "on a later search and there is nothing to report. Apart from the "
                       "runtime warning, ANY OTHER VALUE IS A "
                       "FAILURE of the semantic arm (a "
                       "timeout, a missing index, an unavailable extension) and the rows you are "
                       "holding were found by text matching alone. Say so when you answer: a "
                       "degraded search and a complete one look identical from the results, so "
                       "silence here reads as \"nothing else exists\".",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            "cursor": {"type": "string",
                       "description": "Opaque next_cursor from a previous page."},
            "detail": {"type": "string", "enum": ["summary", "full"],
                       "default": "summary"},
            "historical": {"type": "boolean", "default": False,
                           "description": "Include nodes whose claim is no longer current "
                                          "(valid_until in the past, or a Decision whose "
                                          "props.valid_until has passed). The default hides "
                                          "them and reports how many in `validity_filtered`; "
                                          "pass true when the question is about what USED to "
                                          "hold."},
            "as_of": {"type": "string",
                      "description": "Read the graph as it held during a period: YYYY, YYYY-MM or "
                                     "YYYY-MM-DD, or 'query' to take the one absolute date written "
                                     "in the question (e.g. '2026-08', 'August 2026', "
                                     "'2026년 8월', 'in 2025'). Hides claims that did not hold at "
                                     "any time in that period, counted in `validity_filtered`, and "
                                     "echoes what it used in `time_scope`. With 'query', a question "
                                     "with no date or with two different dates is read with "
                                     "today's view and `time_scope.applied` is false. Omit for "
                                     "today's view (the default, unchanged)."},
            "owner": {"type": "string"},
        }, "required": ["query"]},
        "run": _t_search,
    },
    {
        "name": "vinc_role",
        "description": "Read one role contract by agent slug or concept id in one call, below 10000 UTF-8 bytes. "
                       "Returns id, unchanged props, directed works_with groups with exact edge endpoints, "
                       "type, evidence and confidence, and a compact rendered contract fenced as graph data. "
                       "An unknown, foreign or non-role node returns found:false. Oversized contracts return "
                       "an explicit error without truncating permissions. Omit role to list the role roster "
                       "without full contracts. This reads only roles in agent or vinc/agent topics and "
                       "their direct child topics; it does not execute or delegate the role. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "role": {"type": "string", "description": "Role slug (agent-dev-core) or exact concept:agent- id; omit to list."},
            "owner": {"type": "string"}}, "required": []},
        "run": _t_role,
    },
    {
        "name": "vinc_node",
        "description": "Read ONE node by exact id without its neighbors, document excerpts or layout. "
                       "Returns {kind:'node', executed, found, node:{id,kind,...stored fields}}. "
                       "The node includes every field its stored kind carries, including props as an "
                       "object, tags, content_time and valid_from/valid_until where those columns exist. "
                       "Fields absent from that kind's schema are not invented. Exact reads include "
                       "retired claims; read valid_from/valid_until before treating one as current. "
                       "found:false and node:null mean the id does not exist in the selected graph, "
                       "including an id owned by another account or space. executed:false means the "
                       "store could not be read; say so rather than treating it as a missing node. "
                       "Use vinc_brief for joined answer material and vinc_neighbors to walk relations. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node_id": {"type": "string", "description": "Exact stored node id, including its type prefix."},
            **_OWNER_PROP,
        }, "required": ["node_id"]},
        "run": _t_node,
    },
    {
        "name": "vinc_brief",
        "description": "One question in, joined answer material out — the deterministic read that "
                       "replaces a vinc_search + vinc_graph + vinc_doc_get chain. A question that "
                       "CONTAINS A NODE ID (concept:x, record:x, decision:x, episode:x) resolves to "
                       "that node directly, skipping search and ranking (resolved_via:'id'); an id "
                       "that fails to load answers status:'none' with that id in `unreadable`, and "
                       "no other node is ranked in its place. Otherwise it resolves the "
                       "question to ONE node by strict ranking, then returns that node's description, "
                       "its canonical document excerpts and its typed relations in a single call. "
                       "Returns {kind, executed, query, status, node:{id,title,domain,kind,valid_from,"
                       "valid_until,validity,superseded_by}, one_liner, "
                       "body_src, body_cite, excerpts:[{doc_id,doc_title,section,excerpt,method}], "
                       "links:{degree,out,in,attached,total,items:[{id,title,domain,type,dir,evidence,"
                       "confidence,note}],truncated}, candidates:[...], grounds:[id...]}. "
                       "Nodes, candidates, links and excerpts carry `cite_id`; "
                       "`cite_grounds` lists only canonical node citations while `grounds` "
                       "keeps the original source keys. Excerpt cite_id is null when its "
                       "document mirror is missing or unreadable. "
                       "READ `status` FIRST: 'resolved' means one node won strictly, so node/excerpts/"
                       "links are filled; 'choose' means the leading candidates TIED and nothing was "
                       "guessed — pick one from `candidates` (each carries hit_tokens/title_tokens, the "
                       "numbers that tied) and ask again with the candidate's id; an exact title works "
                       "only when no other node shares it; 'none' means the question "
                       "matched nothing — unless `unreadable` is present, which means the named nodes "
                       "could not be loaded (an id in the question that does not exist, or a match "
                       "deleted or re-owned between search and read): a different next move, so branch on that field, not the note. "
                       "`search_error` means the document-search arm failed: the answer came from the "
                       "node arms alone, so a 'none' carrying it is not evidence that nothing matches. "
                       "`partial:true` means the node was read but its excerpts or relations were not "
                       "(`partial_note` says which), so empty `links` there do not mean an isolated node. "
                       "NOTHING IS GENERATED: `one_liner` is quoted, never written — "
                       "`body_src` says whether it came from the one_liner column, a props key, or a "
                       "document excerpt, and `body_cite` names the document when it is an excerpt. "
                       "`grounds` lists every id the material rests on, so an answer built from this "
                       "call can cite its sources; it stays complete even when the result was shrunk to "
                       "fit the size limit. "
                       "`links.degree` IS NOT THE NODE'S DEGREE: it counts the concept-and-decision "
                       "relation layer only (out + in), and `attached` is the REMAINDER it leaves "
                       "out (the records, episodes and bridges returned in the same list). `total` "
                       "is the node's connectivity, the same number vinc_neighbors reports as `count`; "
                       "read that one when you want how connected a node is, and never sum degree "
                       "across nodes as if it were. "
                       "`links.items[].evidence` is a BOOLEAN (does this relation "
                       "carry a source sentence) and `evidence_text` is that sentence when one exists, "
                       "so a relation can be QUOTED rather than merely counted. "
                       "`node.validity` is 'current', 'historical' or 'future', derived from the "
                       "node's OWN interval (a Record's columns, a Decision's props); when it is not "
                       "current a `validity_note` says so and names `superseded_by` if the graph "
                       "holds a successor. A node reached by id skips the default filter, so this "
                       "is the one place a retired claim announces itself: do not quote a "
                       "historical node as the present state. "
                       "Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "The question, in natural language."},
            "max_excerpts": {"type": "integer", "default": 3, "minimum": 1, "maximum": 12,
                             "description": "How many canonical document excerpts to "
                                            "attach to a resolved node."},
            "historical": {"type": "boolean", "default": False,
                           "description": "Include nodes whose claim is no longer current "
                                          "(valid_until in the past, or a Decision whose "
                                          "props.valid_until has passed). The default hides "
                                          "them and reports how many in `validity_filtered`; "
                                          "pass true when the question is about what USED to "
                                          "hold."},
            "as_of": {"type": "string",
                      "description": "Read the graph as it held during a period: YYYY, YYYY-MM or "
                                     "YYYY-MM-DD, or 'query' to take the one absolute date written "
                                     "in the question (e.g. '2026-08', 'August 2026', "
                                     "'2026년 8월', 'in 2025'). Hides claims that did not hold at "
                                     "any time in that period, counted in `validity_filtered`, and "
                                     "echoes what it used in `time_scope`. With 'query', a question "
                                     "with no date or with two different dates is read with "
                                     "today's view and `time_scope.applied` is false. Omit for "
                                     "today's view (the default, unchanged)."},
            "owner": {"type": "string"},
        }, "required": ["query"]},
        "run": _t_brief,
    },
    {
        "name": "vinc_recall",
        "description": "Read back the episode timeline — what was recorded as having HAPPENED, newest first; "
                       "episodes with no date come last. "
                       "Returns {kind, executed, rows:[{id,title,date,owner,domain,tenant,workspace,summary,"
                       "pinned,tags,access_count,last_access}], has_more, next_cursor}. When `has_more` is "
                       "true, pass `next_cursor` back as `cursor` with the same other arguments to read the "
                       "next page; a cursor from a different query is refused with `error` (invalid_cursor), "
                       "and one whose timeline changed meanwhile with stale_cursor, never a silent restart. "
                       "`domain` covers the whole subtree ('vinc/agent' includes 'vinc/agent/roles'); "
                       "`tags` keeps episodes carrying at least one of the given tags. "
                       "`tenant`/`workspace` and `access_count`/"
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
            "cursor": {"type": "string",
                       "description": "Opaque next_cursor from a previous page of the same query."},
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
                       "if you only have a phrase, run vinc_search first. Concept and decision seeds both expand. "
                       "depth 2 is the working default, 4 the ceiling, and depth 2 and beyond carry a node "
                       "budget: when the seed reaches more concepts than the budget allows, the depth is "
                       "lowered a whole layer at a time (never below 1) and the result says so in "
                       "`depth_budget` and `depth_note`. That is how a hub seed with no `depth` given comes "
                       "back at depth 1. A result that is still too large is shrunk rather than refused: "
                       "the seed stays whole, the ring may come back as id, label and kind only, and if even "
                       "that does not fit the least connected neighbours are left out and counted in "
                       "`ring_truncated`; read the full ring with vinc_neighbors. `shrunk` and `note` count "
                       "what was kept. `rev` and `updated_at` are the node's current version, so you can hold what "
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
                                   "ignored; `depth` given explicitly beats the url's. "
                                   "The address also names the space it was copied in "
                                   "(?space=personal or a team id, before the #): the call "
                                   "goes to that space, so pass the url as is and leave "
                                   "`space` out. An explicit `space` beats the url's."},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "review_mirror": {"type": "boolean", "default": False,
                              "description": "For one exact concept:doc.<doc_id> seed only, "
                                             "include bounded read-only mirror_review provenance. "
                                             "No source text, repair or semantic census inference. "
                                             "Absent/unreadable seeds keep the ordinary not-found response."},
            "select": {"type": "string", "enum": ["layers", "prize"], "default": "layers",
                       "description": "How to fill the node budget when depth reaches more "
                                      "concepts than it allows. Both count the same thing: the "
                                      "seed plus that many concepts, with what is attached to "
                                      "them on top. 'layers' (default) lowers the depth until "
                                      "every concept within it fits, so 'every node within N "
                                      "hops' stays true. 'prize' keeps the requested depth but "
                                      "ranks only the concepts linked to the seed through other "
                                      "concepts, and keeps the connected set of them with the "
                                      "most ppr, so a far but central concept can beat a near "
                                      "but peripheral one. Concepts reached only through "
                                      "records, episodes, bridges, decisions or topics are not "
                                      "candidates and are left out; `selection` says how many "
                                      "were ranked and kept. 'prize' falls back to 'layers', and "
                                      "says so in `selection`, for a decision seed, when depth "
                                      "reaches more than 600 concepts, or when the ranked "
                                      "concepts already fit the budget. With 'prize' every node "
                                      "also carries `ppr_recent` (ppr with a 90-day recency "
                                      "half-life mixed in, counted from the newest node in "
                                      "the result)."},
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
        "name": "vinc_edges",
        "description": "Read actual directed incident edges for one exact node or a batch of "
                       "1..100 ids, without graph/node payloads. Supply either node or nodes. "
                       "Returns edges with from, to, type, evidence, confidence and derived; "
                       "unavailable properties are null. type is the physical relation label; "
                       "subtype preserves TYPED/ATTACHES_TO authored types. Filter by either. "
                       "dir defaults to both and preserves stored orientation. Includes structural "
                       "IN_DOMAIN, PARENT_OF and MENTIONS. missing_nodes does not distinguish "
                       "foreign from absent ids. count is the total; edges_truncated marks a cut. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string", "description": "Exact node id or domain:<name>."},
            "nodes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
            "type": {"type": "string"},
            "dir": {"type": "string", "enum": ["out", "in", "both"], "default": "both"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
            "owner": {"type": "string"},
        }, "oneOf": [{"required": ["node"], "not": {"required": ["nodes"]}},
                     {"required": ["nodes"], "not": {"required": ["node"]}}]}, "run": _t_edges,
    },
    {
        "name": "vinc_path",
        "description": "Find the shortest relation path(s) between two concepts. **Concept ids only**: this walks the concept layer, so a record, decision or episode id can never be an endpoint even when it exists and is well connected (it attaches to concepts rather than sitting on the path graph) — you get an explicit error saying so, not a bare not-found. Returns the physically "
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
            "a": {"type": "string", "description": "Exact source concept id (must start with `concept:`)."},
            "b": {"type": "string", "description": "Exact target concept id (must start with `concept:`)."},
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
                       "want linking (feeds vinc_suggest_ties). kind='communities' runs Leiden: "
                       "returns {communities:[{community,size,dominant_domain,quality,members}]} — the "
                       "measured clusters (distinct from the domain axis), a basis for proposing "
                       "themes (ratified by the user, never auto-written). kind='community_context' "
                       "(requires `node`) answers a broad 'what is over in this area' question with "
                       "no generation: takes ONE seed concept id, finds the Leiden community it "
                       "belongs to, and returns {community, size, quality, dominant_domain, "
                       "representatives:[{id,title,one_liner,excerpt}], members:[{id,title}], "
                       "members_truncated} — up to 5 representatives (ranked by how many chunks "
                       "mention them) carry a quoted source excerpt, the rest are id/title only. "
                       "executed:false with a note if the seed is unknown or isolated (community -1). "
                       "Member id lists are capped by `member_limit` (default 50): `size` always "
                       "reports the TRUE total, and a group whose list was cut carries "
                       "members_truncated:true — raise member_limit to see the rest. Computed in "
                       "Vinc over your own nodes only (no engine extension), and deterministic: "
                       "the same graph always yields the same groups. Leiden never returns a "
                       "disconnected community. `quality` carries numbers, not a verdict: "
                       "connected, internal_edges, boundary_edges, density, conductance (lower "
                       "is a cleaner cut) and modularity_share (negative means the group is no "
                       "better than chance). kind='claims' reads record props instead of "
                       "graph structure. It returns "
                       "{conflicts:[{subject_path,newer,older}], conflict_count, "
                       "unanchored:[{id,title,path_hint}], unanchored_count, anchored_count, "
                       "scanned}. `conflicts` pairs the most recent measurement of a file with "
                       "every earlier one that carries a different measured_at, so the question "
                       "it poses is 'does this older claim still stand'. It does NOT decide "
                       "which side is right; open the path and look. `unanchored` lists records "
                       "that name a file in their text but carry no props.subject_path, so they "
                       "cannot be compared with anything: that count is meant to be driven down "
                       "by adding the field. Both lists are capped by member_limit. "
                       "kind='domain_roots' is a steward topic census: root counts/shares, "
                       "largest_root (null on a tie), unassigned_nodes and domains outside "
                       "the largest root. Counts cover Concept, Decision, Record and Episode, "
                       "not derived Domain or Bridge. Multiple roots can be intentional; "
                       "this report never rejects writes or changes layout. Outside-domain "
                       "rows are capped by member_limit with members_truncated and full counts.",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["components", "communities", "community_context", "claims", "domain_roots"],
                     "description": "components (WCC orphans/islands) | communities (Leiden "
                                    "clusters) | community_context (one seed's cluster + "
                                    "representative excerpts, requires `node`) | claims "
                                    "(records that disagree about one file, and records about "
                                    "code with no file named) | domain_roots "
                                    "(read-only steward census of unexpected topic roots)."},
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
                       "Writing an Agent role (concept:agent-<name>)? Its contract is the "
                       "Roles section, which is always in the answer: when the owner's file "
                       "has none, the default section is inserted and `roles_section` reads "
                       "'default' (the stored file is unchanged). A role write that breaks the "
                       "contract is still stored, and vinc_ingest says what is wrong in "
                       "`warnings`. "
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
                       "the exact id via vinc_search/vinc_graph first. Use vinc_neighbors to "
                       "inspect its relations before deletion. Pass dry_run:true to read "
                       "the complete incident edge preview and count without changing the node, "
                       "edges, tombstones or jobs. An oversized preview is refused explicitly, "
                       "never truncated silently. The preview is a point-in-time read; subsequent "
                       "writes can change the eventual delete count. deleted.edges is a count "
                       "after the fact, not a pre-deletion preview. Returns "
                       "{executed, found, deleted:{node, edges}}; found:false if the id is absent.",
        "inputSchema": {"type": "object", "properties": {
            "node_id": {"type": "string",
                        "description": "Exact type-prefixed node id to delete (e.g. concept:ipaas)."},
            "dry_run": {"type": "boolean", "default": False,
                        "description": "Preview all incident edges without writing. Default false deletes."},
            **_OWNER_PROP,
        }, "required": ["node_id"]},
        "run": _t_delete,
    },
    {
        "name": "vinc_delete_edge",
        "description": "Delete ONE relationship between two nodes (the nodes stay). "
                       "Covers the SYNCED relation types only: relates_to | prerequisite_of | "
                       "derives_from | inherits_from | returns_to | about | ATTACHES_TO. A Record source can "
                       "select its authored edge type (relates_to, prerequisite_of, derives_from "
                       "or inherits_from), preserving that qualifier in its deletion tombstone, "
                       "or select the physical ATTACHES_TO carrier. CONNECTS and TYPED remain "
                       "outside this surface. Endpoints follow the "
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
                     "description": "relates_to | prerequisite_of | derives_from | inherits_from | returns_to | about | ATTACHES_TO"},
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
                       "into the canonical one. This is irreversible — confirm both exact ids first, "
                       "and quote the title of drop_id back to the person before you call, because "
                       "that title is what disappears. IF THE PAIR IS NOT A DUPLICATE, the move you "
                       "want is vinc_merge_reject: it records the distinction, stops the pair being "
                       "proposed again, and is reversible (undo:true). Reach for that first — this "
                       "tool is the half of the workflow that cannot be taken back. "
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
                       "replaced, content_hash, skipped, title, warnings}. `title` is the title AS STORED after this call, so one call confirms what the document is now named — do not follow a write with vinc_doc_list to check. Unchanged text is a no-op "
                       "(skipped=true). READ `created`: it is the only field that separates "
                       "editing a document from making a second one, because a fresh write and "
                       "an unchanged re-write both report replaced 0. A document's own front "
                       "matter often carries a doc_id: line and THAT IS NOT ITS STORE ID; "
                       "passing the declared value here does not edit that document, it creates "
                       "another one with the same title. When the body declares an id different "
                       "from the one being written to, `warnings` says so. `topic` STATES what "
                       "the document is about instead of leaving it to be inferred: without it the "
                       "topic is derived by scanning the body for concept titles, which is why a "
                       "document that cites other documents rather than naming concepts can end up "
                       "with no topic at all or a misleading one. Passing it files the document "
                       "under that topic and the answer survives re-writes; the response echoes it "
                       "back as `topic`.",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Full document text (the canonical content)."},
            "doc_id": {"type": "string", "description": "Existing doc to edit; omit to create."},
            "title": {"type": "string", "description": "Document title. OMIT IT TO KEEP THE TITLE THE DOCUMENT ALREADY HAS — it is not defaulted to empty. Pass \"\" only to actually clear the title. This matters on an edit: the title is separate from any title: line in the body, which this tool never reads."},
            "origin": {"type": "string", "description": "Provenance label only (not a join key). Omit to preserve the existing origin; pass an empty string to clear it. A metadata-only change does not rewrite document text."},
            "topic": {"type": "string", "description": "The topic this document is about, as a domain path (e.g. vinc/infra/deploy). Authored, not inferred: it is recorded on the document's own concept:doc.<id> node and takes precedence over the topic derived from body matching. Omit to leave the existing answer alone — passing \"\" does NOT clear it."},
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
        "description": "Reconstruct a document's canonical text from its chunks. Returns "
                       "{text, title, verified, chunks, updated_at}. `verified` is an INTEGRITY "
                       "flag: true means the chunks were offset-contiguous and their content hash "
                       "matched, so what you got back is the document that was stored. WHEN IT IS "
                       "FALSE the text may be incomplete or reordered and it still reads as prose "
                       "— nothing about the output looks wrong. Say so rather than answering from "
                       "it silently: a quietly truncated document is indistinguishable from a "
                       "short one. A document caught mid-write is refused instead of answered: "
                       "`executed:false` with `chunks` disagreeing with `chunk_count` means a "
                       "write or a delete was replacing its chunks when you read (a `chunk_count` "
                       "of -1 marks the write itself). That is not an empty document. Read it "
                       "again in a moment; if it stays that way the last write was interrupted "
                       "and re-putting the full text heals it.",
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
                       "other devices on sync. The chunks ARE the canonical text (Chunk=SSoT), "
                       "so this destroys the document itself and not a copy of it. "
                       "Irreversible; confirm the exact doc_id with vinc_doc_list and read the "
                       "document with vinc_doc_get before you call, so you know what is being "
                       "destroyed. ⚠ A `doc_id:` line in the document body is NOT its store id "
                       "— deleting by the declared value removes a different document or "
                       "nothing at all. Returns {executed, deleted_chunks, deleted_meta}.",
        "inputSchema": {"type": "object", "properties": {
            "doc_id": {"type": "string"},
        }, "required": ["doc_id"]},
        "run": _t_doc_delete,
    },
]

HOST_TOOLS: list[dict] = [
    {
        "name": "search",
        "description": "Search the signed-in Vinc graph for documents and nodes. "
                       "Returns {results:[{id, title, url}]}, at most `limit` in "
                       "total across documents and nodes. Use fetch with a "
                       "result id to read the full text. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
        }, "required": ["query"]},
        "outputSchema": {"type": "object", "properties": {
            "results": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "title": {"type": "string"},
                "url": {"type": "string"},
            }, "required": ["id", "title", "url"]}},
        }, "required": ["results"]},
        "run": _t_host_search,
    },
    {
        "name": "fetch",
        "description": "Fetch the full text of a search result by id. "
                       "Document ids start with doc:, node ids keep their "
                       "type prefix (concept:, record:, …). Returns "
                       "{id, title, text, url, metadata}. An id that does not "
                       "exist (or that another account owns) is a tool error, "
                       "never an empty text. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"},
        }, "required": ["id"]},
        "outputSchema": {"type": "object", "properties": {
            "id": {"type": "string"}, "title": {"type": "string"},
            "text": {"type": "string"}, "url": {"type": "string"},
            "metadata": {"type": "object"},
        }, "required": ["id", "title", "text", "url"]},
        "run": _t_host_fetch,
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS + HOST_TOOLS}

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
    "vinc_node": {"title": "Read one stored node", "readOnlyHint": True},
    "vinc_neighbors": {"title": "Observe a node's neighbors", "readOnlyHint": True},
    "vinc_edges": {"title": "Read directed incident edges", "readOnlyHint": True},
    "vinc_role": {"title": "Read a role contract or roster", "readOnlyHint": True},
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
    "search": {"title": "Search (ChatGPT)", "readOnlyHint": True},
    "fetch": {"title": "Fetch (ChatGPT)", "readOnlyHint": True},
}


def _directory_annotations(ann: dict) -> dict:
    """디렉터리 스캔이 요구하는 세 힌트를 빠짐없이 채운다.

    ChatGPT 제출은 도구마다 readOnlyHint·openWorldHint·destructiveHint 를
    명시해야 한다. 그래프는 닫힌 계정 공간이므로 openWorld 는 전부 false.
    읽기 도구의 destructiveHint 기본을 false 로 고정한다(스펙 기본은 true).
    """
    out = dict(ann)
    out.setdefault("openWorldHint", False)
    if "destructiveHint" not in out:
        out["destructiveHint"] = False if out.get("readOnlyHint") else True
    return out

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
                   "address (/t/<team-uuid>/mcp) — there the address already fixed the space. "
                   "A View address passed as `url` names its own space (?space=...), so you "
                   "can leave this out when you have one."}}

#: 데스크탑 프로파일에서 덧붙는 한 문장. 거기에는 `target` 이 있고, 스페이스는
#: **계정 그래프에만 존재한다** — 로컬 스토어에는 고를 것이 없다. 그래서 두 인자의
#: 조합 중 하나는 뜻이 없고, 뜻이 없는 조합은 조용히 무시하지 말고 거절해야 한다
#: (무시하면 팀에 쓰려던 호출이 로컬에 쌓인다).
_SPACE_LOCAL_NOTE = (" On this desktop connector a space exists only on your account "
                     "graph, so `space` requires target:'remote'; sending it with "
                     "target:'local' is an error rather than a silent local write.")


def specs(*, allow_target: bool = True, local_only: bool = False) -> list[dict]:
    """도구 목록. ``allow_target=False`` 면 `target` 프로퍼티를 주입하지 않는다.

    ``local_only=True`` 는 **로컬 모드의 stdio**다(VIN-132): 앱 모드가 그래프를 정하므로
    호출마다 고르는 `target` 도, 계정 그래프에만 있는 `space` 도 싣지 않는다. 호스트
    전용 도구(search·fetch)도 없다. 근거:
    [[vinc.plan.mcp-follows-the-app-mode-vin-132-2026-09-29]]

    **왜 프로파일로 가르나**(위험 감사 5.5). `target` 은 데스크탑 커넥터 하나로
    로컬·원격을 고르라고 만든 것이다(A1.9 A2). 그런데 주입이 무조건이라 **허브가
    내보내는 목록에도** 실렸고, 허브에서 `target:"remote"` 가 오면 허브가 자기
    자신 밖으로 아웃바운드를 시도한다 — 서버가 스스로에게 클라이언트가 되는,
    의도한 적 없는 경로다. 허브(`vinc_mcp.http_server`)는 False 로 부른다."""
    out = []
    for t in (TOOLS if (allow_target or local_only) else TOOLS + HOST_TOOLS):
        sch = t["inputSchema"]
        props = {**sch.get("properties", {})}
        # `space` 는 **양쪽 프로파일에** 싣는다. 허브에서는 주소 대신 스페이스를 고르는
        # 인자고, 데스크탑에서는 `target:"remote"` 로 간 호출이 허브에서 같은 판정을
        # 받게 하는 통로다. 데스크탑에만 문장 하나가 붙는다 — 거기에는 `target` 이
        # 있어서 뜻 없는 조합(local + space)이 생기기 때문이다.
        if local_only:
            pass                                # 원본 스키마 그대로: target·space 없음
        elif allow_target:
            props.update(_TARGET_PROP)
            sp = dict(_SPACE_PROP["space"])
            sp["description"] += _SPACE_LOCAL_NOTE
            props["space"] = sp
        else:
            props.update(_SPACE_PROP)
            # 허브는 `debug` 를 실행 경로에서 벗긴다(call). 광고도 하지 않는다 —
            # 안 듣는 스위치를 목록에 두면 호스트가 켜 놓고 효과를 기다린다.
            props.pop("debug", None)
        spec = {"name": t["name"], "description": t["description"],
                "inputSchema": {**sch, "properties": props}}
        if t.get("outputSchema"):
            spec["outputSchema"] = t["outputSchema"]
        ann = _ANNOTATIONS.get(t["name"])
        if ann:
            spec["annotations"] = _directory_annotations(ann)
        out.append(spec)
    return out


#: 주제 상한이 걸리는 도구 — 새 주제를 만들 수 있는 쓰기 표면(AP-1 §3.3).
#: 조회·삭제·병합은 주제를 **늘릴 수 없어** 대상이 아니다.
CAPPED_TOOLS = ("vinc_ingest",)


# ── 결과 크기 축소기(GM-14) ──────────────────────────────────────────────────
# 봉투(rpc._result_text)가 상한 초과 결과를 **거부하는 대신** 여기 등록된 도구는
# 꼬리 행을 덜어 페이지로 만든다. vinc_mcp 는 api 패키지를 임포트하지 않으므로
# (독립 브리지 계약) 커서 코덱을 여기 미러한다 — 두 사본의 왕복 호환은
# tests/rag/test_search_pagination.py 의 교차 계약 테스트가 고정한다.

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
             "tc": out.get("total_count"), "arms": out.get("arms")})
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + \
            "page shrunk to fit the result size limit; continue with next_cursor"
    return out


#: 봉투가 참조하는 도구별 축소기 레지스트리. 미등재 도구는 기존 거부 유지
#: (JSON 을 중간에서 자르면 깨진 구문이 된다). 등재된 것도 문자 단위로는 안 자른다 —
#: 덜 중요한 **필드나 항목**을 통째로 덜어낸다.
def _shrink_material_lists(block: dict, over: Callable[[], bool]) -> tuple[int, int]:
    """발췌를 먼저, 관계를 나중에. `over()` 가 참인 동안 항목을 덜어낸다."""
    dropped_ex = dropped_ln = 0
    ex = block.get("excerpts")
    links = block.get("links")
    items = links.get("items") if isinstance(links, dict) else None
    if isinstance(ex, list):
        while ex and over():
            ex.pop()
            dropped_ex += 1
    if isinstance(items, list):
        while items and over():
            items.pop()
            dropped_ln += 1
    return dropped_ex, dropped_ln


def _shrink_brief_result(out: dict, budget: int) -> dict | None:
    """vinc_brief 결과를 예산 안으로 줄인다 — **발췌를 먼저, 관계를 나중에** 덜어낸다.

    관계는 그 노드의 근거 지도라 마지막까지 남긴다. 발췌는 `grounds` 에 doc_id 가
    남으므로 vinc_doc_get 으로 되찾을 수 있다 — 덜어내도 길이 끊기지 않는다는 뜻이다.
    choose 에서 플래그로 붙는 `candidate_material` 도 같은 순서로 덜어낸다. 그 키를
    건너뛰면 켠 채 예산을 넘길 때 거부가 나고, 기본값을 못 켠다.
    줄일 수 없으면 None 을 돌려 기존 거부 경로로 보낸다(조용한 절단보다 거부가 정직하다).
    근거: [[vinc.plan.mcp-agent-surface §4]]
    근거: [[decision:option-c-assembly-is-built-behind-a-flag-left-off-2026-09-13]]"""
    if not isinstance(out, dict):
        return None

    def _size() -> int:
        return len(json.dumps(out, ensure_ascii=False))

    def _over() -> bool:
        return _size() > budget

    dropped_ex = dropped_ln = 0
    # 부가 키부터. 확정 답의 재료보다 choose 후보 재료가 먼저 빠져야 한다.
    cm = out.get("candidate_material")
    if isinstance(cm, dict):
        de, dl = _shrink_material_lists(cm, _over)
        dropped_ex += de
        dropped_ln += dl
    de, dl = _shrink_material_lists(out, _over)
    dropped_ex += de
    dropped_ln += dl
    if _size() > budget:
        return None
    if dropped_ex or dropped_ln:
        out["shrunk"] = {"excerpts_dropped": dropped_ex, "links_dropped": dropped_ln}
        note = ("shrunk to fit the result size limit: dropped %d excerpt(s) then %d "
                "link(s); `grounds` still lists every id" % (dropped_ex, dropped_ln))
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + note
    return out


def _shrink_graph_result(out: dict, budget: int) -> dict | None:
    """vinc_graph 결과를 예산 안으로 줄인다 — **시드는 통째로, 이웃은 껍데기만**.

    순서: 이웃 노드의 `props` → 이웃 노드의 `one_liner` → 엣지의 `evidence` → 시드에
    닿지 않는 엣지 → 시드의 직접 이웃이 아닌 노드 → **직접 고리를 id·label·kind 로**
    → **직접 고리의 꼬리**. 시드 노드는 손대지 않는다. 이 도구로 노드 **하나**의
    props 를 읽는 독자가 있기 때문이다: `agents_sync --pull` 과 역할 껍데기의 "두
    호출"이 그것이고, 2026-09-05 에 `concept:agent-dev-account` 의 depth 1 이 75,481자로
    거부되자 `--pull` 이 죽었다(Vinc#204 후속).

    둘째 고리 단은 왜 있나: depth 1 은 개념 이웃만이 아니라 그 이웃들에 붙은 record·
    episode 와 그들 사이의 엣지까지 싣는다(F14 합류). `concept:agent-dev-app` 은 직접
    이웃이 일곱인데 depth 1 이 87,417자였다(2026-09-05).

    고리 단 둘은 왜 있나: 허브는 직접 고리만으로 상한을 넘긴다(`concept:vinc-mcp`
    depth 1 = 342,008자). 여태 여기서 None 을 돌려 **허브는 어느 깊이로도 못 읽었다**.
    이제 고리를 id·제목·종류만 남기고, 그래도 넘치면 고리의 꼬리를 덜고 몇 개를
    덜었는지(`ring_truncated`)와 나머지를 읽을 길(vinc_neighbors)을 말한다. 고리
    순서는 degree 내림차순(동률은 id)이라 남는 것은 가장 많이 이어진 이웃이다.

    `note` 는 **남은 것을 세어서** 쓴다. 고정 문구("직접 고리는 온전하다")가 고리를
    잃은 결과에도 붙어 있었다(감사 §5-5).

    덜어낸 것은 되찾을 수 있다: 이웃의 props 는 그 이웃의 id 로 다시 부르면 나오고,
    엣지의 evidence 는 `vinc_brief` 가, 둘째 고리는 이웃을 시드로 다시 부르면 나온다.
    시드 자신이 예산을 넘길 때만 None 을 돌려 거부 경로로 보낸다.
    근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4"""
    if not isinstance(out, dict) or not isinstance(out.get("nodes"), list):
        return None

    def _j(v) -> int:
        return len(json.dumps(v, ensure_ascii=False))

    # 매 단계 전체를 다시 직렬화하면 허브(노드 수백·34만 자)에서 제곱 비용이 된다.
    # 값 하나를 바꿀 때 전체 길이의 변화는 그 값의 직렬화 길이 차와 정확히 같으므로
    # 그 차로 따라가고, 끝에서 한 번 실제로 잰다.
    cur = _j(out)
    seed = out.get("seed")
    nodes = out["nodes"]
    edges = out.get("edges") if isinstance(out.get("edges"), list) else None

    # 원본 기준 고리·바깥 — note 가 "무엇이 남았나"를 이 둘에 대어 센다.
    ring0: set = set()
    for e in edges or []:
        if isinstance(e, dict) and seed in (e.get("source"), e.get("target")):
            ring0.add(e.get("target") if e.get("source") == seed else e.get("source"))
    ring0.discard(seed)
    all_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    ring0 &= all_ids
    far0 = all_ids - ring0 - {seed}

    dropped = {"neighbour_props": 0, "neighbour_one_liners": 0, "edge_evidence": 0}
    for key, empty, counter in (("props", {}, "neighbour_props"),
                                ("one_liner", "", "neighbour_one_liners")):
        for n in nodes:
            if cur <= budget:
                break
            if isinstance(n, dict) and n.get("id") != seed and n.get(key):
                cur += _j(empty) - _j(n[key])
                n[key] = empty
                dropped[counter] += 1
    if edges is not None:
        for e in edges:
            if cur <= budget:
                break
            if isinstance(e, dict) and e.get("evidence"):
                cur += _j("") - _j(e["evidence"])
                e["evidence"] = ""
                dropped["edge_evidence"] += 1
    dropped["far_edges"] = dropped["far_nodes"] = 0
    if edges is not None and seed and cur > budget:
        # 시드에 닿지 않는 엣지(이웃끼리, 이웃과 그 record 사이)를 덜어낸다.
        near = [e for e in edges
                if isinstance(e, dict) and seed in (e.get("source"), e.get("target"))]
        dropped["far_edges"] = len(edges) - len(near)
        edges[:] = near
        cur = _j(out)
        if cur > budget:
            # 시드의 직접 이웃이 아닌 노드(둘째 고리)를 덜어낸다.
            keep = {seed} | ring0
            before = len(nodes)
            nodes[:] = [n for n in nodes if isinstance(n, dict) and n.get("id") in keep]
            dropped["far_nodes"] = before - len(nodes)
            cur = _j(out)
    dropped["ring_slimmed"] = dropped["ring_dropped"] = 0
    if seed and cur > budget:
        # 고리 단 ①: 이웃을 id·label·kind 로. 엣지도 끝점과 타입만.
        for i, n in enumerate(nodes):
            if isinstance(n, dict) and n.get("id") != seed:
                nodes[i] = {"id": n.get("id"), "label": n.get("label"),
                            "kind": n.get("kind"), "_deg": n.get("degree") or 0}
                dropped["ring_slimmed"] += 1
        if edges is not None:
            edges[:] = [{"source": e.get("source"), "target": e.get("target"),
                         "type": e.get("type")} for e in edges if isinstance(e, dict)]
        # 고리 순서: 많이 이어진 이웃이 먼저 남는다(결정론 — 동률은 id).
        head = [n for n in nodes if n.get("id") == seed]
        rest = sorted((n for n in nodes if n.get("id") != seed),
                      key=lambda n: (-int(n.get("_deg") or 0), str(n.get("id"))))
        for n in rest:
            n.pop("_deg", None)
        nodes[:] = head + rest
        cur = _j(out)
        if cur > budget:
            # 고리 단 ②: 꼬리부터 던다. 노드와 그 노드에 닿는 엣지를 함께.
            by_node: dict = {}
            for e in edges or []:
                other = e["target"] if e.get("source") == seed else e.get("source")
                by_node.setdefault(other, []).append(e)
            # 뒤에 붙을 표식(ring_truncated·note)의 자리는 봉투의 예비분이 맡는다.
            gone: set = set()
            while cur > budget and len(nodes) > 1 and nodes[-1].get("id") != seed:
                n = nodes.pop()
                gone.add(n.get("id"))
                cur -= _j(n) + 2          # ", " 구분자
                for e in by_node.get(n.get("id"), []):
                    cur -= _j(e) + 2
                dropped["ring_dropped"] += 1
            if edges is not None and gone:
                edges[:] = [e for e in edges
                            if e.get("source") not in gone and e.get("target") not in gone]
            cur = _j(out)
            while cur > budget and len(nodes) > 1:
                # 추정이 모자랐을 때의 안전판(구분자 계산이 어긋나도 예산은 지킨다).
                n = nodes.pop()
                if edges is not None:
                    edges[:] = [e for e in edges if n.get("id") not in
                                (e.get("source"), e.get("target"))]
                dropped["ring_dropped"] += 1
                cur = _j(out)
    if _j(out) > budget:
        return None
    if any(dropped.values()):
        final = {n.get("id") for n in nodes if isinstance(n, dict)}
        ring_kept, far_kept = len(ring0 & final), len(far0 & final)
        dropped.update(ring_total=len(ring0), ring_kept=ring_kept,
                       far_total=len(far0), far_kept=far_kept)
        out["shrunk"] = dropped
        parts = [f"kept the seed whole, {ring_kept} of {len(ring0)} direct neighbour(s) "
                 f"and {far_kept} of {len(far0)} node(s) beyond them",
                 f"emptied props on {dropped['neighbour_props']} node(s), one_liner on "
                 f"{dropped['neighbour_one_liners']}, evidence on "
                 f"{dropped['edge_evidence']} edge(s)",
                 f"dropped {dropped['far_edges']} edge(s) and {dropped['far_nodes']} "
                 f"node(s) not touching the seed"]
        if dropped["ring_slimmed"]:
            parts.append("direct neighbours are reduced to id, label and kind")
        if dropped["ring_dropped"]:
            out["ring_truncated"] = dropped["ring_dropped"]
            parts.append(f"{dropped['ring_dropped']} direct neighbour(s) left out "
                         f"(the least connected); read the whole ring with "
                         f"vinc_neighbors node={seed} limit=500")
        note = ("shrunk to fit the result size limit: " + "; ".join(parts)
                + "; anything dropped comes back by reading that node's own id")
        out["note"] = ((str(out.get("note")) + "; ") if out.get("note") else "") + note
    return out


SHRINKERS = {"vinc_search": _shrink_search_result,
             # W1 — brief 는 조밀한 이웃에서 예산을 넘길 수 있다. 정책으로
             # 강등한다: 발췌부터 덜고 관계는 마지막까지 남긴다.
             "vinc_brief": _shrink_brief_result,
             # 조밀한 노드의 depth 1 이 상한을 넘겼다(2026-09-05, 75,481자). 시드를
             # 지키고 이웃의 props 부터 던다 — 시드 하나를 읽으러 온 독자가 있다.
             # 허브는 고리까지 줄여 싣는다(거부는 시드 자신이 넘칠 때뿐).
             "vinc_graph": _shrink_graph_result}


# ── 인자 검증(D5) ────────────────────────────────────────────────────────────
# 스키마의 `required`·`minimum`·`maximum`·`enum` 은 여태 **권고**였다: `required` 만
# 봤고 범위는 아무도 안 봤다. 그래서 `limit=101` 이 조용히 잘리거나(쿼리층 max/min)
# 그대로 사이드카까지 갔다. 이제 MCP 층이 **먼저 거절한다** — 자르지 않는다.
# 거절은 JSON-RPC -32602 가 아니라 **도구 결과**(isError + 행동 가능한 문장 +
# `_meta.vinc_invalid_argument`)다: 호스트 다수가 프로토콜 오류를 모델에게 안 보여
# 주고, 모델은 고칠 근거 없이 같은 호출을 되풀이한다.
# 쿼리층 클램프는 **그대로 둔다** — 사이드카·CLI 는 MCP 를 안 지난다.
# 근거: [[decision:mcp-argument-errors-are-tool-results-not-protocol-errors-2026-09-24]]

class InvalidArgument(Exception):
    """도구 인자 거절. ⚠ ValueError 를 상속하지 말 것 — rpc 의 `except (KeyError,
    ValueError)` → -32602 경로에 삼켜져 결과 봉투가 되지 못한다."""

    def __init__(self, tool: str, argument: str, *, value=None, minimum=None,
                 maximum=None, reason: str | None = None, text: str = ""):
        self.meta: dict = {"tool": tool, "argument": argument}
        if reason is not None:
            self.meta["reason"] = reason
        else:
            self.meta.update({"value": value, "minimum": minimum, "maximum": maximum})
        self.text = text
        super().__init__(text)

    def result(self) -> dict:
        """tools/call 결과 봉투. 스택·Cypher 는 싣지 않는다 — 문장과 좌표뿐."""
        return {"content": [{"type": "text", "text": self.text}], "isError": True,
                "_meta": {"vinc_invalid_argument": dict(self.meta)}}


def _as_number(v, integer: bool):
    """스키마 수치 인자를 비교용 수로. 수가 아니면 None(= 형식 거절)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return (int(v) if v.is_integer() else None) if integer else v
    if isinstance(v, str):
        try:
            return int(v.strip()) if integer else float(v.strip())
        except ValueError:
            return None
    return None


def _cursor_shape_ok(cur) -> bool:
    """커서 **모양**만 본다(코덱 v1 · 음 아닌 정수 오프셋). 어느 질의의 것인가(qh)는
    사이드카가 판정한다 — owner·질의·detail 이 거기에만 다 모여 있다."""
    if not isinstance(cur, str) or not cur:
        return False
    try:
        pad = "=" * (-len(cur) % 4)
        data = json.loads(base64.urlsafe_b64decode((cur + pad).encode("ascii")))
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("v") != 1:
        return False
    off = data.get("off", 0)
    return isinstance(off, int) and not isinstance(off, bool) and off >= 0


def _invalid_cursor(tool: str, reason: str) -> InvalidArgument:
    return InvalidArgument(
        tool, "cursor", reason=reason,
        text=(f"Invalid argument: cursor for {tool} was refused ({reason}). "
              "Nothing was returned. Retry without `cursor` to start from the first page, "
              "or pass the next_cursor returned by the same call with the same arguments."))


def validate_args(name: str, args: dict | None) -> None:
    """스키마 대조. 모르는 도구는 KeyError(프로토콜 오류로 남긴다), 인자 위반은
    InvalidArgument. rpc 가 **쿼터를 세기 전에** 부르고, `call` 도 한 번 더 부른다
    (rpc 를 안 지나는 호출자 — 테스트·평가 스크립트 — 도 같은 판정을 받게)."""
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = args if isinstance(args, dict) else {}
    if name == "vinc_edges":
        node, nodes = args.get("node"), args.get("nodes")
        ids = [node] if node is not None else nodes
        if ((node is not None and nodes is not None) or not isinstance(ids, list)
                or not 1 <= len(ids) <= 100
                or any(not isinstance(n, str) or not n.strip() or "\x1f" in n for n in ids)):
            raise InvalidArgument(name, "node/nodes", reason="provide exactly one node or 1..100 ids",
                                  text="Invalid argument: provide either node or a nodes array of 1..100 exact ids.")
    sch = tool["inputSchema"]
    props = sch.get("properties") or {}
    for req in sch.get("required", []):
        if args.get(req) in (None, ""):
            raise InvalidArgument(
                name, req, reason="missing required argument",
                text=(f"Invalid argument: {req} is required for {name} and was not given. "
                      f"Nothing was run. Retry with {req} set."))
    for key, spec in props.items():
        v = args.get(key)
        if v is None:
            continue
        typ = spec.get("type")
        if typ in ("integer", "number") and ("minimum" in spec or "maximum" in spec):
            lo, hi = spec.get("minimum"), spec.get("maximum")
            n = _as_number(v, typ == "integer")
            if n is None:
                kind = "an integer" if typ == "integer" else "a number"
                raise InvalidArgument(
                    name, key, value=v, minimum=lo, maximum=hi,
                    text=(f"Invalid argument: {key}={v!r} is not {kind} for {name}. "
                          f"Nothing was run. Retry with {key} between {lo} and {hi}."))
            if (lo is not None and n < lo) or (hi is not None and n > hi):
                more = (", and continue with next_cursor for more"
                        if key == "limit" and "cursor" in props else "")
                raise InvalidArgument(
                    name, key, value=v, minimum=lo, maximum=hi,
                    text=(f"Invalid argument: {key}={v} is outside {lo}..{hi} for {name}. "
                          f"Nothing was run. Retry with {key} between {lo} and {hi}{more}."))
        elif typ == "string" and spec.get("enum") and v not in spec["enum"]:
            allowed = ", ".join(spec["enum"])
            raise InvalidArgument(
                name, key, reason=f"must be one of: {allowed}",
                text=(f"Invalid argument: {key}={v!r} is not accepted by {name}. "
                      f"Nothing was run. Retry with {key} set to one of: {allowed}."))
    if "cursor" in props and args.get("cursor") not in (None, "") \
            and not _cursor_shape_ok(args["cursor"]):
        raise _invalid_cursor(name, "malformed cursor")


def call(name: str, args: dict | None, bridge: Bridge,
         forced_owner: str | None = None,
         entitlements: dict | None = None,
         allow_target: bool = True,
         local_only: bool = False) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = dict(args or {})                     # 복사 — 호출자 dict 불변, target 제거
    target = (args.pop("target", None) or "local").lower()
    # `space` 는 **라우팅 메타데이터**지 도구 인자가 아니다. 기본은 벗기는 것이다 —
    # 사이드카 핸들러는 params 를 명시로 조립하니 흘러가도 무해하지만, `remote.call` 은
    # `owner`·`target` 만 빼고 **전부 넘기므로** 벗기지 않으면 아무 데나 새어 나간다.
    #
    # 예외는 하나. **데스크탑에서 `target:"remote"` 로 가는 호출**은 그대로 실어
    # 보낸다(스키마가 그 조합을 광고한다). 판정은 허브가 한다 — 키가 그 팀의 멤버임을
    # 계정에 묻고 역할로 게이트한다. 여기서 판정을 흉내 내면 두 번째 표가 생긴다.
    #
    # 허브 자신(`allow_target=False`)은 이 분기에 안 들어온다: 거기서 `space` 는 HTTP
    # 층이 이미 읽어 스페이스를 정한 뒤라, 남은 값은 벗기는 것이 맞다.
    space = args.pop("space", None)
    # 로컬 모드의 stdio(VIN-132): 앱 모드가 그래프를 정한다. 스키마에서 뺀 `target`·`space`
    # 가 와도 조용히 무시하지 않는다 — 무시하면 계정에 쓰려던 것이 로컬에 쌓인다.
    if local_only and target != "local":
        raise ValueError(
            "this computer is in local mode, so this connector reads only the local store. "
            "To reach your account graph, switch the Vinc app to account mode and restart "
            "this AI host.")
    if local_only and space:
        raise ValueError(
            "local mode has no spaces: spaces live on your account graph. Switch the Vinc "
            "app to account mode and restart this AI host to use them.")
    if allow_target and space:
        if target != "remote":
            # 로컬 스토어에는 고를 스페이스가 없다. 조용히 무시하면 팀에 쓰려던 것이
            # 로컬에 쌓인다 — 이 표면 전체가 막으려는 그 고장이다.
            raise ValueError(
                "space applies to your account graph: pass target:'remote' with it "
                "(target:'local' has no spaces to choose from).")
        args["space"] = space
    # 붙여넣은 주소가 계정 그래프의 스페이스를 말하는데(`?space=personal`·팀 id)
    # 로컬 스토어로 가려는 호출이다. 조용히 로컬에서 찾으면 **같은 id 의 다른 노드**나
    # "없음"을 답한다. 위의 space + local 조합과 같은 이유로 거절하고 길을 말한다.
    # `target:"remote"` 면 url 을 그대로 실어 보내고 허브가 주소에서 스페이스를 읽는다.
    # 근거: [[decision:a-node-address-names-its-space]]
    if (allow_target or local_only) and not space and target != "remote" and args.get("url"):
        from . import viewurl
        at = viewurl.space_of(str(args["url"]))
        if at and at != viewurl.LOCAL:
            raise ValueError(
                f"that address was copied from your account graph (space {at}), and "
                + ("this computer is in local mode: switch the Vinc app to account mode "
                   "and restart this AI host to read it where it lives." if local_only else
                   "this desktop connector reads the local store unless told otherwise: "
                   "pass target:'remote' to read it where it lives."))
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
    # 인증된(forced_owner) 경로에서는 `debug` 를 벗긴다 — 실행 계획에는 Cypher 와
    # 다른 소유자 스코프를 가늠할 파라미터가 실린다. 로컬 사이드카 진단은 그대로다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] S13.
    if forced_owner:
        args.pop("debug", None)
    validate_args(name, args)
    if target == "remote":
        from . import remote                    # 지연 import — 로컬 경로엔 부담 0
        out = remote.call(name, args)
    else:
        out = tool["run"](args, bridge)
    # 커서가 **어느 질의의 것인가**는 사이드카만 안다(owner·질의·detail 이 거기 모인다).
    # 그 판정(`invalid_cursor: …`)도 같은 봉투로 올린다 — 빈 결과처럼 보이면 안 된다.
    if args.get("cursor") and isinstance(out, dict):
        err = str(out.get("error") or "")
        if err.startswith("invalid_cursor"):
            reason = ("cursor does not belong to this query" if "different" in err
                      else "malformed cursor")
            raise _invalid_cursor(name, reason)
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
#: 열리지 않는다. `tests/mcp/test_mcp_space_address.py` 가 전 도구의 주석 실재를 못 박는다.
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
