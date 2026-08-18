# -*- coding: utf-8 -*-
"""프로세스 생존 조회 — **조회만 한다. 대상을 죽이지 않는다.**

이 파일이 따로 있는 이유가 이 모듈의 요점이다.

POSIX 에서 `os.kill(pid, 0)` 은 신호를 보내지 않는 생존 확인이지만, **Windows 의
`os.kill` 은 `TerminateProcess` 로 매핑되는 파괴적 관용구**다. 즉 그 관용구를 그대로
쓰면 "살아 있나?" 라고 묻는 순간 상대가 죽는다.

그 사실은 사이드카(`api/server.py`)에 이미 적혀 있었고 거기서는 안전하게 구현돼
있었다. 그런데 **같은 함수의 두 번째 사본**이 `vinc_mcp/bridge.py` 에 있었고 그쪽은
`os.kill` 을 그대로 썼다. 브리지가 조회하는 pid 는 `~/.vinc/session.json` 에 적힌
**앱(셸) 자신**이므로(`app/src-tauri/src/main.rs` 의 `write_session`), Windows 에서
`vinc-sidecar.exe mcp serve` 를 띄우면 몇 초 안에 앱이 사라졌다.

2026-08-18 실기 관측: 앱 기동 18회 중 17회가 종료 로그 없이 사망(TerminateProcess 는
정리할 틈을 주지 않는다) · 크래시 덤프 0 · 이벤트 로그 0 · session.json 에서 pid 를
지우면 재현되지 않음. 마지막 한 줄이 인과를 고정한다.

그래서 구현을 한 벌로 모은다. 사본이 둘이면 그중 하나만 고쳐지고, 이 사고가 정확히
그렇게 났다.
"""
from __future__ import annotations

import os

__all__ = ["pid_alive", "alive_windows"]


def alive_windows(pid: int, k32) -> bool:
    """Windows 조회 — 커널 인터페이스를 **인자로 받는다**.

    이렇게 갈라 두면 이 분기를 Windows 아닌 기계에서도 그대로 시험할 수 있다.
    `os.name` 을 전역으로 바꿔 가며 시험하려 들면 그 패치가 pathlib 같은 표준
    라이브러리까지 흔든다(실측: pytest 가 `WindowsPath` 를 만들려다 죽었다).
    """
    from ctypes import wintypes
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102   # 미신호 → 아직 실행 중
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        k32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """pid 가 살아 있는가. **대상에 아무 영향도 주지 않는다.**

    Windows 는 `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(0)` 로 조회만 한다.
    핸들을 못 열거나(종료·부재) 신호 상태(종료)면 사망으로 본다. 오판은 fail-safe
    방향(호출자가 한 번 더 확인)일 뿐 대상을 건드리지 않는다.
    """
    if os.name == "nt":
        import ctypes
        return alive_windows(pid, ctypes.windll.kernel32)
    try:
        os.kill(pid, 0)   # POSIX: 신호 0 = 생존 확인(전송 안 함)
        return True
    except OSError:
        return False
