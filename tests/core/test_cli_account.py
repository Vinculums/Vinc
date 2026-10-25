"""Account CLI target precedence, v1 routing, and failure contracts."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest

from api import cli, cli_target, remote_client
from api.cli_tools import _tool_request
from types import SimpleNamespace
from vinc_mcp import rest_routes, tools


class _Response:
    def __init__(self, data: dict):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def test_target_precedence(monkeypatch):
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {"mode": "account"})
    monkeypatch.setenv("VINC_TARGET", "local")
    assert cli_target.resolve_target("account") == "account"
    assert cli_target.resolve_target(None) == "local"
    monkeypatch.delenv("VINC_TARGET")
    assert cli_target.resolve_target(None) == "account"
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {})
    assert cli_target.resolve_target(None) == "local"
    monkeypatch.setenv("VINC_TARGET", "invalid")
    with pytest.raises(ValueError):
        cli_target.resolve_target(None)


def test_account_environment_url_and_space(monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_test")
    monkeypatch.setenv("VINC_API_URL", "https://custom.test/")
    monkeypatch.setenv("VINC_SPACE", "11111111-1111-1111-1111-111111111111")
    seen = []
    monkeypatch.setattr(remote_client, "urlopen",
                        lambda request, **_: (seen.append(request), _Response({"ok": True}))[1])
    assert cli_target.resolve_space(None) == "11111111-1111-1111-1111-111111111111"
    remote_client.call("vinc_whoami", {}, space=cli_target.resolve_space(None))
    assert seen[0].full_url == ("https://custom.test/v1/me?"
                                "space=11111111-1111-1111-1111-111111111111")


def test_every_public_tool_uses_a_v1_route(monkeypatch):
    names = {item["name"] for item in tools.TOOLS}
    assert len(names) == 37
    observed = []

    def receive(request, *, timeout):
        observed.append(request)
        assert timeout == 30
        return _Response({"kind": "tool", "ok": True})

    monkeypatch.setattr(remote_client, "urlopen", receive)
    schema = {item["name"]: item["inputSchema"].get("properties", {})
              for item in tools.TOOLS}
    def sample(name, field):
        kind = schema[name].get(field, {}).get("type")
        return (["x", "y"] if kind == "array" else True if kind == "boolean"
                else 2 if kind == "integer" else 0.5 if kind == "number"
                else "sample")
    for name in names:
        route = remote_client._route(name, {})
        args = {arg: "concept:test" for _, arg in route.path_args}
        args.update({field: sample(name, field)
                     for field in (*route.query, *route.body)})
        assert remote_client.call(name, args, key="vinc_sk_test",
                                  base_url="https://api.test", space="personal")["ok"]
        request = observed[-1]
        url = urlsplit(request.full_url)
        assert url.scheme == "https" and url.netloc == "api.test"
        assert url.path.startswith("/v1/") and "{" not in url.path
        assert request.get_method() == route.method
        assert request.get_header("Authorization") == "Bearer vinc_sk_test"
        want_query = {field: ([",".join(args[field])]
                               if isinstance(args[field], list) else
                               ["true" if args[field] is True else
                                "false" if args[field] is False else str(args[field])])
                      for field in route.query}
        assert parse_qs(url.query) == want_query
        if route.method in ("POST", "PUT"):
            assert json.loads(request.data) == {field: args[field] for field in route.body}
        else:
            assert request.data is None
    assert {route.tool for route in rest_routes.ROUTES} == names
    assert len(observed) == 37


def test_edge_batch_uses_the_read_post_route(monkeypatch):
    observed = []
    def receive(request, **_kw):
        observed.append(request)
        return _Response({"executed": True, "edges": []})
    monkeypatch.setattr(remote_client, "urlopen", receive)
    remote_client.call("vinc_edges", {"nodes": ["concept:a", "concept:b"], "dir": "out"},
                       key="vinc_ro_test", base_url="https://api.test")
    request = observed[0]
    assert request.full_url == "https://api.test/v1/edges/query"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"nodes": ["concept:a", "concept:b"], "dir": "out"}


def test_account_key_only_and_space_on_route(monkeypatch):
    monkeypatch.delenv("VINC_API_KEY", raising=False)
    monkeypatch.setenv("VINC_REMOTE_MCP_TOKEN", "legacy-credential")
    with pytest.raises(remote_client.RemoteFailure) as missing:
        remote_client.call("vinc_whoami", {})
    assert missing.value.exit_code == 4
    assert missing.value.result["error"]["code"] == "not_authenticated"

    seen = []
    monkeypatch.setattr(remote_client, "urlopen",
                        lambda request, **_: (seen.append(request), _Response({"ok": True}))[1])
    remote_client.call("vinc_neighbors", {"node": "concept:a/b", "limit": 2,
                                          "owner": "spoofed"},
                       key="vinc_ro_test", base_url="https://api.test",
                       space="11111111-1111-1111-1111-111111111111")
    assert "/v1/nodes/concept%3Aa%2Fb/neighbors?" in seen[0].full_url
    assert "space=11111111-1111-1111-1111-111111111111" in seen[0].full_url
    assert "owner" not in seen[0].full_url


def test_graph_seed_and_document_edit_take_path_routes(monkeypatch):
    seen = []
    monkeypatch.setattr(remote_client, "urlopen",
                        lambda request, **_: (seen.append(request), _Response({"ok": True}))[1])
    remote_client.call("vinc_graph", {"seed_id": "concept:a/b", "depth": 2},
                       key="vinc_sk_test", base_url="https://api.test")
    remote_client.call("vinc_doc_put", {"doc_id": "doc:a/b", "text": "body"},
                       key="vinc_sk_test", base_url="https://api.test")
    assert seen[0].get_method() == "GET"
    assert urlsplit(seen[0].full_url).path == "/v1/nodes/concept%3Aa%2Fb/graph"
    assert parse_qs(urlsplit(seen[0].full_url).query) == {"depth": ["2"]}
    assert seen[1].get_method() == "PUT"
    assert urlsplit(seen[1].full_url).path == "/v1/documents/doc%3Aa%2Fb"
    assert json.loads(seen[1].data) == {"text": "body"}


@pytest.mark.parametrize(("status", "expected"),
                         [(400, 1), (401, 4), (403, 1), (404, 1), (429, 6), (503, 5)])
def test_http_failure_exit_codes(monkeypatch, status, expected):
    def fail(request, **_):
        raise HTTPError(request.full_url, status, "failed", {},
                        io.BytesIO(b'{"error":{"code":"failed"}}'))
    monkeypatch.setattr(remote_client, "urlopen", fail)
    with pytest.raises(remote_client.RemoteFailure) as outcome:
        remote_client.call("vinc_whoami", {}, key="vinc_sk_test")
    assert outcome.value.exit_code == expected


def test_network_failure_is_exit_five(monkeypatch):
    monkeypatch.setattr(remote_client, "urlopen",
                        lambda *_args, **_kw: (_ for _ in ()).throw(URLError("offline")))
    with pytest.raises(remote_client.RemoteFailure) as outcome:
        remote_client.call("vinc_whoami", {}, key="vinc_sk_test")
    assert outcome.value.exit_code == 5


def test_account_cli_uses_v1_and_tool_result_shape(monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_test")
    calls = []
    def call(name, args, *, space):
        calls.append((name, args, space))
        return {"kind": "search", "rows": [{"id": "concept:x"}], "next_cursor": None}
    with patch("api.cli_account.call", side_effect=call):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["--target", "account", "--space",
                             "11111111-1111-1111-1111-111111111111",
                             "--format", "json", "search", "--query", "x"])
    assert code == 0
    assert json.loads(output.getvalue())["rows"][0]["id"] == "concept:x"
    assert calls == [("vinc_search", {"query": "x", "limit": 5,
                                       "cursor": None, "detail": "summary"},
                      "11111111-1111-1111-1111-111111111111")]


def test_call_json_space_is_used_unless_global_space_is_explicit(monkeypatch):
    monkeypatch.setenv("VINC_API_KEY", "vinc_sk_test")
    calls = []
    def call(_name, _args, *, space):
        calls.append(space)
        return {"kind": "whoami", "owner": "user:test"}
    with patch("api.cli_account.call", side_effect=call):
        cli.main(["--target", "account", "call", "vinc_whoami", "--args",
                  '{"space":"11111111-1111-1111-1111-111111111111"}'])
        cli.main(["--target", "account", "--space", "personal", "call",
                  "vinc_whoami", "--args",
                  '{"space":"11111111-1111-1111-1111-111111111111"}'])
    assert calls == ["11111111-1111-1111-1111-111111111111", "personal"]


def test_local_only_account_command_is_usage_error():
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(["--target", "account", "--format", "json", "plan", "search"])
    assert code == 2
    assert output.getvalue() == ""


def test_nine_existing_verbs_now_map_to_tool_shapes(monkeypatch):
    cases = [
        (dict(cmd="search", query="x", top_k=5, cursor=None, detail="summary"),
         "vinc_search"),
        (dict(cmd="recall", query="", tenant=None, workspace=None, rank="recency",
              tags=None, domain=None, limit=10), "vinc_recall"),
        (dict(cmd="brief", query="x", max_excerpts=3), "vinc_brief"),
        (dict(cmd="decide", topic="x", depth=2), "vinc_decide"),
        (dict(cmd="domains"), "vinc_domains"),
        (dict(cmd="doc", doccmd="get", id="doc:x"), "vinc_doc_get"),
        (dict(cmd="doc", doccmd="ls"), "vinc_doc_list"),
        (dict(cmd="doc", doccmd="put", file=None, id="doc:x", title="X",
              origin="test"), "vinc_doc_put"),
        (dict(cmd="doc", doccmd="rm", id="doc:x"), "vinc_doc_delete"),
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("document body"))
    for flags, expected in cases:
        name, data, _guard = _tool_request(SimpleNamespace(owner=None, **flags))
        assert name == expected
        assert "owner" not in data
        if name == "vinc_doc_put":
            assert data["text"] == "document body"
