"""Host-side role checks. No model calls, graph writes, or automatic approvals.

Text rules retain the original agent-lab gate's node-ID, refusal and diff handling.
Unlike that prototype, policy comes from typed checks and action IDs, never reason prose.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


# 노드 ID·거절 문장·삭제된 diff 줄은 행동 제안으로 판정하지 않는다.
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
_KNOWN_ACTIONS = frozenset(_ACTIONS) | frozenset(_DECISION_WORDS)
_REFUSAL = re.compile(r"\b(?:cannot|can't|can not|won't|will not|not allowed|forbid\w*|must not|unable|not permitted|i do not|i don't|omit\w*|left out|not included|excluded|dropped)\b|할 수 없|못 합니다|못합니다|금지|하지 않습니다|불가", re.I)
_SELF = re.compile(r"\b(?:i will|i'll|let me|i am going to|i'm going to|i have|i've)\b|제가 [^.]{0,30}(?:하겠|할게|해 드릴|했)", re.I)
_CLAIM = re.compile(r"\b(?:i|we)(?:\s+have|'ve)?\s+(?:merged|edited|committed|pushed|applied|signed off|deployed|changed|updated)\b|\b(?:has|have) been (?:merged|committed|pushed|signed off|deployed)\b|\b(?:is|are|was|were) (?:now )?(?:merged|committed|pushed|deployed)\b|^[-*]\s*(?:merged|committed|pushed|deployed|published)\b|(?:수행|완료|커밋|병합|머지|반영|푸시|사인오프|서명|배포|변경|변경 요청|수정)(?:을|를)?\s?(?:했|하였|되었|됐|해 ?두었|해 ?뒀|해 ?놓았|해 ?놨)(?:습니다|어요|다|고)?|고쳤", re.I)
_UNWITNESSED = re.compile(r"\b(?:pass(?:es|ed)? (?:all )?(?:the )?gate|gate scripts? pass|ensured gate|wrote [^.]{0,40}(?:into|to) (?:the )?(?:vinc )?graph|documented in the (?:vinc )?graph|covered with (?:unit )?tests|tests? pass(?:es|ed)?|(?:i|we) ran (?:the )?(?:[\w-]+ )?(?:suite|tests?)|\d[\d,]* passed)\b|^[-*]\s*(?:tests?|gates?|checks?)\s*:[^.]*\bpass(?:es|ed)?\b", re.I)
# 부정·인용·남의 일은 이 실행의 완료 주장이나 사람 전용 행동 제안이 아니다.
# 부정은 쉼표·and·but 을 넘지 않는다: "did not wait and merged" 의 merged 는 부정되지 않는다.
_NEGATED = re.compile(r"\b(?:nothing|not|never|no)\b(?:(?!\b(?:and|but|then|so)\b)[^.;,]){0,30}\b(?:merged|committed|pushed|deployed|published|passed|sent|deleted)\b|(?:하지|되지) 않았|안 했|아직 [^.]{0,20}않", re.I)
_ATTRIBUTED = re.compile(r"\b(?:reports?|says|said|according to)\b(?: that)?", re.I)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
_PAST_FORMS = re.compile(r"\b(?:merged|pushed|committed|deployed|published|sent|deleted|landed|applied|rotated|migrated|tagged)\b", re.I)
# 사람 전용 행동을 이 답이 직접 하려 하거나(의도), 사람에게 넘기거나(인계), 시키는(명령형) 문장만 본다.
_INTENT = re.compile(r"\b(?:i will|i'll|i'd|i would|let me|i am going to|i'm going to|we will|we'll|i plan to)\b|제가 [^.]{0,30}(?:하겠|할게|해 드릴|드리겠)|하겠습니다|할게요", re.I)
_HANDOFF = re.compile(r"\b(?:please|you (?:can|should|need to|must|may)|(?:your|a person's|human) (?:approval|call|decision|sign-?off)|(?:needs?|requires?|awaiting|waiting (?:on|for)) (?:your|a person|a human|human|someone|approval|sign-?off)|a (?:person|human|maintainer|owner) (?:needs to|has to|must|should)|next step|when you(?:'re| are)? ready|as soon as|(?:once|before|after|until) (?:you|a person|approval|it'?s approved)|recommend\w*|ready (?:for|to)|go ahead|(?:ask|request|have|get) (?:a |your )?(?:human|person|team member|maintainer|someone|owner))\b|사람이|담당자|승인|결정|주세요|주시면|주시기|바랍니다|해야", re.I)
_IMPERATIVE = re.compile(r"^(?:[-*]\s*)?(?:merg(?:e|ing)|push(?:ing)?|deploy(?:ing)?|commit(?:ting)?|run|apply(?:ing)?|publish(?:ing)?|send(?:ing)?|delet(?:e|ing)|land(?:ing)?|rotat(?:e|ing)|tag(?:ging)?)\b(?=\s+(?:it|this|that|these|those|them|the|a|an|now|pr\b|#|`|v?\d|main|origin)|\s*[.!]|$)", re.I)
# 조건절("when a tag is pushed")은 일어난 일의 보고가 아니다. 완료 주장을 찾기 전에 지운다.
_CONDITIONAL = re.compile(r"\b(?:when|whenever|if|once|after|until|before)\b[^,.;]*", re.I)
_REPO = re.compile(r"\bVinc-homepage\b|\bVinc\b")
_PAST_EDIT = re.compile(r"^(?:[-*]\s*)?(?:updated|edited|changed|wrote|added|removed|created)\s+(?:the\s+)?(?:sync\s+scripts?|scripts?|files?|code|config(?:uration)?|tests?)\b", re.I)
_ID = re.compile(r"\b(?:concept|decision|record|episode|domain|project|claim):[A-Za-z0-9._\-/]+")
_PERSON_FIELD = re.compile(r"^\s*PERSON[- ]ONLY:\s*(.+)$", re.M | re.I)
# 때를 나타내는 말(before·once·until)은 사람이나 승인을 가리킬 때만 표지다. "Before 0.8.4" 같은 이력은 아니다.
_MARKER = re.compile(r"\b(?:approv\w*|sign[- ]?off|needed|required|requires?|request\w*|await\w*|pending|decide|decision|recommend\w*|please|ask|(?:until|before|once|after) (?:you|a person|approval|it'?s approved)|when you(?:'re| are)? ready|as soon as)\b|승인|결정|사인오프|서명", re.I)
_KEYLINE = re.compile(r'^\s*[-*]?\s*"?([A-Za-z][\w-]*(?:\.[\w-]+)+)"?\s*(?:=|:)\s*"?(.*?)"?,?\s*$')
_COPY_RULES = {
    "variable_particle": re.compile(r"\{[^}]+\}(?:을|를|이|가|은|는|에서|에게|에|으로|로|와|과|의|도|만|이나|나)(?![(（])"),
    "em_dash": re.compile("—"), "second_person": re.compile(r"당신|귀하|회원님|여러분"),
    "object_honorific": re.compile(r"되셨|드리|드립|드릴|드려|께서"),
}
_KO_COMMENT = re.compile(r"(korean|한국어|한글)[^.\n]{0,40}(comment|주석)|(comment|주석)[^.\n]{0,40}(korean|한국어|한글)", re.I)
_HEDGE = re.compile(r"\b(?:fine|allowed|allows|acceptable|okay|ok|not a defect|no [\w ]{0,20}needed|per (?:the )?rules?|forbids flagging|is allowed)\b|문제없|허용", re.I)
_DEFECT = re.compile(r"\b(?:defect|violat\w*|should be (?:in )?english|must be (?:in )?english|translate|issue)\b", re.I)
_REQUIRED = frozenset({"person_only", "side_effect_evidence", "handoff_manifest"})

# 바깥으로 나가는 사람 전용 행동 다섯은 언급 하나하나를 절 단위로 읽는다(부정·미룸·주장·요청·모름).
# 나머지 사람 전용 행동은 fails() 의 문장 단위 규칙 그대로다.
_ACTS = ("merge", "migration", "push_tag", "publish", "send")
# 절 경계. 부정과 미룸은 이 경계를 넘지 않는다. or·nor 는 부정을 나눠 가지므로 경계가 아니다.
_CLAUSE = re.compile(r"\s*[,:()]\s*|\s+(?=(?:and|but|so|then|while|whereas|because|although|unless|if|when|whenever|once|after|before|until)\b)|(?<=고)\s+|(?<=지만)\s+|(?<=는데)\s+", re.I)
_SUBORDINATE = re.compile(r"^(?:if|unless|when|whenever|once|after|before|until)\b", re.I)
# 같은 절 안의 부정 표지. "not only" 는 부정이 아니다.
_NEG = re.compile(r"\b(?:not(?!\s+only\b)|never|no|none|nothing|nobody|without|cannot|neither|nor|unable|refuse[sd]?|forbid\w*)\b|n't\b|(?:지|진)\s*(?:않|못|마)|\s안\s?(?:함|했|해|하|됩|돼|되|됨)|못\s?(?:했|함|합)|말\s?것|마세요|금지|불가|없이", re.I)
# 행동을 사람이 맡는다고 적은 평서문(미룸). 독자에게 시키거나 의무를 지우는 말이 있으면 미룸이 아니다.
_ACTOR = re.compile(r"\b(?:owners?|maintainers?|a person|a human|the person|the human)\b|\bowner's\b|소유자|오너|사람이|사람의|사람 몫|담당자", re.I)
_OBLIGE = re.compile(r"\b(?:must|needs? to|has to|have to|should|ought to|please|you|your)\b|해야|주세요|주시|바랍니다|부탁", re.I)
_ASK = re.compile(r"\b(?:please|you|your)\b|주세요|주시면|주시기|바랍니다|부탁", re.I)
_MENTION = {
    "merge": re.compile(r"(?<!\w)merg(?:e|es|ed|ing)\b(?![- ](?:conflicts?|commits?|base)\b)(?!\s+(?:origin/)?(?:main|master)\s+into\b)|\bland(?:s|ed|ing)?\b[^.]{0,40}\bon (?:main|master)\b|병합|머지", re.I),
    "migration": re.compile(r"\bmigrat\w*|마이그레이션", re.I),
    "push_tag": re.compile(r"\bpush(?:es|ed|ing)?\b|푸시|\btag\w*|태그", re.I),
    "publish": re.compile(r"publish|게시(?!판|물|글)|발행|퍼블리시|출간", re.I),
    "send": re.compile(r"send|\bsent\b|(?<!내)보내|보냈|발송|전송", re.I),
}
# 보내기는 메시지를 보낼 때만 send 다(요청 본문·헤더를 보내는 코드 설명은 아니다). 영어 send 는 목적어 없이도 언급이다.
_MESSAGE = re.compile(r"\b(?:e-?mails?|mail|messages?|repl(?:y|ies)|dms?|notifications?|newsletters?|invit\w*|announcements?|slack|letters?)\b|\bto (?:the (?:owner|team|channel)|#|@)|메일|메시지|메세지|답장|알림|공지|슬랙|문자|초대", re.I)
_PAST = r"(?:했|하였|됐|되었|완료|끝냈|해 ?(?:두었|뒀|놓았|놨))"
_NOT_PERFECT = r"(?<!\bbe )(?<!\bis )(?<!\bare )(?<!\bget )(?<!\bgets )"
_ACT_CLAIM = {
    "merge": re.compile(_NOT_PERFECT + r"\bmerged\b|\blanded\b|(?:머지|병합)\S{0,3}\s?" + _PAST, re.I),
    "migration": re.compile(r"\b(?:ran|applied|executed|performed|completed|finished)\b[^,;]{0,30}\bmigrations?\b|\bmigrations?\b[^,;]{0,20}\b(?:ran|was (?:run|applied)|has been (?:run|applied)|completed)\b|\bmigrated\b|마이그레이션[^.,;]{0,15}(?:실행|적용|돌렸|완료|했)", re.I),
    "push_tag": re.compile(_NOT_PERFECT + r"\bpushed\b|\btagged\b(?=[^,;]{0,30}(?:\bv?\d+\.\d+|\brelease\b|\bHEAD\b|\bcommit\b))|푸시\S{0,3}\s?" + _PAST + r"|태그[^.,;]{0,15}(?:달았|만들었|생성했|붙였|찍었|올렸)", re.I),
    "publish": re.compile(_NOT_PERFECT + r"\bpublished\b|(?:게시|발행|퍼블리시|출간)\S{0,3}\s?" + _PAST, re.I),
    "send": re.compile(_NOT_PERFECT + r"\bsent\b|보냈|(?:발송|전송)\S{0,3}\s?" + _PAST, re.I),
}
# 이 절에서 시험용·로컬임이 보이는 마이그레이션은 라이브 데이터 주장이 아니다.
_LOCAL = re.compile(r"\b(?:tests?|temp|tmp|fixtures?|in-memory|dry[- ]run|local)\b|테스트|임시", re.I)
# push 의 대상: 태그·버전이 보이면 push_tag, 작업 브랜치가 보이면 아님, 둘 다 안 보이거나 main 이면 모른다(push_tag 로 읽는다).
_TAGGISH = re.compile(r"\btags?\b|--tags|\bv?\d+\.\d+(?:\.\d+)*\b|태그", re.I)
_MAINISH = re.compile(r"\b(?:to|origin|into)\s+(?:main|master)\b|\bmain branch\b", re.I)
_BRANCHISH = re.compile(r"\bbranch(?:es)?\b|브랜치|\bcommits?\b|\bchanges\b|\bthe (?:fix|work|patch)\b|\bHEAD\b|-u\s+origin\s+\S+|\b(?:fix|feat|chore|docs|test|refactor|agents|ci|perf|build|style)/[\w./-]+", re.I)
_PUSH_VERB = re.compile(r"\bpush(?:es|ed|ing)?\b|푸시", re.I)
# 주장의 주어: 없거나(보고체), I·we 거나, 완료 수동(has been·was)이다. 남의 이름이 주어면 이 실행의 주장이 아니다.
_SELF_SUBJECT = re.compile(r"\s*(?:[-*\d.)]+\s*)?(?:(?:and|then|so|but)\s+)?(?:(?:i|we)(?:'ve|\s+have|\s+had|\s+also|\s+just|\s+already|\s+then|\s+successfully)*\s+)?", re.I)
_PERFECT_PASSIVE = re.compile(r"\b(?:(?:has|have|had)\s+been|was|were)\s+(?:\w+\s+)?$", re.I)
_OTHER_KO = re.compile(r"(?:님|씨)(?:이|가|께서)|께서")
_LEAD = re.compile(r"^(?:and|but|so|then)\s+", re.I)
# 완료 주장 검사는 세미콜론과 but 에서 나눠 읽는다. 한 구절의 거절이 다른 구절의 주장을 가리지 않는다.
_PART = re.compile(r";|,?\s+but\s+", re.I)


def _clauses(segment: str) -> list[tuple[str, bool]]:
    """(절, 종속절인가). if·when·once·after·before·until 로 여는 절의 행동은 일어난 일의 보고가 아니다."""
    return [(part.strip(), bool(_SUBORDINATE.match(part.strip())))
            for part in _CLAUSE.split(segment) if part.strip()]


def _push_target(clauses: list[tuple[str, bool]], i: int) -> str:
    """push 한 것이 무엇인가: tag · branch · unclear. 대상은 그 절과, and 로 이어진 다음 절까지에서 찾는다."""
    window = clauses[i][0]
    for clause, _ in clauses[i + 1:]:
        if not clause.lower().startswith("and ") or _PUSH_VERB.search(clause):
            break
        window += " " + clause
    if _TAGGISH.search(window):
        return "tag"
    if _MAINISH.search(window) or not _BRANCHISH.search(window):
        return "unclear"
    return "branch"


def _claimed(action: str, clause: str) -> bool:
    """이 절이 이 실행이 그 행동을 했다고 보고하는가."""
    for match in _ACT_CLAIM[action].finditer(clause):
        if re.match(r"[가-힣]", match.group(0)) or re.match(r"[가-힣]", match.group(0)[-1:]):
            if not (_OTHER_KO.search(clause) or _ACTOR.search(clause)):
                return True
            continue
        before, after = clause[:match.start()], clause[match.end():]
        if (_SELF_SUBJECT.fullmatch(before) or match.group(0).lower().startswith("migration")
                or (_PERFECT_PASSIVE.search(before) and not re.search(r"\bby\b", after, re.I))):
            return True
    return False


def _act_findings(sentence: str, actions: Iterable[str]) -> list[tuple[str, str, str]]:
    """바깥으로 나가는 사람 전용 행동의 (행동, 절, 종류). 종류는 claim · request · unclear 다.

    한 절 안에 부정 표지가 있으면 그 절의 언급은 세지 않는다. 사람이 맡는다는 평서문(미룸)도 세지 않는다.
    과거형 보고는 주장이고, 이 답이 하려 하거나 넘기거나 시키는 문장은 요청이다. push 의 대상을
    읽지 못하면 push_tag 로 센다(unclear).
    """
    text = _QUOTED.sub(" ", sentence)
    agency = bool(_INTENT.search(text))
    handoff = bool(_HANDOFF.search(text) or _MARKER.search(text))
    asked = bool(_ASK.search(text))
    out: list[tuple[str, str, str]] = []
    for segment in text.split(";"):
        clauses = _clauses(segment)
        for i, (clause, subordinate) in enumerate(clauses):
            for action in actions:
                found = [m.group(0) for m in _MENTION[action].finditer(clause)]
                if not found:
                    continue
                target = "act"
                if action == "push_tag" and all(_PUSH_VERB.fullmatch(word) for word in found):
                    target = _push_target(clauses, i)
                    if target == "branch":
                        continue
                if action == "send" and not _MESSAGE.search(clause) and not any(
                        "send" in word.lower() for word in found):
                    continue
                if _NEG.search(clause):
                    continue
                kind = "unclear" if target == "unclear" else None
                if (not subordinate and _claimed(action, clause)
                        and not (action == "migration" and _LOCAL.search(clause))
                        and not (action == "send" and not _MESSAGE.search(clause))):
                    out.append((action, clause, kind or "claim"))
                    continue
                if _ACTOR.search(clause) and not _OBLIGE.search(clause) and not asked:
                    continue
                if subordinate:
                    # 쉼표 없는 "Once it lands I'll ..." 는 주절이 종속절 안에 붙어 있다. 그 안의 의도는 센다.
                    request = bool(_HANDOFF.search(clause) or _MARKER.search(clause) or _ASK.search(clause)
                                   or _INTENT.search(clause))
                else:
                    request = agency or handoff or bool(_IMPERATIVE.search(_LEAD.sub("", clause)))
                if request:
                    out.append((action, clause, kind or "request"))
    return out


_KIND_TEXT = {
    "claim": "reads as a report that this person-only action happened",
    "request": "asks for this person-only action or offers to take it",
    "unclear": ("mentions a push whose target is not shown to be a work branch, so it is read as "
                "push_tag (name the branch, as in 'pushed branch fix/x', if that is what it was)"),
    "field": "names it on a PERSON-ONLY line",
    "requested": "is a person-only action the host asked for in person_only_requests",
}


def _message(finding: Mapping[str, str]) -> str:
    """거절 한 건을 한 문장으로: 검사, 행동 id, 걸린 구절, 고치는 법."""
    check, action, phrase = finding["check"], finding["action"], finding["phrase"]
    head = f'{check} ({action}): "{phrase}"'
    if finding["kind"] in _KIND_TEXT:
        return (head + " " + _KIND_TEXT[finding["kind"]] + ". A person answers before the run goes on; "
                "if the action did not happen and is not asked for, say so in the same clause "
                "(for example 'did not merge').")
    if finding["kind"] == "unproven":
        return (head + " claims a side effect with no host proof. Accepted proof is a ToolProof the host "
                f"attaches from its own observed call: action='{finding['proof']}', target=<what it ran>, "
                "call_id=<its call id>, success=True, claim=<this whole sentence, exactly>. A channel with no "
                "host proof (vinc_run_submit over MCP) cannot carry one: restate the sentence as what was "
                "attempted and where to read the result.")
    if finding["kind"] == "manifest":
        return f"{check}: {phrase} is missing, incomplete, or cites a source that does not resolve."
    if finding["kind"] == "check":
        return f"{check}: this check failed."
    return head + " is forbidden for this role."


@dataclass(frozen=True)
class ToolProof:
    """Construct from a host-observed terminal call, never from model text or JSON.

    target names the exact command, resource, or package the host verified.
    It must describe this run. This library cannot authenticate arbitrary caller data.
    """
    action: str
    target: str
    call_id: str
    success: bool
    claim: str = ""


@dataclass(frozen=True)
class GateResult:
    verdict: str
    failed_checks: tuple[str, ...] = ()
    person_only_actions: tuple[str, ...] = ()
    evidence_sentences: tuple[str, ...] = ()
    #: 걸린 것 하나마다 {check, action, kind, phrase, message}. 같음 비교와 해시에는 들지 않는다.
    findings: tuple[dict[str, str], ...] = field(default=(), compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "failed_checks": list(self.failed_checks),
                "person_only_actions": list(self.person_only_actions),
                "evidence_sentences": list(self.evidence_sentences),
                "findings": [dict(f) for f in self.findings]}


class OutputGateStopped(RuntimeError):
    """Blocked output or a pending person decision; contains no model output."""
    def __init__(self, result: GateResult) -> None:
        self.result = result
        super().__init__(f"Output gate: {result.verdict}")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _rendered(answer: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    lines = answer.splitlines()
    for i, line in enumerate(lines):
        match = _KEYLINE.match(line)
        if match:
            value = match.group(2).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            if value:
                out.append((match.group(1), value))
    for value in re.findall(r"[\"“'']([^\"”'']*[가-힣][^\"”'']*)[\"”'']", answer):
        if not any(value == v for _, v in out):
            out.append(("(quoted)", value))
    return out


def _check_valid(check: Any) -> bool:
    if not isinstance(check, dict) or not isinstance(check.get("type"), str):
        return False
    kind = check["type"]
    if kind in _REQUIRED or kind == "korean_comment_allowed":
        return set(check) == {"type"}
    if kind == "command_success":
        repos = check.get("repos")
        return (set(check) == {"type", "command", "repos"} and isinstance(check["command"], str)
                and bool(check["command"].strip()) and len(check["command"]) <= 4096
                and isinstance(repos, list) and bool(repos)
                and all(isinstance(r, str) and r in {"Vinc", "Vinc-homepage"} for r in repos)
                and len(set(repos)) == len(repos))
    if kind in {"forbidden_actions", "rendered_copy"}:
        key, allowed = (("actions", frozenset(_ACTIONS)) if kind == "forbidden_actions"
                        else ("rules", frozenset(_COPY_RULES) | {"english_value"}))
        items = check.get(key)
        return (set(check) == {"type", key} and isinstance(items, list) and bool(items)
                and all(isinstance(x, str) and x in allowed for x in items)
                and len(set(items)) == len(items))
    return False


def _gates_valid(gates: Any) -> bool:
    return (isinstance(gates, list) and len(gates) <= 64
            and all(isinstance(g, dict) and set(g) == {"id", "check", "on_fail"}
                    and isinstance(g["id"], str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", g["id"])
                    and isinstance(g["on_fail"], str) and g["on_fail"] in {"block", "interrupt"}
                    and _check_valid(g["check"])
                    for g in gates)
            and len({g["id"] for g in gates}) == len(gates))


def preserve_required_route(base: Iterable[tuple[str, str]],
                            override: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """An override may add paths but may not bypass a required gate-runner hop.

    Retaining edges while adding a direct shortcut is also a bypass. Therefore
    role-route overrides must retain the entire base route; additions can only
    start at previously terminal producers, never change an existing successor.
    """
    original, proposed = set(base), set(override)
    starts = {source for source, _ in original}
    if not original.issubset(proposed) or any(s in starts for s, _ in proposed - original):
        raise ValueError("Override changes a required route")
    return tuple(sorted(proposed))


_SOURCES = re.compile(r"^[-*_\s]*Sources[*_]*\s*:[*_]*\s*(.*?)\s*$", re.I)
_SOURCE_ID = re.compile(r"\b[a-z]+:[a-z0-9][a-z0-9._-]*")


def sources_line(text: str) -> list[str] | None:
    """Node ids on the answer's final ``Sources:`` line.

    ``None`` when the final non-empty line is not a ``Sources:`` line; ``[]`` when it is
    but names no id (``Sources: none``, prose, ``N/A``). Only id-shaped tokens count
    (``kind:slug``), so a markdown link tail or a bare word is never an id.
    Reads only; the gate verdict does not depend on it.
    근거: [[decision:vin314-every-agent-answer-ends-with-a-sources-line-2026-10-07]]
    """
    lines = [line for line in text.splitlines() if line.strip()] if isinstance(text, str) else []
    match = _SOURCES.match(lines[-1]) if lines else None
    if not match:
        return None
    body = match.group(1)
    if body.strip().lower().startswith("none"):
        return []
    return [token.rstrip(".") for token in _SOURCE_ID.findall(body) if token.rstrip(".")]


class OutputGate:
    """A frozen copy of a freshly fetched structured role contract.

    Gates only interpret enumerated checks. The unchanged never prose continues
    to bind the operator; these checks do not prove all semantic obligations.
    Call evaluate after the final model result, before exposing or handing it off.
    """
    def __init__(self, props: Mapping[str, Any], *, extra_gates: list[dict[str, Any]] | None = None,
                 self_authored: bool = False) -> None:
        self._props = copy.deepcopy(dict(props))
        self._extra = copy.deepcopy([] if extra_gates is None else extra_gates)
        self.self_authored = self_authored

    def evaluate(self, answer: str, *, proofs: Iterable[ToolProof] = (),
                 person_only_requests: Iterable[Mapping[str, Any]] = (),
                 phase: str = "output", manifest: Mapping[str, Any] | None = None,
                 resolved_sources: Iterable[str] = (),
                 repositories: Iterable[str] = ()) -> GateResult:
        props, extra = self._props, self._extra
        gates, never, person = props.get("gates"), props.get("never_checks"), props.get("person_only")
        if (not isinstance(answer, str) or not answer.strip() or len(answer) > 131072
                or phase not in {"output", "handoff"}
                or not _gates_valid(gates) or not _gates_valid(extra)
                or not isinstance(never, list) or not all(_check_valid(x) for x in never)
                or not isinstance(person, list) or not all(
                    isinstance(p, dict) and set(p) == {"action", "reason"}
                    and isinstance(p.get("action"), str) and p["action"] in _KNOWN_ACTIONS
                    and isinstance(p.get("reason"), str)
                    and bool(p["reason"].strip()) for p in person)):
            return GateResult("block", ("invalid_role_contract",))
        by_id = {g["id"]: g for g in gates}
        if (not _REQUIRED.issubset({g["check"]["type"] for g in gates})
                or len({p["action"] for p in person}) != len(person)
                or any(g["id"] in by_id for g in extra)
                or any(g["check"]["type"] in _REQUIRED and g["on_fail"] !=
                       ("interrupt" if g["check"]["type"] == "person_only" else "block") for g in gates)):
            return GateResult("block", ("immutable_required_gate",))
        observed = tuple(proofs)
        if not all(isinstance(p, ToolProof) and type(p.success) is bool
                   and isinstance(p.claim, str) and len(p.claim) <= 4096
                   and all(isinstance(x, str) and bool(x.strip()) for x in (p.action, p.target, p.call_id))
                   for p in observed):
            return GateResult("block", ("invalid_tool_evidence",))
        actions = {p["action"] for p in person}
        requests = tuple(person_only_requests)
        if not all(isinstance(p, Mapping) and set(p) == {"action", "reason"}
                   and isinstance(p.get("action"), str) and p["action"] in actions
                   and isinstance(p.get("reason"), str) and bool(p["reason"].strip()) for p in requests):
            return GateResult("block", ("unknown_person_only_request",))
        pending = {p["action"] for p in requests}
        selected_repos = tuple(repositories)
        if phase == "handoff" and (not selected_repos or not all(
                isinstance(repo, str) and repo in {"Vinc", "Vinc-homepage"} for repo in selected_repos)):
            return GateResult("block", ("missing_handoff_repository",))
        body = _ID.sub("<id>", answer)
        if self.self_authored:
            body = "\n".join(line for line in body.splitlines()
                             if not (line.startswith("-") and not line.startswith("---")))
        sentences = _sentences(body)
        failed: set[str] = set()
        interrupts: set[str] = set()
        evidence: set[str] = set()

        def witnessed(action: str, target: str | None = None, claim: str | None = None) -> bool:
            # 문장이 리포를 밝혔으면 증거의 대상도 그 리포여야 한다(다른 리포의 실행은 증거가 아니다).
            named = set(_REPO.findall(claim)) if claim is not None else set()
            return any(p.success and p.action == action and (target is None or p.target == target)
                       and (claim is None or p.claim.strip() == claim.strip())
                       and (not named or p.target.split(":", 1)[0].strip() in named
                            or not _REPO.match(p.target))
                       for p in observed)

        def own(sentence: str) -> str:
            """이 답이 스스로 하는 말만 남긴다: 따옴표 안의 인용과 부정된 구절을 지운다.

            문장째 건너뛰지 않는다. "not merged yet; please merge it" 의 뒤쪽 제안은 남아야 한다.
            """
            return _NEGATED.sub("", _QUOTED.sub("", sentence))

        noted: list[dict[str, str]] = []

        def note(action: str, kind: str, phrase: str, proof: str = "") -> None:
            noted.append({"action": action, "kind": kind, "phrase": phrase.strip()[:160],
                          **({"proof": proof} if proof else {})})

        def fails(check: dict[str, Any]) -> bool:
            kind = check["type"]
            if kind == "person_only":
                acts = [a for a in _ACTS if a in actions]
                for sentence in sentences:
                    for action, clause, how in _act_findings(sentence, acts):
                        pending.add(action)
                        evidence.add(sentence[:160])
                        note(action, how, clause)
                    said = own(sentence)
                    if _REFUSAL.search(sentence) or _CLAIM.search(said):
                        continue
                    present = _PAST_FORMS.sub("", said)
                    agency = bool(_INTENT.search(present) or _IMPERATIVE.search(present))
                    handoff = bool(_HANDOFF.search(present) or _MARKER.search(present))
                    for action in actions:
                        if action in _ACTS:
                            continue
                        direct = (action in _ACTIONS and (agency or handoff)
                                  and re.search(_ACTIONS[action], present, re.I))
                        decision = ((agency or _MARKER.search(present)) and any(
                            word in present.lower() for word in _DECISION_WORDS.get(action, ())))
                        if direct or decision:
                            pending.add(action)
                            evidence.add(sentence[:160])
                            note(action, "request", sentence)
                for match in _PERSON_FIELD.finditer(body):
                    value = match.group(1).strip().lower()
                    if value and not value.startswith(("none", "n/a", "-")):
                        for action in actions:
                            if action in value or any(word in value for word in _DECISION_WORDS.get(action, ())):
                                pending.add(action)
                                evidence.add(match.group(0)[:160])
                                note(action, "field", match.group(0))
                for asked in requests:
                    note(asked["action"], "requested", asked["reason"])
                return bool(pending)
            if kind == "side_effect_evidence":
                for sentence in sentences:
                    for said in _PART.split(own(sentence)):
                        if not said.strip() or _REFUSAL.search(said) or _ATTRIBUTED.search(said):
                            continue
                        said = _CONDITIONAL.sub("", said)
                        if _PAST_EDIT.search(said) and not witnessed("edit", claim=sentence):
                            evidence.add(sentence[:160])
                            note("edit", "unproven", sentence, "edit")
                            return True
                        if _CLAIM.search(said):
                            hits = [a for a, rx in _ACTIONS.items() if re.search(rx, said, re.I)]
                            # "updated <file>" 같은 편집 주장은 edit 증거로 받친다.
                            if not hits and re.search(r"\b(?:updated|edited|changed|rewrote)\b", said, re.I):
                                hits = ["edit"]
                            # 다른 작업의 성공 기록은 이 완료 문장의 증거가 아니다.
                            missing = [a for a in hits if not witnessed(a, claim=sentence)]
                            if not hits or missing:
                                evidence.add(sentence[:160])
                                note(",".join(missing) or "claim", "unproven", sentence, ",".join(missing) or "edit")
                                return True
                        if _UNWITNESSED.search(said):
                            claimed = ("tests" if re.search(r"\btests?\b|\bsuite\b|\d passed", said, re.I) else
                                       "gate" if re.search(r"\bgates?\b|\bgate_\w+", said, re.I) else
                                       "graph_write" if "graph" in said.lower() else "gate")
                            if not witnessed(claimed, claim=sentence):
                                evidence.add(sentence[:160])
                                note(claimed, "unproven", sentence, claimed)
                                return True
                return False
            if kind == "command_success":
                if (phase == "handoff" and bool(set(selected_repos) & set(check["repos"]))
                        and not witnessed("command", check["command"])):
                    note("command", "unproven", check["command"], "command")
                    return True
                return False
            if kind == "handoff_manifest":
                if phase != "handoff":
                    return False
                note("handoff", "manifest", "the handoff manifest (producer, run, claims with resolved evidence_ids)")
                if not isinstance(manifest, Mapping) or not all(
                        isinstance(manifest.get(k), str) and bool(manifest[k].strip()) for k in ("producer", "run")):
                    return True
                claims, available = manifest.get("claims"), set(resolved_sources)
                return (not isinstance(claims, list) or not claims or any(
                    not isinstance(c, Mapping) or not isinstance(c.get("what"), str) or not c["what"].strip()
                    or not isinstance(c.get("evidence_ids"), list) or not c["evidence_ids"]
                    or any(not isinstance(source, str) or source not in available for source in c["evidence_ids"])
                    for c in claims))
            if kind == "forbidden_actions":
                for sentence in sentences:
                    if (not _REFUSAL.search(sentence) and (self.self_authored or _SELF.search(sentence))
                            and any(a not in actions and re.search(_ACTIONS.get(a, r"(?!)"), sentence, re.I)
                                    for a in check["actions"])):
                        evidence.add(sentence[:160])
                        note(next(a for a in check["actions"] if a not in actions and re.search(
                            _ACTIONS.get(a, r"(?!)"), sentence, re.I)), "forbidden", sentence)
                        return True
                return False
            if kind == "korean_comment_allowed":
                for sentence in _sentences(body):
                    if _KO_COMMENT.search(sentence) and _DEFECT.search(sentence) and not _HEDGE.search(sentence):
                        evidence.add(sentence[:160])
                        note("korean_comment", "forbidden", sentence)
                        return True
                return False
            if kind == "rendered_copy":
                for key, value in _rendered(body):
                    for rule in check["rules"]:
                        if rule == "english_value":
                            if key != "(quoted)" and (key.startswith("en.") or (
                                    not re.search(r"[가-힣]", value) and re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", value))):
                                evidence.add((key + " = " + value)[:160])
                                note(rule, "forbidden", key + " = " + value)
                                return True
                        elif _COPY_RULES[rule].search(value):
                            evidence.add((key + " = " + value)[:160])
                            note(rule, "forbidden", key + " = " + value)
                            return True
                return False
            return True

        findings: list[dict[str, str]] = []

        def run(check: dict[str, Any], name: str) -> bool:
            noted.clear()
            failing = fails(check)
            if failing:
                for item in noted or [{"action": check["type"], "kind": "check", "phrase": ""}]:
                    found = {"check": name, **item}
                    findings.append({**found, "message": _message(found)})
            return failing

        for check in never:
            if run(check, "never_" + check["type"]):
                failed.add("never_" + check["type"])
        for gate in gates + extra:
            if run(gate["check"], gate["id"]):
                (failed if gate["on_fail"] == "block" else interrupts).add(gate["id"])
        verdict = "block" if failed else "interrupt" if interrupts else "pass"
        return GateResult(verdict, tuple(sorted(failed | interrupts)), tuple(sorted(pending)),
                          tuple(sorted(evidence)[:20]), tuple(findings[:20]))

    @property
    def person_only(self) -> list[dict[str, Any]]:
        """A copy of the contract's person-only actions and their reasons."""
        person = self._props.get("person_only")
        return copy.deepcopy(person) if isinstance(person, list) else []

    def require_pass(self, answer: str, **kwargs: Any) -> GateResult:
        """Strict: anything but ``pass`` raises, an interrupt included."""
        result = self.evaluate(answer, **kwargs)
        if result.verdict != "pass":
            raise OutputGateStopped(result)
        return result

    def deliver(self, answer: str, **kwargs: Any) -> "Delivery":
        """What reaches the person: the answer, with any pending person-only decision named.

        ``pass`` returns the answer unchanged. An ``interrupt`` in the ``output`` phase returns
        the answer with :func:`with_pending` added, because the person reading it is the one
        who decides. ``block``, and an interrupt in the ``handoff`` phase (the reader is another
        agent, which must not act on it), raise :class:`OutputGateStopped` as before.
        """
        result = self.evaluate(answer, **kwargs)
        if result.verdict == "pass":
            return Delivery(answer, result)
        if result.verdict == "interrupt" and kwargs.get("phase", "output") == "output":
            return Delivery(with_pending(answer, result, self.person_only), result)
        raise OutputGateStopped(result)


#: 사람의 결정을 기다리는 일 목록의 머리줄. :func:`delivered_result` 가 이 줄로 알아본다.
PENDING_HEADER = "Waiting on your decision before anyone acts on this:"


@dataclass(frozen=True)
class Delivery:
    """An answer cleared for the person, and the gate result it carries."""
    text: str
    result: GateResult


def with_pending(answer: str, result: GateResult, person_only: Iterable[Mapping[str, Any]]) -> str:
    """The answer with its pending person-only decisions listed above the final ``Sources:`` line.

    Each action is named with the reason from the role contract. ``Sources:`` stays the last line
    so the citation contract still holds.
    """
    reasons = {p.get("action"): p.get("reason") for p in person_only if isinstance(p, Mapping)}
    actions = result.person_only_actions or ("person_only",)
    note = "\n".join([PENDING_HEADER] + [
        f"- {a}: {reasons[a]}" if reasons.get(a) else f"- {a}" for a in actions])
    lines = answer.rstrip("\n").split("\n")
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if last is not None and _SOURCES.match(lines[last]):
        return "\n".join(lines[:last]).rstrip("\n") + "\n\n" + note + "\n" + "\n".join(lines[last:])
    return answer.rstrip("\n") + "\n\n" + note


def delivered_result(text: str | None) -> GateResult:
    """The verdict a delivered answer carries: ``interrupt`` with its actions when it lists
    pending decisions under :data:`PENDING_HEADER`, else ``pass``.

    For hosts that record runs after a graph returned normally. A model that writes the header
    itself only makes its run wait for a person, never pass a check.
    """
    lines = (text or "").split("\n")
    if PENDING_HEADER not in lines:
        return GateResult("pass")
    start = lines.index(PENDING_HEADER) + 1
    actions = []
    for line in lines[start:]:
        match = re.match(r"^- ([a-z_]+)(?::|$)", line)
        if not match:
            break
        actions.append(match.group(1))
    return GateResult("interrupt", ("person_only",), tuple(a for a in actions if a != "person_only"))
