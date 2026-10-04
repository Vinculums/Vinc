"""Output gate middleware and an explicit post-model LangGraph node."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from vinc_client.output_gate import GateResult, OutputGate, OutputGateStopped


def evaluate_output_node(state: Mapping[str, Any], gate: OutputGate, *,
                         evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run after the model node; route gate.verdict to pass/block/approval.

    This node never calls a model or interrupts inside a model hop. Evidence must
    be supplied by the host; the function deliberately ignores state['proofs'].
    The returned object contains decisions, not the original model output.
    """
    messages = state.get("messages") or []
    if not messages:
        return {"vinc_output_gate": GateResult("block", ("missing_final_output",)).as_dict()}
    last = messages[-1]
    if getattr(last, "tool_calls", None):
        return {"vinc_output_gate": GateResult("block", ("pending_tool_calls",)).as_dict()}
    text = getattr(last, "content", None)
    if not isinstance(text, str):
        return {"vinc_output_gate": GateResult("block", ("unsupported_final_output",)).as_dict()}
    facts = dict(evidence or {})
    review = state.get("structured_response")
    if review is not None:
        data = review.model_dump() if hasattr(review, "model_dump") else review
        if isinstance(data, Mapping) and "person_only_requests" in data:
            facts["person_only_requests"] = data["person_only_requests"]
    return {"vinc_output_gate": gate.evaluate(text, **facts).as_dict()}


def require_output_pass(state: Mapping[str, Any], gate: OutputGate, *,
                        evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Post-model node that raises when an output needs blocking or approval."""
    update = evaluate_output_node(state, gate, evidence=evidence)
    result = update["vinc_output_gate"]
    if result["verdict"] != "pass":
        raise OutputGateStopped(GateResult(result["verdict"], tuple(result["failed_checks"]),
                                           tuple(result["person_only_actions"]),
                                           tuple(result["evidence_sentences"])))
    return update


def create_output_gate_middleware(gate: OutputGate, *,
                                 evidence: Callable[[Any, Any], Mapping[str, Any]] | None = None) -> Any:
    """Create LangChain middleware (requires the agents extra).

    Final output is checked in after_model, before the agent returns. Intermediate
    AI messages with tool calls continue to the tool loop. This is an output
    barrier, not a pre-execution tool authorization gate. Use invoke/ainvoke;
    do not expose unchecked model stream events to an end user.
    """
    from langchain.agents.middleware import AgentMiddleware

    class VincOutputGateMiddleware(AgentMiddleware):
        def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            messages = state.get("messages") or []
            if messages and getattr(messages[-1], "tool_calls", None):
                return None
            # 상태 스키마에 없는 메타데이터는 반환하지 않는다. 판정으로 분기할 때는
            # 명시적 노드 헬퍼를 쓰고, 미들웨어는 통과한 응답만 돌려준다.
            require_output_pass(state, gate, evidence=evidence(state, runtime) if evidence else None)
            return None

        async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
            return self.after_model(state, runtime)

    return VincOutputGateMiddleware()
