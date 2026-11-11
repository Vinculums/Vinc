# -*- coding: utf-8 -*-
"""셀프호스트 허브(`VINC_SELF_HOSTED=on`) — 소유자 하나, 계정 없음.

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S1. 잠그는 것:
  ① 정적 토큰으로 온 도구 호출이 다른 `owner` 를 실어도 고정 소유자로 간다(플래그가 꺼져
     있으면 호출자의 값이 그대로 간다 — 이 플래그가 닫는 구멍이다).
  ② 팀·커뮤니티 스페이스를 겨눈 호출은 "셀프호스트에는 없다"로 답하고 브리지를 안 부른다.
     팀 주소(`/t/<uuid>/mcp`)는 404 다.
  ③ 계정과 말하는 장치(키 검증·멤버십·한도·연결 흐름)와 PK 네임스페이싱이 꺼진다.
  ④ 출력의 vincs.io 주소가 `VINC_PUBLIC_URL` 로 바뀌거나 빠진다.
  ⑤ 허브 조립은 `VINC_MCP_TOKEN`·`VINC_OWNER` 만으로 된다. 모양이 틀린 소유자는 기동을 멈춘다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import entitlement, openapi_v1, roles, selfhost, tools  # noqa: E402
from vinc_mcp import http_server as H  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"
PINNED = "user:me"
ON = {"VINC_SELF_HOSTED": "on", "VINC_OWNER": PINNED}


class _RecordingBridge:
    def __init__(self):
        self.owner = "tenant:local"
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"kind": "graph", "executed": True}

    def owners(self):
        out = []
        for _m, _p, params, body in self.calls:
            src = body if isinstance(body, dict) and "owner" in body else (params or {})
            out.append(src.get("owner"))
        return out


class _Hub(unittest.TestCase):
    env: dict = {}

    def setUp(self):
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("VINC_SELF_HOSTED", "VINC_OWNER", "VINC_PUBLIC_URL"):
            if key not in self.env:
                os.environ.pop(key, None)
        self.bridge = _RecordingBridge()
        self.httpd = H.make_server(H.MCPHTTPApp(self.bridge, static_token="S"), "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def post(self, msg, path="/mcp"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(msg).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer S",
                     "MCP-Protocol-Version": "2025-06-18"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    @staticmethod
    def graph(**args):
        args.setdefault("seed_id", "concept:x")
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "vinc_graph", "arguments": args}}


class FlagOffTests(_Hub):
    def test_꺼져_있으면_정적_토큰은_호출자의_owner_를_쓴다(self):
        self.post(self.graph(owner="user:other"))
        self.assertEqual(self.bridge.owners(), ["user:other"])


class OwnerPinTests(_Hub):
    env = ON

    def test_다른_owner_를_실어도_고정_소유자로_간다(self):
        code, _ = self.post(self.graph(owner="user:other"))
        self.assertEqual(code, 200)
        self.assertEqual(self.bridge.owners(), [PINNED])

    def test_owner_를_안_실어도_고정_소유자다(self):
        self.post(self.graph())
        self.assertEqual(self.bridge.owners(), [PINNED])

    def test_팀과_커뮤니티는_없다고_답한다(self):
        for space in (TEAM, f"community:{TEAM}"):
            _, got = self.post(self.graph(space=space))
            self.assertTrue(got["result"]["isError"], space)
            self.assertEqual(got["result"]["content"][0]["text"], selfhost.NO_SPACES)
        _, got = self.post(self.graph(url=f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x"))
        self.assertEqual(got["result"]["content"][0]["text"], selfhost.NO_SPACES)
        self.assertEqual(self.bridge.calls, [])

    def test_personal_은_그대로_된다(self):
        self.post(self.graph(space="personal"))
        self.assertEqual(self.bridge.owners(), [PINNED])

    def test_팀_주소는_404(self):
        code, _ = self.post(self.graph(), path=f"/t/{TEAM}/mcp")
        self.assertEqual(code, 404)
        self.assertEqual(self.bridge.calls, [])

    def test_틀린_토큰은_401(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp", data=json.dumps(self.graph()).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer nope"},
            method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 401)


class AccountOffTests(unittest.TestCase):
    def test_계정_장치와_PK_네임스페이싱이_꺼진다(self):
        from store import pkns
        seed = base64.b64encode(bytes(32)).decode()
        with mock.patch.dict(os.environ, {"VINC_MCP_ED25519_SEED": seed, "VINC_PK_NAMESPACED": "on"}):
            os.environ.pop("VINC_SELF_HOSTED", None)
            self.assertTrue(pkns.enabled())
            on_before = entitlement.enabled()
            with mock.patch.dict(os.environ, ON):
                self.assertFalse(pkns.enabled())
                self.assertFalse(entitlement.enabled())
        self.assertTrue(on_before)

    def test_허브는_토큰과_소유자만으로_조립된다(self):
        from api.cli import _serve_http_kwargs
        args = argparse.Namespace(auth_token=None, oauth=False, issuer=None, host="127.0.0.1",
                                  port=8790)
        env = {**ON, "VINC_MCP_TOKEN": "S", "VINC_QUOTA": "on",
               "VINC_ACCOUNT_PUBLIC_KEY": "x", "VINC_CONNECT_URL": "https://vincs.io/connect"}
        with mock.patch.dict(os.environ, env):
            kw = _serve_http_kwargs(args)
        self.assertEqual(kw["static_token"], "S")
        for key in ("verify_key", "verify_member", "verify_community", "community_write",
                    "community_read", "quota_config", "mcp_seed", "account_public", "connect_url"):
            self.assertIsNone(kw[key], key)

    def test_모양이_틀린_소유자는_기동을_멈춘다(self):
        for bad in ("team:x", "user:a b", "community:x", ""):
            with mock.patch.dict(os.environ, {**ON, "VINC_OWNER": bad}):
                if bad == "":
                    self.assertEqual(selfhost.owner(), selfhost.DEFAULT_OWNER)
                    continue
                with self.assertRaises(ValueError):
                    selfhost.check()


class LinkTests(unittest.TestCase):
    def test_바깥_주소가_있으면_그것을_싣는다(self):
        with mock.patch.dict(os.environ, {**ON, "VINC_PUBLIC_URL": "https://vinc.example.com/"}):
            os.environ.pop("VINC_CITE_ORIGIN", None)
            self.assertEqual(openapi_v1.generate()["servers"], [{"url": "https://vinc.example.com"}])
            self.assertIn("https://vinc.example.com/mcp", roles._mcp_entries())
            self.assertNotIn("vincs.io", roles._mcp_entries())
            self.assertTrue(tools._cite_url("node", "concept:x").startswith(
                "https://vinc.example.com/app/?space=personal"))

    def test_바깥_주소가_없으면_빼거나_자리표시다(self):
        with mock.patch.dict(os.environ, ON):
            os.environ.pop("VINC_PUBLIC_URL", None)
            os.environ.pop("VINC_CITE_ORIGIN", None)
            self.assertEqual(openapi_v1.generate()["servers"], [])
            self.assertIn("https://<your-hub>/mcp", roles._mcp_entries())
            self.assertEqual(tools._cite_url("node", "concept:x"), "")

    def test_꺼져_있으면_종전_주소다(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("VINC_SELF_HOSTED", None)
            self.assertEqual(openapi_v1.generate()["servers"], [{"url": "https://mcp.vincs.io"}])
            self.assertIn(roles.MCP_URL, roles._mcp_entries())


if __name__ == "__main__":
    unittest.main()
