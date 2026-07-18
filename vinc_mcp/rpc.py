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

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

# tools/list 응답의 description 보강. vinc_ingest 의 구 override(허용 행렬)는 F1
# 개방층 포팅과 함께 원 정의(vinc_mcp/tools.py — 정본)로 이관·삭제했다: 개방
# 캐리어(Entity/TYPED)·about·role·proposed_types 를 포함한 최신 계약은 tools.py 가
# 단일 출처다. 남은 override 는 이 작업과 무관한 보강 2건뿐.
_DESC_OVERRIDES: dict[str, str] = {
    "vinc_recall": (
        "List recent episodes in an owner scope, optionally narrowed by domain or tags. "
        "Episodes only — for Concept or Record nodes use vinc_graph or vinc_search instead."
    ),
    "vinc_domains": (
        "List the owner's domains discovered from data (open set, not a fixed list). "
        "Per domain, returns episodes/concepts/records/total aggregate counts."
    ),
}


def _tool_specs() -> list[dict]:
    """tools.specs() 위에 description override 를 얹어 반환(스키마는 그대로)."""
    return [
        {**spec, "description": _DESC_OVERRIDES.get(spec["name"], spec["description"])}
        for spec in tools.specs()
    ]


class Dispatcher:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.initialized = False

    # 반환 None = 응답 없음(notification 또는 id 없는 요청).
    # forced_owner: 원격 인증경로가 해석한 소유자(멀티테넌트 강제 스탬핑, B-B2).
    # plan: 소유자 티어(B-D2 쿼터에서 사용) — 로컬 stdio 는 둘 다 None.
    def handle(self, msg: dict, forced_owner: str | None = None,
               plan: str | None = None, quota=None) -> dict | None:
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
                                    plan=plan, quota=quota)
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
                  plan: str | None = None, quota=None) -> dict:
        if method == "initialize":
            want = params.get("protocolVersion")
            return {
                "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vinc", "version": __version__},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": _tool_specs()}
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
                out = tools.call(name, args, self.bridge, forced_owner=forced_owner)
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
    "vinc_search": "lower `limit`, or use a more specific `query`",
    "vinc_domains": "this owner has an unusually large domain set",
    "vinc_suggest_ties": "lower `limit`",
}


def _result_text(text: str, name: str, args: dict) -> dict:
    """tools/call 결과 봉투. 상한 초과면 **행동 가능한 오류**로 강등한다."""
    if len(text) <= MAX_RESULT_CHARS:
        return {"content": [{"type": "text", "text": text}], "isError": False}
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
