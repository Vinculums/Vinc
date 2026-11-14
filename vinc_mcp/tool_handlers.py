# -*- coding: utf-8 -*-
"""MCP 도구 핸들러: 도구 인자를 사이드카 요청으로 옮기고 답을 다듬는 `_t_*` 함수들과, ChatGPT 디렉터리
계약의 호스트 `search` · `fetch`.

`tools` 의 도구 항목(`TOOLS` · `HOST_TOOLS`)이 `run` 칸으로 이 함수들을 가리키고 `tools.call` 이 부른다.
`tools` 가 이 모듈의 이름을 그대로 다시 내보낸다(테스트와 다른 모듈이 `tools.X` 로 부른다). 이 모듈은
`tools` 를 import 하지 않는다.
"""
from __future__ import annotations

import json
import os

from store import run_state as _run_state
from store.querybase import DOC_MIRROR_PREFIX, doc_id_of

from .bridge import Bridge
from . import events, guards


def _owner(args: dict, bridge: Bridge) -> str:
    return args.get("owner") or bridge.owner


def owner_scope(owner: str | None) -> str:
    """owner 가 어느 종류의 그래프인가: `personal` · `team` · `community` · `unscoped`.

    `vinc_whoami` 와 사이드카의 `vinc_conventions`(`api/server.py` `_owner_scope`)가 같은
    값을 내야 한다. 한때 로컬 기본 소유자 `tenant:local` 을 whoami 는 unscoped, 관례는
    personal 로 답했다. `personal` 은 계정의 개인 그래프(`user:`)만이다. 로컬 스토어의
    소유자(`tenant:local`, 빈 값)는 계정 그래프가 아니라서 `unscoped` 다. 두 사본의 일치는
    tests/core/test_conventions_scope.py 가 대조한다."""
    owner = owner or ""
    if owner.startswith("community:"):
        return "community"
    if owner.startswith("team:"):
        return "team"
    return "personal" if owner.startswith("user:") else "unscoped"


#: 결과를 읽는 모델에게 "답에서 그렇다고 말하라"를 응답 안에서 전한다(도구 설명은 짧은 요약이라
#: 이 규칙을 싣지 않는다). 사이드카가 아니라 여기서 붙이는 이유: CLI 와 HTTP 의 검색 결과는 같아야
#: 한다(게이트①). 재빌드 창(vector_rebuilding)과 인코더 판 경고(encoder_runtime_warning)는 의미 팔이
#: 돈 것이라 실패가 아니다(근거는 위 DOCS_BASE 주석).
_DEGRADED_ANSWER_NOTE = (
    "Meaning search did not run for this call (see vector_note), so these results come from keyword "
    "matching alone and can miss relevant items. Say so in your answer.")
_UNVERIFIED_ANSWER_NOTE = (
    "The integrity check failed: this text may be incomplete or out of order while still reading as "
    "normal prose. Say so if you answer from it.")


def _with_search_note(out):
    if (isinstance(out, dict) and out.get("vector_note") and not out.get("vector_rebuilding")
            and not out.get("encoder_runtime_warning")):
        return {**out, "answer_note": _DEGRADED_ANSWER_NOTE}
    return out


def _t_search(args, bridge):
    # SEC-1: owner 를 반드시 실어 보낸다. 여태 미전송이라 forced_owner 스탬핑이
    # 무의미했고 node_hits 가 남의 Concept/Record 를 그대로 돌려줬다.
    # GM-14: cursor/detail 은 그대로 통과(None 은 Bridge 가 걸러 미전송) — 해석은
    # 사이드카 한 곳에서만 한다(CLI·HTTP·MCP 동등성).
    return _with_search_note(bridge.request("GET", "/search", params={
        "query": args["query"], "top_k": args.get("limit", 20),
        "cursor": args.get("cursor"), "detail": args.get("detail"),
        "historical": "true" if args.get("historical") else None,
        "as_of": args.get("as_of"), "known_at": args.get("known_at"),
        "mode": args.get("mode"), "kinds": _kinds_param(args),
        "owner": _owner(args, bridge)}))


def _kinds_param(args: dict) -> str | None:
    kinds = args.get("kinds")
    return ",".join(kinds) if isinstance(kinds, list) and kinds else None


def _time_params(args: dict) -> dict:
    """시점 읽기 두 축. 값이 없으면 키도 없다(Bridge 가 None 을 거르지만 모양을 지킨다)."""
    return {k: args[k] for k in ("as_of", "known_at") if args.get(k)}


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
        **_time_params(args), "debug": "true" if args.get("debug") else None})


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
    if review and (not seed.startswith(DOC_MIRROR_PREFIX)
                   or not doc_id_of(seed) or "\x1f" in seed):
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
    params.update(_time_params(args))
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
        "limit": args.get("limit", 50), **_time_params(args)})


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
        "weight": args.get("weight", "hops"), **_time_params(args)})


def _t_merge_candidates(args, bridge):
    # E-G4 병합 후보(조회 전용) — 확정은 vinc_merge_nodes 경유(자동 병합 금지 T1).
    return bridge.request("GET", "/merge-candidates", params={
        "owner": _owner(args, bridge), "limit": args.get("limit", 20),
        "min_score": args.get("min_score", 0.6),
        "include_structural": "true" if args.get("include_structural") else None})


def _mutation_options(args):
    return {k: args[k] for k in ("operation_id", "preview_digest", "dry_run",
        "create_only", "expected_content_hash") if k in args}


def _t_operation_status(args, bridge):
    return bridge.request("GET", "/operation-status", params={
        "owner": _owner(args, bridge), "operation_id": args["operation_id"]})


def _t_events(args, bridge):
    # 변경 기록에서 계산한 이벤트 피드(사이드카 `/events`). 내부 위치는 걷어 낸다.
    kinds = args.get("kinds")
    out = bridge.request("GET", "/events", params={
        "owner": _owner(args, bridge), "since": args.get("since"), "role": args.get("role"),
        "kinds": ",".join(kinds) if isinstance(kinds, list) and kinds else None,
        "limit": args.get("limit")})
    return events.public(out)


def _t_runs(args, bridge):
    # 실행 기록 목록(사이드카 `/runs`). `cited` 를 주면 그 주장을 인용한 실행만 돌려준다.
    return bridge.request("GET", "/runs", params={
        "owner": _owner(args, bridge), "cited": args.get("cited"), "role": args.get("role"),
        "run": args.get("run"), "flagged": args.get("flagged"), "limit": args.get("limit")})


def _t_eval_cards(args, bridge):
    # 역할에 붙은 평가 카드(사이드카 `/eval-cards`). 카드는 SDK 가 쓰고 여기는 읽기만 한다.
    return bridge.request("GET", "/eval-cards", params={
        "owner": _owner(args, bridge), "role": args.get("role"), "limit": args.get("limit")})


def _t_run_start(args, bridge):
    # 실행 프로토콜: 역할 하나의 실행을 연다. 상태는 실행 기록 Episode 가 든다.
    from . import run_protocol
    return run_protocol.start(args, bridge, _owner(args, bridge))


def _t_run_submit(args, bridge):
    from . import run_protocol
    return run_protocol.submit(args, bridge, _owner(args, bridge))


def _t_merge_reject(args, bridge):
    # F10-b 기각 기억 — 병합(비가역)과 짝을 이루는 가역 쓰기. undo 로 취소된다.
    return bridge.request("POST", "/merge-reject", body={
        "owner": _owner(args, bridge), "a": args["a"], "b": args["b"],
        "note": args.get("note") or "", "undo": bool(args.get("undo")), **_mutation_options(args)})


def _t_merge_rejections(args, bridge):
    # F10-b 기각 **목록**(조회 전용). vinc_merge_reject 로 쓸 수는 있는데 읽을 수가
    # 없어서, 어시스턴트가 이미 기각된 쌍을 다시 제안하는 짝 결손이 있었다.
    return bridge.request("GET", "/merge-rejections", params={"owner": _owner(args, bridge)})


def _t_doc_extract(args, bridge):
    # D45-V 정본→개념 파생. 웹 전용이라 문서 워크플로의 절반이 어시스턴트에게
    # 닫혀 있었다(문서는 쓸 수 있는데 거기서 개념을 못 뽑는다).
    return bridge.request("POST", "/doc/extract", body={
        "owner": _owner(args, bridge), "id": args["doc_id"],
        "apply": bool(args.get("apply")), "candidates": args.get("candidates"), **_mutation_options(args)})


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


#: 결정 문이 한 번에 받는 경로 수 — `api.decide_read.DECIDE_PATHS_MAX` 의 미러(vinc_mcp 는 api 를
#: 임포트하지 않는다). 둘이 같은지는 tests/core/test_decide_paths.py 가 본다.
DECIDE_PATHS_MAX = 200


def _t_decide_paths(args, bridge):
    # 결정 문(조회 전용) — 바뀐 경로를 다스리는 현재 기록·결정. 경로 목록은
    # vinc_edges 의 nodes 처럼 JSON 하나로 싣는다. 가부는 가리지 않는다(사이드카가 목록만 낸다).
    params = {"owner": _owner(args, bridge),
              "paths": json.dumps(args["paths"], ensure_ascii=False)}
    if args.get("member_limit") is not None:
        params["member_limit"] = args["member_limit"]
    return bridge.request("GET", "/decide-paths", params=params)


def _t_ingest(args, bridge):
    blocked = guards.run_state_writes(args.get("nodes"))
    if blocked:
        from . import run_protocol
        return {"kind": "ingest", "executed": False, "error": "run_state_protected",
                "nodes": blocked,
                "note": ("Refused: a run record's " + ", ".join(run_protocol.STATE_KEYS)
                         + " belong to the run protocol, and props_patch cannot change its "
                         + " or ".join(_run_state.ROLE_KEYS) + ". Answer a waiting run with "
                         "vinc_run_submit, or as a person in " + run_protocol.PERSON_PATH
                         + ". Nothing was written.")}
    fragment = {"nodes": args.get("nodes") or [], "edges": args.get("edges") or [],
                "owner": _owner(args, bridge), **_mutation_options(args)}
    if "review_claims" in args:
        fragment["review_claims"] = args["review_claims"]
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
    body = {"node": args["node_id"], "owner": _owner(args, bridge), **_mutation_options(args)}
    if "dry_run" in args:
        body["dry_run"] = args["dry_run"]
    if args.get("stray_type") is not None:
        body["stray_type"] = args["stray_type"]
    return bridge.request("POST", "/delete", body=body)


def _t_merge(args, bridge):
    return bridge.request("POST", "/merge-nodes",
                          body={"keep": args["keep_id"], "drop": args["drop_id"],
                                "owner": _owner(args, bridge), **_mutation_options(args)})


def _t_delete_edge(args, bridge):
    return bridge.request("POST", "/delete-edge",
                          body={"from": args["from_id"], "to": args["to_id"],
                                "type": args["type"], "owner": _owner(args, bridge), **_mutation_options(args)})


# ── 문서 정본(Chunk=SSoT, D45 P4) — 편집 표면 도구 ────────────────────────────
def _t_doc_get(args, bridge):
    out = bridge.request("GET", "/doc", params={
        "id": args["doc_id"], "owner": _owner(args, bridge)})
    if isinstance(out, dict) and out.get("executed", True) and out.get("verified") is False:
        out = {**out, "answer_note": _UNVERIFIED_ANSWER_NOTE}
    return out


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
    # 시간 필드 넷은 3값이다(키 부재=보존 · ""=비움 · 값=설정). 키가 있을 때만 실어야
    # 사이드카가 부재와 비움을 가른다(`api/server.py` `write_doc_put`).
    return bridge.request("POST", "/doc/put", body={
        "owner": _owner(args, bridge), "text": args["text"],
        "id": args.get("doc_id"), "title": args.get("title"),
        "origin": args.get("origin"), "topic": args.get("topic"), **_mutation_options(args),
        **{k: args[k] for k in ("content_time", "valid_from", "valid_until", "superseded_by")
           if k in args}})


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
    scope = owner_scope(owner)
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
    joined = entitlement.list_communities(owner.split(":", 1)[1])
    out["communities"] = joined or []
    out["communities_source"] = "account" if joined is not None else "unavailable"
    return out


def _t_doc_delete(args, bridge):
    return bridge.request("POST", "/doc/delete", body={
        "id": args["doc_id"], "owner": _owner(args, bridge), **_mutation_options(args)})


def _t_node(args, bridge):
    return bridge.request("GET", "/node", params={
        "owner": _owner(args, bridge), "node_id": args["node_id"]})


def _t_role(args, bridge):
    from .roles import contract
    return contract(args.get("role"), bridge, forced_owner=_owner(args, bridge),
                    limit=args.get("limit"), offset=args.get("offset", 0),
                    expected_hash=args.get("expected_hash"), expected_rev=args.get("expected_rev"),
                    compact=args.get("compact") is True)


def _t_role_export(args, bridge):
    # 렌더링은 사이드카가 한다: vincpack 은 api 의 팩 인코더가 필요하고 vinc_mcp 는 api 를 부르지 않는다.
    role, fmt = args.get("role"), args.get("format") or "skill"
    if not isinstance(role, str) or not role.strip():
        return {"kind": "role_export", "executed": False, "error": "role_required",
                "note": "Name the role to export."}
    from .roles import ROLE_EXPORT_FORMATS
    if fmt not in ROLE_EXPORT_FORMATS:
        return {"kind": "role_export", "executed": False, "error": "invalid_export_format",
                "note": "format must be one of " + ", ".join(ROLE_EXPORT_FORMATS)}
    # 역할인지·남의 것인지는 사이드카가 vinc_role 과 같은 규칙으로 가린다.
    return bridge.request("GET", "/roles/export", params={
        "owner": _owner(args, bridge), "role": role.strip(), "format": fmt})


def _t_role_check(args, bridge):
    # 패키지(vinc_client.output_gate)와 같은 결정론 게이트를 허브에서 돌린다. MCP 로 붙는
    # 호스트도 같은 판정을 받게 하려는 것이다. 모델이 보낸 증거는 받지 않는다: 이 도구의
    # 호출자는 모델이라, 무언가를 실행했다는 주장은 여기서 언제나 block 이다.
    # 근거: [[vinc.canon.agent-roles]]
    from .roles import contract
    from .role_output_gate import OutputGate
    role, output = args.get("role"), args.get("output")
    if not isinstance(role, str) or not role.strip():
        return {"kind": "role_check", "executed": False, "error": "role_required",
                "note": "Name the role whose contract the output is checked against."}
    if not isinstance(output, str) or not output.strip() or len(output) > 131072:
        return {"kind": "role_check", "executed": False, "error": "output_required",
                "note": "Pass the final output as one non-empty string of at most 131072 characters."}
    # 결과만 돌려주고 계약은 싣지 않으므로 읽기 예산(roles.CONTRACT_CAP)에 걸리지 않는다.
    got = contract(role.strip(), bridge, forced_owner=_owner(args, bridge), capped=False)
    if not got.get("executed") or not got.get("found"):
        return {**got, "kind": "role_check"}
    requests = args.get("person_only_requests") or []
    result = OutputGate(got["props"], self_authored=bool(args.get("self_authored"))).evaluate(
        output, person_only_requests=requests if isinstance(requests, list) else [None])
    return {"kind": "role_check", "executed": True, "found": True, "role": got["id"],
            **result.as_dict(),
            "note": ("pass releases this output only; it grants no tool, merge, deploy or publish "
                     "permission. A claim that something ran blocks here, because evidence must "
                     "come from the host, not from the caller. The role's never prose still binds "
                     "the operator beyond these structured checks.")}


def _t_brief(args, bridge):
    # W1 — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기(생성 없음).
    out = bridge.request("GET", "/brief", params={
        "owner": _owner(args, bridge), "query": args["query"],
        "max_excerpts": args.get("max_excerpts", 3),
        "historical": "true" if args.get("historical") else None,
        "as_of": args.get("as_of"), "known_at": args.get("known_at"), "mode": args.get("mode"),
        "kinds": _kinds_param(args)})
    from .roles import is_role_id
    if is_role_id(args["query"].strip()) and out.get("executed"):
        role = _t_role({**args, "role": args["query"].strip()}, bridge)
        if role.get("error") == "role_contract_too_large" and role.get("compact") is False:
            role = _t_role({**args, "role": args["query"].strip(), "compact": True}, bridge)
        if role.get("found") or not role.get("executed"):
            out = {**out, "role": role}
    return _with_search_note(out)


#: ChatGPT deep research / company knowledge 는 이름이 정확히 `search`·`fetch` 인
#: 읽기 쌍만 조회한다. 허브 프로파일에만 노출하고, 구현은 vinc_search / vinc_doc_get
#: (노드는 vinc_brief) 의 얇은 별칭이다.
#: 근거: [[vinc.canon.mcp-contract]]
_CITE_ORIGIN = (os.environ.get("VINC_CITE_ORIGIN") or "https://vincs.io").rstrip("/")


def _cite_origin() -> str:
    """인용 주소의 앞부분. 셀프호스트 허브는 자기 바깥 주소이고, 그것도 없으면 빈 값이다."""
    from . import selfhost
    if os.environ.get("VINC_CITE_ORIGIN") or not selfhost.enabled():
        return _CITE_ORIGIN
    return selfhost.public_url() or ""
_NODE_PREFIXES = ("concept:", "record:", "decision:", "episode:", "domain:", "bridge:")


def _cite_url(kind: str, ident: str, owner: str = "") -> str:
    """인용용 URL. 비어 있으면 ChatGPT 가 출처로 만들지 않는다.

    앱이 복사하는 주소와 **같은 모양**이다: `/app/` 아래, 해시 앞에 `?space=`.
    전에는 사이트 루트(`/#view/…`)를 가리켰는데 루트는 `#view` 를 모르므로 인용을
    누르면 랜딩이 떴고, 스페이스가 없어 팀 결과도 개인 주소로 나갔다.
    근거: [[decision:a-node-address-names-its-space]]"""
    owner = str(owner or "")
    space = (owner[len("team:"):] if owner.startswith("team:") else
             owner if owner.startswith("community:") else "personal")
    where = "doc" if kind == "doc" else "trace"
    origin = _cite_origin()
    return f"{origin}/app/?space={space}#view/{where}/{ident}" if origin else ""


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
                     "owner": args.get("owner"),
                     **{k: args[k] for k in ("mode", "historical", "as_of", "known_at") if k in args}}, bridge)
    # B9 — 청크 팔만 실패하고 노드 후보가 있으면 그것으로 답한다. 살릴지는 REST /v1/search 와
    # 같은 규칙(`toolcall.partial_search`)이 정한다. 살리지 않는 실패는 도구 오류다.
    from .toolcall import partial_search
    failed = not isinstance(raw, dict) or raw.get("error") or raw.get("executed") is False
    kept = partial_search(raw) if failed else raw
    if kept is None:
        raise RuntimeError("search_unavailable")
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
    out = {"results": results[:limit]}
    if failed or raw.get("node_error") or raw.get("vector_note") or raw.get("partial") or \
            (raw.get("node_semantic") or {}).get("state") in ("partial", "empty_mirror",
                                                              "model_mismatch"):
        out["diagnostics"] = {k: raw[k] for k in ("arms", "node_semantic", "node_window",
                              "vector_rebuilding", "encoder_runtime_warning") if k in raw}
        if failed:
            out["diagnostics"]["search_error"] = kept["search_error"]
        if raw.get("node_error"):
            out["diagnostics"]["node_search_degraded"] = True
        if raw.get("vector_note"):
            out["diagnostics"]["chunk_semantic_diagnostic"] = True
    if raw.get("answer_note"):
        out["answer_note"] = raw["answer_note"]    # 저하된 검색을 답에서 말하라는 지시(별칭도 같다)
    return out


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
