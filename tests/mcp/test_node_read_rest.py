"""The REST node route and MCP use the same exact, scoped stored-node read."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from test_rest_v1 import Server, TEAM
from vinc_mcp import tools


@pytest.mark.parametrize("kind", ["concept", "record", "decision", "episode", "bridge", "domain"])
def test_missing_exact_ids_return_404_for_every_kind(kind):
    srv = Server(scope="read")
    srv.bridge.override = {"kind": "node", "executed": True, "found": False, "node": None}
    try:
        node_id = f"{kind}:missing/nested"
        status, _headers, body = srv.request("GET", "/v1/nodes/" + quote(node_id, safe=""))
        assert status == 404 and body["error"]["code"] == "not_found"
        assert srv.bridge.calls[-1][2]["node_id"] == node_id
    finally:
        srv.close()


@pytest.mark.parametrize("suffix,argument", [
    ("/neighbors", "node"), ("/graph", "seed"), ("/tie-suggestions", "node"),
])
def test_encoded_nested_domain_routes_preserve_id_and_space_owner(suffix, argument):
    srv = Server(role="viewer", scope="read")
    srv.bridge.override = {"executed": True, "found": True, "neighbors": [], "count": 0}
    try:
        node_id = "domain:vinc/agent/roles"
        status, _headers, _body = srv.request(
            "GET", "/v1/nodes/" + quote(node_id, safe="") + suffix + f"?space={TEAM}")
        assert status == 200
        args = srv.bridge.calls[-1][2]
        assert args[argument] == node_id and args["owner"] == "team:" + TEAM
    finally:
        srv.close()


def test_node_route_returns_props_in_one_bridge_call_without_brief_material():
    srv = Server(scope="read")
    result = {"kind": "node", "executed": True, "found": True,
              "node": {"id": "record:claims", "kind": "record", "title": "Claims",
                       "props": {"claims": [{"claim": "Preserved", "evidence_ids": ["concept:a"]}]},
                       "tags": ["package"], "valid_from": "2025", "valid_until": "2026",
                       "content_time": "2025-10"}}
    srv.bridge.override = result
    try:
        status, _headers, body = srv.request("GET", "/v1/nodes/record%3Aclaims")
        assert (status, body) == (200, result)
        assert srv.bridge.calls == [("GET", "/node", {"owner": "user:u1", "node_id": "record:claims"}, None)]
        assert set(body) == {"kind", "executed", "found", "node"}
    finally:
        srv.close()


def test_exact_read_keeps_space_owner_and_matches_mcp():
    srv = Server(role="viewer", scope="read")
    srv.bridge.override = {"kind": "node", "executed": True, "found": True,
                           "node": {"id": "concept:role", "props": {"mission": "Read"}}}
    try:
        status, _h, rest = srv.request("GET", f"/v1/nodes/concept%3Arole?space={TEAM}")
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vinc_node", "arguments": {"node_id": "concept:role", "space": TEAM}}}
        mstatus, _h, mcp = srv.request("POST", "/mcp", body=rpc)
        assert status == mstatus == 200
        assert rest == json.loads(mcp["result"]["content"][0]["text"])
        assert all(call[2]["owner"] == "team:" + TEAM for call in srv.bridge.calls)
        assert len(srv.bridge.calls) == 2
    finally:
        srv.close()


def test_brief_keeps_its_joined_material_contract():
    srv = Server()
    brief = {"kind": "brief", "executed": True, "status": "resolved",
             "node": {"id": "concept:x"}, "one_liner": "Quoted",
             "links": {"items": [{"id": "concept:y"}]}, "excerpts": [{"doc_id": "canon"}]}
    srv.bridge.override = brief
    try:
        status, _h, body = srv.request("POST", "/v1/brief", body={"query": "concept:x"})
        assert (status, body) == (200, brief)
        assert srv.bridge.calls[0][1] == "/brief"
        assert tools.tool_tier("vinc_node") == "viewer"
    finally:
        srv.close()
