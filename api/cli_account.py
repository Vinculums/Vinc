"""Account-target CLI dispatch through REST v1."""
from __future__ import annotations

import json
import sys

from vinc_mcp import tools

from .cli_target import resolve_space
from .cli_tools import _confirm, _tool_request
from .remote_client import RemoteFailure, call


def run(args, *, space: str) -> tuple[dict | None, int | None]:
    try:
        name, data, destructive = _tool_request(args)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"vinc: {exc}", file=sys.stderr)
        return None, 2
    if destructive and not _confirm(args, destructive):
        print("vinc: confirmation required; pass --yes", file=sys.stderr)
        return None, 2
    try:
        tools.validate_args(name, data)
    except tools.InvalidArgument as exc:
        print(f"vinc: {exc}", file=sys.stderr)
        return None, 2
    try:
        return call(name, data, space=resolve_space(getattr(args, "space", None), data)), None
    except RemoteFailure as exc:
        return exc.result, exc.exit_code
    except ValueError as exc:
        print(f"vinc: {exc}", file=sys.stderr)
        return None, 2
