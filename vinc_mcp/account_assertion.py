# -*- coding: utf-8 -*-
"""계정이 서명해 허브의 `/internal/*` 경로로 보내는 어서션을 받는 검사 하나.

`/internal/*` 는 공개 도달 가능하고 남은 관문이 이 서명 하나뿐이다. 그래서 검사를 경로마다
따로 적지 않고 `verify` 한 곳에서 같은 차례로 한다:

  1. 계정 공개키로 서명과 수명(≤2분)을 본다.
  2. `typ` 가 그 경로가 받는 것이고 `iss=account` · `aud=mcp` 인가.
  3. 서명된 본문 지문 `bh` 가 받은 바이트와 같은가(`body_matches`).
  4. 한 번만 쓰는 `jti` 를 처음 보는가(경로가 jti 를 소비할 때만, `claim_jti`).

앞의 검사가 거절하면 뒤의 검사는 돌지 않는다. 그래서 `typ` 이나 `bh` 로 거절된 어서션은 jti 를
태우지 않는다. 이 차례를 바꾸면 경로의 답이 바뀐다(`tests/mcp/test_internal_assertion_table.py`).

거절은 전부 PermissionError 다. 허브 핸들러는 그것을 사유 없이 401 `unauthorized` 로 답한다.
어느 필드가 틀렸는지 서명 없는 호출자에게 알리지 않기 위해서다.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Callable

from . import edverify

#: 서명 시각 `iat` 에서 이만큼(초) 지난 어서션은 `exp` 가 남아 있어도 거절한다.
MAX_AGE_S = 120


def verify(public: bytes, assertion: str, raw: bytes | None, typs: tuple[str, ...], *,
           refusal: str, once: Callable[[dict], bool] | None) -> dict:
    """서명·수명·`typ/iss/aud`·본문 지문·1회용 jti 를 차례로 보고 맞으면 payload 를 돌려준다.

    `typs` 는 그 경로가 받는 `typ` 전부다. `refusal` 은 서명·수명·`typ/iss/aud` 가 틀렸을 때의
    PermissionError 문구이고 경로마다 다르다. `once` 는 jti 를 소비하는 함수이고, `None` 은 jti 를
    보지 않는다는 뜻이다: 읽기라서 재생해도 바뀌는 것이 없는 경로(`/internal/quota`)만 그렇게 부른다.
    `once` 에 기본값을 두지 않는다. 경로마다 jti 를 소비하는지를 부르는 자리에서 적게 한다."""
    payload = edverify.verify_assertion(public, assertion or "", max_age=MAX_AGE_S)
    if not payload or payload.get("typ") not in typs \
            or payload.get("iss") != "account" or payload.get("aud") != "mcp":
        raise PermissionError(refusal)
    if not body_matches(payload, raw):
        raise PermissionError("request body does not match the signed digest")
    if once is not None and not once(payload):
        raise PermissionError("this approval was already used")
    return payload


def body_matches(payload: dict, raw: bytes | None) -> bool:
    """서명된 지문과 받은 본문이 같은가. `bh` 가 없거나 비었으면 거절이다.

    어서션은 owner 와 op 를 묶지만 **인자는 서명되지 않은 본문에서 읽힌다.** 지문을 대조하지
    않으면 잡은 어서션을 유효 창 안에 다른 인자로 재생할 수 있다. 빈 본문도 해시해서 대조한다
    (건너뛰지 않는다).

    ⚠ `bh` 를 선택으로 바꾸지 않는다. 되돌릴 일이 생기면(계정을 `bh`·`jti` 를 싣지 않는 판으로
    롤백하는 경우뿐이다) 여기를 `return True` 로 되돌리는 것이 아니라 **계정을 먼저 되돌린다.**
    순서를 뒤집으면 그 사이 창이 곧 구멍이다."""
    want = payload.get("bh")
    if not isinstance(want, str) or not want:
        return False
    got = base64.urlsafe_b64encode(
        hashlib.sha256(raw or b"").digest()).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(got, want)


def claim_jti(seen: dict, payload: dict) -> bool:
    """이 어서션을 처음 쓰는가. `jti` 는 **필수다**(없으면 거절, 이유는 `body_matches` 와 같다).

    `seen` 은 이미 쓴 jti → 기억을 끝낼 시각이다. 부를 때마다 끝난 항목을 지워 표가 자라지 않게
    한다. 표를 어디에 얼마나 오래 두는가는 부르는 쪽이 정한다."""
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        return False
    now = time.time()
    for k, exp in [(k, e) for k, e in seen.items() if e <= now]:
        seen.pop(k, None)
    if jti in seen:
        return False
    # 창보다 조금 길게 잡는다 — 시계 어긋남으로 만료가 앞당겨지면 재생이 열린다.
    seen[jti] = now + 180
    return True
