"""``build_agent``: a running CrewAI agent from a role address, in one call.

The role is read once and pinned by hash. Every run reads the role's required documents,
hides the tools the role disallows, refuses calls the role holds for a person, caps tool calls
at the role's budget, and checks the final answer with the role's gates (a task guardrail that
stops instead of retrying). ``run`` returns an :class:`~vinc.sdk.agent_plan.AgentRun` and writes
the run record at the end. A person-only action stops the run in ``waiting``: approving it is a
separate, explicit step (:class:`VincPersonOnlyInput`), and approval never executes a tool.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

from crewai import Crew, Task

from vinc.sdk import AsyncVincClient, VincClient
from vinc.sdk.agent_plan import (AgentPlan, AgentRun, afinish_run, aplan_agent, failed_run,
                                 finish_run, plan_agent, run_result)
from vinc.sdk.context import DEFAULT_MAX_CHARS
from vinc.sdk.output_gate import GateResult, OutputGateStopped
from vinc.sdk.package import DEFAULT_PACKAGE_MAX_CHARS
from vinc.sdk.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc.sdk.tools import TOOL_CALLS_PER_RUN

from .agent import VincAgent, _bind
from .output_gate import create_output_guardrail

HOST = "crewai"
_EXPECTED = "The final answer to the task."


def _task_text(task: Any) -> str:
    if not isinstance(task, str) or not task.strip():
        raise TypeError("pass the task as a non-empty string")
    return task


class VincCrewAgent:
    """A CrewAI :class:`VincAgent` bound to one pinned role, run one task at a time.

    ``agent`` is the underlying CrewAI agent; use it in your own ``Crew`` for anything
    ``run`` does not cover. Calls through ``run`` and ``arun`` are checked by the role's
    gates and recorded.
    """

    def __init__(self, agent: VincAgent, plan: AgentPlan, *, client: VincClient | None,
                 async_client: AsyncVincClient | None, record: bool, space: str | None) -> None:
        self.agent, self.plan = agent, plan
        self._client, self._async_client = client, async_client
        self._record, self._space = record, space

    def _crew(self, text: str, expected_output: str | None) -> Crew:
        task = Task(description=text, expected_output=expected_output or _EXPECTED,
                    agent=self.agent, guardrail=create_output_guardrail(self.plan.gate))
        return Crew(agents=[self.agent], tasks=[task])

    def run(self, task: str, *, run_id: str | None = None,
            expected_output: str | None = None) -> AgentRun:
        rid = run_id or "run-" + uuid.uuid4().hex[:12]
        crew = self._crew(_task_text(task), expected_output)
        try:
            output = crew.kickoff()
        except OutputGateStopped as stopped:
            result = run_result(self.plan, rid, None, stopped.result)
        except Exception:
            if self._record and self._client is not None:
                finish_run(self._client, failed_run(self.plan, rid), host=HOST, space=self._space)
            raise
        else:
            result = run_result(self.plan, rid, output.raw, GateResult("pass"))
        if self._record and self._client is not None:
            finish_run(self._client, result, host=HOST, space=self._space)
        return result

    async def arun(self, task: str, *, run_id: str | None = None,
                   expected_output: str | None = None) -> AgentRun:
        rid = run_id or "run-" + uuid.uuid4().hex[:12]
        crew = self._crew(_task_text(task), expected_output)
        try:
            output = await crew.akickoff()
        except OutputGateStopped as stopped:
            result = run_result(self.plan, rid, None, stopped.result)
        except Exception:
            if self._record and self._async_client is not None:
                await afinish_run(self._async_client, failed_run(self.plan, rid), host=HOST,
                                  space=self._space)
            raise
        else:
            result = run_result(self.plan, rid, output.raw, GateResult("pass"))
        if self._record and self._async_client is not None:
            await afinish_run(self._async_client, result, host=HOST, space=self._space)
        return result


def _assemble(plan: AgentPlan, llm: Any, tools: Sequence[Any], *, client: VincClient | None,
              async_client: AsyncVincClient | None, expose_tools: bool,
              capability_map: Mapping[str, str] | None, tool_calls_per_run: int | None,
              prefetch_max_chars: int, max_chars: int, agent_kwargs: Mapping[str, Any]) -> VincAgent:
    return _bind(plan.contract, client, async_client, llm=llm, tools=tools,
                 prefetch_max_chars=prefetch_max_chars, prefetch_pin=plan.pin or None,
                 expose_tools=expose_tools, tool_calls_per_run=tool_calls_per_run,
                 max_chars=max_chars, package_id=None, package_space=None,
                 package_max_chars=DEFAULT_PACKAGE_MAX_CHARS, capability_map=capability_map,
                 agent_kwargs=agent_kwargs)


def build_agent(address: str, llm: Any, *, tools: Sequence[Any] = (),
                client: VincClient | None = None, async_client: AsyncVincClient | None = None,
                api_key: str | None = None, base_url: str | None = None,
                space: str | None = None, capability_map: Mapping[str, str] | None = None,
                record: bool = True, expose_tools: bool = True,
                tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                max_chars: int = DEFAULT_MAX_CHARS, **agent_kwargs: Any) -> VincCrewAgent:
    """Read ``address``, pin its contract and return a running agent for it.

    ``address`` is ``reviewer``, ``agent-reviewer`` or ``concept:agent-reviewer``. ``llm`` is
    any CrewAI LLM. ``tools`` are yours; ``vinc_brief`` and ``vinc_search`` are added unless
    ``expose_tools=False``. The role is read with a sync client, so ``run`` works; for
    ``arun`` also pass ``async_client``, or use :func:`abuild_agent`. With ``record=True``
    (the default) each run writes its run record, which needs a ``vinc_sk_`` key; a failed
    write is kept on the result as ``record_error`` and never raised. If the role changes
    later, the next run raises ``VincRoleChanged`` instead of running on a contract nobody
    reviewed.
    """
    client = client or VincClient(api_key, base_url=base_url, space=space)
    plan = plan_agent(client, address, space=space, capability_map=capability_map)
    agent = _assemble(plan, llm, tools, client=client, async_client=async_client,
                      expose_tools=expose_tools, capability_map=capability_map,
                      tool_calls_per_run=tool_calls_per_run,
                      prefetch_max_chars=prefetch_max_chars, max_chars=max_chars,
                      agent_kwargs=agent_kwargs)
    return VincCrewAgent(agent, plan, client=client, async_client=async_client, record=record,
                         space=space)


async def abuild_agent(address: str, llm: Any, *, tools: Sequence[Any] = (),
                       async_client: AsyncVincClient | None = None, api_key: str | None = None,
                       base_url: str | None = None, space: str | None = None,
                       capability_map: Mapping[str, str] | None = None, record: bool = True,
                       expose_tools: bool = True,
                       tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                       prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                       max_chars: int = DEFAULT_MAX_CHARS, **agent_kwargs: Any) -> VincCrewAgent:
    """:func:`build_agent` for async code: the role is read and the agent runs with an async
    client, so use ``arun``."""
    async_client = async_client or AsyncVincClient(api_key, base_url=base_url, space=space)
    plan = await aplan_agent(async_client, address, space=space, capability_map=capability_map)
    agent = _assemble(plan, llm, tools, client=None, async_client=async_client,
                      expose_tools=expose_tools, capability_map=capability_map,
                      tool_calls_per_run=tool_calls_per_run,
                      prefetch_max_chars=prefetch_max_chars, max_chars=max_chars,
                      agent_kwargs=agent_kwargs)
    return VincCrewAgent(agent, plan, client=None, async_client=async_client, record=record,
                         space=space)
