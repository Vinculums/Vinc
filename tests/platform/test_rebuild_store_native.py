# -*- coding: utf-8 -*-
"""`scripts/rebuild_store_native.py` — 네이티브 EXPORT/IMPORT 재구축이 행·인덱스를 다 옮기고
쓰기를 받는 스토어를 내놓는가. 허브 복구(`hub-store-rebuild.yml`)가 이것 하나에 기댄다.

판정이 어긋나면 대상을 남기지 않는다는 것도 본다 — 반쯤 지은 파일이 볼륨에 올라가면
그것으로 갈아탄 허브가 조용히 데이터를 잃는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from store import embedded_backend as eb  # noqa: E402

_DRIVER, _ = eb._import_driver()


def _seed(path: str) -> None:
    from api import docstore
    from store.backend import open_store
    from store.config import load_config
    s = open_store(load_config({"data_dir": path}))
    assert s is not None, eb.EmbeddedBackend.last_open_error
    try:
        s.provision(dim=4)
        for i in range(3):
            r = docstore.put_doc(s, owner="user:a", doc_id=f"doc.{i}", title=f"Doc {i}",
                                 text=f"Alpha beta document {i}.\n\n" + "Body text. " * 40,
                                 topic="vinc/test")
            assert r.get("executed"), r
        s.run("MERGE (c:Concept {concept_id: 'k1'}) SET c.owner_id = 'user:a', c.title = 'K'")
        s.ensure_search_indexes(dim=4)
    finally:
        s.close()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NativeRebuildTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "db")
        self.dst = os.path.join(self.dir, "db.rebuilt")
        _seed(self.src)

    def test_rows_indexes_and_writes_survive_the_rebuild(self):
        import rebuild_store_native as rb
        report = rb.rebuild(self.src, self.dst)
        self.assertTrue(report["ok"], report.get("problems"))
        self.assertEqual(report["src_counts"], report["dst_counts"])
        self.assertIn("Chunk.chunk_ft", report["dst_indexes"])
        self.assertIn("Concept._PK", report["dst_indexes"])
        self.assertTrue(report["probe_after"]["ok"], report["probe_after"])
        self.assertTrue(os.path.exists(self.dst))

    def test_an_existing_target_is_refused_and_left_alone(self):
        with open(self.dst, "wb") as f:
            f.write(b"KEEP")
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "rebuild_store_native.py"),
                            "--data", self.src, "--out", self.dst, "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FileExistsError", r.stdout)
        # 판정 실패는 반쯤 지은 대상을 지우지만, 처음부터 있던 파일은 이 스크립트의 것이 아니다.
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), b"KEEP", "거절하면서 기존 파일을 지웠다")


if __name__ == "__main__":
    unittest.main()
