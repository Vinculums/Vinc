"""``build_agent``: a running LangGraph agent from a role address, in one call.

Needs the ``agents`` extra (``pip install "vinc-langgraph[agents]"``).

The role is read once and pinned by hash. The agent reads the role's required documents before
each run, hides the tools the role disallows, refuses calls the role holds for a person, caps
tool calls at the role's budget, and checks the final answer with the role's gates. ``invoke``
returns an :class:`~vinc_client.agent_plan.AgentRun` and writes the run record at the end.
A person-only action stops the run in ``waiting``: approving it is a separate, explicit step
(:func:`vinc_langgraph.create_person_only_node`), and approval never executes a tool.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping, Sequence

from langchain_core.messages import HumanMessage
from vinc_client import AsyncVincClient, VincClient
from vinc_client.agent_plan import (AgentPlan, AgentRun, afinish_run, aplan_agent, failed_run,
                                    finish_run, plan_agent, run_result)
from vinc_client.output_gate import OutputGateStopped, delivered_result
from vinc_client.prefetch import DEFAULT_PREFETCH_MAX_CHARS
from vinc_client.tools import TOOL_CALLS_PER_RUN

from .helpers import create_vinc_tools
from .middleware import VincContextMiddleware
from .output_gate import create_output_gate_middleware

HOST = "langgraph"


def _payload(task: Any) -> Mapping[str, Any]:
    """A string is the user's first message; a mapping is the graph input as is."""
    if isinstance(task, str):
        return {"messages": [HumanMessage(content=task)]}
    if isinstance(task, Mapping):
        return task
    raise TypeError("pass the task as a string or as the graph input mapping")


def _final_text(state: Any) -> str | None:
    messages = (state or {}).get("messages") if isinstance(state, Mapping) else None
    content = getattr(messages[-1], "content", None) if messages else None
    return content if isinstance(content, str) else None


class VincAgent:
    """A compiled LangGraph agent bound to one pinned role.

    ``graph`` is the compiled agent; use it directly for streaming or resuming. Calls through
    ``invoke`` and ``ainvoke`` are checked by the role's gates and recorded.
    """

    def __init__(self, graph: Any, plan: AgentPlan, *, client: VincClient | None,
                 async_client: AsyncVincClient | None, record: bool, space: str | None) -> None:
        self.graph, self.plan = graph, plan
        self._client, self._async_client = client, async_client
        self._record, self._space = record, space

    def invoke(self, task: Any, config: Any = None, *, run_id: str | None = None,
               **kwargs: Any) -> AgentRun:
        rid = run_id or "run-" + uuid.uuid4().hex[:12]
        payload = _payload(task)
        try:
            state = self.graph.invoke(payload, config, **kwargs)
        except OutputGateStopped as stopped:
            result = run_result(self.plan, rid, None, stopped.result)
        except Exception:
            if self._record and self._client is not None:
                finish_run(self._client, failed_run(self.plan, rid), host=HOST, space=self._space)
            raise
        else:
            text = _final_text(state)
            result = run_result(self.plan, rid, text, delivered_result(text))
        if self._record and self._client is not None:
            finish_run(self._client, result, host=HOST, space=self._space)
        return result

    async def ainvoke(self, task: Any, config: Any = None, *, run_id: str | None = None,
                      **kwargs: Any) -> AgentRun:
        rid = run_id or "run-" + uuid.uuid4().hex[:12]
        payload = _payload(task)
        try:
            state = await self.graph.ainvoke(payload, config, **kwargs)
        except OutputGateStopped as stopped:
            result = run_result(self.plan, rid, None, stopped.result)
        except Exception:
            if self._record and self._async_client is not None:
                await afinish_run(self._async_client, failed_run(self.plan, rid), host=HOST,
                                  space=self._space)
            raise
        else:
            text = _final_text(state)
            result = run_result(self.plan, rid, text, delivered_result(text))
        if self._record and self._async_client is not None:
            await afinish_run(self._async_client, result, host=HOST, space=self._space)
        return result


def _assemble(plan: AgentPlan, model: Any, tools: Sequence[Any], *, client: VincClient | None,
              async_client: AsyncVincClient | None, system_prompt: str | None,
              checkpointer: Any, capability_map: Mapping[str, str] | None,
              tool_calls_per_run: int | None, name: str | None,
              create_agent_kwargs: Mapping[str, Any], prefetch_max_chars: int) -> Any:
    from langchain.agents import create_agent

    middleware = [
        VincContextMiddleware(client, async_client, prefetch_role=plan.role_id,
                              prefetch_pin=plan.pin or None, capability_map=capability_map,
                              tool_calls_per_run=tool_calls_per_run,
                              prefetch_max_chars=prefetch_max_chars),
        create_output_gate_middleware(plan.gate),
        *create_agent_kwargs.get("middleware", ()),
    ]
    extra = {k: v for k, v in create_agent_kwargs.items() if k != "middleware"}
    # 쓰기 도구는 쓸 수 있는 역할에게, 쓰기 키일 때만 준다.
    # 근거: [[decision:the-default-agent-tool-surface-is-reads-plus-role-gated-writes-2026-10-31]]
    key_client = client or async_client
    writes = plan.may_write and key_client is not None and not key_client.read_only
    return create_agent(model, tools=[*create_vinc_tools(client, async_client, writes=writes), *tools],
                        system_prompt=system_prompt, middleware=middleware,
                        checkpointer=checkpointer, name=name or plan.role_id.split(":", 1)[1],
                        **extra)


def build_agent(address: str, model: Any, *, tools: Sequence[Any] = (),
                client: VincClient | None = None, async_client: AsyncVincClient | None = None,
                api_key: str | None = None, base_url: str | None = None,
                space: str | None = None, system_prompt: str | None = None,
                checkpointer: Any = None, capability_map: Mapping[str, str] | None = None,
                record: bool = True, tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                name: str | None = None, prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                **create_agent_kwargs: Any) -> VincAgent:
    """Read ``address``, pin its contract and return a running agent for it.

    ``address`` is ``reviewer``, ``agent-reviewer`` or ``concept:agent-reviewer``. ``model`` is
    any LangChain chat model. ``tools`` are yours; ``vinc_brief`` and ``vinc_search`` are added,
    and ``vinc_ingest`` and ``vinc_doc_put`` too when the role may write and the key can write.
    The role is read with a sync client, so ``invoke`` works; for ``ainvoke`` also pass
    ``async_client``, or use :func:`abuild_agent`. With ``record=True`` (the default) each run
    writes its run record, which needs a ``vinc_sk_`` key; a failed write is kept on the result
    as ``record_error`` and never raised. If the role changes later, the next run raises
    ``VincRoleChanged`` instead of running on a contract nobody reviewed. The role's required
    reading goes into every run in full, and a role whose reading is longer than
    ``prefetch_max_chars`` (24,000 characters by default) stops before the model is called with
    ``prefetch_budget_exceeded``; raise it for a role with a large reading.
    """
    client = client or VincClient(api_key, base_url=base_url, space=space)
    plan = plan_agent(client, address, space=space, capability_map=capability_map)
    graph = _assemble(plan, model, tools, client=client, async_client=async_client,
                      system_prompt=system_prompt, checkpointer=checkpointer,
                      capability_map=capability_map, tool_calls_per_run=tool_calls_per_run,
                      name=name, create_agent_kwargs=create_agent_kwargs,
                      prefetch_max_chars=prefetch_max_chars)
    return VincAgent(graph, plan, client=client, async_client=async_client, record=record,
                     space=space)


async def abuild_agent(address: str, model: Any, *, tools: Sequence[Any] = (),
                       async_client: AsyncVincClient | None = None, api_key: str | None = None,
                       base_url: str | None = None, space: str | None = None,
                       system_prompt: str | None = None, checkpointer: Any = None,
                       capability_map: Mapping[str, str] | None = None, record: bool = True,
                       tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                       name: str | None = None,
                       prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                       **create_agent_kwargs: Any) -> VincAgent:
    """:func:`build_agent` for async code: the role is read and the agent runs with an async
    client, so use ``ainvoke``."""
    async_client = async_client or AsyncVincClient(api_key, base_url=base_url, space=space)
    plan = await aplan_agent(async_client, address, space=space, capability_map=capability_map)
    graph = _assemble(plan, model, tools, client=None, async_client=async_client,
                      system_prompt=system_prompt, checkpointer=checkpointer,
                      capability_map=capability_map, tool_calls_per_run=tool_calls_per_run,
                      name=name, create_agent_kwargs=create_agent_kwargs,
                      prefetch_max_chars=prefetch_max_chars)
    return VincAgent(graph, plan, client=None, async_client=async_client, record=record,
                     space=space)
