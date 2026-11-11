"""vinc_search·vinc_brief 의 `kinds` 가 MCP 와 REST v1 에서 사이드카까지 그대로 간다.
모르는 종류는 사이드카에 닿기 전에 거절하고, 인자가 없으면 사이드카 매개변수도 예전 그대로다."""
from __future__ import annotations

import pytest

from tests.mcp.test_rest_v1 import Server
from vinc_mcp import tools


class _Bridge:
    owner = "user:local"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, body=None, **_kw):
        self.calls.append((path, params))
        return {"executed": True, "rows": [], "node_hits": [], "status": "none"}


@pytest.mark.parametrize("tool,path", [("vinc_search", "/search"), ("vinc_brief", "/brief")])
def test_mcp_forwards_kinds_as_a_comma_list(tool, path):
    br = _Bridge()
    tools.call(tool, {"query": "decisions about x", "kinds": ["decision", "episode"]}, br)
    assert br.calls[-1][0] == path
    assert br.calls[-1][1]["kinds"] == "decision,episode"


@pytest.mark.parametrize("tool", ["vinc_search", "vinc_brief"])
def test_without_kinds_the_sidecar_params_are_unchanged(tool):
    br = _Bridge()
    tools.call(tool, {"query": "x"}, br)
    assert br.calls[-1][1].get("kinds") is None


@pytest.mark.parametrize("bad", [["bridge"], "decision", ["decision", 3]])
def test_unknown_or_malformed_kinds_are_refused_before_the_sidecar(bad):
    with pytest.raises(tools.InvalidArgument) as err:
        tools.validate_args("vinc_search", {"query": "x", "kinds": bad})
    assert err.value.meta["argument"] == "kinds"


def test_rest_v1_search_and_brief_carry_kinds():
    srv = Server()
    try:
        status, _, _ = srv.request("POST", "/v1/search", body={"query": "x", "kinds": ["record"]})
        assert status == 200 and srv.bridge.calls[-1][2]["kinds"] == "record"
        status, _, _ = srv.request("POST", "/v1/brief", body={"query": "x", "kinds": ["episode"]})
        assert status == 200 and srv.bridge.calls[-1][2]["kinds"] == "episode"
        n = len(srv.bridge.calls)
        status, _, body = srv.request("POST", "/v1/search", body={"query": "x", "kinds": ["note"]})
        assert status == 400 and body["error"]["code"] == "invalid_argument"
        assert len(srv.bridge.calls) == n
    finally:
        srv.close()
