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

# tools/list 응답의 description 보강 — 원 정의(vinc_mcp/tools.py)는 이번 작업 스코프
# 밖(다른 에이전트 동시 작업 파일 제외 목록에는 없으나, 작업 파일을 정확히 2개로
# 한정한 지시에 따라 여기서 override 한다). fragment.py 실제 허용 (from,to) 쌍을
# 그대로 반영 — 실사용 사고: 허용 안 되는 쌍은 skip 되고 warnings + partial:true 로
# 보고된다는 점을 명시한다.
_DESC_OVERRIDES: dict[str, str] = {
    "vinc_ingest": (
        "MERGE an authored graph fragment (nodes + edges) into the store by exact id. "
        "The only write surface. Reuse existing ids (read-before-write). "
        "Edge (from,to) allow-matrix (api/fragment.py SSoT): "
        "relates_to | prerequisite_of | derives_from | inherits_from = concept→concept "
        "or record→concept (record→concept is preserved as ATTACHES_TO{type=<edge type>} "
        "since the embedded rel table fixes FROM=Record/TO=Concept); "
        "connects_via = bridge→concept (stored as CONNECTS). relates_to is treated as "
        "undirected between concepts: if the reverse edge already exists, the new one is "
        "skipped and counted as merged.edges.skipped_duplicate. Any node type, edge type, "
        "or (from,to) pair outside this matrix is skipped and reported in warnings, and "
        "the response sets partial:true whenever warnings is non-empty."
    ),
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
    def handle(self, msg: dict) -> dict | None:
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
            result = self._dispatch(method, params)
        except _MethodNotFound:
            return _err(msg_id, -32601, f"method not found: {method}")
        except (KeyError, ValueError) as e:
            return _err(msg_id, -32602, str(e))
        except Exception as e:              # 브리지 연결 실패 등
            return _err(msg_id, -32603, f"{type(e).__name__}: {e}")
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> dict:
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
            try:
                out = tools.call(name, args, self.bridge)
            except (KeyError, ValueError):
                raise                       # 잘못된 도구/인자 = 프로토콜 오류(-32602)
            except Exception as e:          # 실행 오류 = 도구 결과로 강등(스펙)
                return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                        "isError": True}
            return {"content": [{"type": "text",
                                 "text": json.dumps(out, ensure_ascii=False)}],
                    "isError": False}
        raise _MethodNotFound(method)


class _MethodNotFound(Exception):
    pass


def _err(msg_id, code: int, message: str) -> dict | None:
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}
