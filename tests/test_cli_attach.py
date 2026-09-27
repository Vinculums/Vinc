"""A local CLI query reads through the writer already holding its store."""
from __future__ import annotations

import contextlib
import io
import json
import os
import threading
from argparse import Namespace

from api import cli
from api.cli_transport import _finish_job, attached_bridge
from api.auth import SessionAuth
from api.server import VincApp, make_server
from store.config import load_config
from vinc_mcp import bridge as bridge_module


def _run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


def test_matching_live_sidecar_answers_a_locked_store(tmp_path, monkeypatch):
    data_file = str(tmp_path / "db")
    token = "test-cli-attach-token"
    app = VincApp.open(load_config({"data_dir": data_file}), auth=SessionAuth(token))
    httpd = make_server(app, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps({"port": httpd.server_address[1],
                                        "token": token, "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(bridge_module, "_session_path", lambda: str(session_path))
    monkeypatch.setenv("MEMORY_DATA_DIR", data_file)
    source = tmp_path / "sample.md"
    source.write_text("# Sample\n\nOne line.\n", encoding="utf-8")
    cases = [["doc", "ls", "--data", data_file],
             ["doc", "get", "--id", "sample", "--owner", "user:local", "--data", data_file],
             ["domains"], ["recall", "--query", "nothing", "--owner", "user:local"],
             ["graph", "--owner", "user:local"], ["decide", "--topic", "nothing"]]
    try:
        assert app.store is not None
        put_code, put_bytes = _run_cli(["doc", "put", "--id", "sample", "--owner", "user:local",
                                        "--file", str(source), "--data", data_file,
                                        "--format", "json"])
        assert put_code == 0
        assert json.loads(put_bytes)["executed"] is True
        answers = [_run_cli([*case, "--format", "json"]) for case in cases]
        attached_code, attached_bytes = answers[0]
        assert attached_code == 0
        assert json.loads(attached_bytes)["kind"] == "doc.list"
        assert attached_bridge(Namespace(cmd="doc", data=str(tmp_path / "other"))) is None
        session_path.write_text(json.dumps({"port": httpd.server_address[1],
                                            "token": "wrong", "pid": os.getpid()}), encoding="utf-8")
        locked_code, locked_bytes = _run_cli(["doc", "ls", "--data", data_file,
                                              "--format", "json"])
        assert locked_code == 1
        assert json.loads(locked_bytes)["error"]["code"] == "store_in_use"
        recall_code, recall_bytes = _run_cli(["recall", "--query", "nothing",
                                              "--owner", "user:local", "--format", "json"])
        assert recall_code == 1
        assert json.loads(recall_bytes)["error"]["code"] == "store_in_use"
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()
    local_answers = [_run_cli([*case, "--format", "json"]) for case in cases]
    assert local_answers == answers


def test_async_route_prints_the_terminal_result(monkeypatch):
    monkeypatch.setattr("api.cli_transport.time.sleep", lambda _: None)

    class _Bridge:
        def __init__(self):
            self.calls = 0
        def request(self, method, path, **kwargs):
            assert method == "GET" and path == "/jobs/job-1"
            self.calls += 1
            return ({"status": "running"} if self.calls == 1 else
                    {"status": "done", "result": {"kind": "backup", "executed": True}})

    bridge = _Bridge()
    assert _finish_job(bridge, {"job_id": "job-1", "status": "queued"}) == {
        "kind": "backup", "executed": True}
    assert bridge.calls == 2
