"""스페이스(그래프 owner)마다 짧은 창 안의 호출 수를 막는 버스트 상한.

하루 한도와 달리 모든 플랜에 같다: Genius 의 "하루 상한 없음"은 이 상한 위에서만 성립한다.
카운터는 프로세스 메모리에만 둔다. 허브는 레플리카 하나이고, 배포 중 두 리비전이 겹치는
짧은 동안 상한이 리비전마다 따로 세지는 것은 버스트 방어로는 문제가 없다.
근거: [[vinc.plan.plans-and-limits-redesign-2026-10-17 §2.5]]
"""
from __future__ import annotations

import threading
import time
from collections import deque


class BurstLimiter:
    """owner 마다 최근 `window_s` 초 안의 호출이 `limit` 를 넘지 않게 한다(미끄러지는 창)."""

    def __init__(self, limit: int, window_s: float = 60.0, max_owners: int = 10_000):
        if limit <= 0:
            raise ValueError("limit must be positive")
        self.limit, self.window_s, self._max_owners = limit, window_s, max_owners
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def take(self, owner: str, now: float | None = None) -> float:
        """한 번 쓴다. 허용이면 0.0, 거부면 다시 부를 수 있을 때까지의 초(쓰지 않는다)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(owner)
            if hits is None:
                if len(self._hits) >= self._max_owners:
                    self._forget_idle(t)
                hits = self._hits[owner] = deque()
            while hits and t - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window_s - (t - hits[0])
            hits.append(t)
            return 0.0

    def _forget_idle(self, t: float) -> None:
        # 창 밖으로 나간 owner 만 지운다. 인증된 owner 만 여기 오므로 수는 계정 수로 묶인다.
        for owner in [o for o, h in self._hits.items() if not h or t - h[-1] >= self.window_s]:
            del self._hits[owner]
