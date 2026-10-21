"""A role's ``disallowed_tools``, applied the same way by every framework package.

The role contract that prefetch reads names tools the role must not use. An adapter
removes those tools from what the model sees, refuses a call to one of them, and warns
once per run about entries that matched none of the agent's tools. Names match exactly;
nothing here maps one tool vocabulary onto another.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from .errors import VincInvalidArgument

log = logging.getLogger("vinc_client")

T = TypeVar("T")

#: 계약 칸 이름. Claude Code 서브에이전트 내보내기(``disallowedTools``)와 같은 칸이다.
DISALLOWED_KEY = "disallowed_tools"
REFUSAL = "The role {role} does not allow the {tool} tool; continue without it."
UNMATCHED = ("Role %s disallows tools that match none of this agent's tools: %s. "
             "Names match exactly; check the role contract against the agent's tool names.")


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
    """The tools one role contract disallows, by exact name."""

    def __init__(self, role_id: str, disallowed: Iterable[str] = ()) -> None:
        self.role_id = role_id
        self.disallowed = frozenset(disallowed)

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any]) -> "RoleToolPolicy":
        """Read ``props.disallowed_tools``; absent or null means none. A malformed list stops the run."""
        props = contract.get("props")
        raw = props.get(DISALLOWED_KEY) if isinstance(props, Mapping) else None
        raw = [] if raw is None else raw
        if (not isinstance(raw, list) or len(raw) > 256
                or any(not isinstance(n, str) or not n.strip() or len(n) > 256 for n in raw)):
            raise VincInvalidArgument("invalid_role_contract", "Invalid role tool list.")
        return cls(str(contract.get("id") or ""), raw)

    def __bool__(self) -> bool:
        return bool(self.disallowed)

    def allows(self, name: str | None) -> bool:
        return name not in self.disallowed

    def allowed(self, tools: Sequence[T]) -> list[T]:
        """``tools`` without the disallowed ones, in their order."""
        return [tool for tool in tools if self.allows(tool_name(tool))]

    def unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Disallowed names that match none of ``tools``, sorted."""
        names = {tool_name(tool) for tool in tools}
        return sorted(self.disallowed - names)

    def warn_unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Log one warning naming the entries that matched no tool; returns them."""
        missing = self.unmatched(tools)
        if missing:
            log.warning(UNMATCHED, self.role_id, ", ".join(missing))
        return missing

    def refusal(self, name: str) -> str:
        """The tool result a refused call gets instead of running."""
        return REFUSAL.format(role=self.role_id, tool=name)
