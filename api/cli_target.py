"""Resolve the CLI graph target and account space from explicit inputs."""
from __future__ import annotations

import os

from store.config import read_local_config


def resolve_target(explicit: str | None) -> str:
    target = (explicit or os.environ.get("VINC_TARGET") or
              read_local_config().get("mode") or "local")
    if target not in ("local", "account"):
        raise ValueError("target must be local or account")
    return target


def resolve_space(explicit: str | None, tool_args: dict | None = None) -> str:
    return explicit or (tool_args or {}).get("space") or os.environ.get("VINC_SPACE") or "personal"
