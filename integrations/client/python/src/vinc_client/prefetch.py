"""Retrieve a chosen role's reading list and task evidence before the model runs."""
from __future__ import annotations

import hashlib
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


def document_key(read: str) -> str:
    """Required reads are exact document keys, including explicit document mirrors."""
    return read.removeprefix("concept:doc.")


def required_reading(doc_id: str, result: dict[str, Any]) -> str:
    """Require a complete, verified canonical document; excerpts cannot satisfy a read."""
    text = result.get("text")
    if (result.get("doc_id") != document_key(doc_id) or not result.get("executed")
            or result.get("verified") is not True or result.get("partial")
            or result.get("error") or not isinstance(text, str) or not text.strip()
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != result.get("content_hash")):
        raise VincInvalidArgument("prefetch_spec_unresolved",
                                  "A required role reading is missing, incomplete or unverified.")
    data = {k: result[k] for k in ("doc_id", "title", "content_hash", "updated_at",
                                  "content_time", "verified", "text") if k in result}
    return f"Required reading (full canonical document): {doc_id}\n{BOUNDARY}\n\n" + fence(
        json.dumps(data, ensure_ascii=False, indent=2))


def check_budget(size: int, max_chars: int) -> None:
    if size > max_chars:
        raise VincInvalidArgument("prefetch_budget_exceeded",
                                  "Required role context exceeds max_chars; raise the budget.")


def render_prefetch(contract: dict[str, Any], task: str, specs: list[tuple[str, dict]],
                    brief: dict, search: dict, *, max_chars: int) -> str:
    """Fence the complete role contract and retain returned citation ids in evidence blocks."""
    role = {k: contract[k] for k in ("id", "title", "domain", "props", "works_with")
            if k in contract}
    parts = [f"{BOUNDARY}\n\n{fence(json.dumps(role, ensure_ascii=False, indent=2))}"]
    for doc_id, result in specs:
        parts.append(required_reading(doc_id, result))
    joined = "\n\n".join(parts)
    check_budget(len(joined), max_chars)
    block = format_answer(brief, search, max_chars=min(6000, max_chars - len(joined) - 2))
    if block:
        joined += "\n\n" + block
    return joined
