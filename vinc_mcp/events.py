# -*- coding: utf-8 -*-
"""MCP Events 확장(초안 고정판)과 서명 웹훅 전달. 표준 라이브러리만 쓴다.

- 이벤트 계산은 사이드카 `GET /events`(api/events.py)가 한다. 여기는 그 피드를 MCP 메서드
  (`events/list` · `events/poll` · `events/subscribe` · `events/unsubscribe`)로 내고, 구독마다
  Standard Webhooks 로 서명한 POST 를 보낸다.
- 웹훅은 `VINC_EVENTS_WEBHOOK` 이 켜진 프로세스에서만 열린다. 서명 비밀은 이 프로세스
  메모리에만 있고 디스크·OAuth 저장소·그래프 어디에도 쓰지 않는다. 그래서 허가 기간은
  한 시간을 넘지 않고, 재시작 뒤에는 클라이언트의 갱신이 구독과 비밀을 되살린다.
- 콜백 주소는 구독 때와 보낼 때마다 다시 풀어 공인 주소인지 보고, 확인한 그 주소로
  붙는다(TLS 이름은 원래 호스트). 리다이렉트는 따라가지 않는다.

계약 정본(종류·커서·전달·보안·테넌시): [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import base64
import collections
import datetime
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from store.envflag import truthy

from .netguard import NotPublic, Unresolved, public_address, public_ips  # noqa: F401 — public_address 는 이 이름으로도 들인다
from .netguard import tls_connect as _tls_connect

#: 따르는 초안의 고정점. 두 문서가 어긋나는 곳은 계약 정본 §1 이 정한다.
EVENTS_SPEC = ("modelcontextprotocol/experimental-ext-triggers-events "
               "docs/design-sketch-proposal.md (2026-02-19, SEP-3415 codes) "
               "+ developers.openai.com/plugins/build/mcp-events")
EXTENSION = "io.modelcontextprotocol/events"
#: api/events.py 의 KINDS 와 같아야 한다(테스트가 대조한다). vinc_mcp 는 api 를 못 들인다.
KINDS = ("role.reading_changed", "run.citation_superseded", "role.lesson_added",
         "run.waiting", "run.answered")
RESERVED: tuple[str, ...] = ()

INVALID_PARAMS = -32602
NOT_FOUND = -32023
FORBIDDEN = -32024
RESOURCE_EXHAUSTED = -32025
UNSUPPORTED = -32026
CALLBACK_ERROR = -32027
#: `lastError` 와 `data.reason` 에 쓰는 범주. 받는 쪽 본문·헤더는 절대 싣지 않는다.
CATEGORIES = ("connection_refused", "timeout", "tls_error", "http_4xx", "http_5xx",
              "challenge_failed")

TTL_MIN_MS = 300_000
TTL_MAX_MS = 3_600_000
MAX_BODY = 256 * 1024
READ_CAP = 64 * 1024
TIMEOUT_S = 5.0
#: 재시도 간격(초). 첫 시도를 더해 다섯 번이고 그 뒤에는 버린다.
RETRY_DELAYS = (5, 30, 120, 300)
ROTATION_GRACE_S = 300
VERIFY_TTL_S = 86_400
CHALLENGES_PER_MIN = 6
LEDGER_CAP = 2000
POLL_MS = 30_000
POSTS_PER_TICK = 50
PAGE_LIMIT = 200
MAX_SUBS_TOTAL = 1000
_URL_MAX = 2048

_ROLE_RE = re.compile(r"(?:concept:)?agent-[A-Za-z0-9._-]{1,120}")
_SPACE_RE = re.compile(r"[0-9A-Za-z:-]{1,80}")
_CURSOR_RE = re.compile(r"0|\d{1,18}\.\d{1,18}")


def webhook_enabled() -> bool:
    """웹훅 전달 스위치. 기본은 꺼짐이고 매번 env 를 읽는다."""
    return truthy(os.environ.get("VINC_EVENTS_WEBHOOK"))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


class EventsError(Exception):
    """JSON-RPC 오류 하나. `reply()` 가 봉투를 만든다."""

    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def reply(self, msg_id) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return {"jsonrpc": "2.0", "id": msg_id, "error": err}


class CallbackFailure(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


# ── 이벤트 정의(events/list) ─────────────────────────────────────────────────

_ROLE_ARG = {"type": "string", "description":
             "The role to watch: an agent slug (agent-maker) or its concept:agent- id."}
_SPACE_ARG = {"type": "string", "description":
              "A team id to watch that team's space; omit for your personal graph."}
INPUT_SCHEMA = {"type": "object", "properties": {"role": _ROLE_ARG, "space": _SPACE_ARG},
                "required": ["role"], "additionalProperties": False}
_STR = {"type": "string"}
_DESCRIPTIONS = {
    "role.reading_changed": (
        "A document the role must read (listed in its reads) was edited, retired or deleted, so "
        "anything built from that role now reads something new. Fetch it with vinc_doc_get.",
        {"role": _STR, "doc_id": _STR, "title": _STR,
         "change": {"type": "string", "enum": ["updated", "retired", "deleted"]},
         "content_hash": _STR, "valid_until": _STR, "superseded_by": _STR}),
    "run.citation_superseded": (
        "A claim that one of the role's recorded runs cited (its cited_ids) was retired: it now "
        "has valid_until or a successor, so that run's output may rest on a retired claim.",
        {"run": _STR, "role": _STR, "claim": _STR, "valid_until": _STR, "superseded_by": _STR}),
    "role.lesson_added": (
        "A Record or Decision was attached to or about the role: a lesson, a rule or a shim. "
        "An edit to the same lesson repeats its eventId; drop repeats by eventId.",
        {"role": _STR, "lesson": _STR,
         "lesson_kind": {"type": "string", "enum": ["record", "decision"]}, "title": _STR}),
    "run.waiting": (
        "One of the role's runs stopped for a person's decision. The data carries no action text: "
        "read the run with vinc_runs to see the question, and expires_at says when it lapses.",
        {"run": _STR, "role": _STR, "space": _STR, "title": _STR, "ask": {"type": "integer"},
         "asked_at": _STR, "expires_at": _STR}),
    "run.answered": (
        "A run that was waiting for a person was answered: approve, decline or a reply. via is "
        "person for a signed-in person in the Vinc app, agent for vinc_run_submit. Call "
        "vinc_run_submit or vinc_run_start with the run id to continue it, or read the ended run "
        "with vinc_runs.",
        {"run": _STR, "role": _STR,
         "answer": {"type": "string", "enum": ["approve", "decline", "reply"]},
         "answered_at": _STR, "actions": {"type": "array", "items": _STR},
         "via": {"type": "string", "enum": ["person", "agent"]}, "space": _STR, "title": _STR}),
}


def definitions(*, webhook: bool) -> list[dict]:
    """`events/list` 의 항목. 웹훅이 꺼져 있으면 poll 만 광고한다."""
    delivery = ["poll", "webhook"] if webhook else ["poll"]
    out = []
    for name in KINDS:
        desc, props = _DESCRIPTIONS[name]
        out.append({"name": name, "description": desc, "delivery": list(delivery),
                    "inputSchema": json.loads(json.dumps(INPUT_SCHEMA)),
                    "payloadSchema": {"type": "object", "properties": dict(props),
                                      "required": sorted(props)}})
    return out


# ── 인자 검증 ─────────────────────────────────────────────────────────────────

def check_name(name) -> str:
    if name in KINDS:
        return name
    if name in RESERVED:
        raise EventsError(UNSUPPORTED, f"event {name!r} is reserved and not emitted yet",
                          {"feature": "event", "value": name})
    raise EventsError(NOT_FOUND, f"unknown event {name!r}", {"kind": "event"})


def check_arguments(arguments) -> dict:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise EventsError(INVALID_PARAMS, "arguments must be an object")
    extra = set(arguments) - {"role", "space"}
    if extra:
        raise EventsError(INVALID_PARAMS, "unknown argument: " + ", ".join(sorted(extra)))
    role = arguments.get("role")
    if not isinstance(role, str) or not _ROLE_RE.fullmatch(role):
        raise EventsError(INVALID_PARAMS, "arguments.role must be an agent slug or concept:agent- id")
    space = arguments.get("space")
    if space is not None and (not isinstance(space, str) or not _SPACE_RE.fullmatch(space)):
        raise EventsError(INVALID_PARAMS, "arguments.space must be a team id")
    return dict(arguments)


def role_id(role: str) -> str:
    return role if role.startswith("concept:") else "concept:" + role


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_cursor(value) -> tuple[int, int] | None:
    """api/events.py 와 같은 표기. `None` 은 지금부터."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _CURSOR_RE.fullmatch(value):
        raise EventsError(INVALID_PARAMS, "cursor must be one returned by Vinc, or null")
    if value == "0":
        return 0, 0
    n, d = value.split(".")
    return int(n), int(d)


def format_cursor(pos: tuple[int, int]) -> str:
    return f"{pos[0]}.{pos[1]}"


def decode_secret(value) -> bytes:
    """`whsec_` + base64(24..64 바이트). 그 밖은 InvalidParams."""
    if not isinstance(value, str) or not value.startswith("whsec_"):
        raise EventsError(INVALID_PARAMS, "delivery.secret must be a whsec_ value")
    try:
        raw = base64.b64decode(value[6:], validate=True)
    except (ValueError, TypeError):
        raise EventsError(INVALID_PARAMS, "delivery.secret is not valid base64") from None
    if not 24 <= len(raw) <= 64:
        raise EventsError(INVALID_PARAMS, "delivery.secret must decode to 24..64 bytes")
    return raw


def grant_ttl_ms(params: dict) -> int:
    """요청 `ttlMs` → 허가. 없거나 null 이면 최대치(무기한은 주지 않는다)."""
    if "ttlMs" not in params or params["ttlMs"] is None:
        return TTL_MAX_MS
    ttl = params["ttlMs"]
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise EventsError(INVALID_PARAMS, "ttlMs must be an integer or null")
    return max(TTL_MIN_MS, min(TTL_MAX_MS, ttl))


# ── 서명(Standard Webhooks v1) ────────────────────────────────────────────────

def sign(secret: bytes, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret, f"{msg_id}.{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return "v1," + base64.b64encode(mac.digest()).decode("ascii")


def signature_header(keys: list[bytes], msg_id: str, timestamp: int, body: bytes) -> str:
    return " ".join(sign(k, msg_id, timestamp, body) for k in keys)


def signed_headers(keys: list[bytes], msg_id: str, sub_id: str, body: bytes,
                   now: float) -> dict:
    ts = int(now)
    return {"Content-Type": "application/json", "webhook-id": msg_id,
            "webhook-timestamp": str(ts),
            "webhook-signature": signature_header(keys, msg_id, ts, body),
            "X-MCP-Subscription-Id": sub_id, "User-Agent": "vinc-events/1"}


# ── 콜백 주소와 보내기 ────────────────────────────────────────────────────────

def check_url(url) -> tuple[str, int, str]:
    """`(host, port, target)`. https · 443 · 사용자 정보와 조각 없음. 네트워크는 안 쓴다."""
    if not isinstance(url, str) or not url or len(url) > _URL_MAX:
        raise EventsError(INVALID_PARAMS, "delivery.url must be an https URL")
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise EventsError(INVALID_PARAMS, "delivery.url has an invalid port") from None
    if parts.scheme != "https" or not parts.hostname:
        raise EventsError(INVALID_PARAMS, "delivery.url must be an https URL")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise EventsError(INVALID_PARAMS, "delivery.url must not carry user info or a fragment")
    if port not in (None, 443):
        raise EventsError(INVALID_PARAMS, "delivery.url must use port 443")
    if any(c.isspace() for c in url):
        raise EventsError(INVALID_PARAMS, "delivery.url must not contain spaces")
    target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    return parts.hostname, 443, target


def resolve_public(host: str, port: int, resolver=socket.getaddrinfo) -> str:
    """주소 하나. 풀린 주소 가운데 하나라도 공인이 아니면 거절한다(풀리지 않은 것과 같은 답)."""
    try:
        return public_ips(host, port, resolver)[0]
    except (Unresolved, NotPublic):
        raise CallbackFailure("connection_refused") from None


class Sender:
    """서명된 POST 한 번. 확인한 주소로 붙고, 리다이렉트를 따르지 않고, 응답은 일부만 읽는다."""

    def __init__(self, *, resolver=socket.getaddrinfo, connector=_tls_connect,
                 timeout: float = TIMEOUT_S):
        self.resolver, self.connector, self.timeout = resolver, connector, timeout

    def post(self, url: str, headers: dict, body: bytes) -> tuple[int, bytes]:
        host, port, target = check_url(url)
        ip = resolve_public(host, port, self.resolver)
        try:
            sock = self.connector(ip, port, host, self.timeout)
        except socket.timeout:
            raise CallbackFailure("timeout") from None
        except (ssl.SSLError, ssl.CertificateError):
            raise CallbackFailure("tls_error") from None
        except OSError:
            raise CallbackFailure("connection_refused") from None
        conn = http.client.HTTPSConnection(host, port, timeout=self.timeout)
        conn.sock = sock
        try:
            conn.request("POST", target, body=body, headers={**headers,
                                                             "Content-Length": str(len(body))})
            resp = conn.getresponse()
            data = resp.read(READ_CAP)
            return resp.status, data
        except socket.timeout:
            raise CallbackFailure("timeout") from None
        except (ssl.SSLError, ssl.CertificateError):
            raise CallbackFailure("tls_error") from None
        except (OSError, http.client.HTTPException):
            raise CallbackFailure("connection_refused") from None
        finally:
            conn.close()


def status_category(status: int) -> str | None:
    if 200 <= status < 300:
        return None
    return "http_5xx" if status >= 500 else "http_4xx"


# ── 구독 ──────────────────────────────────────────────────────────────────────

@dataclass
class Pending:
    event: dict
    attempts: int = 0
    next_at: float = 0.0


@dataclass
class Subscription:
    id: str
    principal: str
    owner: str
    name: str
    arguments: dict
    role: str
    url: str
    keys: list = field(default_factory=list)          # [(bytes, 만료 시각 또는 None)]
    expires_at: float = 0.0
    watermark: tuple = (0, 0)
    frontier: tuple = (0, 0)
    active: bool = True
    last_delivery_at: float | None = None
    last_error: str | None = None
    failed_since: float | None = None
    pending: list = field(default_factory=list)
    done: collections.OrderedDict = field(default_factory=collections.OrderedDict)
    window: collections.deque = field(default_factory=collections.deque)
    record_id: str | None = None
    record_until: str = ""
    role_domain: str = ""

    def signing_keys(self, now: float) -> list[bytes]:
        self.keys = [(k, until) for k, until in self.keys if until is None or until > now]
        return [k for k, _ in self.keys]

    def status(self) -> dict:
        out = {"active": self.active, "lastDeliveryAt": _iso(self.last_delivery_at),
               "lastError": self.last_error}
        if self.failed_since is not None:
            out["failedSince"] = _iso(self.failed_since)
        return out


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _day(ts: float, plus: int = 0) -> str:
    d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()
    return (d + datetime.timedelta(days=plus)).isoformat()


def subscription_id(principal: str, url: str, name: str, arguments: dict) -> str:
    raw = canonical([principal, url, name, canonical(arguments)])
    return "sub_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _later(a: tuple, b: tuple) -> tuple:
    return max(a[0], b[0]), max(a[1], b[1])


class Runtime:
    """한 프로세스의 웹훅 구독과 전달. 메서드는 스레드 안전하다.

    `tick()` 한 번이 한 주기다: 만료 정리 → 소유자마다 피드 한 페이지 → 대기 중인 이벤트
    POST → 워터마크 전진. 배경 스레드는 `tick()` 을 주기마다 부를 뿐이다."""

    def __init__(self, bridge, *, sender: Sender | None = None, clock=time.time,
                 tick_s: float | None = None, max_per_principal: int | None = None,
                 write_records: bool = True):
        self.bridge = bridge
        self.sender = sender or Sender()
        self.clock = clock
        self.tick_s = tick_s if tick_s is not None else _env_int("VINC_EVENTS_TICK_S", 20, 5, 600)
        self.max_per_principal = (max_per_principal if max_per_principal is not None
                                  else _env_int("VINC_EVENTS_MAX_SUBS", 20, 1, 200))
        self.write_records = write_records
        self.subs: dict[str, Subscription] = {}
        self._verified: dict[tuple, float] = {}
        self._challenges: dict[str, collections.deque] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 피드 ──
    def _feed(self, owner: str, since: tuple | None, *, limit: int = PAGE_LIMIT,
              role: str | None = None, kinds: tuple = ()) -> dict:
        params = {"owner": owner, "limit": limit}
        if since is not None:
            params["since"] = format_cursor(since)
        if role:
            params["role"] = role
        if kinds:
            params["kinds"] = ",".join(kinds)
        out = self.bridge.request("GET", "/events", params=params)
        if not isinstance(out, dict) or out.get("executed") is not True:
            raise RuntimeError(str((out or {}).get("error") or "event feed unavailable"))
        return out

    # ── 구독·해지 ──
    def subscribe(self, params: dict, *, principal: str, owner: str, can_write: bool) -> dict:
        name = check_name(params.get("name"))
        arguments = check_arguments(params.get("arguments"))
        delivery = params.get("delivery")
        if not isinstance(delivery, dict):
            raise EventsError(INVALID_PARAMS, "delivery must be an object")
        if delivery.get("mode", "webhook") != "webhook":
            raise EventsError(UNSUPPORTED, "only webhook delivery is served by events/subscribe",
                              {"feature": "deliveryMode", "value": delivery.get("mode")})
        url = delivery.get("url")
        host, port, _ = check_url(url)
        key = decode_secret(delivery.get("secret"))
        ttl_ms = grant_ttl_ms(params)
        cursor = parse_cursor(params.get("cursor"))
        role = role_id(arguments["role"])
        sid = subscription_id(principal, url, name, arguments)
        now = self.clock()
        with self._lock:
            live = self.subs.get(sid)
            if live is not None and live.expires_at > now:
                return self._refresh(live, key, ttl_ms, now)
            live_subs = [s for s in self.subs.values() if s.expires_at > now]
            mine = sum(1 for s in live_subs if s.principal == principal)
            if mine >= self.max_per_principal or len(live_subs) >= MAX_SUBS_TOTAL:
                raise EventsError(RESOURCE_EXHAUSTED, "too many event subscriptions",
                                  {"limit": "subscriptions", "max": self.max_per_principal})
        found = self.bridge.request("GET", "/roles", params={"owner": owner, "role": role})
        if not isinstance(found, dict) or found.get("executed") is not True:
            raise EventsError(-32603, "the role could not be read right now")
        if not found.get("found"):
            raise EventsError(INVALID_PARAMS, f"no role {role!r} in this space")
        domain = str(((found.get("node") or {}).get("domain")) or "")
        head = parse_cursor(self._feed(owner, None, limit=1)["cursor"])
        truncated = False
        start = head
        if cursor is not None:
            if cursor[0] > head[0] or cursor[1] > head[1]:
                truncated = True
            else:
                start = cursor
        try:
            resolve_public(host, port, self.sender.resolver)
        except CallbackFailure as exc:
            raise EventsError(CALLBACK_ERROR, "the callback endpoint could not be reached",
                              {"reason": exc.category}) from None
        self._verify(principal, url, host, sid, key, now)
        sub = Subscription(id=sid, principal=principal, owner=owner, name=name,
                           arguments=arguments, role=role, url=url, keys=[(key, None)],
                           expires_at=now + ttl_ms / 1000, watermark=start, frontier=start,
                           role_domain=domain)
        with self._lock:
            self.subs[sid] = sub
        if can_write and self.write_records:
            self._write_record(sub, now)
        self.start()
        return {"id": sid, "refreshBefore": _iso(sub.expires_at),
                "cursor": format_cursor(sub.watermark), "truncated": truncated}

    def _refresh(self, sub: Subscription, key: bytes, ttl_ms: int, now: float) -> dict:
        current = sub.signing_keys(now)
        if key not in current:
            sub.keys = [(k, now + ROTATION_GRACE_S) for k in current] + [(key, None)]
        sub.expires_at = now + ttl_ms / 1000
        sub.active = True
        if sub.record_id and _day(sub.expires_at, 1) > sub.record_until:
            self._extend_record(sub, _day(sub.expires_at, 1))
        return {"id": sub.id, "refreshBefore": _iso(sub.expires_at),
                "cursor": format_cursor(sub.watermark), "truncated": False,
                "deliveryStatus": sub.status()}

    def unsubscribe(self, params: dict, *, principal: str) -> dict:
        name = params.get("name")
        arguments = check_arguments(params.get("arguments"))
        delivery = params.get("delivery")
        url = delivery.get("url") if isinstance(delivery, dict) else None
        if not isinstance(name, str) or not isinstance(url, str):
            raise EventsError(INVALID_PARAMS, "name and delivery.url are required")
        sid = subscription_id(principal, url, name, arguments)
        with self._lock:
            sub = self.subs.pop(sid, None)
        if sub is not None and sub.record_id:
            self._extend_record(sub, _day(self.clock()))
        return {}

    def _verify(self, principal: str, url: str, host: str, sid: str, key: bytes,
                now: float) -> None:
        with self._lock:
            until = self._verified.get((principal, url))
            if until is not None and until > now:
                return
            recent = self._challenges.setdefault(host, collections.deque())
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= CHALLENGES_PER_MIN:
                raise EventsError(RESOURCE_EXHAUSTED, "too many verification requests to this host",
                                  {"limit": "verifications", "max": CHALLENGES_PER_MIN})
            recent.append(now)
        challenge = secrets.token_urlsafe(32)
        body = canonical({"type": "verification", "challenge": challenge}).encode("utf-8")
        msg_id = "msg_verification_" + secrets.token_hex(8)
        try:
            status, data = self.sender.post(url, signed_headers([key], msg_id, sid, body, now), body)
        except CallbackFailure as exc:
            raise EventsError(CALLBACK_ERROR, "the callback endpoint could not be verified",
                              {"reason": exc.category}) from None
        category = status_category(status)
        if category:
            raise EventsError(CALLBACK_ERROR, "the callback endpoint could not be verified",
                              {"reason": category})
        try:
            echoed = json.loads(data.decode("utf-8")).get("challenge")
        except (ValueError, AttributeError, UnicodeDecodeError):
            echoed = None
        if not isinstance(echoed, str) or not hmac.compare_digest(echoed, challenge):
            raise EventsError(CALLBACK_ERROR, "the callback endpoint could not be verified",
                              {"reason": "challenge_failed"})
        with self._lock:
            self._verified[(principal, url)] = now + VERIFY_TTL_S

    # ── 구독 기록(그래프) ──
    def _write_record(self, sub: Subscription, now: float) -> None:
        """역할에 붙는 구독 기록. URL·호스트·비밀은 싣지 않는다. 실패해도 구독은 산다."""
        rid = "record:event-subscription-" + hashlib.sha256(sub.id.encode()).hexdigest()[:16]
        until = _day(sub.expires_at, 1)
        node = {"id": rid, "type": "record",
                "title": f"Event subscription: {sub.name} for {sub.role[len('concept:'):]}",
                "domain": sub.role_domain or "agent", "tags": ["event-subscription"],
                "valid_from": _day(now), "valid_until": until,
                "props": {"event": sub.name, "role": sub.role, "delivery": "webhook",
                          "subscription_id": sub.id,
                          "principal_kind": sub.principal.split(":", 1)[0]}}
        edge = {"from": rid, "to": sub.role, "type": "relates_to",
                "evidence": f"This webhook subscription watches the role for {sub.name} events."}
        if self._ingest(sub.owner, [node], [edge]):
            sub.record_id, sub.record_until = rid, until

    def _extend_record(self, sub: Subscription, until: str) -> None:
        if self._ingest(sub.owner, [{"id": sub.record_id, "type": "record",
                                     "valid_until": until}], []):
            sub.record_until = until

    def _ingest(self, owner: str, nodes: list, edges: list) -> bool:
        from . import tools
        try:
            out = tools.call("vinc_ingest", {"nodes": nodes, "edges": edges}, self.bridge,
                             forced_owner=owner, hub=True)
        except Exception as exc:                       # noqa: BLE001 — 기록은 부가물이다
            print(f"[events] subscription record not written: {type(exc).__name__}",
                  file=sys.stderr, flush=True)
            return False
        return isinstance(out, dict) and out.get("executed") is not False

    # ── 전달 ──
    def tick(self) -> dict:
        now = self.clock()
        stats = {"scanned": 0, "sent": 0, "delivered": 0, "abandoned": 0, "expired": 0}
        with self._lock:
            gone = [s for s in self.subs.values() if s.expires_at <= now]
            for s in gone:
                self.subs.pop(s.id, None)
            groups: dict[str, list[Subscription]] = {}
            for s in self.subs.values():
                if s.active:
                    groups.setdefault(s.owner, []).append(s)
        stats["expired"] = len(gone)
        for s in gone:
            if s.record_id:
                self._extend_record(s, _day(now))
        for owner, subs in groups.items():
            try:
                self._scan(owner, subs)
                stats["scanned"] += 1
            except Exception as exc:                   # noqa: BLE001 — 한 소유자가 나머지를 막지 않는다
                print(f"[events] feed read failed: {type(exc).__name__}", file=sys.stderr,
                      flush=True)
        budget = POSTS_PER_TICK
        for subs in groups.values():
            for sub in subs:
                try:
                    budget = self._deliver(sub, budget, stats)
                finally:
                    self._advance(sub)
        return stats

    def _scan(self, owner: str, subs: list[Subscription]) -> None:
        lowest = (min(s.frontier[0] for s in subs), min(s.frontier[1] for s in subs))
        head = parse_cursor(self._feed(owner, None, limit=1)["cursor"])
        if head[0] <= lowest[0] and head[1] <= lowest[1]:
            return
        page = self._feed(owner, lowest)
        end = parse_cursor(page["cursor"])
        for sub in subs:
            queued = {p.event["eventId"] for p in sub.pending}
            for ev in page.get("events") or []:
                pos = ev.get("_pos") or ["n", 0]
                mark = sub.frontier[0] if pos[0] == "n" else sub.frontier[1]
                if (ev.get("name") != sub.name or (ev.get("data") or {}).get("role") != sub.role
                        or int(pos[1]) <= mark or ev["eventId"] in sub.done
                        or ev["eventId"] in queued):
                    continue
                sub.pending.append(Pending(event=ev))
                queued.add(ev["eventId"])
            sub.frontier = _later(sub.frontier, end)

    def _deliver(self, sub: Subscription, budget: int, stats: dict) -> int:
        """이 주기에 보낼 차례인 것을 보낸다. 실패하면 그 구독은 이번 주기를 멈춘다:
        느린 받는 쪽 하나가 한 주기에 쓰는 시간은 타임아웃 한 번이다."""
        now = self.clock()
        for item in list(sub.pending):
            if budget <= 0 or not sub.active or self.subs.get(sub.id) is not sub:
                break
            if item.next_at > now:
                continue
            budget -= 1
            stats["sent"] += 1
            outcome = self._post(sub, item, now)
            if outcome == "ok":
                stats["delivered"] += 1
            elif outcome == "abandon":
                stats["abandoned"] += 1
            else:
                break
        return budget

    def _post(self, sub: Subscription, item: Pending, now: float) -> str:
        ev = item.event
        body = canonical({"eventId": ev["eventId"], "name": ev["name"],
                          "timestamp": ev["timestamp"], "data": ev["data"],
                          "cursor": format_cursor(sub.watermark)}).encode("utf-8")
        if len(body) > MAX_BODY:
            self._finish(sub, item)
            return "abandon"
        headers = signed_headers(sub.signing_keys(now), ev["eventId"], sub.id, body, now)
        try:
            status, _ = self.sender.post(sub.url, headers, body)
            category = status_category(status)
        except CallbackFailure as exc:
            status, category = 0, exc.category
        except EventsError:
            status, category = 0, "connection_refused"
        self._health(sub, category is None, category, now)
        if category is None:
            sub.last_delivery_at = now
            self._finish(sub, item)
            return "ok"
        if status in (410, 413):
            self._finish(sub, item)
            return "abandon"
        item.attempts += 1
        if item.attempts > len(RETRY_DELAYS):
            self._finish(sub, item)
            return "abandon"
        item.next_at = now + RETRY_DELAYS[item.attempts - 1]
        return "retry"

    def _finish(self, sub: Subscription, item: Pending) -> None:
        if item in sub.pending:
            sub.pending.remove(item)
        sub.done[item.event["eventId"]] = None
        while len(sub.done) > LEDGER_CAP:
            sub.done.popitem(last=False)

    def _health(self, sub: Subscription, ok: bool, category: str | None, now: float) -> None:
        sub.window.append((now, ok))
        while sub.window and sub.window[0][0] <= now - 3600:
            sub.window.popleft()
        if ok:
            sub.last_error, sub.failed_since = None, None
            return
        sub.last_error = category
        if sub.failed_since is None:
            sub.failed_since = now
        failures = sum(1 for _, good in sub.window if not good)
        if len(sub.window) >= 100 and failures > 0.95 * len(sub.window):
            sub.active = False

    @staticmethod
    def _advance(sub: Subscription) -> None:
        """안전 워터마크: 대기 중인 이벤트 바로 앞까지, 대기가 없으면 본 데까지."""
        n, d = sub.frontier
        for item in sub.pending:
            counter, seq = item.event.get("_pos") or ["n", 0]
            if counter == "n":
                n = min(n, int(seq) - 1)
            else:
                d = min(d, int(seq) - 1)
        sub.watermark = _later(sub.watermark, (n, d))

    # ── 배경 스레드 ──
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="vinc-events",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.tick()
            except Exception as exc:                   # noqa: BLE001 — 주기는 계속 돈다
                print(f"[events] tick failed: {type(exc).__name__}: {exc}", file=sys.stderr,
                      flush=True)


def poll(bridge, params: dict, *, owner: str) -> dict:
    """`events/poll` 한 번. 구독 상태를 들지 않는다(커서는 클라이언트 몫)."""
    name = check_name(params.get("name"))
    arguments = check_arguments(params.get("arguments"))
    cursor = parse_cursor(params.get("cursor"))
    limit = params.get("maxEvents")
    limit = 100 if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise EventsError(INVALID_PARAMS, "maxEvents must be an integer 1..500")
    query = {"owner": owner, "limit": limit, "kinds": name, "role": role_id(arguments["role"])}
    if cursor is not None:
        query["since"] = format_cursor(cursor)
    out = bridge.request("GET", "/events", params=query)
    if not isinstance(out, dict) or out.get("executed") is not True:
        raise EventsError(-32603, str((out or {}).get("error") or "event feed unavailable"))
    return {"events": [{k: e[k] for k in ("eventId", "name", "timestamp", "data")}
                       for e in out.get("events") or []],
            "cursor": out.get("cursor"), "truncated": bool(out.get("truncated")),
            "hasMore": bool(out.get("has_more")), "nextPollMs": POLL_MS}


def public(result):
    """도구·REST 가 내는 모양. 내부 위치(`_pos`)를 걷어 낸다."""
    if not isinstance(result, dict) or not isinstance(result.get("events"), list):
        return result
    return {**result, "events": [{k: v for k, v in e.items() if k != "_pos"}
                                 for e in result["events"] if isinstance(e, dict)]}
