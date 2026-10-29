"""A role's tool rules, applied the same way by every framework package.

The role contract that prefetch reads names tools the role must not use
(``disallowed_tools``), and may set a capability policy and a per-run ``budget``. An
adapter removes disallowed tools from what the model sees, refuses a call to one of them,
and warns once per run about entries that matched none of the agent's tools. Names match
exactly. Capability ids reach tools through a ``capability_map`` the developer gives (tool
name to capability id). Without one, the default maps of :mod:`vinc_client.capabilities`
bind the common servers' tools (GitHub, Linear, Slack, file system, shell); a tool those
maps do not know is left alone. A host that named a server differently (``gh`` for GitHub)
says so in the role's ``tool_servers``, ``VINC_TOOL_SERVERS`` or the ``tool_servers`` argument;
the device setting wins over the role.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from .capabilities import (TOOL_SERVERS_ENV, default_capability, env_tool_servers,
                           parse_tool_servers)
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
UNKNOWN_TOOLS = ("Role %s sets capabilities, but none of this agent's tools is in the default "
                 "capability maps, so they are not enforced; pass a capability_map, or name the "
                 "host's servers in tool_servers (gh=github).")


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
                 unbound: bool = False, run: RolePolicy | None = None,
                 servers: Mapping[str, str] | None = None) -> None:
        self.role_id = role_id
        self.disallowed = frozenset(disallowed)
        self.approve = frozenset(approve) - self.disallowed
        self.tool_calls = tool_calls
        self.unbound = unbound
        # 지도 없이 능력을 정한 역할: 도구 이름마다 기본 지도로 능력 id 를 찾아 판정한다.
        self._run = run if run is not None and run.capabilities else None
        self._servers = dict(servers or {})

    def _verdict(self, name: str | None) -> str | None:
        """기본 지도로 묶인 도구의 판정: ``deny``, ``approve`` 또는 None(손대지 않는다)."""
        if self._run is None:
            return None
        cap = default_capability(name, self._servers)
        if cap is None:
            return None
        verdict = self._run.capability(cap)
        if verdict == "deny" or (verdict is None and self._run.capabilities.get("allow")):
            return "deny"
        return "approve" if verdict == "approve" else None

    @property
    def restricts_tools(self) -> bool:
        """Whether some tools may be hidden: a disallowed name or capabilities on the defaults."""
        return bool(self.disallowed) or self._run is not None

    @property
    def holds_tools(self) -> bool:
        """Whether a call may be refused for approval: an approve name or the defaults."""
        return bool(self.approve) or self._run is not None

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any],
                      capability_map: Mapping[str, str] | None = None, *,
                      tool_servers: Mapping[str, str] | None = None) -> "RoleToolPolicy":
        """Read ``props.disallowed_tools`` and the run policy (:class:`RolePolicy`).

        Absent keys mean no rule. A malformed list, run policy or ``capability_map`` stops
        the run. With a map, a tool whose capability the role denies is disallowed, one it
        holds for approval is refused with a sentence asking for a person, and when the role
        has an ``allow`` list a mapped tool that matches none of it is disallowed. Tools left
        out of the map are not touched. With no map (``None``) the default maps decide the
        same way for the tools they know; an empty map turns them off. ``budget.tool_calls``
        caps all tool calls of a run. ``tool_servers`` (host server name to map name) adds to
        the role's own and to ``VINC_TOOL_SERVERS``, and wins over both.
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
        given = {} if tool_servers is None else parse_tool_servers(tool_servers)
        if given is None:
            raise VincInvalidArgument("invalid_argument", "tool_servers maps a host's server "
                                      "names to github, linear, slack or filesystem.")
        try:
            device = env_tool_servers()
        except ValueError:
            raise VincInvalidArgument("invalid_argument", f"{TOOL_SERVERS_ENV} reads like "
                                      "gh=github,lin=linear (github, linear, slack or "
                                      "filesystem).") from None
        limit = run.limit("tool_calls")
        return cls(str(contract.get("id") or ""), deny, approve=approve,
                   tool_calls=None if limit is None else int(limit),
                   unbound=bool(run.capabilities) and capability_map is not None
                   and not capability_map,
                   run=run if capability_map is None else None,
                   servers={**run.tool_servers, **device, **given})

    def __bool__(self) -> bool:
        # unbound 도 참이다: 어댑터가 첫 모델 호출에서 경고를 한 번 내야 한다.
        return bool(self.disallowed or self.approve or self.tool_calls is not None or self.unbound
                    or self._run is not None)

    def allows(self, name: str | None) -> bool:
        return (name not in self.disallowed and name not in self.approve
                and self._verdict(name) is None)

    def allowed(self, tools: Sequence[T]) -> list[T]:
        """``tools`` without the disallowed ones, in their order. Tools held for approval
        stay visible so the model can ask for one; a call to one is refused."""
        return [tool for tool in tools if tool_name(tool) not in self.disallowed
                and self._verdict(tool_name(tool)) != "deny"]

    def unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Disallowed names that match none of ``tools``, sorted."""
        names = {tool_name(tool) for tool in tools}
        return sorted(self.disallowed - names)

    def warn_unmatched(self, tools: Iterable[Any]) -> list[str]:
        """Log one warning naming the entries that matched no tool, and one when capabilities
        are set but unbound; returns the unmatched entries."""
        tools = list(tools)
        missing = self.unmatched(tools)
        if missing:
            log.warning(UNMATCHED, self.role_id, ", ".join(missing))
        if self.unbound:
            log.warning(UNBOUND, self.role_id)
        elif self._run is not None and not any(default_capability(tool_name(t), self._servers)
                                               for t in tools):
            log.warning(UNKNOWN_TOOLS, self.role_id)
        return missing

    def refusal(self, name: str) -> str:
        """The tool result a refused call gets instead of running."""
        template = APPROVAL if name in self.approve or self._verdict(name) == "approve" else REFUSAL
        return template.format(role=self.role_id, tool=name)

    def over_budget(self, position: int) -> bool:
        """Whether the run's tool call at ``position`` (0 for the first) is past the budget."""
        return self.tool_calls is not None and position >= self.tool_calls

    def budget_message(self) -> str:
        """The tool result a call past the budget gets instead of running."""
        return BUDGET.format(role=self.role_id, limit=self.tool_calls)
