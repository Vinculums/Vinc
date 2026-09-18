# -*- coding: utf-8 -*-
"""PyInstaller 진입점(D43) — `vinc serve …` 로 실행되는 단일 바이너리.
api.cli 를 그대로 위임한다(계약 1:1 — CLI 와 바이너리의 동작 동일)."""
import sys

from api.cli import main

if __name__ == "__main__":
    sys.exit(main())
