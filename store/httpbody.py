# -*- coding: utf-8 -*-
"""HTTP 요청 본문 읽기 — 허브(`vinc_mcp/http_server.py`)와 사이드카(`api/server.py`)가 함께 쓴다.

`store/` 에 두는 이유는 층이다: `vinc_mcp` 는 `api` 를 import 할 수 없다
(`scripts/gate_ownership.py` 의 `LAYER_BANS`). 오류를 어떻게 응답으로 바꿀지(허브는 빈
dict 로 강등, 사이드카는 413·400)는 호출부가 정한다. 여기는 읽기와 해석만 한다.
"""
from __future__ import annotations

import json

__all__ = ["BodyTooLarge", "read_body", "parse_json"]


class BodyTooLarge(ValueError):
    """`Content-Length` 가 상한을 넘었다. 본문은 **읽지 않았다**."""

    def __init__(self, length: int, limit: int):
        super().__init__(f"request body {length} bytes exceeds the {limit} byte limit")
        self.length = length
        self.limit = limit


def read_body(headers, rfile, limit: int) -> bytes:
    """`Content-Length` 만큼 읽어 바이트로 돌려준다. 길이가 없거나 0 이하면 `b""`.

    상한을 넘으면 **한 바이트도 읽지 않고** `BodyTooLarge` 를 던진다(읽으면 그게 메모리
    공격 통로다). 읽지 않은 바이트가 소켓에 남으므로 keep-alive 연결이면 호출부가 연결을
    닫아야 한다. 안 닫으면 그 잔여물이 다음 요청으로 파싱된다(요청 밀수)."""
    n = int(headers.get("Content-Length") or 0)
    if n <= 0:
        return b""
    if n > limit:
        raise BodyTooLarge(n, limit)
    return rfile.read(n)


def parse_json(raw: bytes):
    """본문 바이트 → JSON 값. 빈 본문은 `{}`.

    UTF-8 이 아니면 `UnicodeDecodeError`, JSON 이 아니면 `json.JSONDecodeError` 를 던진다
    (둘 다 `ValueError`). `json.loads(bytes)` 의 인코딩 추정에 맡기지 않는다: CP949 본문이
    다른 무엇으로 읽히면 오류 대신 깨진 값이 저장된다."""
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))
