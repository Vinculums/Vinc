"""A CrewAI ``Agent`` built from one Vinc role contract.

``create_agent`` reads the role once to give the agent its role, goal and backstory.
Every task the agent then executes is one run: before the model is called the run
reads the role contract, its required documents and the task evidence through
``prefetch_with_contract`` (the call the other adapters make), hands that block to
the task as context, and applies the contract's ``disallowed_tools`` to the tools the
task's executor is built with. Nothing here writes to Vinc.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from crewai import Agent
from crewai.tools import BaseTool
from pydantic import PrivateAttr

from vinc.sdk import AsyncVincClient, VincClient
from vinc.sdk.context import DEFAULT_MAX_CHARS
from vinc.sdk.package import DEFAULT_PACKAGE_MAX_CHARS
from vinc.sdk.package import package_space as _checked_space
from vinc.sdk.prefetch import DEFAULT_PREFETCH_MAX_CHARS, RolePrefetch
from vinc.sdk.tool_policy import RoleToolPolicy
from vinc.sdk.tools import TOOL_CALLS_PER_RUN, VINC_TOOL_NAMES, ToolCallCap

from .tools import create_vinc_tools

#: create_agent 이 정하는 칸. 호출자가 덮으면 계약과 다른 페르소나가 된다.
_OWN_FIELDS = ("role", "goal", "backstory")


def _bullet(item: Any) -> str:
    # 렌더러(vinc_mcp.roles._bullets)와 같은 모양: 구조화된 사람 전용 항목은 "action: reason".
    if isinstance(item, Mapping) and set(item) == {"action", "reason"}:
        return f"- {item['action']}: {item['reason']}"
    return f"- {item}"


def role_fields(contract: Mapping[str, Any]) -> dict[str, str]:
    """``role``, ``goal`` and ``backstory`` for a CrewAI ``Agent`` from one role contract.

    The role is the job title, the goal is the mission, and the backstory names the role
    and carries its prohibitions (``never``) and the actions only a person takes
    (``person_only``). The complete contract still reaches every run in the prefetch block.
    """
    props = contract.get("props")
    role_id = contract.get("id")
    if not contract.get("found", True) or not isinstance(props, Mapping) or not role_id:
        raise ValueError("not a role contract; read it with client.role(role_id)")
    mission = props.get("mission")
    if not isinstance(mission, str) or not mission.strip():
        raise ValueError(f"role {role_id} has no mission")
    title = str(props.get("job_title") or contract.get("title") or role_id).strip()
    lines = [f"You act as the Vinc role {role_id} ({title}).", "", "Never:"]
    lines += [_bullet(item) for item in props.get("never") or []] or ["- (none)"]
    lines += ["", "Only a person does these. Propose them and stop; do not do them:"]
    lines += [_bullet(item) for item in props.get("person_only") or []] or ["- (none)"]
    return {"role": title, "goal": " ".join(mission.split()), "backstory": "\n".join(lines)}


@dataclass(eq=False)
class _Binding:
    client: VincClient
    async_client: AsyncVincClient | None
    role_id: str
    prefetch_max_chars: int
    prefetch_pin: str | None
    expose_tools: bool
    tool_calls_per_run: int | None
    max_chars: int
    package_id: str | None
    package_space: str | None
    package_max_chars: int
    capability_map: Mapping[str, str] | None


@dataclass(eq=False)
class _Run:
    """One task execution: the contract it read and the tools it may use, to its end."""
    agent: "VincAgent"
    task: Any
    block: str
    policy: RoleToolPolicy
    vinc_tools: list[Any]
    warned: bool = False
    #: 사람 전용 단계나 게이트가 멈춘 이유. CrewAI 의 재시도가 같은 런에 다시 들어오면 이걸 올린다.
    stopped: BaseException | None = None
    #: 이 런에서 실행기가 부른 도구 호출 수. budget.tool_calls 를 센다.
    used: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def held(self, name: str) -> str | None:
        """The result a call to ``name`` gets instead of running, or ``None`` to run it."""
        # 다른 두 어댑터와 같은 순서: 예산이 먼저, 그다음 승인 보류.
        with self.lock:
            position, self.used = self.used, self.used + 1
        if self.policy.over_budget(position):
            return self.policy.budget_message()
        if not self.policy.allows(name):
            return self.policy.refusal(name)
        return None


class _RunTool(BaseTool):
    """One tool of one run under the role's approval holds and call budget.

    The model sees the original's name, description and arguments. A call the policy holds
    gets its sentence and the original never runs; any other call runs the original.
    """
    _original: Any = PrivateAttr(default=None)
    _vinc_run: Any = PrivateAttr(default=None)

    @classmethod
    def wrap(cls, original: Any, run: _Run) -> "_RunTool":
        fields = {"name": original.name, "description": original.description,
                  "args_schema": original.args_schema}
        for key in ("result_as_answer", "cache_function"):
            if hasattr(original, key):
                fields[key] = getattr(original, key)
        tool = cls(**fields)
        tool._original, tool._vinc_run = original, run
        return tool

    def _run(self, **kwargs: Any) -> Any:
        return self._vinc_run.held(self.name) or self._original.run(**kwargs)

    async def _arun(self, **kwargs: Any) -> Any:
        held = self._vinc_run.held(self.name)
        if held:
            return held
        if type(self._original)._arun is BaseTool._arun:
            return await asyncio.to_thread(self._original.run, **kwargs)
        return await self._original.arun(**kwargs)


_RUN: contextvars.ContextVar[_Run | None] = contextvars.ContextVar("vinc_crewai_run", default=None)


def current_run() -> _Run | None:
    """The Vinc run of the task executing in this context, if any."""
    return _RUN.get()


def _task_text(task: Any) -> str:
    return str(getattr(task, "description", "") or "").strip()


def _with_block(block: str, context: str | None) -> str:
    return f"{block}\n\n{context}" if context else block


class VincAgent(Agent):
    """A CrewAI ``Agent`` bound to one Vinc role. Build it with :func:`create_agent`.

    Each ``execute_task`` / ``aexecute_task`` call is one run. Before the model is called
    the run reads the role contract, every required document in full and the task evidence
    (``prefetch_with_contract``, no truncation; a block over ``prefetch_max_chars`` raises
    ``VincInvalidArgument`` with code ``prefetch_budget_exceeded`` and the model is not
    called). The block reaches the model as the task's context, ahead of any context
    CrewAI passes from earlier tasks.

    The contract's ``disallowed_tools`` are removed, by exact tool name, from the tools the
    task's executor is built with, so the model does not see them and CrewAI cannot run
    them; entries that match none of the agent's tools give one warning per run. The run
    policy applies too (``RoleToolPolicy.from_contract``): with ``capability_map`` a tool
    the role denies is removed the same way and a call to one it holds for approval gets
    a sentence asking for a person, and ``budget.tool_calls`` caps all tool calls of the
    run, in the order they run. With ``expose_tools`` the run adds ``vinc_brief`` and
    ``vinc_search`` with a fresh call cap.

    ``kickoff`` (an agent outside a crew) is refused: it would skip the role. Run the agent
    on a ``Task`` in a ``Crew``.
    """

    _vinc: _Binding | None = PrivateAttr(default=None)

    @property
    def vinc_role_id(self) -> str | None:
        return self._vinc.role_id if self._vinc else None

    def copy(self) -> "VincAgent":  # type: ignore[override]
        # Crew.copy()·kickoff_for_each 가 에이전트를 복제한다. 역할 결속이 빠지면 역할 없이 돈다.
        clone = super().copy()
        clone._vinc = self._vinc
        return clone

    def _binding(self) -> _Binding:
        if self._vinc is None:
            raise RuntimeError("this VincAgent is bound to no role; build it with create_agent")
        return self._vinc

    def _rejoin(self, task: Any) -> _Run | None:
        # CrewAI 는 실행 오류가 나면 같은 에이전트의 execute_task 를 다시 부른다. 그 재진입은 같은
        # 런이다: 읽은 블록·도구·상한을 그대로 쓰고, 멈춘 런이면 모델을 다시 부르지 않고 올린다.
        run = _RUN.get()
        if run is None or run.agent is not self or run.task is not task:
            return None
        if run.stopped is not None:
            raise run.stopped
        return run

    def _start(self, task: Any, fetched: RolePrefetch, package: str | None) -> _Run:
        binding = self._binding()
        # 계약의 도구 목록이나 실행 정책이 잘못됐으면 모델 호출 전에 여기서 멈춘다.
        policy = RoleToolPolicy.from_contract(fetched.contract, binding.capability_map)
        tools = (create_vinc_tools(binding.client, binding.async_client, max_chars=binding.max_chars,
                                   cap=ToolCallCap(binding.tool_calls_per_run))
                 if binding.expose_tools else [])
        block = fetched.block if package is None else f"{fetched.block}\n\n{package}"
        return _Run(self, task, block, policy, tools)

    def _prefetch(self, task: Any) -> tuple[RolePrefetch, str | None]:
        binding = self._binding()
        fetched = binding.client.prefetch_with_contract(
            binding.role_id, _task_text(task), max_chars=binding.prefetch_max_chars,
            pin=binding.prefetch_pin)
        package = (binding.client.get_package_context(
            binding.package_id, space=binding.package_space, max_chars=binding.package_max_chars)
            if binding.package_id else None)
        return fetched, package

    async def _aprefetch(self, task: Any) -> tuple[RolePrefetch, str | None]:
        binding = self._binding()
        client = binding.async_client
        if client is None:
            return await asyncio.to_thread(self._prefetch, task)
        fetched = await client.prefetch_with_contract(
            binding.role_id, _task_text(task), max_chars=binding.prefetch_max_chars,
            pin=binding.prefetch_pin)
        package = (await client.get_package_context(
            binding.package_id, space=binding.package_space, max_chars=binding.package_max_chars)
            if binding.package_id else None)
        return fetched, package

    def execute_task(self, task: Any, context: str | None = None,
                     tools: list[Any] | None = None) -> Any:
        if self._rejoin(task) is not None:
            return super().execute_task(task, context, tools)
        run = self._start(task, *self._prefetch(task))
        token = _RUN.set(run)
        try:
            return super().execute_task(task, _with_block(run.block, context), tools)
        finally:
            _RUN.reset(token)

    async def aexecute_task(self, task: Any, context: str | None = None,
                            tools: list[Any] | None = None) -> Any:
        if self._rejoin(task) is not None:
            return await super().aexecute_task(task, context, tools)
        run = self._start(task, *(await self._aprefetch(task)))
        token = _RUN.set(run)
        try:
            return await super().aexecute_task(task, _with_block(run.block, context), tools)
        finally:
            _RUN.reset(token)

    def create_agent_executor(self, tools: list[Any] | None = None, task: Any = None) -> None:
        # CrewAI 는 태스크마다 여기서 실행기를 짓는다(prepare_tools). 이 런의 정책을 마지막에 건다:
        # 태스크 도구·에이전트 도구·위임 도구 무엇이 와도 금지 도구는 실행기에 들어가지 않는다.
        run = _RUN.get()
        if run is None or run.agent is not self:
            return super().create_agent_executor(tools=tools, task=task)
        given = list(tools if tools is not None else self.tools or [])
        if run.vinc_tools:
            given = [t for t in given if getattr(t, "name", None) not in VINC_TOOL_NAMES]
            given += run.vinc_tools
        if not run.warned:
            run.warned = True
            run.policy.warn_unmatched(given)
        allowed = run.policy.allowed(given)
        if run.policy.approve or run.policy.tool_calls is not None:
            # 승인 보류 도구는 모델에 보이되 부르면 문장으로 답하고, 예산은 이 런의 모든 호출을 센다.
            allowed = [_RunTool.wrap(tool, run) for tool in allowed]
        return super().create_agent_executor(tools=allowed, task=task)

    def kickoff(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        raise NotImplementedError(_NO_KICKOFF)

    def kickoff_async(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        raise NotImplementedError(_NO_KICKOFF)

    async def akickoff(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        raise NotImplementedError(_NO_KICKOFF)


_NO_KICKOFF = ("A VincAgent reads its role before each task; Agent.kickoff would run without "
               "it. Give the agent a Task and run it in a Crew.")


def create_agent(client: VincClient, role_id: str, *, llm: Any = None, tools: Sequence[Any] = (),
                 async_client: AsyncVincClient | None = None,
                 prefetch_max_chars: int = DEFAULT_PREFETCH_MAX_CHARS,
                 prefetch_pin: str | None = None, expose_tools: bool = False,
                 tool_calls_per_run: int | None = TOOL_CALLS_PER_RUN,
                 max_chars: int = DEFAULT_MAX_CHARS, package_id: str | None = None,
                 package_space: str | None = None,
                 package_max_chars: int = DEFAULT_PACKAGE_MAX_CHARS,
                 capability_map: Mapping[str, str] | None = None,
                 **agent_kwargs: Any) -> VincAgent:
    """Build a :class:`VincAgent` for ``role_id`` (``concept:agent-...``).

    Reads the role once (``client.role``, which falls back to the compact form for a role
    over the hub's read limit) for the agent's role, goal and backstory. ``prefetch_pin``
    (the ``hash`` from ``client.role``) holds this read and every run's read to that
    contract: a changed role raises ``VincRoleChanged`` and the model is not called.

    ``async_client`` serves ``Crew.akickoff``; without it the async path runs the sync
    reads in a thread. ``package_id`` with ``package_space`` adds one handoff package to
    every run, read with the current key. ``capability_map`` (tool name to capability id)
    binds the role's capability policy to this agent's tools. Other keyword arguments go to
    ``Agent``.
    """
    taken = sorted(set(agent_kwargs) & set(_OWN_FIELDS))
    if taken:
        raise TypeError(f"create_agent sets {', '.join(taken)} from the role contract")
    if (package_id is None) != (package_space is None):
        raise ValueError("package_id and package_space go together")
    contract = client.role(role_id, pin=prefetch_pin)
    agent = VincAgent(**role_fields(contract), llm=llm, tools=list(tools), **agent_kwargs)
    agent._vinc = _Binding(
        client=client, async_client=async_client, role_id=str(contract["id"]),
        prefetch_max_chars=prefetch_max_chars, prefetch_pin=prefetch_pin,
        expose_tools=expose_tools, tool_calls_per_run=tool_calls_per_run, max_chars=max_chars,
        package_id=package_id,
        package_space=None if package_space is None else _checked_space(package_space),
        package_max_chars=package_max_chars, capability_map=capability_map)
    return agent
