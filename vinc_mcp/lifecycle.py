# -*- coding: utf-8 -*-
"""사이드카 생명주기 — attach 우선, 없으면 spawn ([[vinc.mcp.skeleton]] §3).

`data_dir/sidecar.lock` 에 `{pid, host, port, token, refcount}` 를 원자적으로
저장한다(임시파일+rename). 여러 프론트 도어(MCP·GUI)가 하나의 사이드카를 refcount
로 공유 — 마지막 사용자가 나갈 때만 프로세스를 종료한다. 경합 방지는 별도
`.lck` 파일의 `flock`(POSIX 전용 — Windows 이식은 P3 Tauri 크로스플랫폼 시 별도
검토, 스켈레톤 §3 "경합" 조항의 구현).

spawn 대상은 기존 `python -m api.cli serve`(P2) — 신규 프로세스 진입점을 만들지
않는다(비즈니스 로직 0 원칙, §2).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class LifecycleError(RuntimeError):
    """사이드카 attach/spawn 실패."""


@dataclass
class SidecarHandle:
    host: str
    port: int
    token: str
    pid: int
    spawned_by_us: bool   # 정보용(로깅) — release 종료 판단은 lockfile 의 refcount 가 정본


def _lock_path(data_dir: str) -> Path:
    return Path(data_dir) / "sidecar.lock"


def _guard_path(data_dir: str) -> Path:
    return Path(data_dir) / "sidecar.lock.lck"


@contextlib.contextmanager
def _guarded(data_dir: str):
    """lockfile 읽기-판단-쓰기 임계구역을 프로세스 간 배타(flock)로 보호."""
    guard = _guard_path(data_dir)
    guard.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(guard), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_state_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sidecar.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)   # 신호 0 = 생존 확인(전송 안 함)
    except OSError:
        return False
    return True


def _health_ok(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _spawn_sidecar(*, token: str, parent_pid: int, timeout: float = 10.0) -> tuple[int, int]:
    """`python -m api.cli serve` 를 백그라운드로 띄우고 첫 stdout 줄(JSON)에서
    port 를 읽는다(`api/server.py` `serve()` 의 기동 정보 계약)."""
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "api.cli", "serve", "--port", "0",
         "--parent-pid", str(parent_pid), "--token", token],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(repo_root),
    )
    deadline = time.monotonic() + timeout
    line = ""
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if line:
                break
            if proc.poll() is not None:
                raise LifecycleError(f"sidecar exited during spawn (code={proc.returncode})")
        if not line:
            proc.kill()
            proc.wait()
            raise LifecycleError("sidecar spawn timed out (no startup line)")
    finally:
        proc.stdout.close()   # 기동 줄만 읽으면 끝 — 파이프를 열어둘 이유 없음
    try:
        info = json.loads(line)
    except json.JSONDecodeError as exc:
        raise LifecycleError(f"sidecar startup line not JSON: {line!r}") from exc
    # 우리가 이 자식의 유일한 부모다 — reap 하지 않으면 종료(SIGTERM/크래시) 후
    # 좀비로 남는다(`os.kill(pid, 0)` 은 좀비도 "생존"으로 오판). 블로킹 wait 를
    # 데몬 스레드에 맡겨 호출자를 막지 않으면서 자식이 죽는 즉시 reap 한다.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid, int(info["port"])


def attach_or_spawn(data_dir: str, *, parent_pid: int | None = None) -> SidecarHandle:
    """lockfile 을 읽어 살아있으면 attach(refcount+=1), 아니면 spawn(refcount=1)."""
    parent_pid = parent_pid or os.getpid()
    lock_path = _lock_path(data_dir)
    with _guarded(data_dir):
        state = _read_state(lock_path)
        if state and _pid_alive(state.get("pid", -1)) and _health_ok(state["host"], state["port"]):
            state["refcount"] = int(state.get("refcount", 0)) + 1
            _write_state_atomic(lock_path, state)
            return SidecarHandle(state["host"], state["port"], state["token"],
                                 state["pid"], spawned_by_us=False)

        token = secrets.token_urlsafe(24)
        pid, port = _spawn_sidecar(token=token, parent_pid=parent_pid)
        new_state = {"pid": pid, "host": "127.0.0.1", "port": port, "token": token, "refcount": 1}
        _write_state_atomic(lock_path, new_state)
        return SidecarHandle("127.0.0.1", port, token, pid, spawned_by_us=True)


def release(data_dir: str, handle: SidecarHandle) -> None:
    """refcount -= 1; 0 이 되면(누가 spawn 했든) lockfile 이 기록한 pid 를 종료한다
    ([[vinc.mcp.skeleton]] §3 "종료 순서 무관하게 마지막 사용자가 정리")."""
    lock_path = _lock_path(data_dir)
    with _guarded(data_dir):
        state = _read_state(lock_path)
        if state is None:
            return
        state["refcount"] = max(0, int(state.get("refcount", 1)) - 1)
        if state["refcount"] <= 0:
            with contextlib.suppress(OSError):
                os.kill(state["pid"], 15)   # SIGTERM — server.py 가 clean shutdown(E6)
            with contextlib.suppress(FileNotFoundError):
                lock_path.unlink()
            return
        _write_state_atomic(lock_path, state)
