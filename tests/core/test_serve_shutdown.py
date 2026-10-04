"""실제 CLI 프로세스에 신호를 보내 종료와 스토어 재개방을 함께 검증한다."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

import pytest


@pytest.mark.skipif(os.name != "posix", reason="POSIX process signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("delivery", ["parent", "worker"])
def test_serve_signal_exits_and_reopens_store_within_five_seconds(tmp_path, sig, delivery):
    if delivery == "worker" and sys.platform != "linux":
        pytest.skip("Linux thread-directed signal delivery")
    # 동결본도 같은 검사를 통과해야 한다. 부모에게만 보내 bootloader 전달까지 잰다.
    frozen = os.environ.get("VINC_TEST_FROZEN_CLI")
    command = [str(Path(frozen).resolve())] if frozen else [sys.executable, "-m", "api.cli"]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("VINC_", "MEMORY_", "PYTHON"))}
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "db"
    env.update(HOME=str(home), XDG_DATA_HOME=str(home / "data"),
               XDG_CONFIG_HOME=str(home / "config"), MEMORY_DATA_DIR=str(data),
               VINC_TARGET="local", VINC_SIDECAR_TOKEN="synthetic-shutdown-token")
    if not frozen and os.environ.get("VINC_LBUG_EXT_DIR"):
        env["VINC_LBUG_EXT_DIR"] = os.environ["VINC_LBUG_EXT_DIR"]
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    descendants = []
    with stdout.open("w") as out, stderr.open("w") as err:
        process = subprocess.Popen(command + ["--target", "local", "serve", "--port", "0"],
                                   env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            info = None
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and process.poll() is None:
                lines = stdout.read_text().splitlines()
                if lines:
                    info = json.loads(lines[0])
                    break
                time.sleep(0.05)
            assert info and info["driver"], "serve did not open a real store"
            request = urllib.request.Request(
                f"http://127.0.0.1:{info['port']}/health",
                headers={"Authorization": "Bearer synthetic-shutdown-token"})
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
            # Linux onefile에는 실제 Python 자식이 있다. 그 자식이 남으면 종료가 아니다.
            children = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            if children.exists():
                descendants = [int(pid) for pid in children.read_text().split()]
                if frozen:
                    assert descendants, "expected a onefile bootloader child"
            started = time.monotonic()
            if delivery == "parent":
                os.kill(process.pid, sig)
            else:
                # 신호가 워커에 도착해도 Python 처리기는 메인 스레드에서 실행된다.
                import ctypes
                python_pid = descendants[0] if frozen else process.pid
                tasks = Path(f"/proc/{python_pid}/task")
                worker = next(int(p.name) for p in tasks.iterdir() if int(p.name) != python_pid)
                libc = ctypes.CDLL(None, use_errno=True)
                assert libc.tgkill(python_pid, worker, sig) == 0, ctypes.get_errno()
            assert process.wait(timeout=5) == 0
            for pid in descendants:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            remaining = 5 - (time.monotonic() - started)
            assert remaining > 0, "process exit exceeded five seconds"
            reopened = subprocess.run(command + ["--target", "local", "--format", "json", "domains"],
                                      env=env, capture_output=True, text=True, timeout=remaining)
            assert reopened.returncode == 0, reopened.stderr
            assert json.loads(reopened.stdout)["executed"] is True
            elapsed = time.monotonic() - started
            assert elapsed < 5
            print(f"{sig.name}: exit and real store reopen in {elapsed:.3f}s "
                  f"(frozen={bool(frozen)}, delivery={delivery}, children={len(descendants)})")
        finally:
            # 이 테스트가 새 세션으로 띄운 프로세스들만 정리한다.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
