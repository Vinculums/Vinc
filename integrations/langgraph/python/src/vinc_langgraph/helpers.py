"""Helpers for graph nodes: read context, build a system message, expose read tools, record.

None of these writes on its own. ``record_episode`` writes because you call it,
which in a graph belongs in a node after an ``interrupt()`` a person resumed.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from vinc_client import (AsyncVincClient, VincClient, VincQuotaExceeded, VincUnavailable,
                         format_brief, format_search)
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS


def last_user_text(messages: str | Sequence[Any]) -> str:
    """The newest human message's text; a plain string is returned as is."""
    if isinstance(messages, str):
        return messages.strip()
    for message in reversed(list(messages or [])):
        if isinstance(message, HumanMessage) or getattr(message, "type", "") == "human":
            text = getattr(message, "text", None)
            # 1.x 에서 .text 는 속성이다(호출하면 폐기 경고). 문자열이 아닌 호출체일 때만 부른다.
            if callable(text) and not isinstance(text, str):
                text = text()
            return str(text if text is not None else message.content).strip()
    return ""


def get_vinc_context(client: VincClient, messages: str | Sequence[Any], *,
                     search_fallback: bool = True, max_chars: int = DEFAULT_MAX_CHARS,
                     max_excerpts: int = 3) -> str | None:
    """A fenced context block for the newest human message, or ``None``.

    ``None`` also when Vinc is unavailable or the quota is spent; a key or space
    problem raises.
    """
    return client.get_context(last_user_text(messages), search_fallback=search_fallback,
                              max_chars=max_chars, max_excerpts=max_excerpts)


async def aget_vinc_context(client: AsyncVincClient, messages: str | Sequence[Any], *,
                            search_fallback: bool = True, max_chars: int = DEFAULT_MAX_CHARS,
                            max_excerpts: int = 3) -> str | None:
    """Async :func:`get_vinc_context`."""
    return await client.get_context(last_user_text(messages), search_fallback=search_fallback,
                                    max_chars=max_chars, max_excerpts=max_excerpts)


def get_vinc_package_context(client: VincClient, package_id: str, *, space: str,
                             max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Read a handoff by id from graph state, checking current source access on every call."""
    return client.get_package_context(package_id, space=space, max_chars=max_chars)


async def aget_vinc_package_context(client: AsyncVincClient, package_id: str, *, space: str,
                                   max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> str:
    """Async :func:`get_vinc_package_context`; missing packages fail explicitly."""
    return await client.get_package_context(package_id, space=space, max_chars=max_chars)


def build_system_message(block: str | None, base: SystemMessage | str | None = None
                         ) -> SystemMessage | None:
    """Append ``block`` to ``base`` (or start one). Returns ``base`` unchanged when there
    is no block, and ``None`` when there is neither."""
    if isinstance(base, str):
        base = SystemMessage(content=base)
    if not block:
        return base
    if base is None:
        return SystemMessage(content=block)
    return SystemMessage(content=[*base.content_blocks, {"type": "text", "text": block}])


def prefetch(client: VincClient, role_id: str, task: str, *, space: str | None = None,
             max_chars: int = DEFAULT_PREFETCH_MAX_CHARS) -> str:
    """Fetch a chosen role's reading list and task evidence before a graph model node."""
    return client.prefetch(role_id, task, space=space, max_chars=max_chars)


async def aprefetch(client: AsyncVincClient, role_id: str, task: str, *, space: str | None = None,
                    max_chars: int = DEFAULT_PREFETCH_MAX_CHARS) -> str:
    """Async :func:`prefetch`."""
    return await client.prefetch(role_id, task, space=space, max_chars=max_chars)


class _BriefArgs(BaseModel):
    query: str = Field(description="A question, or an exact node id such as decision:some-slug.")


class _SearchArgs(BaseModel):
    query: str = Field(description="What to look for.")
    limit: int = Field(default=5, ge=1, le=20, description="How many matches of each kind.")


_DOWN = "Vinc is not available right now ({code}); answer without it."


def create_vinc_tools(client: VincClient | None = None,
                      async_client: AsyncVincClient | None = None, *,
                      max_chars: int = DEFAULT_MAX_CHARS) -> list[StructuredTool]:
    """Two read-only tools, ``vinc_brief`` and ``vinc_search``.

    Pass a sync client, an async client, or both; each tool gets the paths it
    has a client for. Nothing here can write.
    """
    if client is None and async_client is None:
        raise ValueError("pass a VincClient, an AsyncVincClient, or both")

    def brief(query: str) -> str:
        try:
            return (format_brief(client.brief(query), max_chars=max_chars)  # type: ignore[union-attr]
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    async def abrief(query: str) -> str:
        try:
            return (format_brief(await async_client.brief(query), max_chars=max_chars)  # type: ignore[union-attr]
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    def search(query: str, limit: int = 5) -> str:
        try:
            return (format_search(client.search(query, limit=limit),  # type: ignore[union-attr]
                                  max_chars=max_chars, limit=limit) or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    async def asearch(query: str, limit: int = 5) -> str:
        try:
            return (format_search(await async_client.search(query, limit=limit),  # type: ignore[union-attr]
                                  max_chars=max_chars, limit=limit) or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    return [
        StructuredTool.from_function(
            func=brief if client else None, coroutine=abrief if async_client else None,
            name="vinc_brief", args_schema=_BriefArgs,
            description="Look up one node in the user's Vinc knowledge graph: a decision, "
                        "record or concept with its summary, document excerpts and relations."),
        StructuredTool.from_function(
            func=search if client else None, coroutine=asearch if async_client else None,
            name="vinc_search", args_schema=_SearchArgs,
            description="Search the user's Vinc knowledge graph by text and meaning; returns "
                        "matching nodes and document passages with their ids."),
    ]


def record_episode(client: VincClient, title: str, *, summary: str = "",
                   about: Iterable[str] = (), date: str | None = None,
                   domain: str | None = None, tags: Iterable[str] | None = None,
                   space: str | None = None) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions (needs a ``vinc_sk_`` key).

    Put it in the node that runs after a person resumed an ``interrupt()``.
    """
    return client.record_episode(title, summary=summary, about=about, date=date,
                                 domain=domain, tags=tags, space=space)


async def arecord_episode(client: AsyncVincClient, title: str, *, summary: str = "",
                          about: Iterable[str] = (), date: str | None = None,
                          domain: str | None = None, tags: Iterable[str] | None = None,
                          space: str | None = None) -> dict[str, Any]:
    """Async :func:`record_episode`."""
    return await client.record_episode(title, summary=summary, about=about, date=date,
                                       domain=domain, tags=tags, space=space)


__all__ = ["last_user_text", "get_vinc_context", "aget_vinc_context",
           "build_system_message", "create_vinc_tools", "record_episode", "arecord_episode",
           "get_vinc_package_context", "aget_vinc_package_context", "write_package", "awrite_package",
           "prefetch", "aprefetch"]


def write_package(client: VincClient, question: str, conclusion: str, *,
                  claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                  space: str, package_id: str | None = None) -> dict[str, Any]:
    """Explicit producer write; keep it in a graph node reached after review."""
    return client.write_package(question, conclusion, claims=claims,
                                produced_by=produced_by, run_id=run_id,
                                space=space, package_id=package_id)


async def awrite_package(client: AsyncVincClient, question: str, conclusion: str, *,
                         claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                         space: str, package_id: str | None = None) -> dict[str, Any]:
    """Async :func:`write_package`."""
    return await client.write_package(question, conclusion, claims=claims,
                                      produced_by=produced_by, run_id=run_id,
                                      space=space, package_id=package_id)
