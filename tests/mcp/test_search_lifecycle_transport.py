"""검색 상태와 시간 옵션이 MCP, REST, 계정 CLI에서 동일하게 전달된다."""
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from api import cli
from vinc_mcp import tools
from tests.mcp.test_rest_v1 import Server, TEAM


RESULT = {"kind": "search", "executed": True, "rows": [], "node_hits": [],
          "arms": "fts+vector", "total_count": 0, "has_more": False, "next_cursor": None,
          "node_error": "RuntimeError: node_vector_query_failed",
          "node_semantic": {"state": "degraded", "error": "node_vector_query_failed",
                            "freshness": "not_checked", "scope": "returned_candidates"},
          "node_window": {"pagination": "first_page_only", "total_count_scope": "chunks",
                          "returned": 0, "lexical_candidates": 0}}


def test_mcp_rest_account_cli_preserve_node_degradation_and_temporal_arguments():
    server = Server(role="viewer", scope="read")
    args = {"query": "synthetic", "mode": "historical", "as_of": "2026-10", "known_at": "2026"}
    try:
        server.bridge.override = RESULT
        status, _, rest = server.request("POST", f"/v1/search?space={TEAM}", body=args)
        assert status == 200 and rest == RESULT
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "vinc_search", "arguments": {**args, "space": TEAM}}}
        status, _, mcp = server.request("POST", "/mcp", body=rpc)
        assert status == 200 and json.loads(mcp["result"]["content"][0]["text"]) == RESULT
        out = io.StringIO()
        with patch.dict("os.environ", {"VINC_API_KEY": "vinc_sk_good", "VINC_API_URL": server.base}), \
                redirect_stdout(out), patch.object(server.server.RequestHandlerClass, "log_message",
                                                  lambda *args: None):
            assert cli.main(["--target", "account", "--space", TEAM, "call", "vinc_search",
                             "--args", json.dumps(args)]) == 0
        assert json.loads(out.getvalue()) == RESULT
        for _, _, params, _ in server.bridge.calls:
            assert params["owner"] == "team:" + TEAM
            assert all(params[k] == args[k] for k in ("mode", "as_of", "known_at"))
    finally:
        server.close()


def test_repository_search_schema_exposes_all_temporal_options():
    spec = next(t for t in tools.TOOLS if t["name"] == "vinc_search")
    assert {"historical", "mode", "as_of", "known_at"} <= set(spec["inputSchema"]["properties"])
    assert "node_semantic" in spec["description"] and "node_window" in spec["description"]


def test_host_search_does_not_turn_backend_failure_into_successful_empty_results():
    class Bridge:
        owner = "user:synthetic"
        def request(self, *args, **kwargs):
            return {"executed": False, "error": "index_missing"}
    import pytest
    with pytest.raises(RuntimeError, match="search_unavailable"):
        tools._t_host_search({"query": "synthetic"}, Bridge())


def test_host_search_preserves_degradation_and_temporal_options():
    class Bridge:
        owner = "user:synthetic"
        def request(self, method, path, params):
            assert params["mode"] == "current" and params["known_at"] == "2026"
            return RESULT
    result = tools._t_host_search({"query": "synthetic", "mode": "current", "known_at": "2026"}, Bridge())
    assert result["diagnostics"]["node_semantic"]["state"] == "degraded"
