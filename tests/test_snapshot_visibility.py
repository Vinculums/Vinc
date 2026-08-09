# -*- coding: utf-8 -*-
"""영속 스냅샷 상태가 **밖에서 보인다**(2026-08-09).

왜 필요한가. `VINC_STORE_LOCAL=on` 이후 작업본은 컨테이너 임시 디스크에 있고
컨테이너와 함께 사라진다. 따라서 "스냅샷이 실제로 돌고 있는가" 가 곧 **데이터가
남는가**이고, 그것이 안 보이면 "돌고 있다고 믿는 백업이 없는" 상태가 된다 —
이 리포가 임베딩 워커에서 38시간, OAuth 등록에서 배포마다 겪은 그 형태다.

사이드카는 이 진단을 **예전부터 싣고 있었다.** 그런데 허브 `/health` 가
`detail["embed"]` 만 올리고 `detail["snapshot"]` 은 **버리고 있었다.** 그래서
2026-08-09 에 밖에서 확인할 방법이 없어 파일 mtime 으로 재다가 오판했다
(Azure REST API 의 lastModified 와 컨테이너가 CIFS 로 보는 mtime 이 다르다).

그래서 이 파일은 **양쪽 끝을 다 못박는다** — 정책이 값을 만드는지, 그리고 그 값이
허브 응답까지 올라오는지. 사이드카만 검사하면 오늘의 결함을 못 잡는다.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request

from store.persistence import SnapshotPolicy
from vinc_mcp.bridge import Bridge
from vinc_mcp.http_server import MCPHTTPApp, make_server


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class PolicyDetailTest(unittest.TestCase):

    def test_never_succeeded_reports_null_age(self):
        """가장 위험한 값 — 이 프로세스가 사는 동안 **한 번도 성공한 적이 없다.**

        상태만 보면 'pending' 이라 무해해 보인다. age 가 null 이어야 알람이 걸린다."""
        p = SnapshotPolicy(every=300, clock=_Clock())
        d = p.detail()
        self.assertIsNone(d["age_s"], d)

    def test_age_counts_from_the_last_success_not_the_last_attempt(self):
        """실패가 반복되면 시도 시각은 갱신되는데 **남은 것은 옛것**이다.

        시도 시각으로 세면 '방금 백업됨'으로 보이면서 실제로는 낡아 간다."""
        clk = _Clock()
        p = SnapshotPolicy(every=300, clock=clk)
        p.done("ok")
        clk.t += 120
        p.done("error: OSError")                     # 실패 — 남은 것은 120초 전 것
        clk.t += 60
        d = p.detail()
        self.assertEqual(d["age_s"], 180, d)
        self.assertTrue(d["state"].startswith("error"), d)

    def test_pending_write_is_visible(self):
        """쓰기가 있었는데 아직 안 떴다 = 지금 죽으면 그만큼 잃는다."""
        p = SnapshotPolicy(every=300, clock=_Clock())
        self.assertFalse(p.detail()["pending_write"])
        p.mark_dirty()
        self.assertTrue(p.detail()["pending_write"])


class _Bridge(Bridge):
    """사이드카 대역 — `health_detail()` 로 진단을 돌려준다."""

    def __init__(self, detail):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:test")
        self._detail = detail

    def health_detail(self):
        return self._detail

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": False}


class HubSurfaceTest(unittest.TestCase):
    """허브 `/health` 가 사이드카의 진단을 **올려 주는가**.

    사이드카가 싣는 것과 허브가 내보내는 것은 다른 문제다. 오늘의 결함이 정확히
    그 사이에 있었다 — 사이드카는 싣고 허브는 버렸다. 그래서 **허브 응답으로**
    검사한다."""

    def _health(self, detail):
        app = MCPHTTPApp(_Bridge(detail), static_token="t")
        httpd = make_server(app, "127.0.0.1", 0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_address[1]}/health", timeout=5) as r:
            return json.loads(r.read().decode())

    def test_hub_surfaces_graph_snapshot(self):
        """회귀 본체 — 사이드카가 실은 진단이 허브 응답에 **최상위로** 나와야 한다."""
        obj = self._health({"status": "ok",
                            "snapshot": {"state": "ok", "age_s": 12,
                                         "every_s": 300.0, "pending_write": False}})
        self.assertIn("graph_snapshot", obj,
                      "허브가 사이드카의 snapshot 진단을 버렸다 — 밖에서 백업 상태를 "
                      "볼 방법이 없다(2026-08-09 결함)")
        self.assertEqual(obj["graph_snapshot"]["state"], "ok")
        self.assertEqual(obj["graph_snapshot"]["age_s"], 12)

    def test_never_succeeded_reaches_the_outside(self):
        """가장 위험한 값이 밖까지 도달하는가 — 이게 안 보이면 알람을 못 건다."""
        obj = self._health({"status": "ok",
                            "snapshot": {"state": "pending", "age_s": None,
                                         "every_s": 300.0, "pending_write": True}})
        self.assertIsNone(obj["graph_snapshot"]["age_s"])
        self.assertTrue(obj["graph_snapshot"]["pending_write"])

    def test_absent_diagnostic_does_not_break_health(self):
        """진단이 없는 구 사이드카에서도 /health 는 200 이어야 한다 — 관측을 얹다가
        헬스를 깨뜨리면 CD 검증이 통째로 막힌다."""
        obj = self._health({"status": "ok"})
        self.assertEqual(obj["status"], "ok")
        self.assertNotIn("graph_snapshot", obj)


if __name__ == "__main__":
    unittest.main()
