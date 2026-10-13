"""Conversion guards must work on the production request reader connection."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api.auth import SessionAuth
from api.server import VincApp
from api import topic_conversion as conversion
from store.backend import open_store
from store.config import StoreConfig
from store.embedded_backend import _import_driver


SOURCE = "user:reader-source"
TARGET = "team:reader-target"
OTHER = "user:reader-other"
RUN = "00000000-0000-4000-8000-000000000077"
DRIVER, _ = _import_driver()


@unittest.skipIf(DRIVER is None, "Embedded driver required")
class TopicConversionReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.flags = patch.dict(os.environ, {"VINC_TOPIC_CONVERSION": "1"})
        self.flags.start()
        self.addCleanup(self.flags.stop)
        self.addCleanup(self.directory.cleanup)
        store = open_store(StoreConfig(data_dir=str(Path(self.directory.name) / "store"), buffer_pool_mb=64))
        self.assertIsNotNone(store)
        store.provision(dim=4)
        self.app = VincApp(store=store, auth=SessionAuth("test"))
        self.addCleanup(self.app.close)
        self.app._query_embedding = lambda _: (None, None)

    def read(self, owner, method, params):
        token = conversion.request_owner.set(owner)
        try:
            return method({**params, "owner": owner})
        finally:
            conversion.request_owner.reset(token)

    def test_enabled_guard_preserves_search_and_domain_reads(self):
        for mode in ("general", "current", "historical"):
            with self.subTest(mode=mode):
                code, result = self.read(SOURCE, self.app.read_search,
                                         {"query": "reader probe", "mode": mode, "top_k": 1})
                self.assertEqual(code, 200)
                self.assertTrue(result.get("executed"), result)
                self.assertFalse(result.get("error"), result)
        code, result = self.read(SOURCE, self.app.read_domains, {})
        self.assertEqual(code, 200)
        self.assertTrue(result.get("executed"), result)

    def test_pending_journal_fences_only_affected_owners(self):
        conversion._save(self.app.store, RUN, {
            "status": "committing", "source": SOURCE, "target": TARGET,
            "topic": "reader/topic", "physical_id": SOURCE + "\x1fconcept:test",
        })
        for owner in (SOURCE, TARGET, None):
            with self.subTest(owner=owner):
                code, result = self.read(owner, self.app.read_domains, {})
                self.assertEqual(code, 409)
                self.assertEqual(result["error"], "space_conversion_pending")
        code, result = self.read(OTHER, self.app.read_domains, {})
        self.assertEqual(code, 200)
        self.assertTrue(result.get("executed"), result)

    def test_reader_journal_keeps_embedded_physical_identifiers(self):
        journal = {"status": "active", "source": SOURCE, "target": TARGET,
                   "physical_id": SOURCE + "\x1fconcept:test"}
        conversion._save(self.app.store, RUN, journal)
        reader = self.app.store.reader_view()
        try:
            self.assertEqual(conversion.journals(reader), [journal])
        finally:
            reader.close()


if __name__ == "__main__":
    unittest.main()
