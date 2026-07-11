# -*- coding: utf-8 -*-
"""Streamable HTTP 전송 — 원격 프로파일(D36)용 루프백 MCP 서버.

바인딩은 127.0.0.1 고정 기본(F4) — 공개 노출은 Caddy 가 전담한다(deploy/
Caddyfile). 엔드포인트:

  POST /mcp                              JSON-RPC 요청(단건) → JSON 응답
  GET  /.well-known/oauth-protected-resource     RFC 9728
  GET  /.well-known/oauth-authorization-server   RFC 8414
  POST /register    동적 클라이언트 등록
  GET  /authorize   인가(관리 키 + PKCE) → 302 code
  POST /token       코드/리프레시 → bearer

인증 2모드(둘 중 하나만 통과하면 됨):
  - 정적 bearer(--auth-token | env VINC_MCP_TOKEN) — 파일럿·내부용.
  - OAuth 게이트(--oauth) — claude.ai 커넥터 등록용.
둘 다 미설정이면 기동을 거부한다(무인증 공개 사고 방지).

SSE 스트림은 제공하지 않는다(GET /mcp → 405) — 우리 도구는 전부 단발
요청/응답이라 스트리밍 이득이 없고, 스펙상 405 는 허용된 응답이다.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bridge import Bridge
from .oauth import OAuthGate
from .rpc import Dispatcher

MAX_BODY = 4 * 1024 * 1024


class MCPHTTPApp:
    def __init__(self, bridge: Bridge, *, static_token: str | None = None,
                 oauth: OAuthGate | None = None):
        if not static_token and oauth is None:
            raise ValueError("no auth configured: set a static token or enable OAuth")
        self.bridge = bridge
        self.static_token = static_token or None
        self.oauth = oauth
        # Dispatcher 는 상태가 initialized 플래그뿐 — 세션 간 공유해도 무해하지만
        # 스펙의 세션 관념에 맞춰 Mcp-Session-Id 별로 분리한다.
        self._sessions: dict[str, Dispatcher] = {}
        self._lock = threading.Lock()

    def dispatcher(self, session_id: str) -> Dispatcher:
        with self._lock:
            d = self._sessions.get(session_id)
            if d is None:
                d = Dispatcher(self.bridge)
                self._sessions[session_id] = d
            return d

    def authorized(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        token = header[len("Bearer "):].strip()
        if self.static_token and token == self.static_token:
            return True
        return self.oauth.verify(token) if self.oauth else False


class _Handler(BaseHTTPRequestHandler):
    server_version = "vinc-mcp"

    @property
    def app(self) -> MCPHTTPApp:
        return self.server.app  # type: ignore[attr-defined]

    # ── 응답 헬퍼 ───────────────────────────────────────────────────────────
    def _json(self, code: int, obj: dict, headers: dict | None = None) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _unauthorized(self) -> None:
        meta = f'{self._issuer()}/.well-known/oauth-protected-resource'
        self._json(401, {"error": "unauthorized"},
                   {"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'})

    def _issuer(self) -> str:
        if self.app.oauth is not None:
            return self.app.oauth.issuer
        host = self.headers.get("Host") or "127.0.0.1"
        return f"http://{host}"

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return {}
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        raw = self.rfile.read(n).decode("utf-8")
        if ctype == "application/x-www-form-urlencoded":
            return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ── 라우팅 ──────────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        gate = self.app.oauth
        if url.path == "/.well-known/oauth-protected-resource":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            return self._json(200, gate.resource_metadata())
        if url.path == "/.well-known/oauth-authorization-server":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            return self._json(200, gate.as_metadata())
        if url.path == "/authorize":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                location = gate.authorize(params)
            except PermissionError as e:
                return self._json(403, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if url.path == "/mcp":
            return self._json(405, {"error": "SSE stream not provided; POST JSON-RPC"})
        if url.path == "/health":
            return self._json(200, {"status": "ok"})
        return self._json(404, {"error": f"no such route: GET {url.path}"})

    def do_POST(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        gate = self.app.oauth
        if url.path == "/register":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                return self._json(201, gate.register(self._body()))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
        if url.path == "/token":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                return self._json(200, gate.token(self._body()))
            except ValueError as e:
                return self._json(400, {"error": "invalid_grant", "error_description": str(e)})
        if url.path == "/mcp":
            if not self.app.authorized(self.headers.get("Authorization")):
                return self._unauthorized()
            msg = self._body()
            if not isinstance(msg, dict) or not msg:
                return self._json(400, {"error": "expected a JSON-RPC message"})
            session = self.headers.get("Mcp-Session-Id") or "default"
            reply = self.app.dispatcher(session).handle(msg)
            if reply is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._json(200, reply, {"Mcp-Session-Id": session})
        return self._json(404, {"error": f"no such route: POST {url.path}"})

    def log_message(self, fmt, *args):  # 조용히 — 로그는 systemd/journald 몫
        pass


def make_server(app: MCPHTTPApp, host: str = "127.0.0.1", port: int = 8790) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


def serve_http(bridge: Bridge, *, host: str = "127.0.0.1", port: int = 8790,
               static_token: str | None = None, oauth: OAuthGate | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth)
    httpd = make_server(app, host, port)
    print(json.dumps({"host": host, "port": httpd.server_address[1],
                      "auth": ("static+oauth" if static_token and oauth else
                               "oauth" if oauth else "static")}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        bridge.close()
    return 0
