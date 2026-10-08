"""Retrieve a chosen role's reading list and task evidence before the model runs."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .context import BOUNDARY, SOURCES_LINE, fence, format_answer
from .errors import VincInvalidArgument, VincUnavailable

DEFAULT_PREFETCH_MAX_CHARS = 24000
PREFETCH_READ_CONCURRENCY = 8
TASK_EVIDENCE_MAX_CHARS = 6000
# 블록 머리의 인용 지시. 같은 스페이스에서 확인한 미러만 인용 id 로 준다.
# 뒤 문장(SOURCES_LINE)은 context 모드와 같은 바이트다.
# 근거: [[record:vin314-live-exp01-rerun-prefetch-cites-a-node-in-1-of-6-2026-10-07]]
CITE_LINE = ("Required readings with a verified mirror name their citable id after \"cite as\"; "
             "cite that id, not the document key. Readings marked \"no citable id\" "
             "have no verified node citation; do not invent one. " + SOURCES_LINE)


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
    """Candidate mirror id; the same-space node read must verify it before citation."""
    return "concept:doc." + document_key(read)


def with_citation(read: str, document: dict[str, Any], node_result: dict[str, Any]) -> dict[str, Any]:
    """Attach only the id observed by a fresh node read, never the node's private body."""
    # 문서 접근권과 미러 접근권은 따로 확인한다. 조회 실패를 미러 부재로 취급하지 않는다.
    if node_result.get("executed") is not True or node_result.get("error"):
        raise VincUnavailable("prefetch_citation_unresolved", "A required reading's mirror could not be checked.")
    node = node_result.get("node")
    if node_result.get("found") is False and node is None:
        return {**document, "cite_id": None}
    if (node_result.get("found") is not True or not isinstance(node, dict)
            or node.get("id") != cite_id(read)):
        raise VincUnavailable("prefetch_citation_unresolved", "A required reading's mirror response is invalid.")
    return {**document, "cite_id": node["id"]}


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
    citation = (f"cite as {result['cite_id']}" if result.get("cite_id") == cite_id(doc_id)
                else "no citable id")
    return (f"Required reading (full canonical document): {doc_id} ({citation})"
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


def citation_policy(contract: dict[str, Any], specs: list[tuple[str, dict]]) -> str:
    """Repeat the citation contract after long readings with exact verified source ids."""
    ids = [contract["id"]]
    ids.extend(result["cite_id"] for read, result in specs
               if result.get("cite_id") == cite_id(read))
    sources = " ".join(f"[{node_id}]" for node_id in dict.fromkeys(ids))
    return ("Citation contract for this run:\n"
            f"Verified role and required-reading source IDs: {sources}\n"
            "Copy source IDs exactly; do not add prefixes, derive an ID from a document key, "
            "or use an ID merely mentioned inside a required reading. Live tool results may "
            "provide additional citable IDs. Cite only sources you actually relied on. "
            "An answer that uses the supplied role contract or required readings cannot "
            "claim Sources: none. A reading marked no citable id has no verified mirror; "
            "do not invent one.\n" + SOURCES_LINE)


def render_prefetch(contract: dict[str, Any], task: str, specs: list[tuple[str, dict]],
                    brief: dict, search: dict, *, max_chars: int) -> str:
    parts = [role_context(contract)]
    for doc_id, result in specs:
        parts.append(required_reading(doc_id, result))
    joined = "\n\n".join(parts)
    footer = citation_policy(contract, specs)
    required_size = len(joined) + 2 + len(footer)
    check_budget(required_size, max_chars)
    block = format_answer(brief, search,
                          max_chars=min(TASK_EVIDENCE_MAX_CHARS, max_chars - required_size - 2))
    if block:
        joined += "\n\n" + block
    return joined + "\n\n" + footer
