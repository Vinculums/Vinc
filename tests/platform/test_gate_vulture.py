# -*- coding: utf-8 -*-
"""죽은 파이썬 게이트(gate_vulture)의 판정 시험.

vulture 자체는 돌리지 않는다(CI 의 core 잡에는 깔려 있지 않다). 여기서 잠그는 것은
게이트가 vulture 출력을 어떻게 가르는가다: 허용 목록 밖은 떨어지고, 허용 목록은
경로와 이름이 **둘 다** 맞아야 통과시키며, 못 읽은 파일은 실패가 아니라 경고다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import gate_vulture as G                      # noqa: E402

ALLOW = [("api/remote_client.py", "newurl"), ("vinc_mcp/*.py", "do_PUT")]


class JudgeTests(unittest.TestCase):
    def test_허용_목록_밖은_떨어진다(self):
        bad, ok, skipped = G.judge("api/x.py:3: unused import 'json' (90% confidence)", ALLOW)
        self.assertEqual((len(bad), ok, skipped), (1, [], []))

    def test_경로와_이름이_둘_다_맞아야_통과한다(self):
        out = "\n".join([
            "api\\remote_client.py:23: unused variable 'newurl' (100% confidence)",   # 윈도우 경로
            "api/other.py:9: unused variable 'newurl' (100% confidence)",             # 다른 파일
            "vinc_mcp/http_server.py:1441: unused method 'do_PUT' (100% confidence)",
        ])
        bad, ok, _ = G.judge(out, ALLOW)
        self.assertEqual(len(ok), 2)
        self.assertEqual(bad, ["api/other.py:9: unused variable 'newurl' (100% confidence)"])

    def test_도달_불가_코드도_떨어진다(self):
        bad, _, _ = G.judge("api/x.py:7: unsatisfiable 'if' condition (100% confidence)", ALLOW)
        self.assertEqual(len(bad), 1)

    def test_못_읽은_파일은_경고로_남는다(self):
        bad, ok, skipped = G.judge('store\\embedded_schema.py:19: invalid syntax at "# type: X"', [])
        self.assertEqual((bad, ok, len(skipped)), ([], [], 1))


class AllowFileTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "allow.txt"
            p.write_text(text, encoding="utf-8")
            return G.load_allow(p)

    def test_이유_없는_줄은_받지_않는다(self):
        with self.assertRaises(ValueError):
            self._load("api/x.py newurl\n")

    def test_주석과_빈_줄은_건너뛴다(self):
        self.assertEqual(self._load("# 머리\n\napi/x.py newurl  # 시그니처\n"),
                         [("api/x.py", "newurl")])

    def test_리포의_허용_목록이_읽힌다(self):
        allow = G.load_allow()
        self.assertIn(("vinc_mcp/http_server.py", "do_PUT"), allow)


if __name__ == "__main__":
    unittest.main()
