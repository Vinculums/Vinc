"""Local MCP child sidecars stay local even during an account terminal/app session."""
import os
import sys

import pytest

from vinc_mcp import bridge as bridge_module


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("source", ["env", "config"])
def test_local_mcp_spawns_sidecar_with_explicit_local(monkeypatch, tmp_path, frozen, source):
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

    def spawn(argv, **kwargs):
        seen.append(argv)
        # 이 인터프리터는 비동결이다. 동결 argv는 같은 CLI 진입점으로 실행하고,
        # 실제 빌드 파일은 릴리스 smoke에서 별도로 확인한다.
        if frozen:
            assert argv[1:4] == ["--target", "local", "serve"]
            argv = [argv[0], "-m", "api.cli", *argv[1:]]
        else:
            assert argv[1:6] == ["-m", "api.cli", "--target", "local", "serve"]
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(bridge_module.subprocess, "Popen", spawn)
    probe = bridge_module.Bridge(base="http://127.0.0.1:1")
    child = None
    try:
        probe.ensure(wait=20)
        child = probe._child
        assert probe.health()
        assert os.path.exists(tmp_path / "db")
        assert seen
    finally:
        probe.close()
        if child:
            child.wait(timeout=20)
