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
# 새로 걸지 않는다. 기본값은 위 규칙이다: 문장의 낱말이 모두 아래 닫힌 목록에 있고 push 대상이 모두 작업 브랜치일
# 때만 거둔다. 목록에 없는 낱말·플래그·기호·글자가 하나라도 있으면 위 규칙 그대로 걸린다. 목록에는 해롭지 않은 것만 둔다.
PUSH_READING_LISTS: dict[str, frozenset[str]] = {
    # 작업 브랜치 접두사. users 는 users/<이름>/<나머지> 꼴일 때만.
    "prefixes": frozenset({"fix", "feat", "feature", "chore", "docs", "test", "tests", "refactor", "perf",
                           "security", "ci", "build", "style", "wip", "spike", "users", "dependabot",
                           "weg1456", "agents", "design", "platform", "vincos", "infra", "revert"}),
    "push_words": frozenset({"push", "pushed", "pushes", "pushing", "force-push", "force-pushed", "force-pushes",
                             "force-pushing"}),
    "flags": frozenset({"-u", "--set-upstream", "--force-with-lease", "--force-if-includes", "-f", "--force",
                        "--no-verify", "--quiet", "-q", "--verbose", "-v", "--dry-run", "-n"}),
    "remotes": frozenset({"origin", "upstream"}),
    "git": frozenset({"git"}),
    "remote_word": frozenset({"remote"}),
    "branch_words": frozenset({"branch", "브랜치"}),
    "branch_adjectives": frozenset({"working", "work", "feature", "topic"}),
    "determiners": frozenset({"the", "a", "my", "our", "this"}),
    # 대상과 동사 사이에도 올 수 있는 말("is pushed", "를 push", "to origin").
    "linking": frozenset({"is", "was", "has", "been", "be", "are", "now", "just", "to", "onto", "up", "with",
                          "를", "을", "는", "은", "에", "로", "으로"}),
    "joining": frozenset({"and", "then", "와", "과", "그리고"}),
    "words": frozenset({"i", "i'll", "i’ll", "i'd", "i’d", "we", "we'll", "we’ll", "will", "would", "let", "me",
                        "am", "going", "plan", "please", "again", "first", "next", "step", "also", "you", "when",
                        "ready", "of", "run", "ran", "for", "on", "제가", "하고", "하겠습니다", "했습니다", "합니다",
                        "할게요", "함", "한", "했고", "하였습니다", "해", "주세요", "다음", "단계"}),
    "opening": frozenset({"open", "opened", "opening", "opens", "create", "created", "creating",
                          "열었습니다", "열겠습니다", "열고", "엽니다", "열었고"}),
    "pull_request_words": frozenset({"draft", "pull", "request", "pr", "review", "requested"}),
    # 문장 맨 앞의 목록 표시.
    "list_markers": frozenset({"-", "*", "+"}),
    # "푸시" 바로 뒤에 붙는 어미.
    "korean_endings": frozenset({"", "하고", "하겠습니다", "했습니다", "합니다", "할게요", "함", "한", "했고",
                                 "하였습니다", "해", "할"}),
}
_PUSH_SYMBOL = {word: symbol for key, symbol in (
    ("push_words", "PUSH"), ("flags", "F"), ("remotes", "R"), ("git", "G"), ("remote_word", "RW"),
    ("branch_words", "B"), ("branch_adjectives", "ADJ"), ("determiners", "D"), ("linking", "L"),
    ("joining", "J"), ("words", "W"), ("opening", "O")) for word in PUSH_READING_LISTS[key]}
_PUSH_SYMBOL.update({"draft": "DR", "pull": "PL", "request": "RQ", "pr": "PR", "review": "RV", "requested": "RQD"})
# 이름이 이것이거나 이것으로 시작하거나 경로 한 칸이 이것이면 작업 브랜치가 아니다.
_GUARDED_NAMES = ("main", "master", "trunk", "head", "production", "prod", "release", "releases", "gh-pages",
                  "stable", "develop", "default", "live", "staging", "deploy", "latest", "nightly", "origin",
                  "upstream", "refs", "tags", "tag")
_GUARDED_SET = frozenset(_GUARDED_NAMES)
# 경로 한 칸 어디든 버전 꼴: v 다음 숫자, 숫자끼리 . - _ 로 이음, 숫자만인 칸, 날짜 길이의 숫자, 릴리스 낱말 옆의
# 숫자. 어느 갈래도 / 를 넘지 않으므로 이름 전체에 한 번 찾는다.
_VERSIONISH = re.compile(r"v\d|\d+[._-]\d+|(?:^|/)\d+(?:/|$)|\d{6}|(?:release|rel|rc|alpha|beta|snapshot|nightly|latest"
                         r"|stable|hotfix|pre|preview)[._-]?\d|\d[._-]?(?:release|rel|rc|alpha|beta|snapshot"
                         r"|nightly|latest|stable|hotfix|pre|preview)", re.I)
_NAME_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:[./][A-Za-z0-9_-]+)*")
_PUSH_PIECE = re.compile(r"[^\s`\"“”‘()|]+")
_HANGUL_RUN = re.compile(r"[가-힣]+|[^가-힣]+")
_PR_NUMBER = re.compile(r"#\d+")
_BULLET = re.compile(r"^[-*+]\s+")
_COLON_WORDS = PUSH_READING_LISTS["push_words"] | PUSH_READING_LISTS["branch_words"] | {"step"}
# 기호열 문법. T 는 대상(B 낱말 옆의 NB 또는 접두사 있는 NP), push 동사는 문장에 하나, 그 앞뒤에는 대상·플래그·원격과
# 이음말(쉼표 C 포함)만, 나머지 자리에는 낱말(W)·이음말·draft pull request 를 연다·리뷰 요청 구만 온다.
_T = r"(?:(?:D )?(?:ADJ )?B (?:NP|NB)|(?:D )?(?:NP|NB) B|NP)"
_TS = rf"{_T}(?:(?: (?:J|C))+ {_T})*"
_MID = r"(?:F|L|(?:D )?R(?: RW)?)"
_PV = r"(?:G )?PUSH"
_PRP = r"(?:D )?(?:DR )?(?:PL RQ|PR)(?: N#)?"
_X = rf"(?:W|L|J|C|O {_PRP}|{_PRP}(?: L)? O|(?:(?:RQ|RQD) )?RV(?: RQD)?(?: W {_PRP})?)"
_PUSH_GRAMMAR = re.compile(rf"(?:{_X} )*(?:{_PV}(?: {_MID})* {_TS}(?: {_MID})*|{_TS}(?: {_MID})* {_PV})(?: {_X})*")
# 이보다 긴 문장은 읽지 않고 위 규칙대로 둔다(짧은 보고 문장만 거둔다).
_PUSH_MAX = 600
_PUSH_CONTEXT = 300
_CLOSERS = "`'\"”’)*"
# 줄바꿈 뒤에서 새 덩어리를 여는 줄: 목록 줄, 번호 줄, 제목, 표 줄.
_UNIT_START = re.compile(r"(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|\|)")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_HEADING = re.compile(r"\s*#{1,6}\s")
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


def _push_name(token: str) -> str | None:
    """작업 브랜치 이름의 기호. 접두사가 목록에 있으면 NP, 없으면 branch 낱말 옆에서만 대상이 되는 NB, 아니면 None."""
    if not _NAME_SHAPE.fullmatch(token) or not any(c in token for c in "/-_"):
        return None
    low = token.lower()
    parts = low.split("/")
    if low.startswith(_GUARDED_NAMES) or not _GUARDED_SET.isdisjoint(parts) or _VERSIONISH.search(low):
        return None
    if len(parts) >= 2 and parts[0] in PUSH_READING_LISTS["prefixes"] and (parts[0] != "users" or len(parts) >= 3):
        return "NP"
    return "NB"


def _push_symbols(sentence: str) -> list[str] | None:
    """문장을 닫힌 목록의 기호로 바꾼다. 목록에 없는 조각이 하나라도 있으면 None."""
    out: list[str] = []
    for piece in _PUSH_PIECE.findall(sentence):
        whole = piece.lstrip("'’")
        piece = whole.rstrip("'’.,;!")
        # 뒤의 쉼표·쌍반점은 기호 C 로 남긴다("fix-x, 브랜치" 는 브랜치 낱말이 이름을 이끄는 꼴이 아니다).
        comma = "," in whole[len(piece):] or ";" in whole[len(piece):]
        if not piece:
            # 부호만 남은 조각("`fix/x`!" 의 "!").
            if comma:
                out.append("C")
            continue
        # 알려진 낱말 뒤의 쌍점만 지운다("Push:", "branch:", "브랜치:"). 이름 뒤의 쌍점은 refspec 이라 남긴다.
        if piece.endswith(":") and piece[:-1].lower() in _COLON_WORDS:
            piece = piece[:-1]
        # 한글이 없는 조각은 나누지 않는다(긴 출력에서 조각마다 정규식을 돌리지 않게).
        for run in ((piece,) if piece.isascii() else _HANGUL_RUN.findall(piece)):
            low = run.lower()
            if low in _PUSH_SYMBOL:
                out.append(_PUSH_SYMBOL[low])
            elif run.startswith("푸시") and run[2:] in PUSH_READING_LISTS["korean_endings"]:
                out.append("PUSH")
            elif run.startswith("브랜치") and _PUSH_SYMBOL.get(run[3:]) in ("L", "J"):
                out += ["B", _PUSH_SYMBOL[run[3:]]]
            elif _PR_NUMBER.fullmatch(run):
                out.append("N#")
            elif (name := _push_name(run)) is not None:
                out.append(name)
            else:
                return None
        if comma:
            out.append("C")
    return out


def _spans(body: str, sentences: list[str]) -> list[tuple[int, int]]:
    """나눈 문장마다 body 안의 자리. 차례대로 찾지 못하면 빈 목록(거두지 않는다)."""
    out, at = [], 0
    for sentence in sentences:
        start = body.find(sentence, at)
        if start < 0:
            return []
        at = start + len(sentence)
        out.append((start, at))
    return out


def _bounded(before: str, sentence: str, after: str) -> bool:
    """문장이 제 덩어리에 혼자 있는가. 같은 문단의 앞뒤 글은 모르는 글이라 대상을 더할 수 있으므로 False.

    덩어리의 앞: 글의 처음, 빈 줄, 또는 줄바꿈 뒤의 새 목록 줄·표 줄이나 제목·"...:" 줄 다음.
    덩어리의 뒤: 글의 끝, 또는 . ! 로 끝나고(표 줄은 |) 빈 줄이나 줄바꿈 뒤의 새 목록 줄·표 줄·제목.
    코드·따옴표·괄호가 짝이 맞지 않으면 문장 나누기가 그 가운데를 자른 것이라 False. before·after 는 가까운 글만.
    """
    if (sentence.count("`") % 2 or sentence.count('"') % 2 or sentence.count("“") != sentence.count("”")
            or sentence.count("(") != sentence.count(")")):
        return False
    head = before.rstrip()
    if head:
        gap, previous = before[len(head):], head.rsplit("\n", 1)[-1]
        # 앞 줄이 잘리지 않고 다 보일 때만 제목 줄로 읽는다(창이 줄 가운데서 시작하면 모른다).
        whole_line = "\n" in head or len(before) < _PUSH_CONTEXT
        if not (_BLANK_LINE.search(gap) or ("\n" in gap and (
                _BULLET.match(sentence) or sentence.startswith("|") or previous.endswith(":")
                or (whole_line and _HEADING.match(previous))))):
            return False
    tail = after.lstrip()
    if not tail:
        return True
    gap = after[:len(after) - len(tail)]
    ended = sentence.rstrip(_CLOSERS)[-1:] in (".", "!") or sentence.endswith("|")
    return ended and bool(_BLANK_LINE.search(gap) or ("\n" in gap and _UNIT_START.match(tail)))


# 거둔 문장 밖의 글이 조용한가를 보는 표지. push·태그 낱말, git, 지키는 이름(낱말째), 버전 꼴, @ 가운데
# 하나라도 문장 밖에 있으면 그 글이 다른 대상을 더할 수 있다.
_REST_TRIGGER = re.compile(
    r"push|푸시|(?<![A-Za-z0-9_])tags?(?![A-Za-z0-9_])|--tags|태그|(?<![A-Za-z0-9_])git(?![A-Za-z0-9_])|@"
    r"|(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(name) for name in _GUARDED_NAMES) + r")(?![A-Za-z0-9_])"
    r"|메인|마스터|운영|프로덕션|릴리스|릴리즈|배포|기본\s*브랜치", re.I)


def _trigger_bounds(body: str) -> tuple[int, int] | None:
    """body 에서 표지가 처음 시작하는 자리와 마지막으로 끝나는 자리. 표지가 없으면 None.

    글 전체를 한 번만 훑는다. 문장마다 다시 훑으면 push 문장이 많은 긴 글에서 시간이 제곱으로 든다.
    """
    first = last = None
    for pattern in (_REST_TRIGGER, _VERSIONISH):
        for match in pattern.finditer(body):
            first = match.start() if first is None else min(first, match.start())
            last = match.end() if last is None else max(last, match.end())
    return None if first is None or last is None else (first, last)


def _rest_is_quiet(bounds: tuple[int, int] | None, span: tuple[int, int]) -> bool:
    """문장 밖에 표지가 하나도 없는가. 모든 표지가 이 문장의 자리 안에 있어야 한다.

    ⚠ 문장 하나만 보고 거두면, 빈 줄이나 새 목록 줄 뒤의 다른 덩어리가 지키는 대상을 실어도 질문이 사라진다.
    """
    return bounds is None or (bounds[0] >= span[0] and bounds[1] <= span[1])


def _branch_push_only(sentence: str, before: str = "", after: str = "") -> bool:
    """문장의 낱말이 모두 닫힌 목록에 있고, push 하나의 대상이 모두 작업 브랜치이며, 문장 경계가 분명한가.

    모르면 False(위 규칙대로 걸린다).
    """
    if len(sentence) > _PUSH_MAX or not _bounded(before, sentence, after):
        return False
    symbols = _push_symbols(_BULLET.sub("", sentence, count=1))
    return symbols is not None and bool(_PUSH_GRAMMAR.fullmatch(" ".join(symbols)))


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
                spans: list[tuple[int, int]] | None = None
                bounds: tuple[int, int] | None = None
                scanned = False
                for index, sentence in enumerate(sentences):
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
                        if decision and action == "push_tag":
                            spans = spans if spans is not None else _spans(body, sentences)
                            if spans and _branch_push_only(sentence, body[max(0, spans[index][0] - _PUSH_CONTEXT):
                                                                           spans[index][0]],
                                                           body[spans[index][1]:spans[index][1] + _PUSH_CONTEXT]):
                                if not scanned:
                                    bounds, scanned = _trigger_bounds(body), True
                                if _rest_is_quiet(bounds, spans[index]):
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
