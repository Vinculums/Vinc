# -*- coding: utf-8 -*-
"""토픽 이동 울타리의 상태: 스토어의 울타리 깃발, 완료 증명 집합, `VINC_TOPIC_CONVERSION` 읽기.

울타리는 이동 중인 원본·대상 소유자의 읽기와 쓰기를 막는다(`api.topic_conversion.check_read`·`check_write`).
그 검사를 할지는 스토어마다 깃발 하나로 정한다. 깃발은 스토어가 열릴 때 서고(환경이 켜졌거나 저널이 이미
있을 때, `store.embedded_backend`), 저널을 처음 쓸 때 선다(:func:`activate`). 깃발이 없는 스토어(다른 백엔드,
시험 대역)는 검사하지 않는다.

완료 증명 집합은 이 프로세스가 영속 게시를 확인한 3판 완료 저널(정본 JSON)이다. 그 저널만 울타리를 푼다.
처음 더할 때 writer 스토어에 만들고, 리더 뷰는 writer 의 것을 본다.

환경 `VINC_TOPIC_CONVERSION=1` 은 허브 배포가 켠다(`.github/workflows/hub-deploy.yml`). 새 이동의 조사(preflight)를
허락하고, 전환 영속 확인과 범위 복원을 켜며, 스토어를 열 때부터 깃발을 세운다.
"""
from __future__ import annotations

import os

#: 스토어 객체에 얹는 깃발과 집합의 속성 이름.
FLAG = "_topic_conversion_guard"
TERMINALS = "_topic_conversion_terminals"
#: 새 이동을 켜는 환경 변수.
ENV = "VINC_TOPIC_CONVERSION"


def enabled_by_env() -> bool:
    """`VINC_TOPIC_CONVERSION` 이 정확히 `1` 인가. 부를 때마다 읽는다."""
    return os.environ.get(ENV) == "1"


def guard_active(store) -> bool:
    """이 스토어가 이동 울타리를 검사해야 하는가. 깃발이 없는 스토어는 아니다."""
    return bool(getattr(store, FLAG, False))


def activate(store) -> None:
    """깃발을 세운다. 저널을 처음 쓸 때 부르고, 이 뒤로 그 스토어의 읽기·쓰기가 울타리를 거친다."""
    setattr(store, FLAG, True)


def terminals(store) -> set:
    """완료 증명 집합. 아직 없으면 빈 집합을 돌려주고 스토어에 만들지 않는다."""
    return getattr(store, TERMINALS, set())


def add_terminal(store, journal_json: str) -> None:
    """영속 게시를 확인한 완료 저널(정본 JSON)을 더한다. 집합이 없으면 이 스토어에 만든다."""
    if not hasattr(store, TERMINALS):
        setattr(store, TERMINALS, set())
    getattr(store, TERMINALS).add(journal_json)
