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

    def test_sec1_owner_fixed_to_caller(self):
        # payload 가 owner 를 위조해도 import 는 호출 owner 로 고정한다.
        payload = {"docs": [{"doc_id": "dX", "title": "t", "origin": "",
                             "content_hash": ds._sha("남의 것"),
                             "updated_at": "2026-07-24T00:00:00Z", "text": "남의 것"}],
                   "tombstones": []}
        docsync.import_docs(self.B, owner="user:legit", payload=payload)
        row = self.B.run("MATCH (d:_DocMeta) WHERE d.doc_id='dX' RETURN d.owner_id AS o")
        self.assertEqual(row[0]["o"], "user:legit")


if __name__ == "__main__":
    unittest.main()
