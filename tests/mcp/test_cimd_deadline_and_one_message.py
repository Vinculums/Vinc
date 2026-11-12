# -*- coding: utf-8 -*-
"""CIMD 가져오기는 마감 하나 안에 끝나고, 밖에서 이름 풀이를 가를 수 있는 문장을 돌려주지 않는다.

- 마감: 상대가 헤더나 본문을 조금씩 흘리거나, 붙은 뒤 말이 없거나, TLS 악수를 흘리거나, 이름 풀기가
  늦어도 `fetch_cimd` 는 `_CIMD_DEADLINE` 에 작은 여유를 더한 시간 안에 "not reachable" 로 끝난다.
  시간은 단조 시계로 잰다. 가짜 서버는 몇 초 동안만 흘리고 스스로 끝낸다.
- 한 문장: 사설 주소로 풀리는 이름, 안 풀리는 이름, 연결이 거절되는 공인 주소는 `/authorize` 의
  응답이 바이트 단위로 같다(상태·헤더·본문). `Date` 헤더만 시험에서 고정한다(시각이지 사유가 아니다).
  응답 시간은 일부러 맞추지 않는다. 진짜 사유는 서버 로그의 `[oauth]` 줄에만 남고, 그 줄에는
  스킴·호스트·포트만 실린다.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import netguard, oauth  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402
from vinc_mcp.oauth import OAuthGate, _s256  # noqa: E402
from vinc_mcp.store import Store  # noqa: E402

CIMD_URL = "https://cimd.example/oauth/client.json"
CALLBACK = "https://cimd.example/callback"
PUBLIC_IP = "93.184.216.34"
UNREACHABLE = "client metadata document is not reachable"
NOT_ALLOWED = "client metadata URL is not allowed"

DEADLINE = 1.0     # 시험용 마감(초). 운영 값은 oauth._CIMD_DEADLINE 이다.
MARGIN = 1.0       # 마감 뒤 정리(소켓 끊기·스레드 깨우기)에 주는 여유
DRIP_FOR = 6.0     # 가짜 서버가 흘리는 시간. 마감이 없으면 가져오기가 이만큼 붙잡힌다.
DRIP_EVERY = 0.2   # 한 바이트 사이 간격. 읽기 한 번의 상한(_CIMD_TIMEOUT)보다 훨씬 짧다.


# ── 가짜 서버 ────────────────────────────────────────────────────────────────

def _read_request(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        buf += chunk
    return True


def _drip(conn, data: bytes):
    """`DRIP_FOR` 초 동안 한 바이트씩 보낸다. 상대가 끊으면 거기서 멈춘다."""
    end = time.monotonic() + DRIP_FOR
    for b in data:
        if time.monotonic() > end:
            return
        conn.sendall(bytes([b]))
        time.sleep(DRIP_EVERY)


def _drip_headers(conn):
    if _read_request(conn):
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        _drip(conn, b"X-Slow: " + b"a" * 1000 + b"\r\n")


def _drip_body(conn):
    if _read_request(conn):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     b"Content-Length: 4096\r\n\r\n{")
        _drip(conn, b" " * 4095)


def _silent(conn):
    if _read_request(conn):
        time.sleep(DRIP_FOR)


def _drip_tls_handshake(conn):
    conn.recv(4096)                                    # ClientHello
    conn.sendall(b"\x16\x03\x03\x40\x00")              # 16 KiB 짜리 handshake 레코드를 예고하고
    _drip(conn, b"\x00" * 16384)                       # 그 몸통을 한 바이트씩 흘린다


@pytest.fixture
def raw_server():
    made = []

    def make(behaviour):
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen(8)

        def serve():
            while True:
                try:
                    conn, _ = lsock.accept()
                except OSError:
                    return

                def one(c=conn):
                    try:
                        behaviour(c)
                    except OSError:
                        pass
                    finally:
                        c.close()

                threading.Thread(target=one, daemon=True).start()

        threading.Thread(target=serve, daemon=True).start()
        made.append(lsock)
        return lsock.getsockname()[1]

    yield make
    for lsock in made:
        lsock.close()


def _public(host, port, *a, **kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]


def _route_plain(monkeypatch, port):
    """확인한 공인 주소로 붙는 자리를 로컬 평문 서버로 돌린다."""
    monkeypatch.setattr(oauth, "_cimd_resolve", _public)
    monkeypatch.setattr(oauth, "_cimd_connect", lambda ip, p, host, timeout:
                        socket.create_connection(("127.0.0.1", port), timeout=timeout))


def _timed_fetch():
    start = time.monotonic()
    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(CIMD_URL)
    return time.monotonic() - start, str(exc.value)


# ── 마감 ─────────────────────────────────────────────────────────────────────

def test_the_deadline_is_above_the_per_read_timeout():
    assert oauth._CIMD_TIMEOUT < oauth._CIMD_DEADLINE


@pytest.mark.parametrize("behaviour", [_drip_headers, _drip_body, _silent],
                         ids=["drip-headers", "drip-body", "silent-after-accept"])
def test_a_slow_server_is_cut_at_the_deadline(behaviour, raw_server, monkeypatch):
    port = raw_server(behaviour)
    _route_plain(monkeypatch, port)
    monkeypatch.setattr(oauth, "_CIMD_DEADLINE", DEADLINE, raising=False)

    took, message = _timed_fetch()

    assert message == UNREACHABLE
    assert took < DEADLINE + MARGIN, f"{behaviour.__name__} held the fetch {took:.2f}s"


def test_a_dripped_tls_handshake_is_cut_at_the_deadline(raw_server, monkeypatch):
    port = raw_server(_drip_tls_handshake)
    monkeypatch.setattr(oauth, "_cimd_resolve", _public)
    monkeypatch.setattr(oauth, "_cimd_connect", lambda ip, p, host, timeout:
                        netguard.tls_connect("127.0.0.1", port, host, timeout))
    monkeypatch.setattr(oauth, "_CIMD_DEADLINE", DEADLINE, raising=False)

    took, message = _timed_fetch()

    assert message == UNREACHABLE
    assert took < DEADLINE + MARGIN, f"dripped TLS handshake held the fetch {took:.2f}s"


def test_a_slow_name_resolution_is_cut_at_the_deadline(monkeypatch):
    def slow(host, port, *a, **kw):
        time.sleep(DRIP_FOR)
        return _public(host, port)

    monkeypatch.setattr(oauth, "_cimd_resolve", slow)
    monkeypatch.setattr(oauth, "_cimd_connect", lambda *a: (_ for _ in ()).throw(
        ConnectionRefusedError(111, "Connection refused")))
    monkeypatch.setattr(oauth, "_CIMD_DEADLINE", DEADLINE, raising=False)

    took, message = _timed_fetch()

    assert message == UNREACHABLE
    assert took < DEADLINE + MARGIN, f"slow resolution held the fetch {took:.2f}s"


def test_a_body_cut_at_the_deadline_is_not_used_as_a_document(raw_server, monkeypatch):
    """길이를 안 밝히고 닫힘으로 끝나는 본문이 마감에 잘리면, 잘린 바이트가 JSON 으로 맞더라도
    문서로 쓰지 않는다."""
    def close_delimited(conn):
        if _read_request(conn):
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
                         b'{"client_id": "%s", "redirect_uris": ["%s"]}'
                         % (CIMD_URL.encode(), CALLBACK.encode()))
            _drip(conn, b" " * 1000)

    _route_plain(monkeypatch, raw_server(close_delimited))
    monkeypatch.setattr(oauth, "_CIMD_DEADLINE", DEADLINE, raising=False)

    took, message = _timed_fetch()

    assert message == UNREACHABLE
    assert took < DEADLINE + MARGIN


def test_a_quick_server_still_answers(raw_server, monkeypatch):
    body = b'{"client_id": "%s", "redirect_uris": ["%s"]}' % (CIMD_URL.encode(), CALLBACK.encode())

    def quick(conn):
        if _read_request(conn):
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))

    _route_plain(monkeypatch, raw_server(quick))
    monkeypatch.setattr(oauth, "_CIMD_DEADLINE", DEADLINE, raising=False)
    assert oauth.fetch_cimd(CIMD_URL)["redirect_uris"] == [CALLBACK]


# ── 한 문장 ──────────────────────────────────────────────────────────────────

class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")

    def request(self, method, path, params=None, body=None, timeout=60):
        return {"executed": False}


@pytest.fixture
def raw_authorize(monkeypatch):
    """`/authorize` 에 CIMD client_id 로 묻고 응답을 **바이트 그대로** 돌려준다."""
    gate = OAuthGate("https://mcp.example", "adminkey", store=Store(":memory:"))
    httpd = make_server(MCPHTTPApp(_StubBridge(), oauth=gate), "127.0.0.1", 0)
    # 시각은 사유가 아니다. 두 응답을 바이트로 대조하려고 Date 만 고정한다.
    monkeypatch.setattr(httpd.RequestHandlerClass, "date_time_string",
                        lambda self, timestamp=None: "Thu, 01 Jan 2026 00:00:00 GMT")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def ask(client_id=CIMD_URL):
        params = {"client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
                  "code_challenge": _s256("v" * 48), "code_challenge_method": "S256",
                  "state": "st", "key": "adminkey"}
        with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
            s.sendall(b"GET /authorize?%s HTTP/1.0\r\nHost: mcp.example\r\n\r\n"
                      % urllib.parse.urlencode(params).encode())
            out = b""
            while chunk := s.recv(65536):
                out += chunk
        return out

    yield ask
    httpd.shutdown()
    httpd.server_close()


def _resolves_private(host, port, *a, **kw):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


def _does_not_resolve(host, port, *a, **kw):
    raise socket.gaierror(-2, "Name or service not known")


def _never_connect(ip, port, host, timeout):
    raise AssertionError("공인이 아닌 주소로 붙으려 했다")


def _refused(ip, port, host, timeout):
    raise ConnectionRefusedError(111, "Connection refused")


def test_private_resolution_and_no_resolution_answer_byte_for_byte_the_same(raw_authorize,
                                                                           monkeypatch):
    monkeypatch.setattr(oauth, "_cimd_connect", _never_connect)
    monkeypatch.setattr(oauth, "_cimd_resolve", _resolves_private)
    private = raw_authorize()
    monkeypatch.setattr(oauth, "_cimd_resolve", _does_not_resolve)
    unresolved = raw_authorize()
    monkeypatch.setattr(oauth, "_cimd_resolve", _public)
    monkeypatch.setattr(oauth, "_cimd_connect", _refused)
    refused = raw_authorize()

    assert private.startswith(b"HTTP/1.0 400 ")
    assert UNREACHABLE.encode() in private
    assert private == unresolved == refused


def test_the_real_reason_goes_to_the_server_log_without_path_or_query(raw_authorize, monkeypatch,
                                                                      capsys):
    secret_url = "https://internal.example/oauth/client.json?token=s3cret"
    monkeypatch.setattr(oauth, "_cimd_connect", _never_connect)
    monkeypatch.setattr(oauth, "_cimd_resolve", _resolves_private)
    raw_authorize(secret_url)
    monkeypatch.setattr(oauth, "_cimd_resolve", _does_not_resolve)
    raw_authorize(secret_url)
    monkeypatch.setattr(oauth, "_cimd_resolve", _public)
    monkeypatch.setattr(oauth, "_cimd_connect", _refused)
    raw_authorize(secret_url)

    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("[oauth] client metadata fetch refused")]
    assert len(lines) == 3
    assert "name resolves to a non-public address" in lines[0]
    assert "name did not resolve" in lines[1]
    assert "connection or TLS failed" in lines[2] and "HTTPS_PROXY" in lines[2]
    for ln in lines:
        assert "scheme=https host='internal.example' port=443" in ln
        assert "s3cret" not in ln and "client.json" not in ln


@pytest.mark.parametrize("url", ["https://10.0.0.5/client.json", "https://[::1]/client.json",
                                 "https://cimd.example:notaport/client.json"])
def test_shapes_refused_before_any_lookup_still_say_not_allowed(url, monkeypatch):
    def no_lookup(*a, **kw):
        raise AssertionError("모양으로 막을 주소를 찾아봤다")

    monkeypatch.setattr(oauth, "_cimd_resolve", no_lookup)
    monkeypatch.setattr(oauth, "_cimd_connect", _never_connect)
    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(url)
    assert str(exc.value) == NOT_ALLOWED
