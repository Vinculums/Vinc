"""A context provider and read tools that connect an Agent Framework agent to Vinc.

Reading happens on every run, before the model is called. Writing happens only
when your code calls :func:`record_episode`; the provider never stores the
conversation.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Iterable, Sequence

from agent_framework import (AgentSession, ChatContext, ChatMiddleware, ChatMiddlewareLayer,
                             ContextProvider, FunctionInvocationContext, FunctionMiddleware,
                             FunctionTool, SessionContext, tool)
from vinc_client import (AsyncVincClient, VincNotFound, VincQuotaExceeded, VincUnavailable,
                         format_brief, format_search)
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS, package_space
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tool_policy import RoleToolPolicy
from vinc_client.tools import (BRIEF_DESCRIPTION, BRIEF_NAME, SEARCH_DESCRIPTION,
                               SEARCH_LIMIT_DEFAULT, SEARCH_NAME, TOOL_CALLS_PER_RUN, BriefArgs,
                               SearchArgs, ToolCallCap, temporal_options)

DEFAULT_SOURCE_ID = "vinc"
log = logging.getLogger("vinc_agent_framework")
NO_CHAT_MIDDLEWARE = ("Role %s disallows tools, but this chat client runs no chat middleware "
                      "(it does not include ChatMiddlewareLayer), so the model still sees them; "
                      "calls to them are refused.")


def _last_user_text(messages: Sequence[Any]) -> str:
    """The text of the newest user message; the question the run is about."""
    for message in reversed(list(messages or [])):
        if str(getattr(message, "role", "")).lower().endswith("user"):
            return (getattr(message, "text", "") or "").strip()
    return ""


def create_vinc_tools(client: AsyncVincClient, *, max_chars: int = DEFAULT_MAX_CHARS,
                      cap: ToolCallCap | None = None) -> list[FunctionTool]:
    """Two read-only tools the model may call: ``vinc_brief`` and ``vinc_search``.

    Nothing here can write. When Vinc is unavailable, the quota is spent or
    the id does not exist, the tool answers with a sentence saying so instead
    of failing the run. With ``cap`` the two tools share its call budget; the
    provider passes its own and resets it at every run.
    """
    # 도구는 모델 턴 안에서 돈다. 허브를 기다리지 않고 곧장 문장으로 물러선다.
    client = client.without_retries()
    # 없는 id(코드 not_found)는 프레임워크 기본 오류("Error: Function failed.") 대신 이유를 담은
    # 문장으로 돌린다. space_not_found 같은 설정 오류는 그대로 올린다. LangGraph 쪽과 같은 문장이다.
    # 근거: [[record:vin318-live-exp02-r2-parity-tool-calls-2-vs-1-2026-10-07]]

    # 이름·설명·인자 스키마는 vinc-client 한 곳에서 온다. LangGraph 쪽과 같은 스키마다.
    @tool(name=BRIEF_NAME, description=BRIEF_DESCRIPTION, schema=BriefArgs)
    async def vinc_brief(query: str, mode: str | None = None, historical: bool | None = None,
                         as_of: str | None = None, known_at: str | None = None) -> str:
        if cap is not None and not cap.spend():
            return cap.message
        try:
            return (format_brief(await client.brief(query, **temporal_options(mode, historical, as_of, known_at)), max_chars=max_chars)
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return f"Vinc is not available right now ({exc.code}); answer without it."
        except VincNotFound as exc:
            if exc.code != "not_found":
                raise
            return f"Vinc found nothing for that ({exc.code}); try a different query."

    @tool(name=SEARCH_NAME, description=SEARCH_DESCRIPTION, schema=SearchArgs)
    async def vinc_search(query: str, limit: int = SEARCH_LIMIT_DEFAULT, mode: str | None = None,
                          historical: bool | None = None, as_of: str | None = None,
                          known_at: str | None = None) -> str:
        if cap is not None and not cap.spend():
            return cap.message
        try:
            return (format_search(await client.search(query, limit=limit, **temporal_options(mode, historical, as_of, known_at)), max_chars=max_chars,
                                  limit=limit)
                    or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return f"Vinc is not available right now ({exc.code}); answer without it."

    return [vinc_brief, vinc_search]


class _HideDisallowedTools(ChatMiddleware):
    """Chat middleware for one run: the model does not see the role's disallowed tools.

    The first model call of the run logs one warning naming the contract entries that
    matched none of the agent's tools.
    """

    def __init__(self, policy: RoleToolPolicy) -> None:
        self.policy = policy
        self._warned = False

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        options = dict(context.options or {})
        tools = list(options.get("tools") or [])
        if not self._warned:
            self._warned = True
            self.policy.warn_unmatched(tools)
        allowed = self.policy.allowed(tools)
        if len(allowed) != len(tools):
            # 새 dict 로 바꾼다. 함수 호출 루프가 쥔 도구 목록은 그대로라서, 모델이 그래도 부른
            # 호출은 _RefuseDisallowedTools 가 거절 문장으로 답한다. 다 빠지면 tools 칸을 비운다.
            if allowed:
                options["tools"] = allowed
            else:
                options.pop("tools", None)
                options.pop("tool_choice", None)
            context.options = options
        await call_next()


class _RefuseDisallowedTools(FunctionMiddleware):
    """Function middleware for one run: a call to a disallowed tool gets a refusal result."""

    def __init__(self, policy: RoleToolPolicy) -> None:
        self.policy = policy

    async def process(self, context: FunctionInvocationContext,
                      call_next: Callable[[], Awaitable[None]]) -> None:
        name = context.function.name
        if self.policy.allows(name):
            await call_next()
            return
        context.result = self.policy.refusal(name)


class VincContextProvider(ContextProvider):
    """Adds Vinc context to the instructions of every agent run.

    ``before_run`` briefs the newest user message and, when something matched,
    appends a fenced block marked as data. With ``search_fallback`` (the default)
    a tie or a miss is widened with a search, so a run costs one call, or two.
    ``after_run`` does nothing: unlike memory providers that store each turn,
    this one never writes. Use :func:`record_episode` after a person approved
    what happened.

    With ``expose_tools`` the model may call ``vinc_brief`` and ``vinc_search``
    at most ``tool_calls_per_run`` times per run; past that the tool answers with
    a sentence saying so. ``None`` turns the cap off.

    With ``prefetch_role`` the role contract's ``disallowed_tools`` apply to the run, by
    exact tool name. The provider adds two middleware for that run only: a chat middleware
    that removes those tools from what the model sees, and a function middleware that
    answers a call to one of them with a sentence saying the role does not allow it. The
    first model call logs one warning naming entries that matched none of the agent's tools.
    Hiding needs a chat client that runs chat middleware (``ChatMiddlewareLayer``, as the
    built-in clients do); with any other client the tools stay visible, calls are still
    refused, and the run logs a warning saying so.

    ``prefetch_pin`` (the ``hash`` from ``AsyncVincClient.role``) holds role prefetch to that
    contract: when the role changed, ``before_run`` raises ``VincRoleChanged`` and the model is
    not called. Moving the pin to the new hash is your decision; nothing moves it for you.
    """

    def __init__(self, client: AsyncVincClient | None = None, *, api_key: str | None = None,
                 base_url: str | None = None, space: str | None = None,
                 source_id: str = DEFAULT_SOURCE_ID, search_fallback: bool = True,
                 max_chars: int = DEFAULT_MAX_CHARS, max_excerpts: int = 3,
                 min_query_chars: int = 3, expose_tools: bool = False,
                 prefetch_role: str | None = None,
                 prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                 tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                 prefetch_pin: str | None = None) -> None:
        if prefetch_pin is not None and not prefetch_role:
            # 역할 없이 고정만 주면 맥락 모드로 조용히 돌게 된다. 시작 전에 멈춘다.
            raise ValueError("prefetch_pin needs prefetch_role")
        super().__init__(source_id)
        self.client = client or AsyncVincClient(api_key, base_url=base_url, space=space)
        self._owns_client = client is None
        self.search_fallback = search_fallback
        self.max_chars = max_chars
        self.max_excerpts = max_excerpts
        self.min_query_chars = min_query_chars
        self.prefetch_role = prefetch_role
        self.prefetch_max_chars = prefetch_max_chars
        self.prefetch_pin = prefetch_pin
        self.expose_tools = expose_tools
        self.tool_calls_per_run = tool_calls_per_run

    async def before_run(self, *, agent: Any, session: AgentSession, context: SessionContext,
                         state: dict[str, Any]) -> None:
        task = _last_user_text(context.input_messages)
        if self.prefetch_role:
            fetched = await self.client.prefetch_with_contract(
                self.prefetch_role, task, max_chars=self.prefetch_max_chars,
                pin=self.prefetch_pin)
            block = fetched.block
            # 계약의 도구 목록이 잘못됐으면 여기서 멈춘다(모델 호출 전).
            policy = RoleToolPolicy.from_contract(fetched.contract)
            if policy:
                if not isinstance(getattr(agent, "client", None), ChatMiddlewareLayer):
                    log.warning(NO_CHAT_MIDDLEWARE, policy.role_id)
                # 미들웨어는 이 런의 것이다. 동시에 도는 런끼리 나눠 쓰지 않는다.
                context.extend_middleware(self.source_id, [_HideDisallowedTools(policy),
                                                           _RefuseDisallowedTools(policy)])
        else:
            block = await self.client.get_context(
                task, search_fallback=self.search_fallback,
                max_chars=self.max_chars, max_excerpts=self.max_excerpts,
                min_query_chars=self.min_query_chars)
        if block:
            context.extend_instructions(self.source_id, block)
        if self.expose_tools:
            # 도구는 런마다 새로 만든다: 상한(ToolCallCap)이 그 런의 것이라 동시에 도는 런끼리
            # 나눠 쓰지 않는다. LangGraph 쪽은 그래프 상태에서 같은 수를 센다.
            context.extend_tools(self.source_id, create_vinc_tools(
                self.client, max_chars=self.max_chars, cap=ToolCallCap(self.tool_calls_per_run)))

    async def after_run(self, *, agent: Any, session: AgentSession, context: SessionContext,
                        state: dict[str, Any]) -> None:
        # 의도적으로 비어 있다. 대화를 그래프에 쓰지 않는다 — 쓰기는 record_episode 뿐.
        # 근거: [[decision:framework-integrations-ship-zep-shaped-knowledge-packages-first-2026-09-28]]
        return None

    async def aclose(self) -> None:
        """Close the HTTP client if this provider created it."""
        if self._owns_client:
            await self.client.aclose()


async def record_episode(client: AsyncVincClient, title: str, *, summary: str = "",
                         about: Iterable[str] = (), date: str | None = None,
                         domain: str | None = None, tags: Iterable[str] | None = None,
                         space: str | None = None, episode_id: str | None = None,
                         create_only: bool | None = None,
                         operation_id: str | None = None) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions. Needs a ``vinc_sk_`` key.

    Call it after a person approved what the agent did; see
    :meth:`vinc_client.AsyncVincClient.record_episode`.
    Save episode_id in host state before approval and reuse it only for this event's retries.
    """
    return await client.record_episode(title, summary=summary, about=about, date=date,
                                       domain=domain, tags=tags, space=space, episode_id=episode_id,
                                       create_only=create_only, operation_id=operation_id)


async def record_run(client: AsyncVincClient, run_id: str, *, role: str, outcome: str,
                     **fields: Any) -> dict[str, Any]:
    """Write one agent run as an Episode about its role. Needs a ``vinc_sk_`` key.

    ``fields`` are :meth:`vinc_client.AsyncVincClient.record_run`'s keyword
    arguments. Call it after the run ends; a retry of the same ``run_id`` merges.
    """
    return await client.record_run(run_id, role=role, outcome=outcome, **fields)


class PackageProvider(ContextProvider):
    """Read one explicit same-space handoff before each run, with no permission cache.

    Bind the package when constructing the receiving agent; use another provider
    for another handoff. Session-specific ids are never stored on a shared provider.
    Failure propagates so a missing package cannot silently become a successful handoff.
    Writing is a separate explicit ``client.write_package`` call.
    """

    def __init__(self, client: AsyncVincClient, package_id: str, *, space: str,
                 source_id: str = "vinc-package",
                 max_chars: int = DEFAULT_PACKAGE_MAX_CHARS) -> None:
        super().__init__(source_id)
        self.client = client
        self.package_id = package_id
        self.space = package_space(space)
        self.max_chars = max_chars

    async def before_run(self, *, agent: Any, session: AgentSession, context: SessionContext,
                         state: dict[str, Any]) -> None:
        block = await self.client.get_package_context(self.package_id, space=self.space,
                                                      max_chars=self.max_chars)
        context.extend_instructions(self.source_id, block)

    async def after_run(self, *, agent: Any, session: AgentSession, context: SessionContext,
                        state: dict[str, Any]) -> None:
        # 패키지나 수신자의 대화는 자동 저장하지 않는다.
        return None


async def write_package(client: AsyncVincClient, question: str, conclusion: str, *,
                        claims: Iterable[dict[str, Any]], produced_by: str, run_id: str,
                        space: str, package_id: str | None = None,
                        create_only: bool | None = None,
                        operation_id: str | None = None) -> dict[str, Any]:
    """Explicit producer write; invoke after reviewing its conclusion and claim evidence."""
    return await client.write_package(question, conclusion, claims=claims,
                                      produced_by=produced_by, run_id=run_id,
                                      space=space, package_id=package_id,
                                      create_only=create_only, operation_id=operation_id)


async def prefetch(client: AsyncVincClient, role_id: str, task: str, *, space: str | None = None,
                   max_chars: int = DEFAULT_PREFETCH_MAX_CHARS, pin: str | None = None) -> str:
    """Read the selected role and its required/task evidence before a model call; never write.

    With ``pin`` a role that changed raises ``VincRoleChanged`` instead.
    """
    return await client.prefetch(role_id, task, space=space, max_chars=max_chars, pin=pin)
