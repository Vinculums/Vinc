# -*- coding: utf-8 -*-
"""엣지 경로의 두 구멍 — **실 엔진**으로 잰다.

기존 `_stamp_edge_endpoints` 테스트 셋(tests/core/test_view.py:395·413·426)은 `_FakeStore`
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag        # noqa: E402
from store import pkns                  # noqa: E402

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

    def _rev(self, nid: str, owner: str) -> tuple[int, int]:
        # 스토어 직접 조회다 — PK 네임스페이싱이 켜지면 노드 PK 는 물리 키이므로
        # 소유자를 받아 합성한다(논리 슬러그로 물으면 0행이 돌아온다).
        rows = self.store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id "
            "RETURN n.rev AS rev, n.sync_seq AS seq", {"id": pkns.key(owner, nid)})
        self.assertTrue(rows, f"{nid} 가 없다")
        return int(rows[0].get("rev") or 0), int(rows[0].get("seq") or 0)

    # ── 0. 보관할 열이 없는 근거는 조용히 사라지지 않는다 ──────────────────
    #
    # GRAPH.md 는 record 엣지와 about 에 evidence 를 **요구**하는데 vinc_ingest 는
    # 한동안 concept↔concept 과 about 에서만 받았다. 계약이 서로 다르고, 다르다는
    # 사실이 저작자에게 닿지 않았다: 필드는 사라지는데 warnings 는 비어 있었다
    # (2026-08-30 실측). #117 이 경고를 달았고, v16 이 ATTACHES_TO 에 열을 줘서
    # record 엣지는 경고 대상에서 **저장 대상**으로 옮겨 갔다. 무열 rel 로 남은
    # CONNECTS·TYPED 가 경고 경로의 증인이다.
    # 근거: [[record:attaches-to-carries-evidence-since-v16]]

    def _ingest_edges(self, edges):
        return frag.merge_fragment({"owner": A, "nodes": [
            {"id": "concept:t", "type": "concept", "title": "T"},
            {"id": "record:t", "type": "record", "title": "R"},
            {"id": "episode:t", "type": "episode", "title": "E", "date": "2026-08-30"},
            {"id": "bridge:t", "type": "bridge", "title": "B"},
        ], "edges": edges}, store=self.store)

    def test_record_edge_evidence_is_stored_since_v16(self):
        """record 엣지의 근거가 경고 없이 ATTACHES_TO 열에 실제로 앉는다 —
        GRAPH.md 가 요구하던 그 필드다. type 보존과 공존해야 한다(같은 SET 절)."""
        r = self._ingest_edges([{"from": "record:t", "to": "concept:t",
                                 "type": "relates_to",
                                 "evidence": "이 문장은 이제 보관된다.",
                                 "confidence": 0.7}])
        self.assertEqual(r["merged"]["edges"]["created"], 1, r)
        self.assertEqual([w for w in r["warnings"] if "not stored" in w], [])
        self.assertFalse(r.get("partial"), r)
        rows = self.store.run(
            "MATCH (:Record)-[x:ATTACHES_TO]->(:Concept) "
            "RETURN x.type AS t, x.evidence AS ev, x.confidence AS cf")
        self.assertEqual(rows[0]["t"], "relates_to")
        self.assertEqual(rows[0]["ev"], "이 문장은 이제 보관된다.")
        self.assertAlmostEqual(float(rows[0]["cf"]), 0.7)

    def test_confidence_is_named_too_and_bridge_edges_are_covered(self):
        r = self._ingest_edges([{"from": "bridge:t", "to": "concept:t",
                                 "type": "connects_via",
                                 "evidence": "사라진다", "confidence": 0.5}])
        hits = [w for w in r["warnings"] if "not stored on CONNECTS" in w]
        self.assertEqual(len(hits), 1, r["warnings"])
        self.assertIn("evidence and confidence", hits[0])

    def test_a_relation_that_has_the_column_stays_silent_and_stores(self):
        """경고가 뜨지 말아야 할 곳에서 안 뜨는지가 절반이다 — 전부에 대고 경고하면
        경고는 읽히지 않는다."""
        r = self._ingest_edges([{"from": "episode:t", "to": "concept:t",
                                 "type": "about", "evidence": "이건 보관된다.",
                                 "confidence": 0.9}])
        self.assertEqual([w for w in r["warnings"] if "not stored" in w], [])
        rows = self.store.run(
            "MATCH (e:Episode)-[x:ABOUT]->(c:Concept) RETURN x.evidence AS ev")
        self.assertEqual(rows[0]["ev"], "이건 보관된다.")

    def test_no_warning_when_the_author_sent_nothing(self):
        r = self._ingest_edges([{"from": "record:t", "to": "concept:t",
                                 "type": "relates_to"}])
        self.assertEqual([w for w in r["warnings"] if "not stored" in w], [])

    # ── 1. 교차테넌트 엣지가 남의 rev·툼스톤을 건드리지 않는다 ──────────────
    def test_cross_tenant_edge_leaves_victim_rev_and_tombstone_alone(self):
        # B 의 노드 둘. owner 를 실어(owner_explicit) B 소유로 못 박는다.
        r = frag.merge_fragment({
            "owner": B,
            "nodes": [{"id": "concept:bx", "type": "concept", "title": "BX"},
                      {"id": "concept:by", "type": "concept", "title": "BY"}],
        }, store=self.store)
        self.assertTrue(r["executed"], r)
        before_x = self._rev("concept:bx", B)
        before_y = self._rev("concept:by", B)
        # 툼스톤 ekey 는 논리 `src|REL|dst` 를 한 번 합성한 것이다(pkns.ekey).
        bkey = pkns.ekey(B, "concept:bx", "RELATED_TO", "concept:by")

        # B 가 지운 엣지의 삭제 표식을 심는다(실제 삭제 경로가 남기는 그 행).
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = 'RELATED_TO', "
            "t.dst = $d, t.owner_id = $o, t.deleted_at = '2026-01-01T00:00:00Z', "
            "t.sync_seq = 1, t.origin = 'local'",
            {"k": bkey, "s": "concept:bx", "d": "concept:by", "o": B})

        # A 가 같은 (src,rel,dst) 로 엣지를 건다. A 는 이 노드들을 소유하지 않는다.
        r2 = frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:bx", "type": "concept", "title": "BX"},
                      {"id": "concept:by", "type": "concept", "title": "BY"}],
            "edges": [{"from": "concept:bx", "to": "concept:by", "type": "relates_to"}],
        }, store=self.store)
        self.assertTrue(r2["executed"], r2)

        # 노드는 SEC-3 가 이미 막는다(소유권 탈취 차단) — 그것부터 확인.
        # ⚠ PK 네임스페이싱이 켜지면 이 경고가 **사라지는 것이 맞다**. A 의 쓰기는
        # `Aconcept:bx` 라는 자기 행으로 가므로 B 의 행과 부딪칠 일이 없다 —
        # 그 충돌을 없애는 것이 design A 의 목적(감사 H1)이다. 아래 두 축(B 의
        # rev 불변 · B 의 툼스톤 존치)은 두 모드에서 똑같이 성립해야 한다.
        if not pkns.enabled():
            self.assertTrue(
                any("owned by another tenant" in w for w in r2.get("warnings") or []),
                r2.get("warnings"))

        # 핵심: B 의 rev/sync_seq 가 그대로다.
        self.assertEqual(self._rev("concept:bx", B), before_x, "B 의 rev/seq 가 올라갔다")
        self.assertEqual(self._rev("concept:by", B), before_y, "B 의 rev/seq 가 올라갔다")

        # 그리고 B 의 삭제 표식이 살아 있다 — 지워지면 삭제가 조용히 취소된다.
        ts = self.store.run(
            "MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
            {"k": bkey})
        self.assertEqual(int(ts[0]["c"]), 1, "남의 엣지 툼스톤이 지워졌다(삭제 취소)")

    def test_same_owner_still_stamps_and_cancels_its_own_tombstone(self):
        """스코프를 걸었다고 **자기** 경로가 죽으면 안 된다(3b 의 회귀 방지 축)."""
        frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:ax", "type": "concept", "title": "AX"},
                      {"id": "concept:ay", "type": "concept", "title": "AY"}],
        }, store=self.store)
        before = self._rev("concept:ax", A)
        akey = pkns.ekey(A, "concept:ax", "RELATED_TO", "concept:ay")
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = 'RELATED_TO', "
            "t.dst = $d, t.owner_id = $o, t.deleted_at = '2026-01-01T00:00:00Z', "
            "t.sync_seq = 1, t.origin = 'local'",
            {"k": akey, "s": "concept:ax", "d": "concept:ay", "o": A})

        frag.merge_fragment({
            "owner": A,
            "nodes": [{"id": "concept:ax", "type": "concept", "title": "AX"},
                      {"id": "concept:ay", "type": "concept", "title": "AY"}],
            "edges": [{"from": "concept:ax", "to": "concept:ay", "type": "relates_to"}],
        }, store=self.store)

        self.assertGreater(self._rev("concept:ax", A)[0], before[0],
                           "자기 노드가 스탬프 안 됐다")
        ts = self.store.run(
            "MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
            {"k": akey})
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
    def _plant(self, owner: str, a: str, b: str) -> tuple[str, str]:
        """돌려주는 것은 **물리 키** 쌍이다. `_stamp_edge_endpoints` 는 이미 합성된
        끝점을 받으므로(합성은 호출부의 몫), 그 함수를 직접 부르는 테스트는 여기서
        받은 값을 그대로 넘겨야 한다. 툼스톤 `src`/`dst` **열**은 동기화 와이어로
        나가는 값이라 논리로 남고, `ekey` 만 한 번 합성된다."""
        ka, kb = pkns.key(owner, a), pkns.key(owner, b)
        for nid, title in ((ka, a), (kb, b)):
            self.store.run(
                "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, "
                "n.owner_id = $o, n.rev = 1, n.sync_seq = 1, n.updated_at = 'x'",
                {"id": nid, "t": title, "o": owner})
        self.store.run(
            "MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $a, t.rel = 'RELATED_TO', "
            "t.dst = $b, t.owner_id = $o, t.deleted_at = 'x', t.sync_seq = 1, "
            "t.origin = 'local'",
            {"k": pkns.ekey(owner, a, "RELATED_TO", b), "a": a, "b": b, "o": owner})
        return ka, kb

    _ENDS = (("Concept", "concept_id"), ("Concept", "concept_id"))

    def test_stamp_endpoints_scoped_skips_foreign_nodes_and_tombstone(self):
        self._plant(B, "concept:fa", "concept:fb")
        before = self._rev("concept:fa", B)
        ws: list[str] = []
        # A 의 호출부가 합성한 끝점을 넘긴다(프로덕션에서 `_merge_edge` 가 하는 것과
        # 같다 — 조각의 owner 로 합성한다). 플래그가 꺼져 있으면 논리 그대로다.
        frag._stamp_edge_endpoints(self.store, "RELATED_TO", self._ENDS,
                                   pkns.key(A, "concept:fa"), pkns.key(A, "concept:fb"),
                                   A, osc=True, warnings=ws)
        self.assertEqual(self._rev("concept:fa", B), before, "남의 rev 를 올렸다")
        ts = self.store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
                            {"k": pkns.ekey(B, "concept:fa", "RELATED_TO", "concept:fb")})
        self.assertEqual(int(ts[0]["c"]), 1, "남의 툼스톤을 지웠다(삭제 취소)")
        self.assertTrue(any("not yours" in w for w in ws), ws)

    def test_stamp_endpoints_unscoped_still_stamps_everything(self):
        """osc=False(로컬·owner 미실 경로)는 종전 동작 그대로여야 한다 — 이 축이
        깨지면 owner 를 안 싣는 ~15개 호출부가 조용히 스탬프를 잃는다."""
        ka, kb = self._plant(B, "concept:ua", "concept:ub")
        before = self._rev("concept:ua", B)
        # B 가 합성한 끝점을 A 의 owner 로 넘긴다 — osc=False 가 owner 술어를 **안**
        # 거는지가 이 테스트의 축이고, 그것은 끝점 스탬프에서 재진다.
        frag._stamp_edge_endpoints(self.store, "RELATED_TO", self._ENDS,
                                   ka, kb, A, osc=False)
        self.assertGreater(self._rev("concept:ua", B)[0], before[0])
        # 툼스톤은 다르다. `ekey` 는 **부른 쪽의 owner** 로 합성되므로, 플래그가 켜지면
        # A 의 키가 B 의 표식을 못 짚어 상쇄가 일어나지 않는다. 그것이 의도다 — owner
        # 를 안 싣는 경로는 플래그가 영구히 꺼진 스토어(로컬·데스크탑)에만 있다.
        ts = self.store.run("MATCH (t:_EdgeTombstone) WHERE t.ekey = $k RETURN count(t) AS c",
                            {"k": pkns.ekey(B, "concept:ua", "RELATED_TO", "concept:ub")})
        self.assertEqual(int(ts[0]["c"]), 1 if pkns.enabled() else 0)

    def test_edge_exists_scoped_does_not_see_a_foreign_edge(self):
        """`was` 가 남의 엣지를 보고 True 가 되면 내 엣지가 스탬프를 건너뛰어
        증분 동기화에 영영 안 실린다."""
        ka, kb = self._plant(B, "concept:ea", "concept:eb")
        self.store.run("MATCH (a:Concept), (b:Concept) WHERE a.concept_id = $a "
                       "AND b.concept_id = $b MERGE (a)-[:RELATED_TO]->(b)",
                       {"a": ka, "b": kb})
        # 무스코프 조회는 B 가 합성한 끝점으로 물어야 그 엣지를 본다.
        self.assertTrue(frag._edge_exists(self.store, "RELATED_TO", self._ENDS, ka, kb))
        # A 로 스코프를 걸면, 플래그가 켜졌을 때는 A 가 합성한 끝점으로 묻게 되므로
        # (프로덕션의 `_merge_edge` 와 같은 모양) 남의 엣지가 보이지 않는다.
        self.assertFalse(
            frag._edge_exists(self.store, "RELATED_TO", self._ENDS,
                              pkns.key(A, "concept:ea"), pkns.key(A, "concept:eb"),
                              owner=A, osc=True),
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
