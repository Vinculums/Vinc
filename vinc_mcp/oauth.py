# -*- coding: utf-8 -*-
"""OAuth 게이트(원격 전용, remote-profile §7-3) — MCP 스펙 범위 최소 구현.

구성: 보호 자원 메타데이터(RFC 9728) + 인가 서버 메타데이터(RFC 8414) +
동적 클라이언트 등록(RFC 7591) + 인가 코드 + PKCE(S256 전용) + 토큰 발급.
자원 서버와 인가 서버는 같은 프로세스(단일 사용자 규모 R5 — 분리 이득 없음).

승인 2모드(둘 다 이 게이트가 지원):
  - **관리 키**(env VINC_OAUTH_ADMIN_KEY): /authorize?key= 자동 승인. 파일럿·
    부트스트랩용. 이 경로로 발급된 토큰은 owner=None(=단일 테넌트 하위호환).
  - **회원 승인**(B-B3): /authorize → connect_request 저장 → 공홈 로그인·동의 →
    owner=user:{public_id} 로 코드·토큰 스탬핑. authorize_member()/complete().
키·회원경로 모두 미설정이면 인가는 거부되고 정적 토큰 모드만 남는다.

상태 영속(B-A): 인메모리 dict → vinc_mcp.store.Store(SQLite). **재기동에도
클라이언트·토큰·쿼터가 유지**된다(재인가 불필요). 기본 store 는 인메모리
(`:memory:`, 디스크 미오염) — 프로덕션은 cli.py 가 디스크 Store 를 주입한다.
⚠ "재기동에도 유지"는 **상태 파일이 영속 볼륨에 있을 때만** 성립한다. 그 경로가
컨테이너 임시 레이어로 떨어져 배포마다 등록이 날아간 것이 아래 `unknown client_id`
사고의 원인이었다(DEPLOYMENT.md §4.6) — 경로는 엔트리포인트가 `/data/mcp` 로 고정한다.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

from .store import Store

CODE_TTL = 300
TOKEN_TTL = 3600
CONNECT_TTL = 600      # 회원 승인 대기(B-B3) — 10분


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthGate:
    def __init__(self, issuer: str, admin_key: str | None,
                 token_ttl: int = TOKEN_TTL, *, store: Store | None = None):
        self.issuer = issuer.rstrip("/")
        self.admin_key = admin_key or None
        self.token_ttl = token_ttl
        self.store = store if store is not None else Store(":memory:")

    # ── 메타데이터 ──────────────────────────────────────────────────────────
    def resource_metadata(self) -> dict:
        return {"resource": self.issuer + "/mcp",
                "authorization_servers": [self.issuer]}

    def as_metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "registration_endpoint": self.issuer + "/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    # ── 동적 등록(RFC 7591) ─────────────────────────────────────────────────
    def register(self, body: dict) -> dict:
        uris = body.get("redirect_uris") or []
        if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris) or not uris:
            raise ValueError("redirect_uris (non-empty list) is required")
        client_id = secrets.token_urlsafe(16)
        self.store.add_client(client_id, uris, body.get("client_name") or "")
        return {"client_id": client_id, "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]}

    # ── 인가 파라미터 검증(두 승인 모드 공통) ────────────────────────────────
    def _validate_authorize(self, params: dict) -> tuple[str, str, str]:
        """(client_id, redirect_uri, challenge) 반환 — 검증 실패 시 예외."""
        client = self.store.get_client(params.get("client_id") or "")
        if client is None:
            raise ValueError("unknown client_id")
        redirect = params.get("redirect_uri") or ""
        if redirect not in client["redirect_uris"]:
            raise ValueError("redirect_uri not registered")
        if params.get("response_type") != "code":
            raise ValueError("response_type must be 'code'")
        challenge = params.get("code_challenge") or ""
        if not challenge or params.get("code_challenge_method") != "S256":
            raise ValueError("PKCE S256 code_challenge is required")
        return params["client_id"], redirect, challenge

    # ── 인가: 관리 키 자동 승인(코드 + PKCE) ─────────────────────────────────
    def authorize(self, params: dict) -> str:
        """관리 키 경로. 검증 통과 시 redirect 대상 URL(302 Location)."""
        if self.admin_key is None:
            raise PermissionError("authorization disabled: no admin key configured")
        if params.get("key") != self.admin_key:
            raise PermissionError("wrong or missing admin key")
        client_id, redirect, challenge = self._validate_authorize(params)
        code = secrets.token_urlsafe(24)
        # 관리 키 경로 = 단일 테넌트 하위호환 → owner/plan 미지정(None).
        self.store.add_code(code, client_id, redirect, challenge,
                            owner=None, plan=None, exp=time.time() + CODE_TTL)
        return self._redirect_with(redirect, {"code": code}, params.get("state"))

    # ── 인가: 회원 승인 대기 등록(B-B3) ──────────────────────────────────────
    def begin_connect(self, params: dict) -> tuple[str, dict]:
        """검증 후 connect_request 를 저장하고 (req_id, client_meta) 반환.

        http_server 가 이를 CRA 로 서명해 공홈 /connect 로 리다이렉트한다.
        승인은 계정 서비스가 하고, complete() 로 코드가 발급된다.
        """
        client_id, redirect, challenge = self._validate_authorize(params)
        client = self.store.get_client(client_id)
        req_id = secrets.token_urlsafe(18)
        self.store.add_connect_request(req_id, client_id, redirect, challenge,
                                       params.get("state"), exp=time.time() + CONNECT_TTL)
        return req_id, {"client_id": client_id, "client_name": (client or {}).get("name", ""),
                        "redirect_uri": redirect}

    def complete_connect(self, req_id: str, owner: str, plan: str) -> str:
        """계정 승인(AA 검증 후) → connect_request 단일 소비 → 코드 발급 → redirect URL.

        owner/plan 이 코드에 스탬핑되고, 이후 토큰까지 전파된다(멀티테넌트 바인딩).
        """
        rec = self.store.consume_connect_request(req_id)
        if rec is None:
            raise ValueError("connect request not found, expired, or already used")
        code = secrets.token_urlsafe(24)
        self.store.add_code(code, rec["client_id"], rec["redirect_uri"], rec["challenge"],
                            owner=owner, plan=plan, exp=time.time() + CODE_TTL)
        self.store.set_owner_plan(owner, plan)
        return self._redirect_with(rec["redirect_uri"], {"code": code}, rec.get("state"))

    @staticmethod
    def _redirect_with(redirect: str, qs: dict, state: str | None) -> str:
        if state:
            qs = {**qs, "state": state}
        sep = "&" if "?" in redirect else "?"
        return redirect + sep + urllib.parse.urlencode(qs)

    # ── 토큰 발급 ───────────────────────────────────────────────────────────
    def token(self, form: dict) -> dict:
        grant = form.get("grant_type")
        if grant == "authorization_code":
            rec = self.store.pop_code(form.get("code") or "")
            if rec is None or rec["exp"] < time.time():
                raise ValueError("invalid or expired code")
            if form.get("client_id") != rec["client_id"]:
                raise ValueError("client_id mismatch")
            if form.get("redirect_uri") != rec["redirect_uri"]:
                raise ValueError("redirect_uri mismatch")
            if _s256(form.get("code_verifier") or "") != rec["challenge"]:
                raise ValueError("PKCE verification failed")
            return self._issue(rec["client_id"], rec.get("owner"), rec.get("plan"))
        if grant == "refresh_token":
            rec = self.store.pop_refresh(form.get("refresh_token") or "")
            if rec is None:
                raise ValueError("invalid refresh_token")
            return self._issue(rec["client_id"], rec.get("owner"), rec.get("plan"))
        raise ValueError(f"unsupported grant_type: {grant}")

    def _issue(self, client_id: str, owner: str | None = None,
               plan: str | None = None) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self.store.add_token(access, client_id, owner, plan, time.time() + self.token_ttl)
        self.store.add_refresh(refresh, client_id, owner, plan)
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": self.token_ttl, "refresh_token": refresh}

    # ── 자원 접근 검증 ──────────────────────────────────────────────────────
    def verify(self, token: str) -> bool:
        return self.store.get_token(token) is not None

    def resolve(self, token: str) -> dict | None:
        """유효 토큰이면 {client_id, owner, plan, exp}, 아니면 None(owner 스탬핑용)."""
        return self.store.get_token(token)
