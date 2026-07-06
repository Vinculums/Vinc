# -*- coding: utf-8 -*-
"""vinc_mcp/audit.py 순수 단위 테스트 — 파일 append·digest 요약(§6) 검증."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import audit  # noqa: E402


class DigestArgsTests(unittest.TestCase):
    def test_ingest_digest_summarizes_counts_and_domains_no_raw_text(self):
        args = {"nodes": [{"id": "a", "domain": "fashion", "title": "SECRET TITLE"},
                          {"id": "b", "domain": "fashion"}],
                "edges": [{"from": "a", "to": "b", "type": "relates_to"}]}
        d = audit.digest_args("vinc_ingest", args)
        self.assertEqual(d, {"nodes": 2, "edges": 1, "domains": ["fashion"]})
        self.assertNotIn("SECRET TITLE", json.dumps(d))

    def test_other_tools_digest_to_key_presence_only(self):
        d = audit.digest_args("vinc_search", {"query": "margiela", "domain": None, "limit": 5})
        self.assertEqual(d, {"keys": ["limit", "query"]})   # domain=None 제외, 원문(margiela) 없음


class RecordTests(unittest.TestCase):
    def test_appends_one_json_line_with_expected_shape(self):
        with tempfile.TemporaryDirectory() as d:
            audit.record(d, tool="vinc_search", source="mcp", session="s1",
                        args={"query": "x"}, result={"executed": True, "rows": []},
                        warnings=[], duration_ms=12.34, ts="2026-07-06T00:00:00Z")
            path = Path(d) / "logs" / "mcp-audit.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["tool"], "vinc_search")
            self.assertEqual(entry["session"], "s1")
            self.assertEqual(entry["duration_ms"], 12.3)
            self.assertEqual(entry["warnings"], [])

    def test_multiple_records_append_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(3):
                audit.record(d, tool="vinc_recall", source="mcp", session="s1",
                            args={}, result={}, warnings=None, duration_ms=1.0, ts=str(i))
            path = Path(d) / "logs" / "mcp-audit.jsonl"
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
