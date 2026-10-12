# -*- coding: utf-8 -*-
"""전체 그래프 읽기의 결과 캐시 — 같은 소유자·같은 인자·같은 변경 표식이면 다시 계산하지 않는다.

계산이 같은 값을 내는 조건을 **전부 키에 넣는다**:

  - 소유자: 소유자별 칸이다. 한 소유자의 항목은 다른 소유자의 읽기에 닿지 않는다.
  - 인자: `stage`·`snapshot` 만 뺀 모든 쿼리 인자. 인자가 하나라도 다르면 다른 항목이다.
  - 변경 표식(`api.changes`): 그 소유자의 쓰기 관문이 센 값. 쓰기가 성공하면 값이
    바뀌어 이전 항목은 다시 읽히지 않는다. 프로세스 세대가 앞에 붙어 재시작도 가른다.

⚠ **표식은 읽기 전에 잡아야 한다.** 계산 도중 쓰기가 끝나면 항목은 옛 표식 아래 들어가고
아무도 그 키로 다시 읽지 않는다. 읽은 뒤에 잡으면 옛 데이터가 새 표식 아래 들어간다.

⚠ 이 캐시는 표식이 세지 않는 변화를 모른다: 시간이 흘러 효력이 끝나는 주장, 표식 밖의
직접 쓰기. 그래서 항목은 `ttl` 초가 지나면 쓰지 않는다(상한을 두는 안전망이지 정합성
근거가 아니다). 특정 시점 읽기(`as_of`·`known_at`)·시드 읽기·노드 예산·검토 모드는
애초에 넣지 않는다.

값은 **직렬화한 문자열**로 둔다. 호출부가 돌려받은 사전을 고쳐도 다음 읽기에 번지지
않고, 크기를 문자 수로 잴 수 있다.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Callable

#: 이 인자가 하나라도 있으면 캐시를 쓰지 않는다. 결과가 시각·시드·예산·모드에 기대거나
#: 호출마다 모양이 달라지는 읽기들이다.
UNCACHEABLE = frozenset({"seed", "max_nodes", "as_of", "known_at", "historical",
                         "review_mirror", "debug"})
#: 키에서 빼는 인자. 단계 읽기의 제어값이라 결과를 바꾸지 않는다.
CONTROL = frozenset({"stage", "snapshot"})


def cache_key(params: dict) -> str | None:
    """이 요청의 캐시 키. 캐시할 수 없는 요청이면 None."""
    owner = params.get("owner")
    if not owner or not isinstance(owner, str):
        return None
    for name in UNCACHEABLE:
        if params.get(name) not in (None, "", 0, "0", False):
            return None
    shaped = {k: v for k, v in params.items() if k not in CONTROL}
    try:
        return json.dumps(shaped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


class GraphResultCache:
    def __init__(self, *, ttl: float = 60.0, max_entries: int = 6,
                 max_bytes: int = 48 * 1024 * 1024,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        # (owner, key) -> (marker, text, expires)
        self._items: "OrderedDict[tuple[str, str], tuple[str, str, float]]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, owner: str, key: str, marker: str) -> str | None:
        now = self._clock()
        with self._lock:
            item = self._items.get((owner, key))
            if item is None:
                self.misses += 1
                return None
            held_marker, text, expires = item
            if held_marker != marker or expires <= now:
                self._drop((owner, key))
                self.misses += 1
                return None
            self._items.move_to_end((owner, key))
            self.hits += 1
            return text

    def put(self, owner: str, key: str, marker: str, text: str) -> bool:
        size = len(text)
        if size > self.max_bytes:
            return False
        with self._lock:
            self._drop((owner, key))
            self._items[(owner, key)] = (marker, text, self._clock() + self.ttl)
            self._bytes += size
            while self._items and (len(self._items) > self.max_entries
                                   or self._bytes > self.max_bytes):
                oldest = next(iter(self._items))
                self._drop(oldest)
            return True

    def _drop(self, slot) -> None:
        item = self._items.pop(slot, None)
        if item is not None:
            self._bytes -= len(item[1])

    def clear_owner(self, owner: str) -> None:
        with self._lock:
            for slot in [s for s in self._items if s[0] == owner]:
                self._drop(slot)

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._items)
