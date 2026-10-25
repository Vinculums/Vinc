"""The read tools ``vinc_brief`` and ``vinc_search`` as CrewAI tools.

Names, descriptions, argument schemas, the per-run call cap and the sentences a tool
answers with come from ``vinc.sdk.tools``, the same declarations the LangGraph and
Agent Framework adapters build from. Nothing here can write.
"""
from __future__ import annotations

import asyncio
import threading

from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from vinc.sdk import (AsyncVincClient, VincClient, VincNotFound, VincQuotaExceeded,
                      VincUnavailable, format_brief, format_search)
from vinc.sdk.context import DEFAULT_MAX_CHARS
from vinc.sdk.tools import (BRIEF_DESCRIPTION, BRIEF_NAME, SEARCH_DESCRIPTION,
                            SEARCH_LIMIT_DEFAULT, SEARCH_NAME, BriefArgs, SearchArgs,
                            ToolCallCap, temporal_options)

# 두 어댑터와 같은 문장이다. 없는 id(not_found)만 도구 결과로 돌리고 설정 오류는 올린다.
# 근거: [[record:vin318-live-exp02-r2-parity-tool-calls-2-vs-1-2026-10-07]]
_DOWN = "Vinc is not available right now ({code}); answer without it."
_MISSING = "Vinc found nothing for that ({code}); try a different query."
# CrewAI 는 한 턴의 도구 호출을 여러 스레드로 함께 돌린다. 상한을 세는 일만 한 줄로 세운다.
_CAP_LOCK = threading.Lock()


class _VincTool(BaseTool):
    _client: VincClient | None = PrivateAttr(default=None)
    _async_client: AsyncVincClient | None = PrivateAttr(default=None)
    _cap: ToolCallCap | None = PrivateAttr(default=None)
    _max_chars: int = PrivateAttr(default=DEFAULT_MAX_CHARS)

    def _sync(self) -> VincClient:
        if self._client is None:
            raise RuntimeError(f"{self.name} has no VincClient; pass client= to create_vinc_tools")
        return self._client

    def _over_cap(self) -> str | None:
        # 상한은 인자 검증 뒤에 센다(CrewAI 가 스키마로 먼저 거른다). Agent Framework 와 같다.
        if self._cap is None:
            return None
        with _CAP_LOCK:
            spent = self._cap.spend()
        return None if spent else self._cap.message


class VincBriefTool(_VincTool):
    name: str = BRIEF_NAME
    description: str = BRIEF_DESCRIPTION
    args_schema: type[BaseModel] = BriefArgs

    def _run(self, query: str, mode: str | None = None, historical: bool | None = None,
             as_of: str | None = None, known_at: str | None = None) -> str:
        client = self._sync()
        capped = self._over_cap()
        if capped:
            return capped
        try:
            return (format_brief(client.brief(query, **temporal_options(mode, historical, as_of, known_at)),
                                 max_chars=self._max_chars)
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            return _MISSING.format(code=exc.code)

    async def _arun(self, query: str, mode: str | None = None, historical: bool | None = None,
                    as_of: str | None = None, known_at: str | None = None) -> str:
        if self._async_client is None:
            return await asyncio.to_thread(self._run, query, mode, historical, as_of, known_at)
        capped = self._over_cap()
        if capped:
            return capped
        try:
            return (format_brief(await self._async_client.brief(
                        query, **temporal_options(mode, historical, as_of, known_at)),
                        max_chars=self._max_chars)
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            return _MISSING.format(code=exc.code)


class VincSearchTool(_VincTool):
    name: str = SEARCH_NAME
    description: str = SEARCH_DESCRIPTION
    args_schema: type[BaseModel] = SearchArgs

    def _run(self, query: str, limit: int = SEARCH_LIMIT_DEFAULT, mode: str | None = None,
             historical: bool | None = None, as_of: str | None = None,
             known_at: str | None = None) -> str:
        client = self._sync()
        capped = self._over_cap()
        if capped:
            return capped
        try:
            return (format_search(client.search(query, limit=limit, **temporal_options(
                        mode, historical, as_of, known_at)), max_chars=self._max_chars, limit=limit)
                    or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)

    async def _arun(self, query: str, limit: int = SEARCH_LIMIT_DEFAULT, mode: str | None = None,
                    historical: bool | None = None, as_of: str | None = None,
                    known_at: str | None = None) -> str:
        if self._async_client is None:
            return await asyncio.to_thread(self._run, query, limit, mode, historical, as_of, known_at)
        capped = self._over_cap()
        if capped:
            return capped
        try:
            return (format_search(await self._async_client.search(query, limit=limit, **temporal_options(
                        mode, historical, as_of, known_at)), max_chars=self._max_chars, limit=limit)
                    or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return _DOWN.format(code=exc.code)


def create_vinc_tools(client: VincClient | None = None,
                      async_client: AsyncVincClient | None = None, *,
                      max_chars: int = DEFAULT_MAX_CHARS,
                      cap: ToolCallCap | None = None) -> list[BaseTool]:
    """Two read-only CrewAI tools, ``vinc_brief`` and ``vinc_search``.

    Pass a sync client, an async client, or both. Without an async client the async
    path runs the sync one in a thread. When Vinc is unavailable, the quota is spent or
    the id does not exist, a tool answers with a sentence instead of failing; a key or
    space error raises. With ``cap`` the two tools share its call budget.
    """
    if client is None and async_client is None:
        raise ValueError("pass a VincClient, an AsyncVincClient, or both")
    tools: list[_VincTool] = [VincBriefTool(), VincSearchTool()]
    for tool in tools:
        tool._client = client.without_retries() if client is not None else None
        tool._async_client = async_client.without_retries() if async_client is not None else None
        tool._cap, tool._max_chars = cap, max_chars
    return list(tools)
