# -*- coding: utf-8 -*-
"""MCP JSON-RPC 디스패처 — 전송 중립(stdio·Streamable HTTP 가 공유).

MCP 스펙의 서버 최소면만 구현한다: initialize / initialized / ping /
tools/list / tools/call. 그 밖의 메서드는 -32601. 도구 실행 오류는 스펙대로
프로토콜 오류가 아니라 result.isError=True 로 돌려준다(호스트가 재시도·수정
가능하도록).
"""
from __future__ import annotations

import json

from . import __version__
from . import tools
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


def _tool_specs(allow_target: bool = True) -> list[dict]:
    return tools.specs(allow_target=allow_target)


class Dispatcher:
    def __init__(self, bridge: Bridge, *, allow_target: bool = True):
        self.bridge = bridge
        self.initialized = False
        # `target`(로컬/원격 선택)은 **데스크탑 커넥터에만** 의미가 있다. 허브는
        # 이미 그 '원격'이므로 자기 자신에게 다시 아웃바운드를 걸 이유가 없다
        # (위험 감사 5.5). 기본 True 는 stdio·테스트의 현행 동작 유지.
        self.allow_target = allow_target

    # 반환 None = 응답 없음(notification 또는 id 없는 요청).
    # forced_owner: 원격 인증경로가 해석한 소유자(멀티테넌트 강제 스탬핑, B-B2).
    # plan: 소유자 티어(B-D2 쿼터에서 사용) — 로컬 stdio 는 둘 다 None.
    # entitlements: 등급에서 해석된 숫자(주제 상한 등) — 사이드카로 그대로 실린다.
    def handle(self, msg: dict, forced_owner: str | None = None,
               plan: str | None = None, quota=None,
               entitlements: dict | None = None) -> dict | None:
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
                                    plan=plan, quota=quota, entitlements=entitlements)
        except _MethodNotFound:
            return _err(msg_id, -32601, f"method not found: {method}")
        except (KeyError, ValueError) as e:
            return _err(msg_id, -32602, str(e))
        except Exception as e:              # 브리지 연결 실패 등
            return _err(msg_id, -32603, f"{type(e).__name__}: {e}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict, *, forced_owner: str | None = None,
                  plan: str | None = None, quota=None,
                  entitlements: dict | None = None) -> dict:
        if method == "initialize":
            want = params.get("protocolVersion")
            negotiated = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            info: dict = {"name": "vinc", "version": __version__}
            # icons·websiteUrl 은 SEP-973(스펙 2025-11-25)이 Implementation 에 더한 필드다.
            # **협상된 리비전이 그 필드를 아는 경우에만** 싣는다.
            #
            # ⚠ 실사고(2026-07-31): 협상 버전과 무관하게 항상 실었다가 claude.ai 커넥터가
            # "계정은 인증됐지만 연결 중 오류"로 죽었다. 근거로 삼았던 "모르는 클라이언트는
            # 추가 필드를 그냥 무시한다"는 **스펙 문장에는 맞지만 구현에는 틀렸다** — 클라이언트가
            # 협상된 리비전의 스키마로 엄격 검증하면 모르는 키에서 거부한다. 사양이 허용하는
            # 것과 상대가 받아주는 것은 다르다.
            #
            # 우리는 PROTOCOL_VERSIONS 에 2025-11-25 를 넣지 않는다(그 리비전 전체를 준수한다고
            # 주장할 근거가 없다). 그래서 이 두 필드는 지금 **아무에게도 안 나간다**. 나중에
            # 그 리비전을 정식 지원할 때 목록에 추가하면 이 게이트가 저절로 열린다.
            # 그때 상대는 2025-11-25 를 요청한 클라이언트뿐이므로 이 필드를 안다.
            #
            # PNG 를 먼저 놓는 이유: 스펙상 클라이언트는 PNG·JPEG 지원이 **필수**이고
            # SVG 는 선택이다(SVG 는 스크립트를 품을 수 있어 소비 시 주의 대상).
            #
            # 아이콘 자체는 /favicon.{png,ico,svg} 로 계속 서빙한다 — 프로토콜 밖이라
            # 무해하고, 브라우저 탭·수동 참조에 쓰인다.
            if negotiated >= "2025-11-25":   # ISO 날짜라 사전식 비교가 곧 시간순
                info["icons"] = [
                    {"src": "https://mcp.vincs.io/favicon.png",
                     "mimeType": "image/png", "sizes": "128x128"},
                    {"src": "https://mcp.vincs.io/favicon.svg",
                     "mimeType": "image/svg+xml", "sizes": "any"},
                ]
                info["websiteUrl"] = "https://vincs.io"
            return {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": info,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": _tool_specs(self.allow_target)}
        if method == "tools/call":
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            # 쿼터는 여기서만 소비(B-D2) — initialize/tools_list/ping 은 무료라
            # 커넥터 등록·핸드셰이크가 한도를 갉아먹지 않는다. 로컬 stdio 는
            # forced_owner 가 없어 quota 가 호출되지 않는다(무제한).
            if quota is not None and forced_owner:
                denial = quota(forced_owner, plan)
                if denial is not None:
                    return denial
            try:
                out = tools.call(name, args, self.bridge, forced_owner=forced_owner,
                                 entitlements=entitlements,
                                 allow_target=self.allow_target)
            except (KeyError, ValueError):
                raise                       # 잘못된 도구/인자 = 프로토콜 오류(-32602)
            except Exception as e:          # 실행 오류 = 도구 결과로 강등(스펙)
                return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                        "isError": True}
            return _result_text(json.dumps(out, ensure_ascii=False), name, args)
        raise _MethodNotFound(method)


# 도구 결과 크기 상한 — Anthropic 원격 MCP 요건은 "도구 결과당 최대 25,000 토큰".
# 토큰 대신 **문자수**로 잰다(토크나이저 의존 없이 결정론적). 보수적으로 1토큰≈3자로
# 잡아 25k 토큰 ≈ 75k 자 → 여유를 둬 72k. 초과분을 잘라 보내지 않는 이유: JSON 을
# 자르면 모델이 **깨진 구문**을 받는다 — 조용한 오염보다 명시적 거절이 낫다.
MAX_RESULT_CHARS = 72_000

# 응답이 큰 도구별로 '어떻게 줄일지'를 알려준다 — 그냥 "너무 큼"은 행동 불가.
_NARROW_HINT = {
    "vinc_graph": "lower `depth` (e.g. 1) or pick a more specific `seed_id`",
    "vinc_decide": "lower `depth` (e.g. 1)",
    "vinc_recall": "lower `limit`, or narrow by `domain`/`tags`",
    "vinc_search": "lower `limit`, use detail:'summary' (the default), or continue "
                   "with `next_cursor`",
    "vinc_domains": "this owner has an unusually large domain set",
    "vinc_suggest_ties": "lower `limit`",
}


def _result_text(text: str, name: str, args: dict) -> dict:
    """tools/call 결과 봉투. 상한 초과면 **행동 가능한 오류**로 강등한다.

    GM-14: `tools.SHRINKERS` 에 등재된 도구(현재 vinc_search)는 거부 대신 꼬리
    행을 덜어 예산 안의 **잘린 페이지**로 돌려준다 — 잘렸다는 사실은 has_more 와
    next_cursor 가 알린다. JSON 을 문자 단위로 자르지 않으므로 구문은 항상 온전하다.
    미등재 도구는 기존 거부를 유지한다(깨진 JSON 보다 명시적 거절이 낫다)."""
    if len(text) <= MAX_RESULT_CHARS:
        return {"content": [{"type": "text", "text": text}], "isError": False}
    shrink = tools.SHRINKERS.get(name)
    if shrink is not None:
        try:
            slim = shrink(json.loads(text), MAX_RESULT_CHARS)
        except Exception:
            slim = None
        if slim is not None:
            slim_text = json.dumps(slim, ensure_ascii=False)
            if len(slim_text) <= MAX_RESULT_CHARS:
                return {"content": [{"type": "text", "text": slim_text}],
                        "isError": False}
    hint = _NARROW_HINT.get(name, "request a narrower slice")
    msg = (f"Result too large: {len(text):,} characters (limit {MAX_RESULT_CHARS:,}). "
           f"Nothing was changed. Narrow the request — {hint}.")
    return {"content": [{"type": "text", "text": msg}], "isError": True,
            "_meta": {"vinc_too_large": {"chars": len(text),
                                         "limit": MAX_RESULT_CHARS, "tool": name}}}


class _MethodNotFound(Exception):
    pass


def _err(msg_id, code: int, message: str) -> dict | None:
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}
