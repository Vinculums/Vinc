"""Public REST v1 read routes share MCP execution and its account gates."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from vinc_mcp import rest_routes, tools
from vinc_mcp.http_server import MCPHTTPApp, make_server
from vinc_mcp.store import Store


TEAM = "0198c0de-1111-7222-8333-444455556666"


class Bridge:
    owner = "user:local"

    def __init__(self):
        self.calls = []
        self.override = None

    def request(self, method, path, params=None, body=None, **_kw):
        self.calls.append((method, path, params, body))
        if self.override is not None:
            return self.override
        if path == "/search":
            return {"kind": "search", "executed": True, "rows": [],
                    "has_more": False, "next_cursor": None}
        return {"executed": True, "rows": []}


class Server:
    def __init__(self, *, role="viewer", scope="full", quota=None):
        self.bridge = Bridge()

        def verify(token, team=None):
            if token != "vinc_sk_good":
                return None
            if team:
                return {"owner": "team:" + team, "role": role, "scope": scope,
                        "member": "user:u1", "plan": "team"}
            return {"owner": "user:u1", "scope": scope, "plan": "free"}

        self.app = MCPHTTPApp(self.bridge, verify_key=verify,
                              store=Store(":memory:") if quota else None,
                              quota_config=quota)
        self.server = make_server(self.app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, *, body=None, key="vinc_sk_good"):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), json.loads(response.read())


def test_read_routes_cover_every_viewer_tool_and_no_write_tool():
    names = {route.tool for route in rest_routes.READ_ROUTES}
    assert names == {item["name"] for item in tools.TOOLS
                     if tools.tool_tier(item["name"]) == "viewer"}
    assert all(tools.tool_tier(route.tool) == "viewer" for route in rest_routes.READ_ROUTES)
    assert len({(route.method, route.path) for route in rest_routes.ROUTES}) == \
        len(rest_routes.ROUTES)


def test_rest_result_equals_mcp_text_and_owner_is_forced():
    srv = Server()
    try:
        status, headers, body = srv.request(
            "POST", "/v1/search", body={"query": "hello", "owner": "user:victim"})
        assert status == 200 and headers["Vinc-Request-Id"].startswith("req_")
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vinc_search", "arguments": {"query": "hello"}}}
        status, _headers, mcp = srv.request("POST", "/mcp", body=rpc)
        assert status == 200
        assert body == json.loads(mcp["result"]["content"][0]["text"])
        assert all(call[2]["owner"] == "user:u1" for call in srv.bridge.calls)
    finally:
        srv.close()


def test_rest_rejects_non_key_auth_and_coercion_before_quota():
    cfg = {"free_limit": 1, "team_limit": 1, "window_s": 86400, "lock_s": 3600}
    srv = Server(quota=cfg)
    try:
        for key in (None, "bad"):
            status, headers, body = srv.request("GET", "/v1/domains", key=key)
            assert status == 401 and body["error"]["code"] == "not_authenticated"
            assert headers["WWW-Authenticate"] == "Bearer"
        status, _headers, body = srv.request("GET", "/v1/episodes?limit=abc")
        assert status == 400 and body["error"]["code"] == "invalid_argument"
        status, _headers, body = srv.request(
            "POST", "/v1/search", body={"query": "x", "historical": "false"})
        assert status == 400 and body["error"]["code"] == "invalid_argument"
        status, _headers, body = srv.request("POST", "/v1/search", body={"query": "x"})
        assert status == 200
        status, headers, body = srv.request("POST", "/v1/search", body={"query": "x"})
        assert status == 429 and body["error"]["code"] == "quota_exceeded"
        assert int(headers["Retry-After"]) > 0
        assert len(srv.bridge.calls) == 1
    finally:
        srv.close()


def test_team_space_and_view_url_are_gated_before_execution():
    srv = Server(role="viewer")
    try:
        status, _headers, body = srv.request("GET", "/v1/domains?space=bad")
        assert status == 404 and body["error"]["code"] == "space_not_found"
        status, _headers, body = srv.request("POST", "/v1/graph",
                                              body={"url": "https://vincs.io/view?space=local"})
        assert status == 400 and body["error"]["code"] == "on_device_space"
        status, _headers, body = srv.request("GET", f"/v1/domains?space={TEAM}")
        assert status == 200
        assert srv.bridge.calls[-1][2]["owner"] == "team:" + TEAM
    finally:
        srv.close()


def test_openapi_drift_and_method_errors():
    from vinc_mcp.openapi_v1 import FILE, serialized
    assert FILE.read_text(encoding="utf-8") == serialized()
    srv = Server()
    try:
        status, _headers, body = srv.request("GET", "/v1/openapi.json", key=None)
        assert status == 200 and body["openapi"] == "3.1.0"
        status, headers, body = srv.request("GET", "/v1/search")
        assert status == 405 and headers["Allow"] == "POST"
        assert body["error"]["code"] == "method_not_allowed"
        status, _headers, body = srv.request("GET", "/v1/no-such-path")
        assert status == 404 and body["error"]["code"] == "route_not_found"
    finally:
        srv.close()


def test_cursor_error_and_unknown_percent_encoded_node():
    srv = Server()
    try:
        srv.bridge.override = {"executed": False, "error": "invalid_cursor: stale cursor"}
        status, headers, body = srv.request(
            "POST", "/v1/search", body={"query": "x", "cursor": "eyJhIjoxfQ"})
        assert status == 400 and body["error"]["code"] == "invalid_argument"
        assert body["error"]["request_id"] == headers["Vinc-Request-Id"]
        srv.bridge.override = {"kind": "node", "executed": True, "found": False, "node": None}
        status, _headers, body = srv.request("GET", "/v1/nodes/concept%3Aunknown")
        assert status == 404 and body["error"]["code"] == "not_found"
        assert srv.bridge.calls[-1][2]["node_id"] == "concept:unknown"
    finally:
        srv.close()
