# -*- coding: utf-8 -*-
"""허브 연결 천장·읽기 타임아웃(감사 2026-08-18 M7).

공개 엔드포인트가 연결마다 스레드를 **무제한** 만들고 소켓 타임아웃이 없었다. 이
프로세스는 레플리카 하나로 공유 그래프를 서비스하므로 스레드 무제한은 곧 메모리
무제한이고, 아무것도 안 보내는 연결은 스레드를 영원히 잡는다(slowloris).

소켓을 바인딩하지 않고 **천장 로직 자체**를 부른다. 스레드를 섞으면 시험하는 것이
경합이 되고, 여기서 고정하려는 것은 "슬롯을 언제 잡고 언제 반납하는가"다."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import http_server as hs   # noqa: E402


def test_handler_has_a_read_timeout():
    """`StreamRequestHandler` 가 이 값을 소켓에 건다 — 없으면 slowloris 가 성립한다."""
    assert hs._Handler.timeout and hs._Handler.timeout > 0


class _Sock:
    def __init__(self):
        self.sent = b""
        self.closed = False

    def sendall(self, b):
        self.sent += b

    def close(self):
        self.closed = True


def _server(cap):
    """__init__ 을 우회해 천장만 가진 인스턴스를 만든다(소켓 바인딩 없음)."""
    srv = object.__new__(hs.BoundedHTTPServer)
    srv._slots = threading.BoundedSemaphore(cap)
    srv.handled = []
    srv.shutdown_request = lambda r: r.close()
    return srv


def _accept(srv, sock):
    """process_request 를 부르되 스레드는 띄우지 않는다 — 수락 여부만 본다."""
    spawned = []
    orig = hs.ThreadingHTTPServer.process_request
    hs.ThreadingHTTPServer.process_request = lambda self, r, a: spawned.append(a)
    try:
        hs.BoundedHTTPServer.process_request(srv, sock, ("127.0.0.1", 1))
    finally:
        hs.ThreadingHTTPServer.process_request = orig
    return bool(spawned)


def test_past_the_ceiling_it_answers_503_and_closes():
    srv = _server(2)
    assert _accept(srv, _Sock()) and _accept(srv, _Sock())

    third = _Sock()
    assert _accept(srv, third) is False, "천장을 넘겨 수락했다"
    assert b"503" in third.sent, third.sent
    assert b"Connection: close" in third.sent
    assert third.closed, "거절한 연결을 닫지 않았다"


def test_a_finished_request_gives_its_slot_back():
    srv = _server(1)
    assert _accept(srv, _Sock())
    assert _accept(srv, _Sock()) is False

    # 스레드가 끝나면 process_request_thread 의 finally 가 이 반납을 한다.
    # 여기서는 그 효과만 재현한다 — 반납 경로 자체는 아래 테스트가 본다.
    srv._slots.release()
    assert _accept(srv, _Sock()), "슬롯이 반납되지 않았다"


def test_the_thread_wrapper_releases_even_when_handling_raises():
    """예외가 나도 반납해야 한다 — 안 그러면 오류 하나가 천장을 영구히 깎는다."""
    srv = _server(1)
    srv._slots.acquire()

    orig = hs.ThreadingHTTPServer.process_request_thread

    def boom(self, r, a):
        raise RuntimeError("handler blew up")

    hs.ThreadingHTTPServer.process_request_thread = boom
    try:
        try:
            hs.BoundedHTTPServer.process_request_thread(srv, _Sock(), ("127.0.0.1", 1))
        except RuntimeError:
            pass
    finally:
        hs.ThreadingHTTPServer.process_request_thread = orig

    assert srv._slots.acquire(blocking=False), "실패한 요청이 슬롯을 물고 죽었다"
