"""Retrieve a chosen role's reading list and task evidence before the model runs."""
from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple, Sequence

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


class RolePrefetch(NamedTuple):
    """A prefetch block and the role contract it was built from, read in the same call."""

    block: str
    contract: dict[str, Any]


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


def citation_policy(contract: dict[str, Any], specs: list[tuple[str, dict]], *,
                    extra_ids: Sequence[str] = (), extra_uncitable: Sequence[str] = ()) -> str:
    """Repeat the citation contract after long readings with exact verified source ids.

    ``extra_ids`` and ``extra_uncitable`` add what a context compiler verified beyond the
    required readings; left empty, the text is prefetch's, byte for byte.
    """
    ids = [contract["id"]]
    ids.extend(result["cite_id"] for read, result in specs
               if result.get("cite_id") == cite_id(read))
    sources = " ".join(f"[{node_id}]" for node_id in dict.fromkeys(ids))
    uncitable = [document_key(read) for read, result in specs
                 if result.get("cite_id") != cite_id(read)]
    extra = ""
    if extra_ids:
        extra += ("Other verified source IDs in this context: "
                  + " ".join(f"[{node_id}]" for node_id in dict.fromkeys(extra_ids)) + "\n")
    if extra_uncitable:
        extra += ("Other document keys with NO citable node in this space: "
                  f"{json.dumps(list(dict.fromkeys(extra_uncitable)), ensure_ascii=False)}\n")
    return ("Citation contract for this run:\n"
            f"Verified role and required-reading source IDs: {sources}\n"
            f"Required document keys with NO citable node in this space: {json.dumps(uncitable, ensure_ascii=False)}\n"
            + extra +
            "These uncitable documents are available as reference text, but must not appear "
            "in Sources. Adding concept:doc. to one of their keys creates an unverified ID. "
            "For the supplied role/readings, select Sources IDs only from the verified list above. "
            "Copy source IDs exactly; do not add prefixes, derive an ID from a document key, "
            "or use an ID merely mentioned inside a required reading. Live tool results may "
            "provide additional citable IDs. Cite only sources you actually relied on. "
            "An answer that uses the supplied role contract or required readings cannot "
            "claim Sources: none. A reading marked no citable id has no verified mirror; "
            "do not invent one.\n"
            f"If you relied only on the role contract, the exact final line is Sources: [{contract['id']}].\n"
            + SOURCES_LINE)


def required_context(contract: dict[str, Any], specs: list[tuple[str, dict]], *,
                     max_chars: int) -> tuple[str, str, int]:
    """Assemble and budget the full required context before using task evidence.

    Pure formatting of this call's verified responses; it performs no reads or caching.
    Both client modes and the renderer use the same citation-inclusive size.
    """
    parts = [role_context(contract)]
    for doc_id, result in specs:
        parts.append(required_reading(doc_id, result))
    joined = "\n\n".join(parts)
    footer = citation_policy(contract, specs)
    required_size = len(joined) + 2 + len(footer)
    check_budget(required_size, max_chars)
    return joined, footer, required_size


def render_prefetch(contract: dict[str, Any], task: str, specs: list[tuple[str, dict]],
                    brief: dict, search: dict, *, max_chars: int) -> str:
    required = required_context(contract, specs, max_chars=max_chars)
    return _with_task_evidence(required, brief, search, max_chars=max_chars)


def _with_task_evidence(required: tuple[str, str, int], brief: dict, search: dict, *,
                        max_chars: int) -> str:
    # 이 호출에서 이미 검증·예산 확인한 문자열을 재사용한다. 호출 사이에는 보관하지 않는다.
    joined, footer, required_size = required
    block = format_answer(brief, search,
                          max_chars=min(TASK_EVIDENCE_MAX_CHARS, max_chars - required_size - 2))
    if block:
        joined += "\n\n" + block
    return joined + "\n\n" + footer
