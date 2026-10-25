"""Person-only actions raised to CrewAI's human input step (``Task(human_input=True)``)."""
from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping

from crewai.core.providers.human_input import get_provider, reset_provider, set_provider

from vinc.sdk.approval import (ApprovalRequest, ApprovalResult, prepare_person_only,
                               resolve_person_only)
from vinc.sdk.output_gate import OutputGate

from .agent import current_run


class VincApprovalRejected(RuntimeError):
    """A person rejected the draft. ``result`` has status ``rejected`` and no output."""

    def __init__(self, result: ApprovalResult) -> None:
        self.result = result
        super().__init__("A person rejected the draft; nothing was released.")


class VincPersonOnlyInput:
    """CrewAI's human input step for ``tasks``, answered through ``vinc.sdk.approval``.

    Give each task ``human_input=True`` and the role's output schema
    (``response_model=RoleOutput``, or a final answer that is ``RoleOutput`` JSON), then
    run the crew inside ``with approval.installed():``. When the agent finishes a bound
    task, the step checks the draft with ``prepare_person_only``:

    - the gate blocks it: ``OutputGateStopped`` is raised and no one is asked;
    - it passes: the draft is released as the task output and no one is asked;
    - it needs a person: ``decide(request)`` receives the ``ApprovalRequest`` and returns
      the person's ``ApprovalDecision`` (from your human channel, never from a model).
      Approved releases the saved draft unchanged; rejected raises
      :class:`VincApprovalRejected`.

    The step never calls the model and never runs a tool: an approval releases the draft
    only. A stop is final for that task in this run. CrewAI retries an agent whose run
    raised; an agent from ``create_agent`` answers that retry with the same stop without
    calling the model, and any other agent's retry gets the same stop here without anyone
    being asked again. Tasks not bound here keep CrewAI's own feedback prompt.

    ``evidence(task)`` is a read-only host callback returning ``OutputGate.evaluate``
    keyword arguments for that task's run. ``result(task)`` returns the released result.
    """

    def __init__(self, gate: OutputGate, *, role_id: str, run_id: str,
                 decide: Callable[[ApprovalRequest], Any], tasks: Iterable[Any],
                 evidence: Callable[[Any], Mapping[str, Any]] | None = None) -> None:
        if (not isinstance(role_id, str) or not role_id.startswith("concept:agent-")
                or not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256):
            raise ValueError("Invalid approval scope")
        self.gate, self.role_id, self.run_id = gate, role_id, run_id
        self.decide, self.evidence = decide, evidence
        self._tasks = list(tasks)
        if not self._tasks:
            raise ValueError("bind at least one task")
        self._ids = {str(task.id) for task in self._tasks}
        self._results: dict[str, ApprovalResult] = {}
        self._stops: dict[str, BaseException] = {}
        self._previous: Any = None

    def result(self, task: Any) -> ApprovalResult | None:
        """The pass or approval that released ``task``'s draft in this run, if any."""
        return self._results.get(str(task.id))

    @contextmanager
    def installed(self) -> Iterator["VincPersonOnlyInput"]:
        """Make this CrewAI's human input provider in this context, for the crew run."""
        # human_input 이 꺼진 태스크는 이 단계를 지나지 않으므로 초안이 검사 없이 나간다. 시작 전에 멈춘다.
        unchecked = [task for task in self._tasks if not getattr(task, "human_input", False)]
        if unchecked:
            raise ValueError("every bound task needs human_input=True")
        if self._previous is not None:
            raise RuntimeError("this approval step is already installed")
        self._previous = get_provider()
        token = set_provider(self)
        try:
            yield self
        finally:
            reset_provider(token)
            self._previous = None

    def _bound(self, context: Any) -> Any:
        task = getattr(context, "task", None)
        return task if task is not None and str(task.id) in self._ids else None

    def _stop(self, task: Any, exc: BaseException) -> BaseException:
        self._stops[str(task.id)] = exc
        run = current_run()
        if run is not None and run.task is task:
            run.stopped = exc
        return exc

    def _prepare(self, task: Any, output: Any) -> ApprovalRequest | ApprovalResult:
        stopped = self._stops.get(str(task.id))
        if stopped is not None:
            raise stopped
        return prepare_person_only(output, self.gate, role_id=self.role_id, run_id=self.run_id,
                                   evidence=self.evidence(task) if self.evidence else None)

    def _keep(self, task: Any, result: ApprovalResult) -> None:
        if result.status == "rejected":
            raise VincApprovalRejected(result)
        self._results[str(task.id)] = result

    # ── CrewAI HumanInputProvider ────────────────────────────────────────────

    def setup_messages(self, context: Any) -> bool:
        return self._previous.setup_messages(context)

    def post_setup_messages(self, context: Any) -> None:
        self._previous.post_setup_messages(context)

    def handle_feedback(self, formatted_answer: Any, context: Any) -> Any:
        task = self._bound(context)
        if task is None:
            return self._previous.handle_feedback(formatted_answer, context)
        try:
            checked = self._prepare(task, formatted_answer.output)
            if isinstance(checked, ApprovalRequest):
                decision = self.decide(checked)
                if inspect.isawaitable(decision):
                    close = getattr(decision, "close", None)
                    if close:
                        close()
                    raise TypeError("decide returned an awaitable; use crew.akickoff for an async decide")
                checked = resolve_person_only(checked, decision)
            self._keep(task, checked)
        except Exception as exc:
            raise self._stop(task, exc) from None
        # 승인은 저장된 초안만 내보낸다. 모델 호출도 도구 실행도 없다.
        return formatted_answer

    async def handle_feedback_async(self, formatted_answer: Any, context: Any) -> Any:
        task = self._bound(context)
        if task is None:
            return await self._previous.handle_feedback_async(formatted_answer, context)
        try:
            checked = self._prepare(task, formatted_answer.output)
            if isinstance(checked, ApprovalRequest):
                decision = self.decide(checked)
                if inspect.isawaitable(decision):
                    decision = await decision
                checked = resolve_person_only(checked, decision)
            self._keep(task, checked)
        except Exception as exc:
            raise self._stop(task, exc) from None
        return formatted_answer
