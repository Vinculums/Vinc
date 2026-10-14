"""PR #472 리뷰에서 재현된 REST v1 결함의 회귀 테스트, 그리고 도구별 실패 모양 표.

`FAILURE_CASES` 가 설계 §2.5 의 "도구마다 인벤토리로 정하고 테스트로 고정한다"의 그 표다.
tests/fixtures/tool_failure_shapes.json 은 사람이 읽는 목록이고, 여기가 실제 결과 모양이다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from test_rest_v1 import Server
from vinc_mcp import rest_api, rest_routes, tools
from vinc_mcp.rest_api import _failure

# (도구, 결과, by_id, 기대 상태, 기대 코드). 상태가 None 이면 성공으로 통과해야 한다.
FAILURE_CASES = [
    ("vinc_community_read", {"executed": True, "version": 1}, False, None, None),
    ("vinc_community_read", {"isError": True, "status": 410, "error": "withdrawn"}, False, 410, "gone"),
    ("vinc_operation_status", {"executed": True, "found": False, "state": "unknown"}, False, None, None),
    ("vinc_operation_status", {"executed": False, "error": "receipt_read_unavailable"}, False, 503, "upstream_unavailable"),
    ("vinc_doc_put", {"executed": False, "error": "write_conflict", "status": "conflict"}, False, 409, "write_conflict"),
    ("vinc_delete", {"executed": False, "error": "preview_conflict"}, False, 409, "preview_conflict"),
    ("vinc_community_write", {"saved": True, "revision": 1, "graph": "pending"}, False, None, None),
    ("vinc_community_write", {"isError": True, "status": 403, "error": "protected_rules"}, False,
     403, "role_forbidden"),
    ("vinc_community_write", {"isError": True, "status": 409, "error": "edit_conflict"}, False,
     409, "conflict"),
    ("vinc_role", {"executed": True, "found": False}, True, 404, "not_found"),
    ("vinc_role", {"executed": False, "note": "no reachable store"}, True, 503, "upstream_unavailable"),
    ("vinc_role_check", {"kind": "role_check", "executed": True, "found": False}, True, 404, "not_found"),
    ("vinc_role_check", {"kind": "role_check", "executed": False, "error": "output_required"}, True,
     400, "invalid_argument"),
    ("vinc_role_check", {"kind": "role_check", "executed": True, "found": True, "verdict": "block"},
     True, None, None),
    ("vinc_edges", {"executed": True, "found": False, "edges": []}, True, 404, "not_found"),
    ("vinc_edges", {"executed": True, "found": True, "edges": []}, True, None, None),
    ("vinc_search", {"executed": False, "error": "invalid_cursor: x"}, False, 400, "invalid_argument"),
    ("vinc_search", {"executed": True, "rows": []}, False, None, None),
    ("vinc_recall", {"executed": False, "error": "stale_cursor"}, False, 400, "invalid_argument"),
    ("vinc_recall", {"executed": True, "rows": []}, False, None, None),
    ("vinc_brief", {"status": "none", "unreadable": ["concept:x"]}, False, 404, "not_found"),
    ("vinc_brief", {"status": "none"}, True, 404, "not_found"),
    ("vinc_brief", {"status": "none"}, False, None, None),
    ("vinc_node", {"executed": True, "found": False, "node": None}, True, 404, "not_found"),
    ("vinc_node", {"executed": False, "note": "no reachable store"}, True, 503, "upstream_unavailable"),
    ("vinc_graph", {"found": False}, True, 404, "not_found"),
    ("vinc_decide", {"found": False}, False, 404, "not_found"),
    ("vinc_domains", {"executed": True, "rows": []}, False, None, None),
    ("vinc_suggest_ties", {"found": False}, True, 404, "not_found"),
    ("vinc_suggest_ties", {"executed": True, "suggestions": []}, True, None, None),
    ("vinc_neighbors", {"found": False}, True, 404, "not_found"),
    ("vinc_neighbors", {"found": True, "neighbors": []}, True, None, None),
    ("vinc_path", {"found": False}, False, 404, "not_found"),
    ("vinc_merge_candidates", {"executed": True, "candidates": []}, False, None, None),
    ("vinc_merge_reject", {"executed": False, "error": "invalid pair"}, False, 400, "invalid_argument"),
    ("vinc_merge_rejections", {"executed": True, "rejections": []}, False, None, None),
    ("vinc_diagnose", {"executed": False, "note": "algo extension unavailable"}, False, 503,
     "upstream_unavailable"),
    ("vinc_diagnose", {"executed": True, "components": []}, False, None, None),
    ("vinc_ingest", {"executed": True, "partial": True, "warnings": ["x"], "id_conflicts": []},
     False, None, None),
    ("vinc_ingest", {"executed": False, "error": "no reachable store"}, False, 503,
     "upstream_unavailable"),
    ("vinc_ingest", {"error": "domain_limit_reached", "limit": 5, "current": 5,
                     "attempted": ["a"]}, False, 409, "domain_limit_reached"),
    ("vinc_delete", {"executed": True, "found": False}, True, 404, "not_found"),
    ("vinc_delete_edge", {"executed": True, "found": False}, False, 404, "not_found"),
    ("vinc_delete_edge", {"executed": False, "note": "unsupported edge type"}, False, 400,
     "invalid_argument"),
    ("vinc_merge_nodes", {"executed": False, "error": "unknown node concept:x"}, False, 404,
     "not_found"),
    ("vinc_merge_nodes", {"executed": False, "error": "merge supports concept pairs only"},
     False, 400, "invalid_argument"),
    ("vinc_doc_put", {"executed": False, "error": "invalid doc_id"}, True, 400, "invalid_argument"),
    ("vinc_doc_put", {"error": "sidecar write unconfirmed: TimeoutError: x",
                      "retryable": False}, True, 503, "upstream_unavailable"),
    ("vinc_doc_extract", {"executed": False, "error": "unknown doc_id"}, True, 404, "not_found"),
    ("vinc_doc_extract", {"executed": True, "apply": False, "candidates": []}, True, None, None),
    ("vinc_doc_get", {"executed": False, "error": "unknown doc_id"}, True, 404, "not_found"),
    ("vinc_doc_get", {"executed": True, "verified": False}, True, 503, "upstream_unavailable"),
    ("vinc_doc_list", {"executed": False, "error": "invalid_cursor"}, False, 400, "invalid_argument"),
    ("vinc_doc_list", {"executed": True, "docs": []}, False, None, None),
    ("vinc_doc_delete", {"executed": False, "error": "unknown doc_id"}, True, 404, "not_found"),
    ("vinc_conventions", {"executed": True, "text": "defaults"}, False, None, None),
    ("vinc_whoami", {"executed": True, "spaces_source": "unavailable"}, False, None, None),
    ("vinc_domains", {"error": "sidecar HTTP 502"}, False, 503, "upstream_unavailable"),
]


@pytest.mark.parametrize("tool,result,by_id,status,code", FAILURE_CASES)
def test_failure_shapes_map_per_tool(tool, result, by_id, status, code):
    got = _failure(tool, result, by_id=by_id)
    if status is None:
        assert got is None
    else:
        assert got is not None and got[:2] == (status, code)


def test_every_routed_tool_has_a_pinned_failure_case():
    pinned = {case[0] for case in FAILURE_CASES}
    routed = {route.tool for route in rest_routes.ROUTES}
    assert routed - pinned == set()


def test_unconfirmed_write_says_it_may_have_applied():
    status, code, _msg, details = _failure(
        "vinc_doc_put", {"error": "sidecar write unconfirmed: x", "retryable": False})
    assert (status, code) == (503, "upstream_unavailable")
    assert details == {"retryable": False, "write_may_have_applied": True}


# ── 서버 수준 ────────────────────────────────────────────────────────────────

_READ_SAMPLES = {
    ("POST", "/v1/community/read"): ({"space": "community:0198c0de-1111-7222-8333-444455556666",
        "action": "agent_detail", "payload": {"shareId": "0198c0de-2222-7222-8333-444455556666"}},
        {"space": "community:0198c0de-1111-7222-8333-444455556666", "action": "agent_detail",
         "payload": {"shareId": "0198c0de-2222-7222-8333-444455556666"}}),
    ("GET", "/v1/operations/{operation_id}"): ("", {"operation_id": "00000000-0000-4000-8000-000000000488"}),
    ("GET", "/v1/roles"): ("", {}),
    ("GET", "/v1/roles/{id}"): ("", {"role": "concept:agent-x"}),
    ("POST", "/v1/roles/{id}/check"): ({"output": "x"}, {"role": "concept:agent-x", "output": "x"}),
    ("GET", "/v1/nodes/{id}/edges"): ("", {"node": "concept:x"}),
    ("POST", "/v1/edges/query"): ({"nodes": ["concept:x"]}, {"nodes": ["concept:x"]}),
    ("GET", "/v1/me"): (None, {}),
    ("GET", "/v1/conventions"): ("?lang=en", {"lang": "en"}),
    ("GET", "/v1/domains"): (None, {}),
    ("POST", "/v1/search"): ({"query": "x"}, {"query": "x"}),
    ("POST", "/v1/brief"): ({"query": "x"}, {"query": "x"}),
    ("GET", "/v1/nodes/{id}"): ("", {"node_id": "concept:x"}),
    ("GET", "/v1/nodes/{id}/neighbors"): ("", {"node": "concept:x"}),
    ("GET", "/v1/nodes/{id}/graph"): ("", {"seed_id": "concept:x"}),
    ("POST", "/v1/graph"): ({"url": "https://vincs.io/app/#view/trace/concept:x"},
                            {"url": "https://vincs.io/app/#view/trace/concept:x"}),
    ("GET", "/v1/nodes/{id}/tie-suggestions"): ("", {"node": "concept:x"}),
    ("GET", "/v1/path"): ("?a=concept%3Aa&b=concept%3Ab", {"a": "concept:a", "b": "concept:b"}),
    ("GET", "/v1/episodes"): (None, {}),
    ("GET", "/v1/decide"): ("?topic=x", {"topic": "x"}),
    ("GET", "/v1/diagnose"): ("?kind=claims", {"kind": "claims"}),
    ("GET", "/v1/merge-candidates"): (None, {}),
    ("GET", "/v1/merge-rejections"): (None, {}),
    ("GET", "/v1/documents"): (None, {}),
    ("GET", "/v1/documents/{doc_id}"): ("", {"doc_id": "d1"}),
}

#: 모든 도구가 성공으로 읽을 만한 브리지 답.
_OK = {"executed": True, "found": True, "status": "ok", "verified": True, "rows": [],
       "docs": [], "nodes": [], "edges": [], "neighbors": [], "text": "t",
       "node": {"id": "concept:agent-x", "props": {}}}


@pytest.mark.parametrize("route", rest_routes.READ_ROUTES, ids=lambda r: f"{r.method} {r.path}")
def test_rest_equals_mcp_for_every_read_route(route, monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    sample, arguments = _READ_SAMPLES[(route.method, route.path)]
    path = route.path.replace("{id}", quote("concept:x", safe="")).replace("{doc_id}", "d1").replace(
        "{operation_id}", "00000000-0000-4000-8000-000000000488")
    if route.tool in ("vinc_role", "vinc_role_check"):
        path = route.path.replace("{id}", quote("concept:agent-x", safe=""))
    body = None
    if isinstance(sample, str):
        path += sample
    elif isinstance(sample, dict):
        body = dict(sample)
        if "space" in body:
            path += "?space=" + quote(body.pop("space"), safe="")
    srv = Server()
    srv.bridge.override = dict(_OK)
    try:
        status, _h, rest = srv.request(route.method, path, body=body)
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": route.tool, "arguments": arguments}}
        _s, _h2, mcp = srv.request("POST", "/mcp", body=rpc)
        text = mcp["result"]["content"][0]["text"]
        if mcp["result"].get("isError"):
            assert status >= 400
        else:
            expected = json.loads(text)
            assert (status, rest) == (200, expected), route.path
    finally:
        srv.close()


def test_read_only_key_can_call_every_read_route(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    srv = Server(scope="read")
    srv.bridge.override = dict(_OK)
    try:
        for route in rest_routes.READ_ROUTES:
            sample, _args = _READ_SAMPLES[(route.method, route.path)]
            path = route.path.replace("{id}", "concept%3Ax").replace("{doc_id}", "d1").replace(
                "{operation_id}", "00000000-0000-4000-8000-000000000488")
            if route.tool in ("vinc_role", "vinc_role_check"):
                path = route.path.replace("{id}", "concept%3Aagent-x")
            body = dict(sample) if isinstance(sample, dict) else None
            if body and "space" in body:
                path += "?space=" + quote(body.pop("space"), safe="")
            path += sample if isinstance(sample, str) else ""
            status, _h, out = srv.request(route.method, path, body=body)
            assert status == 200, (route.path, out)
    finally:
        srv.close()


def test_canonical_withdrawal_keeps_http_gone_status(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    srv = Server(scope="read")
    srv.bridge.override = {"isError": True, "status": 410, "error": "withdrawn"}
    try:
        status, _, result = srv.request("POST", "/v1/community/read?space=community%3A0198c0de-1111-7222-8333-444455556666",
            body={"action": "agent_export", "payload": {"shareId": "0198c0de-2222-7222-8333-444455556666", "version": 1, "format": "vincpack"}})
        assert status == 410 and result["error"]["code"] == "gone", result
        assert result["error"]["message"] == "withdrawn"
    finally:
        srv.close()


def test_a_large_result_is_returned_whole():
    srv = Server()
    big = "x" * 100_000
    srv.bridge.override = {"executed": True, "doc_id": "d1", "text": big, "verified": True,
                           "chunks": 1}
    try:
        status, _h, body = srv.request("GET", "/v1/documents/d1")
        assert status == 200 and body["text"] == big
    finally:
        srv.close()


def test_repeated_space_is_refused_and_space_is_lowercased():
    srv = Server()
    try:
        status, _h, body = srv.request(
            "GET", "/v1/domains?space=personal&space=0198c0de-1111-7222-8333-444455556666")
        assert status == 400 and "repeated parameter: space" in body["error"]["message"]
        status, _h, _b = srv.request("GET", "/v1/domains?space=Personal")
        assert status == 200
    finally:
        srv.close()


def test_a_non_json_sidecar_answer_is_upstream_unavailable(monkeypatch):
    srv = Server()

    def broken(*_a, **_kw):
        raise json.JSONDecodeError("Expecting value", "", 0)
    srv.bridge.request = broken
    try:
        status, _h, body = srv.request("GET", "/v1/domains")
        assert status == 503 and body["error"]["code"] == "upstream_unavailable"
    finally:
        srv.close()


def test_quota_denial_carries_limit_headers():
    cfg = {"free_limit": 1, "team_limit": 1, "window_s": 86400, "lock_s": 3600}
    srv = Server(quota=cfg)
    try:
        srv.request("GET", "/v1/domains")
        status, headers, body = srv.request("GET", "/v1/domains")
        assert status == 429
        assert headers.get("Vinc-Quota-Remaining") == "0" and headers.get("Retry-After")
    finally:
        srv.close()


def test_put_outside_v1_keeps_the_standard_501():
    srv = Server()
    try:
        req = urllib.request.Request(srv.base + "/health", data=b"{}", method="PUT")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 501
        assert "Access-Control-Allow-Origin" not in err.value.headers
    finally:
        srv.close()


def test_result_cap_is_not_the_mcp_model_cap():
    from vinc_mcp.rpc import MAX_RESULT_CHARS
    assert rest_api.MAX_RESULT_BYTES > 4 * 1024 * 1024 > MAX_RESULT_CHARS
    assert tools.tool_tier("vinc_doc_get") == "viewer"
