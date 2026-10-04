"""The desktop's inherited secret is consumed before any store/probe work."""
import os

import pytest

from api import cli


@pytest.mark.parametrize("manual,inherited,expected", [
    (None, "synthetic-desktop-token", "synthetic-desktop-token"),
    ("synthetic-manual-token", "synthetic-desktop-token", "synthetic-manual-token"),
    (None, None, None),
    (None, "", None),
])
def test_serve_consumes_environment_token_before_store_work(
        tmp_path, monkeypatch, manual, inherited, expected):
    if inherited is None:
        monkeypatch.delenv("VINC_SIDECAR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("VINC_SIDECAR_TOKEN", inherited)
    monkeypatch.setattr("store.config.resolve_data_dir", lambda _: str(tmp_path / "db"))

    def restore(_):
        assert "VINC_SIDECAR_TOKEN" not in os.environ
        return "off"

    received = []
    monkeypatch.setattr("store.persistence.restore_newest", restore)
    monkeypatch.setattr("api.server.serve", lambda cfg, **kw: received.append(kw))
    # Managed sidecars select local even when the terminal defaults to account.
    monkeypatch.setenv("VINC_TARGET", "account")
    argv = ["--target", "local", "serve", "--data", str(tmp_path / "db")]
    if manual is not None:
        argv.extend(["--token", manual])
    assert cli.main(argv) == 0
    assert received[0]["token"] == expected
    assert "VINC_SIDECAR_TOKEN" not in os.environ


def test_desktop_spawn_has_no_secret_in_arguments():
    from pathlib import Path
    source = (Path(__file__).parents[2] / "app/src-tauri/src/main.rs").read_text(encoding="utf-8")
    spawn = source.split("fn spawn_sidecar(", 1)[1].split("let (mut events, child)", 1)[0]
    args = spawn.split(".args([", 1)[1].split("])", 1)[0]
    assert '"--token"' not in args
    assert "token" not in args
    assert '.env("VINC_SIDECAR_TOKEN", token)' in spawn


def test_inherited_token_authenticates_attach_without_appearing_in_banner(tmp_path):
    import json
    import subprocess
    import sys
    import time
    import urllib.request

    token = "synthetic-inherited-sidecar-token"
    data = str(tmp_path / "db")
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "VINC_STORE_SNAPSHOT", "VINC_DURABLE_ACK"):
        env.pop(name, None)
    env.update(VINC_SIDECAR_TOKEN=token, MEMORY_DATA_DIR=data, VINC_TARGET="local")
    stdout_path, stderr_path = tmp_path / "stdout", tmp_path / "stderr"
    base = None
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
        child = subprocess.Popen([sys.executable, "-m", "api.cli", "serve", "--data", data],
                                 stdout=out, stderr=err, env=env)
        try:
            deadline = time.monotonic() + 40
            info = None
            while time.monotonic() < deadline and child.poll() is None:
                lines = stdout_path.read_text(encoding="utf-8").splitlines()
                if lines:
                    info = json.loads(lines[0])
                    break
                time.sleep(0.05)
            assert info is not None, "sidecar did not emit a startup banner"
            assert info["token"] == f"<supplied:{len(token)}ch>"
            base = f"http://127.0.0.1:{info['port']}"
            session = tmp_path / "session.json"
            session.write_text(json.dumps({"port": info["port"], "token": token,
                                            "pid": child.pid}), encoding="utf-8")
            session.chmod(0o600)
            attach_env = dict(env)
            attach_env.pop("VINC_SIDECAR_TOKEN")
            attached = subprocess.run(
                [sys.executable, "-m", "api.cli", "doc", "ls", "--data", data,
                 "--target", "local", "--format", "json"],
                env=attach_env, capture_output=True, text=True, encoding="utf-8", timeout=30)
            assert attached.returncode == 0
            assert isinstance(json.loads(attached.stdout), dict)
        finally:
            if base and child.poll() is None:
                request = urllib.request.Request(base + "/shutdown", data=b"{}", method="POST",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        assert response.status == 202
                finally:
                    if child.poll() is None:
                        try:
                            child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)
            elif child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert token not in stdout_path.read_text(encoding="utf-8")
    assert token not in stderr_path.read_text(encoding="utf-8")
