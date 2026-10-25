"""`vinc events tail`: 이벤트 피드(vinc_events)를 한 줄에 하나씩 낸다.

MCP Events 를 못 받는 호스트(터미널이 있는 클라우드 에이전트 등)가 쓰는 대체 문이다.
커서를 넘겨 이어 읽고, `--cursor-file` 이 있으면 매번 그 파일에 적어 재시작해도 이어진다.
같은 변경은 같은 eventId 로 다시 나올 수 있으므로 받는 쪽이 id 로 거른다.
근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import json
import os
import sys
import time

MIN_INTERVAL = 5.0


def read_cursor(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except FileNotFoundError:
        return None
    return text or None


def write_cursor(path: str, cursor: str) -> None:
    """임시 파일에 쓰고 바꿔 끼운다. 반쯤 쓴 커서는 남기지 않는다."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(cursor + "\n")
    os.replace(tmp, path)


def _ok(result) -> bool:
    return isinstance(result, dict) and result.get("executed") is True and not result.get("error")


def tail(args, call, emit, *, sleep=time.sleep, out=None) -> int:
    """`call()` 은 `(결과, 종료 코드)` 를 돌려주는 도구 호출 한 번이다(로컬 또는 계정)."""
    out = out or sys.stdout
    if args.since is None and args.cursor_file:
        args.since = read_cursor(args.cursor_file)
    if args.once:
        result, code = call()
        if result is None:
            return code or 2
        if code is None and args.cursor_file and _ok(result) and result.get("cursor"):
            write_cursor(args.cursor_file, result["cursor"])
        rendered = emit(result)
        return code if code is not None else rendered
    interval = max(MIN_INTERVAL, float(args.interval or 30))
    try:
        while True:
            result, code = call()
            if result is None:
                return code or 2
            if code is not None or not _ok(result):
                sys.stderr.write(json.dumps(result, ensure_ascii=False) + "\n")
                return code if code is not None else 1
            for event in result.get("events") or []:
                out.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
            cursor = result.get("cursor")
            if cursor:
                args.since = cursor
                if args.cursor_file:
                    write_cursor(args.cursor_file, cursor)
            if not result.get("has_more"):
                sleep(interval)
    except KeyboardInterrupt:
        return 0
