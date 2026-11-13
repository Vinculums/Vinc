# -*- coding: utf-8 -*-
"""PK 네임스페이싱(v0.8.4 axis 1, design A) — **와이어 계약**.

PR-B 가 쓰기를 합성하고 PR-C 가 읽기를 합성한 뒤 남는 질문은 하나다: 물리 키가
스토어 **밖으로** 새는 곳이 있는가. 새면 두 가지가 동시에 깨진다.

  1. 프라이버시 — 물리 키는 `owner\\x1fid` 라, 번들 한 벌이 남의 기기에 계정
     식별자를 실어 보낸다.
  2. 수렴 — 받는 쪽은 자기 owner 로 다시 합성한다. 논리 슬러그가 와야 그 합성이
     성립하고, 물리 키가 오면 `owner\\x1fowner\\x1fid` 가 되거나(멱등 가드가
     막지만) 남의 owner 를 물려받은 행이 생긴다.

그래서 여기서 재는 것은 값 하나뿐이다: **번들 어디에도 `SEP` 이 없다.** 그리고
그 번들이 다른 스토어에서 실제로 적용돼 논리 id 로 읽히는지까지 왕복시킨다.

플래그가 꺼진 모드에서도 같은 단정이 성립한다(합성이 항등이라 자명하게) — 두 모드를
같은 테스트로 도는 것이 의도다. 근거: [[vinc.plan.node-pk-namespacing]] §7
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import pkns  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _NAME = import_driver()

A_OWNER = "user:alice"
B_OWNER = "user:bob"


def _walk(value):
    """번들 안의 모든 문자열(키·값·중첩 리스트·딕트)을 흘려보낸다."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(k)
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class SyncWireTests(unittest.TestCase):
    """기기 둘 사이에서 번들이 논리 id 만 나르는가."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        # ⚠ `MEMORY_DATA_DIR` 도 복원 목록에 든다 — 안 넣으면 이 클래스가 끝난 뒤
        # 지워진 임시 경로를 가리킨 채 남아 뒤에 도는 파일이 깨진다(실측 이력이
        # tests/core/test_embedded_backend.py 의 같은 픽스처에 적혀 있다).
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "a")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        self.a = self._store("a")
        self.b = self._store("b")

    def _store(self, tag):
        from store.config import load_config
        from store.backend import open_store
        os.environ["MEMORY_DATA_DIR"] = os.path.join(self._tmp, tag)
        cfg = load_config()
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        return st

    def tearDown(self):
        for st in (self.a, self.b):
            try:
                st.close()
            except Exception:      # noqa: BLE001 — 정리 실패가 테스트 결과를 가리지 않게
                pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _seed_a(self, owner=A_OWNER):
        from api import fragment as frag
        r = frag.merge_fragment({"owner": owner, "nodes": [
            {"id": "concept:relativity", "type": "concept", "title": "Relativity",
             "domain": "phys"},
            {"id": "concept:gravity", "type": "concept", "title": "Gravity",
             "domain": "phys"},
            {"id": "record:measured", "type": "record", "title": "Measured once",
             "domain": "phys"},
        ], "edges": [
            {"from": "concept:relativity", "to": "concept:gravity", "type": "relates_to"},
            {"from": "record:measured", "to": "concept:relativity", "type": "relates_to"},
        ]}, store=self.a)
        self.assertTrue(r["executed"], r)
        self.assertEqual(r["merged"]["nodes"]["created"], 3, r)
        return r

    # ── 1. 번들에 분리자가 없다 ──────────────────────────────────────────────
    def test_export_bundle_carries_no_separator(self):
        from api import sync
        self._seed_a()
        bundle = sync.export_delta(owner=A_OWNER, since=0, domains=["phys"],
                                   store=self.a)
        self.assertTrue(bundle["nodes"], bundle)
        leaked = [v for v in _walk(bundle) if pkns.SEP in v]
        self.assertEqual(leaked, [], f"물리 키가 와이어로 샜다: {leaked}")
        # 그리고 노드 id 는 저작한 그대로의 논리 슬러그다.
        self.assertIn("concept:relativity", [n["id"] for n in bundle["nodes"]])

    def test_edge_tombstone_wire_columns_stay_logical(self):
        """`ekey` 는 합성되지만 `src`/`rel`/`dst` **열**은 와이어로 나가므로 논리다.
        여기가 갈리면 받는 쪽이 자기 키로 다시 합성할 재료를 못 받는다."""
        from api import query as q
        from api import sync
        self._seed_a()
        res = q.delete_edge("concept:relativity", "relates_to", "concept:gravity",
                            owner=A_OWNER, store=self.a)
        self.assertTrue(res["found"], res)
        bundle = sync.export_delta(owner=A_OWNER, since=0, domains=["phys"],
                                   store=self.a)
        ts = bundle["edge_tombstones"]
        self.assertEqual(len(ts), 1, bundle)
        self.assertEqual(ts[0]["src"], "concept:relativity")
        self.assertEqual(ts[0]["dst"], "concept:gravity")
        self.assertEqual([v for v in _walk(ts) if pkns.SEP in v], [])

    # ── 2. 받는 쪽이 자기 키로 다시 합성한다 ────────────────────────────────
    def test_delta_lands_under_the_receiver_own_key(self):
        """B 가 A 의 번들을 적용하면, B 의 스토어에는 **B 의** 물리 키가 있어야 한다.
        A 의 키가 그대로 앉으면 B 의 모든 조회가 그 행을 못 짚는다(무음)."""
        from api import query as q
        from api import sync
        self._seed_a()
        bundle = sync.export_delta(owner=A_OWNER, since=0, domains=["phys"],
                                   store=self.a)
        out = sync.apply_delta(owner=B_OWNER, bundle=bundle, store=self.b)
        self.assertTrue(out["executed"], out)

        # B 의 물리 키로 물으면 찾힌다.
        rows = self.b.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                          "RETURN c.title AS t, c.owner_id AS o",
                          {"i": pkns.key(B_OWNER, "concept:relativity")})
        self.assertTrue(rows, "받은 노드가 B 의 키로 앉지 않았다")
        self.assertEqual(rows[0]["o"], B_OWNER)
        self.assertEqual(rows[0]["t"], "Relativity")
        # 그리고 A 의 키로는 안 찾힌다(플래그가 켜진 경우에만 의미가 있다).
        if pkns.enabled():
            self.assertEqual(
                self.b.run("MATCH (c:Concept) WHERE c.concept_id = $i RETURN count(c) AS c",
                           {"i": pkns.key(A_OWNER, "concept:relativity")})[0]["c"], 0,
                "보낸 쪽의 물리 키가 받는 쪽에 그대로 앉았다")

        # 사용자 대면 조회는 두 스토어에서 같은 논리 id 를 돌려준다.
        for store, owner in ((self.a, A_OWNER), (self.b, B_OWNER)):
            nb = q.neighbors("concept:relativity", owner=owner, store=store)
            self.assertTrue(nb["executed"], nb)
            self.assertIn("concept:gravity", {n["id"] for n in nb["neighbors"]},
                          f"{owner}: 이웃이 논리 id 로 안 나왔다")

    def test_two_owners_hold_the_same_slug_through_sync(self):
        """H1 그 자체를 동기화 축에서 다시 잰다 — 한 스토어가 두 소유자의 같은
        슬러그를 동시에 들고 있어도 번들이 서로를 덮지 않는다."""
        from api import fragment as frag
        from api import sync
        self._seed_a()
        # B 의 스토어에 B 가 먼저 같은 슬러그를 저작해 둔다.
        frag.merge_fragment({"owner": B_OWNER, "nodes": [
            {"id": "concept:relativity", "type": "concept", "title": "B 가 쓴 것",
             "domain": "phys"}]}, store=self.b)
        bundle = sync.export_delta(owner=A_OWNER, since=0, domains=["phys"],
                                   store=self.a)
        # 받는 쪽 owner 를 A 로 두고 적용한다(허브가 A 의 계정 그래프를 갱신하는 모양).
        out = sync.apply_delta(owner=A_OWNER, bundle=bundle, store=self.b)
        self.assertTrue(out["executed"], out)
        rows = self.b.run("MATCH (c:Concept) WHERE c.title IS NOT NULL "
                          "RETURN c.concept_id AS id, c.owner_id AS o, c.title AS t")
        got = {(r["id"], r["o"]): r["t"] for r in rows}
        if pkns.enabled():
            self.assertEqual(got.get(("concept:relativity", A_OWNER)), "Relativity")
            self.assertEqual(got.get(("concept:relativity", B_OWNER)), "B 가 쓴 것",
                             "A 의 번들이 B 의 같은 슬러그를 덮었다")
        else:
            # 플래그가 꺼져 있으면 한 행뿐이다(그것이 감사 H1 의 상태다).
            self.assertEqual(len([k for k in got if k[0] == "concept:relativity"]), 1)


if __name__ == "__main__":
    unittest.main()
