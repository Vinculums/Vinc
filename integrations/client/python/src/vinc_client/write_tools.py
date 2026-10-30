"""What the two write tools do when the model calls them, shared by every framework package.

Each runner returns a short sentence for the model instead of raising, as the read tools do:
a partial write names what was skipped, and an unavailable hub or a refused write says so.
"""
from __future__ import annotations

from typing import Any

from .errors import VincError, VincPartialWrite, VincUnavailable

_DOWN = "Vinc is not available right now ({code}); nothing was saved. Say so in your answer."
_REFUSED = "Vinc refused the write ({code}): {message}"


def _merged(result: dict[str, Any]) -> str:
    merged = result.get("merged") or {}
    nodes = merged.get("nodes") or {}
    edges = merged.get("edges") or {}
    return (f"Saved: {nodes.get('created', 0)} nodes created, {nodes.get('matched', 0)} updated, "
            f"{edges.get('created', 0)} edges created.")


def _partial(exc: VincPartialWrite) -> str:
    warnings = [str(w) for w in (exc.result.get("warnings") or [])][:5]
    return "Saved in part. Skipped: " + "; ".join(warnings) if warnings else "Saved in part."


def _doc(result: dict[str, Any]) -> str:
    return (f"Saved document {result.get('doc_id')} "
            f"(content_hash {result.get('content_hash')}).")


def _failed(exc: VincError) -> str:
    if isinstance(exc, VincUnavailable):
        return _DOWN.format(code=exc.code)
    return _REFUSED.format(code=exc.code, message=str(exc)[:300])


def run_ingest(client: Any, nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None) -> str:
    try:
        return _merged(client.ingest(nodes, edges))
    except VincPartialWrite as exc:
        return _partial(exc)
    except VincError as exc:
        return _failed(exc)


async def arun_ingest(client: Any, nodes: list[dict[str, Any]],
                      edges: list[dict[str, Any]] | None = None) -> str:
    try:
        return _merged(await client.ingest(nodes, edges))
    except VincPartialWrite as exc:
        return _partial(exc)
    except VincError as exc:
        return _failed(exc)


def run_doc_put(client: Any, text: str, doc_id: str | None = None, title: str | None = None,
                valid_from: str | None = None, expected_content_hash: str | None = None) -> str:
    try:
        return _doc(client.doc_put(text, doc_id=doc_id, title=title, valid_from=valid_from,
                                   expected_content_hash=expected_content_hash))
    except VincError as exc:
        return _failed(exc)


async def arun_doc_put(client: Any, text: str, doc_id: str | None = None, title: str | None = None,
                       valid_from: str | None = None,
                       expected_content_hash: str | None = None) -> str:
    try:
        return _doc(await client.doc_put(text, doc_id=doc_id, title=title, valid_from=valid_from,
                                         expected_content_hash=expected_content_hash))
    except VincError as exc:
        return _failed(exc)
