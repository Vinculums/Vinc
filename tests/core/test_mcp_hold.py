# -*- coding: utf-8 -*-
"""MCP 붙잡기 모드 — 포트는 먼저 열리고, 앱이 서기 전 요청은 실패하지 않고 기다린다.

허브 writer 인계에서 새 리비전의 MCP 는 사이드카보다 먼저 뜬다. 지키는 것:

1. `/health` 는 기다리지 않고 `starting` 을 답한다.
2. 다른 요청은 앱이 설 때까지 기다렸다가 **그대로 처리된다**(실패로 바뀌지 않는다).
3. 상한을 넘기면 503 과 `Retry-After` 로 답한다 — 숨기지 않고 재시도 때를 알려 준다.
4. 사이드카가 아직이면(조립이 예외) 다시 시도하고, 서버가 내려가면 재시도도 멈춘다.
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp.bridge import Bridge                               # noqa: E402
from vinc_mcp.http_server import make_held_server               # noqa: E402


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": True}

    def health_detail(self, *a, **k):
        return {"status": "ok"}


class Hold(unittest.TestCase):
    def _serve(self, build, hold_s=5.0):
        httpd = make_held_server(_StubBridge(), build, host="127.0.0.1", port=0, hold_s=hold_s)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def _call(self, port, method, path, body=None, results=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        headers = {"Content-Type": "application/json", "Authorization": "Bearer S"}
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Retry-After"), resp.read())
        if results is not None:
            results.append((time.monotonic(), out))
        return out

    def test_health_answers_starting_without_waiting(self):
        gate = threading.Event()
        port = self._serve(lambda: (gate.wait(), {"static_token": "S"})[1])
        t0 = time.monotonic()
        status, _, raw = self._call(port, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["status"], "starting")
        self.assertLess(time.monotonic() - t0, 2.0)
        gate.set()

    def test_a_request_waits_and_is_then_served_not_failed(self):
        gate = threading.Event()
        port = self._serve(lambda: (gate.wait(), {"static_token": "S"})[1])
        results: list = []
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        worker = threading.Thread(target=self._call, args=(port, "POST", "/mcp", rpc, results))
        worker.start()
        time.sleep(0.5)
        self.assertEqual(results, [], "앱이 서기 전에 답이 나갔다")
        released_at = time.monotonic()
        gate.set()
        worker.join(15)
        self.assertEqual(len(results), 1)
        answered_at, (status, _, raw) = results[0]
        self.assertGreaterEqual(answered_at, released_at)
        self.assertEqual(status, 200, raw[:200])
        self.assertIn("tools", json.loads(raw)["result"])

    def test_past_the_hold_limit_it_answers_503_with_retry_after(self):
        port = self._serve(lambda: (_ for _ in ()).throw(RuntimeError("sidecar unreachable")),
                           hold_s=0.5)
        status, retry_after, raw = self._call(port, "POST", "/mcp",
                                              {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 503)
        self.assertEqual(retry_after, "5")
        self.assertEqual(json.loads(raw)["error"], "starting")

    def test_assembly_is_retried_until_the_sidecar_is_ready(self):
        tries = {"n": 0}

        def build():
            tries["n"] += 1
            if tries["n"] < 3:
                raise RuntimeError("sidecar unreachable")
            return {"static_token": "S"}

        httpd = make_held_server(_StubBridge(), build, host="127.0.0.1", port=0, hold_s=5.0)
        self.addCleanup(httpd.server_close)
        import vinc_mcp.http_server as hs
        # 재시도 간격을 줄이지 않고, 조립 스레드가 끝날 때까지만 기다린다.
        self.assertTrue(httpd.ready.wait(10), "조립이 끝나지 않았다")
        self.assertGreaterEqual(tries["n"], 3)
        self.assertIsInstance(httpd.app, hs.MCPHTTPApp)

    def test_retries_stop_when_the_server_goes_down(self):
        def build():
            raise RuntimeError("sidecar unreachable")

        before = {t.ident for t in threading.enumerate() if t.name == "vinc-mcp-hold"}
        httpd = make_held_server(_StubBridge(), build, host="127.0.0.1", port=0, retry_s=0.05)
        mine = [t for t in threading.enumerate()
                if t.name == "vinc-mcp-hold" and t.ident not in before]
        self.assertEqual(len(mine), 1)
        httpd.server_close()
        mine[0].join(5)
        self.assertFalse(mine[0].is_alive(), "내려간 서버의 조립 재시도가 계속 돈다")
        self.assertFalse(httpd.ready.is_set())


if __name__ == "__main__":
    unittest.main()
