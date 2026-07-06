# -*- coding: utf-8 -*-
"""Tests for the learner-method layer: user-model parsing + Activate integration."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import learner_ingest as lm  # noqa: E402
from api import activate as act  # noqa: E402

# A user-model-shaped patterns table (as in CONTEXT/identity/user-model.md).
USER_MODEL = """
## 의사결정 패턴

| 패턴 | 도메인 한정 | 관찰 횟수 | 예시 |
|---|---|---|---|
| **U-01 흐름 구조 선호** — 개념을 인과 흐름으로 묶어 제시 선호 | 전체 | 2 | 물리 01~03 흐름 |
| **U-06 점진적 정보 공개** — 첫 질문은 모호, 단계적 보강 | **FINANCE 한정** | 1 | 주거 결정 세션 |
| 그냥 산문 행 — 표가 아님 | | | |
"""


class ParseTests(unittest.TestCase):
    def test_parses_pattern_rows_only(self):
        rows = lm.parse_learner_model(USER_MODEL, owner="user:doheon")
        self.assertEqual([r["code"] for r in rows], ["U-01", "U-06"])  # prose/header skipped

    def test_fields_extracted(self):
        rows = lm.parse_learner_model(USER_MODEL, owner="user:doheon")
        u1 = rows[0]
        self.assertEqual(u1["pattern_id"], "user:doheon:U-01")
        self.assertEqual(u1["owner_id"], "user:doheon")
        self.assertEqual(u1["name"], "흐름 구조 선호")
        self.assertIn("인과 흐름", u1["description"])
        self.assertEqual(u1["domain"], "")           # 전체 → cross-domain (empty)
        self.assertEqual(u1["observations"], 2)

    def test_domain_specific_pattern_keeps_domain(self):
        rows = lm.parse_learner_model(USER_MODEL, owner="u")
        u6 = [r for r in rows if r["code"] == "U-06"][0]
        self.assertIn("FINANCE", u6["domain"])       # not 전체 → domain preserved

    def test_dedup_first_wins(self):
        dup = USER_MODEL + "| **U-01 다른표 재등장** — 파생본 | 전체 | 9 | x |\n"
        rows = lm.parse_learner_model(dup, owner="u")
        self.assertEqual(sum(r["code"] == "U-01" for r in rows), 1)


class PackIntegrationTests(unittest.TestCase):
    MODEL = [
        {"code": "U-01", "name": "흐름 구조 선호", "description": "인과 흐름 선호",
         "domain": "", "observations": 2},
    ]
    EPISODES = [{"id": "e1", "title": "곡률 시공간", "date": "2026-05-20",
                 "domain": "physics", "summary": "GR 도입"}]

    def test_learner_model_in_json_and_markdown(self):
        pack = act.build_context_pack(self.EPISODES, owner="user:doheon",
                                      learner_model=self.MODEL)
        self.assertEqual(len(pack.to_json()["learner_model"]), 1)
        md = pack.to_markdown()
        self.assertIn("## 학습법 — how user:doheon learns", md)
        self.assertIn("U-01 흐름 구조 선호", md)

    def test_learner_resource_in_manifest(self):
        pack = act.build_context_pack(self.EPISODES, owner="user:doheon",
                                      learner_model=self.MODEL)
        uris = [r["uri"] for r in pack.to_mcp_manifest()["resources"]]
        self.assertIn("memory://user:doheon/_learner", uris)

    def test_no_model_no_learner_section(self):
        pack = act.build_context_pack(self.EPISODES, owner="user:doheon")  # no model
        self.assertNotIn("학습법", pack.to_markdown())
        uris = [r["uri"] for r in pack.to_mcp_manifest()["resources"]]
        self.assertNotIn("memory://user:doheon/_learner", uris)


if __name__ == "__main__":
    unittest.main()
