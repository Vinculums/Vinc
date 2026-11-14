"""허브의 `POST` 는 정수가 아니거나 음수인 `Content-Length` 에 400 으로 답한다(답 없이 끊지 않는다)."""
from __future__ import annotations

import http.client
import json

import pytest

from test_rest_v1 import Server


@pytest.fixture
def server():
    srv = Server()
    try:
        yield srv
    finally:
        srv.close()


def _post(srv, path, length, payload=b"{}"):
    host, port = srv.base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    try:
        conn.putrequest("POST", path)
        conn.putheader("Authorization", "Bearer vinc_sk_good")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", length)
        conn.endheaders(payload)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/mcp", "/sync/push"])
@pytest.mark.parametrize("length", ["abc", "1.5", "-5"])
def test_a_length_that_is_not_a_count_is_400_before_the_body_is_read(server, path, length):
    assert _post(server, path, length) == (400, {"error": "invalid Content-Length"})
    assert server.bridge.calls == []


def test_a_huge_length_is_not_read_and_still_answers(server):
    # 상한을 넘는 본문은 읽지 않고 빈 본문으로 강등한다. /mcp 는 그것을 메시지 없음으로 답한다.
    status, body = _post(server, "/mcp", "9" * 30)
    assert (status, body) == (400, {"error": "expected a JSON-RPC message"})
    assert server.bridge.calls == []


def test_the_v1_door_keeps_its_own_envelope_for_a_bad_length(server):
    status, body = _post(server, "/v1/search", "abc")
    assert status == 400
    assert body["error"]["code"] == "invalid_argument"
    assert body["error"]["message"] == "Invalid Content-Length."
