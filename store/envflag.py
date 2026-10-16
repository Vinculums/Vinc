"""켜고 끄는 값(환경변수·쿼리스트링·JSON)을 읽는 단 하나의 규칙.

받는 값은 대소문자와 앞뒤 공백을 무시하고 한 벌이다:

- 참: ``1`` ``true`` ``yes`` ``on``
- 거짓: ``0`` ``false`` ``no`` ``off``

이 모듈이 있는 이유: 같은 일을 하는 판정이 층마다 따로 있었고 받는 집합이 셋이었다.
그래서 운영자가 ``VINC_X=yes`` 를 주면 어떤 층은 켜고 어떤 층은 무시했다. 플래그를
새로 읽는 자리는 여기를 부른다. 집합을 바꾸려면 여기서 바꾼다.

``store/`` 는 맨 아래 층이라(``scripts/gate_ownership.py`` 의 ``LAYER_BANS``) 어느
패키지든 이것을 가져다 쓸 수 있다.
"""
from __future__ import annotations

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
