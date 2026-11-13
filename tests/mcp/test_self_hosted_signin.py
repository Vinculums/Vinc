# -*- coding: utf-8 -*-
"""셀프호스트 허브의 로그인과 같은 출처 라우트(`vinc_mcp/selfhost_web.py`).

계획 `vinc.plan.self-hosted-hub-web-2026-11-11` S2. 실물 HTTP 로 잰다. 잠그는 것:
  로그인 성공·실패·실패 상한, 허브를 다시 띄워도 세션이 남는다, 로그아웃, CSRF 없음·틀림과
  다른 Origin 은 403, 본문·쿼리의 owner 는 무시된다, 표 밖의 op 는 404, localhost 가 아닌 평문은
  거절, localhost 쿠키에는 `__Host-` 가 없다, HTTPS(믿는 프록시 뒤)는 `__Host-` 와 Secure,
  플래그가 꺼져 있으면 404, 해시 명령 왕복.
"""
from __future__ import annotations

import json
import urllib.parse
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import http_server as H  # noqa: E402
from vinc_mcp import selfhost_web as W  # noqa: E402

PINNED = "user:me"
# 테스트가 느려지지 않게 비용을 낮춘 해시. 검증은 해시에 적힌 비용을 그대로 쓴다.
HASH = W.hash_password("correct horse", n=2 ** 10)


class _Bridge:
    def __init__(self):
        self.owner = "tenant:local"
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"ok": True, "path": path}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _Hub(unittest.TestCase):
    flag = "on"
    extra_env: dict = {}

    def setUp(self):
        self.state = tempfile.mkdtemp()
        env = {"VINC_SELF_HOSTED": self.flag, "VINC_OWNER": PINNED, "VINC_SELFHOST_STATE": self.state,
               W.HASH_ENV: HASH, **self.extra_env}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        if "VINC_TRUST_PROXY" not in self.extra_env:
            os.environ.pop("VINC_TRUST_PROXY", None)
        self.bridge = _Bridge()
        self.start()

    def start(self):
        self.httpd = H.make_server(H.MCPHTTPApp(self.bridge, static_token="S"), "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def req(self, method, path, *, body=None, headers=None, host=None, form=False):
        h = {"Host": host or f"localhost:{self.port}"}
        data = None
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                h["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h,
                                   method=method)
        try:
            resp = _OPENER.open(r, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        raw = resp.read().decode() or "{}"
        try:
            out = json.loads(raw)
        except ValueError:
            out = {"html": raw}
        return resp.status if hasattr(resp, "status") else resp.code, out, resp.headers

    def origin(self, host=None):
        return {"Origin": f"http://{host or f'localhost:{self.port}'}"}

    def login(self, password="correct horse", **kw):
        return self.req("POST", "/login", body={"password": password},
                        headers={**self.origin(), "Accept": "application/json"}, **kw)

    @staticmethod
    def jar(headers) -> dict:
        out = {}
        for c in headers.get_all("Set-Cookie") or []:
            name, rest = c.split("=", 1)
            out[name] = (rest.split(";", 1)[0], c)
        return out

    def signed_in(self):
        code, _, hdr = self.login()
        self.assertEqual(code, 200)
        j = self.jar(hdr)
        cookie = "; ".join(f"{k}={v[0]}" for k, v in j.items())
        return cookie, j["vinc-csrf"][0]


class FlagOffTests(_Hub):
    flag = "off"

    def test_꺼져_있으면_404(self):
        for method, path in (("GET", "/login"), ("POST", "/login"), ("GET", "/api/me"),
                             ("GET", "/api/graph/view")):
            code, _, _ = self.req(method, path, body={} if method == "POST" else None)
            self.assertEqual(code, 404, path)


class SignInTests(_Hub):
    def test_로그인_화면은_스크립트가_없다(self):
        code, out, hdr = self.req("GET", "/login")
        self.assertEqual(code, 200)
        self.assertIn('name="password"', out["html"])
        self.assertNotIn("<script", out["html"])
        self.assertIn("default-src 'none'", hdr["Content-Security-Policy"])

    def test_맞는_비밀번호면_localhost_쿠키를_준다(self):
        code, _, hdr = self.login()
        self.assertEqual(code, 200)
        j = self.jar(hdr)
        self.assertEqual(set(j), {"vinc-session", "vinc-csrf"})
        self.assertIn("HttpOnly", j["vinc-session"][1])
        self.assertNotIn("HttpOnly", j["vinc-csrf"][1])
        self.assertNotIn("Secure", j["vinc-session"][1])
        self.assertIn("SameSite=Lax", j["vinc-session"][1])
        self.assertEqual(len(j["vinc-session"][0]), 43)        # 32바이트 base64url

    def test_폼으로_로그인하면_앱으로_보낸다(self):
        code, _, hdr = self.req("POST", "/login", body={"password": "correct horse"},
                                headers=self.origin(), form=True)
        self.assertEqual((code, hdr["Location"]), (303, "/app/"))
        code, _, hdr = self.req("POST", "/login", body={"password": "nope"},
                                headers=self.origin(), form=True)
        self.assertEqual((code, hdr["Location"]), (303, "/login?error=1"))

    def test_틀린_비밀번호와_실패_상한(self):
        for _ in range(W.FAIL_PER_ADDR):
            code, out, _ = self.login("nope")
            self.assertEqual((code, out["error"]), (401, "wrong_password"))
        code, out, hdr = self.login()                   # 맞는 비밀번호도 창이 지날 때까지 막힌다
        self.assertEqual((code, out["error"]), (429, "too_many_attempts"))
        self.assertGreater(int(hdr["Retry-After"]), 0)

    def test_비밀번호_검사_자리가_없으면_429(self):
        """scrypt 는 한 번에 32 MiB 다. 자리가 다 찼으면 검사하지 않고 429 로 돌려보낸다."""
        held = [W._KDF.acquire(timeout=1) for _ in range(W.KDF_SLOTS)]
        self.assertTrue(all(held))
        try:
            with mock.patch.object(W, "KDF_WAIT_S", 0.05):
                code, out, hdr = self.login()
        finally:
            for _ in held:
                W._KDF.release()
        self.assertEqual((code, out["error"]), (429, "too_many_attempts"))
        self.assertEqual(hdr["Retry-After"], "1")
        self.assertEqual(self.login()[0], 200)          # 자리가 나면 다시 된다

    def test_시도는_검사_전에_센다(self):
        """같은 주소의 묶음은 검사가 끝나기 전에도 상한에 걸린다. 맞는 시도는 되돌린다."""
        st = W._State.of(self.httpd.app)
        for _ in range(W.FAIL_PER_ADDR - 1):
            st.failures.fail("127.0.0.1")
        self.assertEqual(self.login()[0], 200)          # 맞는 시도는 되돌려서 자리가 그대로다
        self.assertEqual(self.login("nope")[0], 401)
        self.assertEqual(self.login()[0], 429)

    def test_다른_Origin_의_로그인은_403(self):
        code, out, _ = self.req("POST", "/login", body={"password": "correct horse"},
                                headers={"Origin": "http://evil.example"})
        self.assertEqual((code, out["error"]), (403, "origin_mismatch"))

    def test_localhost_가_아닌_평문은_로그인을_받지_않는다(self):
        host = "192.168.1.5:8790"
        code, out, hdr = self.req("POST", "/login", body={"password": "correct horse"},
                                  headers=self.origin(host), host=host)
        self.assertEqual((code, out["error"]), (403, "https_required"))
        self.assertIsNone(hdr.get("Set-Cookie"))
        code, out, _ = self.req("GET", "/api/me", host=host)
        self.assertEqual(code, 403)

    def test_비밀번호가_없으면_503(self):
        with mock.patch.dict(os.environ, {W.HASH_ENV: ""}):
            code, out, _ = self.login()
        self.assertEqual((code, out["error"]), (503, "no_password_set"))

    def test_비밀번호_파일도_읽는다(self):
        Path(self.state, W.HASH_FILE).write_text(HASH, encoding="utf-8")
        with mock.patch.dict(os.environ, {W.HASH_ENV: ""}):
            code, _, _ = self.login()
        self.assertEqual(code, 200)


class SessionTests(_Hub):
    def test_세션_없이는_401_이고_optional_은_null(self):
        code, out, hdr = self.req("GET", "/api/me")
        self.assertEqual((code, out["error"]), (401, "unauthorized"))
        self.assertEqual(hdr["Cache-Control"], "no-store")
        self.assertEqual(hdr["X-Content-Type-Options"], "nosniff")
        code, out, _ = self.req("GET", "/api/me?optional=1")
        self.assertEqual((code, out), (200, {"user": None}))

    def test_me_와_teams(self):
        cookie, _ = self.signed_in()
        code, out, _ = self.req("GET", "/api/me", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertTrue(out["self_hosted"] and out["user"]["self_hosted"])
        self.assertEqual(out["user"]["teams"], [])
        code, out, _ = self.req("GET", "/api/teams", headers={"Cookie": cookie})
        self.assertEqual((code, out), (200, []))
        code, out, _ = self.req("GET", "/api/graph/spaces", headers={"Cookie": cookie})
        self.assertEqual(out, {"teams": [], "communities": []})

    def test_허브를_다시_띄워도_세션이_남는다(self):
        cookie, _ = self.signed_in()
        self.httpd.shutdown()
        self.start()
        code, _, _ = self.req("GET", "/api/me", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        raw = Path(self.state, W.SESSIONS_FILE).read_text(encoding="utf-8")
        self.assertNotIn(cookie.split("vinc-session=")[1].split(";")[0], raw)   # 해시만 남는다

    def test_로그아웃하면_세션이_끝난다(self):
        cookie, csrf = self.signed_in()
        code, _, hdr = self.req("POST", "/logout", body={},
                                headers={"Cookie": cookie, "X-CSRF-Token": csrf, **self.origin()})
        self.assertEqual(code, 200)
        self.assertIn("Max-Age=0", hdr.get_all("Set-Cookie")[0])
        code, _, _ = self.req("GET", "/api/me", headers={"Cookie": cookie})
        self.assertEqual(code, 401)

    def test_만료된_세션은_401(self):
        cookie, _ = self.signed_in()
        path = Path(self.state, W.SESSIONS_FILE)
        data = json.loads(path.read_text(encoding="utf-8"))
        for rec in data.values():
            rec["seen"] -= W.IDLE_S + 1
        path.write_text(json.dumps(data), encoding="utf-8")
        code, _, _ = self.req("GET", "/api/me", headers={"Cookie": cookie})
        self.assertEqual(code, 401)


class GraphTests(_Hub):
    def test_읽기는_고정_소유자로_간다(self):
        cookie, _ = self.signed_in()
        code, _, _ = self.req("GET", "/api/graph/view?owner=user:other&focus=x",
                              headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        method, path, params, _ = self.bridge.calls[-1]
        self.assertEqual((method, path, params["owner"], params["focus"]),
                         ("GET", "/view", PINNED, "x"))

    def test_쓰기는_CSRF_와_Origin_이_있어야_하고_owner_는_무시된다(self):
        cookie, csrf = self.signed_in()
        body = {"owner": "user:other", "nodes": []}
        ok_headers = {"Cookie": cookie, "X-CSRF-Token": csrf, **self.origin()}
        for headers, want in (
                ({"Cookie": cookie, **self.origin()}, "csrf"),
                ({"Cookie": cookie, "X-CSRF-Token": "wrong", **self.origin()}, "csrf"),
                ({"Cookie": cookie, "X-CSRF-Token": csrf, "Origin": "http://evil.example"},
                 "origin_mismatch")):
            code, out, _ = self.req("POST", "/api/graph/ingest/fragment", body=body, headers=headers)
            self.assertEqual((code, out["error"]), (403, want))
        self.assertEqual(self.bridge.calls, [])
        code, _, _ = self.req("POST", "/api/graph/ingest/fragment", body=body, headers=ok_headers)
        self.assertEqual(code, 200)
        _, path, _, sent = self.bridge.calls[-1]
        self.assertEqual((path, sent["owner"]), ("/ingest/fragment", PINNED))

    def test_ASCII_밖의_CSRF_와_Origin_은_403(self):
        """`hmac.compare_digest` 는 ASCII 밖의 str 에서 예외를 낸다. 500 이 아니라 거절이어야 한다."""
        cookie, csrf = self.signed_in()
        code, out, _ = self.req("POST", "/api/graph/pin", body={},
                                headers={"Cookie": cookie, "X-CSRF-Token": "\u00e9" + csrf,
                                         **self.origin()})
        self.assertEqual((code, out["error"]), (403, "csrf"))
        code, out, _ = self.req("POST", "/api/graph/pin", body={},
                                headers={"Cookie": cookie, "X-CSRF-Token": csrf,
                                         "Origin": "http://\u00e9vil.example"})
        self.assertEqual((code, out["error"]), (403, "origin_mismatch"))
        code, out, _ = self.req("POST", "/login", body={"password": "x"},
                                headers={"Origin": "http://\u00e9"})
        self.assertEqual((code, out["error"]), (403, "origin_mismatch"))
        self.assertEqual(self.bridge.calls, [])

    def test_표_밖의_op_와_팀은_거절한다(self):
        cookie, csrf = self.signed_in()
        for path in ("/api/graph/sync/push", "/api/graph/community-agent/export",
                     "/api/graph/themes"):
            code, _, _ = self.req("POST", path, body={},
                                  headers={"Cookie": cookie, "X-CSRF-Token": csrf, **self.origin()})
            self.assertEqual(code, 404, path)
        code, out, _ = self.req("GET", "/api/graph/view?team=0198c0de-1111-7222-8333-444455556666",
                                headers={"Cookie": cookie})
        self.assertEqual((code, out["error"]), (404, "space_not_found"))
        self.assertEqual(self.bridge.calls, [])

    def test_잡_폴링(self):
        cookie, _ = self.signed_in()
        self.req("GET", "/api/graph/jobs/abc_123?owner=user:victim", headers={"Cookie": cookie})
        self.assertEqual(self.bridge.calls[-1][1:3], ("/jobs/abc_123", {"owner": PINNED}))
        code, _, _ = self.req("GET", "/api/graph/jobs/..%2Fx", headers={"Cookie": cookie})
        self.assertEqual(code, 400)


class HttpsTests(_Hub):
    extra_env = {"VINC_TRUST_PROXY": "on"}

    def test_믿는_프록시_뒤의_HTTPS_는_Host_접두와_Secure(self):
        host = "vinc.example.com"
        code, _, hdr = self.req("POST", "/login", body={"password": "correct horse"},
                                headers={"Origin": f"https://{host}", "X-Forwarded-Proto": "https",
                                         "Accept": "application/json"}, host=host)
        self.assertEqual(code, 200)
        j = self.jar(hdr)
        self.assertEqual(set(j), {"__Host-session", "__Host-csrf"})
        self.assertIn("Secure", j["__Host-session"][1])
        self.assertIn("Path=/", j["__Host-session"][1])

    def test_프록시를_안_믿으면_헤더로_https_인_척할_수_없다(self):
        with mock.patch.dict(os.environ, {"VINC_TRUST_PROXY": ""}):
            host = "vinc.example.com"
            code, out, _ = self.req("POST", "/login", body={"password": "correct horse"},
                                    headers={"Origin": f"https://{host}", "X-Forwarded-Proto": "https"},
                                    host=host)
        self.assertEqual((code, out["error"]), (403, "https_required"))


class HashTests(unittest.TestCase):
    def test_해시_왕복과_틀린_모양(self):
        h = W.hash_password("pw", n=2 ** 10)
        self.assertTrue(W.verify_password("pw", h))
        self.assertFalse(W.verify_password("pW", h))
        for bad in ("", "plain", "scrypt$1$2$3", "bcrypt$a$b$c$d$e"):
            self.assertFalse(W.verify_password("pw", bad))
        with self.assertRaises(ValueError):
            W.hash_password("")

    def test_해시_명령(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        p = subprocess.run([sys.executable, "-m", "api.cli", "selfhost", "hash-password"], cwd=ROOT,
                           env=env, input="s3cret\n", capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        line = p.stdout.strip()
        self.assertNotIn("s3cret", p.stdout + p.stderr)
        self.assertTrue(line.startswith(f"scrypt${W.SCRYPT_N}$"))
        self.assertTrue(W.verify_password("s3cret", line))


if __name__ == "__main__":
    unittest.main()
