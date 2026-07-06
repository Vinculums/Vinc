# -*- coding: utf-8 -*-
"""사이드카 HTTP 브리지 — vinc-mcp 도구 핸들러의 유일한 HTTP 클라이언트
([[vinc.mcp.skeleton]] §2). 127.0.0.1 전용, 세션 토큰(Bearer)으로만 인증.
stdlib(`urllib`) 전용 — 사이드카(`api/server.py`) 와 동일한 의존성 원칙.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class BridgeError(RuntimeError):
    """사이드카 호출 실패(연결 불가·비 2xx·응답 파싱 실패)."""


class SidecarBridge:
    """`host:port` + `token` 으로 사이드카(`api/server.py`)를 호출하는 얇은 클라이언트.
    비즈니스 로직 0 — 계약 검증은 `tools.py`, 여기는 HTTP 왕복만."""

    def __init__(self, host: str, port: int, token: str, *, timeout: float = 10.0):
        self._base = f"http://{host}:{port}"
        self._token = token
        self._timeout = timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        qs = ("?" + urllib.parse.urlencode(clean)) if clean else ""
        return self._call("GET", path + qs)

    def post(self, path: str, body: dict) -> dict:
        return self._call("POST", path, body)

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self._base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BridgeError(
                f"sidecar {method} {path} -> HTTP {exc.code}: "
                f"{exc.read().decode('utf-8', 'replace')}") from exc
        except urllib.error.URLError as exc:
            raise BridgeError(f"sidecar unreachable ({method} {path}): {exc}") from exc
        except json.JSONDecodeError as exc:
            raise BridgeError(f"sidecar {method} {path} -> invalid JSON response: {exc}") from exc
