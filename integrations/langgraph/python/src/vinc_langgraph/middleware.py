"""Middleware for LangChain ``create_agent`` (which runs on LangGraph).

Needs the ``agents`` extra: ``pip install "vinc-langgraph[agents]"``.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from vinc_client import AsyncVincClient, VincClient
from vinc_client.context import DEFAULT_MAX_CHARS

from .helpers import build_system_message, last_user_text


class VincContextMiddleware(AgentMiddleware):
    """Adds Vinc context to the system message of each model call.

    An agent that calls tools calls the model several times for one question.
    The answer for a question is kept for ``cache_seconds`` so those calls do not
    each spend a Vinc call. Nothing is written: this middleware only reads.
    """

    def __init__(self, client: VincClient | None = None,
                 async_client: AsyncVincClient | None = None, *, api_key: str | None = None,
                 base_url: str | None = None, space: str | None = None,
                 search_fallback: bool = True, max_chars: int = DEFAULT_MAX_CHARS,
                 max_excerpts: int = 3, cache_seconds: float = 60.0) -> None:
        super().__init__()
        self._config = {"api_key": api_key, "base_url": base_url, "space": space}
        self._client = client
        self._async_client = async_client
        self.search_fallback = search_fallback
        self.max_chars = max_chars
        self.max_excerpts = max_excerpts
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str | None]] = {}

    # 클라이언트는 쓰는 쪽(동기·비동기)만 만든다 — 둘 다 만들면 연결 풀이 두 벌 생긴다.
    @property
    def client(self) -> VincClient:
        if self._client is None:
            self._client = VincClient(self._config["api_key"], base_url=self._config["base_url"],
                                      space=self._config["space"])
        return self._client

    @property
    def async_client(self) -> AsyncVincClient:
        if self._async_client is None:
            self._async_client = AsyncVincClient(self._config["api_key"],
                                                 base_url=self._config["base_url"],
                                                 space=self._config["space"])
        return self._async_client

    def _cached(self, query: str) -> tuple[bool, str | None]:
        hit = self._cache.get(query)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return True, hit[1]
        return False, None

    def _remember(self, query: str, block: str | None) -> None:
        if len(self._cache) >= 64:
            self._cache.pop(next(iter(self._cache)))
        self._cache[query] = (time.monotonic(), block)

    def _with_block(self, request: ModelRequest, block: str | None) -> ModelRequest:
        if not block:
            return request
        if hasattr(request, "system_message"):
            return request.override(
                system_message=build_system_message(block, request.system_message))
        # langchain 1.0 초기판의 ModelRequest 는 문자열 system_prompt 만 갖는다.
        base = getattr(request, "system_prompt", None) or ""
        return request.override(system_prompt=f"{base}\n\n{block}" if base else block)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        query = last_user_text(request.messages)
        found, block = self._cached(query)
        if not found:
            block = self.client.get_context(query, search_fallback=self.search_fallback,
                                            max_chars=self.max_chars,
                                            max_excerpts=self.max_excerpts)
            self._remember(query, block)
        return handler(self._with_block(request, block))

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[Any]]) -> Any:
        query = last_user_text(request.messages)
        found, block = self._cached(query)
        if not found:
            block = await self.async_client.get_context(
                query, search_fallback=self.search_fallback, max_chars=self.max_chars,
                max_excerpts=self.max_excerpts)
            self._remember(query, block)
        return await handler(self._with_block(request, block))
