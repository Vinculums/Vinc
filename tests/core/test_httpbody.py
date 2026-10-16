# -*- coding: utf-8 -*-
"""VIN-595 — 요청 본문 읽기는 한 벌(`store/httpbody.py`)이고, 사이드카도 상한을 갖는다.

예전 사이드카는 `Content-Length` 를 그대로 믿고 읽었다. 허브에만 있던 상한을 사이드카에도
건다. 사이드카는 HTTP/1.1 keep-alive 라 읽지 않은 본문을 남긴 채 연결을 유지하면 잔여물이
다음 요청이 된다 — 그래서 413 과 함께 연결을 닫는지도 본다.
"""
from __future__ import annotations

import http.client
import io
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import server as srv  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from store import httpbody  # noqa: E402

_TOKEN = "test-token-httpbody"


def test_상한을_넘으면_읽지_않는다():
    rfile = io.BytesIO(b"x" * 100)
    with pytest.raises(httpbody.BodyTooLarge):
        httpbody.read_body({"Content-Length": "100"}, rfile, 10)
    assert rfile.tell() == 0


def test_길이가_없으면_빈_본문():
    assert httpbody.read_body({}, io.BytesIO(b"{}"), 10) == b""
    assert httpbody.parse_json(b"") == {}


def test_UTF8_이_아니면_ValueError():
    with pytest.raises(ValueError):
        httpbody.parse_json("{\"q\": \"한글\"}".encode("cp949"))


@pytest.fixture
def sidecar(monkeypatch):
    monkeypatch.setattr(srv, "MAX_BODY", 64)
    app = srv.VincApp(store=None, auth=SessionAuth(_TOKEN))
    httpd = srv.make_server(app, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        app.close()


def _post(port, path, payload: bytes):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=payload, headers={
            "Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Connection"), json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def test_사이드카_라우트가_상한을_넘는_본문을_413_으로_닫는다(sidecar):
    big = json.dumps({"owner": "user:a", "nodes": [], "pad": "x" * 200}).encode()
    code, connection, obj = _post(sidecar, "/ingest", big)
    assert code == 413, obj
    assert connection == "close"
    assert "limit" in obj["error"]


def test_무인증_콜백도_상한을_넘으면_413(sidecar):
    code, _, obj = _post(sidecar, "/connect/callback", b"{" + b" " * 200 + b"}")
    assert code == 413, obj


def test_상한_안의_본문은_그대로_라우트에_간다(sidecar):
    code, _, obj = _post(sidecar, "/ingest", b"{\"owner\": \"user:a\"}")
    assert code != 413, obj
