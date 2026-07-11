# -*- coding: utf-8 -*-
"""stdio 전송 — Claude Code·Claude Desktop 로컬 등록용(스켈레톤 결정 1).

MCP stdio 규약: 한 줄에 JSON 메시지 하나(newline-delimited). stdout 은
프로토콜 전용이므로 로그는 stderr 로만 낸다.
"""
from __future__ import annotations

import json
import sys

from .bridge import Bridge
from .rpc import Dispatcher


def serve(bridge: Bridge) -> int:
    dispatcher = Dispatcher(bridge)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print("vinc-mcp: skipping non-JSON line", file=sys.stderr)
                continue
            reply = dispatcher.handle(msg)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        bridge.close()
    return 0
