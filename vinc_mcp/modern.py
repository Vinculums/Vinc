# -*- coding: utf-8 -*-
"""MCP 모던 리비전(요청마다 `_meta` 로 버전과 능력을 싣는 세대)의 순수 함수들.

`initialize` 핸드셰이크 세대는 `rpc.py` 가 지금 그대로 처리한다. 여기 있는 것은 요청 한
건이 모던인지 가리고, 모던이면 결과에 붙일 필드와 거절 봉투를 만드는 일뿐이다. 전송
(stdio·HTTP)과 무관하고 아무 상태도 들지 않는다.

- 세대는 **요청마다** 정한다. `_meta` 의 `io.modelcontextprotocol/protocolVersion` 이
  `MODERN_VERSIONS` 안이면 모던이고, 키가 없거나 핸드셰이크 세대 리비전이면 지금처럼
  처리한다. `initialize` 는 늘 핸드셰이크 세대다. 그 밖의 값은 UnsupportedProtocolVersion.
- 모던 결과에만 `resultType`·서버 신원·캐시 힌트를 싣는다. 핸드셰이크 세대 응답은 한
  글자도 바뀌지 않는다: 협상한 리비전이 모르는 키를 받으면 끊는 클라이언트가 있다.
  근거: [[record:what-the-spec-permits-is-not-what-the-peer-accepts]]
- 설계 근거(세대 판별, 지원 목록, 캐시 힌트 값, MRTR 범위):
  [[decision:mcp-modern-era-is-served-per-request-beside-the-initialize-era]]
"""
from __future__ import annotations

import base64
import binascii
from typing import NamedTuple

from . import __version__

#: 요청마다 `_meta` 로 말하는 리비전. 최신이 앞.
MODERN_VERSIONS = ("2026-07-28",)

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

#: 스펙이 예약한 오류 코드(-32020..-32099). 이 셋 말고는 이 구간을 쓰지 않는다.
HEADER_MISMATCH = -32020
MISSING_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
INVALID_PARAMS = -32602

#: `Mcp-Name` 헤더가 본문의 무엇을 비추는가.
_NAME_FIELD = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}

#: 캐시 힌트 `(ttlMs, cacheScope)`. 도구 목록은 배포 때만 바뀌고 호출자 모두에게 같다.
#: 프롬프트 목록은 그래프의 역할에서 오므로 소유자마다 다르다.
CACHE_HINTS = {
    "tools/list": (3_600_000, "public"),
    "prompts/list": (60_000, "private"),
}
#: `server/discover` 의 `instructions` 는 소유자와 팀 목록을 담으므로 허브에서는 private.
DISCOVER_TTL_MS = 300_000

#: MRTR 로 클라이언트에 물을 수 있는 요청. sampling·roots 는 스펙이 폐기 예정으로 돌려서
#: 새로 열지 않는다.
INPUT_REQUEST_METHODS = ("elicitation/create",)


class Rejection(NamedTuple):
    """모던 요청의 거절. HTTP 에서는 `status` 로, stdio 에서는 봉투만 나간다."""
    code: int
    message: str
    data: dict | None = None
    status: int = 400

    def reply(self, msg_id) -> dict:
        err: dict = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def supported_versions(handshake_versions) -> list[str]:
    """`server/discover` 와 UnsupportedProtocolVersion 이 싣는 목록. 모던이 앞이다."""
    return list(MODERN_VERSIONS) + [v for v in handshake_versions if v not in MODERN_VERSIONS]


def _meta_of(msg: dict) -> dict | None:
    params = msg.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return meta if isinstance(meta, dict) else None


def requested_version(msg: dict):
    """요청 `_meta` 의 프로토콜 버전. 키가 없으면 None(핸드셰이크 세대)."""
    meta = _meta_of(msg)
    return None if meta is None else meta.get(META_VERSION)


def unsupported(requested: str, handshake_versions) -> Rejection:
    return Rejection(UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version",
                     {"supported": supported_versions(handshake_versions),
                      "requested": requested})


def classify(msg: dict, handshake_versions) -> tuple[str | None, Rejection | None]:
    """`(모던 버전, 거절)`. 둘 다 None 이면 핸드셰이크 세대로 지금처럼 처리한다."""
    if msg.get("method") == "initialize":
        return None, None
    want = requested_version(msg)
    if want is None or want in handshake_versions:
        return None, None
    if not isinstance(want, str) or not want:
        return None, Rejection(INVALID_PARAMS, f"_meta {META_VERSION} must be a version string")
    if want not in MODERN_VERSIONS:
        return None, unsupported(want, handshake_versions)
    if not isinstance(_meta_of(msg).get(META_CAPABILITIES), dict):
        return None, Rejection(INVALID_PARAMS,
                               f"missing required _meta field {META_CAPABILITIES}")
    return want, None


def server_info() -> dict:
    """모던 결과 `_meta` 의 서버 신원. 핸드셰이크 세대 serverInfo 와 같은 두 필드다."""
    return {"name": "vinc", "version": __version__}


def complete(result: dict) -> dict:
    """모던 결과 봉투. 사본을 만들어 `resultType` 과 서버 신원을 싣는다.

    이미 `resultType` 이 있으면(MRTR 의 input_required) 그대로 둔다. 다른 `_meta` 키
    (`vinc_quota` 등)는 보존한다. 사본인 이유: 내장 프롬프트처럼 모듈이 들고 있는 dict 가
    결과로 나가는 경우가 있어, 제자리에서 고치면 다음 핸드셰이크 세대 응답에 새 키가 샌다."""
    out = dict(result)
    out.setdefault("resultType", "complete")
    meta = dict(out["_meta"]) if isinstance(out.get("_meta"), dict) else {}
    meta.setdefault(META_SERVER_INFO, server_info())
    out["_meta"] = meta
    return out


def with_cache_hints(result: dict, method: str) -> dict:
    """목록 결과에 `ttlMs`·`cacheScope`. 표에 없는 메서드는 그대로."""
    hint = CACHE_HINTS.get(method)
    if hint is None:
        return result
    return {**result, "ttlMs": hint[0], "cacheScope": hint[1]}


# ── Streamable HTTP 헤더 ───────────────────────────────────────────────────

def decode_header_value(value: str) -> str:
    """`=?base64?...?=` 표식이면 풀고, 아니면 그대로. 못 풀면 ValueError."""
    if value.startswith("=?base64?") and value.endswith("?=") and len(value) >= 11:
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise ValueError(f"bad base64 header value: {e}") from e
    return value


def encode_header_value(value: str) -> str:
    """헤더에 그대로 못 싣는 값(비ASCII·제어문자·앞뒤 공백·표식 모양)은 base64 표식으로."""
    plain = (value == value.strip()
             and all(c == "\t" or 0x20 <= ord(c) <= 0x7E for c in value)
             and not (value.startswith("=?base64?") and value.endswith("?=")))
    if plain:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def check_headers(headers, msg: dict, version: str | None) -> Rejection | None:
    """모던 POST 의 헤더가 본문과 맞는가. `headers` 는 대소문자 무관 `.get` 을 가진 것.

    `version` 은 `classify` 가 돌려준 모던 버전이다. None(핸드셰이크 세대)일 때 볼 것은
    하나뿐이다: 헤더가 모던 리비전을 말하면서 본문에 `_meta` 버전이 없으면 어긋남이다."""
    header = headers.get("MCP-Protocol-Version")
    if version is None and header not in MODERN_VERSIONS:
        return None
    if header != version:
        return Rejection(HEADER_MISMATCH,
                         f"Header mismatch: MCP-Protocol-Version header {header!r} does not "
                         f"match body value {requested_version(msg)!r}")
    method = msg.get("method")
    got = headers.get("Mcp-Method")
    if got != method:
        return Rejection(HEADER_MISMATCH,
                         f"Header mismatch: Mcp-Method header {got!r} does not match body "
                         f"value {method!r}")
    field = _NAME_FIELD.get(str(method))
    if field is None:
        return None
    params = msg.get("params")
    body = params.get(field) if isinstance(params, dict) else None
    raw = headers.get("Mcp-Name")
    try:
        name = None if raw is None else decode_header_value(raw)
    except ValueError as e:
        return Rejection(HEADER_MISMATCH, f"Header mismatch: Mcp-Name: {e}")
    if name != body:
        return Rejection(HEADER_MISMATCH,
                         f"Header mismatch: Mcp-Name header {name!r} does not match body "
                         f"value {body!r}")
    return None


def request_headers(msg: dict) -> dict:
    """모던 요청을 HTTP 로 보낼 때 실을 표준 헤더. 핸드셰이크 세대 메시지에는 빈 dict."""
    version = requested_version(msg)
    if version not in MODERN_VERSIONS:
        return {}
    out = {"MCP-Protocol-Version": version, "Mcp-Method": str(msg.get("method") or "")}
    field = _NAME_FIELD.get(str(msg.get("method")))
    params = msg.get("params")
    name = params.get(field) if field and isinstance(params, dict) else None
    if isinstance(name, str):
        out["Mcp-Name"] = encode_header_value(name)
    return out


# ── MRTR: 클라이언트에게 되묻는 결과 ────────────────────────────────────────

class MissingClientCapability(ValueError):
    """되묻기에 필요한 능력을 클라이언트가 선언하지 않았다. `rejection()` 이 -32021 봉투."""

    def __init__(self, required: dict):
        super().__init__(f"client did not declare {sorted(required)}")
        self.required = required

    def rejection(self) -> Rejection:
        return Rejection(MISSING_CLIENT_CAPABILITY,
                         "Server requires a client capability this request did not declare",
                         {"requiredCapabilities": self.required})


def _require_elicitation(params: dict, caps: dict) -> None:
    mode = params.get("mode") or "form"
    declared = caps.get("elicitation")
    # `elicitation: {}` 는 form 만 선언한 것이다(스펙의 암묵 기본).
    ok = isinstance(declared, dict) and (
        mode in declared or (mode == "form" and not declared))
    if not ok:
        raise MissingClientCapability({"elicitation": {mode: {}}})


def input_required(input_requests: dict | None = None, *, request_state: str | None = None,
                   client_capabilities: dict | None = None) -> dict:
    """MRTR 의 `input_required` 결과 하나를 만든다. 아직 어느 도구에도 연결되어 있지 않다.

    - `input_requests`: `{키: {"method": "elicitation/create", "params": {...}}}`.
      키는 이 요청 안에서 유일한 문자열이다.
    - `request_state`: 클라이언트가 그대로 되돌려 줄 불투명 문자열. **공격자가 정할 수
      있는 입력**이므로, 권한이나 처리에 영향을 주면 부르는 쪽이 무결성 보호(HMAC 등)와
      주체·만료·원요청 결속을 붙여서 넘긴다. 이 함수는 그 값을 해석하지 않는다.
    - `client_capabilities`: 요청 `_meta` 의 능력. 선언 안 된 되묻기는 싣지 않고
      `MissingClientCapability` 로 거절한다(스펙 MUST NOT).

    둘 중 하나는 있어야 한다. 캐시 힌트는 싣지 않는다(중간 결과는 캐시 대상이 아니다).
    `resultType` 이 이미 있으므로 `complete()` 를 거쳐도 덮이지 않는다."""
    if not input_requests and not request_state:
        raise ValueError("input_required needs inputRequests or requestState")
    if request_state is not None and (not isinstance(request_state, str) or not request_state):
        raise ValueError("requestState must be a non-empty string")
    out: dict = {"resultType": "input_required"}
    if input_requests:
        if not isinstance(input_requests, dict):
            raise ValueError("inputRequests must be an object keyed by request id")
        caps = client_capabilities if isinstance(client_capabilities, dict) else {}
        reqs: dict = {}
        for key, req in input_requests.items():
            if not isinstance(key, str) or not key:
                raise ValueError("inputRequests keys must be non-empty strings")
            method = req.get("method") if isinstance(req, dict) else None
            if method not in INPUT_REQUEST_METHODS:
                raise ValueError(f"inputRequests[{key!r}]: unsupported method {method!r}")
            params = req.get("params")
            if not isinstance(params, dict):
                raise ValueError(f"inputRequests[{key!r}]: params must be an object")
            _require_elicitation(params, caps)
            reqs[key] = {"method": method, "params": params}
        out["inputRequests"] = reqs
    if request_state:
        out["requestState"] = request_state
    return out
