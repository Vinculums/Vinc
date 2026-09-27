"""REST v1 envelope over the same tool gate and executor used by MCP."""
from __future__ import annotations

import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

from . import entitlement, rest_routes, toolcall, tools
from .http_server import MAX_BODY, _UUID_RE
from .rpc import _result_text


_SCHEMAS = {item["name"]: item["inputSchema"] for item in tools.specs(allow_target=False)}


def _message(result: dict | None) -> str:
    if not isinstance(result, dict):
        return "The request could not be completed."
    error = result.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "The request failed.")
    if error:
        return str(error)
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("text"):
            return str(part["text"])
    return "The request could not be completed."


def _coerce(value: str, spec: dict):
    kind = spec.get("type")
    if kind == "integer":
        if not re.fullmatch(r"-?\d+", value):
            raise ValueError("must be an integer")
        return int(value)
    if kind == "number":
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError("must be a number") from exc
        if not (number == number and abs(number) != float("inf")):
            raise ValueError("must be a finite number")
        return number
    if kind == "boolean":
        if value.lower() in ("true", "1"):
            return True
        if value.lower() in ("false", "0"):
            return False
        raise ValueError("must be true or false")
    if kind == "array":
        return value.split(",") if value else []
    return value


def _arguments(route: rest_routes.Route, path_fields: dict[str, str],
               query: dict[str, str], body: dict) -> dict:
    schema = _SCHEMAS[route.tool].get("properties") or {}
    extras = (set(query) - set(route.query) - {"space"}) | (set(body) - set(route.body) - {"owner"})
    if extras:
        raise ValueError("unknown parameter: " + ", ".join(sorted(extras)))
    args = {arg: path_fields[field] for field, arg in route.path_args}
    for key in route.query:
        if key in query:
            try:
                args[key] = _coerce(query[key], schema.get(key) or {})
            except ValueError as exc:
                raise ValueError(f"{key} {exc}") from exc
    for key in route.body:
        if key in body:
            args[key] = body[key]
    if query.get("space"):
        args["space"] = query["space"]
    return args


def _failure(name: str, result: dict) -> tuple[int, str, str] | None:
    """Classify known tool result shapes, retaining successful empty/partial results."""
    error = str(result.get("error") or "")
    if error.startswith(("invalid_cursor", "stale_cursor")):
        return 400, "invalid_argument", error
    if name == "vinc_doc_get" and result.get("verified") is False:
        return 503, "upstream_unavailable", "document chunks could not be verified"
    if result.get("found") is False or (name == "vinc_brief" and result.get("status") == "none"):
        return 404, "not_found", error or "The requested graph item was not found."
    if result.get("executed") is False:
        if any(term in error.lower() for term in ("unknown", "not found", "no such", "missing doc")):
            return 404, "not_found", error
        if any(term in error.lower() for term in ("invalid", "required", "malformed", "stale")):
            return 400, "invalid_argument", error
        if any(term in error.lower() for term in ("unavailable", "unreachable", "no store")):
            return 503, "upstream_unavailable", error
        return 500, "internal", error or "The tool did not execute."
    if result.get("isError") or error:
        return 500, "internal", _message(result)
    return None


def handle(handler, method: str) -> bool:
    """Handle every /v1 request, including its 404/405, and leave other paths alone."""
    url = urlparse(handler.path)
    if not (url.path == "/v1" or url.path.startswith("/v1/")):
        return False
    request_id = "req_" + secrets.token_hex(12)

    def send(status: int, value: dict, extra: dict | None = None):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Vinc-Request-Id", request_id)
        for key, val in (extra or {}).items():
            handler.send_header(key, str(val))
        handler.end_headers()
        handler.wfile.write(raw)

    def fail(status: int, code: str, message: str, *, details: dict | None = None,
             extra: dict | None = None):
        send(status, {"error": {"code": code, "message": message,
                                "request_id": request_id, "details": details or {}}}, extra)

    if url.path == "/v1/openapi.json":
        if method != "GET":
            fail(405, "method_not_allowed", "Use GET for this route.", extra={"Allow": "GET"})
        else:
            from .openapi_v1 import document
            send(200, document())
        return True
    selected = rest_routes.find_route(method, url.path)
    if selected is None:
        allowed = rest_routes.allowed_methods(url.path)
        if allowed:
            fail(405, "method_not_allowed", "Method not allowed for this route.",
                 extra={"Allow": ", ".join(sorted(allowed))})
        else:
            fail(404, "route_not_found", "No such v1 route.")
        return True
    route, fields = selected
    query = {key: values[0] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
    space = query.get("space") or "personal"
    if space != "personal" and not _UUID_RE.fullmatch(space):
        fail(404, "space_not_found", "No such space.")
        return True
    header = handler.headers.get("Authorization") or ""
    if not (header.startswith("Bearer ") and header[7:].strip().startswith(entitlement.KEY_PREFIXES)):
        fail(401, "not_authenticated", "A member API key is required.",
             extra={"WWW-Authenticate": "Bearer"})
        return True
    try:
        auth = handler._auth(None, presented_at="/mcp")
    except Exception:
        fail(503, "upstream_unavailable", "API key verification is unavailable.")
        return True
    if not auth.get("ok") or auth.get("source") != "apikey":
        fail(401, "not_authenticated", "The API key was not accepted.",
             extra={"WWW-Authenticate": "Bearer"})
        return True
    body = {}
    if method in ("POST", "PUT"):
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length > MAX_BODY:
            fail(413, "payload_too_large", "The request body exceeds 4 MB.")
            return True
        if length < 0:
            fail(400, "invalid_argument", "Invalid Content-Length.")
            return True
        try:
            body = json.loads(handler.rfile.read(length)) if length else {}
        except (ValueError, UnicodeDecodeError):
            fail(400, "invalid_argument", "The body must be a JSON object.")
            return True
        if not isinstance(body, dict):
            fail(400, "invalid_argument", "The body must be a JSON object.")
            return True
    try:
        args = _arguments(route, fields, query, body)
    except ValueError as exc:
        fail(400, "invalid_argument", str(exc))
        return True
    try:
        gated = toolcall.preflight(
            route.tool, args, base_auth=auth, address_space=None,
            authenticate=lambda want: handler._auth(want, presented_at="/mcp"),
            valid_space=lambda want: bool(_UUID_RE.fullmatch(want)))
    except Exception:
        fail(503, "upstream_unavailable", "Space membership verification is unavailable.")
        return True
    if gated.kind != "ok":
        status, code = {
            "on_device": (400, "on_device_space"),
            "space_not_found": (404, "space_not_found"),
            "role_denied": (403, "role_forbidden"),
            "read_only": (403, "read_only_key"),
        }[gated.kind]
        fail(status, code, gated.message)
        return True
    resolved = gated.auth or auth
    counted = resolved.get("member") or resolved.get("owner")
    outcome = toolcall.execute(route.tool, args, handler.app.bridge,
                               forced_owner=resolved.get("owner"), plan=resolved.get("plan"),
                               quota=lambda _owner, plan: handler.app.quota_check(counted, plan),
                               entitlements=handler.app.entitlements_for(resolved.get("plan")),
                               allow_target=False)
    if outcome.kind == "invalid_argument":
        fail(400, "invalid_argument", _message(outcome.result))
        return True
    if outcome.kind == "quota":
        quota = (outcome.result or {}).get("_meta", {}).get("vinc_quota", {})
        wait = int(quota.get("retry_after_s") or 0)
        fail(429, "quota_exceeded", _message(outcome.result),
             details={"limit": quota.get("limit"), "retry_after_s": wait},
             extra={"Retry-After": wait})
        return True
    if outcome.kind != "ok":
        error = outcome.error
        message = _message(outcome.result) if outcome.result else str(error)
        if isinstance(error, (ConnectionError, TimeoutError, OSError)) or "sidecar unreachable" in message:
            fail(503, "upstream_unavailable", message)
        else:
            fail(500, "internal", message)
        return True
    result = outcome.result or {}
    failure = _failure(route.tool, result)
    if failure:
        fail(*failure)
        return True
    # MCP may shrink a large tool result. Return the identical object its text contains.
    wrapped = _result_text(json.dumps(result, ensure_ascii=False), route.tool, args)
    if wrapped.get("isError"):
        fail(500, "internal", _message(wrapped))
        return True
    send(route.success, json.loads(wrapped["content"][0]["text"]))
    return True
