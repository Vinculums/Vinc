"""Host-side role checks. No model calls, graph writes, or automatic approvals.

Text rules retain the original agent-lab gate's node-ID, refusal and diff handling.
Unlike that prototype, policy comes from typed checks and action IDs, never reason prose.
"""
from __future__ import annotations

import copy
import re
import shlex
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


#: 실행 기록의 행동 종류. 사람 전용 행동 id 로 잇는 것은 KIND_ACTIONS 다.
RECORD_KINDS = ("vcs.push", "vcs.protected_push", "vcs.tag_push", "vcs.merge", "ci.dispatch", "publish", "deploy",
                "message.send", "data.migrate", "other")
#: 기록된 종류마다 그것이 해당하는 사람 전용 행동 id. 역할의 person_only 에 있는 것만 질문이 된다.
#: 보호 브랜치나 읽을 수 없는 대상의 push 는 병합이자 태그 push 일 수 있으므로 둘 다다.
KIND_ACTIONS: dict[str, tuple[str, ...]] = {
    "vcs.merge": ("merge",),
    "vcs.protected_push": ("merge", "push_tag"),
    "vcs.tag_push": ("push_tag",),
    "ci.dispatch": ("publish", "deploy"),
    "publish": ("publish",),
    "deploy": ("deploy", "publish"),
    "message.send": ("send",),
    "data.migrate": ("migration", "pk_namespacing_migration"),
}
#: 기록이 말할 수 있는 사람 전용 행동. 호스트가 본 기록이 있을 때만 이 행동은 기록이 정하고 글은 참고(notes)가
#: 된다. 에이전트가 적은 기록은 질문을 더할 뿐이고 글 읽기는 그대로다. 이 밖의 행동(승인·가격·위험 수용 같은
#: 결정)은 도구 호출로 드러나지 않으므로 언제나 글 읽기가 정한다.
RECORDED_ACTIONS = frozenset(a for actions in KIND_ACTIONS.values() for a in actions)
#: 기록을 남긴 쪽. host 는 호스트가 도구 호출을 직접 본 것, agent 는 에이전트가 스스로 적은 것이다.
OBSERVERS = ("host", "agent")
RECORD_MAX = 256
COMMAND_MAX = 8192
FIELD_MAX = 512


@dataclass(frozen=True)
class RecordedAction:
    """One tool call the run made: a shell command line or a tool name, never a sentence.

    ``observed_by="host"`` only when the host saw the call itself (its tool loop, a hook);
    a record the agent wrote about its own calls is ``"agent"``. The kind is read from
    ``command`` or ``tool`` by :func:`action_kinds`; a stated ``kind`` can only add to it.
    ``target`` is what the host resolved the call to (the branch ``git push`` sent), if known.
    An agent's ``target`` can only add as well: its call is also read as if no target were given.
    """
    command: str = ""
    tool: str = ""
    kind: str = ""
    target: str = ""
    exit_status: int | None = None
    observed_by: str = "agent"
    call_id: str = ""


def record_entry_valid(entry: Any) -> bool:
    """기록 한 줄이 정해진 꼴인가: 타입·길이·종류·누가 보았는가."""
    return (isinstance(entry, RecordedAction)
            and all(isinstance(getattr(entry, k), str) for k in ("command", "tool", "kind", "target", "call_id"))
            and len(entry.command) <= COMMAND_MAX
            and all(len(getattr(entry, k)) <= FIELD_MAX for k in ("tool", "kind", "target", "call_id"))
            and bool(entry.command.strip() or entry.tool.strip() or entry.kind.strip())
            and (entry.kind == "" or entry.kind in RECORD_KINDS)
            and (entry.exit_status is None or type(entry.exit_status) is int)
            and entry.observed_by in OBSERVERS)


# 명령 한 줄을 단순 명령으로 나누는 구분자. 따옴표 안의 구분자도 나누지만 그 조각은 아는 명령이 아니어서 묻지 않는다.
_SHELL_SPLIT = re.compile(r"\|\||&&|[;|&\n]|\$\(|`|\)")
# 명령 앞에 붙어 실제 명령을 감싸는 낱말과 그 낱말의 값을 받는 옵션. 옵션 뒤가 실제 명령이다.
# 값은 옵션 바로 뒤 낱말이거나 붙어 있다(-ubob, --user=bob). 목록에 없는 옵션은 값이 없다고 본다.
_WRAPPERS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-r", "-t", "-T", "-U", "-D", "-R", "--user", "--group",
                       "--host", "--prompt", "--close-from", "--role", "--type", "--command-timeout",
                       "--other-user", "--chdir", "--chroot"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-P"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "xargs": frozenset({"-I", "-n", "-L", "-P", "-s", "-d", "-E", "-a", "--arg-file", "--delimiter", "--max-args",
                        "--max-procs", "--max-chars", "--process-slot-var"}),
    "watch": frozenset({"-n", "--interval"}),
    "exec": frozenset({"-a"}),
    "command": frozenset(), "nohup": frozenset(), "builtin": frozenset(),
}
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?|\.\d+[smhd]?")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
# 셸의 값을 받는 옵션(-o pipefail, -O extglob, --rcfile 파일).
_SHELL_VALUES = frozenset({"--rcfile", "--init-file", "--init-command"})
# 다른 명령을 대신 돌리는 실행기(앞 낱말들)와 그 값을 받는 옵션. 옵션 뒤의 첫 낱말부터가 실제 명령이다.
_RUNNER_PY = frozenset({"--from", "--with", "--with-editable", "--with-requirements", "-p", "--python", "--index",
                        "--index-url", "--extra-index-url", "--directory", "--project", "--package", "--group",
                        "--extra", "--env-file", "--spec", "--pip-args"})
_RUNNERS: dict[tuple[str, ...], frozenset[str]] = {
    ("uvx",): _RUNNER_PY, ("uv", "run"): _RUNNER_PY, ("uv", "tool", "run"): _RUNNER_PY,
    ("pipx", "run"): _RUNNER_PY, ("poetry", "run"): frozenset(), ("pipenv", "run"): frozenset(),
    ("hatch", "run"): frozenset(), ("npx",): frozenset({"-p", "--package"}), ("bunx",): frozenset({"-p", "--package"}),
    ("pnpm", "dlx"): frozenset({"--package"}), ("pnpm", "exec"): frozenset({"--filter", "-F", "-C", "--dir"}),
    ("yarn", "dlx"): frozenset({"-p", "--package"}),
}
_PYTHON = re.compile(r"python[\d.]*|pypy[\d.]*|py")
# npm·pnpm·yarn 의 하위 명령 앞에 올 수 있는 값 옵션(npm --otp 123456 publish).
_PACKAGE_VALUES = frozenset({"--otp", "--registry", "--userconfig", "--globalconfig", "--prefix", "-w", "--workspace",
                             "--tag", "--access", "--cache", "--loglevel", "-C", "--dir", "--filter", "--cwd",
                             "--scope", "--token", "--index", "-r", "--repository", "--repository-url",
                             "--manifest-path", "--package", "-p", "--config"})
_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                                    "--config-env", "--super-prefix"})
# push 의 해롭지 않은 플래그. 목록에 없는 플래그는 대상을 모른다고 보고 묻는다.
_PUSH_SAFE_FLAGS = frozenset({"-u", "--set-upstream", "--force-with-lease", "--force-if-includes", "-f", "--force",
                              "--no-verify", "--verify", "-q", "--quiet", "-v", "--verbose", "--dry-run", "-n",
                              "--atomic", "--no-atomic", "--progress", "--no-progress", "--porcelain", "-4", "-6",
                              "--ipv4", "--ipv6", "--no-force-with-lease", "--no-signed"})
_PUSH_REMOTES = frozenset({"origin", "upstream", "fork", "github"})
_PUBLISH_TOOLS = {"npm": ("publish",), "pnpm": ("publish",), "yarn": ("publish", "npm"), "cargo": ("publish",),
                  "twine": ("upload",), "uv": ("publish",), "poetry": ("publish",), "flit": ("publish",),
                  "gem": ("push",), "vsce": ("publish",), "ovsx": ("publish",), "docker": ("push",),
                  "podman": ("push",), "hatch": ("publish",), "dotnet": ("nuget",), "mvn": ("deploy",)}
_RUN_SCRIPTS = {"publish": "publish", "release": "publish", "deploy": "deploy", "migrate": "data.migrate",
                "db:migrate": "data.migrate", "migration": "data.migrate"}
_DEPLOY_VERBS = frozenset({"apply", "create", "delete", "patch", "replace", "set", "rollout", "scale", "edit",
                           "deploy", "up", "update", "upgrade", "install", "uninstall", "rollback", "destroy",
                           "import", "restart", "start", "stop", "swap", "publish", "push", "sync", "cp"})
_DEPLOY_TOOLS = frozenset({"az", "kubectl", "helm", "terraform", "tofu", "fly", "flyctl", "gcloud", "aws",
                           "firebase", "wrangler", "pulumi", "heroku", "vercel", "netlify", "railway", "doctl"})
_MAIL_TOOLS = frozenset({"sendmail", "mail", "mailx", "mutt", "swaks", "msmtp", "ssmtp"})
_HTTP_TOOLS = frozenset({"curl", "wget", "http", "https", "xh"})
_SEND_URL = re.compile(r"hooks\.slack\.com|slack\.com/api/(?:chat\.postMessage|chat\.postEphemeral|files\.upload)"
                       r"|discord(?:app)?\.com/api/webhooks|api\.telegram\.org/bot[^/\s]*/send|api\.sendgrid\.com"
                       r"|api\.mailgun\.net|api\.postmarkapp\.com|api\.resend\.com|api\.twilio\.com/\S*/Messages"
                       r"|graph\.microsoft\.com/\S*sendMail|gmail\.googleapis\.com/\S*/send"
                       r"|outlook\.office\.com/webhook", re.I)
_MIGRATE_ARGS = re.compile(r"migrat", re.I)
_SQL_DDL = re.compile(r"\b(?:alter|create|drop|truncate)\s+(?:table|index|schema|database|type|view)\b|\.sql\b", re.I)
_MIGRATE_TOOLS = frozenset({"alembic", "flyway", "liquibase", "sqitch", "dbmate", "goose", "migrate", "knex",
                            "sequelize", "prisma", "rails", "rake", "flask"})
_MIGRATE_VERBS = frozenset({"upgrade", "downgrade", "stamp", "migrate", "deploy", "update", "up", "down", "rollback",
                            "apply", "reset", "push", "repair", "clean"})
# 도구 이름(mcp__서버__도구 의 도구 몫)과 능력 id 를 행동 종류로. 서버 안 메시지(다른 세션에 보내기)는 바깥 발송이 아니다.
# 이름은 소문자로 견준다.
_TOOL_KINDS: dict[str, tuple[str, ...]] = {
    "merge_pull_request": ("vcs.merge",), "enable_pr_auto_merge": ("vcs.merge",), "merge_diff": ("vcs.merge",),
    "actions_run_trigger": ("ci.dispatch",), "run_workflow": ("ci.dispatch",),
    "create_release": ("publish", "vcs.tag_push"), "create_tag": ("vcs.tag_push",),
    "slack_post_message": ("message.send",), "slack_reply_to_thread": ("message.send",),
    "send_email": ("message.send",), "send_mail": ("message.send",), "reply_all": ("message.send",),
    "github.pr.merge": ("vcs.merge",), "github.actions.run": ("ci.dispatch",),
    "slack.message.send": ("message.send",), "email.send": ("message.send",)}
# 브랜치에 커밋을 쓰는 도구. 쓰는 브랜치는 호스트가 푼 대상(target)으로만 안다.
_BRANCH_WRITE_TOOLS = frozenset({"push_files", "create_or_update_file", "delete_file"})
_MAIL_SERVER_TOOLS = frozenset({"send_message", "reply", "forward", "send_draft", "post_message"})
_INTERNAL_SERVERS = ("claude-code-remote", "claude_code_remote", "vinc")


def _argv(part: str) -> list[str]:
    # 구분자로 나뉜 조각은 따옴표가 열린 채 끝날 수 있다(bash -lc 'gh pr merge 1; ls'). 닫아서 읽는다.
    for close in ("", "'", '"'):
        try:
            return shlex.split(part + close, posix=True)
        except ValueError:
            continue
    return [w.strip("'\"") for w in part.split()]


def _option(argv: list[str], i: int, values: frozenset[str]) -> tuple[int, str, str | None]:
    """argv[i] 의 옵션 하나: (다음 위치, 옵션, 값). 값을 받는 옵션이면 붙은 값이나 다음 낱말이 값이다.

    짧은 옵션 묶음(-iu bob, -ubob)은 값을 받는 첫 글자에서 끊는다."""
    word = argv[i]
    if word.startswith("--"):
        key, eq, value = word.partition("=")
        if eq:
            return i + 1, key, value
        if key in values:
            return i + 2, key, argv[i + 1] if i + 1 < len(argv) else ""
        return i + 1, key, None
    for j, letter in enumerate(word[1:], 2):
        if "-" + letter in values:
            if word[j:]:
                return i + 1, "-" + letter, word[j:]
            return i + 2, "-" + letter, argv[i + 1] if i + 1 < len(argv) else ""
    return i + 1, word, None


def _strip_wrappers(argv: list[str]) -> list[str]:
    """sudo·env·timeout 같은 감싸개와 그 옵션(값 포함), 앞의 VAR=값 을 벗긴 실제 명령.

    env -S '...' 는 그 글이 명령이고, timeout 은 옵션 뒤의 시간 하나를 더 건너뛴다."""
    i = 0
    while i < len(argv):
        word = argv[i]
        name = word.rsplit("/", 1)[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            i += 1
            continue
        if name not in _WRAPPERS:
            break
        i += 1
        split: str | None = None
        while i < len(argv) and argv[i].startswith("-"):
            if argv[i] == "--":
                i += 1
                break
            if argv[i] == "-":
                i += 1
                continue
            i, key, value = _option(argv, i, _WRAPPERS[name])
            if name == "env" and key in ("-S", "--split-string") and value is not None:
                split = value
        if name == "timeout" and i < len(argv) and _DURATION.fullmatch(argv[i]):
            i += 1
        if split is not None:
            argv, i = _argv(split) + argv[i:], 0
    return argv[i:]


def _shell_command(argv: list[str]) -> str | None:
    """셸 호출(bash -lc '…', sh -ec, bash -o pipefail -c '…', bash -c -- '…', fish --command=…)이 돌리는 명령 글.

    -c 가 든 짧은 옵션 묶음이 있으면 옵션이 끝난 뒤 첫 낱말이 명령이다. -o·-O 는 값을 하나씩 받는다.
    bash <<< '…' 의 here-string 도 셸이 읽는 명령이다."""
    if "<<<" in argv[1:-1]:
        return argv[argv.index("<<<", 1) + 1]
    i, command = 1, False
    while i < len(argv):
        word = argv[i]
        if word == "--":
            i += 1
            break
        if word.startswith("--command="):
            return word.split("=", 1)[1]
        if word.startswith("--"):
            command = command or word == "--command"
            i += 2 if word in _SHELL_VALUES else 1
            continue
        if len(word) > 1 and word[0] in "-+":
            letters = word[1:]
            command = command or (word[0] == "-" and "c" in letters)
            i += 1 + letters.count("o") + letters.count("O")
            continue
        break
    return argv[i] if command and i < len(argv) else None


def _simple_commands(line: str, depth: int = 0) -> list[list[str]]:
    out: list[list[str]] = []
    for part in _SHELL_SPLIT.split(line):
        argv = _strip_wrappers(_argv(part))
        if not argv:
            continue
        head = argv[0].rsplit("/", 1)[-1]
        # bash -lc "..." · eval "..." 는 안의 명령이 실제로 도는 명령이다.
        inner = _shell_command(argv) if depth < 3 and head in _SHELLS else None
        if inner is not None:
            out += _simple_commands(inner, depth + 1)
            continue
        if depth < 3 and head == "eval":
            out += _simple_commands(" ".join(argv[1:]), depth + 1)
            continue
        out.append([head] + argv[1:])
    return out


def _ref_kinds(name: str | None, target: str, *, source: bool = False) -> set[str]:
    """push 의 ref 하나가 닿는 곳. 작업 브랜치면 빈 집합, 태그 꼴이면 tag, 보호·모르는 곳이면 protected.

    명령의 ref 자리는 글과 달리 이름이 무엇인지 분명하다: 경로 한 칸이 지키는 이름일 때만 보호 브랜치다
    (main-fix 는 브랜치다). 원본 자리(source)는 태그인가만 본다. 받는 곳은 받는 자리가 정한다.
    """
    if name in ("HEAD", "@") or name is None:
        name = target or None
    if not name:
        return {"vcs.protected_push"}
    if name.startswith("refs/tags/"):
        return {"vcs.tag_push"}
    if name.startswith("refs/heads/"):
        name = name[len("refs/heads/"):]
    elif name.startswith("refs/"):
        return {"vcs.protected_push"}
    unknown = set() if source else {"vcs.protected_push"}
    low = name.lower()
    parts = low.split("/")
    if not _NAME_SHAPE.fullmatch(name) or not _GUARDED_SET.isdisjoint(parts):
        return unknown
    # 작업 브랜치 접두사가 있는 이름은 숫자가 있어도 브랜치다(같은 이름의 태그가 있어야만 태그로 간다).
    if len(parts) >= 2 and parts[0] in PUSH_READING_LISTS["prefixes"] and (parts[0] != "users" or len(parts) >= 3):
        return set()
    if _VERSIONISH.search(low):
        return {"vcs.tag_push"}
    # 구분자 없는 낱말 하나(latest·foo)는 태그인지 브랜치인지 모른다.
    return set() if any(c in name for c in "/-_") else unknown


def _push_kinds(args: list[str], target: str) -> set[str]:
    kinds: set[str] = set()
    positional: list[str] = []
    for word in args:
        if word.startswith("-") and len(word) > 1:
            if word in _PUSH_SAFE_FLAGS:
                continue
            if word in ("--tags", "--follow-tags"):
                kinds.add("vcs.tag_push")
            elif word.startswith("--force-with-lease="):
                kinds |= _ref_kinds(word.split("=", 1)[1].split(":", 1)[0], "")
            elif word == "--mirror":
                kinds |= {"vcs.protected_push", "vcs.tag_push"}
            else:
                # --all · --delete · -d · --prune · -o · --repo 와 모르는 플래그: 무엇이 어디로 가는지 모른다.
                kinds.add("vcs.protected_push")
            continue
        positional.append(word)
    remote, refs = (positional[0], positional[1:]) if positional else (None, [])
    if not refs and "--tags" in args:
        # 대상 없이 --tags 만 주면 태그만 간다.
        refs = ["refs/tags/*"]
    if remote is not None and remote not in _PUSH_REMOTES:
        # 배포 원격(heroku·production)이나 URL: 받는 곳이 작업 저장소인지 모른다.
        kinds.add("vcs.protected_push")
    for ref in refs or [None]:
        if ref == "refs/tags/*":
            kinds.add("vcs.tag_push")
            continue
        if ref is not None and ("*" in ref or ref.startswith(":") or ref.endswith(":")):
            kinds.add("vcs.protected_push")
            continue
        ref = None if ref is None else ref.lstrip("+")
        if ref is not None and ":" in ref:
            source, destination = ref.split(":", 1)
            if ":" in destination:
                kinds.add("vcs.protected_push")
                continue
            if source not in ("HEAD", "@"):
                kinds |= _ref_kinds(source, target, source=True)
            kinds |= _ref_kinds(destination, "")
        else:
            kinds |= _ref_kinds(ref, target)
    return kinds or {"vcs.push"}


_GH_API_VALUE_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--field", "--raw-field", "-H", "--header", "--input",
                                 "-q", "--jq", "-t", "--template", "--cache", "--hostname", "-p", "--preview"})


def _gh_api_kinds(rest: list[str]) -> set[str]:
    method, path, values = "", "", []
    i = 0
    while i < len(rest):
        word = rest[i]
        if word in _GH_API_VALUE_FLAGS and i + 1 < len(rest):
            if word in ("-X", "--method"):
                method = rest[i + 1].upper()
            else:
                values.append(rest[i + 1])
            i += 2
            continue
        if word.startswith("--method="):
            method = word.split("=", 1)[1].upper()
        elif word.startswith("-X") and len(word) > 2:
            method = word[2:].upper()
        elif not word.startswith("-") and not path:
            path = word
        else:
            values.append(word)
        i += 1
    if not method:
        method = "POST" if any(w in ("-f", "-F", "--field", "--raw-field", "--input") for w in rest) else "GET"
    return _api_kinds(method, path, " ".join(values))


def _body_field(body: str, name: str) -> str | None:
    """요청 본문(-f base=main, JSON {"base":"main"})에서 한 필드의 값."""
    found = re.search(rf"(?:^|[\s,{{&])[\"']?{name}[\"']?\s*[:=]\s*[\"']?([^\s\"',&}}]+)", body)
    return found.group(1) if found else None


def _api_kinds(method: str, path: str, body: str) -> set[str]:
    """GitHub REST·GraphQL 호출 하나(메서드, 경로, 본문)가 닿는 종류. gh api 와 curl·wget·httpie 가 같이 쓴다."""
    path = path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if method == "PUT" and re.search(r"pulls/\d+/merge$", path):
        return {"vcs.merge"}
    if "graphql" in path and re.search(r"mergePullRequest|enablePullRequestAutoMerge|enqueuePullRequest", body):
        return {"vcs.merge"}
    if method in ("POST", "PUT", "PATCH") and path.endswith("/dispatches"):
        return {"ci.dispatch"}
    if method == "POST" and path.endswith("/releases"):
        return {"publish", "vcs.tag_push"}
    if method in ("POST", "PATCH", "PUT", "DELETE") and re.search(r"/git/(?:refs|tags)\b", path):
        if "refs/tags" in path + " " + body or path.endswith("/git/tags"):
            return {"vcs.tag_push"}
        named = re.search(r"/git/refs/heads/(.+)$", path) or re.search(r"refs/heads/([^\s'\"]+)", body)
        return _ref_kinds(named.group(1) if named else None, "") or {"vcs.push"}
    if method == "POST" and path.endswith("/merges"):
        # 브랜치를 base 에 병합한다. base 가 작업 브랜치일 때만 해롭지 않다.
        return {"vcs.push"} if _ref_kinds(_body_field(body, "base"), "") == set() else {"vcs.merge"}
    if method in ("PUT", "DELETE") and "/contents/" in path:
        # 파일 쓰기는 커밋이다. branch 가 없으면 기본 브랜치에 들어간다.
        return _ref_kinds(_body_field(body, "branch"), "") or {"vcs.push"}
    return set()


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
# curl·wget·httpie 에서 요청 본문을 싣는 옵션과, 본문이 아닌 값을 받는 옵션.
_HTTP_DATA = frozenset({"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--data-ascii", "--json",
                        "-F", "--form", "--form-string", "-T", "--upload-file", "--post-data", "--post-file",
                        "--body-data", "--body-file"})
_HTTP_VALUES = _HTTP_DATA | frozenset({
    "-X", "--request", "--method", "-H", "--header", "-u", "--user", "-o", "--output", "-A", "--user-agent",
    "-e", "--referer", "-b", "--cookie", "-c", "--cookie-jar", "-w", "--write-out", "-m", "--max-time",
    "--connect-timeout", "--retry", "-x", "--proxy", "-K", "--config", "--cacert", "--cert", "--key", "-E",
    "-O", "--output-document", "-a", "--auth", "--auth-type", "--header-file"})
_GITHUB_API = re.compile(r"api\.github\.com(/\S*)?|/api/v3(/\S*)|/api(/graphql)\b", re.I)


def _http_kinds(head: str, args: list[str]) -> set[str]:
    """curl·wget·httpie 호출이 메시지 API 나 GitHub API 의 바꾸는 경로에 닿는가."""
    kinds = {"message.send"} if any(_SEND_URL.search(w) for w in args) else set()
    method, path, body = "", None, []
    i = 0
    while i < len(args):
        word = args[i]
        if word.startswith("-") and len(word) > 1:
            if word.startswith("-X") and len(word) > 2:
                method, i = word[2:].upper(), i + 1
                continue
            if word.startswith("-d") and len(word) > 2:
                body.append(word[2:])
                i += 1
                continue
            i, key, value = _option(args, i, _HTTP_VALUES)
            if key in ("-X", "--request", "--method") and value:
                method = value.upper()
            elif key in _HTTP_DATA and value is not None:
                body.append(value)
            continue
        found = _GITHUB_API.search(word)
        if found and path is None:
            path = next((g for g in found.groups() if g), "/")
        elif head in ("http", "https", "xh") and word.upper() in _HTTP_METHODS and path is None and not method:
            method = word.upper()
        elif head in ("http", "https", "xh") and path is not None and re.match(r"[\w.-]+:?=", word):
            body.append(word)
        i += 1
    if path is not None:
        kinds |= _api_kinds(method or ("POST" if body else "GET"), path, " ".join(body))
    return kinds


def _gh_kinds(args: list[str]) -> set[str]:
    words: list[str] = []
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word in ("-R", "--repo", "--hostname"):
            skip = True
        else:
            words.append(word)
    if words[:2] == ["pr", "merge"]:
        return set() if "--disable-auto" in words else {"vcs.merge"}
    if words[:2] in (["workflow", "run"], ["run", "rerun"]):
        return {"ci.dispatch"}
    if words[:2] == ["release", "create"]:
        return {"publish", "vcs.tag_push"}
    if words[:2] in (["release", "upload"], ["release", "edit"]):
        return {"publish"}
    if words[:1] == ["api"]:
        return _gh_api_kinds(words[1:])
    return set()


def _words(args: list[str], values: frozenset[str] = _PACKAGE_VALUES) -> list[str]:
    """옵션과 그 값을 뺀 낱말들(npm --otp 123456 publish 의 publish 가 첫 낱말이다)."""
    out, i = [], 0
    while i < len(args):
        if args[i].startswith("-") and len(args[i]) > 1:
            i = _option(args, i, values)[0]
            continue
        out.append(args[i])
        i += 1
    return out


def _runner_inner(argv: list[str]) -> list[str]:
    """실행기(python -m, uvx, pipx run, uv run, npx …)가 대신 돌리는 명령. 실행기가 아니면 빈 목록."""
    head = argv[0]
    if _PYTHON.fullmatch(head) and "-m" in argv[1:]:
        return argv[argv.index("-m", 1) + 1:]
    for prefix, values in _RUNNERS.items():
        if tuple(argv[:len(prefix)]) == prefix:
            i = len(prefix)
            while i < len(argv) and argv[i].startswith("-"):
                if argv[i] == "--":
                    i += 1
                    break
                i = _option(argv, i, values)[0]
            return argv[i:]
    return []


def _command_kinds(argv: list[str], target: str, depth: int = 0) -> set[str]:
    inner = _runner_inner(argv) if depth < 3 else []
    if inner:
        inner[0] = inner[0].rsplit("/", 1)[-1]
        return _command_kinds(inner, target, depth + 1) | _command_kinds_of(argv, target)
    return _command_kinds_of(argv, target)


def _command_kinds_of(argv: list[str], target: str) -> set[str]:
    head, args = argv[0], argv[1:]
    words = _words(args)
    if head == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in _GIT_GLOBAL_WITH_VALUE else 1
        return _push_kinds(args[i + 1:], target) if i < len(args) and args[i] == "push" else set()
    if head == "gh":
        return _gh_kinds(args)
    if head == "np":
        # np 는 npm 게시 도구다(npx np).
        return {"publish"}
    if head in _PUBLISH_TOOLS and words[:1] and words[0] in _PUBLISH_TOOLS[head]:
        if (head == "yarn" and words[0] == "npm" and words[1:2] != ["publish"]) or (
                head == "dotnet" and words[1:2] != ["push"]):
            return set()
        return {"publish"}
    if head in ("npm", "pnpm", "yarn", "bun") and words[:1] == ["run"] and len(words) > 1:
        kind = _RUN_SCRIPTS.get(words[1]) or ("data.migrate" if _MIGRATE_ARGS.search(words[1]) else None)
        return {kind} if kind else set()
    if head in _DEPLOY_TOOLS:
        if head in ("vercel", "netlify"):
            return {"deploy"} if "--prod" in args or "--production" in args else set()
        return {"deploy"} if any(w.split("-", 1)[0] in _DEPLOY_VERBS for w in words) else set()
    if head in _MAIL_TOOLS:
        return {"message.send"}
    if head in _HTTP_TOOLS:
        return _http_kinds(head, args)
    if head in _MIGRATE_TOOLS:
        return {"data.migrate"} if any(w in _MIGRATE_VERBS or w.startswith(("migrate", "db:")) for w in words) else set()
    if (_PYTHON.fullmatch(head) or head in ("uv", "poetry", "pipenv")) and (
            (any(w.endswith("manage.py") for w in args) and "migrate" in words)
            or any(_MIGRATE_ARGS.search(w) for w in args[:4])):
        return {"data.migrate"}
    if head in ("psql", "mysql", "mariadb", "sqlcmd", "sqlite3") and any(_SQL_DDL.search(w) for w in args):
        return {"data.migrate"}
    return set()


def _tool_kinds(tool: str, target: str) -> set[str]:
    name = tool.strip().lower()
    server, _, basename = name.rpartition("__") if name.startswith("mcp__") else ("", "", name)
    if name in _TOOL_KINDS or basename in _TOOL_KINDS:
        return set(_TOOL_KINDS.get(name) or _TOOL_KINDS[basename])
    if basename in _BRANCH_WRITE_TOOLS:
        # 브랜치에 커밋을 쓰는 도구. 호스트가 푼 대상이 없으면 어느 브랜치인지 모르므로 묻는다.
        return _ref_kinds(target or None, "") or {"vcs.push"}
    if basename.startswith(("publish_", "deploy_")):
        return {"publish"} if basename.startswith("publish_") else {"deploy"}
    if basename in _MAIL_SERVER_TOOLS and server and not any(s in server for s in _INTERNAL_SERVERS):
        return {"message.send"}
    return set()


def action_kinds(entry: RecordedAction) -> frozenset[str]:
    """The kinds one recorded call carries: read from its command line and tool name, plus its stated kind.

    A stated kind only adds: a record that calls ``gh pr merge 12`` a ``vcs.push`` is still a merge.
    Read-only and unknown calls carry none. A script the command runs (``./release.sh``) is not read.
    """
    kinds: set[str] = {entry.kind} if entry.kind in RECORD_KINDS else set()
    stated = entry.target.strip()
    # 호스트가 푼 대상은 그대로 쓴다. 에이전트가 적은 대상은 더할 뿐이라 대상 없이 읽은 것과 합친다.
    targets = (stated,) if entry.observed_by == "host" else ("", stated) if stated else ("",)
    for target in targets:
        if entry.tool.strip():
            kinds |= _tool_kinds(entry.tool, target)
        if entry.command.strip():
            for argv in _simple_commands(entry.command):
                kinds |= _command_kinds(argv, target)
    return frozenset(kinds)


def recorded_person_only(record: Iterable[RecordedAction],
                         person_only: Iterable[str]) -> dict[str, list[RecordedAction]]:
    """Person-only action id -> the recorded calls that carry it.

    An earlier approval never covers a recorded call: each call is asked in the step that records it."""
    wanted = set(person_only)
    out: dict[str, list[RecordedAction]] = {}
    for entry in record:
        for kind in sorted(action_kinds(entry)):
            for action in KIND_ACTIONS.get(kind, ()):
                if action in wanted and entry not in out.get(action, []):
                    out.setdefault(action, []).append(entry)
    return out


_MASK = "***"
# 저장·표시 전에 가리는 비밀의 꼴: 이름에 TOKEN·KEY·SECRET·PASSWORD·PAT 이 든 VAR=값 과 --옵션 값,
# 인증 헤더, URL 의 사용자:비밀번호, curl -u 의 사용자:비밀번호, 알려진 토큰 접두사.
_SECRET_NAME = r"[A-Za-z0-9_-]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|CREDENTIAL|(?:^|_|-)PAT(?=_|-|\b))[A-Za-z0-9_-]*"
_SECRET_PATTERNS = (
    (re.compile(rf"(?i)(?<![\w-])({_SECRET_NAME})=(\"[^\"]*\"|'[^']*'|\S+)"), r"\1=" + _MASK),
    (re.compile(rf"(?i)(--{_SECRET_NAME})(\s+|=)(\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2" + _MASK),
    (re.compile(r"(?i)\b((?:proxy-)?authorization|x-[\w-]*(?:token|key|auth)[\w-]*|private-token|cookie)"
                r"(\s*:\s*)[^'\"\n]+"), r"\1\2" + _MASK),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + _MASK + "@"),
    (re.compile(r"(?<![\w-])(-u|--user)(\s+)[^\s:]+:\S+"), r"\1\2" + _MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|sk-[A-Za-z0-9_-]{8,}"
                r"|xox[abposr]-[A-Za-z0-9-]{6,}|glpat-[A-Za-z0-9_-]{8,}|npm_[A-Za-z0-9]{16,}"
                r"|pypi-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})"), _MASK),
)


def mask_secrets(text: str) -> str:
    """Hide what looks like a credential in a command line before it is stored or shown."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def describe(entry: RecordedAction) -> str:
    """사람이 읽는 기록 한 줄: 무엇을 돌렸고 끝 상태가 무엇이며 누가 보았는가. 비밀은 가린 뒤 자른다."""
    what = mask_secrets(entry.command.strip() or entry.tool.strip() or entry.kind)
    status = "" if entry.exit_status is None else f", exit {entry.exit_status}"
    seen = "observed by the host" if entry.observed_by == "host" else "stated by the agent"
    return f"{what[:300]} ({seen}{status})"


@dataclass(frozen=True)
class GateResult:
    verdict: str
    failed_checks: tuple[str, ...] = ()
    person_only_actions: tuple[str, ...] = ()
    evidence_sentences: tuple[str, ...] = ()
    #: 호스트 기록이 정하는 사람 전용 행동을 글이 말한 자리. verdict 에 들지 않고, 허브는 실행에 남겨 사람에게 보인다.
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"verdict": self.verdict, "failed_checks": list(self.failed_checks),
                               "person_only_actions": list(self.person_only_actions),
                               "evidence_sentences": list(self.evidence_sentences)}
        if self.notes:
            out["notes"] = list(self.notes)
        return out


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
                 repositories: Iterable[str] = (),
                 record: Iterable[RecordedAction] | None = None,
                 host_observed: bool = False) -> GateResult:
        """``record`` is the record of the calls this step made. ``None`` (a host that keeps no
        record) reads person-only actions from the text as before. A record adds a question for
        every recorded call that carries a person-only action; the text is still read as before.
        Only with ``host_observed`` (a host observer signed this step's record, so it lists every
        call) do the actions a record can show (:data:`RECORDED_ACTIONS`) come from the record
        alone; the text about them then becomes ``notes``.
        """
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
        ran = None if record is None else tuple(record)
        if type(host_observed) is not bool or (host_observed and ran is None) or (ran is not None and (
                len(ran) > RECORD_MAX or not all(record_entry_valid(e) for e in ran))):
            return GateResult("block", ("invalid_action_record",))
        # 호스트가 본 기록만 기록이 보일 수 있는 행동을 정하고 글을 notes 로 돌린다. 에이전트가 적은 기록은
        # 빠뜨릴 수 있으므로 질문을 더할 뿐이다.
        by_record = actions & RECORDED_ACTIONS if host_observed else set()
        from_record = recorded_person_only(ran or (), actions)
        notes: set[str] = set()
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

        def raised(action: str, said: str) -> None:
            """글이 사람 전용 행동을 말했다. 호스트 기록이 정하는 행동이면 참고만 남기고, 아니면 묻는다."""
            if action in by_record:
                notes.add(f"person_only ({action}): the text names it ('{mask_secrets(said)[:100]}'); the record "
                          + ("shows it" if action in from_record else "shows no such call"))
            else:
                pending.add(action)
                evidence.add(said[:160])

        def fails(check: dict[str, Any]) -> bool:
            kind = check["type"]
            if kind == "person_only":
                for action, entries in from_record.items():
                    pending.add(action)
                    evidence.add(f"recorded {action}: {describe(entries[0])}"[:160])
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
                            raised(action, sentence)
                for match in _PERSON_FIELD.finditer(body):
                    value = match.group(1).strip().lower()
                    if value and not value.startswith(("none", "n/a", "-")):
                        for action in actions:
                            if action in value or any(word in value for word in _DECISION_WORDS.get(action, ())):
                                raised(action, match.group(0))
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
                          tuple(sorted(evidence)[:20]), tuple(sorted(notes)[:20]))

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
