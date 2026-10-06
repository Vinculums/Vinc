"""Explicit, same-space handoff records; references are checked as the current reader.

Contract: [[vinc.plan.framework-integration-packages-2026-09-28]].
Cross-space locators need a separate server contract; this first version never copies sources.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, Iterable

from .context import BOUNDARY, evidence_metadata, fence
from .errors import VincInvalidArgument

PACKAGE_SCHEMA = "vinc.package.v1"
DEFAULT_PACKAGE_MAX_CHARS = 16000


def package_space(space: str) -> str:
    """Require an explicit target, including an explicit ``personal`` selection."""
    from .client import normalize_space
    if not isinstance(space, str) or not space.strip():
        raise ValueError("a package requires an explicit space")
    return normalize_space(space)


def _invalid() -> VincInvalidArgument:
    return VincInvalidArgument("invalid_package", "Invalid vinc.package.v1 handoff record.")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 16384


def validate_package(node: dict[str, Any]) -> list[str]:
    """Validate the carrier and return distinct source ids without rewriting its fields."""
    nid = node.get("id")
    props = node.get("props")
    if (not isinstance(nid, str) or not nid.startswith("record:")
            or node.get("kind", node.get("type")) != "record" or not isinstance(props, dict)
            or props.get("package_schema") != PACKAGE_SCHEMA
            or not all(_text(props.get(key)) for key in
                       ("question", "conclusion", "produced_by", "run_id"))):
        raise _invalid()
    claims = props.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 100:
        raise _invalid()
    sources: dict[str, None] = {}
    for claim in claims:
        if (not isinstance(claim, dict) or set(claim) != {"claim", "evidence_ids"}
                or not _text(claim["claim"])):
            raise _invalid()
        ids = claim["evidence_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 256:
            raise _invalid()
        for source in ids:
            if (not isinstance(source, str) or not source.strip() or source != source.strip()
                    or len(source) > 2048 or "\x1f" in source or source == nid):
                raise _invalid()
            sources[source] = None
    if len(sources) > 256:
        raise _invalid()
    return list(sources)


def build_package_fragment(question: str, conclusion: str, *,
                           claims: Iterable[dict[str, Any]], produced_by: str,
                           run_id: str, title: str | None = None,
                           package_id: str | None = None) -> dict[str, Any]:
    """One Record, no source stubs or edges. Persisted evidence must already exist."""
    nid = package_id or f"record:package-{secrets.token_hex(12)}"
    node = {"id": nid, "type": "record", "title": title or question,
            "props": {"package_schema": PACKAGE_SCHEMA, "question": question,
                      "conclusion": conclusion, "claims": list(claims),
                      "produced_by": produced_by, "run_id": run_id}}
    validate_package(node)
    return {"nodes": [node], "edges": []}


def format_package(node: dict[str, Any], *, space: str, readable_ids: Iterable[str],
                   evidence_nodes: dict[str, dict[str, Any]] | None = None,
                   max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Preserve complete claims and provenance; mark unavailable evidence as uncitable.

    No source content is copied. An unavailable id is replaced with an unresolved
    marker. Exceeding the budget raises rather than silently changing a conclusion.
    """
    validate_package(node)
    selected = package_space(space)
    readable = set(readable_ids)
    sources = evidence_nodes or {}

    def evidence(nid: str) -> dict[str, Any]:
        state = evidence_metadata(sources.get(nid, {}))
        current = (False if state.get("superseded_by") or state.get("validity") in
                   ("historical", "future") else True if state.get("validity") == "current"
                   else None)
        return {"space": selected, "node_id": nid, "readable": True,
                "use_as_current": current, **state}

    props = node["props"]
    claims = [{"claim": claim["claim"],
               "evidence": [evidence(nid) for nid in
                            dict.fromkeys(claim["evidence_ids"]) if nid in readable],
               "evidence_status": ("resolved" if all(nid in readable for nid in
                                                     claim["evidence_ids"])
                                   else "unresolved; do not cite this claim")}
              for claim in props["claims"]]
    body = json.dumps({"package": {"space": selected, "node_id": node["id"]},
                       "package_state": evidence_metadata(node),
                       "evidence_status_meaning": "resolved means readable, not current; "
                                                  "null use_as_current means unknown",
                       "question": props["question"], "conclusion": props["conclusion"],
                       "produced_by": props["produced_by"], "run_id": props["run_id"],
                       "claims": claims}, ensure_ascii=False, indent=2)
    block = f"{BOUNDARY}\n\n{fence(body)}"
    if len(block) > max_chars:
        raise VincInvalidArgument("package_budget_exceeded",
                                  "The complete package exceeds max_chars; raise the budget.")
    return block
