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

``store/`` 는 맨 아래 층이라(``scripts/gate_ownership.py`` 의 ``LAYER_BANS``) ``api`` 와
``vinc_mcp`` 가 둘 다 이것을 가져다 쓴다.
"""
from __future__ import annotations

import datetime
import json

RUN_PREFIX = "episode:run-"
#: 질문 쪽 상태: 실행이 지금 무엇을 묻는가. 실행 프로토콜의 출력 단계(`_output`)와 작업 닫기
#: (`run_tasks.cancel`)가 쓴다. 닫힌 작업 표시는 답을 막기만 하고 승인을 만들지 않는다.
QUESTION_KEYS = ("state", "pending", "asks", "last_asked_at", "task_cancelled")
#: 답 쪽 상태: 누가 무엇에 답했고 무엇이 승인됐는가. 실행 프로토콜의 `_answer` 가 채운다.
ANSWER_KEYS = ("approved", "answer", "answered_at", "answered_actions", "answered_by", "answer_via",
               "answer_text")
#: 실행 기록의 프로토콜 상태 필드. 도구 쓰기로는 바꾸지 못하고, 가진 실행에는 동기화로 건너가지 않는다.
STATE_KEYS = QUESTION_KEYS + ANSWER_KEYS
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
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
#: 정수로 읽는 횟수 문자열의 최대 자릿수. 더 긴 문자열은 수로 읽지 않는다.
_COUNT_DIGITS = 18


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


def _strings(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def readable(props) -> dict:
    """실행 기록 props 를 읽는 쪽이 그대로 변환해도 되는 모양의 사본으로 읽는다. 원본은 그대로다.

    승인함(`api/runs.py`), 실행 목록, 이벤트(`api/events.py`), 알림(`run_notify`), 실행 프로토콜이
    모두 이것을 지난다. 기록 하나의 값이 수가 아니거나 목록이 아니어도 읽기가 예외로 멈추면 같은
    소유자의 다른 대기까지 보이지 않는다. 그래서 못 읽는 값은 0 이나 빈 목록으로 읽는다.
    대기 중인 실행은 늘 dict 인 대기와 1 이상의 질문 번호를 갖는다: 사람이 그 번호로 답해 닫는다.
    대기의 행동은 문자열만 남으므로 보이는 행동과 승인되는 행동이 같은 목록이다."""
    out = dict(props_of(props))
    for key in ("step", "asks"):
        if key in out:
            out[key] = count(out[key])
    for key, read in (("cited_ids", _strings), ("capabilities_used", _strings),
                      ("approved", _strings), ("answered_actions", _strings),
                      ("capability_flags", _dicts)):
        if key in out:
            out[key] = read(out[key])
    if "used" in out:
        used = out["used"] if isinstance(out["used"], dict) else {}
        out["used"] = {k: v for k, v in used.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
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
    """실행 기록의 프로토콜 상태 필드를 고치려는 노드 id 들(공백을 벗긴 id).

    도구로 `state`·`approved`·`answer` 를 바꿀 수 있으면 에이전트 자격이 사람만 답하는 대기를
    스스로 풀 수 있다. host·trace_ref 같은 나머지 필드는 그대로 `props_patch` 로 더한다."""
    blocked = []
    for node in nodes if isinstance(nodes, list) else []:
        rid = run_id(node.get("id")) if isinstance(node, dict) else None
        if rid is None:
            continue
        keys = set()
        for field in ("props", "props_patch"):
            keys |= set(props_of(node.get(field)))
        if keys & set(STATE_KEYS):
            blocked.append(rid)
    return blocked


def keep_own(incoming, own, *, trusted: bool = False) -> tuple[dict, bool]:
    """동기화로 들어온 실행 기록의 props 에서 프로토콜 상태만 받는 스토어의 값으로 바꾼다.

    `own` 은 받는 스토어에 저장된 props 이고, 받는 스토어가 처음 보는 실행이면 None 이다.
    가진 실행은 들어온 상태 키를 버리고 저장된 상태 키를 그대로 싣는다(props 는 쓸 때 통째로
    바뀌므로 빼기만 하면 저장된 상태가 지워진다). 처음 보는 실행은 `trusted` 가 가른다:
    믿는 쪽(기기가 자기 허브에서 받을 때)이면 그대로, 아니면 `questions_only` 로 간다.
    나머지 키는 보통의 동기화 규칙을 따른다.
    돌려주는 것은 (바꾼 props, 들어온 상태가 남긴 상태와 달랐는가)."""
    incoming = props_of(incoming)
    if own is None:
        if trusted:
            return incoming, False
        kept = questions_only(incoming)
    else:
        kept = {k: v for k, v in props_of(own).items() if k in STATE_KEYS}
    props = {k: v for k, v in incoming.items() if k not in STATE_KEYS}
    props.update(kept)
    differed = any(incoming.get(k, _MISSING) != kept.get(k, _MISSING) for k in STATE_KEYS)
    return props, differed


def _parse(text) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(str(text or ""), _STAMP).replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


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
    """상태를 남긴 실행들을 알리는 경고 한 줄. 결과의 `CODE` 필드에 전체 목록이 함께 간다."""
    named = ", ".join(ids[:_NAMED_MAX])
    more = f" and {len(ids) - _NAMED_MAX} more" if len(ids) > _NAMED_MAX else ""
    return (f"{CODE}: kept this store's protocol state ({STATE_KEYS[0]}, "
            f"{', '.join(ANSWER_KEYS[:2])}, ...) on {len(ids)} run record(s): {named}{more}. "
            f"A run this store never saw keeps its question but no approval or answer. A run's "
            f"waiting, approval and answer change only through the run protocol of the store "
            f"that records them.")
