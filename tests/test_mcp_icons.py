# -*- coding: utf-8 -*-
"""커넥터 아이콘 표면 — initialize serverInfo 와 /favicon.* 서빙.

왜 테스트하나: 이 값들은 **우리가 확인할 수 없는 곳**(claude.ai 커넥터 카드)에서
쓰인다. 지금은 그쪽이 아이콘을 아예 안 읽으므로(claude-ai-mcp#152) 화면으로 회귀를
잡을 방법이 없다 — 계약이 깨져도 조용하다. 그래서 계약 자체를 못 박는다.
"""
from __future__ import annotations

import unittest

from vinc_mcp import http_server, rpc


class ServerInfoIconsTest(unittest.TestCase):
    def _init(self) -> dict:
        d = rpc.Dispatcher.__new__(rpc.Dispatcher)      # bridge 없이 _dispatch 만 본다
        return d._dispatch("initialize", {"protocolVersion": rpc.LATEST_PROTOCOL_VERSION})

    def test_serverInfo_에_icons_와_websiteUrl_이_실린다(self):
        info = self._init()["serverInfo"]
        self.assertEqual(info["name"], "vinc")
        self.assertEqual(info["websiteUrl"], "https://vincs.io")
        self.assertTrue(info["icons"], "icons 가 비어 있다")

    def test_PNG_가_첫_항목이다(self):
        """스펙상 클라이언트는 PNG·JPEG 만 필수 지원이고 SVG 는 선택이다. SVG 를 앞에
        두면 SVG 를 못 읽는 클라이언트가 첫 항목만 보고 포기할 수 있다."""
        icons = self._init()["serverInfo"]["icons"]
        self.assertEqual(icons[0]["mimeType"], "image/png")
        self.assertTrue(icons[0]["src"].endswith(".png"))

    def test_아이콘_src_가_절대_https_다(self):
        """상대경로면 클라이언트가 무엇을 기준으로 풀지 알 수 없다."""
        for ic in self._init()["serverInfo"]["icons"]:
            self.assertTrue(ic["src"].startswith("https://"), ic["src"])

    def test_프로토콜_버전은_올리지_않았다(self):
        """icons 는 2025-11-25 스펙 필드지만 그 리비전 전체를 준수한다고 주장하지
        않는다 — 협상 목록을 올리면 우리가 구현하지 않은 것까지 약속하게 된다."""
        self.assertNotIn("2025-11-25", rpc.PROTOCOL_VERSIONS)


class FaviconAssetTest(unittest.TestCase):
    def test_PNG_상수가_진짜_PNG_다(self):
        self.assertEqual(http_server._FAVICON_PNG[:4], b"\x89PNG")
        self.assertGreater(len(http_server._FAVICON_PNG), 500)

    def test_PNG_가_128px_다(self):
        """serverInfo.icons 의 sizes 와 실제 래스터가 어긋나면 클라이언트가 잘못
        스케일한다. 둘을 함께 못 박는다."""
        import struct
        w, h = struct.unpack(">II", http_server._FAVICON_PNG[16:24])
        self.assertEqual((w, h), (128, 128))
        d = rpc.Dispatcher.__new__(rpc.Dispatcher)
        png = d._dispatch("initialize", {})["serverInfo"]["icons"][0]
        self.assertEqual(png["sizes"], f"{w}x{h}")

    def test_SVG_상수는_그대로_있다(self):
        self.assertIn("<svg", http_server._FAVICON_SVG)


if __name__ == "__main__":
    unittest.main()
