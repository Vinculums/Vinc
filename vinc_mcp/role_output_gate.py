# 생성 파일이다. 손으로 고치지 말 것.
# source: integrations/client/python/src/vinc_client/output_gate.py
# regenerate: python -X utf8 scripts/sync_output_gate.py
"""Host-side role checks. No model calls, graph writes, or automatic approvals.

Text rules retain the original agent-lab gate's node-ID, refusal and diff handling.
Unlike that prototype, policy comes from typed checks and action IDs, never reason prose.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
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
_NEGATED = re.compile(r"\b(?:nothing|not|never|no)\b[^.;]{0,30}\b(?:merged|committed|pushed|deployed|published|passed|sent|deleted)\b|(?:하지|되지) 않았|안 했|아직 [^.]{0,20}않", re.I)
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
# 이름 붙은 작업 브랜치의 push 는 tag push 가 아니다. 이 읽기는 person_only 의 push_tag 걸림을 거두기만 하고
# 새로 걸지 않는다. push 하나라도 대상을 읽지 못하거나 보호·태그 꼴이 보이면 위 규칙 그대로 걸린다.
_REF_PART = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
_REF = rf"\+?(?:{_REF_PART}(?:/{_REF_PART})*:)?{_REF_PART}(?:/{_REF_PART})*"
_REF_END = r"(?![A-Za-z0-9_/@~^-]|[.:][A-Za-z0-9])"
_FLAG = r"(?:--set-upstream|-u|--force-with-lease(?:=[A-Za-z0-9_./:-]+)?|--force-if-includes|--force|-f|--no-verify)"
_REMOTE = r"(?:origin|upstream)"
_BRANCH_WORD = r"(?:(?:working|work|feature|topic|pr)\s+)?branch"
_PUSH_EN = re.compile(r"(?<![A-Za-z0-9_/.-])(?:force[- ]?)?push(?:ed|es|ing)?(?![A-Za-z0-9_/-])", re.I)
# 영어는 동사 뒤에서 대상을 읽는다: 플래그·원격·관사 다음의 이름, 그 뒤의 "to origin"·플래그까지.
_PUSH_AFTER = re.compile(
    rf"(?:\s+(?:{_FLAG}|{_REMOTE}|the|my|our|up|to|(?P<word>{_BRANCH_WORD}))(?=\s))*"
    rf"(?:\s*:)?\s+[`'\"“‘]?(?P<name>{_REF}){_REF_END}[`'\"”’]?(?:\s+branch\b)?"
    rf"(?:\s+(?:to|onto)\s+(?:the\s+)?(?:{_REMOTE}|remote)(?:\s+remote)?\b)?(?:\s+(?:with\s+)?{_FLAG}(?:\s+{_REMOTE})?(?![\w-]))*",
    re.I)
# 동사 뒤에 대상이 없으면 바로 앞을 읽는다("Branch fix/x is pushed", "fix/x (pushed)").
_PUSH_BEFORE = re.compile(
    rf"(?P<word>\b{_BRANCH_WORD}\s*:?\s*)?(?<![A-Za-z0-9_./:+@~^-])[`'\"“‘]?(?P<name>{_REF}){_REF_END}[`'\"”’]?"
    r"\s*(?:\(\s*|:\s*|(?:is|was|has been|got|now|just)\s+)*$", re.I)
_BEFORE_REACH = 160
_REF_TOKEN = re.compile(rf"(?<![A-Za-z0-9_./:+@~^-])[`'\"“‘]?(?P<name>{_REF}){_REF_END}[`'\"”’]?")
_KO_NAMED = re.compile(rf"브랜치\s*:?\s*[`'\"“‘]?(?P<name>{_REF}){_REF_END}[`'\"”’]?|"
                       rf"(?<![A-Za-z0-9_./:+@~^-])[`'\"“‘]?(?P<name2>{_REF}){_REF_END}[`'\"”’]?\s*브랜치")
_PROTECTED = frozenset({"main", "master", "trunk", "head", "production", "prod", "release", "releases",
                        "gh-pages", "stable", "develop", "default", "live"})
_VERSION_PART = re.compile(r"v?\d+(?:\.\d+)*", re.I)
# 읽은 이름을 지운 나머지에 이것이 있으면 이름 붙은 브랜치만의 push 가 아니다.
_PUSH_REST = re.compile(
    r"tag|push|태그|푸시|refs/|--(?:all|mirror|delete|prune)\b|\bHEAD\b|@\{"
    r"|\b(?:main|master|trunk|production|prod|releases?|gh-pages|stable|develop|live|default branch|protected branch"
    r"|all|every\w*|both|branches)\b|메인|기본 브랜치|운영"
    r"|(?<![A-Za-z0-9_/.-])v?\d+(?:\.\d+)+(?![A-Za-z0-9_/-])|\bv\d+\b", re.I)
# 거절·끼어들기가 권하는 말. 모두 이 게이트를 통과해야 한다. 브랜치 자리에 보호·태그 이름을 넣은 꼴은 위 규칙대로
# 걸리고, 위 규칙이 원래 놓치는 꼴은 시험이 목록으로 붙잡아 둔다.
_ADVICE = {
    "merge": ("did not merge", "merging is left to the owner"),
    "push_tag": ("did not push a tag", "the tag push is left to the owner"),
    "publish": ("did not publish", "publishing is left to the owner"),
    "send": ("did not send it", "sending is left to the owner"),
    "migration": ("did not run the migration", "running the migration is left to the owner"),
}
BRANCH_PUSH_EXAMPLE = "pushed branch fix/x to origin"
RESTATE_EXAMPLE = "Attempted to run the tests; the result is in the pull request checks"


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

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "failed_checks": list(self.failed_checks),
                "person_only_actions": list(self.person_only_actions),
                "evidence_sentences": list(self.evidence_sentences)}


class OutputGateStopped(RuntimeError):
    """Blocked output or a pending person decision; contains no model output."""
    def __init__(self, result: GateResult) -> None:
        self.result = result
        super().__init__(f"Output gate: {result.verdict}")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _ordinary_ref(ref: str, bare: bool) -> bool:
    """보호 브랜치·태그·버전이 아닌 작업 브랜치 이름인가. 슬래시 없는 이름은 bare(branch 낱말 뒤·refspec)일 때만."""
    ref = ref[1:] if ref.startswith("+") else ref
    if ":" in ref:
        src, dst = ref.split(":", 1)
        return bool(src) and (src == "HEAD" or _ordinary_ref(src, True)) and _ordinary_ref(dst, True)
    low = ref.lower()
    if low.startswith("refs/"):
        if not low.startswith("refs/heads/"):
            return False
        ref = ref[len("refs/heads/"):]
    elif low.startswith(("origin/", "upstream/")):
        ref = ref.split("/", 1)[1]
    parts = ref.split("/")
    # 슬래시 없는 이름은 구분자(-, _)가 있어야 이름으로 본다("the branch and ..." 의 and 는 이름이 아니다).
    return ((len(parts) > 1 or (bare and bool(re.search(r"[-_]", ref))))
            and not any(p.lower() in _PROTECTED or _VERSION_PART.fullmatch(p) for p in parts))


def _branch_push_only(sentence: str) -> bool:
    """문장의 push 가 모두 이름 붙은 작업 브랜치의 push 이고, 그 이름을 지운 나머지에 태그·보호 브랜치 꼴이 없는가.

    영어 push 는 하나하나 대상을 읽고, 한국어 푸시는 같은 문장(표의 한 줄)에 이름 붙은 브랜치가 있어야 한다.
    """
    spans: list[tuple[int, int]] = []
    verbs = list(_PUSH_EN.finditer(sentence))
    korean = [m.span() for m in re.finditer("푸시", sentence)]
    if not verbs and not korean:
        return False
    for verb in verbs:
        after = _PUSH_AFTER.match(sentence, verb.end())
        if after and _ordinary_ref(after.group("name"), bool(after.group("word")) or ":" in after.group("name")):
            spans.append((verb.start(), after.end()))
            continue
        # 앞은 가까운 글자만 읽는다. 동사마다 문장 앞 전체를 다시 읽으면 긴 출력에서 제곱이 된다.
        lo = max(0, verb.start() - _BEFORE_REACH)
        before = _PUSH_BEFORE.search(sentence[lo:verb.start()])
        if before and _ordinary_ref(before.group("name"), bool(before.group("word"))):
            spans.append((lo + before.start(), verb.end()))
            continue
        return False
    if korean:
        named = [(m.span(), m.group("name")) for m in _REF_TOKEN.finditer(sentence)
                 if "/" in m.group("name")] if "/" in sentence else []
        if "브랜치" in sentence:
            named += [(m.span(), m.group("name") or m.group("name2")) for m in _KO_NAMED.finditer(sentence)]
        if not named or not all(_ordinary_ref(name, True) for _, name in named):
            return False
        spans += korean + [span for span, _ in named]
    # 읽은 구간을 빈칸 하나로 바꾼 나머지. 겹치는 구간(한국어 이름과 영어 대상)은 한 번만 지운다.
    pieces, at = [], 0
    for start, end in sorted(spans):
        if start > at:
            pieces.append(sentence[at:start])
        pieces.append(" ")
        at = max(at, end)
    pieces.append(sentence[at:])
    remainder = "".join(pieces)
    if "/" in remainder and any(not _ordinary_ref(m.group("name"), False) for m in _REF_TOKEN.finditer(remainder) if "/" in m.group("name")):
        return False
    return not _PUSH_REST.search(remainder)


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

        def fails(check: dict[str, Any]) -> bool:
            kind = check["type"]
            if kind == "person_only":
                for sentence in sentences:
                    said = own(sentence)
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
                        # push_tag 걸림만, 그것도 문장의 push 가 모두 이름 붙은 작업 브랜치일 때만 거둔다.
                        if decision and action == "push_tag" and _branch_push_only(sentence):
                            decision = False
                        if direct or decision:
                            pending.add(action)
                            evidence.add(sentence[:160])
                for match in _PERSON_FIELD.finditer(body):
                    value = match.group(1).strip().lower()
                    if value and not value.startswith(("none", "n/a", "-")):
                        for action in actions:
                            if action in value or any(word in value for word in _DECISION_WORDS.get(action, ())):
                                pending.add(action)
                                evidence.add(match.group(0)[:160])
                return bool(pending)
            if kind == "side_effect_evidence":
                for sentence in sentences:
                    said = own(sentence)
                    if _REFUSAL.search(said) or _ATTRIBUTED.search(said):
                        continue
                    said = _CONDITIONAL.sub("", said)
                    if _PAST_EDIT.search(said) and not witnessed("edit", claim=sentence):
                        evidence.add(sentence[:160])
                        return True
                    if _CLAIM.search(said):
                        hits = [a for a, rx in _ACTIONS.items() if re.search(rx, said, re.I)]
                        # "updated <file>" 같은 편집 주장은 edit 증거로 받친다.
                        if not hits and re.search(r"\b(?:updated|edited|changed|rewrote)\b", said, re.I):
                            hits = ["edit"]
                        # 다른 작업의 성공 기록은 이 완료 문장의 증거가 아니다.
                        if not hits or any(not witnessed(a, claim=sentence) for a in hits):
                            evidence.add(sentence[:160])
                            return True
                    if _UNWITNESSED.search(said):
                        claimed = ("tests" if re.search(r"\btests?\b|\bsuite\b|\d passed", said, re.I) else
                                   "gate" if re.search(r"\bgates?\b|\bgate_\w+", said, re.I) else
                                   "graph_write" if "graph" in said.lower() else "gate")
                        if not witnessed(claimed, claim=sentence):
                            evidence.add(sentence[:160])
                            return True
                return False
            if kind == "command_success":
                return (phase == "handoff" and bool(set(selected_repos) & set(check["repos"]))
                        and not witnessed("command", check["command"]))
            if kind == "handoff_manifest":
                if phase != "handoff":
                    return False
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
                        return True
                return False
            if kind == "korean_comment_allowed":
                for sentence in _sentences(body):
                    if _KO_COMMENT.search(sentence) and _DEFECT.search(sentence) and not _HEDGE.search(sentence):
                        evidence.add(sentence[:160])
                        return True
                return False
            if kind == "rendered_copy":
                for key, value in _rendered(body):
                    for rule in check["rules"]:
                        if rule == "english_value":
                            if key != "(quoted)" and (key.startswith("en.") or (
                                    not re.search(r"[가-힣]", value) and re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", value))):
                                evidence.add((key + " = " + value)[:160])
                                return True
                        elif _COPY_RULES[rule].search(value):
                            evidence.add((key + " = " + value)[:160])
                            return True
                return False
            return True

        for check in never:
            if fails(check):
                failed.add("never_" + check["type"])
        for gate in gates + extra:
            if fails(gate["check"]):
                (failed if gate["on_fail"] == "block" else interrupts).add(gate["id"])
        verdict = "block" if failed else "interrupt" if interrupts else "pass"
        return GateResult(verdict, tuple(sorted(failed | interrupts)), tuple(sorted(pending)),
                          tuple(sorted(evidence)[:20]))

    @property
    def person_only(self) -> list[dict[str, Any]]:
        """A copy of the contract's person-only actions and their reasons."""
        person = self._props.get("person_only")
        return copy.deepcopy(person) if isinstance(person, list) else []

    def advice(self, result: GateResult) -> tuple[str, ...]:
        """걸린 사람 전용 행동과 완료 주장 검사마다 고쳐 쓰는 법 한 문장. 결과는 바꾸지 않는다.

        예문("for example '...'")은 이 게이트를 통과하는 말이어야 한다(시험이 본다).
        """
        kinds = {g["id"]: g["check"]["type"] for g in list(self._props.get("gates") or []) + self._extra
                 if isinstance(g, dict) and isinstance(g.get("check"), dict) and isinstance(g.get("id"), str)}
        failed = {kinds.get(c, c[len("never_"):] if c.startswith("never_") else c) for c in result.failed_checks}
        out = []
        if "person_only" in failed:
            for action in result.person_only_actions or ("person_only",):
                undone, left = _ADVICE.get(action, ("did not do it", "it is left to the owner"))
                out.append(f"person_only ({action}): a person decides this before the run goes on. If it did not "
                           f"happen, say so with the verb, for example '{undone}'; if it is the person's step, "
                           f"say that, for example '{left}'."
                           + (" A push of a named working branch is not a tag push: name the branch, for "
                              f"example '{BRANCH_PUSH_EXAMPLE}'." if action == "push_tag" else ""))
        if "side_effect_evidence" in failed:
            out.append("side_effect_evidence: a sentence claims a side effect with no host proof. Say what was "
                       f"attempted and where the result can be read, for example '{RESTATE_EXAMPLE}'. A person-only "
                       "action (merge, tag push, publish, send, live migration) is not restated as attempted: say "
                       "it was not done and is left to the person, for example "
                       "'did not merge; merging is left to the owner'.")
        return tuple(out)

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
