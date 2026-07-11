# -*- coding: utf-8 -*-
"""OAuth 게이트(원격 전용, remote-profile §7-3) — MCP 스펙 범위 최소 구현.

구성: 보호 자원 메타데이터(RFC 9728) + 인가 서버 메타데이터(RFC 8414) +
동적 클라이언트 등록(RFC 7591) + 인가 코드 + PKCE(S256 전용) + 토큰 발급.
자원 서버와 인가 서버는 같은 프로세스(단일 사용자 규모 R5 — 분리 이득 없음).

승인 모델: /authorize 는 **관리 키**(env VINC_OAUTH_ADMIN_KEY, 서버에서만 주입
— 번들·레포 반입 금지, D36 제약)를 아는 소유자만 통과하는 자동 승인이다.
동의 화면·다중 사용자 계정은 비범위(P5 계획 §3). 키 미설정 시 인가는 전면
거부되고 정적 토큰 모드만 남는다.

상태는 프로세스 메모리(코드 5분·토큰 기본 1시간 TTL) — 재기동 시 클라이언트가
재인가하면 되는 규모이고, 디스크에 자격을 남기지 않는 쪽이 안전하다.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse

CODE_TTL = 300
TOKEN_TTL = 3600


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthGate:
    def __init__(self, issuer: str, admin_key: str | None,
                 token_ttl: int = TOKEN_TTL):
        self.issuer = issuer.rstrip("/")
        self.admin_key = admin_key or None
        self.token_ttl = token_ttl
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, float] = {}     # access_token → 만료 epoch
        self.refresh: dict[str, str] = {}      # refresh_token → client_id

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
        self.clients[client_id] = {"redirect_uris": uris,
                                   "name": body.get("client_name") or ""}
        return {"client_id": client_id, "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]}

    # ── 인가(코드 + PKCE) ───────────────────────────────────────────────────
    def authorize(self, params: dict) -> str:
        """검증 통과 시 redirect 대상 URL 을 돌려준다(302 Location)."""
        if self.admin_key is None:
            raise PermissionError("authorization disabled: no admin key configured")
        if params.get("key") != self.admin_key:
            raise PermissionError("wrong or missing admin key")
        client = self.clients.get(params.get("client_id") or "")
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
        code = secrets.token_urlsafe(24)
        self.codes[code] = {"client_id": params["client_id"], "redirect_uri": redirect,
                            "challenge": challenge, "exp": time.time() + CODE_TTL}
        sep = "&" if "?" in redirect else "?"
        qs = {"code": code}
        if params.get("state"):
            qs["state"] = params["state"]
        return redirect + sep + urllib.parse.urlencode(qs)

    # ── 토큰 발급 ───────────────────────────────────────────────────────────
    def token(self, form: dict) -> dict:
        grant = form.get("grant_type")
        if grant == "authorization_code":
            rec = self.codes.pop(form.get("code") or "", None)
            if rec is None or rec["exp"] < time.time():
                raise ValueError("invalid or expired code")
            if form.get("client_id") != rec["client_id"]:
                raise ValueError("client_id mismatch")
            if form.get("redirect_uri") != rec["redirect_uri"]:
                raise ValueError("redirect_uri mismatch")
            verifier = form.get("code_verifier") or ""
            if _s256(verifier) != rec["challenge"]:
                raise ValueError("PKCE verification failed")
            return self._issue(rec["client_id"])
        if grant == "refresh_token":
            client_id = self.refresh.pop(form.get("refresh_token") or "", None)
            if client_id is None:
                raise ValueError("invalid refresh_token")
            return self._issue(client_id)
        raise ValueError(f"unsupported grant_type: {grant}")

    def _issue(self, client_id: str) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self.tokens[access] = time.time() + self.token_ttl
        self.refresh[refresh] = client_id
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": self.token_ttl, "refresh_token": refresh}

    # ── 자원 접근 검증 ──────────────────────────────────────────────────────
    def verify(self, token: str) -> bool:
        exp = self.tokens.get(token)
        if exp is None:
            return False
        if exp < time.time():
            self.tokens.pop(token, None)
            return False
        return True
