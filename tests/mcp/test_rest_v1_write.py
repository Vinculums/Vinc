"""REST write routes keep the MCP tool contract and gate every tier."""
from __future__ import annotations

import json
import http.client
import threading
import urllib.error
import urllib.request

from vinc_mcp import rest_routes, tools
from vinc_mcp.rest_api import _failure
from vinc_mcp.http_server import MCPHTTPApp, make_server


TEAM = "0198c0de-1111-7222-8333-444455556666"


class Bridge:
    owner = "user:local"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, **_kw):
        self.calls.append((method, path, params, body))
        if path == "/doc/put":
            return {"executed": True, "created": True, "doc_id": "doc:test"}
        if path == "/ingest/fragment":
            return {"executed": True, "partial": True, "warnings": ["one skipped"],
                    "id_conflicts": []}
        return {"executed": True}


class Server:
    def __init__(self, *, role="admin", scope="full"):
        self.bridge = Bridge()

        def verify(key, space=None):
            if key != "vinc_sk_good":
                return None
            if space:
                return {"owner": "team:" + space, "role": role, "member": "user:u1",
                        "scope": scope, "plan": "team"}
            return {"owner": "user:u1", "scope": scope, "plan": "free"}

        self.server = make_server(MCPHTTPApp(self.bridge, verify_key=verify),
                                  "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer vinc_sk_good"}, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, dict(response.headers), json.loads(response.read())


CASES = (
    ("POST", "/v1/merge-rejections", {"a": "concept:a", "b": "concept:b"}, "vinc_merge_reject"),
    ("POST", "/v1/fragments", {"nodes": [], "edges": []}, "vinc_ingest"),
    ("POST", "/v1/nodes/merge", {"keep_id": "concept:a", "drop_id": "concept:b"},
     "vinc_merge_nodes"),
    ("DELETE", "/v1/nodes/concept%3Aa", None, "vinc_delete"),
    ("DELETE", "/v1/edges?from_id=concept%3Aa&to_id=concept%3Ab&type=relates_to",
     None, "vinc_delete_edge"),
    ("PUT", "/v1/documents/doc%3Atest", {"text": "body"}, "vinc_doc_put"),
    ("POST", "/v1/documents", {"text": "body"}, "vinc_doc_put"),
    ("DELETE", "/v1/documents/doc%3Atest", None, "vinc_doc_delete"),
    ("POST", "/v1/documents/doc%3Atest/extract", {"apply": False}, "vinc_doc_extract"),
)

MCP_ARGS = (
    {"a": "concept:a", "b": "concept:b"},
    {"nodes": [], "edges": []},
    {"keep_id": "concept:a", "drop_id": "concept:b"},
    {"node_id": "concept:a"},
    {"from_id": "concept:a", "to_id": "concept:b", "type": "relates_to"},
    {"doc_id": "doc:test", "text": "body"},
    {"text": "body"},
    {"doc_id": "doc:test"},
    {"doc_id": "doc:test", "apply": False},
)


def _team(path):
    return path + ("&" if "?" in path else "?") + "space=" + TEAM


def test_all_34_tools_are_routed_and_host_only_tools_are_excluded():
    routed = {route.tool for route in rest_routes.ROUTES}
    public = {item["name"] for item in tools.TOOLS}
    assert routed == public and len(public) == 34
    assert rest_routes.HOST_EXCLUSIONS == {"search", "fetch"}
    assert not routed.intersection(rest_routes.HOST_EXCLUSIONS)
    assert len({(route.method, route.path) for route in rest_routes.ROUTES}) == len(rest_routes.ROUTES)
    for method, path, _body, name in CASES:
        route, _fields = rest_routes.find_route(method, path.split("?", 1)[0])
        assert route.tool == name


def test_every_write_route_rejects_read_only_key_before_bridge():
    srv = Server(scope="read")
    try:
        for method, path, body, _name in CASES:
            status, _headers, result = srv.request(method, path, body)
            assert status == 403, (method, path, result)
            assert result["error"]["code"] == "read_only_key"
        assert srv.bridge.calls == []
    finally:
        srv.close()


def test_team_role_gate_for_every_write_route():
    for role in ("viewer", "editor", "admin"):
        srv = Server(role=role)
        try:
            for method, path, body, name in CASES:
                status, _headers, result = srv.request(method, _team(path), body)
                allowed = tools.role_allows(role, name)
                assert (status < 300) == allowed, (role, method, path, result)
                if not allowed:
                    assert result["error"]["code"] == "role_forbidden"
            assert len(srv.bridge.calls) == sum(tools.role_allows(role, name) for _, _, _, name in CASES)
            for method, _path, params, body in srv.bridge.calls:
                assert (params or body)["owner"] == "team:" + TEAM
        finally:
            srv.close()


def test_partial_ingest_and_created_document_match_mcp_result():
    srv = Server()
    try:
        status, _headers, result = srv.request("POST", "/v1/fragments",
                                                {"nodes": [], "edges": []})
        assert status == 200 and result["partial"] is True
        assert result["warnings"] == ["one skipped"]
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vinc_ingest", "arguments": {"nodes": [], "edges": []}}}
        status, _headers, mcp = srv.request("POST", "/mcp", rpc)
        assert status == 200
        assert result == json.loads(mcp["result"]["content"][0]["text"])
        status, _headers, result = srv.request("PUT", "/v1/documents/doc%3Atest",
                                                {"text": "body"})
        assert status == 201 and result["created"] is True
    finally:
        srv.close()


def test_every_write_route_has_exact_mcp_argument_and_result_parity():
    srv = Server()
    try:
        for (method, path, body, name), args in zip(CASES, MCP_ARGS):
            status, _headers, result = srv.request(method, path, body)
            assert status < 300, (method, path, result)
            rest_call = srv.bridge.calls[-1]
            rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": args}}
            status, _headers, mcp = srv.request("POST", "/mcp", rpc)
            assert status == 200
            assert result == json.loads(mcp["result"]["content"][0]["text"]), name
            assert rest_call == srv.bridge.calls[-1], name
    finally:
        srv.close()


def test_json_string_false_cannot_apply_document_extraction():
    srv = Server()
    try:
        status, _headers, result = srv.request(
            "POST", "/v1/documents/doc%3Atest/extract", {"apply": "false"})
        assert status == 400 and result["error"]["code"] == "invalid_argument"
        assert srv.bridge.calls == []
    finally:
        srv.close()


def test_node_delete_preview_query_has_exact_mcp_boolean_parity():
    srv = Server()
    try:
        for value, flag in [('true', True), ('false', False)]:
            status, _headers, result = srv.request('DELETE', '/v1/nodes/record%3Atest?dry_run=' + value)
            assert status == 200, result
            rest_call = srv.bridge.calls[-1]
            assert rest_call[3]['dry_run'] is flag
            rpc = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'vinc_delete', 'arguments': {'node_id': 'record:test', 'dry_run': flag}}}
            status, _headers, _result = srv.request('POST', '/mcp', rpc)
            assert status == 200
            assert srv.bridge.calls[-1] == rest_call
        before = len(srv.bridge.calls)
        status, _headers, result = srv.request('DELETE', '/v1/nodes/record%3Atest?dry_run=maybe')
        assert status == 400 and result['error']['code'] == 'invalid_argument'
        assert len(srv.bridge.calls) == before
    finally:
        srv.close()


def test_known_tool_failure_shapes_map_to_stable_codes():
    assert _failure("vinc_delete", {"executed": True, "found": False})[1] == "not_found"
    assert _failure("vinc_doc_delete", {"executed": True, "found": False,
                                        "error": "unknown doc_id"})[1] == "not_found"
    assert _failure("vinc_delete_edge", {"executed": True, "found": False,
                                        "note": "unsupported edge type"})[1] == "invalid_argument"
    assert _failure("vinc_merge_nodes", {"executed": True,
                                        "error": "keep and drop are the same node"})[1] == \
        "invalid_argument"
    assert _failure("vinc_diagnose", {"executed": False,
                                     "note": "algorithm unavailable"})[1] == \
        "upstream_unavailable"
    assert _failure("vinc_ingest", {"executed": True, "partial": True,
                                   "warnings": ["skipped"]}) is None


def test_body_limit_returns_413_before_bridge():
    srv = Server()
    try:
        # Advertise an oversized body without sending it: the gate must decide
        # from Content-Length before reading attacker-controlled bytes.
        conn = http.client.HTTPConnection("127.0.0.1", srv.server.server_address[1])
        conn.putrequest("POST", "/v1/documents")
        conn.putheader("Authorization", "Bearer vinc_sk_good")
        conn.putheader("Content-Length", str(4 * 1024 * 1024 + 1))
        conn.endheaders()
        response = conn.getresponse()
        status, headers = response.status, dict(response.headers)
        result = json.loads(response.read())
        conn.close()
        assert status == 413 and result["error"]["code"] == "payload_too_large"
        assert result["error"]["request_id"] == headers["Vinc-Request-Id"]
        assert srv.bridge.calls == []
    finally:
        srv.close()
