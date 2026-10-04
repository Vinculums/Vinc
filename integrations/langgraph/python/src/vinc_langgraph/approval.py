"""An approval node to place strictly after a completed model node."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from langgraph.types import interrupt
from vinc_client.approval import ApprovalRequest, prepare_person_only, resolve_person_only
from vinc_client.output_gate import OutputGate


def create_person_only_node(gate: OutputGate, *, role_id: str, run_id: str,
                           evidence: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
    """Read state['role_output']; return only the checked 'vinc_approval' result.

    Use a checkpointer and the same thread_id on resume. Do not call this helper
    inside a model node: LangGraph restarts the interrupted node. The evidence
    callback must be read-only host evidence; model state cannot provide proofs.
    Never deliver unchecked model stream events or expose the draft as success.
    Approval releases the draft only; tool execution needs separate authorization.
    """
    def approve(state: Mapping[str, Any]) -> dict[str, Any]:
        checked = prepare_person_only(state.get("role_output"), gate, role_id=role_id,
                                      run_id=run_id, evidence=evidence(state) if evidence else None)
        if isinstance(checked, ApprovalRequest):
            # 재개 시 반복되는 것은 이 승인 노드뿐이다. 앞선 모델 홉은 완료 상태다.
            decision = interrupt(checked.model_dump())
            checked = resolve_person_only(checked, decision)
        return {"vinc_approval": checked.as_dict()}
    return approve
