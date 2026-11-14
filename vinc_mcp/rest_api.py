"""REST v1 envelope over the same tool gate and executor used by MCP."""
from __future__ import annotations

import json
import math
import re
import secrets
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import community, entitlement, rest_routes, spaces, toolcall, tools
from store import request_timing
from store.envflag import truthy_strict
from .http_server import MAX_BODY

#: v1 결과 상한. MCP 의 72,000자(rpc.MAX_RESULT_CHARS)는 모델 컨텍스트용 상한이라 스크립트가
#: 받는 v1 에는 쓰지 않는다 — 쓰면 4 MB 로 넣은 문서를 되읽지 못하고, 이미 적용된 쓰기가
#: "Nothing was changed" 로 보고된다. 이것은 응답 한 건의 메모리 상한일 뿐이다.
MAX_RESULT_BYTES = 16 * 1024 * 1024


_SCHEMAS = {item["name"]: item["inputSchema"] for item in tools.specs(hub=True)}


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
        parsed = truthy_strict(value)
        if parsed is None:
            raise ValueError("must be true or false")
        return parsed
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
    # 빠진 인자의 기본값은 여기서 채우지 않는다. 도구 핸들러가 MCP 와 같은 값을 쓴다 —
    # 그래서 /v1/search 의 기본 limit 은 20 이고, 사이드카 /search 와 CLI 의 5 와 다르다
    # (감사 D13, `api/server.py` read_search 주석).
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


def _partial_search(name: str, result: dict) -> dict:
    """B9 — 청크 팔만 실패하고 노드 팔이 후보를 냈으면 버리지 않고 성공으로 낸다.

    살릴지와 살린 모양은 `toolcall.partial_search` 하나가 정한다. 최상위 `error` 는 이 봉투가
    HTTP 오류로 바꾸므로 그대로 두면 살아 있는 node_hits 까지 함께 사라진다. 살리지 않는 결과는
    그대로 돌려주고 `_failure` 가 상태로 옮긴다(store_busy 는 재시도 가능한 503).
    ⚠ 여기서는 `error` 가 있는 결과만 넘긴다. `error` 없이 `executed:false` 인 결과는 `_failure` 가
    실패로 옮긴다(호스트 search 도구는 그것도 살린다)."""
    if not result.get("error") or name != "vinc_search":
        return result
    return toolcall.partial_search(result) or result


_ACCOUNT_CODE_RE = re.compile(r"[a-z][a-z0-9_]*")


def _account_details(error) -> dict:
    """계정 거절의 오류 객체를 v1 details 로 옮긴다. v1 code 는 상태로 정하므로 계정 코드
    (username_required 등)는 account_code 로, 계정의 request_id 는 account_request_id 로 싣고
    나머지 필드(currentRevision 등)는 이름 그대로 둔다. 문장 하나뿐인 문자열 거절은 싣지 않는다."""
    if isinstance(error, dict):
        details = {key: value for key, value in error.items() if key not in ("code", "message", "request_id")}
        if error.get("code"):
            details["account_code"] = str(error["code"])
        if error.get("request_id"):
            details["account_request_id"] = str(error["request_id"])
        return details
    if isinstance(error, str) and _ACCOUNT_CODE_RE.fullmatch(error):
        return {"account_code": error}
    return {}


@dataclass(frozen=True)
class _Facts:
    """`_failure` 의 술어가 읽는 값. `error`·`note` 는 문자열로 맞춘 것이고 `reason` 은 둘을 이은 소문자다."""
    name: str
    result: dict
    by_id: bool
    error: str
    note: str
    reason: str


#: 코드 칸의 이 값은 "도구가 낸 `error` 문자열을 그대로 v1 코드로 쓴다"는 뜻이다.
_ERROR_CODE = object()


@dataclass(frozen=True)
class _FailureRow:
    """`_FAILURES` 의 한 줄. `when` 이 참인 첫 줄이 답한다.

    status 가 None 이면 실패가 아니다(성공 봉투로 낸다). message 는 문장을 만드는 함수이고,
    details 가 있으면 (status, code, message, details) 네 칸을, 없으면 세 칸을 낸다."""
    when: Callable[[_Facts], bool]
    status: int | None
    code: str | object | None = None
    message: Callable[[_Facts], str] | None = None
    details: Callable[[_Facts], dict] | None = None


def _note_or_error(f: _Facts) -> str:
    return f.note or f.error


def _error_or_note(f: _Facts) -> str:
    return f.error or f.note


def _error(f: _Facts) -> str:
    return f.error


def _fixed(text: str) -> Callable[[_Facts], str]:
    return lambda _f: text


def _pick(*keys: str) -> Callable[[_Facts], dict]:
    return lambda f: {k: f.result[k] for k in keys if k in f.result}


def _executed_false_with(*terms: str) -> Callable[[_Facts], bool]:
    return lambda f: f.result.get("executed") is False and any(term in f.reason for term in terms)


_COMMUNITY_TOOLS = (community.WRITE_TOOL, community.READ_TOOL)
#: 계정이 거절한 커뮤니티 호출의 상태 → v1 코드. 문장과 details 는 계정의 것을 옮긴다.
_ACCOUNT_STATUS_CODES = ((400, "invalid_argument"), (401, "not_authenticated"), (403, "role_forbidden"),
                         (404, "not_found"), (409, "conflict"), (410, "gone"), (429, "quota_exceeded"))

#: 도구 결과 모양 → v1 실패. **순서가 계약이다**: 위의 줄이 먼저 답한다. 줄마다의 결과 모양은
#: tests/fixtures/tool_failure_shapes.json 이 사람이 읽게 적고, tests/mcp/test_rest_v1_review_fixes.py 의
#: FAILURE_CASES 가 실제 결과로 고정한다.
_FAILURES: tuple[_FailureRow, ...] = (
    # 계정이 거절한 커뮤니티 쓰기·읽기. message 는 계정의 문장이다(오류 객체를 str() 하면 파이썬 repr 이 나간다).
    *(_FailureRow(lambda f, s=status: f.name in _COMMUNITY_TOOLS and f.result.get("status") == s,
                  status, code, lambda f: _message(f.result),
                  lambda f: _account_details(f.result.get("error")))
      for status, code in _ACCOUNT_STATUS_CODES),
    # 영수증을 읽었으면 found:false(모르는 영수증)도 답이다. 적용되지 않았다는 증거가 아니다.
    _FailureRow(lambda f: f.name == "vinc_operation_status" and f.result.get("executed") is True, None),
    _FailureRow(lambda f: f.result.get("status") == "conflict"
                or f.error in ("write_conflict", "preview_conflict", "operation_id_conflict"),
                409, _ERROR_CODE, _fixed("The saved state no longer matches this request."),
                _pick("node_id", "current_rev", "current_content_hash", "operation_id")),
    # 고정한 역할이 바뀌었다. 계약은 싣지 않고 지금의 rev·hash 만 준다.
    _FailureRow(lambda f: f.error == "role_changed", 409, "role_changed",
                _fixed("The role changed since it was pinned."), _pick("id", "rev", "hash")),
    # 에이전트 자격으로는 사람만 답하는 대기를 풀 수 없다. 사람의 길은 메시지에 있다.
    _FailureRow(lambda f: f.error == "person_answer_required", 403, "person_answer_required", _note_or_error),
    _FailureRow(lambda f: f.error == "run_state_protected", 403, "run_state_protected", _note_or_error),
    _FailureRow(lambda f: f.error == "request_expired", 410, "request_expired", _note_or_error),
    _FailureRow(lambda f: f.error == "invalid_expires_in", 400, "invalid_argument", _note_or_error),
    _FailureRow(lambda f: f.error == "invalid_export_format", 400, "invalid_argument", _note_or_error),
    # 계약이 읽기 예산을 넘었다. 서버 고장이 아니라 요청을 바꾸면(compact) 읽히는 일이다.
    _FailureRow(lambda f: f.error == "role_contract_too_large", 400, "role_contract_too_large",
                lambda f: f.note or "The role contract exceeds the read limit.",
                _pick("id", "bytes", "limit", "compact")),
    _FailureRow(lambda f: f.error in ("write_target_unavailable", "document_unavailable",
                                      "preview_target_unavailable"),
                404, "not_found", _fixed("The requested graph item was not found.")),
    _FailureRow(lambda f: f.error.startswith("preview_")
                and f.error not in ("preview_read_unavailable", "preview_boundary_unavailable"),
                400, _ERROR_CODE, _fixed("A complete supported preview is required before applying.")),
    _FailureRow(lambda f: f.result.get("status") in ("invalid_request", "unsupported_kind"),
                400, _ERROR_CODE, _error),
    # 유지보수 거절은 서버 내부 오류가 아니다. SDK가 재시도 가능한 503을 받아야 한다.
    _FailureRow(lambda f: f.error == "store_busy", 503, "upstream_unavailable",
                lambda f: f.note or "Store maintenance is in progress.",
                lambda _f: {"retryable": True, "retry_after_s": 1}),
    # 브리지가 사이드카와 못 이야기했다. 쓰기라면 **이미 적용됐을 수 있다** — 재시도 금지를 싣는다.
    _FailureRow(lambda f: f.error.startswith("sidecar write unconfirmed")
                or (bool(f.error) and f.result.get("write_may_have_applied") is True),
                503, "upstream_unavailable", _error,
                lambda _f: {"retryable": False, "write_may_have_applied": True}),
    _FailureRow(lambda f: f.error.startswith("sidecar HTTP 5") or f.error.startswith("sidecar unreachable"),
                503, "upstream_unavailable", _error),
    _FailureRow(lambda f: f.error == "domain_limit_reached", 409, "domain_limit_reached",
                _fixed("The space has reached its topic limit."), _pick("limit", "current", "attempted")),
    _FailureRow(lambda f: f.error.startswith(("invalid_cursor", "stale_cursor")),
                400, "invalid_argument", _error),
    _FailureRow(lambda f: f.name == "vinc_delete_edge" and "unsupported edge type" in f.reason,
                400, "invalid_argument", _note_or_error),
    _FailureRow(lambda f: f.name == "vinc_merge_nodes"
                and any(term in f.reason for term in ("supports concept", "same node")),
                400, "invalid_argument", _error_or_note),
    _FailureRow(lambda f: f.name == "vinc_doc_get" and f.result.get("verified") is False,
                503, "upstream_unavailable", _fixed("document chunks could not be verified")),
    # 주소로 물은 노드(`/v1/nodes/{id}`)나 못 읽은 노드는 없는 것이다. 자유 질의가 아무것도 못 찾은 것은
    # 오류가 아니라 빈 답이다(다음 줄).
    _FailureRow(lambda f: f.name == "vinc_brief" and f.result.get("status") == "none"
                and (f.by_id or bool(f.result.get("unreadable"))),
                404, "not_found", lambda f: f.error or "The requested graph item was not found."),
    _FailureRow(lambda f: f.name == "vinc_brief" and f.result.get("status") == "none", None),
    _FailureRow(lambda f: f.result.get("found") is False, 404, "not_found",
                lambda f: f.error or "The requested graph item was not found."),
    _FailureRow(_executed_false_with("unknown", "not found", "no such", "missing doc"),
                404, "not_found", _error_or_note),
    _FailureRow(_executed_false_with("invalid", "required", "malformed", "stale"),
                400, "invalid_argument", _error_or_note),
    _FailureRow(_executed_false_with("unavailable", "unreachable", "no store", "no reachable store"),
                503, "upstream_unavailable", _error_or_note),
    _FailureRow(lambda f: f.result.get("executed") is False, 500, "internal",
                lambda f: f.error or "The tool did not execute."),
    _FailureRow(lambda f: bool(f.result.get("isError")) or bool(f.error), 500, "internal",
                lambda f: _message(f.result)),
)


def _failure(name: str, result: dict, *, by_id: bool = False) -> tuple | None:
    """도구 결과 모양을 v1 상태로 옮긴다. 줄은 `_FAILURES` 에 순서대로 있다.
    반환은 (status, code, message) 또는 (status, code, message, details)이고, 실패가 아니면 None 이다."""
    error = str(result.get("error") or "")
    note = str(result.get("note") or "")
    facts = _Facts(name, result, by_id, error, note, (error + " " + note).lower())
    for row in _FAILURES:
        if not row.when(facts):
            continue
        if row.status is None:
            return None
        code = error if row.code is _ERROR_CODE else row.code
        message = row.message(facts)
        if row.details is None:
            return row.status, code, message
        return row.status, code, message, row.details(facts)
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
    if space != "personal" and not spaces.valid_space(space):
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
                valid_space=spaces.valid_space)
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
    spent: dict = {}
    # 호출의 값(owner · 등급 · 한도 · 자격 · 커뮤니티 콜백)은 `/mcp` 와 같은 `call_context` 가 만든다.
    outcome = toolcall.execute(route.tool, args, handler.app.bridge,
                               handler.app.call_context(resolved, spent), hub=True)
    if outcome.kind == "invalid_argument":
        fail(400, "invalid_argument", _message(outcome.result))
        return True
    if outcome.kind == "quota" and "vinc_rate" in (outcome.result or {}).get("_meta", {}):
        rate = outcome.result["_meta"]["vinc_rate"]
        fail(429, "rate_limited", _message(outcome.result), details=rate,
             extra={"Retry-After": rate["retry_after_s"]})
        return True
    if outcome.kind == "quota":
        quota = (outcome.result or {}).get("_meta", {}).get("vinc_quota", {})
        wait = int(quota.get("retry_after_s") or 0)
        fail(429, "quota_exceeded", _message(outcome.result),
             details={"limit": quota.get("limit"), "retry_after_s": wait,
                      "resets_at": quota.get("resets_at")},
             extra={"Retry-After": wait, "Vinc-Quota-Limit": quota.get("limit") or "",
                    "Vinc-Quota-Remaining": 0, "Vinc-Quota-Reset": quota.get("resets_at") or ""})
        return True
    # 도구가 결과를 냈으면(성공이든 도구가 말한 오류든) 한도를 쓴 것이라 남은 수를 싣는다.
    # 세지 않는 스페이스(Genius·면제)에는 헤더가 없다.
    meta = handler.app.quota_meta(spent)
    quota_headers = ({"Vinc-Quota-Limit": meta["limit"], "Vinc-Quota-Remaining": meta["remaining"],
                      "Vinc-Quota-Reset": meta["resets_at"] or ""} if meta else {})
    if outcome.kind != "ok":
        error = outcome.error
        message = _message(outcome.result) if outcome.result else str(error)
        if isinstance(error, community.WriteUnconfirmed):
            fail(503, "upstream_unavailable", message,
                 details={"retryable": False, "write_may_have_applied": True})
            return True
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
    if not isinstance(result, dict):
        # 사이드카가 JSON 객체가 아닌 답(목록 · 문자열)을 줬다. 요청의 잘못이 아니라 뒤쪽이 읽을 수
        # 없는 답을 낸 것이다(502). 아래 봉투와 실패 표는 객체를 전제한다.
        fail(502, "upstream_unavailable", "The graph service returned an unreadable answer.",
             extra=quota_headers)
        return True
    result = _partial_search(route.tool, result)
    failure = _failure(route.tool, result, by_id=bool(route.path_args))
    if failure:
        status, code, message, *rest = failure
        fail(status, code, message, details=rest[0] if rest else None,
             extra={**quota_headers, **({"Retry-After": "1"}
                                        if result.get("error") == "store_busy" else {})})
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
    send(status, result, quota_headers)
    return True
