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
            return {"tools": tools.specs()}
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
