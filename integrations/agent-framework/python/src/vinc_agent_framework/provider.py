"""A context provider and read tools that connect an Agent Framework agent to Vinc.

Reading happens on every run, before the model is called. Writing happens only
when your code calls :func:`record_episode`; the provider never stores the
conversation.
"""
from __future__ import annotations

from typing import Annotated, Any, Iterable, Sequence

from agent_framework import AgentSession, ContextProvider, FunctionTool, SessionContext, tool
from pydantic import Field
from vinc_client import (AsyncVincClient, VincQuotaExceeded, VincUnavailable, format_brief,
                         format_search)
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_client.package import DEFAULT_PACKAGE_MAX_CHARS, package_space

DEFAULT_SOURCE_ID = "vinc"


def _last_user_text(messages: Sequence[Any]) -> str:
    """The text of the newest user message; the question the run is about."""
    for message in reversed(list(messages or [])):
        if str(getattr(message, "role", "")).lower().endswith("user"):
            return (getattr(message, "text", "") or "").strip()
    return ""


def create_vinc_tools(client: AsyncVincClient, *,
                      max_chars: int = DEFAULT_MAX_CHARS) -> list[FunctionTool]:
    """Two read-only tools the model may call: ``vinc_brief`` and ``vinc_search``.

    Nothing here can write. When Vinc is unavailable or the quota is spent the
    tool answers with a sentence saying so instead of failing the run.
    """

    @tool(name="vinc_brief",
          description="Look up one node in the user's Vinc knowledge graph: a decision, record "
                      "or concept with its summary, document excerpts and relations. Pass a "
                      "question or an exact node id such as decision:some-slug.")
    async def vinc_brief(
        query: Annotated[str, Field(description="A question, or an exact node id.")],
    ) -> str:
        try:
            return (format_brief(await client.brief(query), max_chars=max_chars)
                    or "Vinc has no single node for that; try vinc_search.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return f"Vinc is not available right now ({exc.code}); answer without it."

    @tool(name="vinc_search",
          description="Search the user's Vinc knowledge graph by text and meaning; returns "
                      "matching nodes and document passages with their ids.")
    async def vinc_search(
        query: Annotated[str, Field(description="What to look for.")],
        limit: Annotated[int, Field(description="How many matches of each kind.", ge=1,
                                    le=20)] = 5,
    ) -> str:
        try:
            return (format_search(await client.search(query, limit=limit), max_chars=max_chars,
                                  limit=limit)
                    or "No matches in Vinc.")
        except (VincUnavailable, VincQuotaExceeded) as exc:
            return f"Vinc is not available right now ({exc.code}); answer without it."

    return [vinc_brief, vinc_search]


class VincContextProvider(ContextProvider):
    """Adds Vinc context to the instructions of every agent run.

    ``before_run`` briefs the newest user message and, when something matched,
    appends a fenced block marked as data. With ``search_fallback`` (the default)
    a tie or a miss is widened with a search, so a run costs one call, or two.
    ``after_run`` does nothing: unlike memory providers that store each turn,
    this one never writes. Use :func:`record_episode` after a person approved
    what happened.
    """

    def __init__(self, client: AsyncVincClient | None = None, *, api_key: str | None = None,
                 base_url: str | None = None, space: str | None = None,
                 source_id: str = DEFAULT_SOURCE_ID, search_fallback: bool = True,
                 max_chars: int = DEFAULT_MAX_CHARS, max_excerpts: int = 3,
                 min_query_chars: int = 3, expose_tools: bool = False) -> None:
        super().__init__(source_id)
        self.client = client or AsyncVincClient(api_key, base_url=base_url, space=space)
        self._owns_client = client is None
        self.search_fallback = search_fallback
        self.max_chars = max_chars
        self.max_excerpts = max_excerpts
        self.min_query_chars = min_query_chars
        self._tools = create_vinc_tools(self.client, max_chars=max_chars) if expose_tools else []

    async def before_run(self, *, agent: Any, session: AgentSession, context: SessionContext,
                         state: dict[str, Any]) -> None:
        block = await self.client.get_context(
            _last_user_text(context.input_messages), search_fallback=self.search_fallback,
            max_chars=self.max_chars, max_excerpts=self.max_excerpts,
            min_query_chars=self.min_query_chars)
        if block:
            context.extend_instructions(self.source_id, block)
        if self._tools:
            context.extend_tools(self.source_id, self._tools)

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
                         space: str | None = None) -> dict[str, Any]:
    """Write one Episode about existing concepts or decisions. Needs a ``vinc_sk_`` key.

    Call it after a person approved what the agent did; see
    :meth:`vinc_client.AsyncVincClient.record_episode`.
    """
    return await client.record_episode(title, summary=summary, about=about, date=date,
                                       domain=domain, tags=tags, space=space)


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
                        space: str, package_id: str | None = None) -> dict[str, Any]:
    """Explicit producer write; invoke after reviewing its conclusion and claim evidence."""
    return await client.write_package(question, conclusion, claims=claims,
                                      produced_by=produced_by, run_id=run_id,
                                      space=space, package_id=package_id)
