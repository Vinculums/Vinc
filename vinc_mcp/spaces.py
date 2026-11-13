# -*- coding: utf-8 -*-
"""스페이스 id 의 모양과, 고른 스페이스가 있을 수 있는 값인지 보는 술어 하나.

`/mcp` 의 도구 호출·Events 구독(`http_server`)과 REST v1 의 쿼리 `space`·도구 게이트(`rest_api`)가
이 술어 하나를 쓴다. 주소(`/t/<uuid>/mcp`)와 인자(`space`)도 같은 UUID 모양을 쓴다: 두 벌로 두면
한쪽만 조여도 다른 쪽이 뚫린 채 남는다.
"""
from __future__ import annotations

import re

from . import community

#: 팀 스페이스 id(UUID)의 모양. 대소문자를 다 받는다(부르는 쪽이 소문자로 맞춰 넘긴다).
UUID = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
UUID_RE = re.compile(r"\A" + UUID + r"\Z")


def valid_space(value: str) -> bool:
    """팀 UUID 이거나 `community:<uuid>` 인가. 모양만 본다: 있는지·멤버인지는 계정이 답한다.

    `personal` 은 거짓이다. 개인 그래프는 스페이스를 고르지 않은 것과 같아서 부르는 쪽이 먼저
    가른다. 문자열만 받는다: 문자열이 아니면 `TypeError` 다(부르는 자리는 모두 정규화한
    문자열을 넘긴다)."""
    return bool(UUID_RE.fullmatch(value)) or community.is_space(value)
