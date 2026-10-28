"""Middleware for LangChain ``create_agent`` (which runs on LangGraph).

Needs the ``agents`` extra: ``pip install "vinc-langgraph[agents]"``.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Mapping

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from vinc_client import AsyncVincClient, VincClient
from vinc_client.compiler import ContextPolicy, arole_prefetch, role_prefetch
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS, package_space
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tool_policy import RoleToolPolicy
from vinc_client.tools import TOOL_CALLS_PER_RUN, VINC_TOOL_NAMES, cap_message

from .helpers import build_system_message, last_user_text


def run_tool_call_ids(messages: Any, names: Any = None) -> list[str]:
    """Ids of the tool calls the model made in this run, in model order; with ``names``,
    only calls to those tools.

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
                       if (names is None or call.get("name") in names) and call.get("id"))
    return ids


def vinc_tool_call_ids(messages: Any) -> list[str]:
    """Ids of the vinc tool calls the model made in this run, in model order."""
    return run_tool_call_ids(messages, VINC_TOOL_NAMES)


def _run_marker(messages: Any) -> tuple[str, str]:
    """Which run these messages belong to: the newest human message's id, else its text."""
    for message in reversed(list(messages or ())):
        if getattr(message, "type", "") == "human":
            message_id = getattr(message, "id", None)
            if message_id:
                return ("id", str(message_id))
            break
    return ("text", last_user_text(messages))


#: 두 캐시 각각의 상한. 넘치면 가장 먼저 담은 것부터 버린다.
_CACHE_ENTRIES = 64


class VincContextMiddleware(AgentMiddleware):
    """Adds Vinc context to the system message of each model call.

    An agent that calls tools calls the model several times for one question, so the
    block is not read again for each of those calls. The two modes keep it differently:

    - Question lookup (no ``prefetch_role``) keeps the block for a question for
      ``cache_seconds``. Use ``cache_seconds=0`` for a fresh read on every model call.
    - Role prefetch (``prefetch_role`` set) reads the role contract, its required documents
      and the task evidence once per run, on the run's first model call, and reuses that
      block for the rest of the run. A run is everything after the newest human message
      (its id, or its text when it has none), so a new human message reads again and a run
      in progress finishes on the contract it started with. ``cache_seconds`` does not apply
      to this mode. A failed read is never kept: its error propagates unchanged and the
      next model call reads again.

    Kept blocks belong to one client, effective credential, space and retrieval settings
    (in prefetch mode also the role and ``prefetch_max_chars``), at most 64 per mode.
    ``clear_cache`` drops both kinds after a known source or authorization change; neither
    bypasses the server's authorization caches. Nothing is written: this middleware only reads.

    ``prefetch_pin`` (the ``hash`` from ``VincClient.role``) holds role prefetch to that
    contract: when the role changed, the read raises ``VincRoleChanged`` and the model is not
    called. Moving the pin to the new hash is your decision; nothing moves it for you.

    ``context_policy`` (with ``prefetch_role``) builds the role's block with
    ``vinc_client.compile_context`` under that policy instead of ``prefetch``. The block is
    kept for the run in the same way. ``vinc_client.FULL_TEXT`` gives prefetch's block.

    In role prefetch mode the contract's ``disallowed_tools`` apply to the run, by exact
    tool name: those tools are removed from what the model sees, a call to one of them gets
    a tool result saying the role does not allow it, and the run's first model call logs one
    warning listing the entries that matched none of the agent's tools. The contract comes
    from the same prefetch read, so this costs no hub call.

    The contract's run policy applies too (``RoleToolPolicy.from_contract``). With
    ``capability_map`` (tool name to capability id) a tool the role denies is removed the
    same way, and a call to one it holds for approval gets a sentence asking for a person
    instead of running. ``budget.tool_calls`` caps all tool calls of a run, in model order.
    A malformed policy stops the run before the model is called.

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
                 tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                 prefetch_pin: str | None = None,
                 capability_map: Mapping[str, str] | None = None,
                 context_policy: ContextPolicy | None = None) -> None:
        super().__init__()
        if prefetch_pin is not None and not prefetch_role:
            # 역할 없이 고정만 주면 맥락 모드로 조용히 돌게 된다. 시작 전에 멈춘다.
            raise ValueError("prefetch_pin needs prefetch_role")
        if context_policy is not None and not prefetch_role:
            raise ValueError("context_policy needs prefetch_role")
        self.context_policy = context_policy
        self._config = {"api_key": api_key, "base_url": base_url, "space": space}
        self._client = client
        self._async_client = async_client
        self.search_fallback = search_fallback
        self.max_chars = max_chars
        self.max_excerpts = max_excerpts
        self.cache_seconds = cache_seconds
        self.prefetch_role = prefetch_role
        self.prefetch_max_chars = prefetch_max_chars
        self.prefetch_pin = prefetch_pin
        self.tool_calls_per_run = tool_calls_per_run
        self.capability_map = capability_map
        self._cache: dict[tuple, tuple[float, str | None]] = {}
        self._prefetched: dict[tuple, tuple[str, RoleToolPolicy]] = {}
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

    @staticmethod
    def _refusal(request: Any, policy: RoleToolPolicy, messages: Any) -> ToolMessage | None:
        call = request.tool_call
        name = call.get("name")
        # 예산은 이 런에서 모델이 적은 순서로 센다(상태에서 세므로 동시 런끼리 나눠 쓰지 않는다).
        if policy.tool_calls is not None and call.get("id"):
            ids = run_tool_call_ids(messages)
            if call["id"] in ids and policy.over_budget(ids.index(call["id"])):
                return ToolMessage(content=policy.budget_message(), name=name,
                                   tool_call_id=call["id"])
        if policy.allows(name):
            return None
        return ToolMessage(content=policy.refusal(name), name=name,
                           tool_call_id=call.get("id") or "")

    def _refused(self, request: Any) -> ToolMessage | None:
        # 이 런의 계약은 앞선 모델 호출이 담아 두었다. 비었으면(clear_cache 등) 다시 읽는다.
        if not self.prefetch_role:
            return None
        messages = (getattr(request, "state", None) or {}).get("messages")
        return self._refusal(request, self._prefetch(messages)[1], messages)

    async def _arefused(self, request: Any) -> ToolMessage | None:
        if not self.prefetch_role:
            return None
        messages = (getattr(request, "state", None) or {}).get("messages")
        return self._refusal(request, (await self._aprefetch(messages))[1], messages)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refused(request) or self._capped(request) or handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return (await self._arefused(request) or self._capped(request)
                or await handler(request))

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

    def _keep(self, cache: dict, key: tuple, value: Any) -> None:
        # clear_cache 뒤에 끝난 읽기는 담지 않는다(키 첫 칸이 그때의 세대다).
        if key[0] != self._cache_generation:
            return
        if key not in cache and len(cache) >= _CACHE_ENTRIES:
            cache.pop(next(iter(cache)))
        cache[key] = value

    def _remember(self, key: tuple, block: str | None) -> None:
        self._keep(self._cache, key, (time.monotonic(), block))

    def _prefetch_key(self, client: VincClient | AsyncVincClient, messages: Any) -> tuple:
        return (self._cache_generation, client, client.context_cache_identity,
                self.prefetch_role, self.prefetch_max_chars, self.prefetch_pin,
                id(self.context_policy), _run_marker(messages))

    def _ran(self, key: tuple, fetched: Any, tools: Any) -> tuple[str, RoleToolPolicy]:
        # 계약의 도구 목록이 잘못됐으면 담기 전에 멈춘다. 경고는 새로 읽은 런의 첫 호출에서 한 번.
        policy = RoleToolPolicy.from_contract(fetched.contract, self.capability_map)
        if tools is not None:
            policy.warn_unmatched(tools)
        self._keep(self._prefetched, key, (fetched.block, policy))
        return fetched.block, policy

    def _prefetch(self, messages: Any, tools: Any = None) -> tuple[str, RoleToolPolicy]:
        # 런마다 한 번 읽는다. 실패는 담지 않고 그대로 올린다(다음 모델 호출이 다시 읽는다).
        key = self._prefetch_key(self.client, messages)
        kept = self._prefetched.get(key)
        if kept is not None:
            return kept
        if self.context_policy is not None:
            fetched = role_prefetch(self.client, self.prefetch_role, last_user_text(messages),
                                    policy=self.context_policy,
                                    max_chars=self.prefetch_max_chars, pin=self.prefetch_pin)
        else:
            fetched = self.client.prefetch_with_contract(
                self.prefetch_role, last_user_text(messages), max_chars=self.prefetch_max_chars,
                pin=self.prefetch_pin)
        return self._ran(key, fetched, tools)

    async def _aprefetch(self, messages: Any, tools: Any = None) -> tuple[str, RoleToolPolicy]:
        key = self._prefetch_key(self.async_client, messages)
        kept = self._prefetched.get(key)
        if kept is not None:
            return kept
        if self.context_policy is not None:
            fetched = await arole_prefetch(self.async_client, self.prefetch_role,
                                           last_user_text(messages), policy=self.context_policy,
                                           max_chars=self.prefetch_max_chars,
                                           pin=self.prefetch_pin)
        else:
            fetched = await self.async_client.prefetch_with_contract(
                self.prefetch_role, last_user_text(messages), max_chars=self.prefetch_max_chars,
                pin=self.prefetch_pin)
        return self._ran(key, fetched, tools)

    def _for_role(self, request: ModelRequest, kept: tuple[str, RoleToolPolicy]) -> ModelRequest:
        block, policy = kept
        request = self._with_block(request, block)
        tools = list(getattr(request, "tools", None) or [])
        allowed = policy.allowed(tools)
        return request.override(tools=allowed) if len(allowed) != len(tools) else request

    def clear_cache(self) -> None:
        """Discard retained retrieval of both modes after a known source or authorization change."""
        self._cache.clear()
        self._prefetched.clear()
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
        if self.prefetch_role:
            return handler(self._for_role(request, self._prefetch(request.messages, request.tools)))
        query = last_user_text(request.messages)
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
        if self.prefetch_role:
            kept = await self._aprefetch(request.messages, request.tools)
            return await handler(self._for_role(request, kept))
        query = last_user_text(request.messages)
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
