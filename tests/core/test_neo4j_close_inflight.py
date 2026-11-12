# -*- coding: utf-8 -*-
"""Neo4j 어댑터의 닫기는 다른 스레드에서 진행 중인 질의를 기다린다(`store.neo4j_backend.Neo4jBackend.close`).

잠그는 것: 다른 스레드가 드라이버 세션 안에 있는 동안에는 드라이버를 닫지 않는다, 닫기가 시작된 뒤의 새
질의는 `StoreClosed` 로 거절된다, 임대 갱신(박동)이 진행 중이어도 같다, 묶음 안에서 닫는 스레드는 자기
자신을 기다리지 않는다.

왜: neo4j 드라이버의 `close()` 는 빌려 간 연결까지 닫으며 GOODBYE 를 쓴다. 그 연결에서 다른 스레드가
`sendall` 중이면 송신 버퍼(`Outbox._chunked_data`)를 늘리다 `BufferError: Existing exports of data` 가 난다
(CI job 130934765746, `test_neo4j_location_open` 의 닫기). 서버 없이 가짜 드라이버로 그 순서를 고정한다:
가짜는 질의를 사건(Event)으로 붙들고, 닫힐 때 열린 세션 수를 적는다.
"""
from __future__ import annotations

import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_WAIT = 5.0   # 사건 기다림의 상한. 정상이면 즉시 깬다 — 테스트가 멈추지 않게 하는 안전판일 뿐이다.


class _Record:
    def __init__(self, data):
        self._d = data

    def data(self):
        return dict(self._d)

    def keys(self):
        return list(self._d)

    def __getitem__(self, k):
        return self._d[k]


class _Result(list):
    def single(self):
        return self[0] if self else None


class _Driver:
    """질의를 붙들 수 있는 가짜 드라이버. `close()` 때 열려 있던 세션 수를 `open_at_close` 에 적는다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.open = 0
        self.open_at_close: list[int] = []
        self.hold = threading.Event()      # 세우면 다음 질의가 `release` 까지 붙들린다
        self.entered = threading.Event()   # 붙들린 질의가 들어왔다
        self.release = threading.Event()

    def verify_connectivity(self):
        pass

    def get_server_info(self):
        return types.SimpleNamespace(agent="Neo4j/2026.01.4")

    def session(self, **_kw):
        return _Session(self)

    def _query(self, query):
        if self.hold.is_set():
            self.hold.clear()
            self.entered.set()
            self.release.wait(_WAIT)
        if "RETURN ok" in query:            # 임대 집기는 늘 이긴다
            return _Result([_Record({"ok": True, "holder": "me", "expires": 0})])
        return _Result([_Record({"x": 1})])

    def close(self):
        with self.lock:
            self.open_at_close.append(self.open)


class _Tx:
    def __init__(self, drv):
        self._drv = drv

    def run(self, query, params=None):
        return self._drv._query(query)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _Session:
    def __init__(self, drv):
        self._drv = drv

    def __enter__(self):
        with self._drv.lock:
            self._drv.open += 1
        return self

    def __exit__(self, *exc):
        with self._drv.lock:
            self._drv.open -= 1

    def run(self, query, params=None):
        return self._drv._query(query)

    def execute_write(self, work):
        return work(_Tx(self._drv))

    def begin_transaction(self):
        return _Tx(self._drv)


def _fake_neo4j(drv: _Driver) -> dict[str, types.ModuleType]:
    m = types.ModuleType("neo4j")
    m.__path__ = []
    m.GraphDatabase = types.SimpleNamespace(driver=lambda *a, **kw: drv)
    m.NotificationMinimumSeverity = types.SimpleNamespace(OFF="OFF")
    m.READ_ACCESS, m.WRITE_ACCESS = "READ", "WRITE"
    g = types.ModuleType("neo4j.graph")      # 행 변환(`_value`)이 타입 대조에 쓴다
    g.Node, g.Relationship, g.Path = (type(n, (), {}) for n in ("Node", "Relationship", "Path"))
    m.graph = g
    return {"neo4j": m, "neo4j.graph": g}


class CloseWaitsForInflightTests(unittest.TestCase):
    def setUp(self):
        self.drv = _Driver()
        patcher = mock.patch.dict(sys.modules, _fake_neo4j(self.drv))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.drv.release.set)   # 실패해도 붙든 스레드를 놓아 준다

    def _open(self, **kw):
        from store.neo4j_backend import Neo4jBackend
        return Neo4jBackend("bolt://fake:7687", user="neo4j", password="x", heartbeat=False, **kw)

    def _close_while_held(self, store, hold_in):
        """`hold_in()` 을 다른 스레드에서 붙든 채 닫기를 시작하고, 닫기가 드라이버를 닫았거나
        기다리는 중일 때 질의를 놓아 준다. 닫힐 때 열려 있던 세션 수를 돌려준다."""
        errors: list[BaseException] = []

        def worker():
            try:
                hold_in()
            except BaseException as exc:   # noqa: BLE001 — 주 스레드에서 다시 본다
                errors.append(exc)

        self.drv.hold.set()
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        self.assertTrue(self.drv.entered.wait(_WAIT), "질의가 드라이버에 닿지 않았다")
        closer = threading.Thread(target=store.close, daemon=True)
        closer.start()
        # 고치기 전: 닫기가 곧바로 드라이버를 닫는다(open_at_close 가 찬다).
        # 고친 뒤: 닫기가 새 질의를 막고(_closing) 진행 중인 질의를 기다린다.
        for _ in range(int(_WAIT / 0.005)):
            if self.drv.open_at_close or getattr(store, "_closing", False):
                break
            threading.Event().wait(0.005)
        self.drv.release.set()
        closer.join(_WAIT)
        t.join(_WAIT)
        self.assertFalse(closer.is_alive(), "닫기가 끝나지 않았다")
        self.assertEqual(errors, [])
        return self.drv.open_at_close

    def test_다른_스레드의_질의가_끝난_뒤에_드라이버를_닫는다(self):
        store = self._open(writer=False)
        self.assertEqual(self._close_while_held(store, lambda: store.run("RETURN 1 AS x")), [0])

    def test_진행_중인_임대_갱신도_기다린다(self):
        from store.neo4j_backend import _LEASE_ACQUIRE
        store = self._open(writer=True)
        self.assertFalse(store.read_only)
        self.assertEqual(self._close_while_held(store, lambda: store._lease(_LEASE_ACQUIRE)), [0])

    def test_닫기가_시작된_뒤의_새_질의는_말하고_거절한다(self):
        from store.neo4j_backend import StoreClosed
        store = self._open(writer=False)
        store.close()
        with self.assertRaises(StoreClosed):
            store.run("RETURN 1 AS x")
        with self.assertRaises(StoreClosed):
            with store.write_batch():
                pass
        self.assertEqual(self.drv.open_at_close, [0])

    def test_묶음_안에서_닫는_스레드는_자기를_기다리지_않는다(self):
        store = self._open(writer=False)
        store.close_wait_s = _WAIT
        done = threading.Event()

        def inside():
            with store.write_batch():
                store.close()
                done.set()

        t = threading.Thread(target=inside, daemon=True)
        t.start()
        self.assertTrue(done.wait(1.0), "묶음 안의 닫기가 자기 자신을 기다렸다")
        t.join(_WAIT)


if __name__ == "__main__":
    unittest.main()
