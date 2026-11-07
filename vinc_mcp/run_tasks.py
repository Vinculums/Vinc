# -*- coding: utf-8 -*-
"""실행 프로토콜을 MCP 에 싣는 두 길: Tasks 확장(SEP-2663)과 MRTR 되묻기(SEP-2322). 모던 세대 전용.

실행이 사람 앞에서 대기 상태가 되면 `vinc_run_submit` 의 결과는 클라이언트가 선언한 능력에 따라 갈린다.

- `io.modelcontextprotocol/tasks` 를 선언했으면 `CreateTaskResult` 를 낸다. 작업은 "이 대기 하나"이고
  id 는 `<실행 기록 id>.q<n>` 이다(n 은 그 실행이 사람에게 물은 횟수). 상태는 따로 두지 않는다 -
  `tasks/get` 이 실행 기록 Episode 의 props 에서 매번 계산한다: 대기 중이면 `input_required` 와
  elicitation 하나, 답이 오면 `completed` 와 그 답 뒤의 실행 보기. `tasks/update` 는 답을
  `vinc_run_submit` 의 `answer` 로 옮기고, `tasks/cancel` 은 작업만 닫는다(실행은 그대로 대기).
- elicitation 만 선언했으면 `input_required` 결과를 내고, 클라이언트가 같은 호출에
  `inputResponses` 를 실어 다시 보내면 그 답으로 이어 간다.
- 둘 다 없으면 지금처럼 대기 보기를 그대로 낸다.

사람의 답은 elicitation 의 `accept` 와 `answer: approve|decline` 뿐이다. 사용자가 입력창을 거절하거나
닫은 것은 답이 아니므로 실행은 대기 그대로 남는다.

입력창은 먼저 묻는(`ask_first`) 대기에만 낸다. 사람만 답하는(`person_only`) 대기는 호스트가 답할 수
없으므로 작업은 `working` 으로 남아 사람이 Vinc 앱에서 답할 때까지 기다리고, 되묻기 대신 대기
보기를 그대로 낸다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

from . import modern, run_protocol

EXTENSION = "io.modelcontextprotocol/tasks"
METHODS = ("tasks/get", "tasks/update", "tasks/cancel")
#: 작업이 가리키는 호출. 실행의 다른 도구는 대기 상태를 만들지 못한다.
SUBMIT_TOOL = "vinc_run_submit"
#: 한 작업에 질문은 하나라 키는 고정이다(작업 수명 안에서 유일하다는 조건을 그대로 만족한다).
ASK_KEY = "decision"
POLL_MS = 5000
TASK_SEP = ".q"
#: 사람만 답하는 대기의 작업 상태 문장. 호스트는 답하지 못하고 기다린다.
PERSON_WAIT = ("Waiting for a person to answer in " + run_protocol.PERSON_PATH
               + ". The task completes when they answer.")


class TaskError(ValueError):
    """없거나 읽을 수 없는 작업. 디스패처가 -32602 로 낸다."""


def client_capabilities(params) -> dict:
    meta = params.get("_meta") if isinstance(params, dict) else None
    caps = meta.get(modern.META_CAPABILITIES) if isinstance(meta, dict) else None
    return caps if isinstance(caps, dict) else {}


def tasks_declared(caps: dict) -> bool:
    ext = caps.get("extensions")
    return isinstance(ext, dict) and isinstance(ext.get(EXTENSION), dict)


def elicits(caps: dict) -> bool:
    """폼 되묻기를 받을 수 있는가(`elicitation: {}` 는 폼만 선언한 것이다)."""
    declared = caps.get("elicitation")
    return isinstance(declared, dict) and (not declared or "form" in declared)


def task_id(eid: str, ask: int) -> str:
    return f"{eid}{TASK_SEP}{ask}"


def parse_task_id(text):
    """`(실행 기록 id, 질문 번호)`. 모양이 아니면 None."""
    if not isinstance(text, str):
        return None
    eid, sep, tail = text.rpartition(TASK_SEP)
    if not sep or not eid.startswith(run_protocol.RUN_PREFIX) or not tail.isdigit() or int(tail) < 1:
        return None
    return eid, int(tail)


def input_requests(question: str) -> dict:
    """사람에게 묻는 elicitation 하나. 답은 `approve` 와 `decline` 중 하나다."""
    return {ASK_KEY: {"method": "elicitation/create", "params": {
        "mode": "form",
        "message": question or "A person decides before the role goes on.",
        "requestedSchema": {
            "type": "object",
            "properties": {"answer": {
                "type": "string", "enum": list(run_protocol.ANSWERS), "title": "Decision",
                "description": "approve lets the role take the listed actions; decline ends "
                               "the run as blocked."}},
            "required": ["answer"]}}}}


def answer_of(responses):
    """`inputResponses` 에서 사람의 답. 입력창을 거절·닫았거나 값이 틀리면 None."""
    got = responses.get(ASK_KEY) if isinstance(responses, dict) else None
    if not isinstance(got, dict) or got.get("action") != "accept":
        return None
    content = got.get("content")
    answer = content.get("answer") if isinstance(content, dict) else None
    return answer if answer in run_protocol.ANSWERS else None


def _task(props: dict, eid: str, ask: int, status: str, **extra) -> dict:
    asked = str(props.get("last_asked_at") or props.get("started_at") or "")
    return {"taskId": task_id(eid, ask), "status": status, "createdAt": asked,
            "lastUpdatedAt": str(props.get("answered_at") or asked), "ttlMs": None,
            "pollIntervalMs": POLL_MS, **extra}


def carry(result, params, args):
    """대기에 들어선 `vinc_run_submit` 결과를 클라이언트 능력에 맞는 모양으로. 해당 없으면 None."""
    if (not isinstance(result, dict) or not result.get("executed") or result.get("state") != "waiting"
            or not result.get("ask") or args.get("space")):
        return None
    caps = client_capabilities(params)
    question = str(result.get("question") or "")
    person = (result.get("decision") or {}).get("kind") == "person_only"
    if tasks_declared(caps):
        now = str(result.get("asked_at") or "")
        return {"resultType": "task", "taskId": task_id(result["run"], int(result["ask"])),
                "status": "working" if person else "input_required",
                "statusMessage": PERSON_WAIT if person else question, "createdAt": now,
                "lastUpdatedAt": now, "ttlMs": None, "pollIntervalMs": POLL_MS}
    if person:
        return None
    if elicits(caps):
        return modern.input_required(input_requests(question), client_capabilities=caps)
    return None


def _read(bridge, owner: str, tid) -> tuple[str, int, dict]:
    parsed = parse_task_id(tid)
    if parsed is None:
        raise TaskError("Failed to retrieve task: Task not found")
    eid, ask = parsed
    props = run_protocol._load(bridge, owner, eid)
    if props is not None and "_error" in props:
        raise RuntimeError(str(props["_error"].get("error") or "run could not be read"))
    if props is None or ask > int(props.get("asks") or 0):
        raise TaskError("Failed to retrieve task: Task not found")
    return eid, ask, props


def _current(props: dict, ask: int) -> bool:
    return props.get("state") == "waiting" and int(props.get("asks") or 0) == ask


def get(bridge, owner: str, params: dict, wrap) -> dict:
    """`tasks/get`. `wrap` 는 실행 보기를 도구 결과 봉투로 바꾼다."""
    eid, ask, props = _read(bridge, owner, params.get("taskId"))
    if _current(props, ask):
        if props.get("task_cancelled") == ask:
            return _task(props, eid, ask, "cancelled", statusMessage="The task was cancelled; the "
                         "run still waits for a person.")
        pending = props.get("pending") or {}
        if run_protocol.decision_kind(pending) == "person_only":
            return _task(props, eid, ask, "working", statusMessage=PERSON_WAIT)
        question = str(pending.get("question") or "")
        return _task(props, eid, ask, "input_required", statusMessage=question,
                     inputRequests=input_requests(question))
    view = run_protocol._view(props, eid)
    return _task(props, eid, ask, "completed", statusMessage=f"The run is {props.get('state')}.",
                 result=wrap(view))


def update(bridge, owner: str, params: dict) -> dict:
    """`tasks/update`. 사람의 답이면 `vinc_run_submit` 의 answer 로 옮긴다. 아니면 확인만 한다."""
    eid, ask, props = _read(bridge, owner, params.get("taskId"))
    answer = answer_of(params.get("inputResponses"))
    if answer and _current(props, ask) and props.get("task_cancelled") != ask:
        out = run_protocol.submit({"run": eid, "answer": answer}, bridge, owner)
        if out.get("error") == "person_answer_required":
            raise ValueError(str(out.get("note")))
        if not out.get("executed"):
            raise RuntimeError(str(out.get("error") or out.get("note") or "answer was not applied"))
    return {}


def cancel(bridge, owner: str, params: dict) -> dict:
    """`tasks/cancel`. 작업만 닫는다: 취소는 사람의 결정이 아니라서 실행의 상태는 건드리지 않는다."""
    eid, ask, props = _read(bridge, owner, params.get("taskId"))
    if _current(props, ask) and props.get("task_cancelled") != ask:
        props["task_cancelled"] = ask
        failed = run_protocol._saved(bridge, owner, eid, props)
        if failed:
            raise RuntimeError(str(failed.get("error") or "task was not cancelled"))
    return {}
