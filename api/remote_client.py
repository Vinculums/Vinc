"""Small REST v1 client for the account-target CLI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from vinc_mcp import rest_routes


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
    if tool == "vinc_brief":
        return next(r for r in candidates if r.method == "POST")
    return candidates[0]


def call(tool: str, arguments: dict, *, space: str = "personal",
         key: str | None = None, base_url: str | None = None) -> dict:
    """Call one MCP tool through its public REST route; owner is never sent."""
    key = key if key is not None else os.environ.get("VINC_API_KEY")
    if not key:
        raise RemoteFailure(_error("not_authenticated",
            "Set VINC_API_KEY for the account target, or pass --target local."), 4)
    args = {k: v for k, v in arguments.items()
            if k not in {"owner", "target", "debug", "space"} and v is not None}
    route = _route(tool, args)
    path = route.path
    path_keys = set()
    for field, arg in route.path_args:
        if not args.get(arg):
            raise ValueError(f"{arg} is required for {tool}")
        path = path.replace("{" + field + "}", quote(str(args[arg]), safe=""))
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
    base = (base_url or os.environ.get("VINC_API_URL") or "https://mcp.vincs.io").rstrip("/")
    request = Request(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8")
                      if route.method in ("POST", "PUT") else None,
                      headers={"Authorization": "Bearer " + key,
                               "Accept": "application/json",
                               "Content-Type": "application/json"},
                      method=route.method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = _error("upstream_unavailable" if exc.code >= 500 else
                             "invalid_argument", f"HTTP {exc.code}")
        code = 4 if exc.code == 401 else 6 if exc.code == 429 else \
               5 if exc.code >= 500 else 1
        raise RemoteFailure(payload, code) from None
    except (URLError, OSError, TimeoutError) as exc:
        raise RemoteFailure(_error("upstream_unavailable",
                                  f"Account API unavailable: {type(exc).__name__}"), 5) from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise RemoteFailure(_error("upstream_unavailable",
                                  "Account API returned invalid JSON."), 5) from exc
