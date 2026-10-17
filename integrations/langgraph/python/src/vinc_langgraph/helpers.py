"""Helpers for graph nodes: read context, build a system message, expose read tools, record.

None of these writes on its own. ``record_episode`` writes because you call it,
which in a graph belongs in a node after an ``interrupt()`` a person resumed.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from vinc_client import (AsyncVincClient, VincClient, VincNotFound, VincQuotaExceeded,
                         VincUnavailable, format_brief, format_search)
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tools import (BRIEF_DESCRIPTION, BRIEF_NAME, SEARCH_DESCRIPTION,
                               SEARCH_LIMIT_DEFAULT, SEARCH_NAME, BriefArgs, SearchArgs, temporal_options)


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
    if isinstance(base.content, str):
        # 일반 텍스트는 Agent Framework와 같은 문자열·줄바꿈으로 보낸다.
        content = f"{base.content}\n{block}" if base.content else block
    else:
        content = [*base.content_blocks, {"type": "text", "text": block}]
    return base.model_copy(update={"content": content})


def prefetch(client: VincClient, role_id: str, task: str, *, space: str | None = None,
             max_chars: int = DEFAULT_PREFETCH_MAX_CHARS) -> str:
    """Fetch a chosen role's reading list and task evidence before a graph model node."""
    return client.prefetch(role_id, task, space=space, max_chars=max_chars)


async def aprefetch(client: AsyncVincClient, role_id: str, task: str, *, space: str | None = None,
                    max_chars: int = DEFAULT_PREFETCH_MAX_CHARS) -> str:
    """Async :func:`prefetch`."""
    return await client.prefetch(role_id, task, space=space, max_chars=max_chars)


_DOWN = "Vinc is not available right now ({code}); answer without it."
# 없는 id(코드 not_found)만 도구 결과로 돌려 실행을 끝내지 않는다. space_not_found 같은 설정
# 오류는 그대로 올린다. Agent Framework 쪽과 같은 문장이다.
# 근거: [[record:vin318-live-exp02-r2-parity-tool-calls-2-vs-1-2026-10-07]]
_MISSING = "Vinc found nothing for that ({code}); try a different query."


def create_vinc_tools(client: VincClient | None = None,
                      async_client: AsyncVincClient | None = None, *,
                      max_chars: int = DEFAULT_MAX_CHARS) -> list[StructuredTool]:
    """Two read-only tools, ``vinc_brief`` and ``vinc_search``.

    Pass a sync client, an async client, or both; each tool gets the paths it
    has a client for. Nothing here can write.
    """
    if client is None and async_client is None:
        raise ValueError("pass a VincClient, an AsyncVincClient, or both")
    # 도구는 모델 턴 안에서 돈다. 허브를 기다리지 않고 곧장 문장으로 물러선다.
    client = client.without_retries() if client is not None else None
    async_client = async_client.without_retries() if async_client is not None else None

    def brief(query: str, mode: str | None = None, historical: bool | None = None,
                     as_of: str | None = None, known_at: str | None = None) -> str:
        try:
            return (format_brief(client.brief(query, **temporal_options(mode, historical, as_of, known_at)), max_chars=max_chars)  # type: ignore[union-attr]
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            return _MISSING.format(code=exc.code)

    async def abrief(query: str, mode: str | None = None, historical: bool | None = None,
                     as_of: str | None = None, known_at: str | None = None) -> str:
        try:
            return (format_brief(await async_client.brief(query, **temporal_options(mode, historical, as_of, known_at)), max_chars=max_chars)  # type: ignore[union-attr]
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            return _MISSING.format(code=exc.code)

    def search(query: str, limit: int = SEARCH_LIMIT_DEFAULT, mode: str | None = None,
                      historical: bool | None = None, as_of: str | None = None,
                      known_at: str | None = None) -> str:
        try:
            return (format_search(client.search(query, limit=limit, **temporal_options(mode, historical, as_of, known_at)),  # type: ignore[union-attr]
                                  max_chars=max_chars, limit=limit) or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    async def asearch(query: str, limit: int = SEARCH_LIMIT_DEFAULT, mode: str | None = None,
                      historical: bool | None = None, as_of: str | None = None,
                      known_at: str | None = None) -> str:
        try:
            return (format_search(await async_client.search(query, limit=limit, **temporal_options(mode, historical, as_of, known_at)),  # type: ignore[union-attr]
                                  max_chars=max_chars, limit=limit) or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    return [
        StructuredTool.from_function(
            func=brief if client else None, coroutine=abrief if async_client else None,
            name=BRIEF_NAME, args_schema=BriefArgs, description=BRIEF_DESCRIPTION),
        StructuredTool.from_function(
            func=search if client else None, coroutine=asearch if async_client else None,
            name=SEARCH_NAME, args_schema=SearchArgs, description=SEARCH_DESCRIPTION),
    ]


def record_episode(client: VincClient, title: str, *, summary: str = "",
                   about: Iterable[str] = (), date: str | None = None,
                   domain: str | None = None, tags: Iterable[str] | None = None,
                   space: str | None = None, episode_id: str | None = None,
                   create_only: bool | None = None,
                   operation_id: str | None = None) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions (needs a ``vinc_sk_`` key).

    Put it in the node that runs after a person resumed an ``interrupt()``.
    Save episode_id in host state before that boundary and reuse it only for this event's retries.
    """
    return client.record_episode(title, summary=summary, about=about, date=date,
                                 domain=domain, tags=tags, space=space, episode_id=episode_id,
                                 create_only=create_only, operation_id=operation_id)


async def arecord_episode(client: AsyncVincClient, title: str, *, summary: str = "",
                          about: Iterable[str] = (), date: str | None = None,
                          domain: str | None = None, tags: Iterable[str] | None = None,
                          space: str | None = None, episode_id: str | None = None,
                          create_only: bool | None = None,
                          operation_id: str | None = None) -> dict[str, Any]:
    """Async :func:`record_episode`."""
    return await client.record_episode(title, summary=summary, about=about, date=date,
                                       domain=domain, tags=tags, space=space, episode_id=episode_id,
                                       create_only=create_only, operation_id=operation_id)


__all__ = ["last_user_text", "get_vinc_context", "aget_vinc_context",
           "build_system_message", "create_vinc_tools", "record_episode", "arecord_episode",
           "get_vinc_package_context", "aget_vinc_package_context", "write_package", "awrite_package",
           "prefetch", "aprefetch"]


def write_package(client: VincClient, question: str, conclusion: str, *,
                  claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                  space: str, package_id: str | None = None,
                  create_only: bool | None = None,
                  operation_id: str | None = None) -> dict[str, Any]:
    """Explicit producer write; keep it in a graph node reached after review."""
    return client.write_package(question, conclusion, claims=claims,
                                produced_by=produced_by, run_id=run_id,
                                space=space, package_id=package_id,
                                create_only=create_only, operation_id=operation_id)


async def awrite_package(client: AsyncVincClient, question: str, conclusion: str, *,
                         claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                         space: str, package_id: str | None = None,
                         create_only: bool | None = None,
                         operation_id: str | None = None) -> dict[str, Any]:
    """Async :func:`write_package`."""
    return await client.write_package(question, conclusion, claims=claims,
                                      produced_by=produced_by, run_id=run_id,
                                      space=space, package_id=package_id,
                                      create_only=create_only, operation_id=operation_id)
