# -*- coding: utf-8 -*-
"""CIMD 가져오기(`oauth.fetch_cimd`)의 서버측 요청 위조(SSRF) 위협을 하나씩 닫아 둔 시험.

`client_id` 는 상대가 정하는 주소다. 그 주소로 서버가 직접 붙으므로 아래가 모두 막혀야 한다.

- 리다이렉트: 3xx 는 어떤 코드·어떤 Location 이든 따르지 않고, 두 번째 연결도 만들지 않는다.
- 공인이 아닌 주소: 이름이 사설·루프백·링크로컬·멀티캐스트·미지정·예약·문서용·CGNAT 주소로 풀리거나,
  그런 주소를 안에 실은 IPv6(매핑·NAT64·6to4·Teredo)로 풀리면 붙지 않는다. 숫자 줄임꼴(`127.1`)도 같다.
- 이름 바꿔치기(DNS 리바인딩): 이름은 한 번만 풀고, 붙는 곳은 그때 확인한 주소다.
- 본문 상한: 상한 바이트까지는 받고, 한 바이트라도 넘으면 문서로 쓰지 않는다. 길이를 알리는 방식
  (Content-Length·chunked·닫힘 구분)이 달라도 같다.
- TLS: 확인한 주소로 붙되 SNI 와 인증서 검사는 원래 이름으로 한다. 검증은 꺼지지 않는다.
- 주소 모양: https 가 아니거나 userinfo·조각이 붙은 주소는 이름을 찾아보기 전에 막힌다.

실패는 종류마다 고정된 문장이다(`/authorize` 가 그대로 400 본문에 싣는다). 마감은
`test_cimd_deadline_and_one_message.py`, 리다이렉트 하나와 `/authorize` 오류 모양은
`test_hub_clients_refuse_redirects.py` 가 본다.
근거: [[record:hub-http-clients-refuse-redirects-2026-10-16]]
"""
from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import netguard, oauth  # noqa: E402

CIMD_URL = "https://cimd.example/oauth/client.json"
CALLBACK = "https://cimd.example/callback"
PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_B = "151.101.1.69"
PUBLIC_IP6 = "2606:2800:220:1:248:1893:25c8:1946"
UNREACHABLE = "client metadata document is not reachable"
NOT_ALLOWED = "client metadata URL is not allowed"
INVALID = "client metadata document is not valid"


# ── 가짜 서버 ────────────────────────────────────────────────────────────────

def _read_head(conn):
    """요청 머리를 읽어 (요청 줄, 소문자 헤더 dict). 머리가 끝나기 전에 끊기면 None."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    line, *rest = buf.split(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")
    headers = {k.strip().lower(): v.strip() for k, v in (r.split(":", 1) for r in rest if ":" in r)}
    return line, headers


def _send(conn, status, headers, body=b""):
    head = "HTTP/1.1 %d X\r\n" % status
    head += "".join("%s: %s\r\n" % kv for kv in headers.items()) + "Connection: close\r\n\r\n"
    conn.sendall(head.encode("latin-1") + body)


class _Raw:
    """스레드로 도는 서버 하나. 받은 요청을 `seen`, TLS 면 클라이언트가 보낸 SNI 를 `sni` 에 적는다."""

    def __init__(self, behaviour, ctx=None):
        self.behaviour, self.ctx = behaviour, ctx
        self.seen: list[tuple[str, dict]] = []
        self.sni: list = []
        if ctx is not None:
            ctx.sni_callback = lambda sock, name, c: self.sni.append(name)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        try:
            conn.settimeout(10)
            if self.ctx is not None:
                conn = self.ctx.wrap_socket(conn, server_side=True)
            req = _read_head(conn)
            if req is None:
                return
            self.seen.append(req)
            self.behaviour(conn, req)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._sock.close()


@pytest.fixture
def serve():
    made: list[_Raw] = []

    def make(behaviour, ctx=None):
        srv = _Raw(behaviour, ctx)
        made.append(srv)
        return srv

    yield make
    for srv in made:
        srv.close()


def _doc(**over):
    doc = {"client_id": CIMD_URL, "client_name": "Example", "redirect_uris": [CALLBACK]}
    doc.update(over)
    return doc


def _answer_doc(doc=None):
    body = json.dumps(_doc() if doc is None else doc).encode()
    return lambda conn, req: _send(conn, 200, {"Content-Type": "application/json",
                                               "Content-Length": str(len(body))}, body)


def _addrinfo(ips, port):
    """`getaddrinfo` 가 주는 모양 그대로. IPv6 는 AF_INET6 과 네 칸짜리 소켓 주소다."""
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)) if ":" in ip
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


class _Resolver:
    """부를 때마다 `answers` 의 다음 줄을 준다(끝나면 마지막 줄을 되풀이). 부른 횟수를 센다."""

    def __init__(self, *answers):
        self.answers, self.calls = [tuple(a) for a in answers], 0

    def __call__(self, host, port, *a, **kw):
        ips = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return _addrinfo(ips, port)


class _NoConnect:
    def __init__(self):
        self.calls = []

    def __call__(self, ip, port, host, timeout):
        self.calls.append(ip)
        raise AssertionError("공인이 아닌 주소로 붙으려 했다")


def _route_plain(monkeypatch, port, resolver):
    """이름 풀기를 `resolver` 로, 확인한 주소로 붙는 자리를 로컬 평문 서버로 돌린다.
    붙으라고 받은 주소를 `connected` 에 적어 돌려준다."""
    connected: list[str] = []

    def connect(ip, port_, host, timeout):
        connected.append(ip)
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)
    monkeypatch.setattr(oauth, "_cimd_connect", connect)
    return connected


def _refused(url=CIMD_URL):
    with pytest.raises(ValueError) as exc:
        oauth.fetch_cimd(url)
    return str(exc.value)


# ── 리다이렉트 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_every_redirect_code_is_refused_and_the_target_is_never_contacted(code, serve, monkeypatch):
    second = serve(_answer_doc())
    first = serve(lambda conn, req: _send(
        conn, code, {"Location": f"http://127.0.0.1:{second.port}/oauth/client.json"}))
    connected = _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    assert _refused() == NOT_ALLOWED
    assert [line for line, _ in first.seen] == ["GET /oauth/client.json HTTP/1.1"]
    assert second.seen == []
    # 두 번째 연결을 시도하지도 않는다.
    assert connected == [PUBLIC_IP]


@pytest.mark.parametrize("location", ["https://169.254.169.254/latest/meta-data/",
                                      "http://127.0.0.1:8080/admin",
                                      "https://[::1]/",
                                      "/other/path",
                                      ""])
def test_a_redirect_to_an_internal_or_relative_location_opens_no_second_connection(
        location, serve, monkeypatch):
    first = serve(lambda conn, req: _send(conn, 302, {"Location": location}))
    connected = _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    assert _refused() == NOT_ALLOWED
    assert len(first.seen) == 1
    assert connected == [PUBLIC_IP]


# ── 공인이 아닌 주소 ──────────────────────────────────────────────────────────

NON_PUBLIC = [
    # IPv4 루프백·사설·링크로컬(클라우드 메타데이터 포함)·CGNAT
    "127.0.0.1", "127.255.255.254", "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.10",
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "100.127.255.255",
    # 미지정·멀티캐스트·예약·브로드캐스트
    "0.0.0.0", "0.1.2.3", "224.0.0.1", "239.255.255.255", "240.0.0.1", "255.255.255.255",
    # 벤치마크·IETF 프로토콜·문서용(TEST-NET). 문서용 대역은 공인 자리에 쓸 수 없다.
    "198.18.0.1", "192.0.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.1",
    # IPv6 루프백·미지정·링크로컬·고유로컬·사이트로컬·멀티캐스트·문서용
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "fec0::1", "ff02::1", "ff0e::1", "2001:db8::1",
    # IPv6 안에 실은 IPv4: 매핑·NAT64·6to4·Teredo(안의 주소가 루프백·사설·링크로컬)
    "::ffff:127.0.0.1", "::ffff:10.0.0.5", "::ffff:169.254.169.254", "::ffff:0:7f00:1",
    "64:ff9b::7f00:1", "64:ff9b::a00:1", "2002:7f00:1::1", "2002:a00:1::1",
    "2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001:0:7f00:1::1",
]


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_a_name_that_resolves_to_a_non_public_address_is_never_dialled(address, monkeypatch):
    resolver, never = _Resolver([address]), _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)
    monkeypatch.setattr(oauth, "_cimd_connect", never)

    assert _refused() == UNREACHABLE
    assert never.calls == []
    assert resolver.calls == 1


@pytest.mark.parametrize("answers", [(PUBLIC_IP, "169.254.169.254"), ("127.0.0.1", PUBLIC_IP),
                                     (PUBLIC_IP6, "::1"), (PUBLIC_IP, PUBLIC_IP6, "fe80::1")])
def test_one_non_public_answer_among_public_ones_refuses_the_whole_name(answers, monkeypatch):
    never = _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_resolve", _Resolver(answers))
    monkeypatch.setattr(oauth, "_cimd_connect", never)

    assert _refused() == UNREACHABLE
    assert never.calls == []


@pytest.mark.parametrize("host", ["2130706433", "0x7f.0.0.1", "0177.0.0.1", "127.1", "0"])
def test_numeric_shorthand_for_loopback_is_refused_through_the_real_resolver(host, monkeypatch):
    """`ipaddress` 가 주소로 읽지 않는 줄임꼴은 글자 검사를 지나고 이름 풀기에서 걸린다.
    시스템 리졸버가 풀든 못 풀든 결과는 같다: 붙지 않고, 풀이를 가를 수 없는 같은 문장이다."""
    never = _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_connect", never)

    assert _refused(f"https://{host}/client.json") == UNREACHABLE
    assert never.calls == []


@pytest.mark.parametrize("url", ["https://127.0.0.1/c.json", "https://127.1.2.3:8443/c.json",
                                 "https://[::1]/c.json", "https://[::ffff:7f00:1]/c.json",
                                 "https://[::ffff:127.0.0.1]/c.json", "https://[64:ff9b::7f00:1]/c.json",
                                 "https://[fe80::1%25eth0]/c.json", "https://169.254.169.254/c.json",
                                 "https://0.0.0.0/c.json", "https://[::]/c.json",
                                 "https://224.0.0.1/c.json"])
def test_a_literal_non_public_address_is_refused_before_any_lookup(url, monkeypatch):
    def no_lookup(*a, **kw):
        raise AssertionError("글자로 막을 주소를 찾아봤다")

    never = _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_resolve", no_lookup)
    monkeypatch.setattr(oauth, "_cimd_connect", never)

    assert _refused(url) == NOT_ALLOWED
    assert never.calls == []


@pytest.mark.parametrize("url", ["http://cimd.example/c.json", "HTTPS://cimd.example/c.json",
                                 "ftp://cimd.example/c.json", "file:///etc/passwd",
                                 "//cimd.example/c.json", "cimd.example/c.json", "",
                                 "https://cimd.example", "https://cimd.example/",
                                 "https://cimd.example/c.json#frag",
                                 "https://user:pw@cimd.example/c.json",
                                 "https://cimd.example@127.0.0.1/c.json",
                                 "https://cimd.example\\@127.0.0.1/c.json",
                                 "https://127.0.0.1#@cimd.example/c.json"])
def test_a_client_id_that_is_not_a_plain_https_url_is_refused_before_any_lookup(url, monkeypatch):
    def no_lookup(*a, **kw):
        raise AssertionError("모양으로 막을 주소를 찾아봤다")

    monkeypatch.setattr(oauth, "_cimd_resolve", no_lookup)
    monkeypatch.setattr(oauth, "_cimd_connect", _NoConnect())

    with pytest.raises(ValueError):
        oauth.fetch_cimd(url)


def test_a_non_string_client_id_is_refused(monkeypatch):
    monkeypatch.setattr(oauth, "_cimd_connect", _NoConnect())
    for bad in (None, 123, b"https://cimd.example/c.json", ["https://cimd.example/c.json"]):
        with pytest.raises(ValueError):
            oauth.fetch_cimd(bad)


def test_control_characters_in_the_url_do_not_inject_a_header(serve, monkeypatch):
    first = serve(_answer_doc())
    _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    try:
        oauth.fetch_cimd("https://cimd.example/oauth/client.json\r\nX-Injected: 1")
    except ValueError:
        pass   # 막든 지나가든 상관없다. 머리에 새로 실리면 안 된다.

    for _, headers in first.seen:
        assert "x-injected" not in headers


# ── 이름 바꿔치기(DNS 리바인딩) ─────────────────────────────────────────────────

def test_the_name_is_resolved_once_and_the_checked_address_is_the_one_dialled(serve, monkeypatch):
    """첫 풀이는 공인, 그 뒤의 풀이는 루프백인 이름. 두 번째로 풀면 루프백으로 붙는다."""
    first = serve(_answer_doc())
    resolver = _Resolver([PUBLIC_IP], ["127.0.0.1"])
    connected = _route_plain(monkeypatch, first.port, resolver)

    assert oauth.fetch_cimd(CIMD_URL) == _doc()
    assert resolver.calls == 1
    assert connected == [PUBLIC_IP]


def test_a_refused_first_answer_is_not_retried_with_a_second_lookup(monkeypatch):
    """첫 풀이가 사설이면 거절하고 끝난다. 다시 풀어 공인 답을 얻으려 하지 않는다."""
    resolver, never = _Resolver(["10.0.0.5"], [PUBLIC_IP]), _NoConnect()
    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)
    monkeypatch.setattr(oauth, "_cimd_connect", never)

    assert _refused() == UNREACHABLE
    assert resolver.calls == 1
    assert never.calls == []


def test_a_failed_checked_address_falls_to_the_next_checked_one_not_to_a_new_lookup(serve,
                                                                                   monkeypatch):
    first = serve(_answer_doc())
    resolver = _Resolver([PUBLIC_IP, PUBLIC_IP_B], ["127.0.0.1"])
    tried: list[str] = []

    def connect(ip, port, host, timeout):
        tried.append(ip)
        if ip == PUBLIC_IP:
            raise ConnectionRefusedError(111, "Connection refused")
        return socket.create_connection(("127.0.0.1", first.port), timeout=timeout)

    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)
    monkeypatch.setattr(oauth, "_cimd_connect", connect)

    assert oauth.fetch_cimd(CIMD_URL) == _doc()
    assert tried == [PUBLIC_IP, PUBLIC_IP_B]
    assert resolver.calls == 1


def test_every_checked_address_failing_ends_unreachable_without_a_new_lookup(monkeypatch):
    resolver = _Resolver([PUBLIC_IP, PUBLIC_IP_B], ["127.0.0.1"])
    tried: list[str] = []

    def connect(ip, port, host, timeout):
        tried.append(ip)
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)
    monkeypatch.setattr(oauth, "_cimd_connect", connect)

    assert _refused() == UNREACHABLE
    assert tried == [PUBLIC_IP, PUBLIC_IP_B]
    assert resolver.calls == 1


# ── 본문 상한 ────────────────────────────────────────────────────────────────

def _doc_of_size(n):
    """정확히 `n` 바이트인 CIMD 문서(JSON)."""
    doc = {"redirect_uris": [CALLBACK], "pad": ""}
    fixed = len(json.dumps(doc, separators=(",", ":")).encode())
    doc["pad"] = "a" * (n - fixed)
    raw = json.dumps(doc, separators=(",", ":")).encode()
    assert len(raw) == n
    return raw


def _framed(framing, body):
    """같은 본문을 길이 선언·chunked·닫힘 구분 중 하나로 보낸다."""
    def behave(conn, req):
        if framing == "content-length":
            _send(conn, 200, {"Content-Type": "application/json",
                              "Content-Length": str(len(body))}, body)
        elif framing == "chunked":
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
            for i in range(0, len(body), 8192):
                piece = body[i:i + 8192]
                conn.sendall(b"%x\r\n%s\r\n" % (len(piece), piece))
            conn.sendall(b"0\r\n\r\n")
        else:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Connection: close\r\n\r\n" + body)
    return behave


FRAMINGS = ["content-length", "chunked", "close-delimited"]


@pytest.mark.parametrize("framing", FRAMINGS)
def test_a_document_exactly_at_the_size_cap_is_accepted(framing, serve, monkeypatch):
    cap = oauth._CIMD_MAX_BYTES
    first = serve(_framed(framing, _doc_of_size(cap)))
    _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    assert oauth.fetch_cimd(CIMD_URL)["redirect_uris"] == [CALLBACK]


@pytest.mark.parametrize("framing", FRAMINGS)
def test_a_document_one_byte_over_the_cap_is_refused(framing, serve, monkeypatch):
    cap = oauth._CIMD_MAX_BYTES
    first = serve(_framed(framing, _doc_of_size(cap + 1)))
    _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    message = _refused()
    assert message == f"{INVALID}: document too large"
    assert "aaaa" not in message


def test_a_body_far_larger_than_the_cap_is_cut_early_not_read_to_the_end(serve, monkeypatch):
    """길이를 터무니없이 선언하고 끝없이 보내는 서버. 상한을 넘기는 순간 읽기를 멈춘다."""
    def endless(conn, req):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     b"Content-Length: 1000000000\r\nConnection: close\r\n\r\n")
        chunk = b"a" * 65536
        for _ in range(256):
            conn.sendall(chunk)

    first = serve(endless)
    _route_plain(monkeypatch, first.port, _Resolver([PUBLIC_IP]))

    start = time.monotonic()
    message = _refused()
    took = time.monotonic() - start

    assert message == f"{INVALID}: document too large"
    assert took < 5.0, f"the oversized body held the fetch {took:.2f}s"


# ── TLS: 확인한 주소로 붙고, 이름으로 검증한다 ─────────────────────────────────────

def _openssl(cwd, *args):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """시험용 CA 하나와 그 CA 가 서명한 서버 인증서 둘(cimd.example · other.example)."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI 가 없어 시험용 인증서를 만들 수 없다")
    d = tmp_path_factory.mktemp("pki")
    ec = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    _openssl(d, "req", "-x509", *ec, "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
             "-subj", "/CN=cimd-test-ca",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    for name in ("cimd.example", "other.example"):
        (d / f"{name}.ext").write_text(
            f"subjectAltName=DNS:{name}\nbasicConstraints=CA:FALSE\n"
            "keyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n")
        _openssl(d, "req", "-new", *ec, "-keyout", f"{name}.key", "-out", f"{name}.csr",
                 "-subj", f"/CN={name}")
        _openssl(d, "x509", "-req", "-in", f"{name}.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
                 "-CAcreateserial", "-out", f"{name}.pem", "-days", "2", "-extfile", f"{name}.ext")
    return d


def _server_context(pki, name):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(pki / f"{name}.pem", pki / f"{name}.key")
    return ctx


@pytest.fixture
def trust_test_ca(pki, monkeypatch):
    """`tls_connect` 가 만드는 기본 컨텍스트가 시험용 CA 만 믿게 한다. 검증 자체는 그대로 켜져 있다."""
    real = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context",
                        lambda *a, **kw: real(cafile=str(pki / "ca.pem")))


@pytest.fixture
def dial_to(monkeypatch):
    """`tls_connect` 의 `socket.create_connection` 이 받은 주소를 적고, 실제로는 로컬 서버로 붙는다."""
    dialled: list = []
    real = socket.create_connection

    def install(port):
        def fake(address, timeout=None, *a, **kw):
            dialled.append(address)
            return real(("127.0.0.1", port), timeout)
        monkeypatch.setattr(socket, "create_connection", fake)

    return dialled, install


@pytest.mark.parametrize("public", [PUBLIC_IP, PUBLIC_IP6])
def test_tls_dials_the_checked_address_and_speaks_the_original_name(
        public, pki, serve, trust_test_ca, dial_to, monkeypatch):
    dialled, install = dial_to
    server = serve(_answer_doc(), _server_context(pki, "cimd.example"))
    install(server.port)
    resolver = _Resolver([public], ["127.0.0.1"])
    monkeypatch.setattr(oauth, "_cimd_resolve", resolver)

    assert oauth.fetch_cimd(CIMD_URL) == _doc()

    # 이름이 아니라 확인한 주소로 붙었고, 이름은 한 번만 풀었다.
    assert dialled == [(public, 443)]
    assert resolver.calls == 1
    # SNI 와 Host 는 원래 이름이다(주소가 아니다).
    assert server.sni == ["cimd.example"]
    assert [h["host"] for _, h in server.seen] == ["cimd.example"]


def test_tls_refuses_a_certificate_for_another_name(pki, serve, trust_test_ca, dial_to, monkeypatch):
    """믿는 CA 가 서명했어도 인증서가 요청한 이름의 것이 아니면 붙은 채로 두지 않는다."""
    dialled, install = dial_to
    server = serve(_answer_doc(), _server_context(pki, "other.example"))
    install(server.port)
    monkeypatch.setattr(oauth, "_cimd_resolve", _Resolver([PUBLIC_IP]))

    assert _refused() == UNREACHABLE
    assert dialled == [(PUBLIC_IP, 443)]
    assert server.seen == []


def test_tls_refuses_a_certificate_from_a_ca_the_system_does_not_trust(
        pki, serve, dial_to, monkeypatch):
    """`trust_test_ca` 가 없으면 기본 신뢰 저장소를 쓴다. 시험용 CA 는 거기 없으므로 거절돼야 한다."""
    dialled, install = dial_to
    server = serve(_answer_doc(), _server_context(pki, "cimd.example"))
    install(server.port)
    monkeypatch.setattr(oauth, "_cimd_resolve", _Resolver([PUBLIC_IP]))

    assert _refused() == UNREACHABLE
    assert server.seen == []


def test_tls_connect_dials_only_the_given_address_and_never_looks_the_name_up(
        pki, serve, trust_test_ca, monkeypatch):
    """주소 문자열만 이름 풀기 함수에 들어간다. 이름(`cimd.example`)은 한 번도 들어가지 않는다."""
    server = serve(_answer_doc(), _server_context(pki, "cimd.example"))
    looked_up: list = []
    real = socket.getaddrinfo

    def watch(host, *a, **kw):
        looked_up.append(host)
        return real(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", watch)

    sock = netguard.tls_connect("127.0.0.1", server.port, "cimd.example", 5)
    try:
        assert sock.server_hostname == "cimd.example"
        assert sock.getpeername()[1] == server.port
    finally:
        sock.close()
    assert looked_up == ["127.0.0.1"]


# ── 주소 판정 ────────────────────────────────────────────────────────────────

def test_public_ips_keeps_the_resolved_order_and_drops_duplicates():
    def resolver(host, port, type=0):
        return _addrinfo([PUBLIC_IP, PUBLIC_IP6, PUBLIC_IP], port)

    assert netguard.public_ips("cimd.example", 443, resolver) == [PUBLIC_IP, PUBLIC_IP6]


@pytest.mark.parametrize("failure", [socket.gaierror(-2, "Name or service not known"),
                                     UnicodeError("label too long"), OSError("resolver down")])
def test_public_ips_reports_a_failed_lookup_as_unresolved(failure):
    def resolver(host, port, type=0):
        raise failure

    with pytest.raises(netguard.Unresolved):
        netguard.public_ips("cimd.example", 443, resolver)


def test_public_ips_reports_an_empty_answer_as_unresolved():
    with pytest.raises(netguard.Unresolved):
        netguard.public_ips("cimd.example", 443, lambda host, port, type=0: [])


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "1.1.1.1", PUBLIC_IP6])
def test_ordinary_public_addresses_pass_the_guard(address):
    assert netguard.public_address(address) is True


@pytest.mark.parametrize("text", ["", "not-an-ip", "127.0.0.1/8", "1.2.3", "999.1.1.1", "[::1]"])
def test_text_that_is_not_an_address_is_not_public(text):
    assert netguard.public_address(text) is False
