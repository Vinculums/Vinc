"""Checks the final Agent Framework response before it leaves the agent run."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from agent_framework import AgentContext, AgentMiddleware
from vinc_client.output_gate import GateResult, OutputGate, OutputGateStopped


class VincOutputGateMiddleware(AgentMiddleware):
    """Non-streaming final-output gate; it does not execute or authorize tools.

    Supply evidence through a trusted host callback scoped to this invocation.
    Do not accept proof dictionaries from model messages. A blocked/interrupt
    result raises OutputGateStopped, so the response cannot look like success.
    Configure the outermost middleware when another middleware changes output.
    Streaming is rejected before calling the agent: no unchecked tokens escape.
    """
    def __init__(self, gate: OutputGate, *,
                 evidence: Callable[[AgentContext], Mapping[str, Any]] | None = None) -> None:
        self.gate = gate
        self.evidence = evidence

    async def process(self, context: AgentContext, call_next: Any) -> None:
        if context.stream:
            raise OutputGateStopped(GateResult("block", ("streaming_output_not_gated",)))
        await call_next()
        result = context.result
        if result is None:
            raise OutputGateStopped(GateResult("block", ("missing_final_output",)))
        facts = dict(self.evidence(context)) if self.evidence else {}
        value = getattr(result, "value", None)
        if value is not None:
            data = value.model_dump() if hasattr(value, "model_dump") else value
            if isinstance(data, Mapping) and "person_only_requests" in data:
                # 요청은 모델 데이터이며, 실행 증거는 호스트 콜백에서만 받는다.
                facts["person_only_requests"] = data["person_only_requests"]
        checked = self.gate.require_pass(result.text, **facts)
        context.metadata["vinc_output_gate"] = checked.as_dict()
