# -*- coding: utf-8 -*-
"""`store/procutil.py` 의 재수출 — **구현은 저쪽 한 벌뿐이다.**

2026-09-30 에 `store/` 아래로 내렸다. 이유는 층이다: `vinc_mcp/bridge.py` 가 이 함수를
쓰는데 `vinc_mcp` 는 `api` 를 모르는 층이어야 하고(계획서 `vinc.plan.team-split`,
Linear VIN-242), 이 모듈은 어차피 `api` 의 아무것도 쓰지 않았다.

이 파일을 남기는 이유는 사본을 만들려는 것이 아니라 그 반대다. `from api.procutil
import pid_alive` 라고 적힌 자리(사이드카·테스트)가 갈아타는 동안에도 **같은 함수**를
받게 하려는 것이다. 사본이 둘이던 시절 브리지 쪽만 `os.kill` 을 쓰다가 Windows 에서
앱을 죽인 사고(2026-08-18)가 이 모듈의 존재 이유이고, 재수출은 그 사고를 다시 내지
않는다 — `tests/test_pid_probe.py` 가 두 진입점이 같은 소스인지 본다.
"""
from __future__ import annotations

from store.procutil import alive_windows, pid_alive

__all__ = ["pid_alive", "alive_windows"]
