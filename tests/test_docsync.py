# -*- coding: utf-8 -*-
"""api/docsync — 문서 단위 동기화(D45 P3) 실엔진 왕복 테스트.

두 스토어(A·B) 사이 export→import 로 검증:
  - 텍스트 무손실 전파 + **수신측 재청킹·재임베딩**(embedding 은 payload 에 없다).
  - doc 단위 LWW(updated_at HLC 비교) — 새 쪽 채택, 동률·구버전 로컬 유지.
  - 삭제 전파(_DocTombstone) + 재생성이 삭제를 이김.
  - 커서(since) 증분 — 이미 받은 문서는 다시 받지 않는다.
  - SEC-1: 인바운드 owner 는 호출 owner 로 고정.
드라이버 미설치 시 skip(순수 로직은 _newer 만이라 별도 순수 테스트 최소).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore as ds       # noqa: E402
from api import docsync              # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class PureTests(unittest.TestCase):
    def test_newer_hlc_ordering(self):
        self.assertTrue(docsync._newer("2026-07-24T00:00:01Z", "2026-07-24T00:00:00Z"))
        self.assertFalse(docsync._newer("2026", "2026"))     # 동률 = 로컬 유지
        self.assertFalse(docsync._newer("", "2026"))         # 빈 값 = 최과거
        self.assertTrue(docsync._newer("2026", ""))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DocSyncE2E(unittest.TestCase):
    def _open(self, name):
        d = os.path.join(self._tmp, name)
        cfg = self._cfg(d)
        from store.backend import open_store
        st = open_store(cfg)
        st.provision(dim=4)
        return st

    def _cfg(self, data_dir):
        from store.config import load_config
        return load_config({"data_dir": data_dir})

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in
                       ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ.pop("MEMORY_DATA_DIR", None)   # data_dir 는 cfg 로 명시 주입
        self.A = self._open("A")
        self.B = self._open("B")

    def tearDown(self):
        self.A.close()
        self.B.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _chunks(self, store, doc_id):
        r = store.run("MATCH (c:Chunk) WHERE c.doc_id=$d RETURN count(c) AS c", {"d": doc_id})
        return int(r[0]["c"])

    def test_roundtrip_text_only_reembed(self):
        text = "# 정본\n\n" + "동기화 본문입니다. " * 300   # >2000자 → 재청킹 다분할
        emb = lambda ts: [[1.0, 0.0, 0.0, 0.0] for _ in ts]
        out = ds.put_doc(self.A, owner="user:a", text=text, title="정본", embed=emb)
        did = out["doc_id"]

        delta = docsync.export_docs(self.A, owner="user:a", since=0)
        self.assertEqual(len(delta["docs"]), 1)
        self.assertGreater(delta["cursor"], 0)
        # payload 는 텍스트만 — embedding·오프셋 키가 없다(수신측 재생성).
        self.assertNotIn("embedding", delta["docs"][0])
        self.assertNotIn("chunks", delta["docs"][0])

        # B 는 임베딩 없이 받아도(FTS) 무손실 복원돼야 한다.
        res = docsync.import_docs(self.B, owner="user:a", payload=delta)
        self.assertEqual((res["applied"], res["updated"]), (1, 0))
        got = ds.get_doc(self.B, did, owner="user:a")
        self.assertTrue(got["verified"])
        self.assertEqual(got["text"], text)
        self.assertGreater(self._chunks(self.B, did), 1)   # 로컬 재청킹됨

        # 재-export/import 는 커서·해시로 no-op(멱등).
        again = docsync.import_docs(self.B, owner="user:a",
                                    payload=docsync.export_docs(self.A, owner="user:a", since=0))
        self.assertEqual((again["applied"], again["updated"]), (0, 0))
        # 커서 이후로는 델타가 비어야 한다.
        empty = docsync.export_docs(self.A, owner="user:a", since=delta["cursor"])
        self.assertEqual(empty["docs"], [])

    def test_lww_newer_wins(self):
        did = ds.doc_id_for_origin("user:a", "note")
        ds.put_doc(self.A, owner="user:a", text="원본", doc_id=did,
                   updated_at="2026-07-24T00:00:00Z")
        # B 에 먼저 더 오래된 버전 반영
        docsync.import_docs(self.B, owner="user:a",
                            payload=docsync.export_docs(self.A, owner="user:a"))
        # A 를 더 새 시각으로 편집 → 전파 시 B 가 갱신됨
        ds.put_doc(self.A, owner="user:a", text="개정본(더 새것)", doc_id=did,
                   updated_at="2026-07-24T09:00:00Z")
        r1 = docsync.import_docs(self.B, owner="user:a",
                                 payload=docsync.export_docs(self.A, owner="user:a"))
        self.assertEqual(r1["updated"], 1)
        self.assertEqual(ds.get_doc(self.B, did)["text"], "개정본(더 새것)")
        # 반대로 B 가 더 새면(로컬 편집) 구버전 인바운드는 무시된다.
        ds.put_doc(self.B, owner="user:a", text="B 로컬 최신", doc_id=did,
                   updated_at="2026-07-24T12:00:00Z")
        stale = {"docs": [{"doc_id": did, "title": "", "origin": "",
                           "content_hash": ds._sha("낡은 원격"),
                           "updated_at": "2026-07-24T10:00:00Z", "text": "낡은 원격"}],
                 "tombstones": []}
        r2 = docsync.import_docs(self.B, owner="user:a", payload=stale)
        self.assertEqual(r2["skipped"], 1)
        self.assertEqual(ds.get_doc(self.B, did)["text"], "B 로컬 최신")

    def test_delete_propagation_and_recreate_wins(self):
        did = ds.doc_id_for_origin("user:a", "temp")
        ds.put_doc(self.A, owner="user:a", text="지울 문서", doc_id=did,
                   updated_at="2026-07-24T00:00:00Z")
        docsync.import_docs(self.B, owner="user:a",
                            payload=docsync.export_docs(self.A, owner="user:a"))
        self.assertTrue(ds.get_doc(self.B, did)["executed"])
        # A 에서 삭제 → 툼스톤 전파 → B 에서 삭제
        ds.delete_doc(self.A, did, owner="user:a")
        delta = docsync.export_docs(self.A, owner="user:a")
        self.assertEqual(len(delta["tombstones"]), 1)
        r = docsync.import_docs(self.B, owner="user:a", payload=delta)
        self.assertEqual(r["deleted"], 1)
        self.assertIn("error", ds.get_doc(self.B, did))
        # 재생성(삭제보다 새 시각)이 삭제를 이긴다: 삭제 툼스톤 + 더 새 문서 동시 인바운드
        payload = {"docs": [{"doc_id": did, "title": "부활", "origin": "",
                             "content_hash": ds._sha("부활본"),
                             "updated_at": "2026-07-25T00:00:00Z", "text": "부활본"}],
                   "tombstones": [{"doc_id": did, "deleted_at": "2026-07-24T23:00:00Z"}]}
        docsync.import_docs(self.B, owner="user:a", payload=payload)
        self.assertEqual(ds.get_doc(self.B, did)["text"], "부활본")

    def test_run_carries_documents(self):
        """sync.run 이 문서 국면을 실제로 돈다 — **엔진만 있고 배선이 없던** 결함의
        회귀(2026-08-24 실측: 오케스트레이터가 노드·엣지만 다뤄 [지금 동기화]가
        문서를 한 번도 나르지 않았다, 에러도 없이).

        원격 = B 스토어를 감싼 인프로세스 스텁. 노드 국면은 빈 delta 로 통과시키고
        문서 국면만 실전 경로(export/import)를 태운다."""
        from api import sync
        A, B = self.A, self.B
        cfgA = self._cfg(os.path.join(self._tmp, "A"))

        class Stub:
            def pull(self, since):
                return {"executed": True, "nodes": [], "edges": [],
                        "tombstones": [], "edge_tombstones": [], "next_cursor": since}

            def push(self, chunks):
                return {"executed": True}

            def docs_pull(self, since):
                return docsync.export_docs(B, owner="user:a", since=since)

            def docs_push(self, payload):
                return docsync.import_docs(B, owner="user:a", payload=payload)

        local_id = ds.put_doc(A, owner="user:a", text="로컬 문서", title="로컬")["doc_id"]
        hub_id = ds.put_doc(B, owner="user:a", text="허브 문서", title="허브")["doc_id"]
        out = sync.run(A, cfgA, owner="user:a", remote=Stub())
        self.assertTrue(out["docs_pull"]["executed"])
        self.assertTrue(out["docs_push"]["executed"])
        # 양방향: 허브 문서가 로컬에, 로컬 문서가 허브에 무손실로 선다.
        self.assertEqual(out["docs_pull"]["applied"], 1)
        self.assertEqual(ds.get_doc(A, hub_id, owner="user:a")["text"], "허브 문서")
        self.assertEqual(ds.get_doc(B, local_id, owner="user:a")["text"], "로컬 문서")
        # 에코(방금 받은 문서의 재전송)는 해시 동일로 no-op 이어야 한다.
        self.assertEqual(out["docs_push"]["remote"]["applied"], 1)
        # 두 번째 세션: 커서 이후 변경이 없어 보낼 것이 없다(멱등).
        out2 = sync.run(A, cfgA, owner="user:a", remote=Stub())
        self.assertEqual(out2["docs_push"]["sent"], 0)
        self.assertEqual(out2["docs_pull"]["applied"], 0)

    def test_run_without_doc_transport_soft_fails(self):
        """docs_pull/push 가 없는 원격(구 허브·구 스텁)이어도 노드 동기화는 돈다."""
        from api import sync
        cfgA = self._cfg(os.path.join(self._tmp, "A"))

        class OldStub:
            def pull(self, since):
                return {"executed": True, "nodes": [], "edges": [],
                        "tombstones": [], "edge_tombstones": [], "next_cursor": since}

            def push(self, chunks):
                return {"executed": True}

        out = sync.run(self.A, cfgA, owner="user:a", remote=OldStub())
        self.assertTrue(out["executed"])
        self.assertFalse(out["docs_pull"]["executed"])
        self.assertIn("note", out["docs_pull"])

    def test_chunk_docs_respects_body_cap(self):
        """4MB 바디 상한 보호 — 큰 문서 집합이 조각나고, tombstones 는 마지막 조각에."""
        from api import sync
        payload = {"docs": [{"doc_id": f"d{i}", "text": "가" * 400_000}
                            for i in range(6)],
                   "tombstones": [{"doc_id": "gone", "deleted_at": "2026"}]}
        chunks = sync._chunk_docs(payload, max_bytes=1_500_000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(len(c["docs"]) for c in chunks), 6)
        self.assertEqual(chunks[-1]["tombstones"], payload["tombstones"])
        for c in chunks[:-1]:
            self.assertEqual(c["tombstones"], [])

    def test_sec1_owner_fixed_to_caller(self):
        # payload 가 owner 를 위조해도 import 는 호출 owner 로 고정한다.
        payload = {"docs": [{"doc_id": "dX", "title": "t", "origin": "",
                             "content_hash": ds._sha("남의 것"),
                             "updated_at": "2026-07-24T00:00:00Z", "text": "남의 것"}],
                   "tombstones": []}
        docsync.import_docs(self.B, owner="user:legit", payload=payload)
        row = self.B.run("MATCH (d:_DocMeta) WHERE d.doc_id='dX' RETURN d.owner_id AS o")
        self.assertEqual(row[0]["o"], "user:legit")

    def test_an_ownerless_local_document_is_skipped_not_claimed(self):
        # VIN-226 — 더 새 원격본이라도 무소유 로컬 문서를 가져가지 않는다. 건너뜀으로 센다.
        ds.put_doc(self.B, owner="user:x", text="레거시", doc_id="dL", title="t")
        self.B.run("MATCH (d:_DocMeta) WHERE d.doc_id='dL' SET d.owner_id = ''")
        payload = {"docs": [{"doc_id": "dL", "title": "t", "origin": "",
                             "content_hash": ds._sha("가져가기"),
                             "updated_at": "2999-01-01T00:00:00Z", "text": "가져가기"}],
                   "tombstones": []}
        res = docsync.import_docs(self.B, owner="user:legit", payload=payload)
        self.assertEqual((res["updated"], res["skipped"]), (0, 1), res)
        row = self.B.run("MATCH (d:_DocMeta) WHERE d.doc_id='dL' RETURN d.owner_id AS o")
        self.assertEqual(row[0]["o"], "")


if __name__ == "__main__":
    unittest.main()
