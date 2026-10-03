"""Version flags exit before graph configuration and store access."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from api import cli, cli_target, remote_client
from store import backend
from vinc_mcp import __version__


@pytest.mark.parametrize("flag", ["--version", "-V"])
@pytest.mark.parametrize("options", [[], ["--target", "account"], ["--json"]])
def test_version_exits_before_target_key_or_store(flag, options, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("version output accessed graph configuration or the store")

    monkeypatch.setattr(cli_target, "resolve_target", forbidden)
    monkeypatch.setattr(cli_target, "resolve_space", forbidden)
    monkeypatch.setattr(remote_client, "api_key", forbidden)
    monkeypatch.setattr(backend, "open_store", forbidden)
    monkeypatch.setenv("VINC_FORMAT", "invalid")
    monkeypatch.setenv("VINC_TARGET", "invalid")
    monkeypatch.setenv("VINC_SPACE", "invalid")
    assert cli.main([*options, flag]) == 0
    got = capsys.readouterr()
    assert got.out == f"vinc {__version__}\n"
    assert got.err == ""


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_source_entrypoint_version_does_not_create_user_data(flag, tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               MEMORY_DATA_DIR=str(tmp_path / "store"), VINC_TARGET="account",
               VINC_FORMAT="invalid", VINC_SPACE="invalid")
    env.pop("VINC_API_KEY", None)
    env.pop("VINC_REMOTE_MCP_TOKEN", None)
    proc = subprocess.run([sys.executable, "-m", "api.cli", flag],
                          env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=20, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"vinc {__version__}\n"
    assert proc.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_root_help_lists_version_flags(capsys):
    assert cli.main(["--help"]) == 0
    got = capsys.readouterr()
    assert "--version, -V" in got.out
    assert got.err == ""
