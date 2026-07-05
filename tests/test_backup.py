# -*- coding: utf-8 -*-
"""백업 — 스냅샷 + packs/profile 동봉(설계 §11 P2·R9). 스냅샷 복원 왕복(게이트 ③)은
드라이버 설치 시 실측, 동봉 로직은 fake 스토어로 무드라이버 검증."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.backup import make_backup  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class _FakeStore:
    """snapshot 만 있는 최소 스토어 — 데이터 디렉토리를 그대로 복사(EmbeddedBackend
    계약 동형). 동봉 로직 검증용(드라이버 불요)."""

    def __init__(self, data_dir):
        self._data_dir = data_dir

    def snapshot(self, dest_dir):
        import shutil
        base = os.path.basename(self._data_dir.rstrip("/")) or "store"
        dest = os.path.join(dest_dir, base)
        shutil.copytree(self._data_dir, dest, dirs_exist_ok=True)
        return dest


class BackupBundleTests(unittest.TestCase):
    def test_bundles_store_packs_and_profile(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # fake 데이터 디렉토리 + packs + profile
            data = root / "vinc_store"
            data.mkdir()
            (data / "graph.db").write_text("DATA", encoding="utf-8")
            packs = root / "packs"
            packs.mkdir()
            (packs / "pack.json").write_text("{}", encoding="utf-8")
            profile = root / "profile.yml"
            profile.write_text("prefix: vinc\n", encoding="utf-8")

            out = root / "backup"
            res = make_backup(_FakeStore(str(data)), out,
                              packs_dir=str(packs), profile_path=str(profile))

            self.assertEqual(res["archive"], str(out))
            self.assertTrue((out / "vinc_store" / "graph.db").exists())   # 스토어 스냅샷
            self.assertTrue((out / "packs" / "pack.json").exists())        # 팩 동봉
            self.assertTrue((out / "profile.yml").exists())                # 프로필 동봉
            self.assertEqual((out / "profile.yml").read_text(encoding="utf-8"), "prefix: vinc\n")

    def test_missing_optional_sources_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = root / "vinc_store"
            data.mkdir()
            (data / "x").write_text("y", encoding="utf-8")
            res = make_backup(_FakeStore(str(data)), root / "bk",
                              packs_dir=str(root / "nope"), profile_path=str(root / "nope.yml"))
            self.assertIn("store", res["included"])
            self.assertNotIn("packs", res["included"])
            self.assertNotIn("profile", res["included"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버 미설치 — 스냅샷 복원 왕복 skip")
class SnapshotRoundTripTests(unittest.TestCase):
    """게이트 ③ — write → backup(snapshot) → 복원 dir 로 store open → 데이터 존재."""

    def test_snapshot_restore_roundtrip(self):
        from store.config import load_config
        from store.backend import open_store
        from engines.recall_to_neo4j import merge_episode
        from api import query as q

        with tempfile.TemporaryDirectory() as d:
            src_dir = os.path.join(d, "store")
            env_backup = dict(os.environ)
            os.environ["MEMORY_STORE_BACKEND"] = "embedded"
            os.environ["MEMORY_DATA_DIR"] = src_dir
            os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
            try:
                cfg = load_config()
                store = open_store(cfg)
                store.provision(dim=cfg.embedding_dim)
                with store.session() as s:
                    merge_episode(s, {"episode_id": "b1", "title": "Backup me",
                                      "date": "2026-07-05", "owner_id": "u", "tenant_id": "t",
                                      "domain": "d", "workspace": "w", "mode": "m",
                                      "summary": "s", "completed_items": [], "patterns": [],
                                      "source_file": "f"})
                out = os.path.join(d, "backup")
                res = make_backup(store, out)
                store.close()

                # 복원 dir 로 새 스토어 — 에피소드가 살아있어야 한다.
                restored = res["included"]["store"]
                os.environ["MEMORY_DATA_DIR"] = restored
                r = q.recall(owner="u")
                self.assertTrue(r["executed"])
                self.assertEqual(r["rows"][0]["id"], "b1")
            finally:
                os.environ.clear()
                os.environ.update(env_backup)


if __name__ == "__main__":
    unittest.main()
