"""``build_agent``: a running Agent Framework agent from a role address, in one call.

The role is read once and pinned by hash. The agent reads the role's required documents before
each run, hides the tools the role disallows, refuses calls the role holds for a person, caps
tool calls at the role's budget, and checks the final answer with the role's gates. ``run``
returns an :class:`~vinc_client.agent_plan.AgentRun` and writes the run record at the end.
A person-only action stops the run in ``waiting``: approving it is a separate, explicit step
(:class:`vinc_agent_framework.VincPersonOnlyExecutor`), and approval never executes a tool.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

from agent_framework import Agent
from vinc_client import AsyncVincClient
from vinc_client.agent_plan import (AgentPlan, AgentRun, afinish_run, aplan_agent, failed_run,
                                    run_result)
from vinc_client.output_gate import OutputGateStopped, delivered_result
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tools import TOOL_CALLS_PER_RUN

from .output_gate import VincOutputGateMiddleware
from .provider import VincContextProvider

HOST = "agent-framework"


class VincAgent:
    """An Agent Framework agent bound to one pinned role.

    ``agent`` is the framework agent; use it directly for streaming or sessions. Calls through
    ``run`` are checked by the role's gates and recorded.
    """

    def __init__(self, agent: Any, plan: AgentPlan, *, client: AsyncVincClient, record: bool,
                 space: str | None, owns_client: bool) -> None:
        self.agent, self.plan = agent, plan
        self.client = client
        self._record, self._space, self._owns_client = record, space, owns_client

    async def run(self, task: Any, *, run_id: str | None = None, **kwargs: Any) -> AgentRun:
        rid = run_id or "run-" + uuid.uuid4().hex[:12]
        try:
            response = await self.agent.run(task, **kwargs)
        except OutputGateStopped as stopped:
            result = run_result(self.plan, rid, None, stopped.result)
        except Exception:
            if self._record:
                await afinish_run(self.client, failed_run(self.plan, rid), host=HOST,
                                  space=self._space)
            raise
        else:
            text = getattr(response, "text", None)
            text = text if isinstance(text, str) else None
            result = run_result(self.plan, rid, text, delivered_result(text))
        if self._record:
            await afinish_run(self.client, result, host=HOST, space=self._space)
        return result

    async def aclose(self) -> None:
        """Close the HTTP client if ``build_agent`` created it."""
        if self._owns_client:
            await self.client.aclose()


async def build_agent(address: str, chat_client: Any, *, tools: Sequence[Any] = (),
                      client: AsyncVincClient | None = None, api_key: str | None = None,
                      base_url: str | None = None, space: str | None = None,
                      instructions: str | None = None,
                      capability_map: Mapping[str, str] | None = None, record: bool = True,
                      tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                      name: str | None = None,
                      prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                      **agent_kwargs: Any) -> VincAgent:
    """Read ``address``, pin its contract and return a running agent for it.

    ``address`` is ``reviewer``, ``agent-reviewer`` or ``concept:agent-reviewer``.
    ``chat_client`` is any Agent Framework chat client. ``tools`` are yours; ``vinc_brief`` and
    ``vinc_search`` are added. With ``record=True`` (the default) each run writes its run
    record, which needs a ``vinc_sk_`` key; a failed write is kept on the result as
    ``record_error`` and never raised. If the role changes later, the next run raises
    ``VincRoleChanged`` instead of running on a contract nobody reviewed. The final answer is
    checked in a non-streaming run only. The role's required reading goes into every run in full;
    a role whose reading is longer than ``prefetch_max_chars`` (24,000 characters by default) stops
    before the model is called with ``prefetch_budget_exceeded``.
    """
    owns = client is None
    client = client or AsyncVincClient(api_key, base_url=base_url, space=space)
    try:
        plan = await aplan_agent(client, address, space=space, capability_map=capability_map)
    except BaseException:
        if owns:
            await client.aclose()
        raise
    provider = VincContextProvider(client, prefetch_role=plan.role_id,
                                   prefetch_pin=plan.pin or None, expose_tools=True,
                                   capability_map=capability_map,
                                   tool_calls_per_run=tool_calls_per_run,
                                   prefetch_max_chars=prefetch_max_chars)
    agent = Agent(chat_client, instructions=instructions, tools=list(tools),
                  name=name or plan.role_id.split(":", 1)[1], context_providers=[provider],
                  middleware=[VincOutputGateMiddleware(plan.gate),
                              *agent_kwargs.pop("middleware", ())], **agent_kwargs)
    return VincAgent(agent, plan, client=client, record=record, space=space, owns_client=owns)
