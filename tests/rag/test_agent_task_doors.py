"""A2 의 다른 문(REST v1·CLI) — 로컬 HTTP 흉내와 실행 함수 흉내로 본다."""
from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rag_eval.agent_tasks import arms, doors, hosts, metrics, models, record


class _Stub(BaseHTTPRequestHandler):
    seen: list = []

    def _answer(self):
        size = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(size).decode() if size else ""
        _Stub.seen.append({"method": self.command, "path": self.path, "body": body,
                           "auth": self.headers.get("Authorization")})
        code = 404 if "missing" in self.path else 200
        data = json.dumps({"kind": "node", "found": code == 200}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _answer

    def log_message(self, *a):
        pass


@pytest.fixture()
def v1():
    _Stub.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_the_rest_door_sends_one_request_to_v1_with_the_read_key(v1):
    door = doors.RestDoor(v1, "vinc_sk_eval_x")
    got = door.call("vinc_http", {"method": "post", "path": "/v1/search", "body": {"query": "rag"}})
    assert got["isError"] is False and got["content"][0]["text"].startswith("HTTP 200\n")
    door.call("vinc_http", {"method": "GET", "path": "/v1/nodes/decision:a/neighbors",
                            "query": {"limit": 5, "historical": True}})
    first, second = _Stub.seen
    assert (first["method"], first["path"], json.loads(first["body"])) == ("POST", "/v1/search", {"query": "rag"})
    assert first["auth"] == "Bearer vinc_sk_eval_x"
    assert second["path"] == "/v1/nodes/decision:a/neighbors?limit=5&historical=true"
    missing = door.call("vinc_http", {"method": "GET", "path": "/v1/nodes/missing"})
    assert missing["isError"] and missing["content"][0]["text"].startswith("HTTP 404")


def test_the_rest_door_stays_inside_v1_and_its_one_tool(v1):
    door = doors.RestDoor(v1, "k")
    for args in ({"method": "GET", "path": "/mcp"}, {"method": "GET", "path": "/v1/../mcp"},
                 {"method": "TRACE", "path": "/v1/me"}):
        assert door.call("vinc_http", args)["isError"], args
    assert door.call("vinc_search", {"query": "x"})["isError"]
    assert _Stub.seen == []
    down = doors.RestDoor("http://127.0.0.1:9", "k").call("vinc_http", {"method": "GET", "path": "/v1/me"})
    assert down["isError"] and down.get("_transport_error") and record.classify(down)


def test_the_route_table_names_every_route_with_its_tool_description():
    from vinc_mcp import rest_routes
    tools = [{"name": "vinc_search", "description": "Search this graph. Longer text here."}]
    manual = doors.RestDoor("http://x", "k").manual(tools)
    assert "- POST /v1/search; body: query, limit" in manual and ": Search this graph." in manual
    assert "Longer text" not in manual and "—" not in manual
    assert len([line for line in manual.splitlines() if line.startswith("- ")]) == len(rest_routes.ROUTES)


class FakeRun:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, command, **kw):
        self.calls.append({"command": command, **kw})
        out = self.results.pop(0)
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(command, out[0], stdout=out[1], stderr=out[2])


def test_the_cli_door_runs_the_product_cli_against_the_local_v1_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-the-cli")
    run = FakeRun((0, '{"kind": "search", "rows": []}', ""), (1, "", "error: not found"))
    door = doors.CliDoor("http://127.0.0.1:5/", "vinc_sk_eval_y", run=run)
    ok = door.call("vinc", {"command": 'vinc search --query "release checklist"'})
    assert ok == {"content": [{"type": "text", "text": '{"kind": "search", "rows": []}'}], "isError": False}
    call = run.calls[0]
    assert call["command"][-3:] == ["search", "--query", "release checklist"]
    env = call["env"]
    assert (env["VINC_TARGET"], env["VINC_API_URL"], env["VINC_API_KEY"]) == (
        "account", "http://127.0.0.1:5", "vinc_sk_eval_y")
    assert "ANTHROPIC_API_KEY" not in env and env["HOME"] == door.home == call["cwd"]
    failed = door.call("vinc", {"command": "node get decision:nope"})
    assert failed["isError"] and failed["content"][0]["text"] == "error: not found"


def test_the_cli_door_refuses_local_verbs_target_changes_and_file_reads():
    run = FakeRun(subprocess.TimeoutExpired("vinc", 1))
    door = doors.CliDoor("http://127.0.0.1:5", "k", run=run)
    for command in ("serve", "backup", "search --query x --target local", "doc get --id x --data /tmp/db",
                    "call vinc_search --args-file q.json", "delete decision:x --yes", "", 'search --query "x'):
        assert door.call("vinc", {"command": command})["isError"], command
    assert run.calls == []
    slow = door.call("vinc", {"command": "search --query x"})
    assert slow["isError"] and slow.get("_transport_error")
    assert door.call("vinc_search", {"query": "x"})["isError"]


def test_the_cli_manual_lists_tool_verbs_and_common_usage_from_the_real_help():
    manual = doors.CliDoor("http://127.0.0.1:5", "k").manual()
    assert "- search: hybrid vector+BM25 search" in manual and "- serve" not in manual
    assert "vinc search --query QUERY" in manual and "vinc node get" in manual
    assert "--target" not in manual and "--format" not in manual


class Door:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return {"content": [{"type": "text", "text": "rows: decision:a"}]}

    def tools(self):
        return [doors.CLI_TOOL]


def test_a_door_surface_takes_the_tool_calls_instead_of_mcp():
    class NoMcp:
        def call(self, *a):
            raise AssertionError("the door arm must not reach MCP")
    door = Door()
    surface = arms.Surface(arm="A2-cli", mcp=NoMcp(), door=door, tools=door.tools(),
                           context="Vinc command line.")
    trace = record.Trace()
    out = hosts.McpHost().run(prompt="where is the rag thing", system="sys", surface=surface,
                              model=models.FakeModel(), trace=trace, max_steps=4, cap=4)
    assert door.calls == [("vinc", {"command": "search --query 'where is the rag thing' --top-k 5"})]
    model = next(e for e in trace.events if e["t"] == "model")
    assert model["tools"] == ["vinc"] and model["input"][0]["content"].endswith("Vinc command line.")
    assert out.final_text.endswith("Sources: [decision:a]")


def test_the_fake_model_calls_the_rest_door_with_a_search_request():
    turn = models.FakeModel().step([{"role": "user", "content": "q"}], [doors.HTTP_TOOL])
    assert turn.tool_calls[0].args == {"method": "POST", "path": "/v1/search", "body": {"query": "q", "limit": 5}}


def test_door_arms_are_known_and_paired_against_mcp():
    assert set(doors.DOOR_ARMS) == {"A2-rest", "A2-cli"}
    assert ("A2-rest", "A2") in metrics.HEADLINE and ("A2-cli", "A2") in metrics.HEADLINE
    assert doors.describe(Door(), "m")["tools"] == ["vinc"]
    with pytest.raises(ValueError):
        doors.build("A2", "http://x", "k")
