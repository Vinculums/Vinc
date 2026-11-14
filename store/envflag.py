"""켜고 끄는 값(환경변수·쿼리스트링·JSON)을 읽는 단 하나의 규칙.

받는 값은 대소문자와 앞뒤 공백을 무시하고 한 벌이다:

- 참: ``1`` ``true`` ``yes`` ``on``
- 거짓: ``0`` ``false`` ``no`` ``off``

층마다 받는 집합이 다르면 운영자가 같은 ``VINC_X=yes`` 를 줘도 어떤 층은 켜고 어떤
층은 무시한다. 그래서 플래그를 읽는 자리는 모두 여기를 부르고, 집합을 바꾸려면
여기서만 바꾼다.

값 하나를 읽는 함수(``truthy``·``truthy_strict``)와, 환경변수 이름을 받아 읽는 함수
(``default_on``·``default_off``·``strict``·``one_of``·``positive_int``)가 있다. 이름을 받는
쪽은 환경변수가 없을 때와 비었을 때의 답을 한 곳에 못 박아 둔다.

``store/`` 는 맨 아래 층이라(``scripts/gate_ownership.py`` 의 ``LAYER_BANS``) 어느
패키지든 이것을 가져다 쓸 수 있다.
"""
from __future__ import annotations

import os
from collections.abc import Collection

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _text(value: object) -> str:
    return str(value).strip().lower()


def truthy(value: object, default: bool = False) -> bool:
    """참 집합에 들면 참. 비었거나(``None``·빈 문자열·공백) 없으면 ``default``.

    그 밖의 값(``off``·오타 포함)은 거짓이다. 진짜 ``bool`` 은 그대로 돌려준다:
    HTTP 로는 ``"false"`` 문자열이, MCP/CLI 로는 bool 이 오는데 ``bool("false")`` 는
    참이라서다."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = _text(value)
    if not text:
        return default
    return text in TRUE_VALUES


def truthy_strict(value: object) -> bool | None:
    """참 집합이면 참, 거짓 집합이면 거짓, 그 밖(빈 값 포함)은 ``None``.

    모르는 값을 오류로 돌려야 하는 자리(REST 파라미터 검증 등)가 쓴다."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = _text(value)
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def default_on(name: str) -> bool:
    """환경변수 ``name`` 이 거짓 집합에 들 때만 거짓. 없거나 비었거나 오타여도 참이다.

    켜 둔 채로 쓰는 기능의 비상구용이다: 끄려면 ``off`` 같은 거짓 낱말을 줘야 하고,
    ``of`` 같은 오타는 끄지 못한다."""
    return truthy_strict(os.environ.get(name)) is not False


def default_off(name: str) -> bool:
    """환경변수 ``name`` 이 참 집합에 들 때만 참. 없거나 비었거나 오타면 거짓이다."""
    return truthy(os.environ.get(name))


def strict(name: str, default: bool) -> bool:
    """환경변수 ``name`` 을 참/거짓 집합으로만 읽는다. 없으면 ``default``, 집합 밖의 값은 ``ValueError``.

    오타가 조용히 한쪽으로 기울면 안 되는 설정용이다(``default_on`` 은 오타를 켬으로,
    ``default_off`` 는 끔으로 읽는다). 비어 있거나 공백뿐인 값도 설정된 값이므로 오류다.
    메시지가 변수 이름과 받는 값을 말한다."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    parsed = truthy_strict(raw)
    if parsed is None:
        raise ValueError(
            f"{name}={raw!r} 는 알 수 없는 값이다 — 켜려면 {'/'.join(sorted(TRUE_VALUES))}, "
            f"끄려면 {'/'.join(sorted(FALSE_VALUES))} 중 하나를 준다(대소문자·앞뒤 공백 무시). "
            f"설정하지 않으면 {'켜짐' if default else '꺼짐'}이다")
    return parsed


def one_of(name: str, allowed: Collection[str], default: str) -> str:
    """환경변수 ``name`` 의 값을 앞뒤 공백·대소문자 없이 읽어 ``allowed`` 에 들면 그 값, 아니면 ``default``.

    ``allowed`` 와 ``default`` 는 소문자로 적는다. 없거나 비었거나 모르는 값은 ``default`` 다."""
    value = (os.environ.get(name) or default).strip().lower()
    return value if value in allowed else default


def positive_int(name: str, default: int) -> int:
    """환경변수 ``name`` 을 1 이상의 정수로 읽는다. 없거나 비었거나 정수가 아니면 ``default``.

    0 이하는 1 로 올린다(``default`` 로 돌리지 않는다)."""
    raw = str(os.environ.get(name, "")).strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default
