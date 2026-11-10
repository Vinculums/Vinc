# -*- coding: utf-8 -*-
"""쓰기 대기 집계 — 단일 쓰기 워커 앞에서 어떤 쓰기가 어떤 쓰기 뒤에서 얼마나 기다렸나.

엔진은 쓰기 트랜잭션을 한 번에 하나만 받고, 사이드카는 모든 뮤테이션을 워커 하나
(`api.jobs.JobQueue`)로 직렬화한다. 이 모듈은 그 큐에서 일어난 일을 **수로만** 센다:

- 종류(kind)별로 큐 대기 시간과 실행 시간의 고정 구간 히스토그램, 누가 기다렸는지
  (`run_sync` = 사람이나 호출자가 붙잡고 기다림, `submit` = 폴링), 오류·시간 초과 수.
- **누구 뒤에서 기다렸나**: 기다리는 동안 워커가 돌린 일(잡과 잡 사이의 후처리 틱 포함)의
  겹친 시간을 (기다린 종류, 앞에서 돈 종류) 쌍에 더한다. 대기 시간은 이 쌍들의 합이 된다.
- DDL 가드의 배타 보유 시간(그때 돌던 잡의 종류로 묶는다)과 읽기가 `store_busy` 로
  거절된 수.

요청 재료(본문·id·소유자)는 담지 않는다 — `store.request_timing` 과 같은 규칙이다.
메모리는 종류 수와 쌍 수에 상한을 둬서 묶는다. 넘친 것은 `other` 로 접힌다.
근거: 그래프 문서 `vinc.plan.write-contention-measurement-2026-11-10`
"""
from __future__ import annotations

import contextvars
import json
import sys
import threading
import time
from contextlib import contextmanager

#: 히스토그램 구간 상한(ms). 마지막 칸은 그보다 긴 것 전부다.
BUCKETS_MS = (10, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)
#: 종류·쌍·DDL 라벨 상한. 잡 종류는 코드가 정하는 고정 문자열이라 보통 40개 안쪽이다.
MAX_KINDS = 64
MAX_PAIRS = 256
OTHER = "other"
#: 잡과 잡 사이에 워커가 도는 후처리(영속 스냅샷 등)의 이름. 그것도 쓰기를 막는다.
IDLE_TICK = "idle-tick"
#: 큐 밖에서 잡은 DDL 배타(워커 스레드가 아닌 곳)의 라벨.
OUTSIDE_QUEUE = "outside-queue"
#: 집계 로그 한 줄의 간격(초). 허브는 재시작마다 메모리가 비므로 로그가 이력이다.
LOG_EVERY_S = 3600.0
LOG_PREFIX = "[write-waits]"

#: 워커가 지금 돌리는 잡의 종류. DDL 가드가 배타 보유를 그 종류로 묶는 데 쓴다.
current_kind: contextvars.ContextVar[str] = contextvars.ContextVar("vinc_write_kind",
                                                                  default=OUTSIDE_QUEUE)


class _Hist:
    __slots__ = ("counts", "n", "total", "max")

    def __init__(self):
        self.counts = [0] * (len(BUCKETS_MS) + 1)
        self.n = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, ms: float) -> None:
        ms = max(0.0, ms)
        i = next((i for i, b in enumerate(BUCKETS_MS) if ms <= b), len(BUCKETS_MS))
        self.counts[i] += 1
        self.n += 1
        self.total += ms
        self.max = max(self.max, ms)

    def quantile(self, q: float) -> float:
        """q 분위가 든 구간의 상한(ms). 마지막 칸이면 관측한 최댓값. 근사라 이름에 밝힌다."""
        if not self.n:
            return 0.0
        rank = q * self.n
        seen = 0
        for i, c in enumerate(self.counts):
            seen += c
            if seen >= rank and c:
                return float(BUCKETS_MS[i]) if i < len(BUCKETS_MS) else round(self.max, 1)
        return round(self.max, 1)

    def to_dict(self) -> dict:
        return {"n": self.n, "total_ms": round(self.total, 1),
                "p50_le_ms": self.quantile(0.5), "p95_le_ms": self.quantile(0.95),
                "max_ms": round(self.max, 1)}


class _Kind:
    __slots__ = ("sync", "polled", "errors", "timeouts", "wait", "run")

    def __init__(self):
        self.sync = self.polled = self.errors = self.timeouts = 0
        self.wait, self.run = _Hist(), _Hist()


class WriteWaits:
    """프로세스 하나에 하나(`WRITE_WAITS`). 테스트는 시계를 넣어 따로 만든다."""

    def __init__(self, clock=time.monotonic, *, log=None):
        self._clock = clock
        # 기본은 끔. 오래 도는 서버만 `enable_log()` 로 켠다(CLI 출력은 계약이라 섞지 않는다).
        self._log = log
        self._lock = threading.Lock()
        self._started = clock()
        self._last_log = self._started
        self._kinds: dict[str, _Kind] = {}
        self._pairs: dict[tuple[str, str], list] = {}   # (기다린, 앞에서 돈) → [ms, 횟수]
        self._waiting: dict[object, tuple[str, float]] = {}
        self._running: tuple[object, str, float] | None = None
        self._ddl: dict[str, _Hist] = {}
        self._busy: dict[str, int] = {}

    # ── 상한 ──────────────────────────────────────────────────────────────
    def _kind(self, kind: str) -> str:
        return kind if kind in self._kinds or len(self._kinds) < MAX_KINDS else OTHER

    def _stats(self, kind: str) -> _Kind:
        return self._kinds.setdefault(self._kind(kind), _Kind())

    def _charge(self, behind: str, start: float, end: float) -> None:
        """[start, end) 동안 워커가 `behind` 를 돌렸다. 그동안 기다린 잡마다 겹친 시간을 단다."""
        for kind, enq in self._waiting.values():
            ms = (end - max(start, enq)) * 1000.0
            if ms <= 0:
                continue
            key = (kind, behind)
            if key not in self._pairs and len(self._pairs) >= MAX_PAIRS:
                key = (OTHER, OTHER)
            pair = self._pairs.setdefault(key, [0.0, 0])
            pair[0] += ms
            pair[1] += 1

    # ── 큐가 부르는 것 ─────────────────────────────────────────────────────
    def enqueued(self, token, kind: str, *, sync: bool) -> None:
        with self._lock:
            kind = self._kind(kind)
            s = self._stats(kind)
            if sync:
                s.sync += 1
            else:
                s.polled += 1
            self._waiting[token] = (kind, self._clock())

    def started(self, token) -> None:
        with self._lock:
            kind, enq = self._waiting.pop(token, (OTHER, None))
            now = self._clock()
            if enq is not None:
                self._stats(kind).wait.add((now - enq) * 1000.0)
            self._running = (token, kind, now)

    def finished(self, token, *, ok: bool) -> None:
        with self._lock:
            run = self._running
            self._running = None
            if run is None or run[0] != token:
                return
            _, kind, start = run
            now = self._clock()
            s = self._stats(kind)
            s.run.add((now - start) * 1000.0)
            if not ok:
                s.errors += 1
            self._charge(kind, start, now)

    @contextmanager
    def blocking(self, name: str = IDLE_TICK):
        """잡이 아닌데 워커를 붙잡는 일(후처리 틱). 실행 시간을 세고 기다린 잡에 단다."""
        start = self._clock()
        try:
            yield
        finally:
            with self._lock:
                now = self._clock()
                if self._waiting:
                    self._stats(name).run.add((now - start) * 1000.0)
                    self._charge(name, start, now)

    def timed_out(self, kind: str) -> None:
        with self._lock:
            self._stats(kind).timeouts += 1

    # ── 큐 밖에서 부르는 것 ───────────────────────────────────────────────
    def ddl_held(self, ms: float) -> None:
        label = current_kind.get()
        with self._lock:
            if label not in self._ddl and len(self._ddl) >= MAX_KINDS:
                label = OTHER
            self._ddl.setdefault(label, _Hist()).add(ms)

    def store_busy(self, where: str) -> None:
        with self._lock:
            if where in self._busy or len(self._busy) < MAX_KINDS:
                self._busy[where] = self._busy.get(where, 0) + 1
            else:
                self._busy[OTHER] = self._busy.get(OTHER, 0) + 1

    # ── 읽기 ──────────────────────────────────────────────────────────────
    def snapshot(self, *, top: int = 20) -> dict:
        with self._lock:
            now = self._clock()
            pairs = sorted(self._pairs.items(), key=lambda kv: -kv[1][0])[:top]
            return {
                "uptime_s": round(now - self._started, 1),
                "buckets_ms": list(BUCKETS_MS),
                "kinds": {k: {"sync": s.sync, "polled": s.polled, "errors": s.errors,
                              "timeouts": s.timeouts, "wait": s.wait.to_dict(),
                              "run": s.run.to_dict()}
                          for k, s in sorted(self._kinds.items())},
                "blockers": [{"waiting": w, "behind": b, "total_ms": round(v[0], 1), "n": v[1]}
                             for (w, b), v in pairs],
                "ddl_exclusive_hold": {k: h.to_dict() for k, h in sorted(self._ddl.items())},
                "store_busy": dict(sorted(self._busy.items())),
                "queue": {"waiting_now": len(self._waiting),
                          "running": self._running[1] if self._running else None},
            }

    def enable_log(self, log=None) -> None:
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))

    def maybe_log(self, *, force: bool = False) -> bool:
        """한 시간에 한 번(또는 `force`) 집계를 로그 한 줄로 낸다. 냈으면 True. 꺼져 있으면 False."""
        if self._log is None:
            return False
        now = self._clock()
        with self._lock:
            if not force and now - self._last_log < LOG_EVERY_S:
                return False
            self._last_log = now
        self._log(LOG_PREFIX + " " + json.dumps(self.snapshot(), ensure_ascii=False,
                                                separators=(",", ":")))
        return True


#: 프로세스에 하나. `api.jobs.JobQueue` 와 DDL 가드, 사이드카의 `store_busy` 응답이 쓴다.
WRITE_WAITS = WriteWaits()
