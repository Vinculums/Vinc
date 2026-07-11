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

    def test_preference_signals_survive_lossless(self):
        # D4(§10): 런타임 축적 신호(pinned/tags/access_count/last_access)는 재수집으로
        # 복원 불가 — pack.json 이 무손실 이관 통로. recall 투영이 이 신호를 담고,
        # build_context_pack 이 그대로 보존해야 한다.
        rows = [{"id": "e1", "title": "T", "pinned": True, "tags": ["keep"],
                 "access_count": 7, "last_access": "2026-07-04T10:00"}]
        ep = act.build_context_pack(rows, owner="u").to_json()["episodes"][0]
        for k, v in {"pinned": True, "tags": ["keep"], "access_count": 7,
                     "last_access": "2026-07-04T10:00"}.items():
            self.assertEqual(ep[k], v)

    def test_default_export_omits_learner_section(self):
        # WO §3 F6 · 설계 D6: Vinc 기본 pack 은 learner 섹션 부재(학습 프레이밍 미승계).
        # activate 기본 include_model=False.
        import inspect
        self.assertIs(inspect.signature(act.activate).parameters["include_model"].default, False)


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


class PackVocabularyTests(unittest.TestCase):
    """주제 어휘 동봉 — authored 만 실린다(proposed 는 팩에서 제외, D11-R2 수용기준)."""

    def test_no_theme_means_no_theme_block(self):
        d = act.build_context_pack(ROWS, owner="u").to_json()
        self.assertNotIn("theme", d)

    def test_theme_block_carries_authored_vocabulary(self):
        vocab = [{"name": "해체주의", "kind": "node", "status": "authored"}]
        d = act.build_context_pack(ROWS, owner="u", theme_id="fashion",
                                   vocabulary=vocab).to_json()
        self.assertEqual(d["theme"]["theme_id"], "fashion")
        self.assertEqual([t["name"] for t in d["theme"]["vocabulary"]], ["해체주의"])

    def test_activate_filters_proposed_out_of_pack(self):
        from store.theme_store import ThemeStore
        ts = ThemeStore(tempfile.mkdtemp())
        ts.create_theme("fashion")           # concept 등 시드는 proposed
        ts.propose_type("fashion", "node", "해체주의")
        ts.ratify("fashion", "해체주의")      # 이것만 authored
        res = act.activate(owner="u", theme_id="fashion", themes=ts)
        vocab = res["pack"]["json"]["theme"]["vocabulary"]
        names = {t["name"] for t in vocab}
        self.assertEqual(names, {"해체주의"})            # authored 만
        self.assertNotIn("concept", names)               # proposed 제외


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
