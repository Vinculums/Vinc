# -*- coding: utf-8 -*-
"""임시 디렉터리 우리가 실제로 걸려 있는지 본다.

이 리포에는 러너가 둘(`verify.py`·pytest)이고 우리는 그 둘에 **따로** 걸린다.
한쪽 훅이 사라져도 테스트는 전부 초록으로 남고 `/tmp` 만 조용히 부푼다 — 그
드리프트가 26회쯤 쌓여 러너가 통째로 멈춘 것이 2026-08-26 사고였다. 그러니 재는
것은 우리 코드가 아니라 **지금 이 프로세스의 기본 임시 자리**다. 그래야 훅이 빠진
러너에서만 빨갛게 뜬다. 근거는 tests/_tmpjail.py.
"""
from __future__ import annotations

import os
import tempfile
import unittest


class TmpJailTests(unittest.TestCase):
    def test_new_temp_dirs_land_in_the_jail(self):
        d = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, d)
        self.assertIn("vinc-tests-", d,
                      "임시 디렉터리가 우리 밖에 생겼다 — 이 러너의 우리 훅이 "
                      "빠졌다(pytest 면 tests/conftest.py, unittest 면 verify.py). "
                      "이대로 두면 시스템 /tmp 가 CI 러너를 멈출 때까지 쌓인다")

    def test_child_processes_inherit_the_jail(self):
        # 사이드카·엔트리포인트 테스트가 파이썬을 새로 띄운다. env 를 안 옮기면
        # 그 자식들이 만드는 것은 우리 밖에 남는다.
        for key in ("TMPDIR", "TEMP", "TMP"):
            self.assertIn("vinc-tests-", os.environ.get(key, ""), key)


if __name__ == "__main__":
    unittest.main()
