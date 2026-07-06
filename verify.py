#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stdlib-only dogfooding gate for weg-9000-memory.

Runs every tests/test_*.py with the unittest loader — no pytest, no neo4j,
no chonkie. Confirms the 6 engines import cleanly (lazy heavy deps), the store
schema/migrations render, the query API plans, and the CP adapter satisfies the
platform_bridge contract.

    python verify.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py")
    ok = unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    print("\nverify: OK" if ok else "\nverify: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
