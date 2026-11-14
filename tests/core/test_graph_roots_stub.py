# -*- coding: utf-8 -*-
"""`/graph/roots` 는 시간 가지 배치를 맡아 줄 곳이 없어도 길이 남아 있고, 늘 꺼진 답을 준다.

깨지면 조용하다: 이 길을 부르는 클라이언트가 404 나 500 을 받으면 화면이 직접 배치하는 길로
넘어가지 못한다. 답은 `state:"off"` 하나여야 한다.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import routes  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402

OFF = {"executed": True, "state": "off"}


class _Store:
    driver_name = "fake"

    def reader_view(self):
        return self

    def run(self, cypher, params=None):
        raise AssertionError("the roots stub must not read the store")

    def close(self):
        pass


class GraphRootsStubTests(unittest.TestCase):
    def setUp(self):
        self.app = VincApp(store=_Store(), auth=SessionAuth("t"), quiet_boot=True)
        self.addCleanup(self.app.close)

    def get(self, params):
        return routes.route_get(self.app, "/graph/roots", params)

    def test_every_basis_and_digest_gets_the_off_answer(self):
        for params in ({"owner": "user:a"},
                       {"owner": "user:a", "basis": "recorded"},
                       {"owner": "user:a", "basis": "content"},
                       {"owner": "user:a", "basis": "garbage"},
                       {"owner": "user:a", "basis": "recorded", "have": "abcdef0123456789"}):
            with self.subTest(params=params):
                self.assertEqual(self.get(params), (200, OFF))

    def test_the_owner_is_still_required(self):
        for params in ({}, {"owner": ""}):
            with self.subTest(params=params):
                with self.assertRaises(Exception) as caught:
                    self.get(params)
                self.assertIn("owner", str(caught.exception))

    def test_a_write_does_not_change_the_answer(self):
        self.app.changes.bump("user:a")
        self.assertEqual(self.get({"owner": "user:a"}), (200, OFF))

    def test_the_answer_serialises_as_before(self):
        self.assertEqual(json.dumps(self.get({"owner": "user:a"})[1], separators=(",", ":")),
                         '{"executed":true,"state":"off"}')

    def test_the_route_is_open_to_the_web_proxy(self):
        from vinc_mcp import http_server as hs
        self.assertEqual(hs._PROXY_OPS.get("graph-roots"), ("GET", "/graph/roots"))
        self.assertIn("graph-roots", hs.team_role_tiers()["viewer"])


if __name__ == "__main__":
    unittest.main()
