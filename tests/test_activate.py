# -*- coding: utf-8 -*-
"""Tests for Activate — context-pack + MCP manifest builders, and file export."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import activate as act  # noqa: E402

ROWS = [
    {"id": "2026-06-01-s1", "title": "Billing 정책 결정", "date": "2026-06-01",
     "owner": "user:weg1456", "summary": "토큰 마진 플랜 확정", "pinned": True,
     "tags": ["billing", "kr"], "score": 5.0},
    {"id": "2026-05-20-s2", "title": "Login 흐름", "date": "2026-05-20",
     "owner": "user:weg1456", "summary": "OAuth 도입", "pinned": False, "tags": []},
]


class PackJsonTests(unittest.TestCase):
    def test_json_shape(self):
        pack = act.build_context_pack(ROWS, owner="user:weg1456", query="billing",
                                      generated_at="2026-06-30T00:00:00")
        d = pack.to_json()
        self.assertEqual(d["kind"], "weg-9000-memory/context-pack")
        self.assertEqual(d["owner"], "user:weg1456")
        self.assertEqual(d["episode_count"], 2)
        self.assertEqual(d["episodes"][0]["title"], "Billing 정책 결정")


class PackMarkdownTests(unittest.TestCase):
    def test_markdown_has_title_pin_tags(self):
        md = act.build_context_pack(ROWS, owner="user:weg1456").to_markdown()
        self.assertIn("# Memory Context Pack — user:weg1456", md)
        self.assertIn("## Billing 정책 결정 📌", md)   # pinned marker
        self.assertIn("tags: billing, kr", md)
        self.assertIn("토큰 마진 플랜 확정", md)
        self.assertNotIn("📌\n- date: 2026-05-20", md)  # unpinned has no marker

    def test_markdown_empty(self):
        md = act.build_context_pack([], owner="u1").to_markdown()
        self.assertIn("_(no episodes in scope)_", md)


class McpManifestTests(unittest.TestCase):
    def test_manifest_resource_uris(self):
        man = act.build_context_pack(ROWS, owner="user:weg1456").to_mcp_manifest()
        self.assertEqual(man["schemaVersion"], act.MCP_SCHEMA_VERSION)
        self.assertEqual(man["resources"][0]["uri"], "memory://user:weg1456/2026-06-01-s1")
        self.assertEqual(man["resources"][0]["mimeType"], "text/markdown")
        self.assertEqual(man["pack"]["uri"], "memory://user:weg1456/_pack")


class WriteTests(unittest.TestCase):
    def test_write_pack_creates_three_files(self):
        pack = act.build_context_pack(ROWS, owner="user:weg1456",
                                      generated_at="2026-06-30T00:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            paths = act.write_pack(pack, tmp)
            for key in ("markdown", "json", "mcp"):
                self.assertTrue(Path(paths[key]).is_file())
            # written json round-trips
            data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(data["episode_count"], 2)


class ActivateEntryTests(unittest.TestCase):
    def test_activate_without_db_returns_inline_pack(self):
        # no DB -> recall returns no rows; pack is empty but well-formed
        res = act.activate(owner="user:weg1456")
        self.assertEqual(res["owner"], "user:weg1456")
        self.assertFalse(res["executed"])
        self.assertEqual(res["episode_count"], 0)
        self.assertIn("markdown", res["pack"])
        self.assertIn("mcp", res["pack"])


if __name__ == "__main__":
    unittest.main()
