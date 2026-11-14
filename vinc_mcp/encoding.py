# -*- coding: utf-8 -*-
"""MCP 도구 결과와 REST v1 본문을 JSON 문자열로 만드는 인코더 하나.

`rpc`(MCP 결과 문자열)와 `rest_api`(v1 본문)가 이것을 쓴다. 역할 계약의 읽기 예산(`roles.CONTRACT_CAP`)도
이 인코딩으로 잰다: 예산과 실제로 보내는 바이트가 같아야 한다. 이 모듈은 다른 `vinc_mcp` 모듈을
import 하지 않는다.
"""
from __future__ import annotations

import json


def serialize_result(value) -> str:
    """결과 하나를 JSON 문자열로. 필드는 바꾸지 않는다.

    역할(`kind: role`, 또는 `role` 칸에 역할을 실은 결과)은 공백 없는 구분자(`,` · `:`)로 쓴다.
    역할의 10 KB 예산을 그 인코딩으로 재므로, 구분자를 바꾸면 예산 안이던 역할이 넘친다.
    나머지는 `json.dumps` 의 기본 구분자다. 둘 다 `ensure_ascii=False` 다."""
    role = isinstance(value, dict) and (value.get("kind") == "role" or
        isinstance(value.get("role"), dict) and value["role"].get("kind") == "role")
    return json.dumps(value, ensure_ascii=False, **({"separators": (",", ":")} if role else {}))
