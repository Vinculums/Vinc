# -*- coding: utf-8 -*-
"""WAL 재생이 엔진을 죽이는지 **자식 프로세스에서 먼저** 본다(이 기기의 사이드카 전용).

망가진 WAL 은 두 가지로 온다. 하나는 엔진이 예외로 말하는 것("Checksum verification
failed")이고, 그건 `EmbeddedBackend.open` 이 잡아 사유를 들고 강등한다. 다른 하나는
재생 도중 엔진이 **네이티브로 죽는** 것이다(접근 위반 · fail-fast). 같은 프로세스 안에서는
잡을 길이 없어 사이드카가 통째로 사라지고, 데스크톱은 로그인조차 못 한다: 로그인이 이
사이드카의 루프백으로 키를 받는다. 계정 모드는 로컬 스토어를 쓰지도 않는데 같이 죽는다.
`VINC_WAL_REPLAY_TOLERANT` 도 이것을 못 막는다(엄격·관대 둘 다 접근 위반으로 죽었다).

그래서 WAL 이 남아 있을 때만 같은 열기를 자식에게 먼저 시킨다:

- 자식이 열고 닫았다(0) → 재생과 체크포인트가 끝났다. 부모는 WAL 없이 연다.
- 자식이 예외로 실패했다(`PROBE_RAISED`) → 부모가 열어도 같은 예외다. 부모의 열기가 사유를
  들고 강등하게 둔다. 락 충돌을 WAL 탓으로 돌려 치우면 멀쩡한 쓰기를 버린다.
- 자식이 네이티브로 죽었다(시그널 · NTSTATUS) → WAL 을 옆으로 옮기고(**지우지 않는다**)
  한 번 더 본다. 그래도 죽으면 스토어를 열지 않고 뜬다. 로그인과 계정 모드는 그대로 된다.
- 판정할 수 없다(시간 초과 · 자식을 못 띄움 · 파이썬 쪽 실패) → 아무것도 안 건드리고
  부모가 연다.

허브에는 걸지 않는다(`serve` 가 `--parent-pid` 로 떴을 때만). 허브의 WAL 은 체크포인트
전의 공용 쓰기이고, 그걸 치울지는 사람이 판정한다.
근거: [[record:a-wal-that-crashes-the-engine-takes-the-desktop-sign-in-down-2026-09-27]]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

#: 자식이 "열기가 예외로 실패했다"를 말하는 종료 코드. 0(열림) · argparse 의 2 · Windows
#: CRT 의 `raise(SIGSEGV)` 가 내는 3 과 겹치지 않게 골랐다(로그에서 갈려 읽히도록).
PROBE_RAISED = 70

#: 한 번의 점검 상한. 수 MB WAL 의 재생도 이 안에 끝난다. 넘기면 판정하지 않는다.
PROBE_TIMEOUT_S = 120.0


@dataclass
class Guarded:
    """점검 결과. `open` 이 False 면 부모는 스토어를 열지 않는다."""
    open: bool = True
    note: str = ""        # 사람이 읽을 한 줄. /health 로 나가 화면에 닿을 수 있다(em-dash 금지)
    set_aside: str = ""   # 옆으로 옮긴 WAL 경로


def probe_command(data_dir: str) -> list[str]:
    """스토어를 한 번 열고 닫는 자식 명령. `vinc_mcp/bridge.py` 와 같은 규칙이다:
    동결본은 자기 자신이 CLI 이고, 소스 실행은 모듈로 부른다."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "store-probe", "--data", data_dir]
    return [sys.executable, "-m", "api.cli", "store-probe", "--data", data_dir]


def _exit_text(rc: int) -> str:
    # Windows 의 NTSTATUS 는 부호가 갈려 온다(-1073740791 = 0xC0000409). 16진수로 적는다.
    return f"0x{rc & 0xFFFFFFFF:08X}" if sys.platform == "win32" else str(rc)


def _native_death(rc: int) -> bool:
    """엔진이 **네이티브로** 죽었는가. 파이썬이 낸 실패(1 = 잡히지 않은 예외 · 2 = 인자)는
    아니다. 그걸 죽음으로 읽으면 자식이 모듈을 못 찾은 것만으로 멀쩡한 WAL 을 치운다."""
    if rc < 0:
        return True   # POSIX: 시그널로 끝났다(SIGSEGV · SIGABRT)
    return sys.platform == "win32" and (rc & 0xFFFFFFFF) >= 0xC0000000   # NTSTATUS 오류


def _probe(data_dir: str, run, timeout: float) -> tuple[str, str]:
    """('open' | 'died' | 'other', 종료 코드 글자). 'other' 는 판정하지 않는다는 뜻이다."""
    kw: dict = {}
    if sys.platform == "win32":
        # 창 없는 사이드카가 콘솔 자식을 띄우면 콘솔 창이 번쩍 뜬다.
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = run(probe_command(data_dir), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  timeout=timeout, **kw)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "other", type(exc).__name__
    rc = res.returncode
    if rc == 0:
        return "open", "0"
    return ("died" if _native_death(rc) else "other"), _exit_text(rc)


def guard(data_dir: str, *, run=subprocess.run, timeout: float = PROBE_TIMEOUT_S,
          now=time.time) -> Guarded:
    """WAL 이 있으면 자식으로 먼저 열어 보고, 엔진이 죽으면 WAL 을 옆으로 옮긴다."""
    wal = data_dir + ".wal"
    try:
        if os.path.getsize(wal) == 0:
            return Guarded()
    except OSError:
        return Guarded()   # WAL 이 없다. 재생할 것이 없고 대부분의 기동이 여기로 온다
    verdict, code = _probe(data_dir, run, timeout)
    if verdict != "died":
        return Guarded()
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now()))
    moved = f"{wal}.corrupt-{stamp}"
    try:
        os.replace(wal, moved)
    except OSError as exc:
        # 못 옮겼는데 열면 같은 재생이 이 프로세스를 죽인다. 열지 않는 쪽이 낫다.
        return Guarded(open=False, note=(
            f"WAL 재생이 엔진을 죽였고({code}) WAL 을 옮기지 못했다({type(exc).__name__}). "
            f"스토어를 열지 않고 떴다. 앱을 끄고 '{wal}' 을 옮기면 열린다."))
    note = (f"WAL 재생이 엔진을 죽여({code}) 옆으로 옮겼다: {moved} · "
            "마지막 체크포인트 뒤의 쓰기는 이 스토어에 없다")
    verdict, code = _probe(data_dir, run, timeout)
    if verdict == "died":
        return Guarded(open=False, set_aside=moved, note=(
            f"{note} · WAL 없이도 열기가 엔진을 죽인다({code}). 스토어를 열지 않고 떴다"))
    return Guarded(note=note, set_aside=moved)
