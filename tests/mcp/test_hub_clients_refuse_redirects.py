# -*- coding: utf-8 -*-
"""좌석 확인과 CIMD 가져오기가 리다이렉트를 따르지 않는다.

가짜 서버 둘(스레드의 http.server)을 띄운다. 첫 서버는 302 로 둘째 서버를 가리키고, 둘째 서버는
받은 요청을 적는다. 둘째 서버에 아무것도 안 닿아야 한다.

- 좌석 확인(`selfhost_seats._post`): 인스턴스 키(Bearer)가 둘째 서버로 가지 않고, 호출부는
  계정에 닿지 않았을 때와 같은 결과(`unreachable`)를 받는다.
- CIMD(`oauth.fetch_cimd`): 리다이렉트를 따르지 않고, 공인이 아닌 주소로는 붙지 않으며,
  `/authorize` 에 돌려주는 오류는 종류마다 고정된 문장이다(urllib·소켓의 글이 안 섞인다).
근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import oauth  # noqa: E402
from vinc_mcp import selfhost_seats as S  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256  # noqa: E402
from vinc_mcp.store import Store  # noqa: E402

KEY = "vsh_" + "k" * 43
INSTANCE = "0198c0de-aaaa-7bbb-8ccc-ddddeeeeffff"
CIMD_URL = "https://cimd.example/oauth/client.json"
CALLBACK = "https://cimd.example/callback"
PUBLIC_IP = "93.184.216.34"
UNREACHABLE = "client metadata document is not reachable"
NOT_ALLOWED = "client metadata URL is not allowed"
INVALID = "client metadata document is not valid"


class _Handler(BaseHTTPRequestHandler):
    """서버 객체의 `reply(handler)` 가 (상태, 헤더, 본문)을 돌려준다. 받은 요청은 `seen` 에 적는다."""

    def log_message(self, *a):
        pass

    def _serve(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))
        code, headers, body = self.server.reply(self)
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _serve


def _server(reply):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen, srv.reply = [], reply
    srv.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _json(doc):
    return 200, {"Content-Type": "application/json"}, json.dumps(doc).encode()


def _redirect_to(url):
    return lambda h: (302, {"Location": url}, b"")


@pytest.fixture
def servers():
    made = []

    def make(reply):
        srv = _server(reply)
        made.append(srv)
        return srv

    yield make
    for srv in made:
        srv.shutdown()
        srv.server_close()


# ── 좌석 확인 ───────────────────────────────────────────────────────────────

def test_seat_check_does_not_carry_the_instance_key_through_a_redirect(servers, tmp_path, monkeypatch):
    second = servers(lambda h: _json({"token": ""}))
    first = servers(_redirect_to(f"http://127.0.0.1:{second.port}/api/selfhost/check"))
    monkeypatch.setenv("VINC_SELF_HOSTED", "on")
    monkeypatch.setenv("VINC_SELFHOST_STATE", str(tmp_path))
    monkeypatch.setenv(S.ACCOUNT_ENV, f"http://127.0.0.1:{first.port}")
    S._write(S.INSTANCE_FILE, {"instance_id": INSTANCE, "instance_key": KEY})

    outcome = S.check_once(now=1_800_000_000.0)

    assert [p for _, p, _ in first.seen] == ["/api/selfhost/check"]
    assert first.seen[0][2] == f"Bearer {KEY}"
    assert second.seen == []
    # 계정에 닿지 않았을 때와 같은 결과다(새 모양을 만들지 않는다).
    assert outcome == "unreachable"
    assert S.entitlement(now=1_800_000_000.0)["last_error"] == "unreachable: HubHTTPError"


# ── CIMD: 전송 대역 ──────────────────────────────────────────────────────────

class _HttpsToLocal(urllib.request.HTTPHandler):
    """https 요청을 평문으로 첫 서버에 보낸다. 기본 리다이렉트 처리기는 opener 에 그대로 남는다."""

    handler_order = 400     # 기본 HTTPSHandler 보다 먼저 https 를 받는다

    def __init__(self, port):
        super().__init__()
        self.port = port

    def https_open(self, req):
        return self.do_open(lambda host, **kw: http.client.HTTPConnection("127.0.0.1", self.port, **kw),
                            req)


def _route_cimd_to(monkeypatch, port, *, resolved=(PUBLIC_IP,)):
    """CIMD 의 https 주소를 첫 서버(평문)로 보낸다. 어느 전송을 쓰든 그리로 간다:
    확인한 주소로 붙는 전송이면 이름 풀기와 연결을 바꿔 끼우고, urllib 의 기본 urlopen 으로
    되돌아가더라도 같은 서버로 가서 이 시험이 그대로 잡는다."""
    connected: list[str] = []

    def resolve(host, port_, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_)) for ip in resolved]

    def connect(ip, port_, host, timeout):
        connected.append(ip)
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    monkeypatch.setattr(oauth, "_cimd_resolve", resolve, raising=False)
    monkeypatch.setattr(oauth, "_cimd_connect", connect, raising=False)
    opener = urllib.request.build_opener(_HttpsToLocal(port))
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: opener.open(req, timeout=timeout))
    return connected


def _doc(**over):
    doc = {"client_id": CIMD_URL, "client_name": "Example", "redirect_uris": [CALLBACK]}
    doc.update(over)
    return doc


# ── CIMD: 리다이렉트 ─────────────────────────────────────────────────────────

def test_cimd_fetch_does_not_follow_a_redirect(servers, monkeypatch):
    second = servers(lambda h: _json(_doc()))
    first = servers(_redirect_to(f"http://127.0.0.1:{second.port}/oauth/client.json"))
    _route_cimd_to(monkeypatch, first.port)

    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(CIMD_URL)

    assert [p for _, p, _ in first.seen] == ["/oauth/client.json"]
    assert second.seen == []
    assert str(exc.value) == NOT_ALLOWED


# ── CIMD: 공인이 아닌 주소 ────────────────────────────────────────────────────

class _NoConnect:
    def __init__(self):
        self.calls = []

    def __call__(self, ip, port, host, timeout):
        self.calls.append(ip)
        raise AssertionError("공인이 아닌 주소로 붙으려 했다")


@pytest.mark.parametrize("url", ["https://127.0.0.1/client.json",
                                 "https://10.1.2.3/client.json",
                                 "https://[::1]/client.json",
                                 "https://169.254.169.254/client.json"])
def test_cimd_fetch_refuses_a_literal_non_public_address(url, monkeypatch):
    never = _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_connect", never)
    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(url)
    assert str(exc.value) == NOT_ALLOWED
    assert never.calls == []


@pytest.mark.parametrize("resolved", [("10.0.0.5",), ("192.168.1.10",), ("127.0.0.1",),
                                      (PUBLIC_IP, "10.0.0.5")])
def test_cimd_fetch_refuses_a_name_that_resolves_to_a_non_public_address(resolved, monkeypatch):
    """이름이 공인 주소와 사설 주소로 함께 풀리면 거절한다(하나라도 공인이 아니면 거절)."""
    never = _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_resolve",
                        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                       (ip, port)) for ip in resolved])
    monkeypatch.setattr(oauth, "_cimd_connect", never)
    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(CIMD_URL)
    assert str(exc.value) == NOT_ALLOWED
    assert never.calls == []


def test_cimd_fetch_reads_a_public_address_and_connects_to_the_checked_one(servers, monkeypatch):
    first = servers(lambda h: _json(_doc()))
    connected = _route_cimd_to(monkeypatch, first.port)

    assert oauth.fetch_cimd(CIMD_URL) == _doc()
    # 이름을 다시 풀지 않고 확인한 주소로 붙는다.
    assert connected == [PUBLIC_IP]
    method, path, _ = first.seen[0]
    assert (method, path) == ("GET", "/oauth/client.json")


# ── CIMD: /authorize 에 돌려주는 오류 ─────────────────────────────────────────

class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": False}


@pytest.fixture
def authorize():
    gate = OAuthGate("https://mcp.example", "adminkey", store=Store(":memory:"))
    httpd = make_server(MCPHTTPApp(_StubBridge(), oauth=gate), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def ask():
        params = {"client_id": CIMD_URL, "redirect_uri": CALLBACK, "response_type": "code",
                  "code_challenge": _s256("v" * 48), "code_challenge_method": "S256",
                  "state": "st", "key": "adminkey"}
        try:
            urllib.request.build_opener().open(base + "/authorize?" + urllib.parse.urlencode(params),
                                               timeout=10)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))
        raise AssertionError("authorize did not fail")

    yield ask
    httpd.shutdown()
    httpd.server_close()


_RAW = ("Errno", "errno", "urlopen", "gaierror", "Connection refused", "Name or service",
        "HTTP Error", "Traceback", "socket")


def _clean(text):
    return not any(r in text for r in _RAW)


def test_authorize_says_not_reachable_without_socket_text(authorize, monkeypatch):
    def resolve(*a, **kw):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(oauth, "_cimd_resolve", resolve)
    code, body = authorize()
    assert (code, body["error"]) == (400, UNREACHABLE)
    assert _clean(body["error"])


def test_authorize_says_not_reachable_when_the_connection_is_refused(authorize, monkeypatch):
    def connect(ip, port, host, timeout):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(oauth, "_cimd_resolve",
                        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                       (PUBLIC_IP, port))])
    monkeypatch.setattr(oauth, "_cimd_connect", connect)
    code, body = authorize()
    assert (code, body["error"]) == (400, UNREACHABLE)
    assert _clean(body["error"])


def test_authorize_says_not_reachable_on_an_error_status(authorize, servers, monkeypatch):
    first = servers(lambda h: (503, {"Content-Type": "text/plain"}, b"upstream exploded at 0xdead"))
    _route_cimd_to(monkeypatch, first.port)
    code, body = authorize()
    assert (code, body["error"]) == (400, UNREACHABLE)
    assert "0xdead" not in body["error"]


def test_authorize_says_not_allowed_for_a_private_address(authorize, monkeypatch):
    monkeypatch.setattr(oauth, "_cimd_resolve",
                        lambda host, port, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                       ("10.0.0.5", port))])
    monkeypatch.setattr(oauth, "_cimd_connect", _NoConnect())
    code, body = authorize()
    assert (code, body["error"]) == (400, NOT_ALLOWED)


def test_authorize_says_not_valid_for_a_document_that_is_not_json(authorize, servers, monkeypatch):
    first = servers(lambda h: (200, {"Content-Type": "text/html"}, b"<html>hello</html>"))
    _route_cimd_to(monkeypatch, first.port)
    code, body = authorize()
    assert code == 400
    assert body["error"].startswith(INVALID)
    assert "<html>" not in body["error"] and _clean(body["error"])
