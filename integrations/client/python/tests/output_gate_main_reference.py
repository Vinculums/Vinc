"""시험 전용 기준: 이 브랜치가 갈라진 main 의 person_only 읽기를 그대로 옮긴 사본.

출시 모듈(vinc_client.output_gate)은 이 파일을 읽지 않는다. 생성 시험이 브랜치의 판정을 이 기준과 견주고,
말뭉치 시험이 이 기준이 기록된 main 판정과 같은지 본다. 이 파일의 정규식과 반복문은 고치지 않는다.
"""
from __future__ import annotations

import re
from typing import Iterable

_ACTIONS = {
    "merge": r"\bmerg(?:e|ed|ing)\b|\bland(?:s|ed|ing)?\b[^.]{0,40}\bon (?:main|master)\b|병합|머지",
    "commit": r"\bcommit(?:s|ted|ting)?\b|커밋",
    "push": r"\bpush(?:ed|ing)?\b|푸시",
    "edit": r"\bedit(?:s|ed|ing)? (?:the |this |that |a )?(?:file|code|diff|line|repo\w*|it)\b|\b(?:i will|i'll|let me) edit\b|\bapply (?:this|the) (?:code )?(?:diff|change|fix|patch)|\bchange (?:the )?line\b|파일을? 수정",
    "sign_off": r"\bsign(?:ed)?[- ]off\b|사인오프|서명|시인",
    "deploy": r"\bdeploy(?:ed|ing)?\b|배포",
    "english_value": r"en\.json|english (?:key|value|string)|영어 값|영문값|영어 파일",
    "graph_delete": r"vinc_delete\b|vinc_doc_delete\b",
    "graph_merge": r"vinc_merge_nodes\b",
    "migration": r"\bmigration\b|마이그레이션",
}
_DECISION_WORDS = {
    "sign_off": ("sign-off", "sign off", "signoff", "approv"),
    "keyframe_sign_off": ("sign-off", "sign off", "keyframe"),
    "enable_korean": ("language setting", "language on", "언어"),
    "accept_risk": ("risk accept", "accept the risk", "acceptable for now", "close the finding"),
    "accept_concept": ("accept", "approv"),
    "design_acceptance": ("accept", "approv"), "decision": ("decision", "decide", "approv"),
    "scope": ("scope",), "merge_scope": ("scope",),
    "pricing": ("pricing", "price", " tier"), "public_promise": ("promise",),
    "publish": ("publish",), "publish_book": ("publish",), "book_argument": ("book argument",),
    "wave_approval": ("wave", "approv"), "ratify_schema": ("ratif", "approv"),
    "ratification": ("ratif", "approv"), "metric_ratification": ("ratif", "approv"),
    "migration": ("migration",), "pk_namespacing_migration": ("migration",),
    "state_change": ("state chang", "terraform apply", "apply the plan"),
    "credential_rotation": ("credential", "rotat"),
    "cloud_command": ("cloud", "az command", "replica", "container app"),
    "token_rotation": ("token", "rotat"),
    "signing_key_custody": ("signing key",), "operator_step": ("operator",),
    "bo_operator_step_up": ("step-up", "/bo"), "estimate_assumptions": ("assumption",),
    "override_failed_check": ("override",), "choose_name": ("name",),
    "send": ("send",), "push_tag": ("tag", "push"), "deletion": ("delet",),
}
_REFUSAL = re.compile(r"\b(?:cannot|can't|can not|won't|will not|not allowed|forbid\w*|must not|unable|not permitted|i do not|i don't|omit\w*|left out|not included|excluded|dropped)\b|할 수 없|못 합니다|못합니다|금지|하지 않습니다|불가", re.I)
_CLAIM = re.compile(r"\b(?:i|we)(?:\s+have|'ve)?\s+(?:merged|edited|committed|pushed|applied|signed off|deployed|changed|updated)\b|\b(?:has|have) been (?:merged|committed|pushed|signed off|deployed)\b|\b(?:is|are|was|were) (?:now )?(?:merged|committed|pushed|deployed)\b|^[-*]\s*(?:merged|committed|pushed|deployed|published)\b|(?:수행|완료|커밋|병합|머지|반영|푸시|사인오프|서명|배포|변경|변경 요청|수정)(?:을|를)?\s?(?:했|하였|되었|됐|해 ?두었|해 ?뒀|해 ?놓았|해 ?놨)(?:습니다|어요|다|고)?|고쳤", re.I)
_NEGATED = re.compile(r"\b(?:nothing|not|never|no)\b[^.;]{0,30}\b(?:merged|committed|pushed|deployed|published|passed|sent|deleted)\b|(?:하지|되지) 않았|안 했|아직 [^.]{0,20}않", re.I)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
_PAST_FORMS = re.compile(r"\b(?:merged|pushed|committed|deployed|published|sent|deleted|landed|applied|rotated|migrated|tagged)\b", re.I)
_INTENT = re.compile(r"\b(?:i will|i'll|i'd|i would|let me|i am going to|i'm going to|we will|we'll|i plan to)\b|제가 [^.]{0,30}(?:하겠|할게|해 드릴|드리겠)|하겠습니다|할게요", re.I)
_HANDOFF = re.compile(r"\b(?:please|you (?:can|should|need to|must|may)|(?:your|a person's|human) (?:approval|call|decision|sign-?off)|(?:needs?|requires?|awaiting|waiting (?:on|for)) (?:your|a person|a human|human|someone|approval|sign-?off)|a (?:person|human|maintainer|owner) (?:needs to|has to|must|should)|next step|when you(?:'re| are)? ready|as soon as|(?:once|before|after|until) (?:you|a person|approval|it'?s approved)|recommend\w*|ready (?:for|to)|go ahead|(?:ask|request|have|get) (?:a |your )?(?:human|person|team member|maintainer|someone|owner))\b|사람이|담당자|승인|결정|주세요|주시면|주시기|바랍니다|해야", re.I)
_IMPERATIVE = re.compile(r"^(?:[-*]\s*)?(?:merg(?:e|ing)|push(?:ing)?|deploy(?:ing)?|commit(?:ting)?|run|apply(?:ing)?|publish(?:ing)?|send(?:ing)?|delet(?:e|ing)|land(?:ing)?|rotat(?:e|ing)|tag(?:ging)?)\b(?=\s+(?:it|this|that|these|those|them|the|a|an|now|pr\b|#|`|v?\d|main|origin)|\s*[.!]|$)", re.I)
_ID = re.compile(r"\b(?:concept|decision|record|episode|domain|project|claim):[A-Za-z0-9._\-/]+")
_PERSON_FIELD = re.compile(r"^\s*PERSON[- ]ONLY:\s*(.+)$", re.M | re.I)
_MARKER = re.compile(r"\b(?:approv\w*|sign[- ]?off|needed|required|requires?|request\w*|await\w*|pending|decide|decision|recommend\w*|please|ask|(?:until|before|once|after) (?:you|a person|approval|it'?s approved)|when you(?:'re| are)? ready|as soon as)\b|승인|결정|사인오프|서명", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def main_person_only(answer: str, actions: Iterable[str]) -> tuple[str, ...]:
    """main 의 person_only 검사가 이 답에서 거는 사람 전용 행동들(정렬). 비면 끼어들지 않는다.

    역할 계약의 사람 전용 행동(actions)만 본다. person_only_requests 와 self_authored 는 쓰지 않는다.
    """
    actions = set(actions)
    body = _ID.sub("<id>", answer)
    pending: set[str] = set()
    for sentence in _sentences(body):
        said = _NEGATED.sub("", _QUOTED.sub("", sentence))
        if _REFUSAL.search(sentence) or _CLAIM.search(said):
            continue
        present = _PAST_FORMS.sub("", said)
        agency = bool(_INTENT.search(present) or _IMPERATIVE.search(present))
        handoff = bool(_HANDOFF.search(present) or _MARKER.search(present))
        for action in actions:
            direct = (action in _ACTIONS and (agency or handoff)
                      and re.search(_ACTIONS[action], present, re.I))
            decision = ((agency or _MARKER.search(present)) and any(
                word in present.lower() for word in _DECISION_WORDS.get(action, ())))
            if direct or decision:
                pending.add(action)
    for match in _PERSON_FIELD.finditer(body):
        value = match.group(1).strip().lower()
        if value and not value.startswith(("none", "n/a", "-")):
            for action in actions:
                if action in value or any(word in value for word in _DECISION_WORDS.get(action, ())):
                    pending.add(action)
    return tuple(sorted(pending))
