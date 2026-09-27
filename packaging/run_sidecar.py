# -*- coding: utf-8 -*-
"""PyInstaller 진입점(D43) — `vinc serve …` 로 실행되는 단일 바이너리.
api.cli 를 그대로 위임한다(계약 1:1 — CLI 와 바이너리의 동작 동일)."""
import os
import sys

# Windows 동결본: sidecar.spec 이 OpenSSL 3 DLL 을 번들 루트(_MEIPASS)에 싣지만,
# 파이썬 3.8+ 는 확장 모듈의 의존 DLL 을 PATH 에서도 SetDllDirectory 경로에서도 찾지
# 않는다. 그래서 `ladybug._lbug` import 가 "DLL load failed" 로 죽고 C API 폴백으로
# 떨어져 스토어가 안 열린다(v0.9.4 첫 빌드의 Windows 스모크 실패, 로컬 재현).
# add_dll_directory 는 엔진이 확장(algo·fts·vector)을 LoadLibrary 할 때도 적용된다
# (파이썬이 기동 시 SetDefaultDllDirectories 를 켜 두므로). ladybug import 보다 먼저.
if sys.platform == "win32" and getattr(sys, "frozen", False):
    os.add_dll_directory(sys._MEIPASS)

from api.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
