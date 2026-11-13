"""전체 공유의 원문 완전성·owner 격리·선택 범위·팩 왕복을 실제 스토어로 검증한다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import docstore as ds, share, packfile
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import import_driver


@unittest.skipIf(import_driver()[0] is None, "embedded driver unavailable")
class EverythingDocumentsTests(unittest.TestCase):
    OWNER = "user:source"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = open_store(load_config({"data_dir": str(Path(self.tmp.name) / "db")}))
        self.store.provision(dim=4)
        self.addCleanup(self.store.close)

    def put(self, text, title="Imported", owner=None, **kwargs):
        result = ds.put_doc(self.store, owner=owner or self.OWNER, text=text, title=title, **kwargs)
        self.assertTrue(result["executed"], result)
        return result["doc_id"]

    def pack(self, **scope):
        return share.build_share_pack(owner=self.OWNER, store=self.store, doc_refs=True, **scope)

    def test_everything_contains_unlinked_documents_and_no_other_owner_data(self):
        plain = self.put("Imported text without a topic.")
        linked = self.put("A topic document.", title="Alpha", topic="alpha")
        secret = self.put("Private space text.", owner="team:other", title="Other")
        pack = self.pack()
        self.assertTrue(pack["executed"], pack)
        self.assertEqual({d["doc_id"] for d in pack["documents"]}, {plain, linked})
        self.assertEqual(pack["manifest"]["counts"]["documents"], 2)
        self.assertEqual(pack["doc_refs"][plain], [])
        self.assertTrue(pack["doc_refs"][linked])
        self.assertNotIn(secret, str(pack))
        self.assertNotIn("Private space text", str(pack))

    def test_document_only_space_survives_pack_file_and_receiver_import(self):
        text = "No headings or topic. 한글 원문도 그대로.\n" * 80
        did = self.put(text)
        pack = self.pack()
        self.assertTrue(pack["executed"], pack)
        self.assertEqual(pack["fragment"]["nodes"], [])
        self.assertEqual(pack["manifest"]["counts"]["documents"], 1)
        self.assertEqual(pack["doc_refs"], {did: []})
        # 앱이 선택을 마친 뒤 저장하는 것과 같은 zip 컨테이너를 왕복한다.
        pack.pop("doc_refs")
        pack.pop("executed")
        restored = packfile.from_bytes(packfile.to_bytes(pack))
        imported = share.import_share_pack(restored, owner="user:receiver", store=self.store)
        self.assertTrue(imported["executed"], imported)
        received = ds.list_docs(self.store, owner="user:receiver", detail="summary")["docs"]
        self.assertEqual(len(received), 1)
        got = ds.get_doc(self.store, received[0]["doc_id"], owner="user:receiver")
        self.assertTrue(got["verified"], got)
        self.assertEqual(got["text"], text)
        self.assertNotEqual(got["doc_id"], did)

    def test_explicit_scopes_do_not_gain_unlinked_documents(self):
        plain = self.put("Outside a topic.")
        linked = self.put("Scoped text.", title="Alpha", topic="alpha")
        mirror = ds._DOC_MIRROR_PREFIX + linked
        for scope in ({"domain": "alpha"}, {"tags": ["doc"]}, {"ids": [mirror]},
                      {"ids": []}, {"seed": mirror, "depth": 1}, {"include_records": False}):
            with self.subTest(scope=scope):
                pack = self.pack(**scope)
                self.assertTrue(pack["executed"], pack)
                self.assertNotIn(plain, {d["doc_id"] for d in pack.get("documents", [])})

    def test_conventions_document_follows_existing_document_list_exclusion(self):
        ordinary = self.put("User material.")
        self.put("# GRAPH.md\nHouse rules.", title="GRAPH.md", doc_id="graph.md")
        pack = self.pack()
        self.assertTrue(pack["executed"], pack)
        self.assertEqual([d["doc_id"] for d in pack["documents"]], [ordinary])

    def test_incomplete_or_unverified_document_cannot_be_a_successful_partial_pack(self):
        first = self.put("Readable text.", title="First")
        bad = self.put("Another document.", title="Second")
        original = ds.get_doc
        for failure in ({"executed": False, "error": "document is incomplete"},
                        {"executed": True, "verified": False, "text": "partial"}):
            def read(store, doc_id, **kwargs):
                return failure if doc_id == bad else original(store, doc_id, **kwargs)
            with self.subTest(failure=failure), patch.object(ds, "get_doc", side_effect=read):
                pack = self.pack()
                self.assertFalse(pack["executed"], pack)
                self.assertNotIn("documents", pack)
                self.assertNotIn("fragment", pack)
                self.assertNotIn(first, str(pack))
