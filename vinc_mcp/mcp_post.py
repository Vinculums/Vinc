# -*- coding: utf-8 -*-
"""`POST /mcp` 의 JSON-RPC 파이프라인: 인증을 마친 요청 하나의 본문(단건 또는 배치)을 메시지마다
스페이스 · 역할 · 읽기 키 게이트에 태우고, 디스패처로 보내고, 답을 쓴다.

`http_server._Handler.do_POST` 가 오리진, `MCP-Protocol-Version` 헤더, `?profile=`, 베어러 인증을 끝낸 뒤
`answer(handler, auth, space, profile)` 를 부른다. 함수는 핸들러의 메서드(`_auth` · `_body` · `_json` ·
`_verification_unavailable`)와 앱(`handler.app`)을 거쳐 일한다. 이 모듈은 `http_server` 를 import 하지
않는다.
"""
from __future__ import annotations

from . import entitlement, modern, spaces, toolcall, tools
from .hub_quota import _with_quota_meta
from .rpc import PROTOCOL_VERSIONS

#: 역할 계약을 읽는 비도구 메서드. 스페이스·역할·읽기 키 게이트를 tools/call 과 같이 탄다.
_ROLE_READS = ("prompts/", "skills/", "resources/")


def answer(handler, auth: dict, space: str | None, profile: str | None):
    """인증을 마친 `POST /mcp` 요청 하나의 본문에 답한다. `auth` 는 요청 단위 인증, `space` 는
    주소가 정한 팀(개인 주소면 None), `profile` 은 주소의 `?profile=` 값이다."""
    msg = handler._body()
    # JSON-RPC 배치. `PROTOCOL_VERSIONS` 가 2025-03-26 을 광고하는데 그 리비전은
    # 배치를 지원한다(2025-06-18 에서 제거됨) — 전에는 dict 가 아니면 400 이라
    # **광고한 리비전으로 협상한 클라이언트가 400 을 받았다.** 광고를 접는
    # 대신 지원하는 쪽을 골랐다: 목록에서 빼면 그 리비전만 말하는 구형
    # 클라이언트가 끊기는데, 받아 주는 쪽은 아무도 안 끊긴다.
    batch = isinstance(msg, list)
    msgs = msg if batch else [msg]
    if not msgs or any(not isinstance(m, dict) or not m for m in msgs):
        return handler._json(400, {"error": "expected a JSON-RPC message"})
    # 모던 요청은 버전·능력이 본문 `_meta` 에 있고 헤더가 그것을 비춘다. 어긋나거나
    # 모자라면 스펙대로 400 과 JSON-RPC 오류다. `_meta` 가 없는 요청(핸드셰이크
    # 세대)은 여기를 그냥 지난다. 배치는 모던 세대에 없으므로 메시지마다 디스패처가 본다.
    modern_version = None
    if not batch:
        modern_version, rej = modern.classify(msg, PROTOCOL_VERSIONS)
        rej = rej or modern.check_headers(handler.headers, msg, modern_version)
        if rej is not None:
            return handler._json(rej.status, rej.reply(msg.get("id")))
    session = handler.headers.get("Mcp-Session-Id") or "default"
    # forced_owner: 원격 인증경로가 해석한 소유자 → tools 가 args.owner 를
    # 무시하고 강제 스탬핑(멀티테넌트 봉쇄, B-B2). static/None 이면 하위호환.
    # quota: tools/call 에서만 소비(initialize/tools_list/ping 무료, B-D2).
    # entitlements: 등급에서 해석된 숫자(주제 상한). 원격 인증경로에서만
    # 실린다 — 로컬·정적 토큰은 owner 가 없어 자격 개념 자체가 없다.
    # 스페이스 해석 — **메시지마다** 한다. 팀 주소로 붙었으면 주소가 이미
    # 스페이스를 고정했으므로 인자는 무시하고 요청 단위 auth 를 그대로 쓴다.
    # 개인 주소에서만 `space` 인자가 고르고, 안 실으면 개인이다.
    #
    # ⚠ 게이트가 **요청당 하나**였던 전제가 여기서 무너진다: 배치 안의 메시지가
    # 서로 다른 스페이스를 겨눌 수 있으므로 역할·스코프도 메시지마다 갈린다.
    # 하나로 두면 배치 첫 메시지의 역할이 나머지를 통과시킨다.
    #
    # 멤버십 판정은 **새로 안 짠다** — 같은 resolve_auth 를 다시 부를 뿐이라
    # 키 경로·OAuth 경로·fail-closed 가 전부 그대로다. entitlement 캐시가
    # 왕복을 접으므로 같은 스페이스를 겨눈 배치는 계정을 한 번만 때린다.
    # 근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
    #
    # `space` 를 안 실었으면 **붙여넣은 주소가 스페이스를 말한다**(`url` 의
    # `?space=`). 사람이 채팅에 주소 하나 붙이면 어시스턴트가 스페이스를 하나씩
    # 두드려 볼 필요 없이 한 번에 간다. 판정은 인자와 **같은 줄**을 탄다 —
    # 주소에서 읽었다고 멤버십을 건너뛰지 않는다. 명시 인자가 있으면 그것이
    # 이긴다(`seed_id` 가 url 의 시드를 이기는 것과 같은 규칙).
    # 근거: [[decision:a-node-address-names-its-space]]
    def _gate_for(m):
        method = m.get("method")
        if str(method or "").startswith("events/"):
            # Events 의 스페이스는 구독 인자(`arguments.space`)가 고른다. 스페이스 확인은
            # 조회 도구 vinc_events 와 같은 줄을 탄다.
            params = m.get("params") or {}
            args = params.get("arguments") if isinstance(params, dict) else None
            return toolcall.preflight(
                "vinc_events", args if isinstance(args, dict) else {}, base_auth=auth,
                address_space=space,
                authenticate=lambda want: handler._auth(want, presented_at="/mcp"),
                valid_space=spaces.valid_space)
        if method != "tools/call" and not str(method or "").startswith(_ROLE_READS):
            return toolcall.Outcome("ok", auth=auth)
        params = m.get("params") or {}
        args = params.get("arguments") or (params if method != "tools/call" else {})
        return toolcall.preflight(
            (params.get("name") or "") if method == "tools/call" else "vinc_role", args, base_auth=auth,
            address_space=space,
            authenticate=lambda want: handler._auth(want, presented_at="/mcp"),
            valid_space=spaces.valid_space)

    # 배치는 기존 게이트 우선순위를 유지한다: 모든 메시지의 스페이스를
    # 먼저 보고, 그 다음 역할, 읽기 전용 키 순서로 첫 거절을 고른다.
    # 근거: [[decision:the-space-is-an-argument-and-the-default-is-personal]]
    try:
        gated = [(m, _gate_for(m)) for m in msgs]
    except entitlement.VerificationUnavailable:
        return handler._verification_unavailable()

    def _tool_error(m, message):
        if str(m.get("method") or "").startswith("events/"):
            return handler._json(200, {"jsonrpc": "2.0", "id": m.get("id"), "error": {
                "code": -32024, "message": message}})
        result = {"isError": True, "content": [{"type": "text", "text": message}]}
        if modern.classify(m, PROTOCOL_VERSIONS)[0]:
            result = modern.complete(result)
        return handler._json(200, {"jsonrpc": "2.0", "id": m.get("id"), "result": result})

    for kind in ("on_device", "space_not_found", "role_denied"):
        denied = next(((m, g) for m, g in gated if g.kind == kind), None)
        if denied is not None:
            return _tool_error(denied[0], denied[1].message)
    if space:
        # prompts 는 도구 봉투가 아니라 프로토콜 오류로 거절한다.
        role = auth.get("role") or ""
        if any(str(m.get("method") or "").startswith(_ROLE_READS) for m in msgs) \
                and not tools.role_allows(role, "vinc_neighbors"):
            first = next(m for m in msgs
                         if str(m.get("method") or "").startswith(_ROLE_READS))
            return handler._json(200, {
                "jsonrpc": "2.0", "id": first.get("id"),
                "error": {"code": -32603,
                          "message": f"role {role!r} may not read prompts"}})
    denied = next(((m, g) for m, g in gated if g.kind == "read_only"), None)
    if denied is not None:
        return _tool_error(denied[0], denied[1].message)
    pairs = [(m, g.auth) for m, g in gated]
    d = handler.app.dispatcher()

    def _run(m, a):
        # 2026-09-19 계정 정책: 개인 Free와 TEAM 공유 예산은 서로 독립이다.
        # 멤버 신원은 권한 검증에만 쓰고 소비는 해석된 그래프 owner로 센다.
        # 팀은 매번 계정 멤버십 검증을 거치며 등급도 TEAM으로 해석된다.
        ents = (handler.app.entitlements_for(a["plan"]) if a["owner"] else None)
        spent: dict = {}
        reply = d.handle(m, forced_owner=a["owner"], plan=a["plan"],
                         quota=handler.app.quota_for(a, spent),
                         quota_view=handler.app.quota_view,
                         entitlements=ents,
                         community_write=(lambda args: handler.app.community_write(a, args))
                         if handler.app.community_write is not None else None,
                         community_read=(lambda args: handler.app.community_read(a, args))
                         if handler.app.community_read is not None else None,
                         events_auth=_events_auth(a), profile=profile)
        if m.get("method") != "tools/call":
            return reply
        return _with_quota_meta(reply, handler.app.quota_meta(spent))

    replies = [r for r in (_run(m, a) for m, a in pairs) if r is not None]
    # 전부 notification 이면 본문 없이 202 — 배치도 같다(스펙).
    if not replies:
        handler.send_response(202)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return None
    # `Mcp-Session-Id` 는 **클라이언트가 준 값을 되돌려 줄 뿐**이다. 우리는
    # 세션을 발급하지도 추적하지도 않는다. 그래도 되돌리는 이유는 지금 붙어
    # 있는 호스트의 관측 동작을 바꾸지 않기 위해서다 — 없앨 이득이 없다.
    # 모던 요청은 세션 id 를 되돌리지 않고(스펙: 발급도 반사도 하지 않는다), 모르는
    # 메서드는 404 다. 핸드셰이크 세대는 지금처럼 200 과 반사.
    if modern_version:
        code = (replies[0].get("error") or {}).get("code")
        return handler._json(404 if code == -32601 else 200, replies[0])
    return handler._json(200, replies if batch else replies[0],
                         {"Mcp-Session-Id": session})


def _events_auth(a: dict) -> dict:
    """웹훅 구독의 주체와 구독 기록을 쓸 수 있는가. 주체는 팀이면 멤버, 개인이면 그 사람."""
    owner = a.get("owner") or ""
    team = owner.startswith("team:")
    principal = a.get("member") if team else (owner or None)
    can_write = a.get("scope") == "full" and (
        not team or tools.role_allows(a.get("role") or "", "vinc_ingest"))
    return {"principal": principal, "can_write": bool(principal and can_write)}
