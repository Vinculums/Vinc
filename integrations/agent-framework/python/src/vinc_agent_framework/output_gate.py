"""Checks the final Agent Framework response before it leaves the agent run."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from agent_framework import AgentContext, AgentMiddleware, AgentResponse, Message
from vinc_client.output_gate import GateResult, OutputGate, OutputGateStopped


class VincOutputGateMiddleware(AgentMiddleware):
    """Non-streaming final-output gate; it does not execute or authorize tools.

    Supply evidence through a trusted host callback scoped to this invocation.
    Do not accept proof dictionaries from model messages. A blocked result raises
    OutputGateStopped, so the response cannot look like success. A result that
    needs a person's decision (interrupt) is delivered with that decision named
    under ``PENDING_HEADER`` (``OutputGate.deliver``), because the person reading it
    is the one who decides; ``on_interrupt="stop"`` raises instead, as before.
    Configure the outermost middleware when another middleware changes output.
    Streaming is rejected before calling the agent: no unchecked tokens escape.
    """
    def __init__(self, gate: OutputGate, *,
                 evidence: Callable[[AgentContext], Mapping[str, Any]] | None = None,
                 on_interrupt: str = "deliver") -> None:
        if on_interrupt not in {"deliver", "stop"}:
            raise ValueError("on_interrupt is 'deliver' or 'stop'")
        self.gate = gate
        self.evidence = evidence
        self.on_interrupt = on_interrupt

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
        if self.on_interrupt == "stop":
            checked = self.gate.require_pass(result.text, **facts)
        else:
            delivery = self.gate.deliver(result.text, **facts)
            checked = delivery.result
            if delivery.text != result.text:
                # 마지막 메시지만 결정 목록을 붙인 글로 바꾼다. 나머지 응답 칸은 그대로 둔다.
                context.result = AgentResponse(
                    messages=[*result.messages[:-1], Message("assistant", [delivery.text])],
                    response_id=result.response_id, agent_id=result.agent_id,
                    created_at=result.created_at, finish_reason=result.finish_reason,
                    usage_details=result.usage_details, value=result.value,
                    additional_properties=result.additional_properties)
        context.metadata["vinc_output_gate"] = checked.as_dict()
