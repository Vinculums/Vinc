# -*- coding: utf-8 -*-
"""셀프호스트 허브(`VINC_SELF_HOSTED=on`) — 한 사람이 자기 기계에서 돌리는 단일 테넌트.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S1. 켜면:

- 소유자는 하나다(`VINC_OWNER`, 기본 `tenant:local`). 정적 토큰·OAuth 어느 길로 들어와도
  서버가 그 소유자를 도구 인자에 강제로 싣는다 — 호출자가 보낸 `owner` 는 덮인다.
- 팀·커뮤니티 스페이스는 없다. 그것들을 겨눈 호출은 "셀프호스트에는 없다"로 답한다.
- 계정(vincs.io)과 말하지 않는다: 회원 키 검증·멤버십·커뮤니티 콜백·하루 한도·연결 흐름이
  꺼진다(`entitlement.enabled()` 가 거짓이 된다). PK 네임스페이싱도 꺼진다.
- 출력에 싣던 vincs.io 주소는 `VINC_PUBLIC_URL`(이 허브의 바깥 주소)로 바뀌거나 빠진다.

꺼져 있으면 이 모듈의 어떤 함수도 기존 동작을 바꾸지 않는다.
"""
from __future__ import annotations

import os
import re

from store.envflag import truthy

FLAG = "VINC_SELF_HOSTED"
DEFAULT_OWNER = "tenant:local"

#: 팀·커뮤니티를 겨눈 호출의 답. 없는 스페이스와 같은 말("no such space")로 답하면
#: 사람이 스페이스 id 를 고쳐 보게 되니, 이 허브에 그 기능이 없다는 것을 말한다.
NO_SPACES = ("teams and communities are not available on a self-hosted hub: it holds one "
             "personal graph. Drop the space argument.")

#: `tenant:local` 이나 `user:<public id>`. 공백·구분자가 들어간 값은 PK 합성과 필터를 깬다.
_OWNER = re.compile(r"^(?:tenant:local|user:[A-Za-z0-9._-]{1,64})$")


def enabled() -> bool:
    return truthy(os.environ.get(FLAG))


def owner() -> str:
    """고정 소유자. 모양이 틀리면 기동을 멈춘다 — 조용히 기본값으로 가면 다른 그래프에 쓴다."""
    value = (os.environ.get("VINC_OWNER") or DEFAULT_OWNER).strip()
    if not _OWNER.fullmatch(value):
        raise ValueError(f"VINC_OWNER must be tenant:local or user:<id>, got {value!r}")
    return value


def public_url() -> str | None:
    """이 허브의 바깥 주소(`https://vinc.example.com`). 없으면 None — 그때는 주소를 싣지 않는다."""
    raw = (os.environ.get("VINC_PUBLIC_URL") or "").strip().rstrip("/")
    return raw if raw.startswith(("https://", "http://")) else None


def check() -> None:
    """기동 때 한 번: 켜져 있으면 소유자 모양을 확인한다. 꺼져 있으면 아무것도 안 한다."""
    if enabled():
        owner()
