# -*- coding: utf-8 -*-
"""MCP 가 알리는 버전은 **제품 버전이어야 한다.**

🔴 2026-09-11 측정: 제품이 0.9.1 인데 핸드셰이크의 `serverInfo.version` 은
**0.3.0**(2026-07-18, v0.3.0 릴리스)이었다. 어시스턴트가 화면에 그리는 유일한 Vinc
버전이 그 값이라 사용자에게는 두 릴리스 전 제품으로 보인다.

이 고장이 **여섯 릴리스를 살아남은 이유**가 이 시험이 잠그는 것이다: 릴리스 게이트
(`scripts/version_sync.py --check`)가 자리 목록을 근거로 판정하는데 그 목록에 이
파일이 없었다. 값이 어긋나도 아무 기능이 깨지지 않으므로 증상도 없다. 그래서 여기서는
두 가지를 따로 본다 — 값이 같은가, 그리고 **게이트가 그 자리를 보고 있는가.**
목록에서 빠지면 값을 맞춰 둔 것은 다음 범프에 다시 어긋난다.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vinc_mcp                                            # noqa: E402
from scripts import version_sync                           # noqa: E402


def _product_version() -> str:
    with open(os.path.join(ROOT, "app", "package.json"), encoding="utf-8") as fh:
        return json.load(fh)["version"]


class McpVersion(unittest.TestCase):
    def test_핸드셰이크_버전이_제품_버전과_같다(self):
        self.assertEqual(vinc_mcp.__version__, _product_version())

    def test_serverInfo_가_그_값을_싣는다(self):
        """상수만 맞춰 두고 핸드셰이크가 딴 값을 싣는 일이 없게 한다. 협상 리비전마다
        본다 — `_dispatch` 가 리비전에 따라 `serverInfo` 를 다르게 짓는다."""
        from vinc_mcp import rpc
        d = rpc.Dispatcher.__new__(rpc.Dispatcher)   # bridge 없이 _dispatch 만 본다
        for pv in rpc.PROTOCOL_VERSIONS:
            with self.subTest(protocolVersion=pv):
                info = d._dispatch("initialize", {"protocolVersion": pv})["serverInfo"]
                self.assertEqual(info["version"], _product_version())

    def test_릴리스_게이트가_이_자리를_본다(self):
        """🔴 근본 처방. 값이 아니라 **목록**을 잠근다 — 이 자리가 빠지면 다음 범프에서
        조용히 다시 어긋나고, 그것이 0.3.0 이 여섯 릴리스를 살아남은 기전이다."""
        paths = {os.path.normcase(str(s.path)) for s in version_sync._sites()}
        want = os.path.normcase(os.path.join(ROOT, "vinc_mcp", "__init__.py"))
        self.assertIn(want, paths,
                      "version_sync 의 자리 목록에 vinc_mcp/__init__.py 가 없다")

    def test_모든_자리가_한_버전이다(self):
        """게이트가 지금 이 트리에서 실제로 초록인가(태그 런이 도는 그 판정)."""
        found, missing = version_sync.collect()
        self.assertEqual(missing, [], f"읽지 못한 자리: {missing}")
        self.assertEqual(len(set(found.values())), 1, f"버전 불일치: {found}")


if __name__ == "__main__":
    unittest.main()
