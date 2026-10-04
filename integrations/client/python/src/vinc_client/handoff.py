"""Check a producer package before quoting it into another role's input."""
from __future__ import annotations

from typing import Any, Iterable

from .output_gate import OutputGate, ToolProof
from .package import DEFAULT_PACKAGE_MAX_CHARS, format_package, package_space, validate_package


def check_package_handoff(node: dict[str, Any], *, space: str, gate: OutputGate,
                          readable_ids: Iterable[str], repositories: Iterable[str],
                          proofs: Iterable[ToolProof] = (),
                          max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Return the complete attributed package only after every handoff gate passes.

    The host supplies the producer's freshly read gate and tool records, and
    evidence IDs it resolved in this space under the current reader's permission.
    Never use model-generated proof dictionaries or reuse another reader's cache.
    This function does not read, write, route, dispatch or authorize any tool.
    """
    validate_package(node)
    selected = package_space(space)
    available = tuple(readable_ids)
    props = node["props"]
    # 저장 계약의 claim은 gate 계약의 what으로만 바꾼다. 문장을 다시 쓰지 않는다.
    manifest = {"producer": props["produced_by"], "run": props["run_id"],
                "claims": [{"what": claim["claim"], "evidence_ids": list(claim["evidence_ids"])}
                           for claim in props["claims"]]}
    answer = "\n".join([props["conclusion"], *[claim["claim"] for claim in props["claims"]]])
    gate.require_pass(answer, phase="handoff", manifest=manifest,
                      resolved_sources=available, proofs=proofs, repositories=repositories)
    return format_package(node, space=selected, readable_ids=available, max_chars=max_chars)
