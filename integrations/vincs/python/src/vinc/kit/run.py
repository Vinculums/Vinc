"""``run_role``: build an agent from a role address, run one task with the person's own model, and
write the run record with the raw trace.

Everything runs on the person's machine with the person's key. Vinc calls no model: the model is
the one passed in (an object, or a ``provider:name`` string for LangGraph and CrewAI), and the
only thing sent to Vinc is the run record. The trace stays a local file; the record keeps its
hash, size and name.

What each adapter gives today:

- ``langgraph``: the raw trace, the budget guard (``model_calls``, ``tokens``, ``wall_seconds``),
  and the counts of model and tool calls.
- ``crewai`` and ``agent-framework``: the run, the gates and the record. No trace file and no
  counts yet; the record says only what the run showed.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from vinc.sdk import AsyncVincClient, VincClient
from vinc.sdk.agent_plan import AgentRun, afinish_run, finish_run
from vinc.sdk.errors import VincInvalidArgument
from vinc.sdk.output_gate import GateResult
from vinc.sdk.run import trace_ref

from .trace import BudgetExceeded, Meter, Trace

ADAPTERS = ("langgraph", "agent-framework", "crewai")
DEFAULT_TRACE_DIR = "vinc-traces"


class KitError(ValueError):
    """The run could not start: an unknown adapter, or a model the kit cannot build."""


@dataclass
class KitRun:
    """One finished run: the agent's result, and what the kit saw around it."""

    run: AgentRun
    adapter: str
    model_family: str
    trace: Path | None = None
    steps: int | None = None
    tool_calls: int | None = None
    #: What the run cost at list price, when the model reports it (a subscription run does).
    cost_usd: float | None = None


def model_family(model: Any) -> str:
    """``anthropic`` for ``anthropic:claude-...``; the lower-cased class name for an object."""
    if isinstance(model, str):
        return (model.replace("/", ":", 1).partition(":")[0] or model).strip().lower()
    return type(model).__name__.lower()


def resolve_model(adapter: str, model: Any) -> Any:
    """An object is used as it is. A ``provider:name`` string becomes the framework's model."""
    if adapter not in ADAPTERS:
        raise KitError(f"adapter must be one of: {', '.join(ADAPTERS)}")
    if not isinstance(model, str):
        return model
    provider, sep, name = model.partition(":")
    if not sep or not provider or not name:
        raise KitError("write the model as provider:name, for example anthropic:claude-sonnet-5-5")
    if provider == "claude-code":
        if adapter != "langgraph":
            raise KitError("claude-code:<model id> runs through the langgraph adapter only")
        from .claude_code import ClaudeCodeChat
        return ClaudeCodeChat(model=name)
    if adapter == "langgraph":
        try:
            from langchain.chat_models import init_chat_model
        except ImportError:
            raise KitError('LangGraph needs its extra: pip install "vincs[langgraph]"') from None
        try:
            return init_chat_model(name, model_provider=provider)
        except Exception as exc:  # 공급자 패키지·키가 없는 경우를 한 문장으로.
            raise KitError(f"could not build {model!r}: {exc}") from None
    if adapter == "crewai":
        return f"{provider}/{name}"
    raise KitError("Agent Framework chat clients come from provider packages, so pass the client "
                   "object to run_role in Python; a provider:name string is not enough")


def _explain(exc: Exception) -> Exception:
    """A role whose required reading is longer than the prefetch budget gets one actionable sentence."""
    if isinstance(exc, VincInvalidArgument) and getattr(exc, "code", "") == "prefetch_budget_exceeded":
        return KitError(f"{exc} Pass --prefetch-max-chars (prefetch_max_chars=) with at least that "
                        "many characters to let the role's required reading in.")
    return exc


def _blocked_by_budget(plan_role: str, run_id: str, exc: BudgetExceeded) -> AgentRun:
    return AgentRun(run_id, plan_role, "blocked", None, GateResult("block", (f"budget_{exc.key}",)))


def _extra(family: str, trace_path: Path | None, meter: Meter | None,
           cost_usd: float | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {"model_family": family}
    if cost_usd is not None:
        fields["cost_usd"] = cost_usd
    if trace_path is not None:
        fields["trace"] = trace_ref(trace_path)
    if meter is not None:
        fields["steps"], fields["tool_calls"] = meter.model_calls, meter.tool_calls
        if meter.tokens_in or meter.tokens_out:
            fields["tokens_in"], fields["tokens_out"] = meter.tokens_in, meter.tokens_out
    return fields


def run_role(address: str, task: str, *, model: Any, adapter: str = "langgraph",
             client: VincClient | None = None, async_client: AsyncVincClient | None = None,
             api_key: str | None = None, base_url: str | None = None, space: str | None = None,
             run_id: str | None = None, record: bool = True,
             trace_dir: str | Path | None = DEFAULT_TRACE_DIR, tools: Sequence[Any] = (),
             **build_kwargs: Any) -> KitRun:
    """Run ``task`` as the role at ``address`` and return the result.

    ``model`` is the person's model: an object for the chosen framework, or ``provider:name``
    for LangGraph and CrewAI. ``record=True`` writes the run record (a ``vinc_sk_`` key); a failed
    write is kept on ``run.record_error`` and never raised. ``trace_dir=None`` writes no trace
    file. A person-only action ends the run in ``waiting`` and a failing gate in ``blocked``;
    neither executes anything. Other keyword arguments go to the adapter's ``build_agent``.
    """
    if not isinstance(task, str) or not task.strip():
        raise KitError("pass the task as a non-empty string")
    llm = resolve_model(adapter, model)
    rid = run_id or "run-" + uuid.uuid4().hex[:12]
    family = model_family(model)
    if adapter == "agent-framework":
        return asyncio.run(_run_agent_framework(address, task, llm, family, rid, tools, async_client,
                                                api_key, base_url, space, record, build_kwargs))
    sync = client or VincClient(api_key, base_url=base_url, space=space)
    if adapter == "crewai":
        from vinc.integrations.crewai import build_agent
        built = build_agent(address, llm, tools=tools, client=sync, async_client=async_client,
                            space=space, record=False, **build_kwargs)
        try:
            result = built.run(task, run_id=rid)
        except VincInvalidArgument as exc:
            raise _explain(exc) from None
        meter, path, cost = None, None, None
        plan = built.plan
    else:
        from vinc.integrations.langgraph import build_agent

        from .langgraph_trace import LangGraphTrace
        built = build_agent(address, llm, tools=tools, client=sync, async_client=async_client,
                            space=space, record=False, **build_kwargs)
        plan, cost = built.plan, None
        meter, trace = Meter(plan.budget), Trace()
        price_before = getattr(llm, "list_price_usd", None)
        try:
            result = built.invoke(task, {"callbacks": [LangGraphTrace(trace, meter)]}, run_id=rid)
        except BudgetExceeded as exc:
            result = _blocked_by_budget(plan.role_id, rid, exc)
        except VincInvalidArgument as exc:
            raise _explain(exc) from None
        path = trace.write(trace_dir) if trace_dir is not None and trace.events else None
        price_after = getattr(llm, "list_price_usd", None)
        if price_before is not None and price_after is not None:
            cost = round(price_after - price_before, 8)
    if record:
        finish_run(sync, result, host=adapter, space=space,
                   extra=_extra(family, path, meter, cost))
    return KitRun(result, adapter, family, path, meter.model_calls if meter else None,
                  meter.tool_calls if meter else None, cost)


async def _run_agent_framework(address, task, chat_client, family, rid, tools, async_client, api_key,
                               base_url, space, record, build_kwargs) -> KitRun:
    from vinc.integrations.agent_framework import build_agent
    aclient = async_client or AsyncVincClient(api_key, base_url=base_url, space=space)
    built = await build_agent(address, chat_client, tools=tools, client=aclient, space=space,
                              record=False, **build_kwargs)
    try:
        result = await built.run(task, run_id=rid)
    except VincInvalidArgument as exc:
        raise _explain(exc) from None
    if record:
        await afinish_run(aclient, result, host="agent-framework", space=space,
                          extra=_extra(family, None, None))
    return KitRun(result, "agent-framework", family)
