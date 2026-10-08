"""Middleware for LangChain ``create_agent`` (which runs on LangGraph).

Needs the ``agents`` extra: ``pip install "vinc-langgraph[agents]"``.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from vinc_client import AsyncVincClient, VincClient
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS, package_space
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tools import TOOL_CALLS_PER_RUN, VINC_TOOL_NAMES, cap_message

from .helpers import build_system_message, last_user_text


def vinc_tool_call_ids(messages: Any) -> list[str]:
    """Ids of the vinc tool calls the model made in this run, in model order.

    A run is everything after the newest human message, so a resumed interrupt keeps
    counting and a new question starts at zero.
    """
    ids: list[str] = []
    for message in messages or ():
        kind = getattr(message, "type", "")
        if kind == "human":
            ids = []
        elif kind == "ai":
            ids.extend(call["id"] for call in (getattr(message, "tool_calls", None) or [])
                       if call.get("name") in VINC_TOOL_NAMES and call.get("id"))
    return ids


class VincContextMiddleware(AgentMiddleware):
    """Adds Vinc context to the system message of each model call.

    An agent that calls tools calls the model several times for one question.
    The answer for a question is kept for ``cache_seconds`` so those calls do not
    each spend a Vinc call. Nothing is written: this middleware only reads.
    Entries belong to one client, effective credential, space and retrieval configuration.
    Source changes remain cached until TTL expiry or ``clear_cache``. Use cache_seconds=0
    for fresh reads on every model call; this does not bypass server authorization caches.

    Calls to ``vinc_brief`` and ``vinc_search`` from :func:`create_vinc_tools` are
    capped at ``tool_calls_per_run`` per agent run (the model's calls after the newest
    human message, in order); past that the tool result is a sentence saying so.
    ``None`` turns the cap off.
    """

    def __init__(self, client: VincClient | None = None,
                 async_client: AsyncVincClient | None = None, *, api_key: str | None = None,
                 base_url: str | None = None, space: str | None = None,
                 search_fallback: bool = True, max_chars: int = DEFAULT_MAX_CHARS,
                 max_excerpts: int = 3, cache_seconds: float = 60.0,
                 prefetch_role: str | None = None,
                 prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                 tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN) -> None:
        super().__init__()
        self._config = {"api_key": api_key, "base_url": base_url, "space": space}
        self._client = client
        self._async_client = async_client
        self.search_fallback = search_fallback
        self.max_chars = max_chars
        self.max_excerpts = max_excerpts
        self.cache_seconds = cache_seconds
        self.prefetch_role = prefetch_role
        self.prefetch_max_chars = prefetch_max_chars
        self.tool_calls_per_run = tool_calls_per_run
        self._cache: dict[tuple, tuple[float, str | None]] = {}
        self._cache_generation = 0

    # 상한은 그래프 상태에서 센다(이 호출이 이번 런의 몇 번째 vinc 호출인가). 인스턴스 카운터가
    # 아니라서 같은 에이전트로 동시에 도는 런끼리 상한을 나눠 쓰지 않고, 병렬 호출에서도 모델이
    # 적은 순서대로 결정된다. Agent Framework 쪽은 런마다 새 ToolCallCap 을 가진 도구를 만든다.
    # 인자 검증 전에 세므로 잘못된 인자의 호출도 한 번으로 친다(Agent Framework 는 스키마 검증
    # 뒤에 세어 그 호출은 세지 않는다).
    def _capped(self, request: Any) -> ToolMessage | None:
        limit, call = self.tool_calls_per_run, request.tool_call
        if limit is None or call.get("name") not in VINC_TOOL_NAMES or not call.get("id"):
            return None
        messages = (getattr(request, "state", None) or {}).get("messages")
        if call["id"] in vinc_tool_call_ids(messages)[limit:]:
            return ToolMessage(content=cap_message(limit), name=call["name"],
                               tool_call_id=call["id"])
        return None

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._capped(request) or handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return self._capped(request) or await handler(request)

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

    def _cache_key(self, client: VincClient | AsyncVincClient, query: str) -> tuple:
        return (self._cache_generation, client, client.context_cache_identity, query, self.search_fallback,
                self.max_chars, self.max_excerpts)

    def _cached(self, key: tuple) -> tuple[bool, str | None]:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return True, hit[1]
        return False, None

    def _remember(self, key: tuple, block: str | None) -> None:
        if key[0] != self._cache_generation:
            return
        if len(self._cache) >= 64:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (time.monotonic(), block)

    def clear_cache(self) -> None:
        """Discard retained retrieval after a known source or authorization change."""
        self._cache.clear()
        self._cache_generation += 1

    def _with_block(self, request: ModelRequest, block: str | None) -> ModelRequest:
        if not block:
            return request
        if hasattr(request, "system_message"):
            return request.override(
                system_message=build_system_message(block, request.system_message))
        # langchain 1.0 초기판의 ModelRequest 는 문자열 system_prompt 만 갖는다.
        base = getattr(request, "system_prompt", None) or ""
        return request.override(system_prompt=build_system_message(block, base).content)

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        query = last_user_text(request.messages)
        if self.prefetch_role:
            block = self.client.prefetch(self.prefetch_role, query,
                                         max_chars=self.prefetch_max_chars)
            return handler(self._with_block(request, block))
        key = self._cache_key(self.client, query)
        found, block = self._cached(key)
        if not found:
            block = self.client.get_context(query, search_fallback=self.search_fallback,
                                            max_chars=self.max_chars,
                                            max_excerpts=self.max_excerpts)
            self._remember(key, block)
        return handler(self._with_block(request, block))

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[Any]]) -> Any:
        query = last_user_text(request.messages)
        if self.prefetch_role:
            block = await self.async_client.prefetch(self.prefetch_role, query,
                                                     max_chars=self.prefetch_max_chars)
            return await handler(self._with_block(request, block))
        key = self._cache_key(self.async_client, query)
        found, block = self._cached(key)
        if not found:
            block = await self.async_client.get_context(
                query, search_fallback=self.search_fallback, max_chars=self.max_chars,
                max_excerpts=self.max_excerpts)
            self._remember(key, block)
        return await handler(self._with_block(request, block))


class PackageProvider(VincContextMiddleware):
    """LangChain middleware for one explicit handoff; no content or permission caching.

    For per-run ids held in LangGraph state, use ``get_vinc_package_context``
    in a graph node instead. No write tool or automatic after-run write is installed.
    """

    def __init__(self, package_id: str, *, space: str, client: VincClient | None = None,
                 async_client: AsyncVincClient | None = None, api_key: str | None = None,
                 base_url: str | None = None,
                 max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> None:
        # 패키지 읽기에는 vinc 도구가 없으므로 상한을 끈다.
        super().__init__(client, async_client, api_key=api_key, base_url=base_url,
                         space=package_space(space), max_chars=max_chars, tool_calls_per_run=None)
        self.package_id = package_id

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        block = self.client.get_package_context(self.package_id, space=self._config["space"],
                                                max_chars=self.max_chars)
        return handler(self._with_block(request, block))

    async def awrap_model_call(self, request: ModelRequest,
                               handler: Callable[[ModelRequest], Awaitable[Any]]) -> Any:
        block = await self.async_client.get_package_context(
            self.package_id, space=self._config["space"], max_chars=self.max_chars)
        return await handler(self._with_block(request, block))
