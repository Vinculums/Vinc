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
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import edverify
from .bridge import Bridge
from .oauth import CONNECT_TTL, OAuthGate
from .rpc import Dispatcher

MAX_BODY = 4 * 1024 * 1024


class MCPHTTPApp:
    def __init__(self, bridge: Bridge, *, static_token: str | None = None,
                 oauth: OAuthGate | None = None, verify_key=None,
                 store=None, quota_config: dict | None = None,
                 mcp_seed: bytes | None = None, account_public: bytes | None = None,
                 connect_url: str | None = None):
        if not static_token and oauth is None and verify_key is None:
            raise ValueError("no auth configured: static token, OAuth, or verify_key")
        self.bridge = bridge
        self.static_token = static_token or None
        self.oauth = oauth
        # 회원 승인 흐름(B-B3): 우리 서명키 + 계정 공개키 핀 + 공홈 동의 URL.
        # 셋 다 있어야 활성 — 없으면 /authorize 는 관리 키 경로만 남는다.
        self.mcp_seed = mcp_seed
        self.account_public = account_public
        self.connect_url = (connect_url or "").rstrip("/") or None
        # verify_key(raw)->{owner,plan}|None — 회원 API 키(vinc_sk_*) 검증(B-B1).
        # None 이면 키 경로 비활성. 프로덕션은 cli 가 entitlement.verify_key 를 주입.
        self.verify_key = verify_key
        # 쿼터(B-D2): usage 카운터는 store 에 영속. quota_config 없으면 무제한
        # (기존 테스트·로컬 프로파일 무영향) — cli 가 env 로 명시 활성화한다.
        self.store = store if store is not None else (oauth.store if oauth else None)
        self.quota_config = quota_config
        # Dispatcher 는 상태가 initialized 플래그뿐 — 세션 간 공유해도 무해하지만
        # 스펙의 세션 관념에 맞춰 Mcp-Session-Id 별로 분리한다.
        self._sessions: dict[str, Dispatcher] = {}
        self._lock = threading.Lock()

    def quota_check(self, owner: str, plan: str | None) -> dict | None:
        """소비 1회 시도. 허용이면 None, 초과·잠금이면 **tools/call 결과 dict**.

        카운터는 owner(public_id) 단위 — 키를 여러 개 만들어도 합산된다(우회 불가).
        """
        if not self.quota_config or self.store is None or not owner:
            return None                              # 미설정/로컬 = 무제한
        cfg = self.quota_config
        limit = cfg["pro_limit"] if plan == "pro" else cfg["free_limit"]
        r = self.store.quota_check(owner, limit=limit, window_s=cfg["window_s"],
                                   lock_s=cfg["lock_s"])
        if r["allowed"]:
            return None
        secs = r["retry_after_s"]
        hh, mm = secs // 3600, (secs % 3600) // 60
        tier = "Pro" if plan == "pro" else "Free"
        text = (f"{tier} tier limit reached ({limit} tool calls per "
                f"{int(cfg['window_s'] // 3600)}h). Access resumes in ~{hh}h{mm:02d}m."
                + ("" if plan == "pro" else " Upgrade for a higher limit: https://vincs.app/account"))
        return {"content": [{"type": "text", "text": text}], "isError": True,
                "_meta": {"vinc_quota": {"limit": limit, "retry_after_s": secs,
                                         "upgrade_url": "https://vincs.app/account"}}}

    def dispatcher(self, session_id: str) -> Dispatcher:
        with self._lock:
            d = self._sessions.get(session_id)
            if d is None:
                d = Dispatcher(self.bridge)
                self._sessions[session_id] = d
            return d

    def resolve_auth(self, header: str | None) -> dict:
        """Bearer 를 해석해 {ok, owner, plan, source}. owner 강제 스탬핑의 출처.

        - static: 정적 토큰 = 단일 테넌트 하위호환(owner=None → bridge.owner 사용).
        - apikey: `vinc_sk_*` → verify_key 루프백 → owner=user:{public_id}, plan.
        - oauth : 발급 토큰 → 코드에 스탬핑된 owner/plan(회원 경로면 채워짐).
        """
        miss = {"ok": False, "owner": None, "plan": None, "source": None}
        if not header or not header.startswith("Bearer "):
            return miss
        token = header[len("Bearer "):].strip()
        if self.static_token and token == self.static_token:
            return {"ok": True, "owner": None, "plan": None, "source": "static"}
        if token.startswith("vinc_sk_") and self.verify_key is not None:
            ent = self.verify_key(token)
            if ent:
                return {"ok": True, "owner": ent["owner"],
                        "plan": ent.get("plan"), "source": "apikey"}
            return miss
        if self.oauth is not None:
            rec = self.oauth.resolve(token)
            if rec:
                return {"ok": True, "owner": rec.get("owner"),
                        "plan": rec.get("plan"), "source": "oauth"}
        return miss

    def authorized(self, header: str | None) -> bool:
        return self.resolve_auth(header)["ok"]

    # ── 회원 승인 흐름(B-B3) ─────────────────────────────────────────────────
    def member_connect_enabled(self) -> bool:
        return bool(self.oauth and self.mcp_seed and self.account_public and self.connect_url)

    def begin_member_connect(self, params: dict) -> str:
        """/authorize(회원 경로) → connect_request 저장 + CRA 서명 → 공홈 동의 URL.

        CRA 는 **fragment** 로 넘긴다(#) — 쿼리스트링과 달리 서버 로그·Referer 에
        남지 않는다. 10분 TTL·단일 소비라 유출돼도 창이 좁다.
        """
        if not self.member_connect_enabled():
            raise PermissionError("member connect is not configured")
        req_id, meta = self.oauth.begin_connect(params)   # 파라미터·PKCE 검증 포함
        now = int(time.time())
        cra = edverify.sign_assertion(self.mcp_seed, {
            "typ": "cra", "iss": "mcp", "aud": "account", "req": req_id,
            "client_id": meta["client_id"], "client_name": meta["client_name"],
            "redirect_uri": meta["redirect_uri"], "iat": now, "exp": now + CONNECT_TTL})
        return f"{self.connect_url}#req={urllib.parse.quote(req_id)}&cra={cra}"

    def complete_member_connect(self, params: dict) -> str:
        """/authorize/complete → 계정 승인(AA) 검증·단일 소비 → 코드 발급 → claude.ai 복귀.

        AA 가 owner/plan 의 유일한 출처다. 서명·수명(≤2분)·req 바인딩을 모두 보고,
        connect_request 는 store 에서 원자적으로 단일 소비된다(재생 불가).
        """
        if not self.member_connect_enabled():
            raise PermissionError("member connect is not configured")
        req = params.get("req") or ""
        payload = edverify.verify_assertion(self.account_public, params.get("aa") or "",
                                            max_age=120)
        if not payload or payload.get("typ") != "aa" or payload.get("iss") != "account":
            raise PermissionError("invalid or expired approval")
        if payload.get("req") != req or not req:
            raise PermissionError("approval does not match this request")
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise ValueError("approval is missing a subject")
        plan = payload.get("plan") if payload.get("plan") in ("free", "pro") else "free"
        return self.oauth.complete_connect(req, owner=f"user:{sub}", plan=plan)


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

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

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
                # key= 있으면 관리 키 자동승인(부트스트랩), 없으면 회원 승인 흐름.
                # 회원 경로가 켜져 있으면 이쪽이 정상 경로다(B-B3).
                if params.get("key") or not self.app.member_connect_enabled():
                    location = gate.authorize(params)
                else:
                    location = self.app.begin_member_connect(params)
            except PermissionError as e:
                return self._json(403, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._redirect(location)
        if url.path == "/authorize/complete":
            if gate is None:
                return self._json(404, {"error": "oauth disabled"})
            try:
                location = self.app.complete_member_connect(params)
            except PermissionError as e:
                return self._json(403, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._redirect(location)
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
            auth = self.app.resolve_auth(self.headers.get("Authorization"))
            if not auth["ok"]:
                return self._unauthorized()
            msg = self._body()
            if not isinstance(msg, dict) or not msg:
                return self._json(400, {"error": "expected a JSON-RPC message"})
            session = self.headers.get("Mcp-Session-Id") or "default"
            # forced_owner: 원격 인증경로가 해석한 소유자 → tools 가 args.owner 를
            # 무시하고 강제 스탬핑(멀티테넌트 봉쇄, B-B2). static/None 이면 하위호환.
            # quota: tools/call 에서만 소비(initialize/tools_list/ping 무료, B-D2).
            reply = self.app.dispatcher(session).handle(
                msg, forced_owner=auth["owner"], plan=auth["plan"],
                quota=self.app.quota_check)
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
               static_token: str | None = None, oauth: OAuthGate | None = None,
               verify_key=None, quota_config: dict | None = None,
               mcp_seed: bytes | None = None, account_public: bytes | None = None,
               connect_url: str | None = None) -> int:
    app = MCPHTTPApp(bridge, static_token=static_token, oauth=oauth,
                     verify_key=verify_key, quota_config=quota_config,
                     mcp_seed=mcp_seed, account_public=account_public,
                     connect_url=connect_url)
    httpd = make_server(app, host, port)
    modes = [m for m, on in (("static", static_token), ("oauth", oauth),
                             ("apikey", verify_key),
                             ("member", app.member_connect_enabled())) if on]
    print(json.dumps({"host": host, "port": httpd.server_address[1],
                      "auth": "+".join(modes) or "none"}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        bridge.close()
    return 0
