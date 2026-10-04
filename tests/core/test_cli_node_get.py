"""Exact CLI reads preserve IDs across account, attached and in-process transports."""
from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api import cli, fragment
from api.auth import SessionAuth
from api.server import VincApp, make_server
from store.config import load_config
from vinc_mcp import bridge as bridge_module


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *argv])
    text = out.getvalue() if code == 0 else err.getvalue()
    start = next(i for i, line in enumerate(text.splitlines(True)) if line.startswith("{"))
    payload = json.loads("".join(text.splitlines(True)[start:]))
    if code:
        assert out.getvalue() == ""
    return code, payload


def test_account_exact_route_and_explicit_brief_capture(monkeypatch):
    seen = []

    class Capture(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def answer(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.command, self.path, body))
            missing = "missing" in self.path
            payload = ({"error": {"code": "not_found", "message": "Missing"}} if missing else
                       {"kind": "node", "executed": True, "found": True,
                        "node": {"id": "domain:vinc/agent", "kind": "domain"}})
            data = json.dumps(payload).encode()
            self.send_response(404 if missing else 200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = answer

    server = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("VINC_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("VINC_API_KEY", "vinc_ro_capture")
    try:
        assert run("--target", "account", "node", "get", "domain:vinc/agent")[0] == 0
        assert seen[-1] == ("GET", "/v1/nodes/domain%3Avinc%2Fagent", b"")
        for kind in ("domain", "concept", "record", "decision", "episode"):
            code, payload = run("--target", "account", "node", "get", f"{kind}:missing")
            assert code == 1 and payload["error"]["code"] == "not_found"
            assert seen[-1] == ("GET", f"/v1/nodes/{kind}%3Amissing", b"")
        run("--target", "account", "call", "vinc_brief", "--args",
            '{"query":"domain:vinc/agent"}')
        assert seen[-1] == ("POST", "/v1/brief", b'{"query": "domain:vinc/agent"}')
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("attached", [False, True])
def test_local_exact_reads_and_missing_kinds(tmp_path, monkeypatch, attached):
    data = str(tmp_path / "db")
    monkeypatch.setenv("MEMORY_DATA_DIR", data)
    token = "cli-node-fixture"
    app = VincApp.open(load_config({"data_dir": data}), auth=SessionAuth(token))
    assert app.store is not None
    result = fragment.merge_fragment({"owner": "tenant:local", "nodes": [
        {"id": "concept:fixture", "type": "concept", "title": "Fixture",
         "domain": "vinc/agent/roles", "props": {"fixture": True}},
        {"id": "concept:private", "type": "concept", "title": "Private",
         "domain": "vinc/private"}]}, store=app.store)
    assert result["executed"]
    server = None
    session = tmp_path / "session.json"
    monkeypatch.setattr(bridge_module, "_session_path", lambda: str(session))
    if attached:
        server = make_server(app, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        session.write_text(json.dumps({"port": server.server_address[1], "token": token}))
    else:
        app.close()
    try:
        base = ["--target", "local", "node", "get"]
        code, payload = run(*base, "domain:vinc/agent")
        assert code == 0, payload
        assert payload["node"] == {"id": "domain:vinc/agent", "kind": "domain", "name": "vinc/agent"}
        code, payload = run(*base, "concept:fixture")
        assert code == 0 and payload["node"]["props"] == {"fixture": True}
        for kind in ("domain", "concept", "record", "decision", "episode"):
            code, payload = run(*base, f"{kind}:missing")
            assert code == 1 and payload["error"]["code"] == "not_found"
        code, payload = run(*base, "domain:vinc/agent", "--owner", "user:other")
        assert code == 1 and payload["error"]["code"] == "not_found"
    finally:
        if server:
            server.shutdown()
            server.server_close()
            app.close()
