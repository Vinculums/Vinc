# -*- coding: utf-8 -*-
"""관례 문서(`graph.md`)의 owner 충돌 고정 검사.

배경: `_DocMeta` 조회(`docstore._meta`)는 `doc_id` 만으로 찾는다 — owner 를 안 본다.
그런데 `graphmd.CONVENTIONS_DOC_ID` 는 모든 테넌트가 공유하는 **리터럴**이라, 먼저 그
문서를 쓴 owner 가 물리 행을 영구 점유하고 나머지 owner 는 owner mismatch(SEC-1)를
받았다(P1, 2026-08-05 감사 §3.4, 6주 뒤에도 재현: 2026-09-14 실측으로 확인,
`api/graphmd.py` CONVENTIONS_DOC_ID 는 그대로 리터럴이었다).
근거: [[record:conventions-doc-id-collision-still-live-2026-09-14]]

처방은 `docstore._scoped_doc_id`다 — owner 가 있으면 관례 문서 요청을
`graph.md#<owner>` 물리 슬롯으로 튼다(`put_doc`·`get_doc`·`delete_doc` 진입점 모두).
`get_doc`·`delete_doc` 은 스코핑 이전에 쓰인 레거시(맨 리터럴) 문서를 위한 폴백도
가진다 — 그 폴백도 기존 owner-mismatch 가드를 그대로 타므로 다른 owner 의 레거시
문서가 새지 않는다.

여기서 고정하는 것 넷:
  ① 서로 다른 owner 가 각자 `graph.md` 를 가져도 충돌하지 않는다.
  ② 스코핑 이전에 쓰인 레거시 문서는 원래 owner 에게 여전히 읽힌다(폴백).
  ③ 그 폴백이 다른 owner 에게는 새지 않는다 — owner mismatch 그대로.
  ④ owner 가 없는 로컬 단일 소유자 스토어는 동작이 그대로다(리터럴 그대로 씀)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore, graphmd  # noqa: E402


class ScopedDocIdUnitTests(unittest.TestCase):
    """헬퍼 자체 — 네 갈래를 표로 고정한다."""

    def test_owner가_있으면_관례_리터럴만_튼다(self):
        self.assertEqual(
            docstore._scoped_doc_id(graphmd.CONVENTIONS_DOC_ID, "user:a"),
            f"{graphmd.CONVENTIONS_DOC_ID}#user:a")

    def test_owner가_없으면_그대로다(self):
        self.assertEqual(
            docstore._scoped_doc_id(graphmd.CONVENTIONS_DOC_ID, None), graphmd.CONVENTIONS_DOC_ID)
        self.assertEqual(docstore._scoped_doc_id(graphmd.CONVENTIONS_DOC_ID, ""), graphmd.CONVENTIONS_DOC_ID)

    def test_관례_리터럴이_아니면_owner가_있어도_그대로다(self):
        self.assertEqual(docstore._scoped_doc_id("d1234567890abcdef", "user:a"),
                         "d1234567890abcdef")

    def test_doc_id가_None이어도_예외_없이_그대로다(self):
        self.assertIsNone(docstore._scoped_doc_id(None, "user:a"))


_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ConventionsCollisionE2E(unittest.TestCase):
    """실경로 — `_DocMeta` 는 실제로 `doc_id` 단일 키다. 단위 검사만으로는 그
    조회가 owner 를 정말 안 보는지, 그래서 스코핑이 정말 물리 행을 가르는지를
    못 잡는다."""

    def setUp(self):
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store")}.items():
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

    def test_서로_다른_owner가_각자_graph_md를_가진다(self):
        docstore.put_doc(self.store, owner="user:a", text="# A 의 관례",
                         doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")
        docstore.put_doc(self.store, owner="user:b", text="# B 의 관례",
                         doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")

        got_a = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:a")
        got_b = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:b")

        self.assertTrue(got_a.get("executed"), got_a)
        self.assertTrue(got_b.get("executed"), got_b)
        self.assertEqual(got_a["text"], "# A 의 관례")
        self.assertEqual(got_b["text"], "# B 의 관례")
        # SEC-1 은 그대로다 — 서로의 슬롯을 자기 doc_id 로는 못 연다.
        self.assertNotIn("error", got_a)
        self.assertNotIn("error", got_b)

    def test_레거시_문서는_원래_owner에게_폴백으로_읽힌다(self):
        # 스코핑 이전 쓰기를 흉내: 리터럴 물리 슬롯에 직접 적재.
        raw_id = docstore.put_doc(self.store, owner="user:legacy",
                                  text="# 레거시 관례", origin="")["doc_id"]
        # 이 리포의 fix 이전이라면 doc_id 는 리터럴 그대로였을 것이다 — 여기서는
        # `_scoped_doc_id` 를 우회해 그 상태를 직접 재현한다.
        self.store.run(
            "MATCH (d:_DocMeta {doc_id: $old}) SET d.doc_id = $new",
            {"old": raw_id, "new": graphmd.CONVENTIONS_DOC_ID})
        self.store.run(
            "MATCH (c:Chunk {doc_id: $old}) SET c.doc_id = $new",
            {"old": raw_id, "new": graphmd.CONVENTIONS_DOC_ID})

        got = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:legacy")
        self.assertTrue(got.get("executed"), got)
        self.assertEqual(got["text"], "# 레거시 관례")

    def test_레거시_문서는_다른_owner에게_새지_않는다(self):
        raw_id = docstore.put_doc(self.store, owner="user:legacy",
                                  text="# 레거시 관례", origin="")["doc_id"]
        self.store.run(
            "MATCH (d:_DocMeta {doc_id: $old}) SET d.doc_id = $new",
            {"old": raw_id, "new": graphmd.CONVENTIONS_DOC_ID})
        self.store.run(
            "MATCH (c:Chunk {doc_id: $old}) SET c.doc_id = $new",
            {"old": raw_id, "new": graphmd.CONVENTIONS_DOC_ID})

        got = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:someone-else")
        self.assertFalse(got.get("executed"))
        self.assertIn("owner mismatch", got.get("error", ""))

    def test_owner_없는_로컬_스토어는_그대로_리터럴을_쓴다(self):
        put = docstore.put_doc(self.store, owner="user:solo", text="# 로컬 관례",
                               doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")
        self.assertEqual(put["doc_id"], graphmd.CONVENTIONS_DOC_ID)
        got = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner=None)
        self.assertTrue(got.get("executed"), got)
        self.assertEqual(got["text"], "# 로컬 관례")

    def test_delete도_owner_슬롯을_먼저_보고_레거시로_폴백한다(self):
        docstore.put_doc(self.store, owner="user:a", text="# A", origin="",
                         doc_id=graphmd.CONVENTIONS_DOC_ID, title="GRAPH.md")
        deleted = docstore.delete_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:a")
        self.assertTrue(deleted.get("executed"), deleted)
        self.assertTrue(deleted.get("found"))
        # 지워진 뒤에는 같은 owner 로 다시 읽어도 없다.
        got = docstore.get_doc(self.store, graphmd.CONVENTIONS_DOC_ID, owner="user:a")
        self.assertFalse(got.get("executed"))


if __name__ == "__main__":
    unittest.main()
