"""Transport traces disclose routing while preserving output and credential secrecy."""
import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from urllib.error import HTTPError

import pytest

from api import cli, remote_client
from api.auth import SessionAuth
from api.server import VincApp, make_server
from store.config import load_config
from vinc_mcp import bridge as bridge_module


def invoke(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *argv])
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("status", [200, 404, 401, 429, 503])
def test_account_debug_preserves_streams_and_hides_credentials(monkeypatch, status):
    secret = "vinc_ro_private-key-do-not-log"
    monkeypatch.setenv("VINC_API_KEY", secret)
    result = ({"kind": "node", "executed": True, "found": True, "node": {"id": "domain:vinc/agent"}}
              if status == 200 else {"error": {"code": "not_found", "message": "Missing"}})

    class Response:
        headers = {"X-Request-Id": "req_capture"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps(result).encode()

    def receive(request, **_):
        assert request.get_header("Authorization") == "Bearer " + secret
        if status != 200:
            raise HTTPError(request.full_url, status, "Missing", Response.headers,
                            io.BytesIO(json.dumps(result).encode()))
        return Response()

    monkeypatch.setattr(remote_client, "urlopen", receive)
    args = ["--target", "account", "node", "get", "domain:vinc/agent"]
    before = invoke(args)
    after = invoke(["--debug", *args])
    assert before[:2] == after[:2]
    assert f"GET /v1/nodes/domain%3Avinc%2Fagent -> {status} req_capture" in after[2]
    assert before[2] in after[2]
    assert secret not in after[2] and "Authorization" not in after[2]
    assert "debug" not in invoke(args)[2]


@pytest.mark.parametrize("attached", [False, True])
def test_local_and_attached_debug_uses_actual_routes(monkeypatch, tmp_path, attached):
    data = str(tmp_path / "db")
    monkeypatch.setenv("MEMORY_DATA_DIR", data)
    session = tmp_path / "session.json"
    monkeypatch.setattr(bridge_module, "_session_path", lambda: str(session))
    app = VincApp.open(load_config({"data_dir": data}), auth=SessionAuth("private-sidecar-token"))
    server = None
    if attached:
        server = make_server(app, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        session.write_text(json.dumps({"port": server.server_address[1], "token": "private-sidecar-token"}))
    else:
        app.close()
    try:
        args = ["--target", "local", "node", "get", "concept:missing"]
        before = invoke(args)
        after = invoke(["--debug", *args])
        assert before[:2] == after[:2]
        assert "GET /node?" in after[2] and " -> 200 " in after[2]
        assert ("debug attached" if attached else "debug local") in after[2]
        assert "private-sidecar-token" not in after[2]
        assert "Authorization" not in after[2]
        assert "debug" not in invoke(args)[2]
    finally:
        if server:
            server.shutdown()
            server.server_close()
            app.close()


def test_query_and_body_are_not_traced(monkeypatch):
    from store import diagnostics
    import time
    err = io.StringIO()
    diagnostics.configure(True)
    try:
        with redirect_stderr(err):
            diagnostics.trace("POST", "/v1/search?query=private-customer-body&token=private-key&limit=5",
                              200, time.perf_counter(), request_id="private-key")
    finally:
        diagnostics.configure(False)
    assert "limit=5" in err.getvalue()
    assert "private-" not in err.getvalue()
    assert "redacted" in err.getvalue()
