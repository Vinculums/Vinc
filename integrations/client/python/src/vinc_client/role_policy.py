"""A role's run policy: what it outputs, how it acts, its budget and its capabilities.

The four keys are optional in the role contract. The hub warns when one is malformed and
still stores it; a run is stricter: :meth:`RolePolicy.from_contract` raises before the model
is called. Hosts and adapters enforce the policy. Nothing here calls a model or grants a
permission: ``act`` and ``capability`` only answer what the contract says.
"""
from __future__ import annotations

import fnmatch
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .errors import VincInvalidArgument

# 어휘는 허브의 쓰기 검사(api/role_policy_contract.py)와 같다. integrations/parity 가 고정한다.
OUTPUT_KINDS = ("prose", "code", "verdict", "write_proposal", "none")
ACT_MODES = ("act", "act_and_report", "ask_first", "never")
BUDGET_INTS = ("tool_calls", "model_calls", "tokens")
BUDGET_NUMBERS = ("cost_usd", "wall_seconds")
CAPABILITY_LISTS = ("allow", "deny", "approve")
MAX_ENTRIES = 256
ACTION = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
CAPABILITY = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?:\.\*)?")


def _bad(what: str) -> VincInvalidArgument:
    return VincInvalidArgument("invalid_role_contract", f"Invalid role {what}.")


def _number(value: Any, *, integer: bool) -> bool:
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and value >= 0
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class RolePolicy:
    """The parsed run policy of one role contract. Empty when the role sets none of it."""

    role_id: str
    output_kind: str | None = None
    act_policy: Mapping[str, str] = field(default_factory=dict)
    budget: Mapping[str, float] = field(default_factory=dict)
    capabilities: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    person_only: frozenset[str] = frozenset()

    @classmethod
    def from_contract(cls, contract: Mapping[str, Any]) -> "RolePolicy":
        """Read the four keys from ``contract["props"]``; a malformed one raises."""
        props = contract.get("props")
        props = props if isinstance(props, Mapping) else {}
        kind = props.get("output_kind")
        if kind is not None and kind not in OUTPUT_KINDS:
            raise _bad("output_kind")
        acts = props.get("act_policy")
        acts = {} if acts is None else acts
        if (not isinstance(acts, Mapping) or len(acts) > MAX_ENTRIES
                or any(not isinstance(k, str) or not ACTION.fullmatch(k) or v not in ACT_MODES
                       for k, v in acts.items())):
            raise _bad("act_policy")
        budget = props.get("budget")
        budget = {} if budget is None else budget
        if (not isinstance(budget, Mapping)
                or any(k not in BUDGET_INTS + BUDGET_NUMBERS for k in budget)
                or any(not _number(v, integer=k in BUDGET_INTS) for k, v in budget.items())):
            raise _bad("budget")
        caps = props.get("capabilities")
        caps = {} if caps is None else caps
        if (not isinstance(caps, Mapping) or any(k not in CAPABILITY_LISTS for k in caps)
                or any(not isinstance(v, list) or len(v) > MAX_ENTRIES
                       or any(not isinstance(c, str) or not CAPABILITY.fullmatch(c) for c in v)
                       for v in caps.values())):
            raise _bad("capabilities")
        person = props.get("person_only")
        person_ids = frozenset(item["action"] for item in person
                               if isinstance(item, Mapping) and isinstance(item.get("action"), str)
                               ) if isinstance(person, list) else frozenset()
        # 사람만 하는 행동을 정책이 풀면 계약이 스스로 모순이다. 런은 시작하지 않는다.
        if any(acts.get(a) in ("act", "act_and_report") for a in person_ids):
            raise _bad("act_policy for a person_only action")
        return cls(str(contract.get("id") or ""), kind, dict(acts), dict(budget),
                   {k: tuple(v) for k, v in caps.items()}, person_ids)

    def __bool__(self) -> bool:
        return bool(self.output_kind or self.act_policy or self.budget or self.capabilities)

    def act(self, action: str) -> str | None:
        """How the role may take ``action``: ``act``, ``act_and_report``, ``ask_first`` or
        ``never``. A person-only action without its own entry is ``ask_first``; any other
        action the contract does not name is ``None`` (the host decides)."""
        if action in self.act_policy:
            return self.act_policy[action]
        return "ask_first" if action in self.person_only else None

    def capability(self, capability_id: str) -> str | None:
        """``deny``, ``approve`` or ``allow`` for one capability id, in that order of
        precedence; ``None`` when no entry matches. ``github.*`` matches ``github.pr.merge``."""
        for verdict in ("deny", "approve", "allow"):
            for pattern in self.capabilities.get(verdict, ()):
                if capability_id == pattern or (pattern.endswith(".*") and fnmatch.fnmatchcase(
                        capability_id, pattern[:-2] + ".*")):
                    return verdict
        return None

    def limit(self, key: str) -> float | None:
        """One budget limit (``tool_calls``, ``model_calls``, ``tokens``, ``cost_usd`` or
        ``wall_seconds``), or ``None`` when the role sets none."""
        return self.budget.get(key)
