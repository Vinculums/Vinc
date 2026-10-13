"""REST v1 envelope over the same tool gate and executor used by MCP."""
from __future__ import annotations

import json
import math
import re
import secrets
from urllib.parse import parse_qs, urlparse

from . import entitlement, rest_routes, toolcall, tools
from store import request_timing
from .http_server import MAX_BODY, _UUID_RE

#: v1 결과 상한. MCP 의 72,000자(rpc.MAX_RESULT_CHARS)는 모델 컨텍스트용 상한이라 스크립트가
#: 받는 v1 에는 쓰지 않는다 — 쓰면 4 MB 로 넣은 문서를 되읽지 못하고, 이미 적용된 쓰기가
#: "Nothing was changed" 로 보고된다. 이것은 응답 한 건의 메모리 상한일 뿐이다.
MAX_RESULT_BYTES = 16 * 1024 * 1024


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


def _check_json_type(name: str, value, spec: dict) -> None:
    kind = spec.get("type")
    valid = {
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }.get(kind)
    if valid and not valid(value):
        raise ValueError(f"{name} must be {kind}")
    if kind == "number" and not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


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
            _check_json_type(key, body[key], schema.get(key) or {})
            args[key] = body[key]
    if query.get("space"):
        args["space"] = query["space"]
    return args


def _failure(name: str, result: dict, *, by_id: bool = False) -> tuple | None:
    """도구 결과 모양을 v1 상태로 옮긴다. 표는 tests/mcp/test_rest_v1.py 의 FAILURE_CASES 가 고정한다.
    반환은 (status, code, message) 또는 (status, code, message, details)."""
    error = str(result.get("error") or "")
    note = str(result.get("note") or "")
    reason = (error + " " + note).lower()
    if name == "vinc_operation_status" and result.get("executed") is True:
        return None
    if result.get("status") == "conflict" or error in ("write_conflict", "preview_conflict", "operation_id_conflict"):
        return (409, error, "The saved state no longer matches this request.",
                {k: result[k] for k in ("node_id", "current_rev", "current_content_hash", "operation_id") if k in result})
    if error in ("write_target_unavailable", "document_unavailable", "preview_target_unavailable"):
        return 404, "not_found", "The requested graph item was not found."
    if error.startswith("preview_") and error not in ("preview_read_unavailable", "preview_boundary_unavailable"):
        return 400, error, "A complete supported preview is required before applying."
    if result.get("status") in ("invalid_request", "unsupported_kind"):
        return 400, error, error
    if error == "store_busy":
        # 유지보수 거절은 서버 내부 오류가 아니다. SDK가 재시도 가능한 503을 받아야 한다.
        return (503, "upstream_unavailable", note or "Store maintenance is in progress.",
                {"retryable": True, "retry_after_s": 1})
    # 브리지가 사이드카와 못 이야기했다. 쓰기라면 **이미 적용됐을 수 있다** — 재시도 금지를 싣는다.
    if error.startswith("sidecar write unconfirmed") or (
            error and result.get("write_may_have_applied") is True):
        return (503, "upstream_unavailable", error,
                {"retryable": False, "write_may_have_applied": True})
    if error.startswith("sidecar HTTP 5") or error.startswith("sidecar unreachable"):
        return 503, "upstream_unavailable", error
    if error == "domain_limit_reached":
        return (409, "domain_limit_reached", "The space has reached its topic limit.",
                {k: result[k] for k in ("limit", "current", "attempted") if k in result})
    if error.startswith(("invalid_cursor", "stale_cursor")):
        return 400, "invalid_argument", error
    if name == "vinc_delete_edge" and "unsupported edge type" in reason:
        return 400, "invalid_argument", note or error
    if name == "vinc_merge_nodes" and any(
            term in reason for term in ("supports concept", "same node")):
        return 400, "invalid_argument", error or note
    if name == "vinc_doc_get" and result.get("verified") is False:
        return 503, "upstream_unavailable", "document chunks could not be verified"
    if name == "vinc_brief" and result.get("status") == "none":
        # 주소로 물은 노드(`/v1/nodes/{id}`)나 못 읽은 노드는 없는 것이다. 자유 질의가
        # 아무것도 못 찾은 것은 오류가 아니라 빈 답이다.
        if by_id or result.get("unreadable"):
            return 404, "not_found", error or "The requested graph item was not found."
        return None
    if result.get("found") is False:
        return 404, "not_found", error or "The requested graph item was not found."
    if result.get("executed") is False:
        if any(term in reason for term in ("unknown", "not found", "no such", "missing doc")):
            return 404, "not_found", error or note
        if any(term in reason for term in ("invalid", "required", "malformed", "stale")):
            return 400, "invalid_argument", error or note
        if any(term in reason for term in (
                "unavailable", "unreachable", "no store", "no reachable store")):
            return 503, "upstream_unavailable", error or note
        return 500, "internal", error or "The tool did not execute."
    if result.get("isError") or error:
        return 500, "internal", _message(result)
    return None


def handle(handler, method: str) -> bool:
    """Collect opt-in timings on viewer routes without changing their account gates."""
    if handler.headers.get(request_timing.HEADER) == request_timing.VALUE:
        selected = rest_routes.find_route(method, urlparse(handler.path).path)
        if selected and tools.tool_tier(selected[0].tool) == "viewer":
            with request_timing.capture():
                return _handle(handler, method)
    return _handle(handler, method)


def _handle(handler, method: str) -> bool:
    """Handle every /v1 request, including its 404/405, and leave other paths alone."""
    url = urlparse(handler.path)
    if not (url.path == "/v1" or url.path.startswith("/v1/")):
        return False
    request_id = "req_" + secrets.token_hex(12)
    authenticated = False

    def send(status: int, value: dict, extra: dict | None = None):
        from .roles import serialize_result
        raw = serialize_result(value).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Vinc-Request-Id", request_id)
        timing = request_timing.header("rest") if authenticated else ""
        if timing:
            handler.send_header("Server-Timing", timing)
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
    parsed_query = parse_qs(url.query, keep_blank_values=True)
    repeated = sorted(key for key, values in parsed_query.items() if len(values) > 1)
    if repeated:
        # 첫 값만 쓰면 뒤에 붙인 `space=<team>` 이 조용히 개인 그래프에 작용한다.
        fail(400, "invalid_argument", "repeated parameter: " + ", ".join(repeated))
        return True
    query = {key: values[0] for key, values in parsed_query.items()}
    if "space" in query:
        query["space"] = query["space"].strip().lower()   # /mcp 와 같은 정규화
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
        with request_timing.measure("auth"):
            auth = handler._auth(None, presented_at="/mcp")
    except Exception:
        fail(503, "upstream_unavailable", "API key verification is unavailable.",
             extra={"Retry-After": "1"})
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
        with request_timing.measure("gate"):
            gated = toolcall.preflight(
                route.tool, args, base_auth=auth, address_space=None,
                authenticate=lambda want: handler._auth(want, presented_at="/mcp"),
                valid_space=lambda want: bool(_UUID_RE.fullmatch(want)))
    except Exception:
        fail(503, "upstream_unavailable", "Space membership verification is unavailable.",
             extra={"Retry-After": "1"})
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
    authenticated = True
    outcome = toolcall.execute(route.tool, args, handler.app.bridge,
                               forced_owner=resolved.get("owner"), plan=resolved.get("plan"),
                               quota=handler.app.quota_check,
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
             extra={"Retry-After": wait, "Vinc-Quota-Limit": quota.get("limit") or "",
                    "Vinc-Quota-Remaining": 0})
        return True
    if outcome.kind != "ok":
        error = outcome.error
        message = _message(outcome.result) if outcome.result else str(error)
        if isinstance(error, json.JSONDecodeError):
            # 사이드카가 JSON 이 아닌 200 을 줬다. 요청이 아니라 뒤쪽 문제다.
            fail(503, "upstream_unavailable", "The graph service returned an unreadable answer.")
            return True
        if outcome.kind == "protocol_error" and isinstance(error, (KeyError, ValueError)):
            fail(400, "invalid_argument", message)
            return True
        if isinstance(error, (ConnectionError, TimeoutError, OSError)) or "sidecar unreachable" in message:
            fail(503, "upstream_unavailable", message)
        else:
            fail(500, "internal", message)
        return True
    result = outcome.result or {}
    failure = _failure(route.tool, result, by_id=bool(route.path_args))
    if failure:
        status, code, message, *rest = failure
        fail(status, code, message, details=rest[0] if rest else None,
             extra={"Retry-After": "1"} if result.get("error") == "store_busy" else None)
        return True
    from .roles import serialize_result
    size = len(serialize_result(result).encode("utf-8"))
    if size > MAX_RESULT_BYTES:
        # 도구는 이미 실행됐다. 쓰기라면 적용됐으므로 "바뀐 것 없음" 이라고 말하지 않는다.
        fail(500, "internal", f"The result is too large to return ({size:,} bytes). "
             "The tool ran; narrow the request to read its result.",
             details={"bytes": size, "limit": MAX_RESULT_BYTES,
                      "tool_ran": True, "writes": tools.tool_tier(route.tool) != "viewer"})
        return True
    status = (201 if route.tool == "vinc_doc_put" and result.get("created")
              else route.success)
    send(status, result)
    return True
