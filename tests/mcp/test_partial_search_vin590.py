"""VIN-590 B9 — 청크 팔만 실패하면 REST /v1/search 와 호스트 search 가 노드 후보를 버리지 않는다.

vinc_search(MCP)와 vinc_brief 는 같은 실패에서 node_hits 를 이미 지킨다. 노드 후보도 없을
때만 실패다. 인자 오류(invalid_*·stale_cursor)는 노드 팔과 무관하게 실패로 남는다.
"""
from __future__ import annotations

import pytest

from tests.mcp.test_rest_v1 import Server
from vinc_mcp import rest_api, tools

HITS = [{"id": "concept:alpha", "title": "Alpha", "kind": "concept"}]
CHUNK_DOWN = "extension_unavailable: RuntimeError: fts extension unavailable"


def chunk_down(**extra):
    return {"kind": "search", "executed": False, "error": CHUNK_DOWN, "note": CHUNK_DOWN,
            "rows": [], "node_hits": list(HITS), "arms": "none", **extra}


def test_rest_search_keeps_node_hits_and_reports_the_chunk_failure():
    srv = Server()
    try:
        srv.bridge.override = chunk_down()
        status, _, body = srv.request("POST", "/v1/search", body={"query": "alpha"})
        assert status == 200, body
        assert body["node_hits"] == HITS and body["search_error"] == CHUNK_DOWN
        assert "error" not in body and body["executed"] is True
        srv.bridge.override = chunk_down(node_hits=[])
        status, _, body = srv.request("POST", "/v1/search", body={"query": "alpha"})
        assert status >= 500 and body["error"]["code"] in ("upstream_unavailable", "internal")
    finally:
        srv.close()


@pytest.mark.parametrize("error", ["invalid_cursor: x", "stale_cursor: changed", "store_busy"])
def test_rest_search_argument_and_maintenance_errors_stay_failures(error):
    res = rest_api._partial_search("vinc_search", chunk_down(error=error))
    assert res["error"] == error and "search_error" not in res


def test_partial_search_leaves_other_tools_alone():
    res = chunk_down()
    assert rest_api._partial_search("vinc_brief", res) is res


class _Bridge:
    owner = "user:synthetic"

    def __init__(self, result):
        self.result = result

    def request(self, *args, **kwargs):
        return self.result


def test_host_search_answers_with_node_hits_when_only_chunks_failed():
    out = tools._t_host_search({"query": "alpha"}, _Bridge(chunk_down()))
    assert [r["id"] for r in out["results"]] == ["concept:alpha"]
    assert out["diagnostics"]["search_error"] == CHUNK_DOWN


def test_host_search_still_fails_without_any_hits():
    with pytest.raises(RuntimeError, match="search_unavailable"):
        tools._t_host_search({"query": "alpha"}, _Bridge(chunk_down(node_hits=[])))
