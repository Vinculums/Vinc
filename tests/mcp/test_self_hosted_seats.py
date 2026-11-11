# -*- coding: utf-8 -*-
"""셀프호스트 허브의 좌석(`vinc_mcp/selfhost_seats.py`·`selfhost_users.py`).

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S8. 가짜 계정(스레드의 http.server)과 가짜 시계로
잠그는 것:
  토큰 검증(좋은 답·다른 키·다른 대상·다른 허브·만료), 연락이 끊긴 29일째는 유예이고 31일째는
  무료, 계정이 연결을 끊으면 바로 무료, 서명이 틀린 답은 버리고 마지막 좋은 답을 지킨다,
  확인 스레드와 실패 뒤 물러서기, 연결 흐름(승인·거절), 자격보다 많은 사람은 더하지 못한다,
  좌석이 사람 수 아래로 줄어도 아무도 읽기에서 잠기지 않고 쓰기만 막힌다, S2 의 한 사람 로그인,
  플래그가 꺼져 있거나 연결이 없으면 밖으로 아무것도 안 묻는다.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "mcp"))

from vinc_mcp import edverify  # noqa: E402
from vinc_mcp import selfhost_seats as S  # noqa: E402
from vinc_mcp import selfhost_users as U  # noqa: E402
from vinc_mcp import selfhost_web as W  # noqa: E402

import test_self_hosted_signin as signin  # noqa: E402

SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))
PUB = base64.b64encode(edverify.secret_to_public(SEED)).decode()
INSTANCE = "0198c0de-aaaa-7bbb-8ccc-ddddeeeeffff"
KEY = "vsh_" + "k" * 43
DAY = 86400
T0 = 1_800_000_000.0


def token(*, seed=SEED, users=3, seats=2, sub=INSTANCE, aud="selfhost", iat=T0, exp=None):
    return edverify.sign_assertion(seed, {
        "typ": "selfhost-seats", "iss": "account", "aud": aud, "sub": sub, "owner": "user:p",
        "seats": seats, "users": users, "features": ["multi_user", "teams"] if seats else [],
        "iat": int(iat), "exp": int(exp if exp is not None else iat + 30 * DAY)})


class _Account(BaseHTTPRequestHandler):
    """가짜 vincs.io. 서버 객체의 `answer` 가 /check 의 (상태, 본문)이다."""

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        srv = self.server
        srv.seen.append((self.path, self.headers.get("Authorization"), body))
        if self.path == "/api/selfhost/check":
            code, out = srv.answer
        elif self.path == "/api/selfhost/link/start":
            code, out = 200, {"device_code": "d" * 43, "user_code": "BCDF-GHJK",
                              "verification_uri": "https://acct/account/#acc-selfhost",
                              "interval": 1, "expires_in": 30}
        elif self.path == "/api/selfhost/link/poll":
            code, out = 200, srv.polls.pop(0) if srv.polls else {"status": "expired"}
        else:
            code, out = 404, {}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _Base(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.acct = ThreadingHTTPServer(("127.0.0.1", 0), _Account)
        self.acct.seen, self.acct.polls = [], []
        self.acct.answer = (200, {"token": token()})
        threading.Thread(target=self.acct.serve_forever, daemon=True).start()
        self.addCleanup(self.acct.shutdown)
        env = {"VINC_SELF_HOSTED": "on", "VINC_OWNER": "user:me", "VINC_SELFHOST_STATE": self.state,
               S.PUBLIC_KEY_ENV: PUB,
               S.ACCOUNT_ENV: f"http://127.0.0.1:{self.acct.server_address[1]}"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop(W.HASH_ENV, None)

    def linked(self):
        S._write(S.INSTANCE_FILE, {"instance_id": INSTANCE, "instance_key": KEY})


class TokenTests(_Base):
    def test_좋은_답만_믿는다(self):
        self.assertEqual(S.verify(token(), INSTANCE, now=T0 + 1)["users"], 3)
        self.assertIsNone(S.verify(token(seed=OTHER_SEED), INSTANCE, now=T0 + 1))
        self.assertIsNone(S.verify(token(aud="hub"), INSTANCE, now=T0 + 1))
        self.assertIsNone(S.verify(token(sub="someone-else"), INSTANCE, now=T0 + 1))
        self.assertIsNone(S.verify(token(), INSTANCE, now=T0 + 31 * DAY))
        self.assertIsNone(S.verify(token() + "x", INSTANCE, now=T0 + 1))

    def test_공개키가_없으면_늘_무료다(self):
        os.environ.pop(S.PUBLIC_KEY_ENV)
        self.assertEqual(S.BUILTIN_PUBLIC_KEY, "")
        self.assertIsNone(S.verify(token(), INSTANCE, now=T0 + 1))


class EntitlementTests(_Base):
    def test_연결이_없으면_무료(self):
        e = S.entitlement(now=T0)
        self.assertEqual((e["state"], e["users_allowed"], e["linked"]), ("free", 1, False))

    def test_29일째는_유예_31일째는_무료(self):
        self.linked()
        self.assertEqual(S.check_once(now=T0), "ok")
        e = S.entitlement(now=T0 + 1)
        self.assertEqual((e["state"], e["users_allowed"], e["features"]), ("paid", 3, ["multi_user", "teams"]))
        e29 = S.entitlement(now=T0 + 29 * DAY)
        self.assertEqual((e29["state"], e29["users_allowed"]), ("grace", 3))
        e31 = S.entitlement(now=T0 + 31 * DAY)
        self.assertEqual((e31["state"], e31["users_allowed"], e31["features"]), ("free", 1, []))

    def test_연결을_끊으면_바로_무료(self):
        self.linked()
        S.check_once(now=T0)
        self.acct.answer = (401, {"error": {"code": "not_authenticated", "reason": "revoked"}})
        self.assertEqual(S.check_once(now=T0 + DAY), "revoked")
        e = S.entitlement(now=T0 + DAY)
        self.assertEqual((e["state"], e["users_allowed"], e["revoked"]), ("free", 1, True))

    def test_서명이_틀린_답은_버리고_마지막_좋은_답을_지킨다(self):
        self.linked()
        S.check_once(now=T0)
        self.acct.answer = (200, {"token": token(seed=OTHER_SEED, users=50, iat=T0 + DAY)})
        self.assertEqual(S.check_once(now=T0 + DAY), "bad_token")
        e = S.entitlement(now=T0 + DAY)
        self.assertEqual((e["state"], e["users_allowed"], e["last_error"]), ("paid", 3, "bad_token"))

    def test_계정에_닿지_않아도_답을_지킨다(self):
        self.linked()
        S.check_once(now=T0)
        os.environ[S.ACCOUNT_ENV] = "http://127.0.0.1:1"
        self.assertEqual(S.check_once(now=T0 + DAY), "unreachable")
        self.assertEqual(S.entitlement(now=T0 + DAY)["users_allowed"], 3)

    def test_인스턴스_키를_실어_보내고_파일은_0600(self):
        self.linked()
        S.check_once(now=T0)
        path, auth, _ = self.acct.seen[-1]
        self.assertEqual((path, auth), ("/api/selfhost/check", f"Bearer {KEY}"))
        for name in (S.INSTANCE_FILE, S.SEATS_FILE):
            self.assertEqual(os.stat(os.path.join(self.state, name)).st_mode & 0o777, 0o600)


class CheckerTests(_Base):
    def test_한_사람_허브는_아무_데도_묻지_않는다(self):
        self.assertIsNone(S.start_checker())
        os.environ["VINC_SELF_HOSTED"] = "off"
        self.linked()
        self.assertIsNone(S.start_checker())
        self.assertEqual(self.acct.seen, [])

    def test_하루에_한_번_묻고_실패하면_물러선다(self):
        self.linked()
        waits = []
        answers = iter([(200, {"token": token()}), (500, {}), (500, {})])

        def sleep(s):
            waits.append(s)
            try:
                self.acct.answer = next(answers)
            except StopIteration:
                ch.stop()

        self.acct.answer = next(answers)
        ch = S.Checker(clock=lambda: T0, sleep=sleep)
        ch.run()
        self.assertEqual(ch.outcomes[:3], ["ok", "rejected", "rejected"])
        self.assertTrue(S.CHECK_EVERY_S - S.JITTER_S <= waits[0] <= S.CHECK_EVERY_S + S.JITTER_S)
        self.assertEqual(waits[1:3], [S.RETRY_FIRST_S, S.RETRY_FIRST_S * 2])

    def test_끊긴_허브의_스레드는_멈춘다(self):
        self.linked()
        self.acct.answer = (401, {"error": {"reason": "revoked"}})
        ch = S.Checker(clock=lambda: T0, sleep=lambda s: self.fail("should not sleep"))
        ch.run()
        self.assertEqual(ch.outcomes, ["revoked"])


class LinkTests(_Base):
    def test_승인되면_저장하고_바로_한_번_묻는다(self):
        self.acct.polls = [{"status": "pending"}, {"status": "approved", "instance_id": INSTANCE,
                                                   "instance_key": KEY}]
        said = []
        self.assertEqual(S.link("home", say=said.append, sleep=lambda s: None), 0)
        self.assertEqual(S.instance()["instance_id"], INSTANCE)
        self.assertTrue(any("BCDF-GHJK" in line for line in said))
        self.assertFalse(any(KEY in line for line in said))   # 키는 출력하지 않는다
        self.assertEqual(self.acct.seen[-1][0], "/api/selfhost/check")
        self.assertEqual(S.unlink(say=said.append), 0)
        self.assertIsNone(S.instance())

    def test_거절되면_아무것도_남기지_않는다(self):
        self.acct.polls = [{"status": "denied"}]
        self.assertEqual(S.link(say=lambda *_: None, sleep=lambda s: None), 1)
        self.assertIsNone(S.instance())


class UserTests(_Base):
    def test_자격보다_많이_더하지_못한다(self):
        os.environ[W.HASH_ENV] = W.hash_password("pw0", n=2 ** 10)
        with self.assertRaises(U.UserError):
            U.add("ana", "pw1", allowed=1)                       # S2 의 한 사람이 이미 자리다
        self.assertEqual(U.add("ana", "pw1", allowed=3)["username"], "ana")
        self.assertEqual([r["username"] for r in U.users()], ["local", "ana"])
        U.add("bo", "pw2", allowed=3)
        with self.assertRaises(U.UserError):
            U.add("cy", "pw3", allowed=3)
        with self.assertRaises(U.UserError):
            U.add("ANA", "x", allowed=9)                          # 이름은 대소문자를 가리지 않는다
        self.assertEqual([u["can_write"] for u in U.listing(2)], [True, True, False])
        self.assertTrue(U.remove("bo"))
        self.assertFalse(U.remove("bo"))
        self.assertEqual(os.stat(U._path()).st_mode & 0o777, 0o600)


class SignInSeatTests(signin._Hub):
    """S2 의 실물 HTTP 하네스 위에서 사람 표와 좌석을 본다."""

    def setUp(self):
        super().setUp()
        os.environ[S.PUBLIC_KEY_ENV] = PUB
        S._write(S.INSTANCE_FILE, {"instance_id": INSTANCE, "instance_key": KEY})
        S._write(S.SEATS_FILE, {"token": token(iat=time.time()), "checked_at": time.time()})
        U.add("ana", "pw-ana", allowed=3)    # S2 의 비밀번호는 'local' 로 옮겨진다
        U.add("bo", "pw-bo", allowed=3)

    def as_user(self, name, password):
        code, out, hdr = self.req("POST", "/login", body={"username": name, "password": password},
                                  headers={**self.origin(), "Accept": "application/json"})
        self.assertEqual(code, 200, out)
        j = self.jar(hdr)
        return "; ".join(f"{k}={v[0]}" for k, v in j.items()), j["vinc-csrf"][0]

    def write(self, cookie, csrf):
        return self.req("POST", "/api/graph/pin", body={"id": "concept:x"},
                        headers={**self.origin(), "Cookie": cookie, "X-CSRF-Token": csrf})

    def test_이름으로_로그인하고_me_가_그_사람이다(self):
        cookie, _ = self.as_user("bo", "pw-bo")
        code, out, _ = self.req("GET", "/api/me", headers={"Cookie": cookie})
        self.assertEqual((code, out["user"]["username"], out["user"]["can_write"]), (200, "bo", True))
        code, out, _ = self.req("POST", "/login", body={"username": "nobody", "password": "pw-bo"},
                                headers={**self.origin(), "Accept": "application/json"})
        self.assertEqual(code, 401)

    def test_S2_의_비밀번호는_첫_사람으로_남는다(self):
        cookie, _ = self.as_user("local", "correct horse")
        self.assertEqual(self.req("GET", "/api/me", headers={"Cookie": cookie})[1]["user"]["username"],
                         "local")

    def test_좌석이_줄어도_읽기는_되고_쓰기만_막힌다(self):
        cookie, csrf = self.as_user("bo", "pw-bo")
        self.assertEqual(self.write(cookie, csrf)[0], 200)
        os.remove(os.path.join(self.state, S.SEATS_FILE))            # 30일이 지나 무료가 됐다
        code, out, _ = self.write(cookie, csrf)
        self.assertEqual((code, out["error"]), (403, "seat_read_only"))
        self.assertEqual(self.req("GET", "/api/graph/view", headers={"Cookie": cookie})[0], 200)
        cookie2, _ = self.as_user("bo", "pw-bo")                      # 다시 로그인도 된다
        self.assertFalse(self.req("GET", "/api/me", headers={"Cookie": cookie2})[1]["user"]["can_write"])
        first, first_csrf = self.as_user("local", "correct horse")    # 먼저 더한 사람은 쓴다
        self.assertEqual(self.write(first, first_csrf)[0], 200)

    def test_좌석_상태를_보인다(self):
        cookie, _ = self.as_user("ana", "pw-ana")
        code, out, _ = self.req("GET", "/api/selfhost/seats", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertEqual((out["state"], out["users_allowed"], out["users_used"], out["linked"]),
                         ("paid", 3, 3, True))
        self.assertNotIn("instance_key", json.dumps(out))

    def test_지워진_사람의_세션은_끝난다(self):
        cookie, _ = self.as_user("bo", "pw-bo")
        U.remove("bo")
        self.assertEqual(self.req("GET", "/api/me", headers={"Cookie": cookie})[0], 401)


if __name__ == "__main__":
    unittest.main()
