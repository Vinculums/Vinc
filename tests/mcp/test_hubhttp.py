# -*- coding: utf-8 -*-
"""허브 HTTP 한 자리(`vinc_mcp.hubhttp`) — 성공·비 2xx 본문 통과·시간 초과·리다이렉트 거절.

가짜 허브는 루프백 `http.server` 다. 리다이렉트 시험은 302 를 받은 뒤 **대상 주소에
아무 요청도 닿지 않았는지**(그래서 Authorization 이 새지 않았는지)를 본다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vinc_mcp import hubhttp


class _Hub(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):  # 테스트 출력에 접근 로그를 싣지 않는다
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        type(self).seen.append({"method": self.command, "path": self.path,
                                "auth": self.headers.get("Authorization"),
                                "accept": self.headers.get("Accept"),
                                "ctype": self.headers.get("Content-Type"), "body": raw})
        if self.path == "/ok":
            self._send(200, json.dumps({"echo": json.loads(raw or b"null"), "k": "한글"},
                                       ensure_ascii=False).encode("utf-8"))
        elif self.path == "/refused":
            self._send(403, b'{"error":"forbidden"}', extra={"X-Request-Id": "r1"})
        elif self.path == "/slow":
            time.sleep(1.5)
            self._send(200, b"{}")
        elif self.path == "/redirect":
            self._send(302, b"", extra={"Location": f"http://127.0.0.1:{self.server.server_port}/target"})
        elif self.path == "/target":
            self._send(200, b'{"reached":true}')
        else:
            self._send(404, b"")

    do_GET = do_POST = _handle


@pytest.fixture()
def hub():
    _Hub.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def test_post_json_sends_bearer_and_json_and_decodes_utf8(hub):
    out = hubhttp.post_json(hub + "/ok", token="vinc_sk_test", payload={"q": "가"})
    assert out == {"echo": {"q": "가"}, "k": "한글"}
    got = _Hub.seen[-1]
    assert got["method"] == "POST" and got["auth"] == "Bearer vinc_sk_test"
    assert got["accept"] == "application/json" and got["ctype"] == "application/json"


def test_no_token_sends_no_authorization_header(hub):
    hubhttp.post_json(hub + "/ok", token=None, payload={}, headers={"X-Vinc-Assertion": "a"})
    assert _Hub.seen[-1]["auth"] is None


def test_get_json_has_no_body(hub):
    assert hubhttp.get_json(hub + "/target", token="t") == {"reached": True}
    assert _Hub.seen[-1]["method"] == "GET" and _Hub.seen[-1]["body"] == b""


def test_non_2xx_passes_status_body_and_headers_through(hub):
    with pytest.raises(hubhttp.HubHTTPError) as caught:
        hubhttp.post_json(hub + "/refused", token="t", payload={})
    e = caught.value
    assert e.status == 403 and json.loads(e.body) == {"error": "forbidden"}
    assert e.headers.get("X-Request-Id") == "r1" and e.url.endswith("/refused")
    # 기존 `except URLError` 호출부가 그대로 받는다.
    assert isinstance(e, urllib.error.URLError)


def test_timeout_is_a_transport_failure_without_status(hub):
    with pytest.raises(hubhttp.HubHTTPError) as caught:
        hubhttp.post_json(hub + "/slow", token="t", payload={}, timeout=0.3)
    e = caught.value
    assert e.status is None and isinstance(e.cause, (TimeoutError, OSError, urllib.error.URLError))


def test_redirect_is_refused_and_the_key_never_reaches_the_target(hub):
    with pytest.raises(hubhttp.HubHTTPError) as caught:
        hubhttp.post_json(hub + "/redirect", token="vinc_sk_secret", payload={"write": 1})
    assert caught.value.status == 302
    assert [s["path"] for s in _Hub.seen] == ["/redirect"]
    assert not any(s["path"] == "/target" for s in _Hub.seen)


def test_raw_urlopen_seam_also_refuses_redirects(hub):
    import urllib.request
    req = urllib.request.Request(hub + "/redirect", headers={"Authorization": "Bearer k"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        hubhttp.urlopen(req, timeout=5)
    assert caught.value.code == 302
    assert [s["path"] for s in _Hub.seen] == ["/redirect"]


def test_non_json_success_body_is_a_value_error_not_a_transport_error(hub, monkeypatch):
    class _Resp:
        status, headers = 200, {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>"
    monkeypatch.setattr(hubhttp, "urlopen", lambda req, timeout: _Resp())
    with pytest.raises(ValueError):
        hubhttp.post_json(hub + "/ok", token="t", payload={})
