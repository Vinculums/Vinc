# -*- coding: utf-8 -*-
"""계정 연결 메일박스의 만료를 HTTP 없이 고정한다.

메일박스는 `POST /connect/start` 가 발급한 state 논스 하나에 칸 하나를 둔다. 칸의 수명은 발급
시각부터 `expires_in` 초이고, 콜백(`POST /connect/callback`)이 키를 넣어도 늘어나지 않는다. 만료
시각에 닿은 칸은 콜백을 받지 않고, 상태 조회(`GET /connect/status`)는 `expired` 를 답하며 칸을 지운다.
새 발급은 만료된 칸과 키가 든 칸을 치우고, 키를 기다리는 칸은 남긴다.

여기서는 단조시계(`time.monotonic`)를 가짜로 바꿔 경계를 잠 없이 결정론으로 본다. 앱의 공개 메서드만
부르고, 수명은 발급 답의 `expires_in` 으로 읽는다. 브라우저 왕복·Origin·인증 시나리오는
`test_server.py` 의 `ConnectFlowTests` 에 있다.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402

_ORIGIN = "https://vincs.io"


class ConnectMailboxExpiryTests(unittest.TestCase):

    def setUp(self):
        self.now = 5000.0
        env = mock.patch.dict("os.environ", {"VINC_CONNECT_ORIGINS": _ORIGIN})
        env.start()
        self.addCleanup(env.stop)
        self.app = VincApp(store=None, auth=SessionAuth("connect-expiry-test"))
        self.addCleanup(self.app.jobs.shutdown, drain=True)
        clock = mock.patch("time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def start(self):
        code, out = self.app.write_connect_start({})
        self.assertEqual(code, 200)
        return out["state"], out["expires_in"]

    def deliver(self, state, key="vinc_sk_app"):
        return self.app.connect_callback({"state": state, "key": key, "email": "u@example.com"}, _ORIGIN)

    def status(self, state):
        code, out = self.app.read_connect_status({"state": state})
        self.assertEqual(code, 200)
        return out["status"]

    def test_the_lifetime_is_ten_minutes_from_start(self):
        _, ttl = self.start()
        self.assertEqual(ttl, 600)

    def test_a_pending_state_expires_at_the_boundary(self):
        state, ttl = self.start()
        self.now += ttl - 0.5
        self.assertEqual(self.status(state), "pending")
        self.now += 0.5
        self.assertEqual(self.status(state), "expired")
        self.assertEqual(self.status(state), "expired")

    def test_the_callback_is_refused_from_the_boundary_on(self):
        late, ttl = self.start()
        self.now += ttl
        self.assertEqual(self.deliver(late), (400, {"error": "unknown, used or expired state"}))
        self.now -= ttl
        early, _ = self.start()
        self.now += ttl - 0.5
        self.assertEqual(self.deliver(early), (200, {"status": "ok"}))

    def test_a_delivered_key_does_not_extend_the_lifetime(self):
        state, ttl = self.start()
        self.now += 10
        self.assertEqual(self.deliver(state), (200, {"status": "ok"}))
        self.now += ttl - 10
        self.assertEqual(self.status(state), "expired")
        # 지운 칸은 다시 받지도 않는다.
        self.assertEqual(self.deliver(state, "vinc_sk_again"), (400, {"error": "unknown, used or expired state"}))

    def test_an_expired_state_stays_expired_after_status_removes_it(self):
        state, ttl = self.start()
        self.now += ttl + 1
        self.assertEqual(self.status(state), "expired")
        self.now -= ttl + 1
        self.assertEqual(self.status(state), "expired", "조회가 지운 칸이 시계를 되돌려도 살아났다")

    def test_a_new_start_clears_a_delivered_state_but_keeps_a_pending_one(self):
        delivered, _ = self.start()
        self.assertEqual(self.deliver(delivered), (200, {"status": "ok"}))
        pending, _ = self.start()
        # 키가 든 칸은 수명 안이어도 새 발급이 치웠다. 키를 기다리는 칸은 남았다.
        self.assertEqual(self.status(delivered), "expired")
        self.assertEqual(self.status(pending), "pending")
        self.assertEqual(self.deliver(pending), (200, {"status": "ok"}))
        self.assertEqual(self.status(pending), "delivered")

    def test_a_new_start_clears_a_pending_state_only_once_it_has_expired(self):
        kept, ttl = self.start()
        self.now += ttl - 0.5
        self.start()
        self.now -= ttl - 0.5
        self.assertEqual(self.status(kept), "pending")
        cleared, _ = self.start()
        self.now += ttl
        self.start()
        self.now -= ttl
        # 수명이 다한 칸은 새 발급이 치웠다: 시계를 되돌려도 돌아오지 않는다.
        self.assertEqual(self.status(cleared), "expired")


if __name__ == "__main__":
    unittest.main()
