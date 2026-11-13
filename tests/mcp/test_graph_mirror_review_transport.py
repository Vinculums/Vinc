"""선택적 출처 검토가 기존 읽기 경로와 owner 검사를 유지하는지 검증한다."""
from types import SimpleNamespace

import pytest

from api.cli_tools import tool_request
from api import remote_client
from vinc_mcp import tools
from test_rest_v1 import Server, TEAM

MID = "concept:doc.sample"


class Bridge:
    owner = "user:a"

    def __init__(self):
        self.calls = []

    def request(self, method, path, params=None, **kwargs):
        self.calls.append((method, path, params))
        return {"kind": "graph", "executed": True, "found": True, "seed": params["seed"]}


def test_mcp_default_request_unchanged_and_opt_in_forwarded():
    b = Bridge()
    tools._t_graph({"seed_id": MID, "depth": 1}, b)
    assert b.calls[-1] == ("GET", "/graph", {"owner": "user:a", "seed": MID, "depth": 1})
    tools._t_graph({"seed_id": MID, "depth": 1, "review_mirror": True}, b)
    assert b.calls[-1][2]["review_mirror"] is True


@pytest.mark.parametrize("args", [
    {"seed_id": "concept:ordinary", "review_mirror": True},
    {"seed_id": "concept:doc.", "review_mirror": True},
    {"seed_id": MID, "review_mirror": "true"},
    {"url": "#view/ids/concept:doc.a,concept:doc.b", "review_mirror": True},
])
def test_wrong_seed_or_non_boolean_never_calls_bridge(args):
    b = Bridge()
    with pytest.raises(ValueError):
        tools._t_graph(args, b)
    assert not b.calls


def test_doc_url_keeps_existing_redirect_without_diagnostic():
    b = Bridge()
    out = tools._t_graph({"url": "#view/doc/sample", "review_mirror": True}, b)
    assert not out["executed"] and "vinc_doc_get" in out["note"]
    assert not b.calls


def test_rest_get_and_post_keep_viewer_read_scope_and_team_owner():
    srv = Server(role="viewer", scope="read")
    try:
        for method, path, body in [
            ("GET", f"/v1/nodes/concept%3Adoc.sample/graph?review_mirror=true&space={TEAM}", None),
            ("POST", f"/v1/graph?space={TEAM}",
             {"url": "#view/trace/" + MID, "review_mirror": True}),
        ]:
            status, _, _ = srv.request(method, path, body=body)
            assert status == 200
            assert srv.bridge.calls[-1][2]["owner"] == "team:" + TEAM
            assert srv.bridge.calls[-1][2]["review_mirror"] is True
        n = len(srv.bridge.calls)
        status, _, _ = srv.request("GET", "/v1/nodes/concept%3Aordinary/graph?review_mirror=true")
        assert status == 400 and len(srv.bridge.calls) == n
        status, _, _ = srv.request("GET", f"/v1/nodes/concept%3Adoc.sample/graph?review_mirror=true&space=bad")
        assert status == 404 and len(srv.bridge.calls) == n
        status, _, _ = srv.request("GET", "/v1/nodes/concept%3Adoc.sample/graph?review_mirror=true", key=None)
        assert status == 401 and len(srv.bridge.calls) == n
    finally:
        srv.close()


def test_cli_named_command_only_adds_flag_when_selected():
    args = SimpleNamespace(cmd="node", nodecmd="graph", id=MID, depth=1,
                           owner="user:a", review_mirror=False)
    name, data, _ = tool_request(args)
    assert name == "vinc_graph" and "review_mirror" not in data
    args.review_mirror = True
    assert tool_request(args)[1]["review_mirror"] is True


def test_cli_parser_accepts_named_review_flag(monkeypatch, capsys):
    from api import cli, cli_tools
    captured = []

    def run(args):
        captured.append(tool_request(args))
        return {"executed": True}, None

    monkeypatch.setattr(cli_tools, "run", run)
    assert cli.main(["--target", "local", "node", "graph", MID,
                     "--review-mirror", "--owner", "user:a"]) == 0
    assert captured[0][0] == "vinc_graph"
    assert captured[0][1]["review_mirror"] is True


def test_account_cli_uses_existing_rest_route_and_strips_supplied_owner(monkeypatch):
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return b'{"executed":true}'

    def urlopen(request, **kwargs):
        captured.append(request)
        return Response()

    monkeypatch.setattr(remote_client, "urlopen", urlopen)
    remote_client.call("vinc_graph", {"seed_id": MID, "depth": 1,
                       "review_mirror": True, "owner": "evil"},
                       space=TEAM, key="test-only-key", base_url="http://127.0.0.1:9999")
    assert "review_mirror=true" in captured[0].full_url
    assert "space=" + TEAM in captured[0].full_url
    assert "owner" not in captured[0].full_url
