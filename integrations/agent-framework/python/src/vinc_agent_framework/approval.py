"""A native workflow approval executor, downstream of the model executor."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from agent_framework import Executor, WorkflowContext, handler, response_handler
from vinc_client.approval import (ApprovalDecision, ApprovalRequest, ApprovalResult, RoleOutput,
                                  prepare_person_only, resolve_person_only)
from vinc_client.output_gate import OutputGate


class VincPersonOnlyExecutor(Executor):
    """Connect model -> this executor and resume via workflow.run(responses=...).

    The upstream model emits RoleOutput. Blocked output raises before a request;
    pending person actions emit the framework's native request_info event. The
    response handler uses the saved request and never calls the upstream model.
    The host authenticates human responses; approval does not execute any tool.
    Keep unchecked model stream events out of the user-facing result stream.
    """
    def __init__(self, gate: OutputGate, *, role_id: str, run_id: str,
                 evidence: Callable[[RoleOutput], Mapping[str, Any]] | None = None,
                 id: str = "vinc_person_only") -> None:
        super().__init__(id=id)
        self.gate, self.role_id, self.run_id, self.evidence = gate, role_id, run_id, evidence

    @handler
    async def check_output(self, output: RoleOutput,
                           ctx: WorkflowContext[None, ApprovalResult]) -> None:
        checked = prepare_person_only(output, self.gate, role_id=self.role_id, run_id=self.run_id,
                                      evidence=self.evidence(output) if self.evidence else None)
        if isinstance(checked, ApprovalRequest):
            await ctx.request_info(request_data=checked, response_type=ApprovalDecision)
        else:
            await ctx.yield_output(checked)

    @response_handler
    async def on_approval(self, original_request: ApprovalRequest, response: ApprovalDecision,
                          ctx: WorkflowContext[None, ApprovalResult]) -> None:
        # 승인 응답은 저장된 초안만 해제한다. 모델 호출이나 도구 실행은 없다.
        await ctx.yield_output(resolve_person_only(original_request, response))
