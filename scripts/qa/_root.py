"""`scripts/qa/` 의 스크립트를 `python` 으로 직접 돌릴 때의 다리. 정본은 `scripts/_root.py` 다.

직접 실행이면 `sys.path[0]` 이 `scripts/qa` 라서 맨 이름 `_root` 가 이 파일로 풀린다.
리포 루트를 올리고 정본의 `ROOT` 를 그대로 내보낸다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts._root import ROOT  # noqa: E402,F401
