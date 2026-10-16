"""Small REST v1 client for the account-target CLI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from store import diagnostics
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request

from vinc_mcp import rest_routes
from vinc_mcp.hubhttp import urlopen  # 리다이렉트를 따르지 않는 한 자리. 테스트가 이 이름을 바꿔 끼운다
from vinc_mcp.entitlement import KEY_PREFIXES

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def api_key() -> str | None:
    """`VINC_API_KEY`, 그다음 옛 `VINC_REMOTE_MCP_TOKEN`(회원 키 모양일 때만).
    정적 MCP 토큰은 v1 이 받지 않으므로 보내지 않는다. 근거: [[vinc.spec.api-cli-0-9-6 §3.3]]"""
    key = os.environ.get("VINC_API_KEY")
    if key:
        return key
    legacy = os.environ.get("VINC_REMOTE_MCP_TOKEN") or ""
    return legacy if legacy.startswith(KEY_PREFIXES) else None


def _segment(value) -> str:
    # `.`·`..` 는 경로 정규화가 되감는 조각이라 그대로 보내면 다른 라우트로 간다.
    text = quote(str(value), safe="")
    return text.replace(".", "%2E") if text in (".", "..") else text


def _base_url(base_url: str | None) -> str:
    base = (base_url or os.environ.get("VINC_API_URL") or "https://mcp.vincs.io").rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in _LOOPBACK):
        return base
    raise RemoteFailure(_error("invalid_argument",
        "VINC_API_URL must use https (http is allowed only for localhost)."), 2)


def _http_error_code(status: int) -> str:
    return {401: "not_authenticated", 403: "forbidden", 404: "not_found",
            413: "payload_too_large", 429: "quota_exceeded"}.get(
        status, "upstream_unavailable" if status >= 500 else "invalid_argument")


@dataclass
class RemoteFailure(Exception):
    result: dict
    exit_code: int


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "request_id": "", "details": {}}}


def _route(tool: str, args: dict) -> rest_routes.Route:
    candidates = [route for route in rest_routes.ROUTES if route.tool == tool]
    if not candidates:
        raise ValueError(f"no v1 route for {tool}")
    if tool == "vinc_doc_put":
        method = "PUT" if args.get("doc_id") else "POST"
        return next(r for r in candidates if r.method == method)
    if tool == "vinc_graph":
        method = "GET" if args.get("seed_id") else "POST"
        return next(r for r in candidates if r.method == method)
    if tool == "vinc_edges":
        method = "POST" if args.get("nodes") is not None else "GET"
        return next(r for r in candidates if r.method == method)
    if tool == "vinc_role":
        return next(r for r in candidates if bool(r.path_args) == bool(args.get("role")))
    if tool == "vinc_brief":
        return next(r for r in candidates if r.method == "POST")
    return candidates[0]


def call(tool: str, arguments: dict, *, space: str = "personal",
         key: str | None = None, base_url: str | None = None) -> dict:
    """Call one MCP tool through its public REST route; owner is never sent."""
    key = key if key is not None else api_key()
    if not key:
        raise RemoteFailure(_error("not_authenticated",
            "Set VINC_API_KEY for the account target, or pass --target local."), 4)
    base = _base_url(base_url)
    args = {k: v for k, v in arguments.items()
            if k not in {"owner", "target", "debug", "space"} and v is not None}
    route = _route(tool, args)
    path = route.path
    path_keys = set()
    for field, arg in route.path_args:
        if not args.get(arg):
            raise ValueError(f"{arg} is required for {tool}")
        path = path.replace("{" + field + "}", _segment(args[arg]))
        path_keys.add(arg)
    allowed = set(route.query) | set(route.body) | path_keys
    extras = set(args) - allowed
    if tool == "vinc_graph" and args.get("seed_id"):
        # The tool gives seed_id priority over a pasted View URL.
        extras.discard("url")
    if extras:
        raise ValueError("not available on this v1 route: " + ", ".join(sorted(extras)))
    query = {name: args[name] for name in route.query if name in args}
    if space != "personal":
        query["space"] = space
    if query:
        query = {k: ",".join(v) if isinstance(v, list) else
                 ("true" if v else "false") if isinstance(v, bool) else v
                 for k, v in query.items()}
        path += "?" + urlencode(query)
    body = {name: args[name] for name in route.body if name in args}
    request = Request(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8")
                      if route.method in ("POST", "PUT") else None,
                      headers={"Authorization": "Bearer " + key,
                               "Accept": "application/json",
                               "Content-Type": "application/json"},
                      method=route.method)
    started = time.perf_counter()
    status, request_id = "network_error", ""
    try:
        with urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            request_id = response.headers.get("X-Request-Id", "") if hasattr(response, "headers") else ""
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        request_id = exc.headers.get("X-Request-Id", "") if exc.headers else ""
        if 300 <= exc.code < 400:
            raise RemoteFailure(_error("upstream_unavailable",
                f"The API answered a redirect (HTTP {exc.code}); it was not followed."), 5) from None
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = _error(_http_error_code(exc.code), f"HTTP {exc.code}")
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
            payload = _error(_http_error_code(exc.code), f"HTTP {exc.code}")
        wait = exc.headers.get("Retry-After") if exc.code == 429 and exc.headers else None
        details = payload["error"].setdefault("details", {})
        if wait and "retry_after_s" not in details:
            details["retry_after_s"] = int(wait) if wait.isdigit() else wait
        code = 4 if exc.code == 401 else 6 if exc.code == 429 else \
               5 if exc.code >= 500 else 1
        raise RemoteFailure(payload, code) from None
    except (URLError, OSError, TimeoutError) as exc:
        raise RemoteFailure(_error("upstream_unavailable",
                                  f"Account API unavailable: {type(exc).__name__}"), 5) from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise RemoteFailure(_error("upstream_unavailable",
                                  "Account API returned invalid JSON."), 5) from exc
    finally:
        diagnostics.trace(route.method, path, status, started, request_id=request_id, transport="account")
