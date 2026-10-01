# -*- coding: utf-8 -*-
"""워커의 5분 폴이 모델이 같을 때 쓰기 잡을 걸지 않는가 — `VincApp.read_embed_pending`.

잡 큐는 끝난 잡을 전부 쓰기로 세어 스냅샷을 예약한다. 그래서 모델 확인이 매번 잡으로
돌면 아무것도 안 바뀌어도 5분마다 DB 전체가 공유 볼륨으로 복사됐다(757MB 에서 하루
약 270GB). 모델이 같으면 잡이 없어야 하고, 다르면 예전처럼 전환이 돌아야 한다.
근거: [[record:hub-snapshots-the-whole-database-every-five-minutes-because-a-no-op-job-counts-as-a-write-2026-09-25]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EmbedPendingModelCheckTests(unittest.TestCase):
    def setUp(self):
        from store.backend import open_store
        from store.config import load_config
        self._tmp = tempfile.mkdtemp()
        self.store = open_store(load_config({"data_dir": os.path.join(self._tmp, "store")}))
        self.assertIsNotNone(self.store, "임베디드 스토어 open 실패")
        self.store.provision(dim=4)
        self.app = VincApp(store=self.store, auth=SessionAuth("t"))
        self.jobs = []
        real = self.app.jobs.run_sync

        def spy(label, fn, **kw):
            self.jobs.append(label)
            return real(label, fn, **kw)
        self.app.jobs.run_sync = spy

    def tearDown(self):
        try:
            self.app.close()
        except Exception:
            pass

    def test_same_model_runs_no_job(self):
        self.app.read_embed_pending({"model": "model-A"})      # 처음: 기록이 비어 있다
        self.assertEqual(self.jobs, ["embed.model"])
        self.assertEqual(docstore.stored_embed_model(self.store), "model-A")
        self.app.read_embed_pending({"model": "model-A"})      # 이후 매 폴
        self.app.read_embed_pending({"model": "model-A"})
        self.assertEqual(self.jobs, ["embed.model"], "모델이 같은데 쓰기 잡을 또 걸었다")

    def test_a_different_model_still_switches(self):
        self.app.read_embed_pending({"model": "model-A"})
        self.app.read_embed_pending({"model": "model-B"})
        self.assertEqual(self.jobs, ["embed.model", "embed.model"])
        self.assertEqual(docstore.stored_embed_model(self.store), "model-B")

    def test_no_model_param_runs_no_job(self):
        code, body = self.app.read_embed_pending({})
        self.assertEqual(code, 200)
        self.assertEqual(self.jobs, [])

    def test_apply_route_passes_the_basis_through(self):
        """RV-03 — 워커가 실은 본문 해시·모델이 `/embed/apply` 에서 **버려지지 않는다.**
        라우트가 키를 흘리면 대조가 조용히 꺼진다(값이 None 이면 대조를 생략하므로)."""
        d = docstore.put_doc(self.store, owner="u", text="옛 본문", title="T")
        cid = self.store.run("MATCH (c:Chunk) WHERE c.doc_id=$d RETURN c.chunk_id AS id",
                             {"d": d["doc_id"]})[0]["id"]
        body = {"doc_id": d["doc_id"], "embeddings": {cid: [[1.0, 0.0, 0.0, 0.0]]}}
        code, res = self.app.write_embed_apply({**body, "content_hash": "stale"})
        self.assertEqual((code, res.get("error")), (200, "content_changed"), res)
        code, res = self.app.write_embed_apply({**body, "content_hash": d["content_hash"],
                                                "model": "model-X"})
        self.assertEqual(res.get("error"), "model_changed", res)
        code, res = self.app.write_embed_apply({**body, "content_hash": d["content_hash"]})
        self.assertTrue(res.get("executed"), res)


if __name__ == "__main__":
    unittest.main()
