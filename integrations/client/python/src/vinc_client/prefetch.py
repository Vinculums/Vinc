"""Retrieve a chosen role's reading list and task evidence before the model runs."""
from __future__ import annotations

import json
from typing import Any

from .context import BOUNDARY, fence, format_answer
from .errors import VincInvalidArgument

DEFAULT_PREFETCH_MAX_CHARS = 24000


def reading_list(contract: dict[str, Any]) -> list[str]:
    """Keep all declared reads in order; refuse malformed or unbounded contracts."""
    props = contract.get("props")
    if not contract.get("found") or not isinstance(props, dict) or not contract.get("id"):
        raise VincInvalidArgument("invalid_role_contract", "The selected role is unavailable.")
    reads = props.get("reads", [])
    if (not isinstance(reads, list) or len(reads) > 32
            or any(not isinstance(r, str) or not r.strip() or len(r) > 2048 for r in reads)):
        raise VincInvalidArgument("invalid_role_contract", "Invalid role reading list.")
    return list(dict.fromkeys(reads))


def render_prefetch(contract: dict[str, Any], task: str, specs: list[tuple[str, dict]],
                    brief: dict, search: dict, *, max_chars: int) -> str:
    """Fence the complete role contract and retain returned citation ids in evidence blocks."""
    role = {k: contract[k] for k in ("id", "title", "domain", "props", "works_with")
            if k in contract}
    parts = [f"{BOUNDARY}\n\n{fence(json.dumps(role, ensure_ascii=False, indent=2))}"]
    for doc_id, result in specs:
        if result.get("status") != "resolved":
            raise VincInvalidArgument("prefetch_spec_unresolved",
                                      "A required role reading could not be resolved.")
        block = format_answer(result, max_chars=4000)
        if not block:
            raise VincInvalidArgument("prefetch_spec_unresolved",
                                      "A required role reading could not be resolved.")
        parts.append(f"Required reading: {doc_id}\n{block}")
    block = format_answer(brief, search, max_chars=6000)
    if block:
        parts.append(block)
    joined = "\n\n".join(parts)
    if len(joined) > max_chars:
        raise VincInvalidArgument("prefetch_budget_exceeded",
                                  "Required role context exceeds max_chars; raise the budget.")
    return joined
