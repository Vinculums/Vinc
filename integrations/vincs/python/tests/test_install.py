"""Installed-wheel acceptance: no repository imports or native engine needed."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from vinc import AsyncVinc, AsyncVincClient, Vinc, VincClient


def run_cli(*args, extra_env=None):
    with tempfile.TemporaryDirectory() as cwd:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("VINC_") and key not in {"PYTHONHOME", "PYTHONPATH"}}
        env.update(extra_env or {})
        return subprocess.run([sys.executable, "-I", "-m", "vinc", *args],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


def test_client_aliases_and_module_isolation():
    assert Vinc is VincClient
    assert AsyncVinc is AsyncVincClient
    assert "api" not in sys.modules
    assert "store" not in sys.modules
    assert "langgraph" not in sys.modules


def test_cli_help_and_version_without_native_engine():
    for args in [("--help",), ("search", "--help"), ("--version",),
                 ("operation", "status", "--help"), ("delete", "--help"), ("doc", "put", "--help")]:
        result = run_cli(*args)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip()


def test_installed_cli_exposes_guards_and_preview():
    deletion = run_cli("delete", "--help")
    assert all(flag in deletion.stdout for flag in ("--dry-run", "--preview-digest", "--operation-id"))
    document = run_cli("doc", "put", "--help")
    assert all(flag in document.stdout for flag in ("--expected-content-hash", "--create-only", "--operation-id"))


def test_account_cli_missing_key_is_a_stderr_failure():
    result = run_cli("--target", "account", "--json", "whoami")
    assert result.returncode != 0
    assert not result.stdout.strip()
    error = json.loads(result.stderr)
    assert error["error"]["code"] == "not_authenticated"


def test_cli_entry_point_is_installed():
    from importlib.metadata import distribution
    entry = next(e for e in distribution("vincs").entry_points if e.name == "vinc")
    assert entry.value == "vinc.cli:main"


def test_optional_integration_modules_use_the_unified_sdk():
    import importlib.util
    import vinc
    for name in ("langgraph", "agent_framework"):
        spec = importlib.util.find_spec(f"vinc.integrations.{name}")
        assert spec is not None
        assert Path(spec.origin).is_relative_to(Path(vinc.__file__).parent)


def test_account_cli_reaches_rest_and_preserves_result():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    received = []
    expected = {"owner": "user:install-test", "scope": "personal", "spaces": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            body = json.dumps(expected).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_cli("--target", "account", "--space", "personal", "--json", "whoami",
                         extra_env={"VINC_API_URL": f"http://127.0.0.1:{server.server_port}",
                                    "VINC_API_KEY": "vinc_ro_install_test"})
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {**expected, "target": "account", "key_scope": "read"}
        assert received == [("/v1/me", "Bearer vinc_ro_install_test")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_framework_imports_when_extras_are_installed():
    import importlib.util
    if importlib.util.find_spec("langgraph") is not None:
        from vinc.integrations.langgraph import create_vinc_tools
        tools = create_vinc_tools(Vinc(api_key="vinc_ro_install_test"))
        assert len(tools) >= 2
    if importlib.util.find_spec("agent_framework") is not None:
        from vinc.integrations.agent_framework import VincContextProvider
        provider = VincContextProvider(AsyncVinc(api_key="vinc_ro_install_test"))
        assert provider is not None
