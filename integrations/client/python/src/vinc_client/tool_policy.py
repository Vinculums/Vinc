"""A role's tool rules, applied the same way by every framework package.

The role contract that prefetch reads names tools the role must not use
(``disallowed_tools``), and may set a capability policy and a per-run ``budget``. An
adapter removes disallowed tools from what the model sees, refuses a call to one of them,
and warns once per run about entries that matched none of the agent's tools. Names match
exactly. Capability ids reach tools only through a ``capability_map`` the developer gives
(tool name to capability id); nothing here guesses one vocabulary from the other.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from .errors import VincInvalidArgument
from .role_policy import CAPABILITY, RolePolicy

log = logging.getLogger("vinc_client")

T = TypeVar("T")

#: 계약 칸 이름. Claude Code 서브에이전트 내보내기(``disallowedTools``)와 같은 칸이다.
DISALLOWED_KEY = "disallowed_tools"
REFUSAL = "The role {role} does not allow the {tool} tool; continue without it."
UNMATCHED = ("Role %s disallows tools that match none of this agent's tools: %s. "
             "Names match exactly; check the role contract against the agent's tool names.")
APPROVAL = ("The role {role} needs a person's approval before the {tool} tool runs; "
            "ask for it instead of calling the tool.")
BUDGET = "The role {role} allows {limit} tool calls per run; answer from what you already have."
UNBOUND = ("Role %s sets capabilities, but no capability_map binds this agent's tools to "
           "capability ids, so they are not enforced.")


def tool_name(tool: Any) -> str | None:
    """The name a model sees for ``tool``: a tool object's ``name``, or a provider dict's."""
    if isinstance(tool, Mapping):
        name = tool.get("name")
        if not isinstance(name, str):
            function = tool.get("function")
            name = function.get("name") if isinstance(function, Mapping) else None
    else:
        name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


class RoleToolPolicy:
    """The tools one role contract disallows or holds for approval, and its call budget."""

    def __init__(self, role_id: str, disallowed: Iterable[str] = (), *,
                 approve: Iterable[str] = (), tool_calls: int | None = None,
                 unbound: bool = False) -> None:
        self.role_id = role_id
        self.disallowed = frozenset(disallowed)
        self.approve = frozenset(approve) - self.disallowed
        self.tool_calls = tool_calls
        self.unbound = unbound

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any],
                      capability_map: Mapping[str, str] | None = None) -> "RoleToolPolicy":
        """Read ``props.disallowed_tools`` and the run policy (:class:`RolePolicy`).

        Absent keys mean no rule. A malformed list, run policy or ``capability_map`` stops
        the run. With a map, a tool whose capability the role denies is disallowed, one it
        holds for approval is refused with a sentence asking for a person, and when the role
        has an ``allow`` list a mapped tool that matches none of it is disallowed. Tools left
        out of the map are not touched. ``budget.tool_calls`` caps all tool calls of a run.
        """
        props = contract.get("props")
        raw = props.get(DISALLOWED_KEY) if isinstance(props, Mapping) else None
        raw = [] if raw is None else raw
        if (not isinstance(raw, list) or len(raw) > 256
                or any(not isinstance(n, str) or not n.strip() or len(n) > 256 for n in raw)):
            raise VincInvalidArgument("invalid_role_contract", "Invalid role tool list.")
        run = RolePolicy.from_contract(contract)
        if capability_map is not None and (
                not isinstance(capability_map, Mapping)
                or any(not isinstance(k, str) or not k or not isinstance(v, str)
                       or not CAPABILITY.fullmatch(v) for k, v in capability_map.items())):
            raise VincInvalidArgument("invalid_argument",
                                      "capability_map maps tool names to capability ids.")
        deny, approve = set(raw), set()
        allow_list = bool(run.capabilities.get("allow"))
        for tool, cap in (capability_map or {}).items():
            verdict = run.capability(cap)
            if verdict == "deny" or (verdict is None and allow_list):
                deny.add(tool)
            elif verdict == "approve":
                approve.add(tool)
        limit = run.limit("tool_calls")
        return cls(str(contract.get("id") or ""), deny, approve=approve,
                   tool_calls=None if limit is None else int(limit),
                   unbound=bool(run.capabilities) and not capability_map)

    def __bool__(self) -> bool:
        # unbound 도 참이다: 어댑터가 첫 모델 호출에서 경고를 한 번 내야 한다.
        return bool(self.disallowed or self.approve or self.tool_calls is not None or self.unbound)

    def allows(self, name: str | None) -> bool:
        return name not in self.disallowed and name not in self.approve

    def allowed(self, tools: Sequence[T]) -> list[T]:
        """``tools`` without the disallowed ones, in their order. Tools held for approval
        stay visible so the model can ask for one; a call to one is refused."""
        return [tool for tool in tools if tool_name(tool) not in self.disallowed]

    def unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Disallowed names that match none of ``tools``, sorted."""
        names = {tool_name(tool) for tool in tools}
        return sorted(self.disallowed - names)

    def warn_unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Log one warning naming the entries that matched no tool, and one when capabilities
        are set but unbound; returns the unmatched entries."""
        missing = self.unmatched(tools)
        if missing:
            log.warning(UNMATCHED, self.role_id, ", ".join(missing))
        if self.unbound:
            log.warning(UNBOUND, self.role_id)
        return missing

    def refusal(self, name: str) -> str:
        """The tool result a refused call gets instead of running."""
        template = APPROVAL if name in self.approve else REFUSAL
        return template.format(role=self.role_id, tool=name)

    def over_budget(self, position: int) -> bool:
        """Whether the run's tool call at ``position`` (0 for the first) is past the budget."""
        return self.tool_calls is not None and position >= self.tool_calls

    def budget_message(self) -> str:
        """The tool result a call past the budget gets instead of running."""
        return BUDGET.format(role=self.role_id, limit=self.tool_calls)
