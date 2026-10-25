"""The deterministic output gate as a CrewAI task guardrail."""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from crewai import TaskOutput

from vinc.sdk.output_gate import OutputGate


def _requests(output: TaskOutput) -> Any:
    # 모델이 낸 사람 전용 요청. 판정을 더 엄하게만 만드는 모델 데이터이며 실행 증거가 아니다.
    data: Any = None
    if output.pydantic is not None:
        data = output.pydantic.model_dump()
    elif output.json_dict is not None:
        data = output.json_dict
    elif isinstance(output.raw, str) and output.raw.lstrip().startswith("{"):
        try:
            data = json.loads(output.raw)
        except ValueError:
            data = None
    if isinstance(data, Mapping) and "person_only_requests" in data:
        return data["person_only_requests"]
    return None


def create_output_guardrail(gate: OutputGate, *,
                            evidence: Callable[[TaskOutput], Mapping[str, Any]] | None = None
                            ) -> Callable[[TaskOutput], tuple[bool, Any]]:
    """A ``Task(guardrail=...)`` that lets a task's output out only when ``gate`` passes it.

    A blocked output, or one that needs a person's decision, raises ``OutputGateStopped``
    instead of returning ``(False, reason)``: CrewAI would send the reason back to the model
    and retry, and the gate is a barrier, not feedback. The guardrail runs after the agent
    has finished, outside its retry loop, so the exception reaches the caller of
    ``crew.kickoff()``. A passing output is returned unchanged.

    ``evidence`` is a host callback scoped to this run that returns ``OutputGate.evaluate``
    keyword arguments (``proofs`` built from calls the host observed, and so on); never
    build proofs from model text. To let a person approve person-only actions instead of
    stopping, use :class:`VincPersonOnlyInput` on that task in place of this guardrail.
    """
    # 안쪽 함수에는 반환 주석을 달지 않는다. CrewAI 가 서명을 검사하는데 이 모듈의 주석은 문자열이라
    # tuple[bool, Any] 로 읽히지 않고 거절된다.
    def guardrail(output):  # type: ignore[no-untyped-def]
        facts = dict(evidence(output)) if evidence else {}
        requests = _requests(output)
        if requests is not None:
            facts["person_only_requests"] = requests
        gate.require_pass(output.raw, **facts)
        return True, output

    return guardrail
