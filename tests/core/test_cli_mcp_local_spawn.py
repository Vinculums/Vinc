"""Local MCP child sidecars stay local even during an account terminal/app session."""
import os
import sys
from types import SimpleNamespace

import pytest

from vinc_mcp import bridge as bridge_module


def _spawn_diagnostics(probe, children, tmp_path):
    child = children[-1] if children else None
    returncode = child.poll() if child else None
    lines = [
        f"sidecar returncode={returncode!r} alive={child is not None and returncode is None}",
        f"bind={probe.base} tempdir={tmp_path}",
        f"state={tmp_path / 'db'} config={tmp_path / 'home' / '.vinc' / 'config.json'}",
    ]
    for name in ("sidecar.stdout.log", "sidecar.stderr.log"):
        path = tmp_path / name
        value = path.read_text(errors="replace") if path.exists() else "<not created>"
        # 생성된 인증 토큰은 실패 출력에도 남기지 않는다. 설정 내용·argv는 출력하지 않는다.
        if probe.token:
            value = value.replace(probe.token, "[REDACTED]")
        lines.append(f"{path}:\n{value[-12000:]}")
    return "\n".join(lines)


def test_spawn_diagnostics_redacts_token_and_reports_child_state(tmp_path):
    probe = SimpleNamespace(base="http://127.0.0.1:32123", token="fixture-secret-token")
    (tmp_path / "sidecar.stdout.log").write_text("ready fixture-secret-token")
    (tmp_path / "sidecar.stderr.log").write_text("failed fixture-secret-token")
    for returncode in (None, 7):
        result = _spawn_diagnostics(probe, [SimpleNamespace(poll=lambda: returncode)], tmp_path)
        assert probe.token not in result
        assert result.count("[REDACTED]") == 2
        assert f"returncode={returncode!r} alive={returncode is None}" in result
        assert "127.0.0.1:32123" in result
        assert str(tmp_path / "db") in result
        assert str(tmp_path / "home" / ".vinc" / "config.json") in result


@pytest.mark.parametrize("token", ["fixture-valid", "-fixture-leading", "--fixture-leading"])
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("source", ["env", "config"])
def test_local_mcp_spawns_sidecar_with_explicit_local(monkeypatch, tmp_path, frozen, source, token):
    monkeypatch.setattr(bridge_module.secrets, "token_urlsafe", lambda _: token)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = home / ".vinc"
    config.mkdir()
    (config / "config.json").write_text('{"mode":"account"}')
    monkeypatch.setenv("VINC_TARGET", "account" if source == "env" else "local")
    if source == "config":
        monkeypatch.delenv("VINC_TARGET")
    monkeypatch.setenv("VINC_MCP_MODE", "local")
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setattr(bridge_module, "_session_path", lambda: str(tmp_path / "absent-session"))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    real_popen = bridge_module.subprocess.Popen
    seen = []
    children = []

    def spawn(argv, **kwargs):
        seen.append(argv)
        # 이 인터프리터는 비동결이다. 동결 argv는 같은 CLI 진입점으로 실행하고,
        # 실제 빌드 파일은 릴리스 smoke에서 별도로 확인한다.
        if frozen:
            assert argv[1:4] == ["--target", "local", "serve"]
            argv = [argv[0], "-m", "api.cli", *argv[1:]]
        else:
            assert argv[1:6] == ["-m", "api.cli", "--target", "local", "serve"]
        # 부모가 DEVNULL을 지정해도 테스트에서는 실패 원인을 보존한다.
        with (tmp_path / "sidecar.stdout.log").open("wb") as stdout, \
                (tmp_path / "sidecar.stderr.log").open("wb") as stderr:
            kwargs.update(stdout=stdout, stderr=stderr)
            child = real_popen(argv, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(bridge_module.subprocess, "Popen", spawn)
    probe = bridge_module.Bridge(base="http://127.0.0.1:1")
    child = None
    try:
        probe.ensure(wait=20)
        child = probe._child
        assert probe.health()
        # 무인증 health뿐 아니라 토큰을 쓰는 읽기 요청도 원래 값으로 왕복한다.
        result = probe.request("GET", "/domains", params={"owner": "tenant:local"})
        assert result.get("executed") is True
        assert os.path.exists(tmp_path / "db")
        assert seen
    except Exception:
        print(_spawn_diagnostics(probe, children, tmp_path))
        raise
    finally:
        probe.close()
        child = child or (children[-1] if children else None)
        if child:
            child.wait(timeout=20)
