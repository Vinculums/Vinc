"""Resolve the CLI graph target and account space from explicit inputs."""
from __future__ import annotations

import os

from store.config import read_local_config

#: MCP 도구로 답하는 동사. 대상(local·account)을 고르는 것은 이 동사들뿐이다.
#: 나머지는 이 기기 전용이라 설정의 mode·VINC_TARGET·VINC_SPACE 를 따르지 않는다 —
#: 앱은 계정 모드에서도 `serve` 를 플래그 없이 띄운다(app/src-tauri/src/main.rs).
TOOL_CMDS = frozenset({"whoami", "conventions", "node", "path", "ties", "diagnose",
                       "ingest", "delete", "edge", "merge", "call", "search", "recall",
                       "brief", "decide", "domains", "doc"})


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
