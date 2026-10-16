"""`vinc edges` · `vinc role get|check` 가 MCP 도구와 같은 인자·결과·출력 계약을 쓴다."""
from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from api import cli, cli_output, fragment
from api.cli_tools import _tool_request
from api.server import VincApp
from store.config import load_config


def run(*argv, stdin=None, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *argv])
    text = out.getvalue() if code == 0 else err.getvalue()
    lines = text.splitlines(True)
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    if code:
        assert out.getvalue() == ""
    return code, json.loads("".join(lines[start:]))


def _edges(**flags):
    base = {"cmd": "edges", "owner": None, "id": None, "nodes": None,
            "type": None, "dir": None, "limit": None}
    return SimpleNamespace(**{**base, **flags})


def _check(tmp_path, **flags):
    output = tmp_path / "output.txt"
    output.write_text("Final answer.\n", encoding="utf-8")
    base = {"cmd": "role", "rolecmd": "check", "owner": None, "id": "agent-dev-core",
            "output": str(output), "person_only_requests": None, "self_authored": None}
    return SimpleNamespace(**{**base, **flags})


def test_flag_mapping_sends_only_given_options(tmp_path):
    assert _tool_request(_edges(id="concept:a")) == ("vinc_edges", {"node": "concept:a"}, None)
    assert _tool_request(_edges(nodes=["concept:a", "concept:b"], type="RELATES_TO",
                                dir="out", limit=5)) == (
        "vinc_edges", {"nodes": ["concept:a", "concept:b"], "type": "RELATES_TO",
                       "dir": "out", "limit": 5}, None)
    assert _tool_request(SimpleNamespace(cmd="role", rolecmd="get", owner=None,
                                         id="agent-dev-core")) == (
        "vinc_role", {"role": "agent-dev-core"}, None)
    assert _tool_request(_check(tmp_path)) == (
        "vinc_role_check", {"role": "agent-dev-core", "output": "Final answer.\n"}, None)
    requests = [{"action": "merge", "reason": "needs a person"}]
    name, data, guard = _tool_request(_check(
        tmp_path, person_only_requests=json.dumps(requests), self_authored=True))
    assert (name, guard) == ("vinc_role_check", None)
    assert data["person_only_requests"] == requests and data["self_authored"] is True


@pytest.mark.parametrize("argv,needle", [
    (["edges"], "either node or a nodes array"),
    (["edges", "concept:a", "--nodes", "concept:b"], "either node or a nodes array"),
    (["role", "check", "agent-dev-core", "--output", "missing-file.txt"], "--output"),
    (["role", "check", "agent-dev-core", "--output", "-", "--person-only-requests", "{}"],
     "expected a JSON array"),
    (["role", "check", "agent-dev-core", "--output", "-", "--person-only-requests", "["],
     "--person-only-requests"),
])
def test_usage_errors_are_exit_two_on_stderr(monkeypatch, tmp_path, argv, needle):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VINC_TARGET", "local")
    monkeypatch.setattr("api.cli_tools._bridge", lambda args: pytest.fail("no store call"))
    code, payload = run(*argv, stdin="Final answer.", monkeypatch=monkeypatch)
    assert code == 2 and payload["error"]["code"] == "invalid_argument"
    assert needle in payload["error"]["message"]


def test_account_routes_follow_the_v1_contract(monkeypatch, tmp_path):
    seen = []

    class Capture(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def answer(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.command, self.path, json.loads(body) if body else None))
            if "/roles/" in self.path and "missing" in self.path:
                payload = {"kind": "role", "executed": True, "found": False}
            elif "/check" in self.path:
                payload = {"kind": "role_check", "executed": True, "found": True,
                           "role": "concept:agent-dev-core", "verdict": "block",
                           "failed_checks": ["side_effect_evidence"],
                           "person_only_actions": [], "evidence_sentences": ["Tests passed."]}
            elif "/roles/" in self.path:
                payload = {"kind": "role", "executed": True, "found": True,
                           "id": "concept:agent-dev-core", "rendered": "contract"}
            else:
                payload = {"kind": "edges", "executed": True, "edges": [], "count": 0}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("VINC_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("VINC_API_KEY", "vinc_ro_capture")
    monkeypatch.setenv("VINC_TARGET", "account")
    try:
        assert run("edges", "domain:vinc/agent", "--dir", "out", "--limit", "3")[0] == 0
        assert seen[-1] == ("GET", "/v1/nodes/domain%3Avinc%2Fagent/edges?dir=out&limit=3", None)
        assert run("edges", "--nodes", "concept:a", "concept:b", "--space", "team-1")[0] == 0
        assert seen[-1] == ("POST", "/v1/edges/query?space=team-1",
                            {"nodes": ["concept:a", "concept:b"]})
        code, payload = run("role", "get", "agent-dev-core")
        assert code == 0 and payload["rendered"] == "contract"
        assert seen[-1] == ("GET", "/v1/roles/agent-dev-core", None)
        code, payload = run("role", "get", "missing")
        assert code == 1 and payload["error"] == {"code": "not_found",
                                                  "message": "Role not found: missing"}
        code, payload = run("role", "check", "agent-dev-core", "--output", "-",
                            "--self-authored", stdin="Tests passed.\n", monkeypatch=monkeypatch)
        assert code == 1 and payload["verdict"] == "block"
        assert seen[-1] == ("POST", "/v1/roles/agent-dev-core/check",
                            {"output": "Tests passed.\n", "self_authored": True})
        # 명시한 --target 이 VINC_TARGET 보다 앞선다.
        monkeypatch.setattr("api.cli_tools._bridge", lambda args: pytest.fail("local store"))
        monkeypatch.setenv("VINC_TARGET", "local")
        assert run("--target", "account", "edges", "concept:a")[0] == 0
        assert seen[-1][1] == "/v1/nodes/concept%3Aa/edges"
    finally:
        server.shutdown()
        server.server_close()


def test_local_edges_read_the_store_and_name_a_missing_node(tmp_path, monkeypatch):
    data = str(tmp_path / "db")
    monkeypatch.setenv("MEMORY_DATA_DIR", data)
    monkeypatch.setenv("VINC_TARGET", "local")
    monkeypatch.setattr("api.cli_tools.attached_bridge", lambda args: None)
    app = VincApp.open(load_config({"data_dir": data}))
    try:
        result = fragment.merge_fragment({"owner": "tenant:local", "nodes": [
            {"id": "concept:alpha", "type": "concept", "title": "Alpha", "domain": "vinc/test"},
            {"id": "concept:beta", "type": "concept", "title": "Beta", "domain": "vinc/test"}],
            "edges": [{"from": "concept:alpha", "to": "concept:beta", "type": "relates_to"}]},
            store=app.store)
        assert result["executed"], result
    finally:
        app.close()
    code, payload = run("edges", "concept:alpha", "--dir", "out")
    assert code == 0, payload
    assert [(e["from"], e["to"]) for e in payload["edges"] if not e["derived"]] == [
        ("concept:alpha", "concept:beta")]
    code, payload = run("edges", "concept:alpha", "--dir", "in")
    assert code == 0 and [e for e in payload["edges"] if not e["derived"]] == []
    code, payload = run("edges", "--nodes", "concept:alpha", "concept:gone")
    assert code == 0 and payload["missing_nodes"] == ["concept:gone"]
    code, payload = run("edges", "concept:gone")
    assert code == 1 and payload["error"]["code"] == "not_found"
    code, payload = run("role", "get", "agent-nobody")
    assert code == 1 and payload["error"]["message"] == "Role not found: agent-nobody"


EDGE = {"from": "concept:a", "to": "concept:b", "type": "RELATES_TO", "evidence": None,
        "confidence": 0.8, "derived": False}
HUMAN = [
    ("edges", {"kind": "edges", "executed": True, "node": "concept:a", "found": True,
               "edges": [EDGE, {**EDGE, "type": "TYPED", "subtype": "depends_on",
                                "confidence": None}],
               "count": 3, "edges_truncated": True, "missing_nodes": ["concept:x"]},
     "edges: 2; count: 3\nconcept:a -> concept:b\tRELATES_TO\t0.8\n"
     "concept:a -> concept:b\tTYPED/depends_on\n... 1 more; raise --limit\nmissing: concept:x\n"),
    ("role get", {"kind": "role", "executed": True, "found": True, "id": "concept:agent-x",
                  "title": "Agent X", "domain": "vinc/agent", "rendered": "```\nrole\n```"},
     "concept:agent-x\tAgent X\tvinc/agent\n```\nrole\n```\n"),
    ("role check", {"kind": "role_check", "executed": True, "found": True, "verdict": "pass",
                    "failed_checks": [], "person_only_actions": [], "evidence_sentences": [],
                    "note": "pass releases this output only."},
     "verdict: pass\nnote: pass releases this output only.\n"),
]


@pytest.mark.parametrize("command,result,expected", HUMAN, ids=[c[0] for c in HUMAN])
def test_human_views_and_json_bytes(capsys, command, result, expected):
    cli_output.configure("human", command)
    assert cli_output.emit(result) == 0
    got = capsys.readouterr()
    assert got.out == expected and got.err == ""
    cli_output.configure("json", command)
    assert cli_output.emit(result) == 0
    assert capsys.readouterr().out == json.dumps(result, ensure_ascii=False, indent=2) + "\n"


def test_edges_ndjson_is_one_edge_per_line(capsys):
    cli_output.configure("ndjson", "edges")
    assert cli_output.emit({"kind": "edges", "executed": True, "edges": [EDGE, EDGE],
                            "count": 2}) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [EDGE, EDGE]


@pytest.mark.parametrize("verdict", ["block", "interrupt"])
def test_role_check_without_pass_fails_with_its_verdict_on_stderr(capsys, verdict):
    cli_output.configure("human", "role check")
    assert cli_output.emit({"kind": "role_check", "executed": True, "found": True,
                            "verdict": verdict, "failed_checks": ["person_only"],
                            "person_only_actions": ["merge"],
                            "evidence_sentences": ["I merged it."]}) == 1
    got = capsys.readouterr()
    assert got.out == ""
    assert got.err == (f"vinc: verdict: {verdict}\nfailed: person_only\nperson only: merge\n"
                       "evidence: I merged it.\n")
