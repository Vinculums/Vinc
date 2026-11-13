"""검증 장애는 자격 거절이 아니며, 어떤 전송 경로에서도 접근을 열지 않는다."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from vinc_mcp import entitlement as E
from vinc_mcp.http_server import MCPHTTPApp, make_server


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


@pytest.fixture
def account(monkeypatch):
    E._cache.clear()
    monkeypatch.setattr(E, "_seed", lambda: b"\x01" * 32)
    monkeypatch.setattr(E, "_sign_assertion", lambda seed, typ: "test assertion")
    yield
    E._cache.clear()


@pytest.mark.parametrize("route", ["personal", "team_key", "team_oauth"])
@pytest.mark.parametrize("failure", ["timeout", 503, 401, b"bad JSON", {},
                                    {"graph_owner": "user:wrong", "role": "editor"}])
def test_outage_is_not_cached_and_immediate_retry_recovers(account, monkeypatch, caplog, route, failure):
    calls = []
    good = {"sub": "u1", "plan": "free", "scope": "read",
            "graph_owner": "team:t1", "role": "viewer"}
    if route == "team_key":
        good["key_space"] = "t1"

    def upstream(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) > 1:
            return Response(good)
        if failure == "timeout":
            raise TimeoutError("do not leak vinc_ro_secret")
        if isinstance(failure, int):
            raise urllib.error.HTTPError(req.full_url, failure, "private detail", {}, None)
        return Response(failure)

    monkeypatch.setattr(E.hubhttp, "urlopen", upstream)
    verify = (lambda: E.verify_member("u1", "t1")) if route == "team_oauth" else (
        lambda: E.verify_key("vinc_ro_secret", "t1" if route == "team_key" else None))
    with pytest.raises(E.VerificationUnavailable, match="temporarily unavailable") as caught:
        verify()
    assert "secret" not in str(caught.value)
    assert "secret" not in caplog.text and "private detail" not in caplog.text
    assert E._cache == {}
    assert verify()["owner"] == ("user:u1" if route == "personal" else "team:t1")
    assert verify()["owner"] and len(calls) == 2


@pytest.mark.parametrize("route", ["personal", "team_key", "team_oauth"])
def test_explicit_rejection_is_cached_per_scope(account, monkeypatch, route):
    calls = []

    def upstream(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(E.hubhttp, "urlopen", upstream)
    verify = (lambda: E.verify_member("u1", "t1")) if route == "team_oauth" else (
        lambda: E.verify_key("vinc_ro_secret", "t1" if route == "team_key" else None))
    assert verify() is None and verify() is None
    assert len(calls) == 1


class Bridge:
    owner = "user:local"

    def request(self, *args, **kwargs):
        raise AssertionError("an unavailable verifier must not reach the graph")


def test_oauth_team_membership_outage_returns_503():
    class OAuth:
        store = None

        def resolve(self, token):
            return {"owner": "user:u1"}

    def member(sub, team):
        raise E.VerificationUnavailable()

    server = make_server(MCPHTTPApp(Bridge(), oauth=OAuth(), verify_member=member),
                         "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}"
            "/t/0198c0de-1111-7222-8333-444455556666/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers={"Authorization": "Bearer oauth-token", "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        with caught.value as response:
            assert response.code == 503 and response.headers["Retry-After"] == "1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("path,body,team_only", [
    ("/v1/search", {"query": "q"}, False),
    ("/v1/search?space=0198c0de-1111-7222-8333-444455556666", {"query": "q"}, True),
    ("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, False),
    ("/t/0198c0de-1111-7222-8333-444455556666/mcp",
     {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, False),
    ("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "vinc_neighbors", "arguments": {"node": "concept:x",
        "space": "0198c0de-1111-7222-8333-444455556666"}}}, True),
])
def test_rest_and_mcp_outages_return_retryable_503(path, body, team_only):
    def verify(raw, team=None):
        if not team_only or team:
            raise E.VerificationUnavailable()
        return {"owner": "user:u1", "plan": "free", "scope": "read"}

    server = make_server(MCPHTTPApp(Bridge(), verify_key=verify), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}" + path,
            data=json.dumps(body).encode(), headers={"Authorization": "Bearer vinc_ro_secret",
            "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        with caught.value as response:
            assert response.code == 503
            assert response.headers["Retry-After"] == "1"
            assert "secret" not in response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
