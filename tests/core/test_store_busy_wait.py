# -*- coding: utf-8 -*-
"""읽기는 짧은 유지보수를 기다려 넘기고, 긴 유지보수에는 `store_busy` 로 유한하게 답한다(VIN-945).

실엔진 스토어 위의 실제 사이드카에 HTTP 로 묻는다. 유지보수는 DDL 배타 가드를 다른 스레드가
쥐는 것으로 흉내 낸다 — 가드의 배타성은 그대로이고, 바뀐 것은 읽기가 기다리는 시간뿐이다.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import embedded_backend as B  # noqa: E402

_DRIVER, _NAME = B.import_driver()
_TOKEN = "test-token-busy"
_OWNER = "user:a"

#: 짧은 유지보수 창. 1코어에서 잰 임베딩 적용(배타 보유 0.6초 안팎)과 앞선 요청이 빠지기를
#: 기다리는 시간이 겹친 최대(1.3초 안팎)보다 넉넉하게 잡았다. 옛 상한(1초)은 넘는다.
_SHORT_HOLD_S = 2.5


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class StoreBusyWaitTests(unittest.TestCase):
    def setUp(self):
        from api import docstore as ds
        from api.auth import SessionAuth
        from api.server import VincApp, make_server
        from store.backend import open_store
        from store.config import load_config

        self._tmp = tempfile.mkdtemp()
        self._saved = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(self._tmp, "store")
        store = open_store(load_config())
        self.assertIsNotNone(store)
        store.provision(dim=4)
        self.doc_id = ds.put_doc(store, owner=_OWNER, title="Busy",
                                 text="# Busy\n\nmaintenance ride through\n")["doc_id"]
        self.app = VincApp(store=store, auth=SessionAuth(_TOKEN))
        self.httpd = make_server(self.app, host="127.0.0.1", port=0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.app.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _get_doc(self):
        req = urllib.request.Request(f"{self.base}/doc?id={self.doc_id}&owner={_OWNER}")
        req.add_header("Authorization", f"Bearer {_TOKEN}")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, body = resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            status, body = e.code, json.loads(e.read().decode("utf-8"))
        return status, body, time.monotonic() - started

    @contextlib.contextmanager
    def _maintenance(self, seconds: float):
        """다른 스레드가 배타 가드를 `seconds` 동안 쥔다(인덱스·스키마 유지보수의 모양)."""
        held, done = threading.Event(), threading.Event()

        def hold():
            with B.DDL_GUARD.exclusive():
                held.set()
                done.wait(seconds)

        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(held.wait(5), "배타 가드를 잡지 못했다")
        try:
            yield
        finally:
            done.set()
            t.join(seconds + 5)

    def test_read_rides_through_a_short_maintenance_window(self):
        self.assertEqual(self._get_doc()[0], 200)
        with self._maintenance(_SHORT_HOLD_S):
            status, body, took = self._get_doc()
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("executed"), body)
        self.assertIn("maintenance ride through", body.get("text", ""))
        # 유지보수가 끝나기를 기다렸다가 답했고, 상한 안이다.
        self.assertGreater(took, _SHORT_HOLD_S - 0.5)
        self.assertLess(took, B.REQUEST_WAIT_S)

    def test_read_longer_than_the_budget_still_answers_store_busy(self):
        with self._maintenance(B.REQUEST_WAIT_S + 3):
            status, body, took = self._get_doc()
        self.assertEqual(status, 503, body)
        self.assertEqual(body.get("error"), "store_busy")
        self.assertEqual(body.get("retry_after_s"), 1)
        self.assertFalse(body.get("executed"))
        # 상한을 다 쓰고 거절한다 — 유지보수가 끝날 때까지 매달리지 않는다.
        self.assertGreater(took, B.REQUEST_WAIT_S - 0.5)
        self.assertLess(took, B.REQUEST_WAIT_S + 1.5)
        # 유지보수가 끝나면 같은 읽기가 다시 답한다.
        self.assertEqual(self._get_doc()[0], 200)


if __name__ == "__main__":
    unittest.main()
