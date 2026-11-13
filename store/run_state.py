# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)의 프로토콜 상태를 지키는 규칙 한 벌.

대기·승인·답(`STATE_KEYS`)은 그 실행을 가진 스토어의 실행 프로토콜만 쓴다. 쓰기 입구가 모두
이 모듈을 같이 쓴다:

- 도구 쓰기(MCP·REST `vinc_ingest`, 웹 프록시 `ingest-fragment`·`ingest-pack`)는 `writes` 로
  그런 쓰기를 찾아 거절한다(`vinc_mcp/tools.py`, `vinc_mcp/http_server.py`).
- 동기화 적용(`api/sync.py` `apply_delta`)은 묶음을 거절하지 않고 `keep_own` 으로 받는 스토어의
  값을 남긴다. 허브는 push 에서, 기기는 pull 에서 같은 함수를 지난다.
- 받는 스토어가 처음 보는 실행은 믿음이 한쪽으로만 간다. 기기는 자기 허브를 믿어 허브의 상태를
  그대로 받고(`trusted=True`, 기기의 pull 만), 허브는 기기를 믿지 않아 질문(`QUESTION_KEYS`)만
  받고 답(`ANSWER_KEYS`)은 버린다(기본값).
- 실행의 역할(`ROLE_KEYS`)은 상태 키처럼 지킨다: `props_patch` 는 거절하고, 동기화는 프로토콜이
  가진 실행(`held`)의 저장된 역할을 남긴다.
- 프로토콜이 가진 실행 기록에 props 를 통째로 쓰는 쓰기는 거절한다(`api/mutation_contract.py`,
  `whole_props_refusal`). 끝나지 않은 실행(`unfinished`: 일하는 중이거나 사람을 기다린다)은 도구 삭제
  (`api/query.py` `delete_node`)와 믿지 않는 쪽의 삭제 표식(`api/sync.py` `apply_delta`)이 지우지 않는다.
- 사람을 기다리는 실행(`waiting`)의 카드(노드 제목과 `CARD_KEYS`)는 도구 쓰기가 바꾸지 못하고
  (`card_refusal`), 동기화는 받는 스토어의 값을 남긴다(props 는 `keep_own`, 제목은 `api/sync.py` `apply_delta`).

``store/`` 는 맨 아래 층이라(``scripts/gate_ownership.py`` 의 ``LAYER_BANS``) ``api`` 와
``vinc_mcp`` 가 둘 다 이것을 가져다 쓴다.
"""
from __future__ import annotations

import datetime
import json
import unicodedata
import math

RUN_PREFIX = "episode:run-"
#: 질문 쪽 상태: 실행이 지금 무엇을 묻는가. 실행 프로토콜의 출력 단계(`_output`)와 작업 닫기
#: (`run_tasks.cancel`)가 쓴다. 닫힌 작업 표시는 답을 막기만 하고 승인을 만들지 않는다.
#: 실행이 돌린 호출의 기록(`actions_run`), 아직 묻지 못한 기록된 사람 전용 행동(`record_pending`), 호스트 기록이
#: 글을 대신한 자리의 참고(`record_notes`), 이미 받은 호스트 기록의 일회용 id(`host_record_ids`)도 질문 쪽이다.
QUESTION_KEYS = ("state", "pending", "asks", "last_asked_at", "task_cancelled", "actions_run", "record_pending",
                 "record_notes", "host_record_ids")
#: 답 쪽 상태: 누가 무엇에 답했고 무엇이 승인됐는가. 실행 프로토콜의 `_answer` 가 채운다.
ANSWER_KEYS = ("approved", "answer", "answered_at", "answered_actions", "answered_by", "answer_via",
               "answer_text")
#: 실행 기록의 프로토콜 상태 필드. 도구 쓰기로는 바꾸지 못하고, 가진 실행에는 동기화로 건너가지 않는다.
STATE_KEYS = QUESTION_KEYS + ANSWER_KEYS
#: 실행을 연 역할과 그때의 역할 계약 해시. 승인은 이 역할의 정책과 견주므로 상태 키처럼 지킨다:
#: `props_patch` 로는 못 바꾸고, 프로토콜이 가진 실행에는 동기화로 건너가지 않는다. 통째의 props 는
#: 클라이언트의 실행 기록(`vinc_client.record_run`)도 역할을 실어 쓰므로 여기서 거절하지 않는다.
ROLE_KEYS = ("role", "role_hash")
#: 승인함의 카드(`api/runs.py` `waiting_runs`)가 사람에게 보이는 props 가운데 상태·역할 밖의 것. 사람은
#: 노드의 제목과 이것을 읽고 답하므로, 실행이 사람을 기다리는 동안에는 도구 쓰기와 동기화가 바꾸지 못한다.
#: 기다리지 않는 실행에서는 `props_patch` 와 동기화가 보통의 규칙대로 바꾼다.
CARD_KEYS = ("run_id", "task", "subject", "host", "step", "started_at")
#: 실행 프로토콜의 상태. 끝은 `DONE` 하나이고, 일하는 중(working)과 기다리는 중(waiting)은 끝나지 않았다.
STATES = ("working", "waiting", "done")
DONE = "done"
#: 믿지 않는 쪽에서 온 질문의 대기 종류. 누가 답해도 되는지는 보낸 쪽의 말을 따르지 않는다.
_PERSON_ONLY = "person_only"
#: 막힌 쓰기의 오류 코드이자 동기화 결과가 남긴 실행을 싣는 필드 이름.
CODE = "run_state_protected"
#: 경고 문장에 이름을 싣는 실행 수의 상한. 전체 목록은 결과의 같은 이름 필드에 있다.
_NAMED_MAX = 20
_MISSING = object()
#: 사람에게 묻는 요청의 최대 수명(초). 실행 프로토콜이 받는 `expires_in_s` 의 상한이고, 믿지 않는
#: 쪽에서 온 질문도 받는 스토어의 시계로 이 안에서 만료된다.
EXPIRES_MAX_S = 43200
#: 만료 시각이 없는 요청의 수명(초). 물은 때부터 센다.
EXPIRES_DEFAULT_S = 3600
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
#: 정수로 읽는 횟수 문자열의 최대 자릿수. 더 긴 문자열은 수로 읽지 않는다.
_COUNT_DIGITS = 18
#: 일이 내린 결론(`verdict`). 에이전트가 출력과 함께 보고하는 주장이라 프로토콜 상태(`STATE_KEYS`)가
#: 아니고, 게이트 결과(`gate_results`)·프로토콜이 끝낸 까닭(`outcome`·`end_reason`)과 따로 둔다.
#: 검사하는 역할(tier verify 또는 output_kind verdict)은 `CHECK_VERDICTS`, 그 밖은 `WORK_VERDICTS` 를 쓴다.
CHECK_VERDICTS = ("pass", "fail", "blocked")
WORK_VERDICTS = ("delivered", "partial", "blocked")
VERDICTS = ("pass", "fail", "delivered", "partial", "blocked")
#: 결론에 붙는 한 줄(무엇이 막았나, 무엇이 실패했나, 무엇이 남았나)의 최대 길이.
VERDICT_NOTE_MAX = 300
#: 실행에 남는 참고(`record_notes`) 한 줄의 최대 길이.
NOTE_MAX = 300
#: 실행 프로토콜과 읽는 쪽(승인함·실행 목록·이벤트·알림·Tasks)이 읽는 실행 기록의 props 이름.
#: 생성 시험(`tests/mcp/test_run_record_property.py`)이 이 이름마다 잘못된 모양의 값을 넣어 본다.
PROPS = ("run_id", "role", "role_hash", "outcome", "state", "step", "asks", "task", "subject",
         "host", "started_at", "ended_at", "end_reason", "expired_at", "last_asked_at", "used",
         "cited_ids", "citations_missing", "capabilities_used", "capability_flags", "pending",
         "approved", "answer", "answered_at", "answered_actions", "answered_by", "answer_via",
         "answer_text", "task_cancelled", "verdict", "verdict_note", "record_notes")
#: 그중 대기(`pending`) 안에서 읽는 이름.
PENDING_PROPS = ("actions", "checks", "question", "asked_at", "expires_at", "kind", "requests")


def run_id(nid) -> str | None:
    """id 가 실행 기록을 가리키면 스토어가 쓰는 모양(앞뒤 공백을 벗긴 id)을, 아니면 None.

    fragment 병합은 id 의 앞뒤 공백을 벗기고 쓴다. 벗기지 않고 견주면 `" episode:run-x"` 가
    이 검사를 지나 같은 노드에 닿는다. 대소문자는 바꾸지 않는다: 스토어도 바꾸지 않아 다른 노드다."""
    if not isinstance(nid, str):
        return None
    text = nid.strip()
    return text if text.startswith(RUN_PREFIX) else None


def props_of(value) -> dict:
    """노드에 실린 props 를 dict 로 읽는다. 동기화 적용처럼 JSON 문자열도 풀고, 못 읽으면 빈 dict."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else {}
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def count(value) -> int:
    """실행 기록의 횟수(`step`·`asks`)를 음이 아닌 정수로 읽는다. 정수로 못 읽는 값은 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, float):
        value = int(value) if value.is_integer() else 0
    elif isinstance(value, str):
        text = value.strip()
        value = int(text) if text.isdecimal() and len(text) <= _COUNT_DIGITS else 0
    return max(value, 0) if isinstance(value, int) else 0


def number(value) -> int | float | None:
    """사용량 같은 수 하나를 읽는다. 유한한 수(bool 이 아닌 int·float)만 그대로, 나머지는 None.

    NaN·무한은 JSON 의 수가 아니고 `int()` 에서 예외를 낸다. 큰 정수는 float 로 바꾸지 않고 둔다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return value if isinstance(value, float) and math.isfinite(value) else None


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _flags(value) -> list[dict]:
    """능력 감사 표지: id 와 표지가 문자열인 dict 만. 다음 제출이 (id, 표지) 쌍을 집합에 넣는다."""
    return [v for v in _dicts(value)
            if isinstance(v.get("id"), str) and isinstance(v.get("flag"), str)]


def notes(value) -> list[dict]:
    """호스트 기록이 글을 대신한 자리의 참고: 단계 수와 보이는 한 줄인 글만 남긴다."""
    return [{"step": count(v.get("step")), "note": visible_line(v["note"])[:NOTE_MAX]}
            for v in _dicts(value) if isinstance(v.get("note"), str)]


def visible_line(text: str) -> str:
    """보이는 글자만 남긴 한 줄. 제어 문자와 서식 문자(Unicode Cc·Cf: NUL, ESC, 방향 바꿈 등)는 빼고,
    공백은 하나로 접는다. 결론의 한 줄처럼 사람이 그대로 읽는 짧은 글에 쓴다."""
    kept = "".join(ch if unicodedata.category(ch) not in ("Cc", "Cf") else " " for ch in text)
    return " ".join(kept.split())


def readable(props) -> dict:
    """실행 기록 props 를 읽는 쪽이 그대로 변환해도 되는 모양의 사본으로 읽는다. 원본은 그대로다.

    승인함(`api/runs.py`), 실행 목록, 이벤트(`api/events.py`), 알림(`run_notify`), 실행 프로토콜이
    모두 이것을 지난다. 기록 하나의 값이 수가 아니거나 목록이 아니어도 읽기가 예외로 멈추면 같은
    소유자의 다른 대기까지 보이지 않는다. 그래서 못 읽는 값은 0 이나 빈 목록으로 읽고, 사용량에는
    유한한 수만 남긴다.
    대기 중인 실행은 늘 dict 인 대기와 1 이상의 질문 번호를 갖는다: 사람이 그 번호로 답해 닫는다.
    대기의 행동은 문자열만 남으므로 보이는 행동과 승인되는 행동이 같은 목록이다."""
    out = dict(props_of(props))
    for key in ("step", "asks"):
        if key in out:
            out[key] = count(out[key])
    for key, read in (("cited_ids", _strings), ("capabilities_used", _strings),
                      ("approved", _strings), ("answered_actions", _strings),
                      ("capability_flags", _flags), ("record_notes", notes)):
        if key in out:
            out[key] = read(out[key])
    if "used" in out:
        used = out["used"] if isinstance(out["used"], dict) else {}
        out["used"] = {k: v for k, v in used.items() if number(v) is not None}
    # 결론은 보호되지 않아 도구 쓰기로도 바뀐다. 읽는 쪽은 아는 낱말과 글자인 한 줄만 본다.
    if "verdict" in out and out["verdict"] not in VERDICTS:
        out.pop("verdict")
    if "verdict_note" in out:
        if isinstance(out["verdict_note"], str):
            out["verdict_note"] = visible_line(out["verdict_note"])[:VERDICT_NOTE_MAX]
        else:
            out.pop("verdict_note")
    pending = out.get("pending")
    if out.get("state") == "waiting":
        out["asks"] = max(count(out.get("asks")), 1)
        if not isinstance(pending, dict):
            pending = out["pending"] = {}
    if isinstance(pending, dict):
        pending = out["pending"] = dict(pending)
        for key, read in (("actions", _strings), ("checks", _strings), ("requests", _dicts)):
            if key in pending:
                pending[key] = read(pending[key])
        if "question" in pending and not isinstance(pending["question"], str):
            pending.pop("question")
    return out


def writes(nodes) -> list[str]:
    """실행 기록의 프로토콜 상태 필드나 역할을 고치려는 노드 id 들(공백을 벗긴 id).

    도구로 `state`·`approved`·`answer` 를 바꿀 수 있으면 에이전트 자격이 사람만 답하는 대기를
    스스로 풀 수 있다. `props_patch` 로 `role`·`role_hash` 를 바꾸면 승인이 다른 역할의 정책과
    견주어진다. trace_ref 같은 나머지 필드는 그대로 `props_patch` 로 더한다. 사람을 기다리는 실행의
    카드(`CARD_KEYS`)는 저장된 행과 견주어야 하므로 여기가 아니라 쓰기 입구가 거절한다(`card_refusal`)."""
    blocked = []
    for node in nodes if isinstance(nodes, list) else []:
        rid = run_id(node.get("id")) if isinstance(node, dict) else None
        if rid is None:
            continue
        keys = set(props_of(node.get("props")))
        patched = set(props_of(node.get("props_patch")))
        if (keys | patched) & set(STATE_KEYS) or patched & set(ROLE_KEYS):
            blocked.append(rid)
    return blocked


def held(props) -> bool:
    """실행 프로토콜이 가진 실행 기록인가: 저장된 props 에 실행 상태(`state`)가 있다.

    프로토콜이 쓰는 기록은 늘 `state` 를 싣는다. 클라이언트가 쓰는 실행 기록(`vinc_client.record_run`)은
    싣지 않고, 도구 쓰기는 상태 키를 못 쓰므로(`writes`) 그런 기록이 프로토콜의 것이 되지도 않는다."""
    return "state" in props_of(props)


def whole_props_refusal(rid: str) -> str:
    """프로토콜이 가진 실행 기록에 props 를 통째로 쓰려는 쓰기를 거절하는 문장."""
    return (f"Refused: {rid} is a run record the run protocol keeps. A whole props would replace "
            f"its state ({', '.join(QUESTION_KEYS[:2])}, {', '.join(ANSWER_KEYS[:2])}, ...) and its "
            f"{', '.join(ROLE_KEYS)}, task and outcome. Send props_patch with the expected_rev you "
            f"read from vinc_node to change other keys. Nothing was written.")


def delete_refusal(rid: str) -> str:
    """끝나지 않은 실행(`unfinished`)을 지우려는 삭제를 거절하는 문장."""
    return (f"Refused: {rid} is a run that has not finished. It is working or waits for a "
            f"person's answer, and deleting it would take its question off the person's approvals "
            f"or erase the record of a person's answer. A waiting run closes when a person "
            f"declines it in the Approvals screen of the Vinc app or its question expires; a "
            f"working run closes when its host ends it with vinc_run_submit abandon:true. After "
            f"that the owner can delete it. Nothing was deleted.")


def card_changes(stored: dict, title, props) -> list[str]:
    """쓰기가 실행의 카드에서 바꾸는 이름들: 제목(`title`)과 `CARD_KEYS` 가운데 값이 달라지는 것.

    `stored` 는 저장된 행(`title`·`props`)이다. `title` 은 쓰려는 제목이고 None 이면 제목을 쓰지 않는다.
    `props` 는 쓰고 난 뒤의 props 이고 None 이면 props 를 쓰지 않는다. 실행 기록의 제목은 공백뿐이면
    빈 제목으로 저장되므로(`api/fragment.py`) 같은 규칙으로 견준다. 같은 값을 다시 보내면 빈 목록이다."""
    changed = []
    if title is not None:
        text = str(title)
        if (text if text.strip() else "") != (stored.get("title") or ""):
            changed.append("title")
    if props is not None:
        own, after = props_of(stored.get("props")), props_of(props)
        changed += [k for k in CARD_KEYS if after.get(k, _MISSING) != own.get(k, _MISSING)]
    return changed


def card_refusal(rid: str, fields: list[str]) -> str:
    """사람을 기다리는 실행의 카드를 바꾸려는 쓰기를 거절하는 문장."""
    return (f"Refused: {rid} waits for a person's answer, and the person reads its "
            f"{', '.join(fields)} on the approvals card to decide. The card stays as it was "
            f"asked until the person answers or the question expires; after that a write can "
            f"change it. Nothing was written.")


def keep_own(incoming, own, *, trusted: bool = False) -> tuple[dict, bool]:
    """동기화로 들어온 실행 기록의 props 에서 프로토콜 상태만 받는 스토어의 값으로 바꾼다.

    `own` 은 받는 스토어에 저장된 props 이고, 받는 스토어가 처음 보는 실행이면 None 이다.
    가진 실행은 들어온 상태 키를 버리고 저장된 상태 키를 그대로 싣는다(props 는 쓸 때 통째로
    바뀌므로 빼기만 하면 저장된 상태가 지워진다). 처음 보는 실행은 `trusted` 가 가른다:
    믿는 쪽(기기가 자기 허브에서 받을 때)이면 그대로, 아니면 `questions_only` 로 간다.
    프로토콜이 가진 실행(`held`)이면 역할(`ROLE_KEYS`)도 저장된 값을 남긴다: 승인은 그 역할의 정책과
    견준다. 저장된 실행이 사람을 기다리면(`waiting`) 카드(`CARD_KEYS`)도 남긴다: 사람이 읽고 답하는
    글이다. 나머지 키는 보통의 동기화 규칙을 따른다.
    돌려주는 것은 (바꾼 props, 들어온 상태·역할·카드가 남긴 값과 달랐는가)."""
    incoming = props_of(incoming)
    keys = STATE_KEYS
    if own is None:
        if trusted:
            return incoming, False
        kept = questions_only(incoming)
    else:
        own = props_of(own)
        if held(own):
            keys = STATE_KEYS + ROLE_KEYS
        if waiting(own):
            keys = keys + CARD_KEYS
        kept = {k: v for k, v in own.items() if k in keys}
    props = {k: v for k, v in incoming.items() if k not in keys}
    props.update(kept)
    differed = any(incoming.get(k, _MISSING) != kept.get(k, _MISSING) for k in keys)
    return props, differed


def _parse(text) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(str(text or ""), _STAMP).replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def expires_at(pending: dict) -> str:
    """요청의 만료 시각. 만료 필드가 없는 기록은 물은 때부터 `EXPIRES_DEFAULT_S` 로 센다."""
    if pending.get("expires_at"):
        return str(pending["expires_at"])
    asked = _parse(pending.get("asked_at"))
    return (asked + datetime.timedelta(seconds=EXPIRES_DEFAULT_S)).strftime(_STAMP) if asked else ""


def expired(pending: dict, now: datetime.datetime) -> bool:
    """요청이 `now` 에 만료됐는가. 만료 시각을 못 읽으면 만료되지 않았다."""
    until = _parse(expires_at(pending))
    return until is not None and now >= until


def waiting(props, now: datetime.datetime | None = None) -> bool:
    """사람이 아직 답할 수 있는 질문을 가진 실행인가: 대기 중이고 요청이 만료되지 않았다.

    만료된 질문에는 누구도 답할 수 없고 읽는 쪽은 막힌 채 끝난 실행으로 읽는다(`run_protocol.as_read`)."""
    read = readable(props)
    if read.get("state") != "waiting":
        return False
    return not expired(read.get("pending") or {}, now or datetime.datetime.now(datetime.timezone.utc))


def unfinished(props, now: datetime.datetime | None = None) -> bool:
    """끝나지 않은 실행인가. 도구 삭제와 믿지 않는 쪽의 삭제 표식은 이런 실행을 지우지 않는다.

    실행 프로토콜의 상태(`STATES`) 가운데 끝은 `DONE` 하나다. 일하는 중(working)인 실행은 끝나지 않았고,
    기다리는 중(waiting)인 실행은 요청이 만료되지 않았을 때만 끝나지 않았다(`waiting`): 만료된 질문에는
    누구도 답할 수 없고 읽는 쪽도 막힌 채 끝난 실행으로 읽는다. 상태가 없거나 모르는 값인 기록은 답
    (`ANSWER_KEYS`)을 실었으면 끝나지 않은 것으로 읽는다: 지우면 그 답의 기록이 사라진다. 답도 상태도
    없는 기록(클라이언트의 실행 기록)은 끝난 것이다."""
    read = readable(props)
    state = read.get("state")
    if state == DONE:
        return False
    if state == "waiting":
        return waiting(read, now)
    if state == "working":
        return True
    return any(read.get(k) not in (None, "", [], {}) for k in ANSWER_KEYS)


def _within_limit(pending: dict, now: datetime.datetime | None) -> dict:
    """받는 스토어의 시계로 질문의 때를 고친다: 물은 때는 지금보다 뒤일 수 없고(없거나 못 읽어도
    지금), 만료는 지금부터 `EXPIRES_MAX_S` 안이다(없거나 못 읽으면 그 상한)."""
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).replace(microsecond=0)
    limit = moment + datetime.timedelta(seconds=EXPIRES_MAX_S)
    asked = _parse(pending.get("asked_at"))
    if asked is None or asked > moment:
        pending["asked_at"] = moment.strftime(_STAMP)
    until = _parse(pending.get("expires_at"))
    if until is None or until > limit:
        pending["expires_at"] = limit.strftime(_STAMP)
    return pending


def questions_only(props, now: datetime.datetime | None = None) -> dict:
    """믿지 않는 쪽이 보낸 실행 상태에서 질문만 남긴다: 답 키는 없고 승인은 빈 목록이다.

    대기(`pending`)의 종류와 행동마다의 종류를 사람만 답하는 것으로 바꾼다. 보낸 쪽이 `ask_first`
    라고 적어 두면 에이전트 자격의 답이 그 대기를 승인할 수 있다.

    사람에게 보이는 것(`requests`, `question`)과 승인되는 것(`actions`)이 어긋나면 안 된다:
    답은 `actions` 를 승인하므로, 보낸 쪽이 `requests` 에 다른 행동을 적어 두면 사람이 본 적
    없는 행동이 승인된다. 그래서 `requests` 는 `actions` 에서 다시 만들고(같은 행동의 설명만
    옮긴다), 보낸 쪽이 쓴 질문 문장은 버린다(읽는 쪽이 `actions` 와 `checks` 로 다시 만든다).

    대기 중이라면서 대기가 dict 가 아니면 행동이 없는 사람만의 대기로 받는다: 승인함에 보여 사람이
    닫을 수 있고, 승인해도 승인되는 행동이 없다. 질문의 때는 `_within_limit` 로 받는 스토어의 시계에
    맞춘다(`now` 는 그 시계, 시험이 고정한다)."""
    props = props_of(props)
    kept = {k: v for k, v in props.items() if k in QUESTION_KEYS}
    pending = kept.pop("pending", None)
    if not isinstance(pending, dict) and kept.get("state") == "waiting":
        pending = {}
    if isinstance(pending, dict):
        actions = pending.get("actions")
        actions = (list(dict.fromkeys(a for a in actions if isinstance(a, str)))
                   if isinstance(actions, list) else [])
        reasons: dict[str, str] = {}
        sent = pending.get("requests")
        for r in sent if isinstance(sent, list) else []:
            if (isinstance(r, dict) and isinstance(r.get("action"), str)
                    and isinstance(r.get("reason"), str)):
                reasons.setdefault(r["action"], r["reason"])
        kept["pending"] = _within_limit({
            **{k: v for k, v in pending.items() if k not in ("question", "requests")},
            "actions": actions, "kind": _PERSON_ONLY,
            "requests": [{"action": a, "reason": reasons.get(a, ""), "kind": _PERSON_ONLY}
                         for a in actions]}, now)
    kept["approved"] = []
    return kept


def warning(ids: list[str]) -> str:
    """상태·역할·카드를 남겼거나 지우지 않은 실행들을 알리는 경고 한 줄. 결과의 `CODE` 필드에 전체 목록이 간다."""
    named = ", ".join(ids[:_NAMED_MAX])
    more = f" and {len(ids) - _NAMED_MAX} more" if len(ids) > _NAMED_MAX else ""
    return (f"{CODE}: kept this store's protocol state ({STATE_KEYS[0]}, "
            f"{', '.join(ANSWER_KEYS[:2])}, ...) and role on {len(ids)} run record(s): "
            f"{named}{more}. A run this store never saw keeps its question but no approval or "
            f"answer, a run that waits for a person keeps the title, {', '.join(CARD_KEYS[:3])}, "
            f"... the person reads, and a delete marker does not remove a run that has not "
            f"finished. A run's waiting, approval, answer and role change only through the run "
            f"protocol of the store that records them.")
