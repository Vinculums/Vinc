# -*- coding: utf-8 -*-
"""셀프호스트 허브의 웹 번들(`/app/*`, `vinc_mcp/selfhost_static.py`).

잠그는 것: 플래그가 꺼지면 404 그대로다, `/` 는 `/app/` 으로 간다, 파일이 있으면 그 파일을
맞는 형식·캐시로 준다, 확장자 없는 경로는 index.html 이다, 없는 자산은 404 다, 번들 밖으로
못 나간다, 번들이 없으면 503 으로 그렇다고 말한다, 배치 워커에만 WebAssembly 를 연다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import http_server as H  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPEN = urllib.request.build_opener(_NoRedirect)


class StaticTests(unittest.TestCase):
    flag = "on"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "assets").mkdir()
        (self.root / "index.html").write_text("<!doctype html><title>Vinc</title>", encoding="utf-8")
        (self.root / "assets" / "main-abc123.js").write_text("console.log(1)", encoding="utf-8")
        (self.root / "sw.js").write_text("//sw", encoding="utf-8")
        (self.root.parent / "secret.txt").write_text("no", encoding="utf-8")
        env = {"VINC_WEB_DIR": str(self.root), "VINC_SELF_HOSTED": self.flag, "VINC_OWNER": "tenant:local"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.httpd = H.make_server(H.MCPHTTPApp(_Bridge(), static_token="S"), "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        try:
            with _OPEN.open(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


class _Bridge:
    owner = "tenant:local"

    def request(self, *a, **k):
        return {}


class FlagOn(StaticTests):
    def test_루트는_app_으로(self):
        for path in ("/", "/app"):
            code, h, _ = self.get(path)
            self.assertEqual((code, h.get("Location")), (308, "/app/"), path)

    def test_index_는_no_store_와_CSP(self):
        code, h, body = self.get("/app/")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>Vinc</title>", body)
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertTrue(h["Content-Type"].startswith("text/html"))
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertNotIn("wasm-unsafe-eval", h["Content-Security-Policy"])
        self.assertNotIn("vincs.io", h["Content-Security-Policy"])

    def test_해시_자산은_오래_캐시한다(self):
        code, h, body = self.get("/app/assets/main-abc123.js")
        self.assertEqual((code, body), (200, b"console.log(1)"))
        self.assertIn("immutable", h["Cache-Control"])
        self.assertTrue(h["Content-Type"].startswith("text/javascript"))

    def test_서비스_워커는_no_cache(self):
        code, h, _ = self.get("/app/sw.js")
        self.assertEqual((code, h["Cache-Control"]), (200, "no-cache"))

    def test_어느_자산에도_wasm_허용이_없다(self):
        for path in ("/app/", "/app/assets/main-abc123.js", "/app/sw.js"):
            _, h, _ = self.get(path)
            self.assertNotIn("wasm-unsafe-eval", h["Content-Security-Policy"], path)

    def test_경로형_딥링크는_index(self):
        code, _, body = self.get("/app/approvals/run-1")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>Vinc</title>", body)

    def test_없는_자산은_404(self):
        self.assertEqual(self.get("/app/assets/missing-1.js")[0], 404)

    def test_번들_밖으로_못_나간다(self):
        for path in ("/app/../secret.txt", "/app/%2e%2e/secret.txt", "/app/..%2fsecret.txt",
                     "/app/assets/..%2f..%2fsecret.txt", "/app/%00"):
            code, _, body = self.get(path)
            self.assertNotEqual(body, b"no", path)
            self.assertIn(code, (200, 404), path)   # 200 이면 index.html 이다

    def test_번들이_없으면_503(self):
        (self.root / "index.html").unlink()
        self.assertEqual(self.get("/app/")[0], 503)


class FlagOff(StaticTests):
    flag = "off"

    def test_꺼져_있으면_기존_404(self):
        for path in ("/app/", "/app/assets/main-abc123.js"):
            self.assertEqual(self.get(path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
