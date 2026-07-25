# -*- coding: utf-8 -*-
"""S2 도메인 관리 — owner 스코프 일괄 편집(실엔진).

여기는 **실엔진**이어야 한다. 목으로는 revstamp(HLC·seq 메타 왕복)를 흉내낼 수
없고, 이 기능의 급소가 바로 "A 의 도메인 변경이 B 에 안 새는가"라서 2-owner 를
같은 스토어에 두고 실제로 확인해야 한다(SEC-1 교훈 — 스텁 초록은 증거가 아니다).

설계 불변식(query.rename_domain 독스트링과 짝):
  · 전역 `:Domain` 노드는 불변 — 무소유 공유 구조라 지우면 남의 그래프가 깨진다.
  · 바뀌는 건 내 Concept/Episode/Record 의 `domain` 문자열뿐.
  · new='' = 미분류로 내림(도메인은 목록에서 사라지고 **노드는 남는다**).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag   # noqa: E402
from api import query as q         # noqa: E402
from store.embedded_backend import _import_driver   # noqa: E402

_DRIVER, _ = _import_driver()
A, B = "user:alice", "user:bob"


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DomainRenameE2E(unittest.TestCase):
    def setUp(self):
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config(
            {"data_dir": os.path.join(tempfile.mkdtemp(), "db")}))
        self.store.provision(dim=4)
        self.addCleanup(self.store.close)
        # 두 owner 가 **같은 이름**의 도메인을 쓴다 — 전역 Domain 공유 상황 재현.
        for owner, pre in ((A, "a"), (B, "b")):
            frag.merge_fragment({"owner": owner, "edges": [], "nodes": [
                {"id": f"concept:{pre}1", "type": "concept", "title": "C1", "domain": "physics"},
                {"id": f"concept:{pre}2", "type": "concept", "title": "C2", "domain": "physics"},
                {"id": f"episode:{pre}e", "type": "episode", "title": "E", "domain": "physics"},
            ]}, store=self.store)

    def _domains(self, owner):
        return {r["domain"]: r["total"]
                for r in q.domains(owner=owner, store=self.store).get("rows", [])}

    def test_rename_is_owner_scoped(self):
        """A 의 rename 이 같은 이름을 쓰는 B 에 **전혀** 닿지 않는다(SEC-1)."""
        out = q.rename_domain(owner=A, old="physics", new="astrophysics", store=self.store)
        self.assertTrue(out["executed"])
        self.assertEqual(out["moved"], 3)          # Concept 2 + Episode 1
        self.assertEqual(self._domains(A), {"astrophysics": 3})
        self.assertEqual(self._domains(B), {"physics": 3})   # B 불변

    def test_empty_new_unassigns_but_keeps_nodes(self):
        """new='' = 미분류 — 도메인은 사라지고 노드는 남는다(삭제와 구분)."""
        q.rename_domain(owner=A, old="physics", new="", store=self.store)
        self.assertEqual(self._domains(A), {})
        rows = self.store.run(
            "MATCH (c:Concept) WHERE c.owner_id = $o RETURN count(c) AS n", {"o": A})
        self.assertEqual(int(rows[0]["n"]), 2)     # 노드 보존

    def test_stamps_revision_for_sync(self):
        """변경 노드마다 revstamp — 안 찍으면 다른 기기가 옛 도메인을 유지한다."""
        before = self.store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $i RETURN c.rev AS rev",
            {"i": "concept:a1"})[0]["rev"]
        q.rename_domain(owner=A, old="physics", new="astro", store=self.store)
        row = self.store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $i "
            "RETURN c.rev AS rev, c.updated_at AS ua, c.sync_seq AS seq",
            {"i": "concept:a1"})[0]
        self.assertGreater(int(row["rev"]), int(before or 0))
        self.assertTrue(row["ua"])
        self.assertGreater(int(row["seq"] or 0), 0)

    def test_global_domain_nodes_untouched(self):
        """전역 `:Domain` 노드는 생성·삭제 모두 안 한다(다른 소유자와 공유 구조)."""
        before = self.store.run("MATCH (d:Domain) RETURN count(d) AS n")[0]["n"]
        q.rename_domain(owner=A, old="physics", new="astro", store=self.store)
        after = self.store.run("MATCH (d:Domain) RETURN count(d) AS n")[0]["n"]
        self.assertEqual(int(after), int(before))

    def test_noop_and_missing_domain_are_safe(self):
        same = q.rename_domain(owner=A, old="physics", new="physics", store=self.store)
        self.assertTrue(same["executed"])
        self.assertEqual(same["moved"], 0)
        missing = q.rename_domain(owner=A, old="nope", new="x", store=self.store)
        self.assertTrue(missing["executed"])
        self.assertEqual(missing["moved"], 0)
        self.assertEqual(self._domains(A), {"physics": 3})   # 아무것도 안 바뀜


if __name__ == "__main__":
    unittest.main()
