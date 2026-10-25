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
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__
from .store import REFRESH_REUSE_GRACE_S, Store

CODE_TTL = 300
TOKEN_TTL = 3600
CONNECT_TTL = 600      # 회원 승인 대기(B-B3) — 10분
#: 메타데이터가 내세우는 스코프. 토큰은 스코프로 갈리지 않고(요청의 `scope` 는 읽지 않는다)
#: 모든 발급에 갱신 토큰이 붙는다. 그래도 적는 이유: ChatGPT 는 `offline_access` 가
#: 안 보이면 갱신을 못 하는 서버로 보고 토큰 만료(TOKEN_TTL) 때 연결을 끊는다(VIN-269).
SCOPES = ["offline_access"]
_CIMD_TIMEOUT = 5
_CIMD_MAX_BYTES = 64 * 1024
#: CIMD 를 가져올 때 쓰는 이름. urllib 기본값(`Python-urllib/3.x`)은 claude.ai 앞단이
#: Cloudflare 1010 으로 막아 403 이 난다(2026-09-30 실측: 같은 주소가 이 값으로는 200).
_CIMD_UA = f"Vinc-MCP/{__version__} (+https://vincs.io)"


def _check_cimd_url(url: str) -> None:
    """CIMD `client_id` 의 모양만 본다(가져오기 전). https · 경로 있음 · 조각·userinfo 없음.

    MCP 2026-07-28 이 클라이언트에게 요구하는 모양이다. 경로 없는 주소(`https://host`)나
    조각이 붙은 주소는 문서의 `client_id` 와 글자 그대로 같을 수 없으므로 여기서 먼저 막는다.
    """
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("CIMD client_id must be an https URL")
    parts = urllib.parse.urlsplit(url)
    if parts.path in ("", "/") or parts.fragment or "@" in parts.netloc:
        raise ValueError("CIMD client_id must be an https URL with a path, "
                         "and no fragment or user info")


def fetch_cimd(url: str) -> dict:
    """CIMD 문서를 가져온다. https 만, 크기·시간 상한.

    ChatGPT 는 `client_id` 자리에 이 URL 을 넣고 DCR 을 건너뛴다
    (`client_id_metadata_document_supported`). 디렉터리 트래픽이
    연결마다 클라이언트를 늘리지 않게 하려는 자리다.
    문서 안의 `client_id` 대조는 여기가 아니라 `OAuthGate._client_of` 가 한다 —
    가져오는 함수를 갈아끼워도(테스트·캐시) 대조가 빠지지 않게.
    근거: [[vinc.spec.remote-profile]]
    """
    _check_cimd_url(url)
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json",
                                          "User-Agent": _CIMD_UA})
    try:
        with urllib.request.urlopen(req, timeout=_CIMD_TIMEOUT) as resp:
            raw = resp.read(_CIMD_MAX_BYTES + 1)
    except urllib.error.URLError as exc:
        raise ValueError(f"CIMD fetch failed: {exc}") from exc
    if len(raw) > _CIMD_MAX_BYTES:
        raise ValueError("CIMD document too large")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CIMD document is not JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("CIMD document must be an object")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise ValueError("CIMD redirect_uris (non-empty list) is required")
    return doc


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _redirect_registered(redirect: str, registered: list[str]) -> bool:
    """정확히 같거나, 루프백(http)이면 **포트만** 다른 것까지 받는다(RFC 8252 §7.3).

    네이티브 클라이언트는 요청 때 임의 포트를 연다. Claude Code 의 CIMD 는
    `http://localhost/callback` 을 적고 `http://localhost:<포트>/callback` 으로 온다.
    호스트·경로·쿼리·조각은 그대로 같아야 한다 — 루프백이 아닌 주소는 정확 일치만."""
    if redirect in registered:
        return True
    r = urllib.parse.urlsplit(redirect)
    if r.scheme != "http" or r.hostname not in _LOOPBACK_HOSTS:
        return False
    key = (r.scheme, r.hostname, r.path, r.query, r.fragment)
    return any((g.scheme, g.hostname, g.path, g.query, g.fragment) == key
               for g in map(urllib.parse.urlsplit, registered))


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthGate:
    def __init__(self, issuer: str, admin_key: str | None,
                 token_ttl: int = TOKEN_TTL, *, store: Store | None = None,
                 cimd_fetch=None):
        self.issuer = issuer.rstrip("/")
        self.admin_key = admin_key or None
        self.token_ttl = token_ttl
        self.store = store if store is not None else Store(":memory:")
        self._cimd_fetch = cimd_fetch or fetch_cimd

    # ── 메타데이터 ──────────────────────────────────────────────────────────
    def resource_metadata(self, resource_path: str = "/mcp") -> dict:
        """RFC 9728 보호 자원 메타데이터. **경로마다 다르다**(#120).

        전에는 `issuer + "/mcp"` 하드코딩이었다. 그래서 팀 주소(`/t/<uuid>/mcp`)에
        등록된 커넥터가 자기가 붙은 자원과 **안 맞는** `resource` 를 발견하게 되고,
        그것이 팀 주소에서 OAuth 를 아예 막아 둔 이유였다. 자원이 여럿이면 메타데이터도
        여럿이어야 한다 — 그것이 RFC 9728 이 자원별 문서를 두는 이유다.

        ⚠ `authorization_servers` 는 **안 갈린다.** 인가 서버는 하나고(RFC 8414 의
        issuer), 갈리는 것은 보호 자원 쪽이다. 그래서 `as_metadata()` 는 경로 인식이
        될 이유가 없다 — 그것을 경로마다 다르게 만들면 클라이언트가 팀마다 다른 인가
        서버를 발견하게 되고, 그건 사실이 아니다.
        """
        path = resource_path if resource_path.startswith("/") else "/" + resource_path
        return {"resource": self.issuer + path,
                "authorization_servers": [self.issuer],
                "scopes_supported": SCOPES}

    def as_metadata(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "registration_endpoint": self.issuer + "/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "scopes_supported": SCOPES,
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            # ChatGPT·Claude 디렉터리는 CIMD 를 우선하고 DCR 로 폴백한다.
            # 이 플래그가 없으면 연결마다 /register 가 클라이언트를 늘리고
            # MAX_CLIENTS 프루닝이 옛 등록을 밀어낸다.
            "client_id_metadata_document_supported": True,
            # RFC 9207. 이것을 참으로 적으면 클라이언트는 `iss` 없는 인가 응답을 **거절한다**.
            # 그래서 인가 응답을 만드는 자리는 `_redirect_with` 하나여야 한다.
            # 근거: [[record:hosted-mcp-oauth-support-before-vin-796-2026-10-25]]
            "authorization_response_iss_parameter_supported": True,
        }

    # ── 동적 등록(RFC 7591) ─────────────────────────────────────────────────
    def register(self, body: dict, *, rebind: bool = False) -> dict:
        """RFC 7591 등록. `rebind` 는 **관리 키 경로에서만 참**이고, 그때만 본문의
        `client_id` 를 그대로 쓴다(`add_client` 가 REPLACE 라 redirect 가 갈린다).

        왜 필요한가: 심사 플랫폼은 자기 redirect uri 를 **등록을 받은 뒤에** 통보한다
        (PlayMCP 는 메일로 주고 24시간 내 설정을 요구한다). 통보가 온 시점에 상대
        콘솔은 이미 심사중이라 client_id 를 못 바꾸므로, 먼저 발급된 client_id 에
        redirect 를 나중에 붙이는 길이 없으면 등록이 영영 성립하지 않는다.

        무인증 경로에 열지 않는 이유: 남의 client_id 를 자기 redirect 로 돌려놓는
        탈취가 된다. PKCE S256 이 필수라 코드를 바꿔 갈 수는 없지만, 그 커넥터를
        망가뜨리는 것만으로 충분히 고장이다."""
        uris = body.get("redirect_uris") or []
        if not isinstance(uris, list) or not all(isinstance(u, str) for u in uris) or not uris:
            raise ValueError("redirect_uris (non-empty list) is required")
        given = body.get("client_id")
        client_id = given if rebind and isinstance(given, str) and given else secrets.token_urlsafe(16)
        self.store.add_client(client_id, uris, body.get("client_name") or "")
        return {"client_id": client_id, "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]}

    # ── 인가 파라미터 검증(두 승인 모드 공통) ────────────────────────────────
    def _client_of(self, client_id: str) -> dict | None:
        """DCR 표 조회. 없으면 https `client_id` 를 CIMD 로 푼다.

        CIMD 클라이언트는 표에 안 남긴다 — 인가 때 문서를 다시 읽으면 되고,
        남기면 무인증 DCR 과 같은 표 증가가 다시 열린다.

        문서의 `client_id` 는 그 문서의 주소와 **글자 그대로** 같아야 한다(MCP 2026-07-28 의
        MUST). 다르면 남의 문서를 제 이름으로 내미는 것이라 거절한다. 정규화(끝 슬래시·대소문자)
        는 하지 않는다 — 스펙이 단순 문자열 비교를 요구한다.
        """
        rec = self.store.get_client(client_id)
        if rec is not None:
            return rec
        if client_id.startswith("https://"):
            _check_cimd_url(client_id)
            doc = self._cimd_fetch(client_id)
            if doc.get("client_id") != client_id:
                raise ValueError("CIMD document client_id does not match its URL")
            return {"redirect_uris": list(doc["redirect_uris"]),
                    "name": doc.get("client_name") or ""}
        return None

    def _validate_authorize(self, params: dict) -> tuple[str, str, str, dict]:
        """(client_id, redirect_uri, challenge, client) 반환 — 검증 실패 시 예외.

        client 를 함께 돌려주는 이유: 회원 경로가 이름을 얻으려고 `_client_of` 를 한 번 더
        부르면 CIMD 문서를 **두 번** 가져온다(가져오기 실패 확률도 두 배)."""
        client_id = params.get("client_id") or ""
        client = self._client_of(client_id)
        if client is None:
            raise ValueError("unknown client_id")
        redirect = params.get("redirect_uri") or ""
        if not _redirect_registered(redirect, client["redirect_uris"]):
            raise ValueError("redirect_uri not registered")
        if params.get("response_type") != "code":
            raise ValueError("response_type must be 'code'")
        challenge = params.get("code_challenge") or ""
        if not challenge or params.get("code_challenge_method") != "S256":
            raise ValueError("PKCE S256 code_challenge is required")
        return client_id, redirect, challenge, client

    @staticmethod
    def _resource_of(params: dict) -> str:
        """RFC 8707 `resource`. 없으면 빈 문자열(구 커넥터 하위호환)."""
        return (params.get("resource") or "").strip()

    # ── 인가: 관리 키 자동 승인(코드 + PKCE) ─────────────────────────────────
    def authorize(self, params: dict) -> str:
        """관리 키 경로. 검증 통과 시 redirect 대상 URL(302 Location)."""
        if self.admin_key is None:
            raise PermissionError("authorization disabled: no admin key configured")
        if params.get("key") != self.admin_key:
            raise PermissionError("wrong or missing admin key")
        client_id, redirect, challenge, _ = self._validate_authorize(params)
        code = secrets.token_urlsafe(24)
        # 관리 키 경로 = 단일 테넌트 하위호환 → owner/plan 미지정(None).
        self.store.add_code(code, client_id, redirect, challenge,
                            owner=None, plan=None, exp=time.time() + CODE_TTL,
                            resource=self._resource_of(params))
        return self._redirect_with(redirect, {"code": code}, params.get("state"))

    # ── 인가: 회원 승인 대기 등록(B-B3) ──────────────────────────────────────
    def begin_connect(self, params: dict) -> tuple[str, dict]:
        """검증 후 connect_request 를 저장하고 (req_id, client_meta) 반환.

        http_server 가 이를 CRA 로 서명해 공홈 /connect 로 리다이렉트한다.
        승인은 계정 서비스가 하고, complete() 로 코드가 발급된다.
        """
        client_id, redirect, challenge, client = self._validate_authorize(params)
        req_id = secrets.token_urlsafe(18)
        self.store.add_connect_request(req_id, client_id, redirect, challenge,
                                       params.get("state"), exp=time.time() + CONNECT_TTL,
                                       resource=self._resource_of(params))
        return req_id, {"client_id": client_id, "client_name": client.get("name", ""),
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
                            owner=owner, plan=plan, exp=time.time() + CODE_TTL,
                            resource=rec.get("resource") or "")
        self.store.set_owner_plan(owner, plan)
        return self._redirect_with(rec["redirect_uri"], {"code": code}, rec.get("state"))

    def _redirect_with(self, redirect: str, qs: dict, state: str | None) -> str:
        """인가 응답(클라이언트 redirect)을 만드는 **유일한** 자리.

        `iss`(RFC 9207)는 메타데이터의 `issuer` 와 글자 그대로 같은 값이다. 클라이언트는
        정규화 없이 비교하므로 여기서도 `self.issuer` 를 그대로 싣는다. 메타데이터가
        `authorization_response_iss_parameter_supported` 를 참으로 적고 있어서, 이 함수를
        거치지 않는 인가 응답이 생기면 그 응답은 클라이언트에서 거절된다."""
        if state:
            qs = {**qs, "state": state}
        qs = {**qs, "iss": self.issuer}
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
            resource = self._bound_resource(form, rec)
            return self._issue(rec["client_id"], rec.get("owner"), rec.get("plan"),
                               resource=resource)
        if grant == "refresh_token":
            return self._refresh(form)
        raise ValueError(f"unsupported grant_type: {grant}")

    def _refresh(self, form: dict) -> dict:
        """갱신 토큰 회전. 검증을 **소비 전에** 하고, 회전은 스토어의 트랜잭션 하나로 한다.

        같은 갱신 토큰이 `REFRESH_REUSE_GRACE_S` 안에 다시 오면 방금 발급한 쌍을 그대로
        돌려준다(새 쌍을 만들지 않으므로 갈래가 생기지 않는다). 여러 작업자가 토큰 만료 뒤
        동시에 갱신하는 호스트가 둘째 요청의 `invalid_grant` 를 보고 자격을 버리던 자리다.
        근거: [[record:a-rotated-refresh-token-answers-again-within-sixty-seconds-2026-10-25]]
        """
        presented = form.get("refresh_token") or ""
        rec = self.store.get_refresh(presented)
        if rec is None:
            raise ValueError("invalid refresh_token")
        resource = self._bound_resource(form, rec)   # 불일치면 여기서 끝 — 토큰은 그대로 산다
        now = time.time()
        out = self.store.rotate_refresh(presented, access=secrets.token_urlsafe(32),
                                        refresh=secrets.token_urlsafe(32),
                                        exp=now + self.token_ttl, resource=resource, now=now)
        if out is None:
            raise ValueError("invalid refresh_token")
        if out["replayed"]:
            # 값이 아니라 사건만 남긴다. 호스트가 실제로 동시 갱신을 하는지 이 줄로 본다.
            sys.stdout.write("[oauth] refresh token reused within %ds: answered with the "
                             "pair it was rotated to\n" % REFRESH_REUSE_GRACE_S)
        return self._token_body(out["access_token"], out["refresh_token"],
                                max(1, int(out["exp"] - now)), out["resource"])

    def _bound_resource(self, form: dict, rec: dict) -> str:
        """인가·토큰 요청의 `resource` 를 코드에 묶인 값과 맞춘다.

        ChatGPT 는 두 요청 모두에 `resource=` 를 붙이고, 그 값이 토큰
        audience 로 돌아와야 한다. 구 커넥터는 이 파라미터를 안 보내므로
        빈 값은 하위호환으로 통과한다.
        """
        requested = self._resource_of(form)
        stored = (rec.get("resource") or "").strip()
        if requested and stored and requested != stored:
            raise ValueError("resource mismatch")
        return stored or requested

    def _issue(self, client_id: str, owner: str | None = None,
               plan: str | None = None, resource: str = "") -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self.store.add_token(access, client_id, owner, plan, time.time() + self.token_ttl,
                             resource=resource)
        self.store.add_refresh(refresh, client_id, owner, plan, resource=resource)
        return self._token_body(access, refresh, self.token_ttl, resource)

    @staticmethod
    def _token_body(access: str, refresh: str, expires_in: int, resource: str) -> dict:
        out = {"access_token": access, "token_type": "Bearer",
               "expires_in": expires_in, "refresh_token": refresh}
        if resource:
            out["resource"] = resource
        return out

    # ── 자원 접근 검증 ──────────────────────────────────────────────────────
    def verify(self, token: str) -> bool:
        return self.store.get_token(token) is not None

    def resolve(self, token: str) -> dict | None:
        """유효 토큰이면 {client_id, owner, plan, exp}, 아니면 None(owner 스탬핑용)."""
        return self.store.get_token(token)
