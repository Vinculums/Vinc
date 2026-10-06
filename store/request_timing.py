"""Opt-in request timings: fixed phase names and numbers, never request material."""
from __future__ import annotations

import contextvars
import functools
import math
import time
from contextlib import ExitStack, contextmanager

HEADER = "Vinc-Diagnostics"
VALUE = "timings"
PHASES = frozenset({"reader", "reader_close", "query", "embed", "keyword",
                    "vector", "node_vector", "bridge", "auth", "gate",
                    "reader_lock", "connection_open", "connection_config",
                    "ddl_shared_wait", "ddl_exclusive_wait", "db_execute",
                    "db_compile", "db_native_exec",
                    "db_fetch", "db_decode", "vector_index", "vector_fallback",
                    "node_vector_load", "node_vector_index", "node_vector_fallback",
                    "node_vector_load_error", "node_vector_query_error"})
_current = contextvars.ContextVar("vinc_request_timing", default=None)


class Timings:
    def __init__(self):
        self.started = time.perf_counter()
        self.values = {}

    def add(self, name, milliseconds):
        self.values[name] = self.values.get(name, 0.0) + milliseconds

    def header(self, total):
        values = {**self.values, total: (time.perf_counter() - self.started) * 1000}
        return ", ".join(f"{name};dur={value:.3f}" for name, value in sorted(values.items()))


def active():
    return _current.get() is not None


def observe(phase, milliseconds):
    """엔진이 보고한 ms를 받는다. 지원하지 않는 통계는 호출부가 생략한다."""
    if phase not in PHASES:
        raise ValueError("unknown timing phase")
    collector = _current.get()
    if collector is None:
        return
    try:
        value = float(milliseconds)
    except (TypeError, ValueError):
        return
    if math.isfinite(value) and 0 <= value <= 3600000:
        collector.add(phase, value)


@contextmanager
def capture():
    collector = Timings()
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


@contextmanager
def measure(phase):
    if phase not in PHASES:
        raise ValueError("unknown timing phase")
    collector = _current.get()
    if collector is None:
        yield
        return
    started, cpu = time.perf_counter(), time.thread_time()
    try:
        yield
    finally:
        collector.add(phase, (time.perf_counter() - started) * 1000)
        collector.add(phase + "_cpu", (time.thread_time() - cpu) * 1000)


def timed(phase):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not active():
                return fn(*args, **kwargs)
            with measure(phase):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def wait(phase, manager):
    """가드 진입만 잰다. 보유·해제·예외 억제는 원래 context manager가 맡는다."""
    if not active():
        with manager as value:
            yield value
        return
    with ExitStack() as stack:
        with measure(phase):
            value = stack.enter_context(manager)
        yield value


def header(total):
    if total not in {"sidecar", "rest"}:
        raise ValueError("unknown timing boundary")
    collector = _current.get()
    return collector.header(total) if collector is not None else ""


def merge_sidecar(value):
    """Only accept bounded finite metrics from the attached sidecar, not arbitrary headers."""
    collector = _current.get()
    if collector is None:
        return
    allowed = {"sidecar", *PHASES, *(p + "_cpu" for p in PHASES)}
    # 새 구간도 전부 받되 입력 크기와 항목 수의 상한은 유지한다.
    for item in str(value or "")[:8192].split(",")[:len(allowed)]:
        name, separator, duration = item.strip().partition(";dur=")
        if not separator or name not in allowed:
            continue
        try:
            milliseconds = float(duration)
        except ValueError:
            continue
        if math.isfinite(milliseconds) and 0 <= milliseconds <= 3600000:
            collector.add(name if name == "sidecar" else "sidecar_" + name, milliseconds)
