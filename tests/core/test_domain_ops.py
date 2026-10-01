# -*- coding: utf-8 -*-
"""S2 도메인 관리 — owner 스코프 일괄 편집(실엔진).

여기는 **실엔진**이어야 한다. 목으로는 revstamp(HLC·seq 메타 왕복)를 흉내낼 수
없고, 이 기능의 급소가 바로 "A 의 도메인 변경이 B 에 안 새는가"라서 2-owner 를
같은 스토어에 두고 실제로 확인해야 한다(SEC-1 교훈 — 스텁 초록은 증거가 아니다).

설계 불변식(query.rename_domain 독스트링과 짝):
  · 전역 `:Domain` 노드를 **지우거나 개명하지 않는다** — 무소유 공유 구조라 남의
    그래프가 깨진다.
  · 바뀌는 건 내 Concept/Episode/Record 의 `domain` 문자열(+ v11 부터 그 문자열의
    그래프 표현인 IN_DOMAIN 엣지)뿐.
  · new='' = 미분류로 내림(도메인은 목록에서 사라지고 **노드는 남는다**).

⚠ 이 파일은 원래 "전역 Domain 노드는 생성도 안 한다"고 적고 있었는데 그건 실제보다
넓었다. `engines/bridges_ingest.py:224` 와 `vinc_ingest` 의 `type:"domain"` 이 이미
Domain 노드를 MERGE 하고 있었다 — 즉 생성은 금지된 적이 없다. 그리고 v11 의
IN_DOMAIN 은 붙일 좌표가 있어야 하므로 생성이 **필요하다**. 위험한 것은 생성이
아니라 삭제·개명이고(남이 그 좌표를 쓰고 있다), 유출도 아니다: 원시 Domain 테이블을
소유 스코프 없이 읽는 제품 코드는 0건이며(2026-08-09 전수), 계층 조회는
`domain_hierarchy.subtree_scoped_cypher` 로 소유 노드에 앵커를 건다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag   # noqa: E402
from api import query as q         # noqa: E402
from store import pkns            # noqa: E402
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
        """new='' = 미분류 — 도메인은 사라지고 노드는 남는다(삭제와 구분).
        미분류는 이제 센서스에서 부재가 아니라 "(none)" 행이다(근거:
        [[vinc.ops.topic-assignment-eval]] §1.1) — 그래서 빈 dict 가 아니라 그 행을 기대한다."""
        q.rename_domain(owner=A, old="physics", new="", store=self.store)
        doms = self._domains(A)
        self.assertEqual(set(doms), {"(none)"})
        self.assertEqual(doms["(none)"], 3)        # Concept 2 + Episode 1, 전부 보존
        rows = self.store.run(
            "MATCH (c:Concept) WHERE c.owner_id = $o RETURN count(c) AS n", {"o": A})
        self.assertEqual(int(rows[0]["n"]), 2)     # 노드 보존

    def test_stamps_revision_for_sync(self):
        """변경 노드마다 revstamp — 안 찍으면 다른 기기가 옛 도메인을 유지한다."""
        before = self.store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $i RETURN c.rev AS rev",
            {"i": pkns.key(A, "concept:a1")})[0]["rev"]
        q.rename_domain(owner=A, old="physics", new="astro", store=self.store)
        row = self.store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $i "
            "RETURN c.rev AS rev, c.updated_at AS ua, c.sync_seq AS seq",
            {"i": pkns.key(A, "concept:a1")})[0]
        self.assertGreater(int(row["rev"]), int(before or 0))
        self.assertTrue(row["ua"])
        self.assertGreater(int(row["seq"] or 0), 0)

    def test_global_domain_nodes_are_never_removed_or_renamed(self):
        """전역 `:Domain` 노드는 **지우지도 개명하지도** 않는다(다른 소유자와 공유).

        새 좌표 생성은 허용된다 — IN_DOMAIN(v11)이 붙을 자리가 있어야 하고, 이미
        bridges_ingest 가 같은 일을 한다. 위험한 것은 **없어지는 것**이다: B 가
        `physics` 를 쓰고 있는데 A 의 이름변경이 그 행을 지우면 B 가 깨진다."""
        names = lambda: {r["n"] for r in                       # noqa: E731
                         self.store.run("MATCH (d:Domain) RETURN d.name AS n")}
        before = names()
        q.rename_domain(owner=A, old="physics", new="astro", store=self.store)
        after = names()
        self.assertTrue(before <= after,
                        f"공유 좌표가 사라졌다: {sorted(before - after)}")
        # B 의 노드는 여전히 physics 다 — 좌표도 그대로여야 그 귀속이 성립한다.
        self.assertEqual({"physics": 3}, self._domains(B))

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
