# -*- coding: utf-8 -*-
"""로컬 자동 등록 — `vinc mcp install --client code|desktop` (remote-profile §7-2).

Claude Code = 공식 `claude mcp add`(user 스코프) 호출. CLI 부재 시 수동 안내를
결과로 반환한다(실패가 아니라 안내 — 사용자가 붙여넣으면 끝).
Claude Desktop = OS 3종 경로의 claude_desktop_config.json 을 **백업 후 병합**
(mcpServers.vinc 키만 만지고 나머지는 보존).
claude.ai 커넥터는 원격 URL 등록(수동 1회) — 로컬 루프백은 구조적으로 불가.
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


def _server_command() -> tuple[str, list[str]]:
    """이 설치본에서 MCP stdio 서버를 띄우는 (command, args)."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["mcp", "serve"]
    return sys.executable, ["-m", "api.cli", "mcp", "serve"]


def desktop_config_path() -> Path:
    sysname = platform.system()
    if sysname == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    if sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def install_desktop(path: Path | None = None) -> dict:
    cfg_path = path or desktop_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    backup = None
    if cfg_path.exists():
        backup = str(cfg_path) + f".bak.{int(time.time())}"
        shutil.copy2(cfg_path, backup)
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            cfg = {}
    command, args = _server_command()
    servers = cfg.setdefault("mcpServers", {})
    servers["vinc"] = {"command": command, "args": args}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return {"client": "desktop", "config": str(cfg_path), "backup": backup,
            "entry": servers["vinc"],
            "note": "restart Claude Desktop to pick up the new server"}


def install_code() -> dict:
    command, args = _server_command()
    argv = ["claude", "mcp", "add", "--scope", "user", "vinc", "--", command, *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"client": "code", "registered": False,
                "note": "claude CLI not found — run this once yourself:",
                "command": " ".join(argv)}
    ok = proc.returncode == 0
    return {"client": "code", "registered": ok,
            "output": (proc.stdout or proc.stderr).strip(),
            "command": " ".join(argv)}


def install(client: str, *, desktop_path: str | None = None) -> dict:
    if client == "desktop":
        return install_desktop(Path(desktop_path) if desktop_path else None)
    if client == "code":
        return install_code()
    raise ValueError(f"unknown client: {client} (expected code|desktop)")


def uninstall_desktop(path: Path | None = None) -> dict:
    """claude_desktop_config.json 에서 `mcpServers.vinc` 키만 제거(백업 후, 나머지 보존).
    설치의 역연산 — 없으면 removed:False(에러 아님, 멱등)."""
    cfg_path = path or desktop_config_path()
    if not cfg_path.exists():
        return {"client": "desktop", "removed": False, "config": str(cfg_path),
                "note": "no Claude Desktop config found — nothing to remove"}
    backup = str(cfg_path) + f".bak.{int(time.time())}"
    shutil.copy2(cfg_path, backup)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        cfg = {}
    servers = cfg.get("mcpServers")
    had = isinstance(servers, dict) and "vinc" in servers
    if had:
        del servers["vinc"]
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return {"client": "desktop", "removed": had, "config": str(cfg_path),
            "backup": backup,
            "note": "restart Claude Desktop to drop the server" if had
                    else "no 'vinc' entry was present"}


def uninstall_code() -> dict:
    """Claude Code 에서 등록 해제 — 공식 `claude mcp remove`(user 스코프). CLI 부재 시
    수동 명령을 안내로 반환."""
    argv = ["claude", "mcp", "remove", "--scope", "user", "vinc"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"client": "code", "removed": False,
                "note": "claude CLI not found — run this once yourself:",
                "command": " ".join(argv)}
    ok = proc.returncode == 0
    return {"client": "code", "removed": ok,
            "output": (proc.stdout or proc.stderr).strip(),
            "command": " ".join(argv)}


def uninstall(client: str, *, desktop_path: str | None = None) -> dict:
    if client == "desktop":
        return uninstall_desktop(Path(desktop_path) if desktop_path else None)
    if client == "code":
        return uninstall_code()
    raise ValueError(f"unknown client: {client} (expected code|desktop)")
