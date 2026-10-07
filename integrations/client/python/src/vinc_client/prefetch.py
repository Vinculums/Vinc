"""Retrieve a chosen role's reading list and task evidence before the model runs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .context import BOUNDARY, fence, format_answer
from .errors import VincInvalidArgument

DEFAULT_PREFETCH_MAX_CHARS = 24000
PREFETCH_READ_CONCURRENCY = 8
TASK_EVIDENCE_MAX_CHARS = 6000
# 블록 머리의 인용 지시. 모델은 문서 키를 인용할 수 없으므로 필수 문서마다 미러 노드 id 를 준다.
# 근거: [[record:vin314-live-exp01-rerun-prefetch-cites-a-node-in-1-of-6-2026-10-07]]
CITE_LINE = ("Each required reading below names its citable id after \"cite as\"; "
             "cite that id, not the document key.")


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


def cite_id(read: str) -> str:
    """The citable mirror node of a required document (``concept:doc.<key>``)."""
    # ponytail: 미러 존재 확인이 없다(문서 응답에 미러 id 가 없고 확인은 문서마다 요청 하나다).
    # 실모델 재실행이 없는 미러를 인용하면 GET /v1/nodes/{id} 확인으로 올린다.
    return "concept:doc." + document_key(read)


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
    return (f"Required reading (full canonical document): {doc_id} (cite as {cite_id(doc_id)})"
            f"\n{BOUNDARY}\n\n") + fence(
        json.dumps(data, ensure_ascii=False, indent=2))


def check_budget(size: int, max_chars: int) -> None:
    if size > max_chars:
        recommended = size + 2 + TASK_EVIDENCE_MAX_CHARS
        raise VincInvalidArgument("prefetch_budget_exceeded",
                                  f"Full required role context needs required_size={size} characters; "
                                  f"max_chars={max_chars}. Set max_chars={size} for required context "
                                  f"or max_chars={recommended} to also reserve task evidence space.",
                                  details={"required_size": size, "max_chars": max_chars,
                                           "recommended_max_chars": recommended})


def role_context(contract: dict[str, Any]) -> str:
    """Head of the prefetch block: the cite line, then the complete fenced role contract."""
    role = {k: contract[k] for k in ("id", "title", "domain", "props", "works_with")
            if k in contract}
    # 인용 지시는 여기(블록 머리)에 둔다. required_size 가 이 함수 길이로 시작하므로 예산에 함께 잡힌다.
    return f"{CITE_LINE}\n{BOUNDARY}\n\n{fence(json.dumps(role, ensure_ascii=False, indent=2))}"


def render_prefetch(contract: dict[str, Any], task: str, specs: list[tuple[str, dict]],
                    brief: dict, search: dict, *, max_chars: int) -> str:
    parts = [role_context(contract)]
    for doc_id, result in specs:
        parts.append(required_reading(doc_id, result))
    joined = "\n\n".join(parts)
    check_budget(len(joined), max_chars)
    block = format_answer(brief, search,
                          max_chars=min(TASK_EVIDENCE_MAX_CHARS, max_chars - len(joined) - 2))
    if block:
        joined += "\n\n" + block
    return joined
