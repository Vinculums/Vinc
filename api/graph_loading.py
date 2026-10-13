"""Ephemeral, bounded continuations for an initial graph read (not a result cache).

Each producer owns its reader. Cancellation/deadline is cooperative between Python
phases and uses that reader's native query timeout. Never close a live database.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import secrets
import sys
import threading
import time


class GraphWire(bytes):
    """An already serialized graph response, owned by a read continuation."""


def encode_result(result):
    return result[0], GraphWire((json.dumps(result[1], ensure_ascii=False,
                               separators=(',', ':'))+'\n').encode('utf-8'))


def _response(result, wire):
    return result if wire else (result[0], json.loads(result[1]))


def _size(value, limit):
    seen, stack, total = set(), [value], 0
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if total > limit:
            return limit + 1
        if isinstance(item, dict):
            stack.extend(item.keys()); stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return total


class ReadControl:
    def __init__(self, deadline, clock=time.monotonic):
        self.deadline, self.clock = deadline, clock
        self.cancelled = threading.Event()

    def remaining(self):
        return max(0, self.deadline - self.clock())

    def check(self):
        if self.cancelled.is_set() or not self.remaining():
            raise TimeoutError('graph read cancelled or expired')


@dataclass
class _Read:
    owner: str
    scope: str
    expires: float
    control: ReadControl
    preview_ready: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    preview: dict | None = None
    result: tuple | None = None
    size: int = 0
    thread: threading.Thread | None = None


def _unavailable():
    return 503, {'executed': False, 'error': 'graph_snapshot_unavailable'}


class GraphReads:
    def __init__(self, *, ttl=45, read_timeout=15, max_running=2, max_entries=8,
                 max_bytes=32*1024*1024, clock=time.monotonic):
        self.ttl, self.read_timeout = ttl, read_timeout
        self.max_running, self.max_entries = max_running, max_entries
        self.max_bytes, self.clock = max_bytes, clock
        self._lock, self._reads, self._closed = threading.Lock(), OrderedDict(), False

    def _reserve(self, read, size):
        remaining = self.max_bytes - sum(r.size for r in self._reads.values() if r is not read)
        for key, old in list(self._reads.items()):
            if size <= remaining:
                break
            if old is not read and old.ready.is_set():
                remaining += old.size
                del self._reads[key]
        return size <= remaining

    def start(self, owner, scope, produce, *, wire=False):
        with self._lock:
            now = self.clock()
            for key, read in list(self._reads.items()):
                if read.expires <= now:
                    read.control.cancelled.set()
                    if read.ready.is_set():
                        del self._reads[key]
            if len(self._reads) >= self.max_entries:
                victim = next((k for k, r in self._reads.items() if r.ready.is_set()), None)
                if victim is not None:
                    del self._reads[victim]
            if (self._closed or sum(not r.ready.is_set() for r in self._reads.values()) >= self.max_running
                    or len(self._reads) >= self.max_entries
                    or sum(r.owner == owner and not r.ready.is_set() for r in self._reads.values()) >= 2):
                return None
            token = secrets.token_urlsafe(32)
            control = ReadControl(now + min(self.ttl, self.read_timeout), self.clock)
            read = _Read(owner, scope, now + self.ttl, control)
            self._reads[token] = read

            def publish(preview):
                control.check()
                if not preview.get('executed') or not preview.get('nodes'):
                    return False
                preview = {**preview, 'snapshot': {'protocol': 'graph-preview-v1', 'token': token}}
                size = _size(preview, self.max_bytes)
                with self._lock:
                    if self._closed or control.cancelled.is_set() or not self._reserve(read, size):
                        return False
                    read.preview, read.size = preview, size
                    read.preview_ready.set()
                return True

            def run():
                timer = threading.Timer(min(self.ttl, self.read_timeout), control.cancelled.set)
                timer.daemon = True; timer.start()
                try:
                    result = produce(publish, control)
                    control.check()
                    # Serialize once and retain bytes, not the full Python graph.
                    # This bounds real retained memory without walking every property.
                    result = encode_result(result)
                    control.check()
                    size = _size((read.preview, result), self.max_bytes)
                    with self._lock:
                        if not self._closed and not control.cancelled.is_set() and self._reserve(read, size):
                            read.result, read.size = result, size
                        else:
                            read.preview, read.result, read.size = None, None, 0
                except Exception:
                    with self._lock:
                        read.preview, read.result, read.size = None, None, 0
                finally:
                    timer.cancel()
                    read.ready.set(); read.preview_ready.set()

            read.thread = threading.Thread(target=run, name='vinc-graph-read', daemon=True)
            read.thread.start()
        read.preview_ready.wait(min(self.ttl, self.read_timeout))
        with self._lock:
            return (200, read.preview) if read.preview is not None else _response(read.result, wire) if read.result else _unavailable()

    def _find(self, owner, scope, token):
        read = self._reads.get(token)
        if (self._closed or read is None or read.owner != owner or read.scope != scope
                or read.expires <= self.clock() or read.control.cancelled.is_set()):
            if read is not None and read.expires <= self.clock():
                read.control.cancelled.set()
            return None
        return read

    def finish(self, owner, scope, token, *, wait=5, wire=False):
        with self._lock:
            read = self._find(owner, scope, token)
        if read is None:
            return 404, {'executed': False, 'error': 'graph_snapshot_unavailable'}
        read.ready.wait(wait)
        with self._lock:
            if self._find(owner, scope, token) is None:
                return 404, {'executed': False, 'error': 'graph_snapshot_unavailable'}
            if not read.ready.is_set():
                return 200, {'executed': False, 'graphSnapshotPending': True}
            return _response(read.result, wire) if read.result else _unavailable()

    def cancel(self, owner, scope, token):
        with self._lock:
            read = self._find(owner, scope, token)
            if read is None:
                return 404, {'executed': False, 'error': 'graph_snapshot_unavailable'}
            read.control.cancelled.set()
            read.preview, read.result, read.size = None, None, 0
        return 200, {'executed': True, 'cancelled': True}

    def invalidate_owner(self, owner):
        """Invalidate retained continuations when ownership changes."""
        with self._lock:
            for key, read in list(self._reads.items()):
                if read.owner == owner:
                    read.control.cancelled.set()
                    read.preview, read.result, read.size = None, None, 0
                    if read.ready.is_set():
                        del self._reads[key]

    def close(self):
        with self._lock:
            self._closed = True
            for read in self._reads.values():
                read.control.cancelled.set()
            threads = [r.thread for r in self._reads.values()]
        for thread in threads:
            thread.join()
        with self._lock:
            self._reads.clear()
