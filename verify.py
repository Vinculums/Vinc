#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dogfooding gate — imports every tests/test_*.py with the unittest loader.

⚠ **이것은 전량이 아니다.** `unittest.TestLoader().discover` 는 `TestCase` 메서드만
수집하고 모듈 수준 `def test_*` 는 못 본다(tests/ 에 `load_tests` 도 없다). 실측
2026-09-07: 같은 트리에서 unittest 1536건 · pytest 1977건 — **441건이 이 러너에
안 보인다.** 그 441건에 소유자 격리·쓰기 테넌시·동기화가 들어 있다.
이 첫 줄이 "runs every" 라고 적혀 있던 탓에, 이 러너가 도는 CI 잡을 보고 "전량이
돈다"고 읽은 판정이 실제로 나왔다. 전량은 `pytest tests` 가 돌고 CI 에서는
`core-pkns` 잡이 그것을 무조건 돈다(.github/workflows/ci.yml).

Confirms the 6 engines import cleanly (lazy heavy deps), the store schema/
migrations render, the query API plans, and the CP adapter satisfies the
platform_bridge contract.

    python verify.py

Deps: the **runner** is stdlib unittest (no pytest runner, no neo4j, no
chonkie), but four test modules (test_mcp_connect · test_mcp_graph_proxy ·
test_mcp_store · test_purge) `import pytest` for its fixture/raises helpers —
without it they fail at import time and surface as `_FailedTest`, not as
skips. This docstring used to claim "no pytest" outright; the drift only
showed up when CI ran on a clean machine (a local venv had it installed).
Extras that gate whole modules: `ladybug` (embedded store e2e — 17 modules
skip without it) and `argon2-cffi` (credential paths).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def build_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    """`tests/` 와 그 아래 팀 폴더(`tests/<패키지>/`)를 **폴더마다 따로** 찾는다.

    팀 폴더에는 `__init__.py` 를 두지 않는다. 두면 폴더 이름이 패키지가 되는데,
    `tests/` 가 sys.path 에 있으므로 `tests/platform` 이 표준 라이브러리 `platform` 을
    가린다. 그런데 unittest 는 패키지가 아닌 하위 폴더로 내려가지 않으므로, 한 번의
    discover 로는 팀 폴더의 테스트를 하나도 못 본다(본 CI 잡의 "Ran N" 하한 300 이 그때
    잡는다). 그래서 각 폴더를 자기 최상위로 두고 따로 discover 한다. 테스트 파일 이름은
    리포 전체에서 하나뿐이라 모듈 이름이 겹치지 않는다. 근거: [[vinc.plan.team-split]]"""
    tests = ROOT / "tests"
    suite = loader.discover(str(tests), pattern="test_*.py")
    for sub in sorted(p for p in tests.iterdir()
                      if p.is_dir() and p.name not in ("fixtures", "__pycache__")):
        suite.addTests(loader.discover(str(sub), pattern="test_*.py", top_level_dir=str(sub)))
    return suite


def main() -> int:
    # 임시 디렉터리 우리. 이 러너에는 pytest 픽스처가 없으니 여기서 건다
    # (본 CI 잡이 이 경로다). 이유는 tests/_tmpjail.py.
    sys.path.insert(0, str(ROOT / "tests"))
    from _tmpjail import install as install_tmp_jail
    install_tmp_jail()

    suite = build_suite(unittest.TestLoader())
    ok = unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    print("\nverify: OK" if ok else "\nverify: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
