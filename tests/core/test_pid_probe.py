# -*- coding: utf-8 -*-
"""pid 생존 조회가 **조회로 끝나는가**.

2026-08-18 실기 사고의 회귀 방지다. `vinc_mcp/bridge.py` 에 `os.kill(pid, 0)` 사본이
있었고, 그 함수가 조회하는 pid 는 `~/.vinc/session.json` 에 적힌 **앱 자신**이었다.
Windows 의 `os.kill` 은 TerminateProcess 라, `vinc-sidecar.exe mcp serve` 를 띄우면
그 조회가 앱을 죽였다(기동 18회 중 17회가 종료 로그 없이 사망 · pid 를 지우면 재현
안 됨).

여기서 고정하는 것은 둘이다.
  ① Windows 경로에서는 `os.kill` 을 **부르지 않는다**.
  ② 구현 사본을 다시 만들지 않는다(브리지는 공유 구현에 위임한다).
"""
from __future__ import annotations

import os

import pytest

from api import procutil


def test_posix_경로는_신호0으로_조회한다():
    """POSIX 에서 신호 0 은 전송 없는 생존 확인이라 그대로 써도 된다."""
    assert procutil.pid_alive(os.getpid()) is True
    # 존재하지 않을 만큼 큰 pid — 사망으로 봐야 한다.
    assert procutil.pid_alive(4_000_000) is False


def test_windows_분기는_조회만_한다():
    """이 테스트가 이 파일의 존재 이유다.

    커널 인터페이스를 가짜로 주입해 Windows 분기를 그대로 태운다. 가짜는 조회 함수
    셋만 갖고 있으므로, 누가 이 분기에서 다시 프로세스를 종료시키려 들면 여기서
    터진다. `os.name` 을 패치하지 않는 이유는 그 패치가 pathlib 까지 흔들기 때문이다.
    """
    class FakeKernel32:
        """OpenProcess · WaitForSingleObject · CloseHandle 만 있는 커널."""

        def __init__(self, handle: int, wait: int):
            self._handle, self._wait, self.closed = handle, wait, []
            self.OpenProcess = _Fn(lambda access, inherit, pid: self._handle)
            self.WaitForSingleObject = _Fn(lambda h, ms: self._wait)
            self.CloseHandle = _Fn(self.closed.append)

    class _Fn:
        """ctypes 처럼 restype·argtypes 대입을 받아 주는 호출 가능 객체."""

        def __init__(self, fn):
            self._fn, self.restype, self.argtypes = fn, None, None

        def __call__(self, *a):
            return self._fn(*a)

    # 살아 있음: 핸들이 열리고 대기가 WAIT_TIMEOUT(미신호)
    k = FakeKernel32(1234, 0x102)
    assert procutil.alive_windows(4242, k) is True
    assert k.closed == [1234], "핸들을 반드시 닫는다"

    # 죽음 ①: 핸들을 못 연다
    assert procutil.alive_windows(4242, FakeKernel32(0, 0x102)) is False
    # 죽음 ②: 핸들은 열리나 신호 상태(WAIT_OBJECT_0)
    assert procutil.alive_windows(4242, FakeKernel32(1234, 0)) is False


def test_windows_분기에_os_kill_이_없다():
    """소스로 고정한다 — 편의상 `os.kill` 로 되돌리면 그 순간 앱이 죽는다."""
    src = __import__("inspect").getsource(procutil.alive_windows)
    assert "os.kill" not in src


def test_구현_사본을_다시_만들지_않는다():
    """브리지와 사이드카가 **같은 구현**을 쓰는지 본다.

    사고의 원인은 함수가 틀린 것이 아니라 **사본이 둘이었던 것**이다. 한쪽만
    고쳐졌고, 하필 위험한 쪽이 앱의 pid 를 조회하고 있었다.
    """
    from api import server
    from vinc_mcp import bridge

    assert server._pid_alive(os.getpid()) is True
    assert bridge._pid_alive(os.getpid()) is True

    src_bridge = __import__("inspect").getsource(bridge._pid_alive)
    assert "os.kill" not in src_bridge.split('"""')[-1], \
        "브리지가 다시 os.kill 을 직접 부른다 — Windows 에서 앱이 죽는다"
    assert "procutil" in src_bridge


def test_세션이_죽은_pid_면_stale_로_본다(tmp_path, monkeypatch):
    """`_read_session` 은 pid 가 죽었으면 그 세션을 무시해야 한다.

    ⚠ 이 판정을 하려고 pid 를 조회하는 것이 사고의 발단이었으므로, 조회가 무해해진
    지금도 그 동작 자체는 유지되는지 확인한다.
    """
    import json

    from vinc_mcp import bridge as B

    sess = tmp_path / "session.json"
    sess.write_text(json.dumps({"port": 55555, "token": "t", "pid": 4_000_000}),
                    encoding="utf-8")
    monkeypatch.setattr(B, "_session_path", lambda: str(sess))

    br = B.Bridge()
    assert br._read_session() is None, "죽은 pid 의 세션은 attach 대상이 아니다"

    sess.write_text(json.dumps({"port": 55555, "token": "t", "pid": os.getpid()}),
                    encoding="utf-8")
    got = br._read_session()
    assert got and got["port"] == 55555


@pytest.mark.parametrize("payload", [
    {"token": "t", "pid": 1},          # port 없음
    {"port": 0, "token": "t"},         # port 0
])
def test_포트가_없으면_세션이_아니다(tmp_path, monkeypatch, payload):
    import json

    from vinc_mcp import bridge as B

    sess = tmp_path / "session.json"
    sess.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(B, "_session_path", lambda: str(sess))
    assert B.Bridge()._read_session() is None
