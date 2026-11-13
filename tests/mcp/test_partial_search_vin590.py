"""VIN-590 B9 — 청크 팔만 실패하면 REST /v1/search 와 호스트 search 가 노드 후보를 버리지 않는다.

vinc_search(MCP)와 vinc_brief 는 같은 실패에서 node_hits 를 이미 지킨다. 노드 후보도 없을
때만 실패다. 인자 오류(invalid_*·stale_cursor)는 노드 팔과 무관하게 실패로 남는다.
"""
from __future__ import annotations

import pytest

from tests.mcp.test_rest_v1 import Server
from vinc_mcp import rest_api, toolcall, tools

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


@pytest.mark.parametrize("error", ["store_busy", "invalid_cursor: x", "stale_cursor: changed"])
def test_host_search_argument_and_maintenance_errors_stay_failures(error):
    """노드 후보가 있어도 유지보수 거절과 인자 오류는 도구 오류다. REST 와 같은 규칙이다."""
    with pytest.raises(RuntimeError, match="search_unavailable"):
        tools._t_host_search({"query": "alpha"}, _Bridge(chunk_down(error=error)))


SHAPES = {
    "chunk_down": chunk_down(),
    "chunk_down_no_hits": chunk_down(node_hits=[]),
    "store_busy": chunk_down(error="store_busy"),
    "store_busy_sidecar": {"executed": False, "error": "store_busy", "retry_after_s": 1},
    "invalid_cursor": chunk_down(error="invalid_cursor: x"),
    "stale_cursor": chunk_down(error="stale_cursor: changed"),
    "sidecar_5xx": chunk_down(error="sidecar HTTP 503: unavailable"),
    "error_but_executed": chunk_down(executed=True),
}


@pytest.mark.parametrize("label", sorted(SHAPES))
def test_rest_and_host_search_salvage_the_same_failures(label):
    """`error` 가 실린 실패는 REST /v1/search 와 호스트 search 가 같은 것을 살리고 같은 것을 버린다."""
    shape = SHAPES[label]
    rest = rest_api._partial_search("vinc_search", dict(shape))
    rest_kept = "error" not in rest
    try:
        host = tools._t_host_search({"query": "alpha"}, _Bridge(dict(shape)))
    except RuntimeError as exc:
        assert str(exc) == "search_unavailable"
        host_kept = False
    else:
        host_kept = True
        assert host["diagnostics"]["search_error"] == rest["search_error"]
    assert rest_kept == host_kept == (toolcall.partial_search(dict(shape)) is not None)


def test_partial_search_leaves_successes_and_non_dicts_alone():
    ok = {"kind": "search", "executed": True, "rows": [], "node_hits": list(HITS)}
    assert toolcall.partial_search(ok) is None
    assert toolcall.partial_search([HITS]) is None
    assert toolcall.partial_search(None) is None
    quiet = {"kind": "search", "executed": False, "rows": [], "node_hits": list(HITS)}
    assert toolcall.partial_search(quiet)["search_error"] == "chunk search did not run"
