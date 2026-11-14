"""시험 전용 단순 분류기: 어떤 행동의 키워드가 글에서 모두 '다른 낱말의 속'이거나 '슬래시가 든 이름의 한 조각'인가.

게이트의 규칙(낱말 머리 일치, 작업 브랜치 이름)을 옮기지 않고 더 느슨하게 쓴 것이다. 게이트가 main 보다 덜 거는 글에서
그 차이가 이 분류기로 설명되는지만 본다. 설명되지 않는 차이는 시험이 실패로 본다.
"""
from __future__ import annotations

import re

# 사람 전용 행동마다 글에서 찾는 낱말. push_tag 는 tag 와 push 둘 다다.
WORDS = {"merge": ("merg",), "send": ("send",), "publish": ("publish",), "migration": ("migration",),
         "push_tag": ("tag", "push")}
_TOKEN_STOP = " \t\r\n`\"'“”‘’()<>,;|"


def embedded_or_named(text: str, action: str) -> bool:
    """action 의 낱말이 하나 이상 있고 그 자리마다 다른 낱말의 속(앞에 영숫자, re·un 머리 제외)이거나 슬래시가 든 조각의 일부다."""
    low, found = text.lower(), False
    for word in WORDS[action]:
        for match in re.finditer(word, low):
            found = True
            at = match.start()
            prefixed = low[max(0, at - 2):at] in ("re", "un") and (at < 3 or not low[at - 3].isalnum())
            embedded = at > 0 and low[at - 1].isalnum() and not prefixed
            left, right = at, match.end()
            while left > 0 and low[left - 1] not in _TOKEN_STOP:
                left -= 1
            while right < len(low) and low[right] not in _TOKEN_STOP:
                right += 1
            if not (embedded or "/" in low[left:right]):
                return False
    return found


def fully_named(text: str, reference, actions) -> bool:
    """참조 게이트(main 의 person_only 읽기)가 건 문장마다, 걸린 행동의 낱말이 모두 이름의 조각이거나 다른 낱말의 속인가.

    그렇다면 이 글에서 질문이 사라진 것은 push 문장 읽기 때문이 아니라 낱말을 동사로 읽지 않기 때문이다.
    """
    seen = False
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        raised = set(reference(sentence, actions))
        if raised:
            seen = True
            if not all(embedded_or_named(sentence, a) for a in raised):
                return False
    return seen


def named_drop(text: str, action: str, reference, actions) -> bool:
    """참조 게이트가 action 을 건 문장마다 그 행동의 낱말이 모두 이름의 조각이거나 다른 낱말의 속인가(걸지 않은 문장은 보지 않는다)."""
    seen = False
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if action in set(reference(sentence, actions)):
            seen = True
            if not embedded_or_named(sentence, action):
                return False
    return seen
