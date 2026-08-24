# -*- coding: utf-8 -*-
"""시간축 이원화(D72 · 스키마 v7) 계약 테스트.

  1. **순수 층**: `_norm_content_time` 형식 계약(사전식 비교가 성립하는 형태만).
  2. **실엔진 층**(ladybug 미설치 시 skip):
     - `_DocMeta.created_at` 최초 1회 기록 · 편집 보존
     - `content_time` 저장·보존·삭제(키 부재 vs 빈 문자열)
     - 추출이 내용 시점을 개념에 비파괴 전파
     - `/graph` basis 스왑 + 에코, `docs_in_range` basis 분기
     - **6개 그래프 플랜 전부 실행**(Binder 함정 회귀 게이트 — v7 열을 붙인 뒤에도
       bridge·entity 축이 살아 있는지 못박는다)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds  # noqa: E402
from api import query as q  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class ContentTimeFormatTests(unittest.TestCase):
    def test_accepts_year_month_day(self):
        for ok in ("1905", "1905-06", "1905-06-28", "0001", "2026-12-31"):
            self.assertEqual(ds._norm_content_time(ok), ok, ok)

    def test_rejects_anything_that_breaks_lexicographic_order(self):
        # 자리를 안 채운 값(1905-6)은 "1905-10" 보다 뒤로 정렬돼 축을 조용히 뒤집는다.
        for bad in ("1905-6", "1905/06", "05-06-1905", "기원전 300", "1905-06-28T00:00",
                    "", "   ", None, "abc"):
            self.assertEqual(ds._norm_content_time(bad), "", repr(bad))

    def test_trims_surrounding_space(self):
        self.assertEqual(ds._norm_content_time("  1905-06  "), "1905-06")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class TimeAxisE2E(unittest.TestCase):
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

    def _meta(self, did):
        return ds._meta(self.store, did)

    def test_created_at_set_once_and_preserved_on_edit(self):
        out = ds.put_doc(self.store, owner=self.OWNER, text="처음.", title="t")
        did = out["doc_id"]
        born = self._meta(did)["created_at"]
        self.assertTrue(born, "신규 문서는 최초 시각을 받아야 한다")
        ds.put_doc(self.store, owner=self.OWNER, text="고쳤다.", doc_id=did, title="t")
        m = self._meta(did)
        self.assertEqual(m["created_at"], born)          # 편집이 보존
        self.assertNotEqual(m["updated_at"], born)       # 수정 시각은 따로 흐른다

    def test_legacy_row_without_created_at_is_not_backfilled(self):
        # 마이그레이션 백필을 하지 않는다는 계약 — 없는 시간을 지어내면 타임라인이
        # "마이그레이션한 날"에 몰린다. 구 행을 흉내 내 '' 로 만든 뒤 편집해 본다.
        out = ds.put_doc(self.store, owner=self.OWNER, text="구 문서.", title="old")
        did = out["doc_id"]
        self.store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $i SET d.created_at = ''",
                       {"i": did})
        ds.put_doc(self.store, owner=self.OWNER, text="구 문서 수정.", doc_id=did, title="old")
        self.assertEqual(self._meta(did)["created_at"], "")

    def test_content_time_validated_preserved_and_clearable(self):
        out = ds.put_doc(self.store, owner=self.OWNER, text="상대성.", title="rel",
                         content_time="1905-06")
        did = out["doc_id"]
        self.assertEqual(self._meta(did)["content_time"], "1905-06")
        # 키를 안 보내면 보존(제목만 고쳐도 시점이 사라지지 않는다)
        ds.put_doc(self.store, owner=self.OWNER, text="상대성 2.", doc_id=did, title="rel")
        self.assertEqual(self._meta(did)["content_time"], "1905-06")
        # 빈 문자열은 "지웠다" — 보존과 구분된다
        ds.put_doc(self.store, owner=self.OWNER, text="상대성 3.", doc_id=did, title="rel",
                   content_time="")
        self.assertEqual(self._meta(did)["content_time"], "")
        # 불합격 형식도 빈 값으로 내려앉는다(저장은 성공, 축에서만 빠진다)
        r = ds.put_doc(self.store, owner=self.OWNER, text="상대성 4.", doc_id=did,
                       title="rel", content_time="1905/06")
        self.assertTrue(r["executed"])
        self.assertEqual(self._meta(did)["content_time"], "")

    def test_extract_propagates_content_time_without_overwriting(self):
        text = "---\ndomain: physics\n---\n# 문서\n\n## 특수상대성\n\n광속은 불변이다.\n"
        out = ds.put_doc(self.store, owner=self.OWNER, text=text, title="물리",
                         content_time="1905")
        ap = ds.extract_doc(self.store, out["doc_id"], owner=self.OWNER, apply=True)
        self.assertEqual(ap["applied"]["created"], 1)
        # 리터럴이던 id 를 파라미터로 뺀다 — 물리 키를 바인딩해야 하므로.
        cid = pkns.key(self.OWNER, "concept:특수상대성")
        row = self.store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                             "RETURN c.content_time AS ct", {"i": cid})
        self.assertEqual(row[0]["ct"], "1905")
        # 사용자가 손질한 값은 재추출이 덮지 않는다
        self.store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                       "SET c.content_time = '1916'", {"i": cid})
        ds.put_doc(self.store, owner=self.OWNER, text=text + "\n덧.", doc_id=out["doc_id"],
                   title="물리", content_time="1905")
        ds.extract_doc(self.store, out["doc_id"], owner=self.OWNER, apply=True)
        row = self.store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                             "RETURN c.content_time AS ct", {"i": cid})
        self.assertEqual(row[0]["ct"], "1916")

    # ── 정상 쓰기 표면(vinc_ingest)에서의 content_time ────────────────────────
    # 2026-08-17 까지 `_merge_node` 는 이 필드를 **아예 안 읽었다**. 열은 Concept·
    # Record 에 있고 문서 추출만 채웠으니, 같은 축이 들어온 문에 따라 갈렸다.
    def _ingest(self, node):
        from api import fragment as frag
        return frag.merge_fragment({"owner": self.OWNER, "nodes": [node]}, store=self.store)

    def _ct(self, label, pk, nid):
        # 쓰기는 API(`merge_fragment`)로 갔고 읽기는 스토어 직접이다. PK
        # 네임스페이싱이 켜지면 쓰기가 합성하므로 여기도 물리 키로 물어야 한다.
        rows = self.store.run(f"MATCH (n:{label}) WHERE n.{pk} = $i "
                              "RETURN n.content_time AS ct, n.sync_seq AS seq",
                              {"i": pkns.key(self.OWNER, nid)})
        return rows[0] if rows else {}

    def test_fragment_writes_content_time_on_concept_and_record(self):
        self._ingest({"id": "concept:c1", "type": "concept", "title": "C",
                      "content_time": "1905-06"})
        self._ingest({"id": "record:r1", "type": "record", "title": "R",
                      "content_time": "1916"})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c1")["ct"], "1905-06")
        self.assertEqual(self._ct("Record", "record_id", "record:r1")["ct"], "1916")

    def test_fragment_preserves_content_time_on_partial_resend(self):
        # 부분 재전송(제목만)이 시점을 깎으면 안 된다 — domain·tags·props 와 같은 가드.
        self._ingest({"id": "concept:c2", "type": "concept", "title": "C",
                      "content_time": "1905"})
        self._ingest({"id": "concept:c2", "type": "concept", "title": "C 고침"})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c2")["ct"], "1905")
        # 빈 문자열은 **명시적 지우기** — 미기재와 구분된다
        self._ingest({"id": "concept:c2", "type": "concept", "content_time": ""})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c2")["ct"], "")

    def test_fragment_refuses_a_malformed_content_time_and_says_so(self):
        # ⚠ 문서 경로(put_doc)는 위반값을 ''로 내려앉히지만 여기는 **보존 + 경고**다.
        # 부분 재전송이 정상인 표면이라 오타는 '지우기'보다 '미기재'에 가깝다.
        self._ingest({"id": "concept:c3", "type": "concept", "title": "C",
                      "content_time": "1905"})
        res = self._ingest({"id": "concept:c3", "type": "concept",
                            "content_time": "yesterday"})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c3")["ct"], "1905")
        self.assertTrue(any("content_time" in w for w in res.get("warnings") or []),
                        f"조용한 유실 금지 — 경고가 있어야 한다: {res.get('warnings')}")

    def test_fragment_promotes_content_time_from_props(self):
        # F9 와 같은 규칙 — 계약에 없던 시절 상류가 props 에 넣어 둔 값을 제자리로.
        # 키가 **아예 없을 때만** 본다(top-level 이 오면 그것이 이긴다).
        self._ingest({"id": "concept:c4", "type": "concept", "title": "C",
                      "props": {"content_time": "1687"}})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c4")["ct"], "1687")
        self._ingest({"id": "concept:c5", "type": "concept", "title": "C",
                      "content_time": "1905", "props": {"content_time": "1687"}})
        self.assertEqual(self._ct("Concept", "concept_id", "concept:c5")["ct"], "1905")

    def test_editing_only_content_time_bumps_the_sync_cursor(self):
        # TRACKED 에 없으면 이 편집은 sync_seq 를 못 올리고, 그러면 **이 기기를 못
        # 떠난다**(다음 delta 가 이 노드를 안 본다). 그것이 이 필드를 추적하는 이유다.
        self._ingest({"id": "concept:c6", "type": "concept", "title": "C",
                      "content_time": "1905"})
        before = int(self._ct("Concept", "concept_id", "concept:c6")["seq"])
        self._ingest({"id": "concept:c6", "type": "concept", "content_time": "1916"})
        after = int(self._ct("Concept", "concept_id", "concept:c6")["seq"])
        self.assertGreater(after, before)
        # 멱등 재전송은 반대로 **안** 올린다(가짜 충돌 방지 — 기존 계약).
        self._ingest({"id": "concept:c6", "type": "concept", "content_time": "1916"})
        self.assertEqual(int(self._ct("Concept", "concept_id", "concept:c6")["seq"]), after)

    def _concept(self, cid, *, domain="phys", created="2026-01-01", content=""):
        self.store.run(
            "MERGE (c:Concept {concept_id:$id}) SET c.title=$t, c.domain=$d, "
            "c.owner_id=$o, c.created=$cr, c.content_time=$ct",
            {"id": pkns.key(self.OWNER, cid), "t": cid.split(":")[-1], "d": domain,
             "o": self.OWNER, "cr": created, "ct": content})

    def test_graph_basis_swaps_ts_and_echoes(self):
        self._concept("concept:relativity", created="2026-01-01", content="1905")
        self._concept("concept:gravity", created="2026-02-01", content="")
        rec = q.graph(owner=self.OWNER, store=self.store)
        self.assertEqual(rec["basis"], "recorded")
        by = {n["id"]: n for n in rec["nodes"]}
        self.assertEqual(by["concept:relativity"]["ts"], "2026-01-01")
        self.assertEqual(by["concept:relativity"]["content_ts"], "1905")
        con = q.graph(owner=self.OWNER, basis="content", store=self.store)
        self.assertEqual(con["basis"], "content")
        by2 = {n["id"]: n for n in con["nodes"]}
        self.assertEqual(by2["concept:relativity"]["ts"], "1905")
        # 내용 시점이 없는 노드는 빈 값으로 남는다 — 0 이나 오늘로 채우지 않는다
        self.assertEqual(by2["concept:gravity"]["ts"], "")
        # 미지 basis 는 조용히 recorded 로 강등하고 그 사실을 에코한다
        self.assertEqual(q.graph(owner=self.OWNER, basis="bogus",
                                 store=self.store)["basis"], "recorded")

    def test_docs_in_range_follows_the_same_basis(self):
        self._concept("concept:relativity", created="2026-01-01", content="1905-06")
        # 기록 축: 2026 구간에 잡히고 1905 구간에는 없다
        rec = q.docs_in_range(owner=self.OWNER, start="2026-01", end="2026-02",
                              store=self.store)
        self.assertEqual(rec["basis"], "authored-or-updated")
        self.assertIn("concept:relativity", {i["id"] for i in rec["items"]})
        # 내용 축: 정반대다. 같은 노드가 1905 구간에 잡힌다
        con = q.docs_in_range(owner=self.OWNER, start="1905", end="1906",
                              basis="content", store=self.store)
        self.assertEqual(con["basis"], "content")
        self.assertIn("concept:relativity", {i["id"] for i in con["items"]})
        empty = q.docs_in_range(owner=self.OWNER, start="2026-01", end="2026-02",
                                basis="content", store=self.store)
        self.assertEqual(empty["items"], [])

    def test_all_graph_plans_still_execute(self):
        """⚠ Binder 함정 회귀 게이트(정본 vinc.d.graph-views §4).

        v7 열을 붙인 뒤 **여섯 플랜이 전부** 실행돼야 한다. 열이 없는 라벨의 플랜에
        content_time 을 붙이면 새 뷰가 아니라 **모든 레벨의 /graph 가 죽는다.**"""
        self._concept("concept:relativity")
        plans = [
            q.graph_plan(owner=self.OWNER),
            q.graph_rows_for_ids_plan(["concept:relativity"], owner=self.OWNER),
            q.episode_rows_plan(owner=self.OWNER),
            q.record_rows_plan(owner=self.OWNER),
            q.bridge_rows_plan(owner=self.OWNER),
            q.entity_rows_for_ids_plan(["entity:x"], owner=self.OWNER),
        ]
        for p in plans:
            res = q.execute(p, None, store=self.store)
            self.assertTrue(res.get("executed"), f"{p.kind}: {res.get('note')}")


if __name__ == "__main__":
    unittest.main()
