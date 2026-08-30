#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dogfooding gate — runs every tests/test_*.py with the unittest loader.

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


def main() -> int:
    # 임시 디렉터리 우리. 이 러너에는 pytest 픽스처가 없으니 여기서 건다
    # (본 CI 잡이 이 경로다). 이유는 tests/_tmpjail.py.
    sys.path.insert(0, str(ROOT / "tests"))
    from _tmpjail import install as install_tmp_jail
    install_tmp_jail()

    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    ok = unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    print("\nverify: OK" if ok else "\nverify: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
