"""운영 스크립트가 리포 루트를 찾는 한 자리.

`scripts/` 와 `scripts/qa/` 는 깊이가 달라서 `parents[1]`·`parents[2]` 를 파일마다 다시
셌다. 그 셈을 여기 한 번만 둔다. import 하면 리포 루트가 `sys.path` 맨 앞에 올라가서
`store`·`api`·`scripts.*` 를 바로 import 할 수 있다.

스크립트는 두 방식으로 불린다. 스크립트 파일을 `python` 으로 직접 돌리면 `sys.path[0]` 이
그 파일의 폴더라 `scripts` 패키지가 안 보이고, 테스트나 다른 스크립트가
`from scripts import x` 로 부르면 반대로 맨 이름 `_root` 가 안 보인다. 그래서 둘 다 받는다:

    try:
        from scripts._root import ROOT
    except ImportError:   # 직접 실행: 리포 루트가 아직 sys.path 에 없다
        from _root import ROOT

`scripts/qa/` 에서 직접 돌 때 맨 이름 `_root` 는 `scripts/qa/_root.py` 다리가 받는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
