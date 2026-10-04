"""CLI result formatting and process status."""
from __future__ import annotations

import contextvars
import json
import os
import sys
from typing import Any


_format: contextvars.ContextVar[str | None] = contextvars.ContextVar("vinc_cli_format", default=None)
_command: contextvars.ContextVar[str] = contextvars.ContextVar("vinc_cli_command", default="")

# 목록 필드는 도구별 공개 응답 계약이다. 첫 행을 보고 추측하지 않는다.
LIST_FIELDS = {
    "recall": "rows", "domains": "rows", "search": "rows",
    "doc ls": "docs", "graph": "nodes", "decide": "nodes",
    "node neighbors": "neighbors", "merge candidates": "candidates",
    "merge rejections": "rejections", "diagnose": "components",
}


def configure(fmt: str | None, command: str) -> None:
    _format.set(fmt)
    _command.set(command)


def chosen_format() -> str:
    value = _format.get() or os.environ.get("VINC_FORMAT")
    if value not in (None, "human", "json", "ndjson"):
        raise ValueError("VINC_FORMAT must be human, json, or ndjson")
    return value or ("human" if sys.stdout.isatty() else "json")


def exit_code(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    if result.get("executed") is False or result.get("isError") is True or result.get("error"):
        return 1
    if result.get("status") == "none":
        return 1
    return 0


def _human(result: Any, command: str) -> str:
    if not isinstance(result, dict):
        return str(result)
    if command == "doc get" and result.get("text") is not None:
        return str(result["text"])
    if command == "brief":
        lines = [str(result.get("one_liner") or result.get("status") or "")]
        lines.extend(str(row.get("excerpt", "")) for row in result.get("excerpts", []) if isinstance(row, dict))
        return "\n".join(line for line in lines if line)
    field = LIST_FIELDS.get(command)
    rows = result.get(field) if field else None
    if isinstance(rows, list):
        rendered = []
        for row in rows:
            if isinstance(row, dict):
                rendered.append("\t".join(str(row.get(k, "")) for k in ("id", "title", "domain") if k in row)
                                or json.dumps(row, ensure_ascii=False))
            else:
                rendered.append(str(row))
        return "\n".join(rendered)
    return json.dumps(result, ensure_ascii=False, indent=2)


def emit(result: Any) -> int:
    fmt = chosen_format()
    code = exit_code(result)
    if fmt == "human" and code:
        error = result.get("error") if isinstance(result, dict) else None
        message = error.get("message", "") if isinstance(error, dict) else error
        print("vinc: " + str(message or "operation failed").removeprefix("vinc: "), file=sys.stderr)
        return code
    if fmt == "human":
        rendered = _human(result, _command.get())
        sys.stdout.write(rendered + ("\n" if rendered and not rendered.endswith("\n") else ""))
    elif fmt == "ndjson" and code == 0:
        field = LIST_FIELDS.get(_command.get())
        rows = result.get(field) if field and isinstance(result, dict) else None
        if isinstance(rows, list):
            for row in rows:
                sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    elif fmt == "ndjson":
        # 실패도 한 줄이다 — ndjson 소비자는 줄 단위로만 읽는다.
        sys.stderr.write(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        # 0.9.6 이전 JSON 바이트 형식을 그대로 둔다.
        stream = sys.stderr if code else sys.stdout
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return code


def usage_error(message: str) -> int:
    emit({"kind": "error", "executed": False,
          "error": {"code": "invalid_argument", "message": message}})
    return 2


def internal_error(exc: BaseException, *, debug: bool = False) -> int:
    result = {"kind": "error", "executed": False,
              "error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}}
    if debug or os.environ.get("VINC_DEBUG") == "1":
        import traceback
        traceback.print_exception(exc, file=sys.stderr)
    return emit(result)
