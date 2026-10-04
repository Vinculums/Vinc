"""인그레스가 디코딩한 도메인 주소도 정확한 REST 읽기로 전달된다."""
from urllib.parse import quote

import pytest

from test_rest_v1 import Server, TEAM
from vinc_mcp import rest_routes


@pytest.mark.parametrize("encoded", [True, False])
@pytest.mark.parametrize("suffix,tool,argument", [
    ("", "vinc_node", "node_id"),
    ("/neighbors", "vinc_neighbors", "node"),
    ("/graph", "vinc_graph", "seed"),
    ("/tie-suggestions", "vinc_suggest_ties", "node"),
])
def test_nested_id_reaches_the_right_tool_and_owner(encoded, suffix, tool, argument):
    node = "domain:vinc/agent/roles"
    srv = Server(role="viewer", scope="read")
    srv.bridge.override = {"executed": True, "found": True, "node": {"id": node},
                           "neighbors": [], "count": 0}
    try:
        address = quote(node, safe="") if encoded else node
        path = "/v1/nodes/" + address + suffix
        assert rest_routes.find_route("GET", path)[0].tool == tool
        status, _headers, _body = srv.request("GET", path + "?space=" + TEAM)
        assert status == 200
        args = srv.bridge.calls[-1][2]
        assert args[argument] == node
        assert args["owner"] == "team:" + TEAM
    finally:
        srv.close()


@pytest.mark.parametrize("kind", ["concept", "record", "episode", "decision", "bridge", "domain"])
@pytest.mark.parametrize("encoded", [True, False])
def test_missing_nested_id_is_not_found_without_brief_fallback(kind, encoded):
    node = f"{kind}:missing/nested"
    srv = Server(scope="read")
    srv.bridge.override = {"executed": True, "found": False, "node": None}
    try:
        address = quote(node, safe="") if encoded else node
        status, _headers, body = srv.request("GET", "/v1/nodes/" + address)
        assert status == 404 and body["error"]["code"] == "not_found"
        assert srv.bridge.calls == [("GET", "/node", {"owner": "user:u1", "node_id": node}, None)]
    finally:
        srv.close()


def test_static_routes_and_methods_keep_their_priority():
    assert rest_routes.find_route("POST", "/v1/nodes/merge")[0].tool == "vinc_merge_nodes"
    assert rest_routes.find_route("GET", "/v1/nodes/domain:vinc/agent/roles/graph")[0].tool == "vinc_graph"
    assert rest_routes.find_route("GET", "/v1/documents/a/b") is None
