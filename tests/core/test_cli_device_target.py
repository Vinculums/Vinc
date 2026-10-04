"""Device operations use one effective target; MCP and file operations keep theirs."""
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from api import cli, cli_target


DEVICE_CASES = [
    ["backup", "--out", "unused"],
    ["export", "--format", "md", "--out", "unused", "--owner", "tenant:local"],
    ["share", "--out", "unused", "--owner", "tenant:local"],
    ["import", "--pack", "unused", "--owner", "tenant:local"],
    ["activate", "--owner", "tenant:local"],
    ["pin", "--episode", "episode:x"],
    ["tag", "--episode", "episode:x", "--tags", "tag"],
    ["touch", "--episode", "episode:x"], ["learner", "--owner", "tenant:local"], ["graph", "--owner", "tenant:local"],
    ["plan", "search"], ["onboard", "--profile", "unused"],
    ["domain-backfill"], ["serve"], ["store-probe", "--data", "unused"],
    ["migrate-dim"], ["migrate-lex", "--data", "unused"],
    ["store-rebuild", "--out", "unused", "--model", "unused"],
    ["graph-md", "build"], ["graph-md", "check", "--file", "unused"],
    ["graph-md", "check", "--doc", "--no-store", "--owner", "tenant:local"],
    ["mcp", "serve-http"],
]


def invoke(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(["--format", "json", *args])
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("source", ["flag", "env", "config"])
@pytest.mark.parametrize("verb", DEVICE_CASES, ids=lambda v: " ".join(v[:2]))
def test_device_account_target_sources_agree(monkeypatch, source, verb):
    monkeypatch.delenv("VINC_TARGET", raising=False)
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {})
    args = verb
    if source == "flag":
        args = ["--target", "account", *verb]
    elif source == "env":
        monkeypatch.setenv("VINC_TARGET", "account")
    else:
        monkeypatch.setattr(cli_target, "read_local_config", lambda: {"mode": "account"})
    code, out, err = invoke(args)
    assert code == 2 and out == ""
    error = json.loads(err)["error"]
    assert error["code"] == "invalid_argument"
    assert "is local only; pass --target local" in error["message"]


def test_explicit_local_overrides_account_environment_and_config(monkeypatch):
    monkeypatch.setenv("VINC_TARGET", "account")
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {"mode": "account"})
    monkeypatch.setattr("api.cli_transport.request_if_attached", lambda args, **_: {"executed": True, "kind": "backup"})
    code, out, err = invoke(["--target", "local", "backup", "--out", "unused"])
    assert code == 0 and json.loads(out)["kind"] == "backup"


@pytest.mark.parametrize("source", ["flag", "env", "config"])
def test_file_only_checks_do_not_open_store(monkeypatch, tmp_path, source):
    monkeypatch.setenv("VINC_TARGET", "account" if source == "env" else "local")
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {"mode": "account"})
    monkeypatch.setattr("store.backend.open_store", lambda *_: pytest.fail("file check opened a store"))
    prefix = ["--target", "account"] if source == "flag" else []
    file = tmp_path / "GRAPH.md"
    file.write_text("# Empty\n")
    code, out, err = invoke([*prefix, "graph-md", "check", "--no-store", "--file", str(file)])
    assert code in (0, 1, 2) and "local only" not in out + err
    code, out, err = invoke([*prefix, "refs", "check", "--root", str(tmp_path)])
    assert code == 2 and "local only" not in out + err


def test_mcp_stdio_account_proxy_and_configuration_remain_available(monkeypatch):
    monkeypatch.setenv("VINC_TARGET", "account")
    monkeypatch.setattr(cli_target, "read_local_config", lambda: {"mode": "account"})
    monkeypatch.setattr("vinc_mcp.proxy.wants_account_mode", lambda **_: True)
    monkeypatch.setattr("vinc_mcp.proxy.serve_remote", lambda key: 0)
    monkeypatch.setenv("VINC_REMOTE_MCP_TOKEN", "fake-test-token")
    monkeypatch.setattr("vinc_mcp.bridge.Bridge.ensure", lambda **_: pytest.fail("proxy spawned sidecar"))
    assert invoke(["mcp", "serve"])[0] == 0
    monkeypatch.setattr("vinc_mcp.install.manual_entry", lambda: {"kind": "manual", "executed": True})
    assert invoke(["--target", "account", "mcp", "manual"])[0] == 0
