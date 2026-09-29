# -*- coding: utf-8 -*-
"""VIN-82 불변 세대 게시/복구의 중단 지점. 실제 엔진 재생은 별도 통합 게이트다."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from store import durable_generation as dg
from store import embedded_backend as eb


class DurableGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.persistent = self.root / "share"
        self.source = self.root / "source"
        self.source.write_bytes(b"BASE-1")
        self.wal = self.root / "source.wal"
        self.wal.write_bytes(b"WAL-1")
        self.live = self.root / "replacement" / "db"

    def test_complete_pair_restores_and_rejects_replay_of_sequence(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        self.assertEqual(dg.restore(self.persistent, self.live), "restored:1")
        self.assertEqual(self.live.read_bytes(), b"BASE-1")
        self.assertEqual(Path(f"{self.live}.wal").read_bytes(), b"WAL-1")
        with self.assertRaisesRegex(dg.GenerationError, "not newer"):
            dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)

    def test_interruption_before_commit_marker_keeps_previous_ack(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        self.source.write_bytes(b"BASE-2")
        self.wal.write_bytes(b"WAL-2")
        real_replace = dg.os.replace

        def interrupt(source, destination):
            if str(destination).endswith("commit-2.json"):
                raise OSError("simulated process interruption")
            return real_replace(source, destination)

        with patch.object(dg.os, "replace", side_effect=interrupt):
            with self.assertRaises(OSError):
                dg.publish(self.persistent, self.source, sequence=2, wal=self.wal)
        self.assertEqual(dg.latest_sequence(self.persistent), 1)
        self.assertEqual(dg.restore(self.persistent, self.live), "restored:1")
        self.assertEqual(self.live.read_bytes(), b"BASE-1")
        self.assertEqual(Path(f"{self.live}.wal").read_bytes(), b"WAL-1")

    def test_interruption_during_wal_copy_does_not_publish_partial_pair(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        original = dg._copy_synced

        def interrupt(src, dst):
            if str(dst).endswith("db.wal"):
                raise OSError("share copy interrupted")
            return original(src, dst)

        with patch.object(dg, "_copy_synced", side_effect=interrupt):
            with self.assertRaises(OSError):
                dg.publish(self.persistent, self.source, sequence=2, wal=self.wal)
        self.assertFalse((self.persistent / "commit-2.json").exists())
        self.assertEqual(dg.restore(self.persistent, self.live), "restored:1")
        self.assertEqual(Path(f"{self.live}.wal").read_bytes(), b"WAL-1")

    def test_corrupt_latest_commit_fails_closed_instead_of_losing_ack(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        self.wal.write_bytes(b"WAL-2")
        dg.publish(self.persistent, self.source, sequence=2, wal=self.wal)
        (self.persistent / "generation-2" / "db.wal").write_bytes(b"CORRUPT")
        with self.assertRaisesRegex(dg.GenerationError, "WAL digest mismatch"):
            dg.restore(self.persistent, self.live)
        self.assertFalse(self.live.exists())

    def test_corrupt_base_fails_closed_even_when_wal_is_intact(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        (self.persistent / "generation-1" / "db").write_bytes(b"WRONG-BASE")
        with self.assertRaisesRegex(dg.GenerationError, "base digest mismatch"):
            dg.restore(self.persistent, self.live)

    def test_no_marker_means_staged_generation_is_not_an_ack(self):
        (self.persistent / "generation-3").mkdir(parents=True)
        (self.persistent / "generation-3" / "db").write_bytes(b"UNCOMMITTED")
        self.assertEqual(dg.restore(self.persistent, self.live), "empty")

    def test_partial_restore_fails_closed(self):
        dg.publish(self.persistent, self.source, sequence=1, wal=self.wal)
        self.live.parent.mkdir(parents=True)
        Path(f"{self.live}.wal").write_bytes(b"ORPHAN")
        with self.assertRaisesRegex(dg.GenerationError, "orphan live WAL"):
            dg.restore(self.persistent, self.live)


@unittest.skipIf(eb._import_driver()[0] is None, "Ladybug driver unavailable")
class RealReplayTests(unittest.TestCase):
    def test_checkpoint_base_plus_later_wal_restores_acknowledged_row(self):
        from store.backend import open_store
        from store.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "local" / "db"
            target = root / "replacement" / "db"
            store = open_store(load_config({"data_dir": str(source)}))
            self.assertIsNotNone(store)
            try:
                store.provision(dim=4)
                staged = Path(store.snapshot(str(root / "baseline")))
                store.run("MERGE (c:Concept {concept_id: 'durable-row'}) "
                          "SET c.title = 'acknowledged', c.owner_id = 'user:a'")
                self.assertTrue(Path(f"{source}.wal").is_file(),
                                "test needs a WAL commit after the baseline checkpoint")
                dg.publish(root / "share", staged, sequence=1,
                           wal=Path(f"{source}.wal"))
            finally:
                store.close()
            self.assertEqual(dg.restore(root / "share", target), "restored:1")
            replacement = open_store(load_config({"data_dir": str(target)}))
            self.assertIsNotNone(replacement)
            try:
                rows = replacement.run(
                    "MATCH (c:Concept {concept_id: 'durable-row'}) RETURN c.title AS title")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["title"], "acknowledged")
            finally:
                replacement.close()


if __name__ == "__main__":
    unittest.main()
