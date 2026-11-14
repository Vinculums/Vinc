# -*- coding: utf-8 -*-
"""허브 HTTP 의 두 문이 함께 쓰는 본문 상한과 JSON 본문 읽기.

`http_server`(`/mcp`·`/internal/*`)와 `rest_api`(`/v1`)가 이 모듈을 import 한다. 이 모듈은 둘 중
어느 것도 import 하지 않는다: `rest_api` 는 `http_server` 를 import 하지 않고 여기서 상한을 읽는다.
바이트 읽기는 `store/httpbody.read_body`(사이드카와 같은 규칙: 상한을 넘으면 읽지 않는다)를 쓴다.
"""
from __future__ import annotations

import json

from store import httpbody

#: 허브가 받는 요청 본문의 상한(4 MB). `/mcp` 는 넘는 본문을 빈 본문으로, `/v1` 은 413 으로 답한다.
MAX_BODY = 4 * 1024 * 1024


class BodyRefused(ValueError):
    """`read_json_body` 가 본문을 받지 않았다. `reason` 은 셋 중 하나다.

    - too_large: `Content-Length` 가 상한을 넘었다. 본문은 읽지 않았다.
    - bad_length: `Content-Length` 가 정수가 아니거나 음수다.
    - not_object: 본문이 JSON 이 아니거나 JSON 객체가 아니다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def read_json_body(headers, rfile, limit: int = MAX_BODY) -> dict:
    """`Content-Length` 만큼 읽어 JSON 객체로 돌려준다. 길이가 없거나 0 이면 `{}` 이고 읽지 않는다.

    받지 않는 본문은 `BodyRefused` 하나로 알린다. 그것을 어떤 상태 코드로 낼지는 부르는 문이 정한다.
    ⚠ 해석은 `json.loads(bytes)` 다: UTF-8 · UTF-16 · UTF-32 와 UTF-8 BOM 을 받는다. UTF-8 만 받는
    `httpbody.parse_json` 으로 바꾸면 `/v1` 이 지금 받는 본문을 거절하게 된다.
    ⚠ 길이가 0 보다 큰데 본문이 비어 오면(연결이 먼저 닫힘) `{}` 가 아니라 not_object 다."""
    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError:
        raise BodyRefused("bad_length") from None
    if length > limit:
        raise BodyRefused("too_large")
    if length < 0:
        raise BodyRefused("bad_length")
    if length == 0:
        return {}
    raw = httpbody.read_body(headers, rfile, limit)
    try:
        body = json.loads(raw)
    except ValueError:
        raise BodyRefused("not_object") from None
    if not isinstance(body, dict):
        raise BodyRefused("not_object")
    return body
