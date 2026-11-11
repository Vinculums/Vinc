# -*- coding: utf-8 -*-
"""Neo4j 쓰기 임대 — 데이터베이스 하나에 Vinc 쓰기 프로세스는 하나다(`store.neo4j_backend`).

잠그는 것: 먼저 연 쪽이 쓰기를 쥐고, 뒤에 연 쪽은 읽기 전용으로 열려 읽기는 되고 쓰기는 서버가
거절한다. 쥔 쪽이 닫으면 바로 넘어가고, 쥔 쪽이 죽어(박동이 멈춰) 만료가 지나도 넘어간다.
`VINC_CONFORMANCE_NEO4J_URI` 가 있을 때만 돈다(CI 는 서비스 컨테이너, 로컬은 테스트용 컨테이너).
⚠ 그 데이터베이스를 비운다. 테스트 전용 서버만 가리킬 것.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

URI = os.environ.get("VINC_CONFORMANCE_NEO4J_URI")
pytestmark = pytest.mark.skipif(not URI, reason="VINC_CONFORMANCE_NEO4J_URI 없음")


def _open(**kw):
    from store.neo4j_backend import Neo4jBackend
    return Neo4jBackend(URI, user=os.environ.get("VINC_CONFORMANCE_NEO4J_USER", "neo4j"),
                        password=os.environ["VINC_CONFORMANCE_NEO4J_PASSWORD"], **kw)


@pytest.fixture(autouse=True)
def _clean():
    s = _open(writer=False)
    try:
        with s._driver.session(database=s.database) as sess:   # 비우기는 임대 밖에서 한다
            sess.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
    finally:
        s.close()
    yield


def test_뒤에_연_쪽은_읽기_전용이고_쓰기는_서버가_거절한다():
    a = _open(heartbeat=False)
    b = _open(heartbeat=False)
    try:
        assert a.writer_state()["writer"] is True
        assert b.read_only is True and a.holder in b.read_only_reason
        a.run("CREATE (:LeaseProbe {v: 1})")
        assert b.run("MATCH (p:LeaseProbe) RETURN p.v AS v") == [{"v": 1}]
        from store.neo4j_backend import StoreReadOnly
        with pytest.raises(StoreReadOnly, match="read-only: another Vinc process holds the writer lease"):
            b.run("CREATE (:LeaseProbe {v: 2})")
    finally:
        b.close()
        a.close()


def test_쥔_쪽이_닫으면_바로_넘어간다():
    a = _open(heartbeat=False)
    b = _open(heartbeat=False)
    try:
        assert b.read_only
        a.close()
        assert b.try_acquire() is True and b.read_only is False
        b.run("CREATE (:LeaseProbe {v: 3})")
    finally:
        b.close()


def test_쥔_쪽이_죽으면_만료_뒤에_넘어간다():
    a = _open(heartbeat=False, lease_ttl_s=1.5)      # 박동 없음 = 죽은 프로세스
    b = _open(heartbeat=False)
    try:
        assert b.read_only
        time.sleep(2.0)
        assert b.try_acquire() is True
        assert a.try_acquire() is False and a.read_only is True   # 돌아온 쪽은 읽기 전용이 된다
    finally:
        b.close()
        a._stop.set()
        a._driver.close()


def test_박동이_임대를_늘린다():
    a = _open(lease_ttl_s=3.0)                         # 박동 주기 = 1초
    b = _open(heartbeat=False)
    try:
        time.sleep(4.0)                                # 만료 3초를 넘겨도 박동이 늘려 놓았다
        assert b.try_acquire() is False
    finally:
        b.close()
        a.close()


def test_동시에_집어도_하나만_쓴다():
    import threading
    opened, errors = [], []
    start = threading.Barrier(8)

    def go():
        try:
            start.wait()
            opened.append(_open(heartbeat=False))
        except Exception as exc:                        # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert not errors, errors
        assert sum(1 for b in opened if not b.read_only) == 1
    finally:
        for b in opened:
            b.close()


def test_같은_설치의_죽은_프로세스가_남긴_임대는_바로_되찾는다():
    """재기동한 사이드카가 만료(기본 60초)를 기다리지 않는다. 다른 설치의 임대는 그대로 기다린다."""
    import subprocess
    from store.neo4j_backend import installation_holder
    inst = "0123456789abcdef0123456789abcdef"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    a = _open(heartbeat=False, holder=installation_holder(inst, pid=dead.pid), lease_ttl_s=600)
    try:
        assert a.writer_state()["writer"] is True
        other = _open(heartbeat=False, holder=installation_holder("f" * 32), lease_ttl_s=600)
        assert other.read_only, "다른 설치는 만료까지 기다린다"
        other.close()
        b = _open(heartbeat=False, holder=installation_holder(inst), lease_ttl_s=600)
        try:
            assert b.writer_state()["writer"] is True
        finally:
            b.close()
    finally:
        a._driver.close()          # 죽은 쪽은 임대를 놓지 않는다


def test_살아_있는_같은_설치의_임대는_빼앗지_않는다():
    from store.neo4j_backend import installation_holder
    inst = "0123456789abcdef0123456789abcdef"
    a = _open(heartbeat=False, holder=installation_holder(inst, pid=os.getppid()), lease_ttl_s=600)
    b = _open(heartbeat=False, holder=installation_holder(inst), lease_ttl_s=600)
    try:
        assert a.writer_state()["writer"] is True and b.read_only
    finally:
        b.close()
        a.close()
