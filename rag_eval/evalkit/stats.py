# -*- coding: utf-8 -*-
"""채점 도구들이 함께 쓰는 통계 둘 — 값이 같은 것만 모았다.

- :func:`mean4`: 4자리로 반올림한 평균, 빈 입력이면 ``None``.
- :func:`boot_means`: 짝지은 차이의 부트스트랩 평균들. 호출부가 백분위 칸과 반올림을 따로 고른다.

**합치지 않은 변형** (같은 이름이어도 값이 달라서 각 파일에 남겼다):

- ``kind_eval.mean``: 빈 입력이 ``0.0`` 이고 이터러블을 받는다.
- ``agent_tasks.metrics._mean``: ``sum/len`` 이라 :func:`mean4` 의 ``fmean`` 과 마지막 비트가 다를 수 있다.
- ``search._mean``: 행 목록에서 키로 뽑아 평균한다.
- ``rerank.paired_bootstrap``: 합을 ``s += x`` 로 더한다. 파이썬 3.12 의 ``sum()`` 은 보정 합산이라
  같은 입력에서도 마지막 비트가 갈릴 수 있어 :func:`boot_means` 와 같은 커널이 아니다.
- 부트스트랩의 백분위 칸은 호출부마다 다르다(``int(0.975*n)`` 와 ``int(0.975*n) - 1``). 칸을 맞추면
  구간 숫자가 움직이므로 호출부가 각자 쥔다.
"""
from __future__ import annotations

import random
import statistics


def mean4(xs: list[float]) -> float | None:
    """4자리로 반올림한 평균. 빈 입력이면 ``None``(0.0 이 아니다 — 표본 없음과 평균 0 은 다른 말이다)."""
    return round(statistics.fmean(xs), 4) if xs else None


def boot_means(deltas: list[float], n: int, rng: random.Random) -> list[float]:
    """``deltas`` 를 복원추출한 평균 ``n`` 개를 오름차순으로 돌려준다.

    ``rng`` 를 받는 이유: 한 호출 안에서 지표 여럿이 한 난수열을 이어 쓰는 호출부가 있다
    (``funnel.paired``). 뽑는 순서(``randrange(m)`` 을 m 번씩 n 회)와 ``sum`` 의 합산 순서가
    구간 숫자를 정하므로 이 식을 고치면 지난 결과와 나란히 놓을 수 없다."""
    m = len(deltas)
    return sorted(sum(deltas[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
