# -*- coding: utf-8 -*-
"""pack_ingest (A+2 2단계, §10) 순수 단위 테스트 — DB 불요.

load_pack 검증·에피소드 키 매핑·id 없는 행 스킵·멱등 카운트를 가짜 세션으로 검증.
실제 오버레이 왕복(재수집→export→ingest)은 임베디드 e2e(test_embedded_backend)에서.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import pack_ingest as pi  # noqa: E402


class _FakeSession:
    """session.run(cypher, **params) 호출을 기록만 하는 가짜 세션."""

    def __init__(self):
        self.calls: list[dict] = []

    def run(self, cypher, **params):
        self.calls.append(params)


class LoadPackTests(unittest.TestCase):
    def _write(self, obj) -> str:
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(obj, f)
        f.close()
        return f.name

    def test_valid_kind_loads(self):
        path = self._write({"kind": pi.PACK_KIND, "owner": "u", "episodes": []})
        pack = pi.load_pack(path)
        self.assertEqual(pack["owner"], "u")

    def test_wrong_kind_rejected(self):
        # 다른 JSON(예: mcp.json·임의 파일)을 실수로 적재하지 않도록 방어(§10).
        path = self._write({"kind": "something/else", "episodes": []})
        with self.assertRaises(ValueError):
            pi.load_pack(path)


class ParamMappingTests(unittest.TestCase):
    def test_projection_keys_map_to_columns(self):
        # 투영 키(id/owner/tenant)를 스키마 컬럼(episode_id/owner_id/tenant_id)으로 매핑.
        ep = {"id": "e1", "owner": "user:me", "tenant": "t:local", "title": "T",
              "domain": "d", "date": "2026-07-01", "workspace": "w", "summary": "s",
              "pinned": True, "tags": ["a"], "access_count": 5, "last_access": "2026-07-02"}
        p = pi._episode_params(ep)
        self.assertEqual(p["episode_id"], "e1")
        self.assertEqual(p["owner_id"], "user:me")
        self.assertEqual(p["tenant_id"], "t:local")
        self.assertEqual(p["pinned"], True)
        self.assertEqual(p["tags"], ["a"])
        self.assertEqual(p["access_count"], 5)

    def test_missing_keys_become_none(self):
        p = pi._episode_params({"id": "e1"})
        # 누락 키는 None → Cypher 의 coalesce 가 흡수(팩 우선/기존 우선 규칙대로).
        self.assertIsNone(p["pinned"])
        self.assertIsNone(p["last_access"])
        self.assertEqual(p["episode_id"], "e1")


class OverlayPackTests(unittest.TestCase):
    def test_skips_rows_without_id(self):
        # episode_id 없는 행은 멱등 키가 없어 스킵(오버레이 대상 아님).
        s = _FakeSession()
        pack = {"episodes": [{"id": "e1", "pinned": True}, {"title": "no-id"},
                             {"id": "e2"}]}
        n = pi.overlay_pack(s, pack)
        self.assertEqual(n, 2)
        self.assertEqual(len(s.calls), 2)
        self.assertEqual({c["episode_id"] for c in s.calls}, {"e1", "e2"})

    def test_all_params_present_for_embedded_dialect(self):
        # Kùzu 계보(ladybug)는 미사용 파라미터를 거부 → 모든 파라미터가 매 호출에 존재해야 한다.
        s = _FakeSession()
        pi.overlay_pack(s, {"episodes": [{"id": "e1"}]})
        expected = {"episode_id", "pinned", "tags", "access_count", "last_access",
                    "title", "owner_id", "tenant_id", "domain", "date",
                    "workspace", "summary"}
        self.assertEqual(set(s.calls[0]), expected)


if __name__ == "__main__":
    unittest.main()
