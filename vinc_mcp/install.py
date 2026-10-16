# -*- coding: utf-8 -*-
"""로컬 자동 등록 — `vinc mcp install --client <host>` (remote-profile §7-2).

**호스트 중립**: Vinc 의 MCP 서버(rpc/tools)에는 특정 벤더 코드가 없다. 이 모듈만
호스트별 "등록 관례"의 차이를 흡수한다. 관례는 두 가지뿐이다:

  · JSON 병합형 — `mcpServers.vinc` 키를 설정 파일에 **백업 후 병합**(나머지 보존).
    Claude Desktop · Cursor 가 같은 스키마를 쓴다.
  · CLI 위임형 — 호스트가 제공하는 공식 등록 명령을 호출한다.
    Claude Code(`claude mcp add`) · Codex CLI(`codex mcp add`).

CLI 부재 시 수동 안내를 결과로 반환한다(실패가 아니라 안내 — 사용자가 붙여넣으면 끝).

여기 없는 호스트(Kimi CLI · VS Code · Zed · Windsurf …)도 **서버는 그대로 동작**한다
— 등록만 각 호스트 방식으로 손수 하면 된다. `manual_entry()` 가 그 붙여넣기용
설정 조각을 돌려준다.

원격(claude.ai · ChatGPT 커넥터 등)은 URL 등록이라 이 모듈 대상이 아니다 — 로컬
루프백은 구조적으로 원격 호스트에 노출할 수 없다.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── 호스트 레지스트리 ────────────────────────────────────────────────────────
# JSON 병합형: label 과 설정 파일 위치만 다르고 병합 로직은 공유한다.
_JSON_CLIENTS: dict[str, str] = {
    "desktop": "Claude Desktop",
    "cursor": "Cursor",
}
# CLI 위임형: (실행 파일, label, 스코프 인자). 스코프 인자는 호스트마다 다르다
# — claude 는 `--scope user`, codex 는 스코프 개념이 없어 빈 리스트.
_CLI_CLIENTS: dict[str, tuple[str, str, list[str]]] = {
    "code": ("claude", "Claude Code", ["--scope", "user"]),
    "codex": ("codex", "Codex CLI", []),
}

CLIENTS: tuple[str, ...] = ("code", "desktop", "cursor", "codex")


def client_label(client: str) -> str:
    if client in _JSON_CLIENTS:
        return _JSON_CLIENTS[client]
    if client in _CLI_CLIENTS:
        return _CLI_CLIENTS[client][1]
    raise ValueError(f"unknown client: {client} (expected {'|'.join(CLIENTS)})")


def _server_command() -> tuple[str, list[str]]:
    """이 설치본에서 MCP stdio 서버를 띄우는 (command, args)."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp", "serve"]
    return sys.executable, ["-m", "api.cli", "mcp", "serve"]


def manual_entry() -> dict:
    """레지스트리에 없는 호스트용 — 사용자가 붙여넣을 stdio 설정 조각.

    Kimi CLI(`~/.kimi/mcp.json`) · VS Code · Zed 등 대부분의 호스트가 이 `mcpServers`
    스키마를 그대로 쓰거나 아주 가깝다. 자동 등록을 늘리기 전에도 이 조각만 있으면
    수동 연결이 성립한다.
    """
    command, args = _server_command()
    return {"mcpServers": {"vinc": {"command": command, "args": args}}}


# ── JSON 병합형 ─────────────────────────────────────────────────────────────
def config_path(client: str) -> Path:
    """JSON 병합형 호스트의 설정 파일 경로(OS 분기 포함)."""
    if client == "desktop":
        sysname = platform.system()
        if sysname == "Windows":
            base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            return Path(base) / "Claude" / "claude_desktop_config.json"
        if sysname == "Darwin":
            return (Path.home() / "Library" / "Application Support" / "Claude"
                    / "claude_desktop_config.json")
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        # Cursor 는 OS 무관 단일 경로(전역 스코프). 프로젝트 스코프
        # `.cursor/mcp.json` 은 사용자 몫 — 앱이 남의 저장소를 건드리지 않는다.
        return Path.home() / ".cursor" / "mcp.json"
    raise ValueError(f"{client} is not a JSON-config client")


def _read_config(cfg_path: Path) -> dict:
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}


def install_json(client: str, path: Path | None = None) -> dict:
    """`mcpServers.vinc` 만 만지고 나머지는 보존한다(백업 후 병합)."""
    label = client_label(client)
    cfg_path = path or config_path(client)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    backup = None
    if cfg_path.exists():
        backup = str(cfg_path) + f".bak.{int(time.time())}"
        shutil.copy2(cfg_path, backup)
        cfg = _read_config(cfg_path)
    command, args = _server_command()
    servers = cfg.setdefault("mcpServers", {})
    servers["vinc"] = {"command": command, "args": args}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return {"client": client, "label": label, "config": str(cfg_path),
            "backup": backup, "entry": servers["vinc"],
            "note": f"restart {label} to pick up the new server"}


def uninstall_json(client: str, path: Path | None = None) -> dict:
    """설정에서 `mcpServers.vinc` 키만 제거(백업 후, 나머지 보존).
    설치의 역연산 — 없으면 removed:False(에러 아님, 멱등)."""
    label = client_label(client)
    cfg_path = path or config_path(client)
    if not cfg_path.exists():
        return {"client": client, "label": label, "removed": False,
                "config": str(cfg_path),
                "note": f"no {label} config found — nothing to remove"}
    backup = str(cfg_path) + f".bak.{int(time.time())}"
    shutil.copy2(cfg_path, backup)
    cfg = _read_config(cfg_path)
    servers = cfg.get("mcpServers")
    had = isinstance(servers, dict) and "vinc" in servers
    if had:
        del servers["vinc"]
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return {"client": client, "label": label, "removed": had,
            "config": str(cfg_path), "backup": backup,
            "note": f"restart {label} to drop the server" if had
                    else "no 'vinc' entry was present"}


def repair_json(client: str, path: Path | None = None) -> dict:
    """우리 항목이 **없어진 파일**을 가리키면 지금 경로로 고쳐 쓴다.

    `install_json` 이 적는 `command` 는 `sys.executable` 의 절대경로다. 그래서 이
    바이너리의 이름이나 자리가 바뀌면 호스트 설정은 그대로 남은 채 **조용히 죽는다**
    — 사용자 쪽에서는 "Vinc MCP 가 안 뜬다" 로만 보인다. 실제로 바뀐 적이 둘 있다:
    엔진이 `vinc-sidecar` 에서 `vinc` 로 이름을 넘겨받았고(VIN-72), 사용자가 앱을
    다른 폴더로 옮기는 경우도 같은 고장이다.

    우리 항목만 본다. `command` 가 실제로 존재하면 손대지 않는다.
    """
    label = client_label(client)
    cfg_path = path or config_path(client)
    if not cfg_path.exists():
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path), "note": "no config"}
    cfg = _read_config(cfg_path)
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path), "note": "no 'vinc' entry"}
    entry = servers.get("vinc")
    if not isinstance(entry, dict):
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path), "note": "no 'vinc' entry"}
    old = entry.get("command")
    # ⚠ `command` 가 없는 항목은 **우리가 쓴 것이 아니다.** 같은 이름으로 원격 항목
    # (`{"url": "https://mcp.vincs.io/mcp"}`)을 손으로 적어 둔 사람이 있고, 그것을
    # 로컬 실행으로 덮으면 멀쩡히 되던 연결을 끊는다. 2026-09-18 에 실제로 한 번
    # 덮어썼다가 백업에서 되돌렸다. 고치는 대상은 **경로가 있었는데 그 파일이 사라진
    # 경우** 하나뿐이다.
    if not isinstance(old, str) or not old:
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path),
                "note": "entry has no command path — not ours to repair"}
    if Path(old).exists():
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path), "note": "entry still points at a file"}
    command, args = _server_command()
    if old == command:
        return {"client": client, "label": label, "repaired": False,
                "config": str(cfg_path), "note": "nothing to point it at"}
    backup = str(cfg_path) + f".bak.{int(time.time())}"
    shutil.copy2(cfg_path, backup)
    servers["vinc"] = {"command": command, "args": args}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return {"client": client, "label": label, "repaired": True, "was": old,
            "config": str(cfg_path), "backup": backup, "entry": servers["vinc"],
            "note": f"restart {label} to pick up the moved server"}


def repair() -> dict:
    """JSON 설정을 쓰는 호스트 전부를 훑어 고친다. 멱등하고, 조용히 실패한다.

    CLI 로 등록하는 호스트(`_CLI_CLIENTS`)는 그 CLI 가 자기 설정을 쥐고 있어 여기서
    건드리지 않는다 — 그쪽은 `mcp install` 을 다시 부르는 것이 정상 경로다.
    """
    out = []
    for client in _JSON_CLIENTS:
        try:
            out.append(repair_json(client))
        except Exception as exc:  # 설정 하나가 깨져도 나머지는 고친다
            out.append({"client": client, "repaired": False, "error": str(exc)})
    return {"repaired": [r for r in out if r.get("repaired")], "checked": out}


# ── CLI 위임형 ──────────────────────────────────────────────────────────────
def _run_cli(client: str, argv: list[str], verb: str) -> dict:
    label = client_label(client)
    key = "registered" if verb == "add" else "removed"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"client": client, "label": label, key: False,
                "note": f"{argv[0]} CLI not found — run this once yourself:",
                "command": " ".join(argv)}
    except subprocess.TimeoutExpired:
        return {"client": client, "label": label, key: False,
                "note": f"{argv[0]} did not respond in time — run this once yourself:",
                "command": " ".join(argv)}
    ok = proc.returncode == 0
    return {"client": client, "label": label, key: ok,
            "output": (proc.stdout or proc.stderr).strip(),
            "command": " ".join(argv)}


def install_cli(client: str) -> dict:
    """호스트 공식 등록 명령 호출 — `<bin> mcp add [scope] vinc -- <command> <args>`.

    Claude Code 와 Codex CLI 가 같은 형태를 쓴다(`--` 뒤가 stdio 실행 명령).
    스코프 인자만 호스트별로 다르다.
    """
    binary, _label, scope = _CLI_CLIENTS[client]
    command, args = _server_command()
    argv = [binary, "mcp", "add", *scope, "vinc", "--", command, *args]
    return _run_cli(client, argv, "add")


def uninstall_cli(client: str) -> dict:
    binary, _label, scope = _CLI_CLIENTS[client]
    return _run_cli(client, [binary, "mcp", "remove", *scope, "vinc"], "remove")


# ── 디스패치 ────────────────────────────────────────────────────────────────
def install(client: str, *, config_path_override: str | None = None) -> dict:
    if client in _JSON_CLIENTS:
        return install_json(client, Path(config_path_override) if config_path_override else None)
    if client in _CLI_CLIENTS:
        return install_cli(client)
    raise ValueError(f"unknown client: {client} (expected {'|'.join(CLIENTS)})")


def uninstall(client: str, *, config_path_override: str | None = None) -> dict:
    if client in _JSON_CLIENTS:
        return uninstall_json(client, Path(config_path_override) if config_path_override else None)
    if client in _CLI_CLIENTS:
        return uninstall_cli(client)
    raise ValueError(f"unknown client: {client} (expected {'|'.join(CLIENTS)})")
