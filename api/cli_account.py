"""Account-target CLI dispatch through REST v1."""
from __future__ import annotations

import json

from vinc_mcp import tools

from .cli_target import resolve_space
from .cli_tools import _confirm, _tool_request
from .remote_client import RemoteFailure, call
from .cli_output import usage_error


def run(args, *, space: str) -> tuple[dict | None, int | None]:
    try:
        name, data, destructive = _tool_request(args)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, usage_error(str(exc))
    if destructive and not _confirm(args, destructive):
        return None, usage_error("confirmation required; pass --yes")
    try:
        tools.validate_args(name, data)
    except tools.InvalidArgument as exc:
        return None, usage_error(str(exc))
    try:
        return call(name, data, space=resolve_space(getattr(args, "space", None), data)), None
    except RemoteFailure as exc:
        return exc.result, exc.exit_code
    except ValueError as exc:
        return None, usage_error(str(exc))
