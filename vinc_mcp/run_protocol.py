# -*- coding: utf-8 -*-
"""실행 프로토콜: 역할 하나를 Vinc 가 단계마다 붙잡고 돌린다. 모델은 호스트에 있다.

`vinc_run_start` 가 실행을 열고, 호스트의 모델이 한 단계를 하면 `vinc_run_submit` 으로 낸다.
Vinc 는 그 산출물에 역할의 게이트(`OutputGate`)를 돌리고, 사람만 하는 행동이나 먼저 묻는 행동
앞에서 실행을 대기 상태로 세운다. 답(approve·decline)이 오면 이어 가거나 막힌 채 끝낸다.
끝나면 실행 기록이 남는다. 상태는 따로 두지 않는다 - 실행 기록 Episode(`episode:run-*`)의 props
가 곧 상태다: `outcome` 은 끝날 때까지 `waiting`, `state` 가 working·waiting·done 을 가른다.

`outcome` 은 프로토콜이 실행을 어떻게 끝냈는가다(게이트 통과 success, 예산·만료·거절 blocked,
abandoned). 일이 무엇으로 끝났는가는 에이전트가 출력과 함께 보고하는 `verdict` 가 따로 싣는다.
사람이 읽는 낱말(제목의 꼬리)은 둘을 함께 보는 `reading` 이 정한다.

한계: 호스트가 낸 문장은 증거가 아니다. 무언가를 했다는 주장은 `role_check` 와 같이 게이트에서
막힌다(증거는 호스트가 관측한 도구 호출에서만 온다). 산출물 원문은 그래프에 넣지 않고 해시와
길이만 남긴다.

대기에는 종류가 있다. `person_only`(역할이 사람만 한다고 적은 행동, 또는 게이트의 끼어들기)는
사람의 로그인 세션만 답한다: `vinc_run_submit` 의 answer 는 에이전트 자격(MCP OAuth·멤버 키·
범위 키)으로 오므로 거절하고, 사람은 계정 백엔드의 서명된 세션 프록시를 거쳐 `person_answer` 로
답한다. `ask_first`(먼저 묻기)는 호스트가 사람에게 물어 그 답을 `vinc_run_submit` 으로 내도 된다.
요청은 만료된다(기본 1시간, 최대 12시간). 만료된 요청에는 누구도 답할 수 없고 실행은 막힌 채
끝난다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import threading
import uuid
import zlib

from store import run_state as _run_rule

from . import role_output_gate, run_notify
from .capability_maps import capability_verdict
from .roles import contract

ROLE_PREFIX = "concept:agent-"
#: 실행 기록 id 의 접두사와 프로토콜 상태 필드는 `store/run_state.py` 한 벌을 쓴다.
RUN_PREFIX = _run_rule.RUN_PREFIX
#: 이 흐름의 실행 기록 토픽(D8). 도메인은 `vinc/` 아래 둔다.
DOMAIN = "vinc/agent/harness"
STATES = ("working", "waiting", "done")
#: 열린 실행의 outcome. 끝나기 전에는 아무 결론도 나지 않았다.
OPEN_OUTCOME = "waiting"
ANSWERS = ("approve", "decline")
#: 사람이 세션으로 답할 때의 답. reply 는 행동을 승인하지 않고 글로 답해 실행을 이어 가게 한다.
PERSON_ANSWERS = ("approve", "decline", "reply")
ANSWER_TEXT_MAX = 2000
#: 대기의 종류. person_only 는 사람 세션만, ask_first 는 호스트가 사람에게 물어 답해도 된다.
DECISION_KINDS = ("person_only", "ask_first")
#: 답이 어느 길로 왔는가. person 은 서명된 세션 프록시, agent 는 도구 호출이다.
ANSWER_VIA = ("person", "agent")
#: 사람에게 묻는 요청의 수명(초). 호스트가 `expires_in_s` 로 고르고, 안 주면 기본값이다.
EXPIRES_DEFAULT_S = 3600
EXPIRES_MIN_S = 60
EXPIRES_MAX_S = _run_rule.EXPIRES_MAX_S
#: 호스트가 적은 행동 설명(person_only_requests 의 reason)을 요청에 남기는 상한.
REASON_MAX = 2000
#: 사람만 답하는 대기를 에이전트가 답하려 할 때 알려 주는 사람의 길.
PERSON_PATH = "the Approvals screen of the Vinc app (https://vincs.io/app/), signed in"
#: 실행 기록의 프로토콜 상태 필드. 도구 쓰기(`vinc_ingest`)로는 바꾸지 못하고 동기화로는 건너가지 않는다.
STATE_KEYS = _run_rule.STATE_KEYS
#: 그중 답 쪽(`_answer` 가 채우는 키). 믿지 않는 동기화는 처음 보는 실행에서 이것을 버린다.
ANSWER_KEYS = _run_rule.ANSWER_KEYS
#: 일이 내린 결론(`verdict`)의 낱말과 한 줄의 상한. 정본은 `store/run_state.py` 다.
VERDICTS = _run_rule.VERDICTS
VERDICT_NOTE_MAX = _run_rule.VERDICT_NOTE_MAX
#: 게이트만 통과하고 끝난 실행의 outcome. 결론을 보고한 실행의 제목에는 이 낱말 대신 결론이 선다.
SUCCESS = "success"
TASK_MAX = 4000
SUBJECT_MAX = 500
#: 실행한 호스트 이름(예 "codex-cli 0.162.1"). 실행 기록의 host 가 된다.
HOST_MAX = 120
#: 실행 기록 제목의 최대 길이. 가장 긴 정상 제목(실행 id 128자와 긴 역할 이름)이 잘리지 않게 넉넉히 둔다.
TITLE_CHARS = 1024
OUTPUT_MAX = 131072
CITED_MAX = 200
#: 역할 props.cites 가 이 값이면 출력 단계마다 그래프에 실재하는 근거 id 가 있어야 한다.
#: 출력 게이트의 검사 종류로 두지 않는다: 배포된 SDK 가 모르는 검사를 만나면 계약째 거절한다.
CITES_REQUIRED = "required"
#: `/node-edges` 한 번에 묻는 id 수의 상한(사이드카 계약).
_EXISTS_BATCH = 100
#: 호스트가 보고하는 사용량. 합산해 역할의 budget 과 견준다.
USAGE_KEYS = ("tool_calls", "tokens_in", "tokens_out", "cost_usd")
#: 호스트가 관측한 능력 id 의 문법(역할 정책과 같다, 와일드카드는 없다). 한 번에·실행 전체에 담는 상한.
_CAPABILITY_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)*")
CAPABILITIES_PER_STEP = 64
CAPABILITIES_MAX = 200
#: 능력 감사의 표지. denied 는 역할이 막은 것, outside_allow 는 허용 목록 밖, unapproved 는 승인 없이 쓴 것.
CAPABILITY_FLAGS = ("denied", "outside_allow", "unapproved")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
#: 완료 주장 대신 통과하는 말. MCP 제출에는 호스트가 관측한 증거(ToolProof)가 닿는 통로가 없어서
#: "했다"는 문장은 늘 막힌다. 실행 계약(next)과 막힌 결과가 같은 문구를 쓴다.
RESTATE = ("what you attempted and where its result can be read (a PR, commit or graph id), not "
           "as success: no host proof reaches this tool")
#: 완료 주장을 보는 검사. 막힌 결과에 그 문장(claim)과 고쳐 쓰는 법을 싣는다.
_CLAIM_CHECK = "side_effect_evidence"


def episode_id(run_id: str) -> str:
    """`vinc_client.run.run_episode_id` 와 같은 id(parity 시험이 고정한다). 같은 run_id 는 같은 노드다."""
    slug = re.sub(r"[^a-z0-9]+", "-", run_id.lower()).strip("-")[:48].strip("-") or "run"
    return f"{RUN_PREFIX}{slug}-{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:8]}"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(text) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(str(text or ""), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def expires_at(pending: dict) -> str:
    """요청의 만료 시각. 만료 필드가 없는 기록은 물은 때부터 기본 수명으로 센다."""
    if pending.get("expires_at"):
        return str(pending["expires_at"])
    asked = _parse_stamp(pending.get("asked_at"))
    return _stamp(asked + datetime.timedelta(seconds=EXPIRES_DEFAULT_S)) if asked else ""


def expired(pending: dict, now: datetime.datetime | None = None) -> bool:
    until = _parse_stamp(expires_at(pending))
    return until is not None and (now or _now()) >= until


#: 요청이 만료돼 끝난 실행의 사유. 읽기가 보이는 것과 다음 제출·답이 저장하는 것이 같은 문장이다.
EXPIRED_REASON = "the request expired before a person answered"


def as_read(props: dict, now: datetime.datetime | None = None) -> dict:
    """읽는 쪽이 보는 실행 상태. 요청이 만료된 대기는 저장된 행이 아직 waiting 이어도 막힌 채
    끝난 것으로 읽는다(`_expire` 가 저장할 모양과 같다). 쓰지 않는다: 저장된 행은 다음 제출·답이
    `_expire` 로 닫는다. 시각은 만료 시각이라 읽을 때마다 달라지지 않는다. 그 밖의 실행은 그대로."""
    if not isinstance(props, dict) or props.get("state") != "waiting":
        return props
    pending = props.get("pending") if isinstance(props.get("pending"), dict) else {}
    if not expired(pending, now):
        return props
    at = expires_at(pending)
    out = {k: v for k, v in props.items() if k != "pending"}
    out.update(outcome="blocked", state="done", ended_at=at, end_reason=EXPIRED_REASON,
               expired_at=at)
    return out


def decision_kind(pending: dict, policy: dict | None = None) -> str:
    """대기의 종류. 종류 필드가 없는 기록은 역할의 person_only 와 견주고, 모르면 person_only 다."""
    kind = pending.get("kind")
    if kind in DECISION_KINDS:
        return kind
    actions = [a for a in pending.get("actions") or [] if isinstance(a, str)]
    if policy is None or not actions:
        return "person_only"
    person = {p.get("action") for p in policy.get("person_only") or [] if isinstance(p, dict)}
    return "person_only" if any(a in person for a in actions) else "ask_first"


#: 행동도 검사도 없는 대기의 질문. 승인해도 승인되는 행동이 없다.
NO_QUESTION = ("This run waits for a person, but its record names no action or check. Approving "
               "approves nothing; declining ends the run.")


def decision(pending: dict, policy: dict | None = None) -> dict:
    """사람에게 보이는 대기 하나: 종류, 질문, 행동마다 호스트가 적은 설명, 물은 때와 만료.

    저장된 질문 문장이 없으면(동기화로 온 대기는 보낸 쪽의 문장을 버린다) 저장된 행동과 검사로
    `_question` 이 만든다. 보낸 쪽이 쓴 글은 질문이 되지 않는다."""
    pending = _run_rule.readable({"pending": pending if isinstance(pending, dict) else {}})["pending"]
    actions = pending.get("actions") or []
    checks = pending.get("checks") or []
    kind = decision_kind(pending, policy)
    requests = pending.get("requests") or []
    if not requests:
        requests = [{"action": a, "reason": "", "kind": kind} for a in actions]
    question = pending.get("question") or (
        _question(actions, policy or {}, checks) if actions or checks else NO_QUESTION)
    return {"kind": kind, "question": question, "actions": actions,
            "requests": requests, "checks": checks,
            "asked_at": str(pending.get("asked_at") or ""), "expires_at": expires_at(pending)}


#: 같은 실행의 읽기·검사·저장을 한 줄로 세운다. 허브는 레플리카 하나라 프로세스 안의 잠금으로
#: 답이 두 번 적용되지 않는다. 여러 레플리카로 늘리면 저장 쪽에서 rev 를 대조해야 한다.
_LOCKS = tuple(threading.RLock() for _ in range(64))


def _lock(eid: str) -> threading.RLock:
    return _LOCKS[zlib.crc32(eid.encode("utf-8")) % len(_LOCKS)]


def _failure(error: str, note: str, **extra) -> dict:
    return {"kind": "run", "executed": False, "error": error, "note": note, **extra}


def _no_state() -> dict:
    """실행 상태(state)가 없거나 모르는 값인 실행 기록. 이어 갈 수 없으므로 아무것도 바꾸지 않는다."""
    return _failure("invalid_run_state", "Invalid: this run record has no run state, so it cannot "
                    "go on and nothing changed. Start a new run with another run_id.")


def _no_role() -> dict:
    """역할을 이 그래프에서 찾을 수 없는 실행 기록. 정책 없이는 이어 가지 않으므로 아무것도 바꾸지 않는다."""
    return _failure("invalid_run_state", "Invalid: this run record names no role this graph has, "
                    "so it cannot go on and nothing changed. A person can decline it in "
                    + PERSON_PATH + ". Start a new run with another run_id.")


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _role_of(props: dict) -> str | None:
    """기록이 가리키는 역할 id. 역할 id 모양의 문자열이 아니면 None(역할을 묻지 않는다)."""
    role = props.get("role")
    if isinstance(role, str) and role.startswith(ROLE_PREFIX) and len(role) > len(ROLE_PREFIX):
        return role
    return None


def _record_role(bridge, owner: str, props: dict) -> tuple[dict | None, dict | None]:
    """(기록의 역할 계약, 실패). 역할을 못 읽거나 그래프에 없으면 `_no_role`, 읽기 실패는 그대로."""
    role = _role_of(props)
    if role is None:
        return None, _no_role()
    got = _fresh_role(bridge, owner, role)
    if not got.get("executed"):
        return None, {**got, "kind": "run"}
    if not got.get("found"):
        return None, _no_role()
    return got, None


def _resolve(run) -> str | None:
    """episode id 로 온 것은 그대로, 호스트의 run id 는 episode id 로 바꾼다. 못 읽으면 None."""
    text = str(run or "").strip()
    if text.startswith(RUN_PREFIX):
        return text
    return episode_id(text) if _RUN_ID.fullmatch(text) else None


def _load(bridge, owner: str, eid: str) -> dict | None:
    # 저장된 행 그대로 읽는다(stored). 만료를 읽기에서 계산하면 제출·답이 `_expire` 를 못 지난다.
    got = bridge.request("GET", "/runs", params={"owner": owner, "run": eid, "limit": 1,
                                                 "stored": "1"})
    if not got.get("executed"):
        return {"_error": got}
    for item in got.get("runs") or []:
        if item.get("id") == eid:
            # 잘못된 모양의 값(수가 아닌 step·asks 등)은 읽는 한 벌로 고쳐 읽는다. 다음 저장이 고친 값을 쓴다.
            props = _run_rule.readable(item.get("props"))
            if not isinstance(props.get("outcome"), str):
                # outcome 을 못 읽는 기록은 열린 실행의 outcome 으로 읽는다.
                props["outcome"] = OPEN_OUTCOME
            return props
    return None


def _save(bridge, owner: str, eid: str, props: dict, *, title: str) -> dict:
    node = {"id": eid, "type": "episode", "title": title, "date": _stamp(_now())[:10],
            "domain": DOMAIN, "tags": ["agent-run"], "props": props}
    # 역할을 못 읽는 기록(사람이 거절해 닫는 것)은 역할 노드와 about 엣지 없이 저장한다.
    role = _role_of(props)
    return bridge.request("POST", "/ingest/fragment", body={
        "owner": owner, "nodes": [node] + ([{"id": role, "type": "concept"}] if role else []),
        "edges": [{"from": eid, "to": role, "type": "about"}] if role else []})


def reading(props: dict) -> str:
    """사람이 실행의 결과로 읽는 낱말(제목의 꼬리). 결론을 보고하지 않은 실행은 끝났으면 outcome,
    아니면 state 다.

    결론을 보고한 실행은 결론이 선다: 게이트만 통과한 success 는 `verdict fail`, 프로토콜이 끝내거나
    아직 열린 실행은 그 낱말 뒤에 `blocked, verdict fail` 처럼 붙는다. 그래서 fail·blocked 를
    보고한 실행이 그냥 success 로 읽히는 자리가 없다. 제목을 싣는 승인함 카드·이벤트·알림·검색이
    모두 이 낱말을 본다."""
    state = props.get("state")
    tail = (_text(props.get("outcome")) or OPEN_OUTCOME) if state == "done" else _text(state)
    verdict = props.get("verdict")
    if verdict not in VERDICTS:
        return tail
    said = f"verdict {verdict}"
    return said if tail in ("", SUCCESS) else f"{tail}, {said}"


def _title(props: dict, eid: str = "") -> str:
    """실행 기록의 제목. run_id·역할·상태를 못 읽어도 만든다(run_id 모양이 아니면 episode id).

    길이는 `TITLE_CHARS` 안이다: 저장은 너무 긴 제목의 노드를 경고 하나만 남기고 건너뛴다."""
    role = _role_of(props)
    tail = reading(props)
    run_id = _text(props.get("run_id"))
    head = f"Run {run_id if _RUN_ID.fullmatch(run_id) else eid}"
    title = f"{head} of {role[len('concept:'):]}: {tail}" if role else f"{head}: {tail}"
    return title[:TITLE_CHARS]


def _saved(bridge, owner: str, eid: str, props: dict) -> dict | None:
    """저장하고, 실패하면 그 결과를 돌려준다. 성공은 None.

    저장은 크기 상한을 넘는 노드를 거절하지 않고 건너뛰며 경고에 그 id 를 적는다. 그 경고가 이
    실행을 가리키면 저장되지 않은 것이다: 성공으로 돌려주면 사람은 반영되지 않은 답을 본다."""
    out = _save(bridge, owner, eid, props, title=_title(props, eid))
    skipped = [w for w in out.get("warnings") or []
               if isinstance(w, str) and "skipped" in w and f"id={eid}" in w]
    if not out.get("executed") or out.get("error") or skipped:
        detail = out.get("error") or out.get("note") or (skipped[0] if skipped else None)
        return _failure("run_not_saved", "The run state could not be saved, so nothing advanced. "
                        "Call again with the same arguments.", detail=detail)
    return None


def _budget_left(policy: dict, props: dict) -> dict:
    # 예산과 사용량은 유한한 수만 센다(`run_state.number`). 수가 아닌 예산 항목은 남은 양이 없다고 적지 않는다.
    budget = policy.get("budget") if isinstance(policy.get("budget"), dict) else {}
    used = props.get("used") if isinstance(props.get("used"), dict) else {}
    left = {}
    calls = _run_rule.number(budget.get("model_calls"))
    if calls is not None:
        left["model_calls"] = max(0, int(calls) - _run_rule.count(props.get("step")))
    tools_max = _run_rule.number(budget.get("tool_calls"))
    if tools_max is not None:
        left["tool_calls"] = max(0, int(tools_max) - int(_run_rule.number(used.get("tool_calls")) or 0))
    return left


def _view(props: dict, eid: str, policy: dict | None = None, **extra) -> dict:
    # 저장된 값을 못 읽어도 보기는 만든다: run_id 는 episode id 로, outcome 은 열린 실행의 것으로 읽는다.
    state = props.get("state")
    out = {"kind": "run", "executed": True, "run": eid,
           "run_id": _text(props.get("run_id")) or eid, "role": _text(props.get("role")),
           "state": _text(state), "outcome": _text(props.get("outcome")) or OPEN_OUTCOME,
           "step": _run_rule.count(props.get("step")), **extra}
    if props.get("capability_flags"):
        out["capability_flags"] = list(props["capability_flags"])
    if props.get("verdict") in VERDICTS:
        # 일이 내린 결론. outcome·end_reason(프로토콜이 끝낸 까닭)과 따로 읽힌다.
        out["verdict"] = props["verdict"]
        if isinstance(props.get("verdict_note"), str) and props["verdict_note"]:
            out["verdict_note"] = props["verdict_note"]
    if props.get("answer") and state != "waiting":
        # 마지막 답. reply 의 글은 여기서만 호스트에게 간다.
        last = {"answer": props["answer"], "answered_at": props.get("answered_at", ""),
                "via": props.get("answer_via") or "agent"}
        if props.get("answer_text"):
            last["text"] = props["answer_text"]
        out["last_answer"] = last
    if state == "waiting":
        pending = props.get("pending") or {}
        out["decision"] = decision(pending, policy)
        out["question"] = out["decision"]["question"]
        out["pending_actions"] = list(out["decision"]["actions"])
        #: 몇 번째 질문인가. MCP Tasks 의 작업 id 가 이 번호를 쓴다(`run_tasks.py`).
        out["ask"] = _run_rule.count(props.get("asks"))
        out["asked_at"] = out["decision"]["asked_at"]
        out["expires_at"] = out["decision"]["expires_at"]
        if out["decision"]["kind"] == "person_only":
            out["next"] = ("Only a person answers this, in " + PERSON_PATH + ". An agent's answer "
                           "is refused. Show the question and tell the person where to answer, then "
                           "poll vinc_runs with run until the run is no longer waiting. No output "
                           "is accepted while it waits.")
        else:
            out["next"] = ("A person decides first. Show the question, then call vinc_run_submit "
                           "with answer approve or decline as the person chose, or the person "
                           "answers in " + PERSON_PATH + ". No output is accepted while it waits.")
    elif state == "working":
        out["next"] = ("Do this step, then call vinc_run_submit with output. Name an action in "
                       "person_only_requests before taking it when the role lists it as person "
                       "only or asks first. Report done work as " + RESTATE + ".")
        if policy is not None:
            out["task"] = props.get("task", "")
            out["policy"] = {k: policy[k] for k in ("reads", "gates", "output_kind", "act_policy",
                                                    "capabilities", "person_only", "never",
                                                    "cites")
                             if k in policy}
            out["budget_left"] = _budget_left(policy, props)
    else:
        out["ended_at"] = props.get("ended_at", "")
        out["end_reason"] = props.get("end_reason", "")
    return out


def _fresh_role(bridge, owner: str, role: str) -> dict:
    return contract(role, bridge, forced_owner=owner, compact=True, capped=False)


def client_label(info) -> str:
    """MCP `initialize` 의 clientInfo 를 실행 기록의 host 한 줄로 만든다. 없으면 빈 문자열."""
    if not isinstance(info, dict):
        return ""
    parts = [str(info.get(k) or "").strip() for k in ("name", "version")]
    return " ".join(" ".join(p for p in parts if p).split())[:HOST_MAX]


def with_host(name: str, args, label: str):
    """연결이 아는 클라이언트를 run_start 의 host 기본값으로 싣는다. 호출자가 준 host 가 이긴다.
    연결 하나에 클라이언트 하나인 곳(stdio·로컬 프록시)에서만 부른다. 허브의 디스패처는 모든
    테넌트가 같이 쓰므로 거기서 기억하면 남의 클라이언트가 섞인다."""
    if name != "vinc_run_start" or not label or not isinstance(args, dict) or args.get("host"):
        return args
    return {**args, "host": label}


def start(args: dict, bridge, owner: str) -> dict:
    role, task = str(args.get("role") or "").strip(), str(args.get("task") or "").strip()
    run_id = str(args.get("run_id") or "").strip() or "run-" + uuid.uuid4().hex[:12]
    if not role:
        return _failure("role_required", "Invalid: name the role to run.")
    if not task or len(task) > TASK_MAX:
        return _failure("task_required", f"Invalid: pass the task as a non-empty string of at most "
                                         f"{TASK_MAX} characters.")
    if not _RUN_ID.fullmatch(run_id):
        return _failure("invalid_run_id", "Invalid: run_id is 1 to 128 letters, digits, '.', '_', "
                                          "':' or '-'.")
    subject = str(args.get("subject") or "").strip()[:SUBJECT_MAX]
    host = " ".join(str(args.get("host") or "").split())[:HOST_MAX]
    eid = episode_id(run_id)
    got = _fresh_role(bridge, owner, role)
    if not got.get("executed") or not got.get("found"):
        return {**got, "kind": "run"}
    existing = _load(bridge, owner, eid)
    if existing and "_error" in existing:
        return {**existing["_error"], "kind": "run"}
    if existing:
        if _role_of(existing) is None:
            return _no_role()
        if existing.get("role") != got["id"]:
            return _failure("run_id_in_use", "Invalid: this run_id belongs to another role.")
        if existing.get("state") not in STATES:
            return _no_state()
        return _view(as_read(existing), eid, got["props"], resumed=True)
    props = {"run_id": run_id, "role": got["id"], "outcome": OPEN_OUTCOME, "state": "working",
             "step": 0, "task": task, "started_at": _stamp(_now()), "role_rev": got.get("rev"),
             "role_hash": got.get("hash"), "used": {}, "approved": []}
    if subject:
        props["subject"] = subject
    if host:
        props["host"] = host
    failed = _saved(bridge, owner, eid, props)
    if failed:
        return failed
    return _view(props, eid, got["props"], resumed=False)


def _end(props: dict, outcome: str, reason: str) -> None:
    props.update(outcome=outcome, state="done", ended_at=_stamp(_now()), end_reason=reason)
    props.pop("pending", None)


def _over_budget(policy: dict, props: dict) -> str:
    budget = policy.get("budget") or {}
    used = props.get("used") or {}
    if "model_calls" in budget and _run_rule.count(props.get("step")) >= int(budget["model_calls"]):
        return "budget_model_calls"
    if "tool_calls" in budget and used.get("tool_calls", 0) > budget["tool_calls"]:
        return "budget_tool_calls"
    if "tokens" in budget and used.get("tokens_in", 0) + used.get("tokens_out", 0) > budget["tokens"]:
        return "budget_tokens"
    if "cost_usd" in budget and used.get("cost_usd", 0) > budget["cost_usd"]:
        return "budget_cost_usd"
    if "wall_seconds" in budget:
        started = _parse_stamp(props.get("started_at"))
        # 시작 시각을 못 읽는 실행은 예산 안에 있다고 볼 수 없다.
        if started is None or (_now() - started).total_seconds() > budget["wall_seconds"]:
            return "budget_wall_seconds"
    return ""


def _add_usage(props: dict, usage) -> None:
    if not isinstance(usage, dict):
        return
    used = props.setdefault("used", {})
    for key in USAGE_KEYS:
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            used[key] = used.get(key, 0) + value


def _add_cited(props: dict, cited) -> None:
    if not isinstance(cited, list):
        return
    merged = list(props.get("cited_ids") or [])
    for item in cited:
        if isinstance(item, str) and ":" in item.strip() and item.strip() not in merged:
            merged.append(item.strip())
    if merged:
        props["cited_ids"] = merged[:CITED_MAX]


def _missing_ids(bridge, owner: str, ids: list[str]) -> list[str] | None:
    """그래프에 없는 id 들. 물어볼 수 없으면 None."""
    missing: list[str] = []
    for i in range(0, len(ids), _EXISTS_BATCH):
        got = bridge.request("GET", "/node-edges", params={
            "owner": owner, "nodes": json.dumps(ids[i:i + _EXISTS_BATCH]), "limit": 1})
        if not isinstance(got, dict) or not got.get("executed"):
            return None
        missing += [m for m in got.get("missing_nodes") or [] if isinstance(m, str)]
    return missing


def _citations(bridge, owner: str, props: dict, policy: dict, output: str) -> list[str]:
    """props.cites 가 required 인 역할의 근거 검사. 실패한 검사 이름들(통과면 빈 목록).

    근거는 cited_ids 와 출력 끝의 Sources 줄에서 모은다. 없는 id 는 기록에서 빼므로 다음
    시도가 같은 id 에 계속 막히지 않는다."""
    if policy.get("cites") != CITES_REQUIRED:
        return []
    _add_cited(props, role_output_gate.sources_line(output) or [])
    ids = list(props.get("cited_ids") or [])
    if not ids:
        return ["citations_required"]
    missing = _missing_ids(bridge, owner, ids)
    if missing is None:
        return ["citations_unverified"]
    if not missing:
        return []
    kept = [i for i in ids if i not in set(missing)]
    if kept:
        props["cited_ids"] = kept
    else:
        props.pop("cited_ids", None)
    props["citations_missing"] = missing[:20]
    return ["citations_missing"]


def _used_capabilities(args: dict):
    """(이번 단계에 호스트가 쓴 능력 id 들, 오류 문장). 없으면 빈 목록."""
    raw = args.get("capabilities_used")
    if raw is None:
        return [], None
    if (not isinstance(raw, list) or len(raw) > CAPABILITIES_PER_STEP
            or any(not isinstance(c, str) or not _CAPABILITY_ID.fullmatch(c) for c in raw)):
        return [], (f"Invalid: capabilities_used is a list of at most {CAPABILITIES_PER_STEP} "
                    "capability ids such as github.pr.merge (no wildcards).")
    return list(dict.fromkeys(raw)), None


def _audit(policy: dict, used: list[str], approved: list[str]) -> list[dict]:
    """쓴 능력마다 역할과 견준 표지. 문제없는 id 는 빠진다."""
    caps = policy.get("capabilities") if isinstance(policy.get("capabilities"), dict) else {}
    allow_list = bool(caps.get("allow"))
    flags = []
    for cap in used:
        verdict = capability_verdict(caps, cap)
        if verdict == "deny":
            flags.append({"id": cap, "flag": "denied"})
        elif verdict == "approve" and cap not in approved:
            flags.append({"id": cap, "flag": "unapproved"})
        elif verdict is None and allow_list:
            flags.append({"id": cap, "flag": "outside_allow"})
    return flags


def _record_capabilities(props: dict, used: list[str], flags: list[dict], step: int) -> None:
    if used:
        merged = list(dict.fromkeys(list(props.get("capabilities_used") or []) + used))
        props["capabilities_used"] = merged[:CAPABILITIES_MAX]
    if flags:
        kept = list(props.get("capability_flags") or [])
        seen = {(f.get("id"), f.get("flag")) for f in kept}
        kept += [{**f, "step": step} for f in flags if (f["id"], f["flag"]) not in seen]
        props["capability_flags"] = kept[:CAPABILITIES_MAX]


def _question(actions: list[str], policy: dict, checks: list[str]) -> str:
    reasons = {p.get("action"): p.get("reason") for p in policy.get("person_only") or []
               if isinstance(p, dict)}
    parts = [f"{a}" + (f" ({reasons[a]})" if reasons.get(a) else "") for a in actions]
    if parts:
        return "A person decides before the role goes on: " + "; ".join(parts) + "."
    return "A gate asks a person to decide: " + ", ".join(checks) + "."


def _expires_in(args: dict):
    """(요청의 수명 초, 오류 문장). 안 주면 기본값이다."""
    raw = args.get("expires_in_s")
    if raw is None:
        return EXPIRES_DEFAULT_S, None
    # 호스트마다 같은 수를 다른 꼴로 보낸다(3600 · 3600.0 · "3600"). 정수로 읽히는 값만 받고 3600.5 · "1h" 는 거절한다.
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, bool) or not isinstance(raw, int) or not EXPIRES_MIN_S <= raw <= EXPIRES_MAX_S:
        return 0, (f"Invalid: expires_in_s is a whole number of seconds from {EXPIRES_MIN_S} to "
                   f"{EXPIRES_MAX_S} (12 hours).")
    return raw, None


def verdicts_for(policy: dict) -> tuple[str, ...]:
    """이 역할이 보고하는 결론의 낱말. 검사하는 역할(tier verify 또는 output_kind verdict)은
    pass·fail·blocked, 그 밖의 역할은 delivered·partial·blocked 다."""
    if policy.get("tier") == "verify" or policy.get("output_kind") == "verdict":
        return _run_rule.CHECK_VERDICTS
    return _run_rule.WORK_VERDICTS


def _verdict(args: dict, policy: dict, mode: str):
    """(결론, 한 줄, 오류 문장). 결론을 보고하지 않았으면 (None, "", None).

    결론은 출력과 함께만 온다. blocked 는 무엇이 막았는지 한 줄(`verdict_note`)로 이름 댄다.
    오류면 아무것도 저장하지 않는다(`_submit` 이 저장 전에 거절한다)."""
    raw, note = args.get("verdict"), args.get("verdict_note")
    if raw in (None, "") and note in (None, ""):
        return None, "", None
    if raw in (None, ""):
        return None, "", "Invalid: verdict_note goes with a verdict."
    if mode != "output":
        return None, "", "Invalid: verdict goes with output, not with an answer or abandon."
    allowed = verdicts_for(policy)
    if not isinstance(raw, str) or raw not in allowed:
        return None, "", "Invalid: verdict for this role is one of " + ", ".join(allowed) + "."
    if note is not None and not isinstance(note, str):
        return None, "", "Invalid: verdict_note is a string."
    note = _run_rule.visible_line(note or "")
    if len(note) > VERDICT_NOTE_MAX:
        return None, "", f"Invalid: verdict_note is at most {VERDICT_NOTE_MAX} characters."
    if raw == "blocked" and not note:
        return None, "", "Invalid: a blocked verdict names what blocked it in verdict_note."
    return raw, note, None


def _record_verdict(props: dict, verdict, note: str) -> None:
    """이 출력이 보고한 결론을 기록에 둔다. 보고하지 않은 출력은 앞서 남은 결론을 그대로 둔다."""
    if verdict is None:
        return
    props["verdict"] = verdict
    if note:
        props["verdict_note"] = note
    else:
        props.pop("verdict_note", None)


def _bad_output(output) -> bool:
    return not isinstance(output, str) or not output.strip() or len(output) > OUTPUT_MAX


def _requests(args: dict, policy: dict, approved: list[str]):
    """호스트가 하려는 행동을 셋으로 가른다: 사람만(게이트로), 먼저 묻기, 금지. 이미 승인된 것은 뺀다."""
    raw = args.get("person_only_requests")
    if raw is None:
        return [], [], [], None
    if not isinstance(raw, list):
        return [], [], [], "Invalid: person_only_requests is a list of {action, reason}."
    person = {p.get("action") for p in policy.get("person_only") or [] if isinstance(p, dict)}
    act = policy.get("act_policy") or {}
    gate, asks, never = [], [], []
    for item in raw:
        if (not isinstance(item, dict) or set(item) != {"action", "reason"}
                or not isinstance(item["action"], str) or not str(item["reason"] or "").strip()):
            return [], [], [], "Invalid: each person_only_request is exactly {action, reason}."
        action = item["action"]
        if action in approved:
            continue
        if act.get(action) == "never":
            never.append(action)
        elif action in person:
            gate.append(item)
        elif act.get(action) == "ask_first":
            asks.append(action)
        elif "." in action and capability_verdict(policy.get("capabilities"), action) == "approve":
            # 승인으로 묶인 능력 id 도 미리 묻는다. 승인되면 capabilities_used 에 그 id 가 와도 표지가 없다.
            asks.append(action)
        else:
            return [], [], [], (f"Invalid: {action} is neither person only nor ask first for this "
                                "role, so it needs no request. Take it, or drop the request.")
    return gate, asks, never, None


def submit(args: dict, bridge, owner: str) -> dict:
    eid = _resolve(args.get("run"))
    if eid is None:
        return _failure("run_required", "Invalid: pass the run id vinc_run_start returned.")
    modes = [k for k in ("output", "answer", "abandon") if args.get(k) not in (None, "", False)]
    if len(modes) != 1:
        return _failure("one_input_required", "Invalid: pass exactly one of output, answer or "
                                              "abandon.")
    with _lock(eid):
        return _submit(args, bridge, owner, eid, modes[0])


def _expire(bridge, owner: str, eid: str, props: dict) -> dict:
    """만료된 요청을 닫는다: 실행은 막힌 채 끝나고 그 사실을 돌려준다."""
    props["expired_at"] = _stamp(_now())
    _end(props, "blocked", EXPIRED_REASON)
    refused = _failure(
        "request_expired", "Refused: the request expired before anyone answered, so the run "
        "ended as blocked. Start a new run to ask again.", run_state=_view(props, eid))
    return _saved(bridge, owner, eid, props) or refused


def _submit(args: dict, bridge, owner: str, eid: str, mode: str) -> dict:
    props = _load(bridge, owner, eid)
    if props is None:
        return {"kind": "run", "executed": False, "found": False, "error": "run_not_found",
                "note": "Unknown run: start one with vinc_run_start."}
    if "_error" in props:
        return {**props["_error"], "kind": "run"}
    if props.get("state") not in STATES:
        return _no_state()
    if props.get("state") == "done":
        return _view(props, eid)
    got, failed = _record_role(bridge, owner, props)
    if failed:
        return failed
    policy = got["props"]
    used, bad = _used_capabilities(args)
    if bad:
        return _failure("invalid_capabilities_used", bad)
    verdict, note, bad = _verdict(args, policy, mode)
    if bad:
        return _failure("invalid_verdict", bad)
    _add_usage(props, args.get("usage"))
    _add_cited(props, args.get("cited_ids"))
    flags = _audit(policy, used, list(props.get("approved") or []))
    # 표지는 이 호출이 낸 단계에 단다(output 이면 _output 이 올릴 다음 단계 번호).
    _record_capabilities(props, used, flags, int(props.get("step", 0)) + (mode == "output"))
    if mode == "abandon":
        _end(props, "abandoned", "abandoned by the host")
        return _finish(bridge, owner, eid, props)
    if props["state"] == "waiting":
        if expired(props.get("pending") or {}):
            return _expire(bridge, owner, eid, props)
        if mode != "answer":
            return _failure("invalid_run_state", "Invalid: the run waits for an answer, not output.",
                            run_state=_view(props, eid, policy))
        if decision_kind(props.get("pending") or {}, policy) == "person_only":
            # 도구 호출은 언제나 에이전트 자격으로 온다. 사람만 답하는 대기는 세션 프록시로만 답한다.
            return _failure(
                "person_answer_required", "Refused: only a person may answer this decision, and "
                "this call came with an agent credential, so nothing changed. The person answers "
                "it in " + PERSON_PATH + ". Poll vinc_runs with run to see the answer.",
                run_state=_view(props, eid, policy))
        return _answer(args, bridge, owner, eid, props, policy, via="agent")
    if mode == "answer":
        return _failure("invalid_run_state", "Invalid: the run is not waiting for an answer.")
    over = _over_budget(policy, props)
    if over:
        # 예산은 게이트보다 먼저 실행을 끝낸다. 그 전에 이 출력이 보고한 결론을 남긴다: 끝낸 까닭은
        # end_reason(예산), 일의 결론은 verdict 로 따로 읽힌다.
        if not _bad_output(args.get("output")):
            _record_verdict(props, verdict, note)
        _end(props, "blocked", over)
        return _finish(bridge, owner, eid, props)
    return _output(args, bridge, owner, eid, props, policy, flags, (verdict, note))


def _finish(bridge, owner, eid, props, **extra) -> dict:
    # 보기를 먼저 만들고 저장한다. 저장 뒤에 예외가 나면 저장된 답이 오류로 보인다.
    view = _view(props, eid, **extra)
    return _saved(bridge, owner, eid, props) or view


def _answer(args, bridge, owner, eid, props, policy, *, via: str, by: str = "",
            text: str = "") -> dict:
    answer = args.get("answer")
    allowed = PERSON_ANSWERS if via == "person" else ANSWERS
    if answer not in allowed:
        return _failure("invalid_answer", "Invalid: answer is " + ", ".join(allowed) + ".")
    pending = props.pop("pending", {}) or {}
    props["answered_at"] = _stamp(_now())
    #: `run.answered` 이벤트(api/events.py)가 읽는다. 다음 답이 오면 덮여 새 이벤트가 된다.
    props["answer"] = answer
    props["answered_actions"] = list(pending.get("actions") or [])
    props["answer_via"] = via
    for key, value in (("answered_by", by), ("answer_text", text)):
        if value:
            props[key] = value
        else:
            props.pop(key, None)
    if answer == "reply":
        # 글로 답한 것은 승인이 아니다. 실행은 이어 가고 같은 행동은 다시 물어야 한다.
        props["state"] = "working"
        return _resume(bridge, owner, eid, props, policy)
    if answer == "decline":
        _end(props, "blocked", "declined by a person")
        return _finish(bridge, owner, eid, props)
    props["approved"] = list(dict.fromkeys(list(props.get("approved") or [])
                                           + list(pending.get("actions") or [])))
    props["state"] = "working"
    return _resume(bridge, owner, eid, props, policy)


def _resume(bridge, owner, eid, props, policy) -> dict:
    # `_finish` 와 같이 보기를 먼저 만든다: 저장된 승인 뒤에는 예외가 날 곳이 없다.
    view = _view(props, eid, policy, approved=list(props.get("approved") or []))
    return _saved(bridge, owner, eid, props) or view


def person_answer(args: dict, bridge, owner: str, *, actor: str) -> dict:
    """사람이 세션으로 답한다. 계정의 서명된 세션 프록시만 부른다(`http_server.graph_owner_request`).

    `ask` 는 사람이 본 질문 번호다. 그 질문이 아직 열려 있을 때만 답이 들어가므로 같은 질문에는
    한 번만 답한다. 재개는 `vinc_run_submit` 의 answer 와 같은 `_answer` 를 지난다."""
    base = {"kind": "run.answer", "executed": False}
    eid = _resolve(args.get("run"))
    if eid is None:
        return {**base, "error": "run_required", "note": "Invalid: pass the run id."}
    ask = args.get("ask")
    if isinstance(ask, str) and ask.isdigit():
        ask = int(ask)
    if isinstance(ask, bool) or not isinstance(ask, int) or ask < 1:
        return {**base, "error": "ask_required",
                "note": "Invalid: pass ask, the question number the waiting run showed."}
    answer = args.get("answer")
    if answer not in PERSON_ANSWERS:
        return {**base, "error": "invalid_answer",
                "note": "Invalid: answer is " + ", ".join(PERSON_ANSWERS) + "."}
    text = args.get("text")
    if text is not None and not isinstance(text, str):
        return {**base, "error": "invalid_text", "note": "Invalid: text is a string."}
    text = (text or "").strip()
    if len(text) > ANSWER_TEXT_MAX or (answer == "reply" and not text):
        return {**base, "error": "invalid_text", "note": f"Invalid: a reply needs text, and text "
                f"is at most {ANSWER_TEXT_MAX} characters."}
    if not isinstance(actor, str) or not actor.startswith("user:"):
        return {**base, "error": "person_required", "note": "Refused: no signed-in person."}
    with _lock(eid):
        props = _load(bridge, owner, eid)
        if props is None:
            return {**base, "found": False, "error": "run_not_found", "note": "Unknown run."}
        if "_error" in props:
            return {**props["_error"], **base}
        if props.get("state") != "waiting" or int(props.get("asks") or 0) != ask:
            return {**base, "error": "already_answered", "note": "Refused: this question is no "
                    "longer open. It was answered, it ended, or a newer question replaced it.",
                    "run_state": _view(props, eid)}
        if expired(props.get("pending") or {}):
            return {**_expire(bridge, owner, eid, props), "kind": "run.answer"}
        policy: dict = {}
        if answer != "decline":
            # 승인과 글 답은 역할의 정책과 견준다. 역할이 없으면 견줄 것이 없어 아무것도 승인하지 않는다.
            # 거절은 역할도 run_id 도 읽지 않는다: 목록에 보이는 대기는 무엇이든 사람이 닫는다.
            got, failed = _record_role(bridge, owner, props)
            if failed:
                if failed.get("error") == "invalid_run_state":
                    failed["note"] = (
                        "Refused: this run record names no role this graph has, so there is no "
                        "policy to check an approval against and nothing was approved. Decline it "
                        "to close the run.")
                return {**failed, **base, "run_state": _view(props, eid)}
            policy = got["props"]
        out = _answer({"answer": answer}, bridge, owner, eid, props, policy, via="person",
                      by=actor, text=text)
        if not out.get("executed"):
            return {**out, "kind": "run.answer"}
        # 여기부터는 저장이 끝난 뒤다. 이미 만든 보기의 값만 옮긴다.
        return {"kind": "run.answer", "executed": True, "run": eid, "run_id": out.get("run_id"),
                "ask": ask, "answer": answer, "answered_at": props.get("answered_at"),
                "answered_by": actor, "via": "person",
                "state": out.get("state"), "outcome": out.get("outcome")}


def _restate(policy: dict, output: str, failed) -> dict:
    """완료 주장으로 막힌 단계에 그 문장과 고쳐 쓰는 법을 준다. 다른 이유로 막혔으면 빈 dict.

    evidence_sentences 는 모든 검사의 문장이 섞이므로 주장 검사만 다시 돌려 그 문장을 집는다.
    증거는 넘기지 않는다: 모델이 낸 인자는 증거가 아니다."""
    ids = {g.get("id") for g in policy.get("gates") or []
           if isinstance(g, dict) and isinstance(g.get("check"), dict)
           and g["check"].get("type") == _CLAIM_CHECK}
    if not (set(failed) & (ids | {"never_" + _CLAIM_CHECK})):
        return {}
    only = {**policy, "never_checks": [], "person_only": [],
            "gates": [g for g in policy.get("gates") or []
                      if (g.get("check") or {}).get("type") in role_output_gate._REQUIRED]}
    said = role_output_gate.OutputGate(only).evaluate(output).evidence_sentences
    if not said:
        return {}
    return {"claim": said[0], "next": "Restate claim as " + RESTATE + ". Then submit output again."}


def _output(args, bridge, owner, eid, props, policy, flags=(), verdict=(None, "")) -> dict:
    output = args.get("output")
    if _bad_output(output):
        return _failure("output_required", f"Invalid: pass the output as one non-empty string of at "
                                           f"most {OUTPUT_MAX} characters.")
    gate_requests, asks, never, bad = _requests(args, policy, list(props.get("approved") or []))
    if bad:
        return _failure("invalid_person_only_requests", bad)
    ttl, bad = _expires_in(args)
    if bad:
        return _failure("invalid_expires_in", bad)
    props["step"] = int(props.get("step", 0)) + 1
    props["output_ref"] = {"sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                           "chars": len(output)}
    # 결론은 세어진 단계마다 남는다: 게이트가 막은 단계, 대기에 든 단계, 끝난 단계 모두. 다음 출력이
    # 다른 결론을 보고하면 바뀌고, 보고하지 않으면 그대로다.
    _record_verdict(props, *verdict)
    if never or flags:
        # 금지된 행동과, 역할이 허락하지 않은 능력을 이미 쓴 단계는 막는다. 실행은 열린 채 다음 시도를 받는다.
        props["gate_results"] = {"verdict": "block", "failed_checks": (
            [f"action_never:{a}" for a in never]
            + [f"capability_{f['flag']}:{f['id']}" for f in flags])}
        failed = _saved(bridge, owner, eid, props)
        return failed or _view(props, eid, policy, status="blocked",
                               failed_checks=props["gate_results"]["failed_checks"])
    uncited = _citations(bridge, owner, props, policy, output)
    if uncited:
        # 근거가 없는 단계는 막는다. 실행은 열린 채 근거를 단 다음 시도를 받는다.
        props["gate_results"] = {"verdict": "block", "failed_checks": uncited}
        failed = _saved(bridge, owner, eid, props)
        missing = props.get("citations_missing") if uncited == ["citations_missing"] else None
        return failed or _view(props, eid, policy, status="blocked", failed_checks=uncited,
                               **({"missing_ids": list(missing)} if missing else {}))
    props.pop("citations_missing", None)
    gate = role_output_gate.OutputGate(policy)
    result = gate.evaluate(output, person_only_requests=gate_requests)
    props["gate_results"] = {"verdict": result.verdict, "failed_checks": list(result.failed_checks)}
    # 고쳐 쓰는 법은 응답에만 싣고 실행 기록에는 남기지 않는다. 호스트가 스스로 이름 댄 행동에는 붙이지 않는다.
    named = tuple(f"person_only ({g['action']})" for g in gate_requests)
    advice = [line for line in gate.advice(result) if not line.startswith(named)]
    if result.verdict == "block":
        failed = _saved(bridge, owner, eid, props)
        return failed or {**_view(props, eid, policy, status="blocked",
                                  failed_checks=list(result.failed_checks),
                                  evidence_sentences=list(result.evidence_sentences),
                                  **({"advice": advice} if advice else {})),
                          **_restate(policy, output, result.failed_checks)}
    actions = list(dict.fromkeys(list(result.person_only_actions) + asks))
    if result.verdict == "interrupt" or actions:
        props["state"] = "waiting"
        now = _now()
        asked_at = _stamp(now)
        props["asks"] = int(props.get("asks") or 0) + 1
        props["last_asked_at"] = asked_at
        person = set(result.person_only_actions) | {g["action"] for g in gate_requests}
        reasons = {i["action"]: str(i["reason"]).strip()[:REASON_MAX]
                   for i in args.get("person_only_requests") or []}
        requests = [{"action": a, "reason": reasons.get(a, ""),
                     "kind": "person_only" if a in person else "ask_first"} for a in actions]
        # 게이트가 끼어든 것(행동 없이)과 사람만 하는 행동이 하나라도 있으면 사람만 답한다.
        kind = ("person_only" if result.verdict == "interrupt" or not actions
                or any(r["kind"] == "person_only" for r in requests) else "ask_first")
        props["pending"] = {"actions": actions, "checks": list(result.failed_checks),
                            "question": _question(actions, policy, list(result.failed_checks)),
                            "asked_at": asked_at, "kind": kind, "requests": requests,
                            "expires_at": _stamp(now + datetime.timedelta(seconds=ttl))}
        failed = _saved(bridge, owner, eid, props)
        if failed:
            return failed
        # 저장된 대기만 알린다(계정 → 웹 푸시). 행동·질문은 싣지 않고, 실패해도 실행은 그대로다.
        run_notify.waiting(owner, eid, props)
        return _view(props, eid, policy, **({"advice": advice} if advice else {}))
    _end(props, "success", "gates passed")
    return _finish(bridge, owner, eid, props, status="passed")
