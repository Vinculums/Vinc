"""Resolve the CLI graph target and account space from explicit inputs."""
from __future__ import annotations

import os

from store.config import read_local_config

#: MCP 도구로 답하는 그래프 동사. 기기 작업은 DEVICE_CMDS가 별도로 제한한다.
TOOL_CMDS = frozenset({"whoami", "conventions", "node", "edges", "role", "path", "ties",
                       "diagnose", "operation", "ingest", "delete", "edge", "merge", "call",
                       "search", "recall", "brief", "decide", "domains", "doc", "gate",
                       "shim", "events", "run", "eval"})

# 기기 스토어·파일을 바꾸거나 로컬 서비스를 시작하는 명령만 제한한다.
# MCP stdio 프록시와 파일 전용 검사는 각각 자신의 계약을 따른다.
DEVICE_CMDS = frozenset({"backup", "export", "share", "import", "activate", "pin",
                         "tag", "touch", "learner", "graph", "plan", "onboard",
                         "domain-backfill", "serve", "store-probe", "migrate-dim",
                         "migrate-lex", "migrate-body-mentions", "store-rebuild"})


def local_only(args) -> bool:
    if args.cmd in DEVICE_CMDS:
        return True
    if args.cmd == "graph-md":
        return not (args.gmcmd == "check" and args.no_store and not args.doc)
    if args.cmd == "mcp":
        return args.mcpcmd == "serve-http"
    return False


def resolve_target(explicit: str | None) -> str:
    """`--target` > `VINC_TARGET` > config.json 의 `mode` > local.

    명시한 값이 틀리면 ValueError. 설정 파일의 모르는 mode 는 local 로 읽는다 —
    사이드카의 `/config` 도 모르는 값을 무시한다(api/server.py)."""
    chosen = explicit or os.environ.get("VINC_TARGET")
    if chosen:
        if chosen not in ("local", "account"):
            raise ValueError("target must be local or account")
        return chosen
    mode = read_local_config().get("mode")
    return mode if mode in ("local", "account") else "local"


def resolve_space(explicit: str | None, tool_args: dict | None = None) -> str:
    """`--space` > 도구 인자의 space > `VINC_SPACE` > personal. `/mcp` 처럼 소문자로 맞춘다."""
    value = explicit or (tool_args or {}).get("space") or os.environ.get("VINC_SPACE") or "personal"
    return str(value).strip().lower() or "personal"
