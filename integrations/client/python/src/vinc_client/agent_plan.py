"""A role read once into everything an agent needs to run it: ``plan_agent``.

Loading a role into a framework used to be a hand-written mapping. This reads the role
contract once, pins it by hash, and derives the same pieces for every framework: the tool
policy, the output gate, the actions only a person may take, the roles that follow it by
live edges, and the budget. Nothing here calls a model, and nothing writes.

:func:`finish_run` closes a run: it maps what happened to a run outcome and writes the run
record on purpose, never raising for a failed write so the answer is not lost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .errors import VincError, VincInvalidArgument
from .output_gate import GateResult, OutputGate, sources_line
from .role_policy import RolePolicy
from .tool_policy import RoleToolPolicy

ROLE_PREFIX = "concept:agent-"
#: How a run ends, from what the gate said. ``interrupt`` waits for a person.
OUTCOME_BY_VERDICT = {"pass": "success", "block": "blocked", "interrupt": "waiting"}
_ERROR_CAP = 200


def role_address(address: str) -> str:
    """``reviewer``, ``agent-reviewer`` and ``concept:agent-reviewer`` name the same role."""
    text = str(address or "").strip()
    if not text or any(ch.isspace() for ch in text):
        raise VincInvalidArgument("invalid_argument", "Name the role to run.")
    if text.startswith("concept:"):
        text = text[len("concept:"):]
    if not text.startswith("agent-"):
        text = "agent-" + text
    return "concept:" + text


@dataclass(frozen=True)
class AgentPlan:
    """One pinned role, ready to be turned into a framework agent."""

    role_id: str
    title: str
    pin: str
    rev: int | None
    contract: Mapping[str, Any]
    policy: RoleToolPolicy
    gate: OutputGate
    person_only: tuple[str, ...]
    hands_to: tuple[str, ...]
    receives_from: tuple[str, ...]
    budget: Mapping[str, float]

    @property
    def props(self) -> Mapping[str, Any]:
        return self.contract.get("props") or {}


def _others(edges: Any, side: str) -> tuple[str, ...]:
    """Role ids at the other end of the edges, in the order the hub returned them."""
    seen: list[str] = []
    for edge in edges or ():
        other = edge.get(side) if isinstance(edge, Mapping) else None
        if isinstance(other, str) and other and other not in seen:
            seen.append(other)
    return tuple(seen)


def _plan(contract: Mapping[str, Any], capability_map: Mapping[str, str] | None,
          self_authored: bool) -> AgentPlan:
    props = contract.get("props")
    if not contract.get("id") or not isinstance(props, Mapping):
        raise VincInvalidArgument("invalid_role_contract", "The role has no readable contract.")
    # 계약이 잘못됐으면 모델을 부르기 전에 여기서 멈춘다.
    policy = RoleToolPolicy.from_contract(contract, capability_map)
    run = RolePolicy.from_contract(contract)
    budget = {key: run.limit(key) for key in ("tool_calls", "model_calls", "tokens", "cost_usd",
                                              "wall_seconds") if run.limit(key) is not None}
    person = props.get("person_only")
    actions = tuple(p["action"] for p in person
                    if isinstance(p, Mapping) and isinstance(p.get("action"), str)) \
        if isinstance(person, list) else ()
    links = contract.get("works_with") or {}
    return AgentPlan(
        role_id=str(contract["id"]), title=str(contract.get("title") or ""),
        pin=str(contract.get("hash") or ""), rev=contract.get("rev"), contract=contract,
        policy=policy, gate=OutputGate(props, self_authored=self_authored),
        person_only=actions, hands_to=_others(links.get("hands_to"), "to"),
        receives_from=_others(links.get("receives_from"), "from"), budget=budget)


def plan_agent(client: Any, address: str, *, space: str | None = None,
               capability_map: Mapping[str, str] | None = None,
               self_authored: bool = False) -> AgentPlan:
    """Read ``address`` with a :class:`VincClient` and pin its contract by hash."""
    return _plan(client.role(role_address(address), space=space), capability_map, self_authored)


async def aplan_agent(client: Any, address: str, *, space: str | None = None,
                      capability_map: Mapping[str, str] | None = None,
                      self_authored: bool = False) -> AgentPlan:
    """:func:`plan_agent` for an :class:`AsyncVincClient`."""
    return _plan(await client.role(role_address(address), space=space), capability_map,
                 self_authored)


@dataclass
class AgentRun:
    """What one run of a built agent ended with."""

    run_id: str
    role_id: str
    outcome: str
    output: str | None
    gate: GateResult | None
    record: dict[str, Any] | None = None
    record_error: str | None = None
    cited_ids: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """``done``, ``blocked`` or ``waiting`` for a person; ``error`` runs raise instead."""
        return {"success": "done", "blocked": "blocked", "waiting": "waiting"}.get(
            self.outcome, self.outcome)


def run_result(plan: AgentPlan, run_id: str, output: str | None,
               gate: GateResult | None) -> AgentRun:
    """The run as it stands: the outcome follows the gate's verdict, ``pass`` when none ran."""
    verdict = gate.verdict if gate is not None else "pass"
    cited = tuple(sources_line(output) or ()) if output else ()
    return AgentRun(run_id, plan.role_id, OUTCOME_BY_VERDICT.get(verdict, "blocked"), output,
                    gate, cited_ids=cited)


def _record_fields(result: AgentRun, host: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"host": host}
    if result.gate is not None:
        fields["gate_results"] = {"verdict": result.gate.verdict,
                                  "failed_checks": ",".join(result.gate.failed_checks)}
    if result.cited_ids:
        fields["cited_ids"] = list(result.cited_ids)
    return fields


def finish_run(client: Any, result: AgentRun, *, host: str, space: str | None = None) -> AgentRun:
    """Write the run record. A failed write is kept on the result, never raised."""
    try:
        result.record = client.record_run(result.run_id, role=result.role_id,
                                          outcome=result.outcome, space=space,
                                          **_record_fields(result, host))
    except VincError as exc:
        result.record_error = f"{exc.code}: {str(exc)[:_ERROR_CAP]}"
    return result


async def afinish_run(client: Any, result: AgentRun, *, host: str,
                      space: str | None = None) -> AgentRun:
    """:func:`finish_run` for an :class:`AsyncVincClient`."""
    try:
        result.record = await client.record_run(result.run_id, role=result.role_id,
                                                outcome=result.outcome, space=space,
                                                **_record_fields(result, host))
    except VincError as exc:
        result.record_error = f"{exc.code}: {str(exc)[:_ERROR_CAP]}"
    return result


def failed_run(plan: AgentPlan, run_id: str) -> AgentRun:
    """A run that raised: recorded as ``error`` so the failure is not invisible."""
    return AgentRun(run_id, plan.role_id, "error", None, None)
