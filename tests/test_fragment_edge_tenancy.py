# -*- coding: utf-8 -*-
"""엣지 경로의 두 구멍 — **실 엔진**으로 잰다.

기존 `_stamp_edge_endpoints` 테스트 셋(tests/test_atlas.py:395·413·426)은 `_FakeStore`
를 쓴다. 스텁은 owner 예측자를 무시하므로 owner 스코프가 빠져 있어도 초록이다
(SEC-1 에서 이미 배운 교훈 — 스텁 초록은 격리의 증거가 아니다). 그래서 여기는
`test_decision_type._fresh_store()` 와 같은 실 엔진 스토어를 쓴다.

재는 것 둘:

1. **교차테넌트 스탬프**(3b) — A 가 B 의 노드를 끝점으로 엣지를 걸 때, B 의
   rev/sync_seq 가 올라가면 B 의 다음 증분 동기화에 **유령 변경**이 실린다. 더 나쁜 것은
   `_EdgeTombstone` DELETE 로 B 가 지운 엣지의 삭제 표식을 지워 **삭제가 조용히
   취소**되는 것이다.
2. **스킵된 노드로 향하는 엣지**(3a) — 끝점 해석표를 원본 payload 로 지으면, 들어가지도
   않은 노드가 살아있는 끝점으로 등록돼 `edges.created` 가 실제보다 크게 보고된다.
   (이 경우를 재는 테스트가 여태 없었다.)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag        # noqa: E402

A = "user:alice"
B = "user:bob"


def _fresh_store():
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
    keep = {k: os.environ.get(k) for k in keys}
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    return store, keep


def _restore(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


try:
    _s, _k = _fresh_store()
    _s.close()
    _restore(_k)
    _ENGINE = True
except Exception:            # noqa: BLE001 — 드라이버 미설치 환경은 skip
    _ENGINE = False


@unittest.skipUnless(_ENGINE, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EdgeTenancyTests(unittest.TestCase):

    def setUp(self):
        self.store, self._keep = _fresh_store()

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _rev(self, nid: str) -> tuple[int, int]:
        rows = self.store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id "
            "RETURN n.rev AS rev, n.sync_seq AS seq", {"id": nid})
        self.assertTrue(rows, f"{nid} 가 없다")
        return int(rows[0].get("rev") or 0), int(rows[0].get("seq") or 0)

    # ── 1. 교차테넌트 엣지가 남의 rev·툼스톤을 건드리지 않는다 ──────────────
    def test_cross_tenant_edge_leaves_victim_rev_and_tombstone_alone(self):
        # B 의 노드 둘. owner 를 실어(owner_explicit) B 소유로 못 박는다.
        r = frag.merge_fragment({
            "owner": B,
            "nodes": [{"id": "concept:bx", "type": "concept", "title": "BX"},
                      {"id": "concept:by", "type": "concept", "title": "BY"}],
        }, store=self.store)
        self.assertTrue(r["executed"], r)
        before_x, before_y = self._rev("concept:bx"), self._rev("concept:by")

        # B 가 지운 엣지의 삭제 표식을 심는다(실제 삭제 경로가 남기는 그 행).
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = 'RELATED_TO', "
            "t.dst = $d, t.owner_id = $o, t.deleted_at = '2026-01-01T00:00:00Z', "
            "t.sync_seq = 1, t.origin = 'local'",
            {"k": "concept:bx|RELATED_TO|concept:by", "s": "concept:bx",
             "d": "concept:by", "o": B})

        # A 가 같은 (src,rel,dst) 로 엣지를 건다. A 는 이 노드들을 소유하지 않는다.
        r2 = frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:bx", "type": "concept", "title": "BX"},
                      {"id": "concept:by", "type": "concept", "title": "BY"}],
            "edges": [{"from": "concept:bx", "to": "concept:by", "type": "relates_to"}],
        }, store=self.store)
        self.assertTrue(r2["executed"], r2)

        # 노드는 SEC-3 가 이미 막는다(소유권 탈취 차단) — 그것부터 확인.
        self.assertTrue(any("owned by another tenant" in w for w in r2.get("warnings") or []),
                        r2.get("warnings"))

        # 핵심: B 의 rev/sync_seq 가 그대로다.
        self.assertEqual(self._rev("concept:bx"), before_x, "B 의 rev/seq 가 올라갔다")
        self.assertEqual(self._rev("concept:by"), before_y, "B 의 rev/seq 가 올라갔다")

        # 그리고 B 의 삭제 표식이 살아 있다 — 지워지면 삭제가 조용히 취소된다.
        ts = self.store.run(
            "MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
            {"k": "concept:bx|RELATED_TO|concept:by"})
        self.assertEqual(int(ts[0]["c"]), 1, "남의 엣지 툼스톤이 지워졌다(삭제 취소)")

    def test_same_owner_still_stamps_and_cancels_its_own_tombstone(self):
        """스코프를 걸었다고 **자기** 경로가 죽으면 안 된다(3b 의 회귀 방지 축)."""
        frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:ax", "type": "concept", "title": "AX"},
                      {"id": "concept:ay", "type": "concept", "title": "AY"}],
        }, store=self.store)
        before = self._rev("concept:ax")
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = 'RELATED_TO', "
            "t.dst = $d, t.owner_id = $o, t.deleted_at = '2026-01-01T00:00:00Z', "
            "t.sync_seq = 1, t.origin = 'local'",
            {"k": "concept:ax|RELATED_TO|concept:ay", "s": "concept:ax",
             "d": "concept:ay", "o": A})

        frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:ax", "type": "concept", "title": "AX"},
                      {"id": "concept:ay", "type": "concept", "title": "AY"}],
            "edges": [{"from": "concept:ax", "to": "concept:ay", "type": "relates_to"}],
        }, store=self.store)

        self.assertGreater(self._rev("concept:ax")[0], before[0], "자기 노드가 스탬프 안 됐다")
        ts = self.store.run(
            "MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
            {"k": "concept:ax|RELATED_TO|concept:ay"})
        self.assertEqual(int(ts[0]["c"]), 0, "자기 툼스톤이 상쇄되지 않았다")

    # ── 2. 스킵된 노드로 향하는 엣지는 created 로 세지 않는다 ────────────────
    def test_edge_to_skipped_node_is_not_counted_as_created(self):
        # Entity 는 title 이 필수다 — 없으면 `_merge_node` 가 스킵한다. 스킵됐는데도
        # 끝점표에 남으면 아래 엣지가 0행 MATCH 로 아무것도 안 만들고 created 로 세진다.
        r = frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:ok", "type": "concept", "title": "OK"},
                      {"id": "policy:no-title", "type": "policy"}],
            "edges": [{"from": "policy:no-title", "to": "concept:ok", "type": "relates_to"}],
        }, store=self.store, themes=None)
        self.assertTrue(r["executed"], r)
        self.assertEqual(r["merged"]["nodes"]["created"], 1, r)
        self.assertEqual(r["merged"]["edges"]["created"], 0,
                         f"들어가지도 않은 노드로 향하는 엣지를 created 로 셌다: {r}")
        self.assertTrue(r.get("partial"), r)
        ws = r.get("warnings") or []
        self.assertTrue(any("title required" in w for w in ws), ws)
        # 조치 안내가 '선언하라'가 아니라 '그 노드를 고쳐라'여야 한다.
        self.assertTrue(any("declared but did not merge" in w for w in ws), ws)

    # ── 3. `_stamp_edge_endpoints` 의 owner 예측자를 **직접** 잰다 ──────────────
    # ⚠ 위 test_cross_tenant_edge_... 는 3b 를 증명하지 않는다(실측 2026-08-13):
    # 3a 가 끝점 해석표를 '실제 MERGE 된 노드'로 좁히면서, SEC-3 로 스킵된 남의 노드는
    # 애초에 끝점이 되지 못해 엣지가 스탬프까지 **도달하지 않는다**. 그래서 3b 를 껐을
    # 때도 그 테스트는 초록이었다. merge_fragment 를 통과하는 교차테넌트 경로는 현재
    # 없고, 3b 는 그 층이 뚫렸을 때를 막는 두 번째 방어선이다. 방어선의 유무는
    # 함수를 직접 불러야만 잰다.
    def _plant(self, owner: str, a: str, b: str) -> None:
        for nid in (a, b):
            self.store.run(
                "MERGE (n:Concept {concept_id: $id}) SET n.title = $id, "
                "n.owner_id = $o, n.rev = 1, n.sync_seq = 1, n.updated_at = 'x'",
                {"id": nid, "o": owner})
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $a, t.rel = 'RELATED_TO', "
            "t.dst = $b, t.owner_id = $o, t.deleted_at = 'x', t.sync_seq = 1, "
            "t.origin = 'local'", {"k": f"{a}|RELATED_TO|{b}", "a": a, "b": b, "o": owner})

    _ENDS = (("Concept", "concept_id"), ("Concept", "concept_id"))

    def test_stamp_endpoints_scoped_skips_foreign_nodes_and_tombstone(self):
        self._plant(B, "concept:fa", "concept:fb")
        before = self._rev("concept:fa")
        ws: list[str] = []
        frag._stamp_edge_endpoints(self.store, "RELATED_TO", self._ENDS,
                                   "concept:fa", "concept:fb", A, osc=True, warnings=ws)
        self.assertEqual(self._rev("concept:fa"), before, "남의 rev 를 올렸다")
        ts = self.store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
                            {"k": "concept:fa|RELATED_TO|concept:fb"})
        self.assertEqual(int(ts[0]["c"]), 1, "남의 툼스톤을 지웠다(삭제 취소)")
        self.assertTrue(any("not yours" in w for w in ws), ws)

    def test_stamp_endpoints_unscoped_still_stamps_everything(self):
        """osc=False(로컬·owner 미실 경로)는 종전 동작 그대로여야 한다 — 이 축이
        깨지면 owner 를 안 싣는 ~15개 호출부가 조용히 스탬프를 잃는다."""
        self._plant(B, "concept:ua", "concept:ub")
        before = self._rev("concept:ua")
        frag._stamp_edge_endpoints(self.store, "RELATED_TO", self._ENDS,
                                   "concept:ua", "concept:ub", A, osc=False)
        self.assertGreater(self._rev("concept:ua")[0], before[0])
        ts = self.store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
                            {"k": "concept:ua|RELATED_TO|concept:ub"})
        self.assertEqual(int(ts[0]["c"]), 0)

    def test_edge_exists_scoped_does_not_see_a_foreign_edge(self):
        """`was` 가 남의 엣지를 보고 True 가 되면 내 엣지가 스탬프를 건너뛰어
        증분 동기화에 영영 안 실린다."""
        self._plant(B, "concept:ea", "concept:eb")
        self.store.run("MATCH (a:Concept), (b:Concept) WHERE a.concept_id = 'concept:ea' "
                       "AND b.concept_id = 'concept:eb' MERGE (a)-[:RELATED_TO]->(b)")
        self.assertTrue(frag._edge_exists(self.store, "RELATED_TO", self._ENDS,
                                          "concept:ea", "concept:eb"))
        self.assertFalse(
            frag._edge_exists(self.store, "RELATED_TO", self._ENDS,
                              "concept:ea", "concept:eb", owner=A, osc=True),
            "owner 스코프인데 남의 엣지를 '이미 있다'로 읽었다")

    def test_skipped_endpoint_warning_is_rolled_up_not_per_edge(self):
        """엣지마다 한 줄이면 봉투 상한(rpc.MAX_RESULT_CHARS)을 넘겨 결과 전체가
        "Nothing was changed" 로 강등된다 — MERGE 는 이미 돌았으므로 거짓말이다."""
        nodes = [{"id": "concept:hub", "type": "concept", "title": "H"},
                 {"id": "policy:bad", "type": "policy"}]
        edges = [{"from": "policy:bad", "to": "concept:hub", "type": "relates_to"}] * 40
        r = frag.merge_fragment({"owner": A, "nodes": nodes, "edges": edges},
                                store=self.store, themes=None)
        rolled = [w for w in (r.get("warnings") or []) if "did not merge" in w]
        self.assertEqual(len(rolled), 1, f"이유당 한 줄이어야 한다: {rolled}")
        self.assertIn("40 edge(s) skipped", rolled[0])


if __name__ == "__main__":
    unittest.main()
