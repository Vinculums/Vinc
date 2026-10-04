"""Post-model approval data. Approval releases a reviewed output, never a tool."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from .output_gate import GateResult, OutputGate, OutputGateStopped
from .review import PersonOnlyRequest

Text = Annotated[str, Field(strict=True, min_length=1, max_length=131072, pattern=r"\S")]
RequestId = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class RoleOutput(BaseModel):
    """Use as the model response schema; requests are proposals, not approvals."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    text: Text
    person_only_requests: Annotated[list[PersonOnlyRequest], Field(max_length=100)] = Field(
        default_factory=list)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    request_id: RequestId
    role_id: Text
    run_id: Text
    output: RoleOutput
    actions: Annotated[list[Text], Field(min_length=1, max_length=100)]


class ApprovalDecision(BaseModel):
    """Construct only from the human channel, never from a model result."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    request_id: RequestId
    approved: StrictBool


@dataclass(frozen=True)
class ApprovalResult:
    status: str
    output: RoleOutput | None
    actions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status,
                "output": self.output.model_dump() if self.output is not None else None,
                "actions": list(self.actions)}


def _validated(value: Any, schema: type[BaseModel]) -> BaseModel:
    if isinstance(value, BaseModel):
        value = value.model_dump(warnings=False)
    if isinstance(value, str):
        if len(value) > 262144:
            raise ValueError("Input exceeds approval data limit")
        value = json.loads(value)
    return schema.model_validate(value)


def _request_id(role_id: str, run_id: str, output: RoleOutput, actions: list[str]) -> str:
    data = {"role_id": role_id, "run_id": run_id,
            "output": output.model_dump(), "actions": actions}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def prepare_person_only(output: Any, gate: OutputGate, *, role_id: str, run_id: str,
                        evidence: Mapping[str, Any] | None = None) -> ApprovalRequest | ApprovalResult:
    """Check once the model hop has completed. No model or interrupt is called here.

    role_id/run_id and evidence are host values scoped to this run. The host must
    fetch the current role contract and keep all side-effect tools disabled until
    their own authorization step. A human cannot override a blocked output here.
    """
    if (not isinstance(role_id, str) or not re.fullmatch(r"concept:agent-[a-z0-9-]+", role_id)
            or not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256):
        raise ValueError("Invalid approval scope")
    try:
        parsed = _validated(output, RoleOutput)
    except (ValueError, TypeError, ValidationError):
        raise OutputGateStopped(GateResult("block", ("invalid_role_output",))) from None
    facts = dict(evidence or {})
    # 이 필드는 모델의 제안이다. 실행 증거나 승인으로 승격하지 않는다.
    facts["person_only_requests"] = [r.model_dump() for r in parsed.person_only_requests]
    checked = gate.evaluate(parsed.text, **facts)
    if checked.verdict == "block":
        raise OutputGateStopped(checked)
    if checked.verdict == "pass":
        return ApprovalResult("pass", parsed)
    actions = list(checked.person_only_actions)
    if not actions:
        raise OutputGateStopped(GateResult("block", ("unresolvable_approval",)))
    return ApprovalRequest(request_id=_request_id(role_id, run_id, parsed, actions),
                           role_id=role_id, run_id=run_id, output=parsed, actions=actions)


def resolve_person_only(request: ApprovalRequest, decision: Any) -> ApprovalResult:
    """Resume the saved request without invoking a model or executing an action.

    The calling application authenticates the human and controls who can submit
    this response. The matching hash binds it to this exact draft and run; it is
    not an authentication token or a grant for any side-effect tool.
    """
    try:
        saved = _validated(request, ApprovalRequest)
        reply = _validated(decision, ApprovalDecision)
    except (ValueError, TypeError, ValidationError):
        raise ValueError("Invalid approval response") from None
    actual = _request_id(saved.role_id, saved.run_id, saved.output, saved.actions)
    if saved.request_id != actual or reply.request_id != actual:
        raise ValueError("Approval response does not match the saved request")
    return ApprovalResult("approved" if reply.approved else "rejected",
                          saved.output if reply.approved else None, tuple(saved.actions))
