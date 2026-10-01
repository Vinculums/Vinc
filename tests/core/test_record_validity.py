# -*- coding: utf-8 -*-
"""W13 — Record 유효구간(valid_from/valid_until): 열 + 투영 + 동기화 왕복.

판정 2([[vinc.plan.0-9-0-open-decisions §2]])의 구현 계약을 고정한다: 유효구간은
**Record 열**로 산다(엣지 구간은 트리거 뒤 별도 패키지). Zep 의 bi-temporal 은
엣지에 사는데 그것은 Zep 의 사실이 추출된 트리플(엣지)이기 때문이고, Vinc 의
주장은 저작된 record·문서에 산다 — record-first 가 충실한 번역이다.

급소 셋, 셋 다 이 파일이 지킨다:

1. **UNION 팔의 모양.** 이 두 열은 Record 에만 있다(one_liner 의 Concept 동형).
   부착 축(Record·Episode·Bridge) UNION 에서 한 팔이라도 자리를 안 맞추면 그
   조회 전체가 Binder 로 죽고, 반대로 자리만 맞추고 Record 팔이 실열을 안 실으면
   무효화가 조용히 안 보인다 — 죽는 쪽보다 나쁜 실패다.
2. **부분 재전송 보존.** 무효화는 `{id, type, valid_until}` 만 실은 재전송으로
   쓴다 — 미기재 필드가 지워지면 무효화 한 번이 구간 시작을 파괴한다
   (title 무가드가 냈던 병과 같은 뿌리, 85c75f2 가 그 처방).
3. **동기화 왕복.** SYNC_FIELDS·delta 플랜·직렬화 셋 중 하나만 빠져도 한 기기의
   무효화가 다른 기기로 안 넘어가고, 그쪽에서 낡은 주장이 유효한 채 남는다
   (Record 자체가 SYNC_FIELDS 에 빠져 있던 실사고와 같은 모양).

스키마 v15 마이그레이션(구 스토어가 새 열 없이 열리는 경로)은 여기 실엔진으로
재연한다 — 구형 Record 테이블을 먼저 만들고 provision 이 ALTER 로 완성하는지.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from api import revstamp  # noqa: E402
from api import sync  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


class RecordValiditySyncContract(unittest.TestCase):
    """스토어 없이 도는 계약 검사 — 세 표가 같은 열을 본다(content_time 전례)."""

    def test_동기화_표_셋이_구간을_나른다(self):
        for f in ("valid_from", "valid_until"):
            self.assertIn(f, sync.SYNC_FIELDS["Record"])
            self.assertIn(f, revstamp.TRACKED["Record"])
            # 열이 없는 라벨에 옮겨 붙이면 _local_snapshot 이 Binder 로 죽는다.
            self.assertNotIn(f, sync.SYNC_FIELDS["Concept"])
            self.assertNotIn(f, revstamp.TRACKED["Concept"])

    def test_delta_플랜과_직렬화가_구간을_싣는다(self):
        plan = sync._records_delta_plan(owner="user:a", domains=["d"], since=0)
        self.assertIn("r.valid_from AS valid_from", plan.cypher)
        self.assertIn("r.valid_until AS valid_until", plan.cypher)
        node = sync._node_from_record({
            "id": "record:x", "title": "T", "domain": "d", "tags": [], "props": "{}",
            "valid_from": "2026-01", "valid_until": "2026-08-29",
            "rev": 2, "updated_at": "u", "created": "c", "sync_seq": 5})
        self.assertEqual(node["valid_from"], "2026-01")
        self.assertEqual(node["valid_until"], "2026-08-29")

    def test_구간은_record_전용_계약이다(self):
        self.assertIn("valid_from", frag._KEPT_FIELDS["Record"])
        self.assertIn("valid_until", frag._KEPT_FIELDS["Record"])
        for label in ("Concept", "Decision", "Episode", "Bridge", "Entity"):
            self.assertNotIn("valid_from", frag._KEPT_FIELDS[label])
        # _CONTRACT_FIELDS 에 있어야 엉뚱한 종류에 실었을 때 경고가 난다.
        self.assertIn("valid_from", frag._CONTRACT_FIELDS)
        self.assertIn("valid_until", frag._CONTRACT_FIELDS)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RecordValidityE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ingest(self, nodes, edges=None, store=None, read_back=False):
        return frag.merge_fragment(
            {"owner": self.OWNER, "nodes": nodes, "edges": edges or []},
            store=store or self.store, read_back=read_back)

    def _row(self, rid, store=None):
        from store import pkns
        rows = (store or self.store).run(
            "MATCH (r:Record) WHERE r.record_id = $id "
            "RETURN r.valid_from AS vf, r.valid_until AS vu, r.title AS title",
            {"id": pkns.key(self.OWNER, rid)})
        self.assertTrue(rows, rid)
        return rows[0]

    # ── 쓰기 경로 ────────────────────────────────────────────────────────
    def test_적재가_구간을_저장하고_되읽기가_보여준다(self):
        res = self._ingest([{"id": "record:v1", "type": "record", "title": "R",
                             "domain": "d", "valid_from": "2026-01",
                             "valid_until": "2026-08"}], read_back=True)
        self.assertTrue(res["executed"], res)
        self.assertNotIn("partial", res, res.get("warnings"))
        row = self._row("record:v1")
        self.assertEqual((row["vf"], row["vu"]), ("2026-01", "2026-08"))
        # 되읽기(stored)에도 실린다 — _KEPT_FIELDS 가 쓰기와 되읽기의 같은 표다.
        stored = {n["id"]: n for n in res.get("stored") or []}
        self.assertIn("record:v1", stored, res)
        self.assertEqual(stored["record:v1"].get("valid_from"), "2026-01")
        self.assertEqual(stored["record:v1"].get("valid_until"), "2026-08")

    def test_부분_재전송이_구간을_보존하고_빈_문자열만_지운다(self):
        self._ingest([{"id": "record:v2", "type": "record", "title": "R",
                       "domain": "d", "valid_from": "2025", "valid_until": "2026"}])
        # 무효화의 실제 모양: 다른 필드는 안 싣는다.
        self._ingest([{"id": "record:v2", "type": "record",
                       "valid_until": "2026-08-29"}])
        row = self._row("record:v2")
        self.assertEqual((row["vf"], row["vu"]), ("2025", "2026-08-29"),
                         "미기재 valid_from 이 지워졌다 — 보존 가드 파손")
        self.assertEqual(row["title"], "R")
        # 명시적 지우기는 빈 문자열이다.
        self._ingest([{"id": "record:v2", "type": "record", "valid_until": ""}])
        self.assertEqual(self._row("record:v2")["vu"], "")

    def test_형식_위반은_저장값을_지키고_경고한다(self):
        self._ingest([{"id": "record:v3", "type": "record", "title": "R",
                       "domain": "d", "valid_until": "2026-08"}])
        res = self._ingest([{"id": "record:v3", "type": "record",
                             "valid_until": "next week"}])
        self.assertTrue(any("valid_until" in w and "YYYY" in w
                            for w in res.get("warnings") or []), res)
        self.assertEqual(self._row("record:v3")["vu"], "2026-08")

    def test_뒤집힌_구간은_저장하되_말한다(self):
        res = self._ingest([{"id": "record:v4", "type": "record", "title": "R",
                             "domain": "d", "valid_from": "2026-08",
                             "valid_until": "2025"}])
        self.assertTrue(any("inverted" in w for w in res.get("warnings") or []), res)
        row = self._row("record:v4")
        self.assertEqual((row["vf"], row["vu"]), ("2026-08", "2025"))
        # 부분 재전송으로 뒤집히는 경우도 잡는다(저장값과의 조합).
        res2 = self._ingest([{"id": "record:v4", "type": "record",
                              "valid_until": "2027"}])
        self.assertFalse(any("inverted" in w for w in res2.get("warnings") or []), res2)

    def test_record_아닌_종류에_실으면_경고하고_버린다(self):
        res = self._ingest([{"id": "concept:c1", "type": "concept", "title": "C",
                             "domain": "d", "valid_until": "2026"}])
        self.assertTrue(any("valid_until not persisted on Concept" in w
                            for w in res.get("warnings") or []), res)

    # ── 읽기 투영 ────────────────────────────────────────────────────────
    def _attach_fixture(self):
        self._ingest(
            [{"id": "concept:anchor", "type": "concept", "title": "A", "domain": "d"},
             {"id": "record:val", "type": "record", "title": "R", "domain": "d",
              "valid_from": "2026-01", "valid_until": "2026-08"},
             {"id": "episode:e1", "type": "episode", "title": "E", "domain": "d",
              "date": "2026-08-29"},
             {"id": "bridge:b1", "type": "bridge", "title": "B"}],
            [{"from": "record:val", "to": "concept:anchor", "type": "relates_to"},
             {"from": "episode:e1", "to": "concept:anchor", "type": "about"},
             {"from": "bridge:b1", "to": "concept:anchor", "type": "connects_via"}])

    def test_graph_record_축이_구간을_싣는다(self):
        self._attach_fixture()
        out = q.graph(owner=self.OWNER, seed="concept:anchor", depth=2, store=self.store)
        self.assertTrue(out["executed"], out)
        by_id = {n["id"]: n for n in out["nodes"]}
        self.assertIn("record:val", by_id, out.get("nodes"))
        self.assertEqual(by_id["record:val"]["valid_from"], "2026-01")
        self.assertEqual(by_id["record:val"]["valid_until"], "2026-08")
        # 열이 없는 종류의 빈 문자열은 결손이 아니라 사실이다(R2 동형).
        self.assertEqual(by_id["concept:anchor"]["valid_from"], "")

    def test_이웃_부착_UNION_세_팔이_다_살고_record_만_구간을_낸다(self):
        self._attach_fixture()
        out = q.neighbors(owner=self.OWNER, node="concept:anchor", store=self.store)
        self.assertTrue(out["executed"] and out["neighbors"], out)
        got = {nb["id"]: nb for nb in out["neighbors"]}
        # 세 팔 전부 — 한 팔이 죽으면 UNION 전체가 죽어 여기 자체가 빈다.
        for nid in ("record:val", "episode:e1", "bridge:b1"):
            self.assertIn(nid, got, out["neighbors"])
        self.assertEqual(got["record:val"]["valid_from"], "2026-01")
        self.assertEqual(got["record:val"]["valid_until"], "2026-08")
        self.assertEqual(got["episode:e1"]["valid_until"], "")

    def test_record_시드_역방향도_죽지_않는다(self):
        self._attach_fixture()
        out = q.neighbors(owner=self.OWNER, node="record:val", store=self.store)
        self.assertTrue(out["executed"], out)
        got = {nb["id"]: nb for nb in out["neighbors"]}
        self.assertIn("concept:anchor", got, out["neighbors"])
        self.assertEqual(got["concept:anchor"]["valid_from"], "")

    def test_node_context_record_카드가_구간을_싣는다(self):
        self._attach_fixture()
        out = q.node_context(owner=self.OWNER, node="concept:anchor", store=self.store)
        self.assertTrue(out["executed"], out)
        items = {i["id"]: i for i in out["links"]["items"]}
        self.assertIn("record:val", items, out["links"])
        self.assertEqual(items["record:val"]["valid_from"], "2026-01")
        self.assertEqual(items["record:val"]["valid_until"], "2026-08")
        # Episode·Bridge 카드도 살아 있고 구간은 빈 사실이다.
        self.assertIn("episode:e1", items)
        self.assertEqual(items["episode:e1"]["valid_until"], "")

    # ── 동기화 왕복 ──────────────────────────────────────────────────────
    def test_sync_왕복에서_구간이_살아남는다(self):
        self._ingest([{"id": "record:s1", "type": "record", "title": "R",
                       "domain": "d", "valid_from": "2026-01",
                       "valid_until": "2026-08"}])
        bundle = sync.export_delta(owner=self.OWNER, since=0, domains=["d"],
                                   store=self.store)
        self.assertTrue(bundle["executed"], bundle)
        wire = {n["id"]: n for n in bundle["nodes"]}
        self.assertIn("record:s1", wire, bundle)
        self.assertEqual(wire["record:s1"]["valid_from"], "2026-01")
        # 수신 스토어에 적용 — 필드 병합·스탬프 보존 경로를 통째로 지난다.
        with tempfile.TemporaryDirectory() as tmp2:
            prev = os.environ.get("MEMORY_DATA_DIR")
            os.environ["MEMORY_DATA_DIR"] = os.path.join(tmp2, "store")
            try:
                from store.config import load_config
                from store.backend import open_store
                other = open_store(load_config())
                other.provision(dim=4)
                try:
                    res = sync.apply_delta(owner=self.OWNER, bundle=bundle, store=other)
                    self.assertTrue(res["executed"], res)
                    row = self._row("record:s1", store=other)
                    self.assertEqual((row["vf"], row["vu"]), ("2026-01", "2026-08"),
                                     "구간이 동기화에서 떨어졌다 — 무효화가 기기를 못 떠난다")
                finally:
                    other.close()
            finally:
                if prev is None:
                    os.environ.pop("MEMORY_DATA_DIR", None)
                else:
                    os.environ["MEMORY_DATA_DIR"] = prev

    def test_구간만_고친_편집도_sync_seq_가_오른다(self):
        self._ingest([{"id": "record:s2", "type": "record", "title": "R",
                       "domain": "d"}])
        before = self._seq("record:s2")
        self._ingest([{"id": "record:s2", "type": "record",
                       "valid_until": "2026-08-29"}])
        after = self._seq("record:s2")
        self.assertGreater(after, before,
                           "무효화가 델타 커서에 안 잡힌다 — 이 편집은 기기를 못 떠난다")
        # 멱등 재전송은 스탬프가 없어야 한다(가짜 충돌 방지 — revstamp 규칙).
        self._ingest([{"id": "record:s2", "type": "record",
                       "valid_until": "2026-08-29"}])
        self.assertEqual(self._seq("record:s2"), after)

    def _seq(self, rid):
        from store import pkns
        rows = self.store.run(
            "MATCH (r:Record) WHERE r.record_id = $id RETURN r.sync_seq AS s",
            {"id": pkns.key(self.OWNER, rid)})
        return int(rows[0]["s"] or 0)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RecordValidityMigrationE2E(unittest.TestCase):
    """v14 스토어(새 열 없는 Record)가 새 코드로 열리는 경로 — 실엔진 재연.

    구형 Record 테이블을 **provision 전에** 손으로 만들면, provision 의 base DDL
    CREATE 는 'already exists' 로 흡수돼 구형이 그대로 남고, migration_statements
    의 v15 ALTER 만 실제로 적용된다 — 실제 업그레이드가 정확히 이 순서다."""
    OWNER = "user:a"

    #: v14 까지의 Record 열(추적열 포함) — 구 스토어의 실제 모양.
    _OLD_RECORD_DDL = (
        "CREATE NODE TABLE Record(record_id STRING, title STRING, owner_id STRING, "
        "tenant_id STRING, domain STRING, tags STRING[], created STRING, "
        "props STRING, content_time STRING, updated_at STRING, rev INT64, "
        "sync_seq INT64, PRIMARY KEY(record_id))")

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_구_스토어가_열을_얻고_플랜이_바인드된다(self):
        self.store.run(self._OLD_RECORD_DDL)
        self.store.provision(dim=4)   # v15 ALTER 가 여기서 두 열을 완성한다
        res = frag.merge_fragment(
            {"owner": self.OWNER,
             "nodes": [{"id": "concept:a", "type": "concept", "title": "A",
                        "domain": "d"},
                       {"id": "record:m1", "type": "record", "title": "R",
                        "domain": "d", "valid_until": "2026-08"}],
             "edges": [{"from": "record:m1", "to": "concept:a",
                        "type": "relates_to"}]},
            store=self.store)
        self.assertTrue(res["executed"], res)
        self.assertNotIn("partial", res, res.get("warnings"))
        # 넓어진 투영 셋이 마이그레이션된 스키마에서 실제로 돈다(Binder 검사).
        out = q.neighbors(owner=self.OWNER, node="concept:a", store=self.store)
        self.assertTrue(out["executed"], out)
        got = {nb["id"]: nb for nb in out["neighbors"]}
        self.assertEqual(got["record:m1"]["valid_until"], "2026-08")
        g = q.graph(owner=self.OWNER, seed="concept:a", depth=2, store=self.store)
        self.assertTrue(g["executed"], g)
        ctx = q.node_context(owner=self.OWNER, node="concept:a", store=self.store)
        self.assertTrue(ctx["executed"], ctx)


_NS_DOC = """같은 검사를 PK 네임스페이싱 **켠 채** 다시 돈다.

읽기 경로는 id 를 물리 키로 바인딩하고 돌아온 값을 벗긴다. 한 모드에서만 초록인
투영은 층을 헷갈린 것이고, 기본 스위트는 플래그가 꺼져 있어 그것을 못 잡는다."""


def _namespaced(base):
    class _Ns(base):
        def setUp(self):
            self._pk_prev = os.environ.get("VINC_PK_NAMESPACED")
            os.environ["VINC_PK_NAMESPACED"] = "on"
            super().setUp()

        def tearDown(self):
            try:
                super().tearDown()
            finally:
                if self._pk_prev is None:
                    os.environ.pop("VINC_PK_NAMESPACED", None)
                else:
                    os.environ["VINC_PK_NAMESPACED"] = self._pk_prev

    _Ns.__name__ = _Ns.__qualname__ = base.__name__ + "UnderNamespacing"
    _Ns.__doc__ = _NS_DOC
    return _Ns


RecordValidityUnderNamespacing = _namespaced(RecordValidityE2E)
RecordValidityMigrationUnderNamespacing = _namespaced(RecordValidityMigrationE2E)


if __name__ == "__main__":
    unittest.main()
