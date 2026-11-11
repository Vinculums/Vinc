# -*- coding: utf-8 -*-
"""셀프호스트 허브의 같은 출처 평면 — 한 사람이 로그인하고 웹 앱이 부르는 계정 모양의 라우트.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S2. `selfhost.enabled()` 일 때만 답하고,
꺼져 있으면 `handle` 이 False 를 돌려 기존 404 가 그대로 난다.

라우트:
  GET  /login              로그인 화면(스크립트 없음)
  POST /login              비밀번호 → 세션·CSRF 쿠키, 303 /app/ (JSON 이면 200 JSON)
  POST /logout             세션 지움(CSRF 필요)
  GET  /api/me             {user: {..., self_hosted: true}, self_hosted: true}
  GET  /api/teams          []
  GET  /api/graph/spaces   {teams: [], communities: []}
  GET|POST /api/graph/...  허브 `_PROXY_OPS` 의 같은 경로 op 를 고정 소유자로 보낸다

지키는 것:
- 소유자는 `selfhost.owner()` 하나다. 본문·쿼리의 owner 는 `proxy_op` 가 벗긴다.
- 상태를 바꾸는 요청은 Origin 이 이 호스트여야 하고, 로그인 뒤에는 CSRF 더블서브밋
  (헤더 = 쿠키 = 세션에 묶인 값)이어야 한다.
- 평문 HTTP 는 localhost 에서만 받는다. 그때 쿠키는 `__Host-` 접두 없이, Secure 없이 준다
  (브라우저가 평문에서 `__Host-` 를 받지 않는다). 그 밖의 평문 호스트는 로그인을 거절한다.
- 비밀번호 해시는 scrypt 이고, 실패한 로그인은 주소마다·전체로 분당 횟수를 막는다.
- 세션 id 는 32바이트 난수이고 파일에는 그 SHA-256 만 남는다. 만료는 서버가 정한다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
import urllib.parse
from collections import deque
from http.cookies import CookieError, SimpleCookie

from store.envflag import truthy

from . import selfhost

# ── 비밀번호 ────────────────────────────────────────────────────────────────

#: scrypt 비용. 메모리 128·N·r = 32 MiB, 이 노트북 기준 한 번에 0.1초 안팎이다.
SCRYPT_N, SCRYPT_R, SCRYPT_P, DKLEN = 2 ** 15, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024
HASH_ENV = "VINC_LOCAL_PASSWORD_HASH"
HASH_FILE = "local-password"


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """`scrypt$N$r$p$salt$hash`(base64url). 빈 비밀번호는 만들지 않는다."""
    if not password:
        raise ValueError("the password is empty")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM,
                        dklen=DKLEN)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")   # noqa: E731
    return f"scrypt${n}${r}${p}${enc(salt)}${enc(dk)}"


def verify_password(password: str, encoded: str) -> bool:
    """상수 시간 비교. 모양이 틀린 해시는 거짓이다(예외를 내지 않는다)."""
    try:
        kind, n, r, p, salt, want = encoded.strip().split("$")
        if kind != "scrypt":
            return False
        dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))   # noqa: E731
        want_b = dec(want)
        got = hashlib.scrypt(password.encode("utf-8"), salt=dec(salt), n=int(n), r=int(r),
                             p=int(p), maxmem=_MAXMEM, dklen=len(want_b))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want_b)


def state_dir() -> str:
    """세션 파일과 비밀번호 파일이 사는 곳: `VINC_SELFHOST_STATE`, 없으면 OAuth 스냅샷 옆
    (컨테이너에서는 영속 볼륨), 그것도 없으면 OAuth 작업본 경로."""
    explicit = os.environ.get("VINC_SELFHOST_STATE")
    if explicit:
        root = explicit
    elif os.environ.get("VINC_MCP_STATE_SNAPSHOT"):
        root = os.path.dirname(os.environ["VINC_MCP_STATE_SNAPSHOT"])
    else:
        from .store import resolve_state_dir
        root = resolve_state_dir()
    os.makedirs(root, exist_ok=True)
    return root


def password_hash() -> str | None:
    """설치 때 정한 해시: 환경 변수가 먼저, 없으면 상태 경로의 `local-password` 파일."""
    value = (os.environ.get(HASH_ENV) or "").strip()
    if value:
        return value
    try:
        with open(os.path.join(state_dir(), HASH_FILE), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


# ── 실패한 로그인 상한 ────────────────────────────────────────────────────

#: 주소마다 분당 5번, 허브 전체로 분당 20번 틀리면 그 창이 지날 때까지 로그인을 막는다.
FAIL_PER_ADDR, FAIL_TOTAL, FAIL_WINDOW_S = 5, 20, 60.0


class FailureLimit:
    def __init__(self, per_key: int = FAIL_PER_ADDR, total: int = FAIL_TOTAL,
                 window_s: float = FAIL_WINDOW_S):
        self.per_key, self.total, self.window_s = per_key, total, window_s
        self._by_key: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def _trim(self, q: deque, now: float) -> None:
        while q and now - q[0] >= self.window_s:
            q.popleft()

    def wait(self, key: str, now: float | None = None) -> float:
        """막혀 있으면 다시 시도할 수 있을 때까지의 초, 아니면 0."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._by_key.get(key, deque())
            self._trim(q, now)
            self._trim(self._all, now)
            waits = []
            if len(q) >= self.per_key:
                waits.append(self.window_s - (now - q[0]))
            if len(self._all) >= self.total:
                waits.append(self.window_s - (now - self._all[0]))
            return max(waits) if waits else 0.0

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._by_key.setdefault(key, deque()).append(now)
            self._all.append(now)
            if len(self._by_key) > 10_000:   # 오래 조용한 주소를 버린다
                for k in [k for k, q in self._by_key.items() if not q or now - q[-1] >= self.window_s]:
                    self._by_key.pop(k, None)


# ── 세션 ────────────────────────────────────────────────────────────────────

#: 계정과 같다: 30일 동안 안 쓰면 끝나고, 써도 90일이면 끝난다.
IDLE_S, ABSOLUTE_S = 30 * 24 * 3600, 90 * 24 * 3600
#: 마지막 사용 시각은 이 간격보다 자주 디스크에 쓰지 않는다.
_TOUCH_S = 3600
SESSIONS_FILE = "selfhost-sessions.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


class SessionStore:
    """작은 JSON 파일 하나. 사용자가 한 명이라 세션도 몇 개뿐이다. 쓰기는 임시 파일 교체."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def create(self, now: float | None = None) -> tuple[str, str]:
        """새 세션 → (세션 토큰, CSRF 토큰). 끝난 세션은 이때 같이 걷는다."""
        now = time.time() if now is None else now
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            data = {k: v for k, v in self._load().items() if self._alive(v, now)}
            data[_digest(token)] = {"created": now, "seen": now, "csrf": csrf}
            self._save(data)
        return token, csrf

    @staticmethod
    def _alive(rec: dict, now: float) -> bool:
        try:
            return (now - float(rec["seen"]) < IDLE_S) and (now - float(rec["created"]) < ABSOLUTE_S)
        except (KeyError, TypeError, ValueError):
            return False

    def get(self, token: str | None, now: float | None = None) -> dict | None:
        """살아 있는 세션의 기록(`csrf` 포함), 없거나 끝났으면 None."""
        if not token:
            return None
        now = time.time() if now is None else now
        key = _digest(token)
        with self._lock:
            data = self._load()
            rec = data.get(key)
            if not isinstance(rec, dict) or not self._alive(rec, now):
                if key in data:
                    data.pop(key)
                    self._save(data)
                return None
            if now - float(rec["seen"]) >= _TOUCH_S:
                rec["seen"] = now
                self._save(data)
            return dict(rec)

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._load()
            if data.pop(_digest(token), None) is not None:
                self._save(data)


# ── 요청 판정 ─────────────────────────────────────────────────────────────

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


def _host(handler) -> str:
    return (handler.headers.get("Host") or "").strip().lower()


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_https(handler) -> bool:
    """허브는 TLS 를 직접 끝내지 않는다. 앞단 프록시가 끝냈다는 표시는 `VINC_TRUST_PROXY` 가
    켜졌을 때만 믿는다(아니면 누구나 헤더 하나로 https 인 척한다)."""
    if not truthy(os.environ.get("VINC_TRUST_PROXY")):
        return False
    return (handler.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower() == "https"


def transport(handler) -> str | None:
    """"https", "localhost"(평문이지만 이 기계), 아니면 None(로그인을 받지 않는다)."""
    if is_https(handler):
        return "https"
    if _hostname(_host(handler)) in _LOCAL_HOSTS:
        return "localhost"
    return None


def cookie_names(kind: str) -> tuple[str, str]:
    return ("__Host-session", "__Host-csrf") if kind == "https" else ("vinc-session", "vinc-csrf")


def _cookies(handler) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie") or "")
    except CookieError:
        return {}
    return {k: m.value for k, m in jar.items()}


def _origin_ok(handler, kind: str) -> bool:
    """상태를 바꾸는 요청은 이 호스트에서 왔어야 한다. Origin 이 없으면 Sec-Fetch-Site 로 본다."""
    origin = handler.headers.get("Origin")
    want = f"{'https' if kind == 'https' else 'http'}://{_host(handler)}"
    if origin:
        return hmac.compare_digest(origin.strip().lower().rstrip("/"), want)
    return (handler.headers.get("Sec-Fetch-Site") or "").lower() == "same-origin"


# ── 응답 ────────────────────────────────────────────────────────────────────

def _send(handler, code: int, body: bytes, ctype: str, *, headers: dict | None = None,
          cookies: list[str] | None = None) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "same-origin")
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    for c in cookies or ():
        handler.send_header("Set-Cookie", c)
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler, code: int, obj, **kw) -> None:
    _send(handler, code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
          "application/json", **kw)


def _set_cookies(kind: str, token: str, csrf: str, max_age: int = IDLE_S) -> list[str]:
    sess, cs = cookie_names(kind)
    secure = "; Secure" if kind == "https" else ""
    tail = f"; Path=/; SameSite=Lax; Max-Age={max_age}{secure}"
    return [f"{sess}={token}{tail}; HttpOnly", f"{cs}={csrf}{tail}"]


def _clear_cookies(kind: str) -> list[str]:
    return _set_cookies(kind, "", "", max_age=0)


_LOGIN_CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"


def _login_page(handler, *, message: str = "", code: int = 200) -> None:
    note = f'<p class="note">{html.escape(message)}</p>' if message else ""
    form = ("" if message.startswith("Sign-in needs HTTPS") else
            '<form method="post" action="/login"><label for="pw">Password</label>'
            '<input id="pw" name="password" type="password" autocomplete="current-password" '
            'required autofocus><button type="submit">Sign in</button></form>')
    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Sign in to Vinc</title><style>"
            "body{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;"
            "min-height:100vh;background:#f7f7f5;color:#1d1d1b}"
            "main{width:min(20rem,calc(100% - 2rem))}h1{font-size:1.25rem}"
            "label{display:block;margin:.75rem 0 .25rem}input{width:100%;box-sizing:border-box;"
            "padding:.5rem;font:inherit}button{margin-top:1rem;padding:.5rem 1rem;font:inherit}"
            ".note{color:#a12b2b}@media(prefers-color-scheme:dark){body{background:#1b1b1a;"
            "color:#eee}}</style></head><body><main><h1>Sign in to Vinc</h1>"
            f"{note}{form}</main></body></html>")
    _send(handler, code, page.encode("utf-8"), "text/html; charset=utf-8",
          headers={"Content-Security-Policy": _LOGIN_CSP, "X-Frame-Options": "DENY"})


# ── 라우팅 ──────────────────────────────────────────────────────────────────

_PATHS = ("/login", "/logout", "/api/me", "/api/teams")


def _owns(path: str) -> bool:
    return path in _PATHS or path == "/api/graph" or path.startswith("/api/graph/")


def _web_ops() -> dict[tuple[str, str], str]:
    """(방법, 경로) → op. 허브 `_PROXY_OPS` 하나에서 만든다: 웹 경로는 사이드카 경로와 같고,
    커뮤니티 op 는 셀프호스트에 없다. 계정의 GET_OPS·POST_OPS 와 같은 표면이다."""
    from .http_server import _PROXY_OPS
    return {(m, p): op for op, (m, p) in _PROXY_OPS.items()
            if p and not op.startswith("community-")}


class _State:
    """허브 앱마다 하나: 세션 저장소와 실패 상한. 경로는 처음 쓸 때 정한다."""
    _lock = threading.Lock()

    @classmethod
    def of(cls, app) -> "_State":
        with cls._lock:
            st = getattr(app, "_selfhost_web", None)
            if st is None:
                st = cls()
                st.sessions = SessionStore(os.path.join(state_dir(), SESSIONS_FILE))
                st.failures = FailureLimit()
                app._selfhost_web = st
            return st


def handle(handler, method: str) -> bool:
    """이 평면의 경로면 답하고 True. 셀프호스트가 아니거나 다른 경로면 False(기존 흐름)."""
    if not selfhost.enabled():
        return False
    url = urllib.parse.urlparse(handler.path)
    if not _owns(url.path):
        return False
    st = _State.of(handler.app)
    kind = transport(handler)
    if url.path == "/login":
        return _login(handler, method, st, kind) or True
    if kind is None:
        _json(handler, 403, {"error": "https_required",
                             "note": "This hub accepts plain HTTP only on localhost."})
        return True
    sess_name, csrf_name = cookie_names(kind)
    jar = _cookies(handler)
    token = jar.get(sess_name)
    rec = st.sessions.get(token)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
    if url.path == "/api/me" and method == "GET" and rec is None and query.get("optional") == "1":
        _json(handler, 200, {"user": None})
        return True
    if rec is None:
        _json(handler, 401, {"error": "unauthorized"})
        return True
    if method not in ("GET", "HEAD"):
        header = handler.headers.get("X-CSRF-Token") or ""
        cookie = jar.get(csrf_name) or ""
        if not _origin_ok(handler, kind):
            _json(handler, 403, {"error": "origin_mismatch"})
            return True
        if not (header and hmac.compare_digest(header, cookie)
                and hmac.compare_digest(header, rec.get("csrf") or "")):
            _json(handler, 403, {"error": "csrf"})
            return True
    if url.path == "/logout":
        if method != "POST":
            _json(handler, 405, {"error": "method_not_allowed"})
            return True
        st.sessions.delete(token)
        _json(handler, 200, {"ok": True}, cookies=_clear_cookies(kind))
        return True
    if url.path == "/api/me" and method == "GET":
        name = (os.environ.get("VINC_LOCAL_USER_NAME") or "local").strip() or "local"
        _json(handler, 200, {"self_hosted": True, "user": {
            "email": None, "username": name, "name": name, "plan": "self_hosted",
            "teams": [], "self_hosted": True, "owner": selfhost.owner()}})
        return True
    if url.path == "/api/teams" and method == "GET":
        _json(handler, 200, [])
        return True
    if url.path.startswith("/api/graph"):
        return _graph(handler, method, url.path[len("/api/graph"):] or "/", query) or True
    _json(handler, 404, {"error": f"no such route: {method} {url.path}"})
    return True


def _login(handler, method: str, st: _State, kind: str | None) -> None:
    wants_json = "application/json" in (handler.headers.get("Accept") or "") \
        or (handler.headers.get("Content-Type") or "").startswith("application/json")
    if kind is None:
        msg = "Sign-in needs HTTPS. Open this hub through its https:// address, or on localhost."
        if method == "GET" and not wants_json:
            return _login_page(handler, message=msg, code=403)
        return _json(handler, 403, {"error": "https_required", "note": msg})
    if method == "GET":
        return _login_page(handler, message="Wrong password." if "error=1" in handler.path else "")
    if method != "POST":
        return _json(handler, 405, {"error": "method_not_allowed"})
    if not _origin_ok(handler, kind):
        return _json(handler, 403, {"error": "origin_mismatch"})
    addr = str((handler.client_address or ("",))[0])
    wait = st.failures.wait(addr)
    if wait > 0:
        return _json(handler, 429, {"error": "too_many_attempts"},
                     headers={"Retry-After": str(int(wait) + 1)})
    body = handler._body()
    password = str(body.get("password") or "")
    encoded = password_hash()
    if encoded is None:
        return _json(handler, 503, {"error": "no_password_set",
                                    "note": f"Set {HASH_ENV} (vinc selfhost hash-password)."})
    if not password or not verify_password(password, encoded):
        st.failures.fail(addr)
        if wants_json:
            return _json(handler, 401, {"error": "wrong_password"})
        return _send(handler, 303, b"", "text/plain", headers={"Location": "/login?error=1"})
    token, csrf = st.sessions.create()
    cookies = _set_cookies(kind, token, csrf)
    if wants_json:
        return _json(handler, 200, {"ok": True}, cookies=cookies)
    return _send(handler, 303, b"", "text/plain", headers={"Location": "/app/"}, cookies=cookies)


def _graph(handler, method: str, sub: str, query: dict) -> None:
    from . import selfhost as sh
    if query.get("team"):
        return _json(handler, 404, {"error": "space_not_found", "note": sh.NO_SPACES})
    if sub == "/spaces" and method == "GET":
        return _json(handler, 200, {"teams": [], "communities": []})
    query = {k: v for k, v in query.items() if k not in ("owner", "team")}
    if sub.startswith("/jobs/") and method == "GET":
        op, args = "job", {"id": sub[len("/jobs/"):]}
    elif sub == "/runs/answer" and method == "POST":
        op, args = "run-answer", handler._body()
    else:
        op = _web_ops().get((method, sub))
        if op is None:
            return _json(handler, 404, {"error": f"no such route: {method} /api/graph{sub}"})
        args = query if method == "GET" else handler._body()
    try:
        out = handler.app.proxy_op(op, args, sh.owner(), actor=sh.owner(), person_session=True)
    except ValueError as e:
        return _json(handler, 400, {"error": str(e)})
    except Exception as e:   # noqa: BLE001 — 사이드카 도달 불가는 502 로 답한다
        return _json(handler, 502, {"error": f"sidecar unreachable: {type(e).__name__}"})
    return _json(handler, 200, out)
