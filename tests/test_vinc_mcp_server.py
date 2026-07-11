# -*- coding: utf-8 -*-
"""vinc_mcp/server.py — FastMCP 앱 구성·도구 등록 검증(mcp SDK 설치 환경에서만).

mcp SDK 는 `vinc[mcp]` extra(선택)라 이 환경(CI 포함)엔 없을 수 있다 — 다른
드라이버 의존 테스트(test_embedded_backend.py)와 동일한 skipIf 패턴.
`SidecarHandle` 을 직접 주입해 실 spawn/attach 없이 등록 표면만 검증한다(그 왕복은
test_vinc_mcp_lifecycle.py 소관).
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import mcp as _mcp_sdk  # noqa: F401
except ImportError:
    _mcp_sdk = None


@unittest.skipIf(_mcp_sdk is None, "mcp SDK(vinc[mcp] extra) 미설치 — vinc_mcp.server skip")
class BuildAppTests(unittest.TestCase):
    def setUp(self):
        from vinc_mcp.lifecycle import SidecarHandle
        from vinc_mcp.server import build_app
        self.tmp = tempfile.mkdtemp()
        handle = SidecarHandle(host="127.0.0.1", port=1, token="t", pid=1, spawned_by_us=False)
        self.app = build_app(data_dir=self.tmp, handle=handle)

    def test_registers_expected_tools(self):
        names = {t.name for t in asyncio.run(self.app.list_tools())}
        self.assertEqual(names, {
            "vinc_search", "vinc_recall", "vinc_graph", "vinc_decide", "vinc_ingest",
            "vinc_themes_list", "vinc_themes_create", "vinc_themes_activate",
            "vinc_types_list", "vinc_types_ratify", "vinc_types_reject", "vinc_types_rename"})

    def test_vinc_graph_tool_reports_gap_without_touching_bridge(self):
        # 실 사이드카가 없는 handle(port=1) 이라 bridge 를 건드리는 도구였다면 실패
        # 했을 것 — vinc_graph 는 미구현 응답을 직접 반환하므로 안전하게 통과한다.
        result = asyncio.run(self.app.call_tool("vinc_graph", {"seed_id": "concept:a"}))
        text = result[0].text if isinstance(result, list) else str(result)
        self.assertIn("seed-based traversal", text)


if __name__ == "__main__":
    unittest.main()
