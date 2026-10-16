# -*- coding: utf-8 -*-
"""MCP JSON-RPC 디스패처 — 전송 중립(stdio·Streamable HTTP 가 공유).

MCP 스펙의 서버 최소면만 구현한다: initialize / initialized / ping /
tools/list / tools/call / prompts/list / prompts/get. 그 밖의 메서드는 -32601.
도구 실행 오류는 스펙대로 프로토콜 오류가 아니라 result.isError=True 로 돌려준다
(호스트가 재시도·수정 가능하도록).

`prompts/*` 는 W2 에서 열었다(역할 열여섯을 프롬프트로 낸다 — 구현은 `roles.py`).
**`resources/*` 는 계속 -32601 이다.** 그 비구현은 실수가 아니라 판정이라
(`vinc_conventions`·`vinc_domains` 가 이미 같은 것을 도구로 낸다) 회귀 테스트가
둘의 갈림을 그대로 못 박는다: prompts 열림, resources 닫힘.

디스패처가 전송 중립이라 stdio 와 허브가 한 구현을 같이 쓴다 — 호스티드 노출(D3)이
따로 배선할 것 없이 여기서 성립한다.
"""
from __future__ import annotations

import json

from . import __version__
from . import community, roles
from . import tools
from . import toolcall
from .bridge import Bridge

# 최신이 앞. 클라이언트가 목록 밖 버전을 요청하면 **최신**으로 응답한다(스펙:
# "SHOULD respond with the latest version it supports") — 구버전으로 떨어뜨리면
# 최신 리비전으로 협상하는 비-Anthropic 호스트가 기능을 잃는다.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]

# tools/list 는 tools.specs() 를 **그대로** 낸다. 여기 description override 계층이
# 있었는데(recall·domains 2건), 덧붙이는 게 아니라 통째로 **교체**하는 구조라
# tools.py 를 고쳐도 호스트에는 안 닿았다 — 바로 이 자리 주석이 "tools.py 가 단일
# 출처"라고 적어 둔 채로. vinc_ingest override 는 F1 때 같은 이유로 이미 원 정의로
# 되돌렸고, 남은 2건의 내용(에피소드 전용 · 도메인별 집계 카운트)도 tools.py 의
# 설명에 흡수했으므로 계층 자체를 걷어낸다. 정본은 tools.py 하나다.


def _tool_specs(hub: bool = False) -> list[dict]:
    return tools.specs(hub=hub)


def _where_am_i(forced_owner: str | None) -> str | None:
    """`initialize` 에 실을 한 문단. 로컬(`forced_owner` 없음)이면 None.

    ⚠ **계정 조회가 실패해도 문단은 나간다.** 목록을 못 얻는 것과 기본이 개인이라는
    사실은 다른 층이고, 후자가 더 중요하다. 목록만 빠지고 나머지는 남는다.
    """
    if not forced_owner:
        return None                     # 로컬 stdio — 고를 스페이스가 없다
    if forced_owner.startswith("team:"):
        return ("You are connected at a team address, so every call in this session "
                "acts on that team's shared graph (" + forced_owner + ") and the "
                "`space` argument is ignored. Call vinc_whoami to confirm.")
    line = ("You are connected to your personal graph (" + forced_owner + "). "
            "Every call acts here unless you pass `space` with a team id, so a write "
            "with no `space` lands in your personal graph even if you meant a team.")
    try:
        from . import entitlement
        rows = entitlement.list_spaces(forced_owner.split(":", 1)[1])
    except Exception:
        rows = None
    if rows:
        named = ", ".join(
            (r.get("name") or "team") + " (" + r["id"] + ", " + r["role"] + ")"
            for r in rows)
        line += " Teams you can reach: " + named + "."
    elif rows is None:
        # 빈 목록과 "못 물었다"를 가른다 — 뭉개면 "팀이 없다"로 읽힌다.
        line += " The team list could not be fetched right now; vinc_whoami retries it."
    return line


class Dispatcher:
    def __init__(self, bridge: Bridge, *, hub: bool = False):
        self.bridge = bridge
        self.initialized = False
        # 프로파일은 둘이다: 로컬(stdio, 기본)과 허브. 허브는 이미 계정 그래프 자신이라
        # `target` 을 받지 않고 `space` 로 스페이스를 고른다(tools.specs).
        self.hub = hub

    # 반환 None = 응답 없음(notification 또는 id 없는 요청).
    # forced_owner: 원격 인증경로가 해석한 소유자(멀티테넌트 강제 스탬핑, B-B2).
    # plan: 소유자 티어(B-D2 쿼터에서 사용) — 로컬 stdio 는 둘 다 None.
    # entitlements: 등급에서 해석된 숫자(주제 상한 등) — 사이드카로 그대로 실린다.
    def handle(self, msg: dict, forced_owner: str | None = None,
               plan: str | None = None, quota=None,
               entitlements: dict | None = None, community_write=None, community_read=None) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        if method is None:
            return None                    # 응답 메시지 등 — 서버는 소비만 한다
        if method.startswith("notifications/"):
            if method == "notifications/initialized":
                self.initialized = True
            return None
        try:
            result = self._dispatch(method, params, forced_owner=forced_owner,
                                    plan=plan, quota=quota, entitlements=entitlements,
                                    community_write=community_write, community_read=community_read)
        except _MethodNotFound:
            return _err(msg_id, -32601, f"method not found: {method}")
        except _QuotaDenied as e:
            # 쿼터 거절문은 tools/call **결과 봉투**로 만들어져 있는데 prompts 에는
            # 그 봉투가 없다. 같은 문장을 프로토콜 오류로 낸다 — 조용히 서빙하는
            # 것보다 낫고, 호스트가 사용자에게 보여 줄 문장은 그대로다.
            return _err(msg_id, -32603, str(e))
        except (KeyError, ValueError) as e:
            return _err(msg_id, -32602, str(e))
        except Exception as e:              # 브리지 연결 실패 등
            return _err(msg_id, -32603, f"{type(e).__name__}: {e}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict, *, forced_owner: str | None = None,
                  plan: str | None = None, quota=None,
                  entitlements: dict | None = None, community_write=None, community_read=None) -> dict:
        if method == "initialize":
            want = params.get("protocolVersion")
            negotiated = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            info: dict = {"name": "vinc", "version": __version__}
            # icons·websiteUrl 은 SEP-973(스펙 2025-11-25)이 Implementation 에 더한 필드다.
            # **협상된 리비전이 그 필드를 아는 경우에만** 싣는다.
            #
            # ⚠ **게이트를 걷어내지 말 것** — 협상과 무관하게 실었다가 커넥터가 죽었다:
            # [[record:what-the-spec-permits-is-not-what-the-peer-accepts]].
            # PROTOCOL_VERSIONS 에 그 리비전이 없으므로 지금 이 필드는 아무에게도 안
            # 나가고, 나중에 목록에 넣으면 게이트가 저절로 열린다.
            # PNG 를 먼저 놓는 이유: 클라이언트는 PNG·JPEG 지원이 **필수**이고 SVG 는
            # 선택이다(SVG 는 스크립트를 품을 수 있다). 아이콘 자체는 /favicon.* 로
            # 계속 서빙한다 — 프로토콜 밖이라 무해하다.
            if negotiated >= "2025-11-25":   # ISO 날짜라 사전식 비교가 곧 시간순
                info["icons"] = [
                    {"src": "https://mcp.vincs.io/favicon.png",
                     "mimeType": "image/png", "sizes": "128x128"},
                    {"src": "https://mcp.vincs.io/favicon.svg",
                     "mimeType": "image/svg+xml", "sizes": "any"},
                ]
                info["websiteUrl"] = "https://vincs.io"
            out = {
                # `prompts` 는 로스터가 **비어 있어도** 광고한다. 그래프에 vinc/agent
                # 도메인이 없는 것은 고장이 아니라 상태이고(빈 목록이 정답), 능력을
                # 그래프 내용에 따라 켰다 껐다 하면 호스트가 캐시한 목록과 어긋난다.
                # `listChanged` 는 안 싣는다 — 서버→클라이언트 알림 경로가 없다.
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}, "prompts": {}},
                "serverInfo": info,
            }
            # 스페이스를 **묻기 전에** 알려 준다. `space` 인자가 생기면서 "안 실으면
            # 개인"이 기본이 됐는데(A안), 그 기본은 조용해서 위험하다 — 팀에 쓰려던
            # 호출이 아무 오류 없이 개인 그래프에 쌓인다. 도구로만 알 수 있게 두면
            # **부르기로 마음먹은 세션만** 알게 되고, 읽기만 하는 세션은 영영 모른다.
            # `instructions` 는 첫 도구 호출 전에 무조건 도착하므로 그 구멍을 덮는다.
            # 연결당 한 번이고 스페이스는 연결 중에 안 바뀌므로 신선도 문제도 없다.
            txt = _where_am_i(forced_owner)
            if txt:
                out["instructions"] = txt
            return out
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": _tool_specs(self.hub)}
        if method == "tools/call":
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            # REST도 같은 검증→한도→도구 호출을 탄다. MCP 봉투만 여기서 만든다.
            # 근거: [[decision:mcp-argument-errors-are-tool-results-not-protocol-errors-2026-09-24]]
            outcome = toolcall.execute(name, args, self.bridge,
                                       forced_owner=forced_owner, plan=plan, quota=quota,
                                       entitlements=entitlements,
                                       hub=self.hub, community_write=community_write, community_read=community_read)
            if outcome.kind == "protocol_error":
                raise outcome.error
            if outcome.kind != "ok":
                return outcome.result
            if name == community.WRITE_TOOL and outcome.result.get("isError"):
                return _tool_result(roles.serialize_result(outcome.result), name, is_error=True)
            return _result_text(roles.serialize_result(outcome.result), name, args)
        if method in ("prompts/list", "prompts/get"):
            if community.is_space(forced_owner):
                raise PermissionError("Community roles are data. Add a role to your own graph before using it as a prompt.")
            # 쿼터를 tools/call 과 **같이** 센다. 이 둘은 initialize·ping 과 달리
            # 스토어를 실제로 조회하므로, 무료로 두면 인증된 호출자에게 계량되지 않는
            # 조회 루프가 열린다(허브는 단일 라이터다). 로컬 stdio 는 forced_owner 가
            # 없어 여기 안 걸린다.
            if quota is not None and forced_owner:
                denial = quota(forced_owner, plan)
                if denial is not None:
                    raise _QuotaDenied(_denial_text(denial))
            if method == "prompts/list":
                return roles.list_prompts(self.bridge, forced_owner=forced_owner)
            return roles.get_prompt(params.get("name") or "",
                                    params.get("arguments") or {},
                                    self.bridge, forced_owner=forced_owner)
        raise _MethodNotFound(method)


# 도구 결과 크기 상한 — Anthropic 원격 MCP 요건은 "도구 결과당 최대 25,000 토큰".
# 토큰 대신 **문자수**로 잰다(토크나이저 의존 없이 결정론적). 보수적으로 1토큰≈3자로
# 잡아 25k 토큰 ≈ 75k 자 → 여유를 둬 72k. 초과분을 잘라 보내지 않는 이유: JSON 을
# 자르면 모델이 **깨진 구문**을 받는다 — 조용한 오염보다 명시적 거절이 낫다.
MAX_RESULT_CHARS = 72_000
#: 축소기에 주는 예산은 상한보다 이만큼 작다. 축소기 셋은 예산에 드는 순간 멈추고 **그 뒤에**
#: 표식(shrunk·note·next_cursor, 최대 ~450자)을 붙인다. 상한을 그대로 주면 조금만 넘긴 원본은
#: 표식만큼 다시 넘겨 아래 거부로 떨어진다 — 2026-09-05 라이브에서 concept:agent-dev-account 의
#: depth 1(75,481자)이 축소기 배포 뒤에도 거부돼 `agents_sync --pull` 이 죽었다.
#: 근거: [[record:shrinker-marker-overshoots-the-budget-2026-09-05]]
_MARKER_RESERVE = 1_000

# 응답이 큰 도구별로 '어떻게 줄일지'를 알려준다 — 그냥 "너무 큼"은 행동 불가.
_NARROW_HINT = {
    "vinc_community_read": "use authenticated POST /v1/community/read or the selected version's website export for the complete immutable artifact",
    # 허브의 depth 1 은 이제 축소기가 고리를 줄여 싣는다 — 여기 오는 것은 시드 노드
    # 자신이 상한을 넘을 때뿐이라 depth 를 낮추라는 말은 소용이 없다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S4
    "vinc_graph": "the seed node itself is larger than the limit; read it with "
                  "vinc_brief or its document with vinc_doc_get, and its ring with "
                  "vinc_neighbors",
    "vinc_decide": "lower `depth` (e.g. 1)",
    "vinc_recall": "lower `limit` and continue with `next_cursor`, or narrow by "
                   "`domain`/`tags`",
    "vinc_search": "lower `limit`, use detail:'summary' (the default), or continue "
                   "with `next_cursor`",
    "vinc_brief": "lower `max_excerpts`, or ask a narrower question",
    "vinc_domains": "this owner has an unusually large domain set",
    "vinc_suggest_ties": "lower `limit`",
}


def _tool_result(text: str, name: str, *, is_error: bool) -> dict:
    """도구 결과 봉투. ChatGPT 인용 쌍은 structuredContent 도 같이 싣는다."""
    out = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if not is_error and name in ("search", "fetch"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            out["structuredContent"] = payload
    return out


def _result_text(text: str, name: str, args: dict) -> dict:
    """tools/call 결과 봉투. 상한 초과면 **행동 가능한 오류**로 강등한다.

    GM-14: `tools.SHRINKERS` 에 등재된 도구(현재 vinc_search·vinc_brief·vinc_graph)는 거부
    대신 덜 중요한 조각을 덜어 예산 안의 **잘린 결과**로 돌려준다 — 잘렸다는 사실은
    도구별 표식(has_more/next_cursor, shrunk)이 알린다. JSON 을 문자 단위로 자르지 않으므로 구문은 항상 온전하다.
    미등재 도구는 기존 거부를 유지한다(깨진 JSON 보다 명시적 거절이 낫다)."""
    if len(text) <= MAX_RESULT_CHARS:
        return _tool_result(text, name, is_error=False)
    shrink = tools.SHRINKERS.get(name)
    if shrink is not None:
        try:
            slim = shrink(json.loads(text), MAX_RESULT_CHARS - _MARKER_RESERVE)
        except Exception:
            slim = None
        if slim is not None:
            slim_text = roles.serialize_result(slim)
            if len(slim_text) <= MAX_RESULT_CHARS:
                return _tool_result(slim_text, name, is_error=False)
    hint = _NARROW_HINT.get(name, "request a narrower slice")
    msg = (f"Result too large: {len(text):,} characters (limit {MAX_RESULT_CHARS:,}). "
           f"Nothing was changed. Narrow the request — {hint}.")
    return {"content": [{"type": "text", "text": msg}], "isError": True,
            "_meta": {"vinc_too_large": {"chars": len(text),
                                         "limit": MAX_RESULT_CHARS, "tool": name}}}


class _MethodNotFound(Exception):
    pass


class _QuotaDenied(Exception):
    """쿼터 거절. tools/call 은 결과 봉투로 강등하지만 prompts 에는 그 봉투가 없다."""


def _denial_text(denial: dict) -> str:
    """쿼터 거절 dict(= tools/call 결과 모양)에서 사람이 읽을 문장만 꺼낸다."""
    for part in denial.get("content") or []:
        if part.get("text"):
            return str(part["text"])
    return "rate limit reached"


def _err(msg_id, code: int, message: str) -> dict | None:
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}
