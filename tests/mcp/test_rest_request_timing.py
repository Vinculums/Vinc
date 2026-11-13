"""Opt-in timings retain REST auth, ownership, result and request cleanup contracts."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from store import request_timing as timing
from test_rest_v1 import Server


def request(server, path, *, key="vinc_sk_good", method="POST", diagnostic=True,
            body=None):
    headers = {"Authorization": "Bearer " + key}
    if diagnostic:
        headers[timing.HEADER] = timing.VALUE
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(server.base + path, headers=headers, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, dict(response.headers), json.loads(response.read())


def test_authenticated_read_adds_only_numeric_header_and_preserves_evidence():
    server = Server()
    try:
        normal = request(server, "/v1/search", body={"query": "private question"}, diagnostic=False)
        timed = request(server, "/v1/search", body={"query": "private question"})
        assert normal[0] == timed[0] == 200 and normal[2] == timed[2]
        assert "Server-Timing" not in normal[1]
        header = timed[1]["Server-Timing"]
        assert "rest;dur=" in header and "auth;dur=" in header and "gate;dur=" in header
        assert "private" not in header and "vinc_sk" not in header and "user:" not in header
        assert server.bridge.calls[-1][2]["owner"] == "user:u1"
        again = request(server, "/v1/search", body={"query": "private question"}, diagnostic=False)
        assert "Server-Timing" not in again[1]
    finally:
        server.close()


@pytest.mark.parametrize("path,key,method,code", [
    ("/v1/search?query=secret", "bad", "POST", 401),
    ("/v1/search?query=secret&unknown=1", "vinc_sk_good", "POST", 400),
    ("/v1/openapi.json", "bad", "GET", 200),
    ("/v1/fragments", "vinc_sk_good", "POST", 400),
])
def test_diagnostics_do_not_enable_writes_public_routes_or_failed_auth(path, key, method, code):
    server = Server()
    try:
        status, headers, _ = request(server, path, key=key, method=method)
        assert status == code and "Server-Timing" not in headers
        assert not server.bridge.calls
    finally:
        server.close()


def test_timings_cross_real_rest_bridge_and_sidecar_without_changing_search(monkeypatch):
    from api.auth import SessionAuth
    from api.server import VincApp, make_server as sidecar_server
    from vinc_mcp.bridge import Bridge
    from vinc_mcp.http_server import MCPHTTPApp, make_server as public_server
    from types import SimpleNamespace

    class EmptyStore:
        driver_name = "fake"
        scoped_chunk_search = True
        def reader_view(self): return self
        def run(self, *_a, **_kw): return []
        def close(self): pass
        @timing.timed("keyword")
        def fulltext_search(self, *_a, **_kw): return []
        @timing.timed("vector")
        def vector_search(self, *_a, **_kw): return []
        @timing.timed("node_vector")
        def node_vector_search(self, *_a, **_kw):
            # 전체 allowlist가 브리지를 지나도 뒤쪽 구간이 잘리지 않아야 한다.
            for phase in timing.PHASES - {"node_vector_load_error", "node_vector_query_error"}:
                with timing.measure(phase):
                    pass
            return []

    monkeypatch.setenv("VINC_EMBED_ENCODE", "http://127.0.0.1:1")
    app = VincApp(store=EmptyStore(), auth=SessionAuth("fixture-sidecar"))
    monkeypatch.setattr(app.encoder, "encode_query", lambda *_a: ([0.5, 0.5], ""))
    sidecar = sidecar_server(app, "127.0.0.1", 0)
    bridge = Bridge(base=f"http://127.0.0.1:{sidecar.server_address[1]}", token="fixture-sidecar")
    public = public_server(MCPHTTPApp(bridge, verify_key=lambda key, team=None:
        {"owner": "user:u1", "scope": "read", "plan": "free"}
        if key == "vinc_ro_fixture" else None), "127.0.0.1", 0)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (sidecar, public)]
    for thread in threads:
        thread.start()
    server = SimpleNamespace(base=f"http://127.0.0.1:{public.server_address[1]}")
    try:
        plain = request(server, "/v1/search", key="vinc_ro_fixture",
                        body={"query": "private question"}, diagnostic=False)
        measured = request(server, "/v1/search", key="vinc_ro_fixture",
                           body={"query": "private question"})
        assert plain[0] == measured[0] == 200 and plain[2] == measured[2], (plain, measured)
        assert "Server-Timing" not in plain[1]
        header = measured[1]["Server-Timing"]
        for phase in ("sidecar_embed", "sidecar_reader", "sidecar_query", "sidecar_keyword",
                      "sidecar_vector", "sidecar_node_vector", "sidecar", "bridge", "rest",
                      "sidecar_reader_lock", "sidecar_ddl_shared_wait", "sidecar_db_execute",
                      "sidecar_node_vector_index", "sidecar_node_vector_fallback",
                      "sidecar_vector_fallback_cpu"):
            assert phase + ";dur=" in header
        assert "private" not in header and "fixture" not in header and "user:" not in header
    finally:
        for s in (public, sidecar):
            s.shutdown()
            s.server_close()
        for thread in threads:
            thread.join(timeout=5)
        app.close()
