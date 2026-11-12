# 생성 파일이다. 손으로 고치지 말 것.
# source: integrations/client/python/src/vinc_client/output_gate.py
# regenerate: python -X utf8 scripts/sync_output_gate.py
"""Host-side role checks. No model calls, graph writes, or automatic approvals.

Text rules retain the original agent-lab gate's node-ID, refusal and diff handling.
Unlike that prototype, policy comes from typed checks and action IDs, never reason prose.
"""
from __future__ import annotations

import bisect
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

# 바깥으로 나가는 사람 전용 행동 다섯은 언급 하나하나를 읽는다. 나머지 사람 전용 행동은 fails() 의 문장 단위 규칙 그대로다.
# 가려내지 못하면 끼어든다: 언급은 부정·미룸·작업 브랜치 push·설명으로 읽힐 때만 빠진다.
_ACTS = ("merge", "migration", "push_tag", "publish", "send")
# 완료 주장 검사는 세미콜론과 but 에서 나눠 읽는다. 한 구절의 거절이 다른 구절의 주장을 가리지 않는다.
_PART = re.compile(r";|,?\s+but\s+", re.I)

# 행동마다 언급의 꼴. 한 줄이 한 꼴이다(언어, 꼴, 정규식). 꼴을 더하는 일은 한 줄과 표의 한 행이다.
#   act: 동사로 쓰인 언급(과거·진행·원형·명령·명령어). 부정·미룸·설명이 아니면 끼어든다.
#   third: 3인칭 현재(merges). 코드나 남을 설명하는 말이므로 이 실행이 주어일 때만 끼어든다.
#   noun: 명사 언급. 같은 문장이 요청하거나 하려 할 때만 끼어든다.
#   push: push 동사. 대상(태그·main·작업 브랜치·모름)이 정한다.
#   run: 마이그레이션 명사. 실행 동사나 완료가 붙어야 행동이다.
#   data: migrate 동사. 데이터·DB·운영 표지가 있어야 행동이다(코드 이전과 가른다).
#   msg: 한국어 보내기. 메시지 맥락이 있어야 send 다.
_FORMS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "merge": (
        ("en", "act", r"merg(?:e|ed|ing)\b|mergeable\b"),
        ("en", "third", r"merges\b"),
        ("en", "act", r"land(?:s|ed|ing)?\b(?=[^.;]{0,40}\b(?:on|into|to) (?:origin/)?(?:main|master)\b)"),
        ("en", "act", r"fast-forward(?:s|ed|ing)?\b(?=[^.;]{0,30}\b(?:main|master)\b)"),
        ("en", "act", r"shipped\b(?=\s*(?:it\b|#|:|the (?:fix|change|pr\b|pull request)|to (?:main|master|prod)))"),
        ("ko", "act", r"머지|병합|(?:main|master|메인)(?:에|으로)\s?반영"),
    ),
    "push_tag": (
        ("en", "push", r"(?:force[- ]?)?push(?:ed|ing)?\b"),
        ("en", "third", r"pushes\b"),
        ("en", "act", r"tagg(?:ed|ing)\b|\bgit tag\b|cut(?:ting)? (?:a |the )?(?:new )?release\b"),
        ("en", "act", r"(?:creat(?:ed|ing)|made|add(?:ed|ing)|bump(?:ed|ing)|plac(?:ed|ing)) (?:a |an |the )?(?:new |release |git )?tags?\b"),
        ("en", "noun", r"tags?\b"),
        ("ko", "push", r"푸시"),
        ("ko", "act", r"태그\S{0,2}\s?(?:를\s?)?(?:달|만들|생성|붙|찍|올|땄|따)"),
        ("ko", "noun", r"태그"),
    ),
    "publish": (
        ("en", "act", r"(?:re)?publish(?:ed|ing)?\b"),
        ("en", "third", r"publishes\b"),
        ("en", "act", r"released\b(?=\s+(?:v?\d|it\b|them\b|the (?:package|release|new|version|crate|gem|app|build)|to\b|on\b))"),
        ("en", "act", r"cut(?:ting)? (?:a |the )?(?:new )?release\b"),
        ("en", "act", r"twine upload\b|upload(?:ed|ing)?\b(?=[^.;]{0,40}\b(?:pypi|npm|crates\.io|rubygems|the registry|app store|play store)\b)"),
        ("en", "act", r"live\b(?=\s+(?:on|in) (?:pypi|npm|crates\.io|the registry|the store|production)\b)"),
        ("ko", "act", r"발행|게시(?!판|물)|퍼블리시|출간|배포(?=\s?(?:했|함|하였|됨|됐|되었|완료|해|하|합|할|중))|릴리스\S{0,2}\s?(?:올렸|올림|냈|냄)"),
    ),
    "send": (
        ("en", "act", r"(?:re)?send(?:ing)?\b|(?:re)?sent\b"),
        ("en", "third", r"sends\b"),
        ("en", "act", r"e-?mail(?:ed|ing)\b|dm(?:ed|'d)\b|messag(?:ed|ing)\b|texted\b"),
        ("en", "act", r"repl(?:ied|y|ying)\s+to\s+(?:the\s+)?(?:customer|client|user|owner|team|email|mail|message|thread|him|her|them)\b"),
        ("en", "act", r"post(?:ed|ing)?\b(?=[^.;]{0,40}(?:\bslack\b|\bdiscord\b|#[a-z][\w-]*|\bthe (?:channel|forum|mailing list)\b|\btwitter\b|\blinkedin\b))"),
        ("ko", "act", r"발송"),
        ("ko", "msg", r"보(?:내|낼|냈|냄|낸)|전송|올렸|올림|올려|답장"),
    ),
    "migration": (
        ("en", "run", r"migrations?\b"),
        ("en", "data", r"migrat(?:e|es|ed|ing)\b"),
        ("en", "act", r"alembic upgrade\b|manage\.py migrate\b|db:migrate\b|prisma migrate\b|flyway migrate\b"),
        ("ko", "run", r"마이그레이션"),
    ),
}
# 행동마다 꼴을 한데 묶은 거름. 문장에 하나도 없으면 그 행동의 꼴은 돌리지 않는다.
_ANY_FORM = {action: re.compile("|".join(f"(?:{rx})" for _, _, rx in forms), re.I) for action, forms in _FORMS.items()}
_FORM_RX = {action: tuple((lang, form, re.compile(("(?<![\\w-])" if lang == "en" else "") + rx, re.I))
                          for lang, form, rx in forms) for action, forms in _FORMS.items()}

# 언급이 행동이 아니라 이름이나 설명인 짝말(merge conflicts, the send button).
_COMPOUND = re.compile(r"(?:merg(?:e|ing)\s+(?:origin/)?(?:main|master)\s+into\b|merge[- ](?:conflicts?|commits?|base|button|tool|driver|strategy|parser|logic|function|method|rules?|order|markers?)\b|send[- ](?:button|path|function|method|buffer|loop)\b|send\(\)|push[- ](?:notifications?|events?|subscriptions?|gate|rules?|targets?)\b|publish[- ]button\b)", re.I)
_KO_COMPOUND = re.compile(r"(?:머지|병합|푸시|보내기|발행|게시)\s?(?:충돌|버튼|커밋|베이스|기능|로직|규칙|알림|게이트)")
# 따옴표 안(제목·인용), 주소, 명령이 아닌 코드 조각은 이 답이 하는 말이 아니다. 자리를 지키도록 공백으로 바꾼다.
_SINGLE = re.compile(r"(?<![\w'])'[^'\n]{1,300}'(?![\w])|\"[^\"\n]*\"|“[^”\n]*”|‘[^’\n]*’|https?://\S+")
_CODE = re.compile(r"`([^`\n]*)`")
_COMMAND = re.compile(r"\s*(?:git|gh|npm|pnpm|yarn|twine|alembic|python\S*|uv|poetry|cargo|flask|rails|bundle|dotnet|make|bash|sh|prisma|flyway|\./)\b", re.I)
# 경로·브랜치 이름·refspec 처럼 / 나 : 로 이은 낱말. 안에 든 push·tag 는 언급이 아니다(대상 읽기에는 쓴다).
_TOKEN = re.compile(r"(?<![\w.@~^*-])[\w.@~^*-]{0,200}(?:[/:][\w.@~^*-]+)+")
# 절 경계: 부정·주어·종속절은 이 경계를 넘지 않는다.
_CLAUSE_END = re.compile(r"[,;:()]|\s(?:and|but|so|then|because|while|whereas|although|though|since)\s|(?:고|며|서|지만|는데|니까)\s", re.I)
# 구간 경계: 미룸과 push 대상은 이 경계를 넘지 않는다.
_SEGMENT_END = re.compile(r";|,?\s(?:but|so|because|whereas|although)\s|(?:지만|는데|니까)\s", re.I)
# "squash merge" 는 병합 방식의 이름이다. 뒤에 목적어가 오면("squash merge it") 명령이다.
_MERGE_KIND = re.compile(r"\b(?:squash|rebase|three-way|octopus)[- ]$", re.I)
_OBJECT = re.compile(r"\s+(?:it|this|that|them|#|pr\b|the\b|now\b|v?\d)", re.I)
# "머지를 기다리는 중" 처럼 다른 일이 그 행동을 기다리는 말의 언급은 명사다.
_KO_WAITED = re.compile(r"(?:을|를|은|는|이|가)?\s?(?:기다리|대기|보류)")
_DETERMINER = re.compile(r"\b(?:the|a|an|this|that|each|every|its|their|our|his|her|any|your|my)\s+$", re.I)
_PASSIVE = re.compile(r"\b(?:is|are|was|were|be|been|being|get|gets|got|if|when|once|unless|until|whenever)\s+$", re.I)
_SUBORDINATE = re.compile(r"\b(?:if|unless|when|whenever|where|once|until|before|after)\b", re.I)
_THIS_RUN = r"(?:\bi\b|\bwe\b|\bthis run\b|\bmy session\b|\bthis session\b|\bthe agent\b|\bthe maker\b|\bclaude\b)"
_SELF_SUBJECT = re.compile(_THIS_RUN + r"(?:\s+(?:also|then|now|just|already|finally|have|has|had|'ve))*\s+$", re.I)
_SELF_ANY = re.compile(_THIS_RUN + r"|\bon (?:their|his|her|the owner's|its) behalf\b|\binstead\b|제가|저는|내가|저희가|대신|이 실행|이 세션", re.I)
# 남이 주어인 과거 보고("Minji merged it")는 이 실행의 주장이 아니다. 이 실행의 이름(Claude·Maker)은 남이 아니다.
_OTHER_SUBJECT = re.compile(r"(?:^|[\s(*-])(?:(?!(?:This|That|It|The|Then|And|But|So|Also|Just|Already|Finally|Today|Yesterday|Now|Status|State|Done|Next|Claude|Maker|Checker|Agent|Codex|Copilot|Successfully)\b)[A-Z][a-z]{2,}|(?i:he|she|they|someone|somebody|(?:the|a) (?:owner|maintainer|person|human|reviewer)))\s+(?:(?i:also|then|just|already|finally)\s+)?$")
_KO_OTHER = re.compile(r"(?:님|씨)(?:이|가|께서)|께서|(?:소유자|오너|관리자|담당자)(?:가|이)\s")
# 행동을 부정하는 말이 그 언급 바로 앞에 있을 때만 부정이다("did not merge", "without merging", "no merge", "nothing was merged").
# 부정어가 다른 낱말에 붙어 있으면("without waiting", "no issues", "did not hesitate to") 부정이 아니다.
_NEG_PRE = re.compile(r"(?:\b(?:not|never|nor|neither|without|cannot|no longer|none of|unable to|refused? to)|n't)\s+(?:(?:yet|ever|actually|really|even|been|be|being|get|got|have|has|had|having|was|were|is|are|it|this|that|the|a|an|any|to|run|ran|apply|applied|execute|executed|perform|performed)\s+){0,3}$|\b(?:no|nothing|nobody|none)\s+(?:(?:[\w#.-]+\s+){1,2}(?:was|were|is|are|has been|have been|got|gets|get)\s+(?:yet\s+)?|(?:was|were|is|are|has been|have been|got|gets|get)\s+(?:yet\s+)?)?$", re.I)
_NEG_POST = re.compile(r"\s*:\s*(?:not|no|none|never)\b|(?:\s+[\w#.-]+){0,2}?\s+(?:was|were|is|are|has|have|had)\s+(?:not|never)\b(?:\s+(?:been|yet|ever))*\s+(?:run|done|executed|performed|made|created|merged|pushed|published|sent|tagged|applied|attempted|started|triggered|invoked|called)\b|\s+(?:nothing|none)\b|\s+no\s+(?:tags?|prs?|pull requests?|emails?|mail|messages?|packages?|releases?|migrations?|branches)\b", re.I)
_NEG_WORD = re.compile(r"\b(?:not|never|no)\b|n't\b", re.I)
_KO_NEG_PRE = re.compile(r"(?:^|\s)(?:안|못)\s?$")
_KO_NEG_POST = re.compile(r"지\s?(?:않|못|마)|\s?안\s?(?:함|했|해|하|한|됩|돼|되|됨|된)|못\s?(?:했|함|합|한)")
# 이중 부정과 의무("하지 않으면 안 됩니다", "안 한 건 아닙니다", "안 보낼 수 없어서")는 부정이 아니다.
_KO_DOUBLE = re.compile(r"^\S{0,2}\s?(?:(?:으)?면\s?안|(?:건|게|것은?|거)\s?아(?:니|닙|님))|^[^.,;]{0,4}수\s?없")
_KO_STOP = re.compile(r"(?:고|며|서|만|데)\s|[.,;!?]")
_KO_DONE = re.compile(r"[^.,;]{0,8}?(?:했|함|됨|됐|되었|완료|끝냈|진행|처리|버렸|올렸|보냈|보냄|달았|땄|중)")
_LIST_SEP = re.compile(r"(\s*,\s*(?:(?:or|nor|and)\s+)?|\s+(?:or|nor)\s+)", re.I)
_LIST_CUT = re.compile(r"[.;:!?]|\s(?:but|so|because|then|while|although)\b", re.I)
# 사람이 그 행동을 한다고 적은 말(미룸). 과거형 주장은 지우지 않는다.
_ATTRIBUTED_TO_PERSON = re.compile(
    r"\b(?:is|are|stays?|remains?|belongs?|rests?)\s+(?:with|to)\s+(?:the\s+)?(?:owners?|person|human|maintainers?)\b"
    r"|\b(?:is|are|was|were|be)\s+(?:left|kept)\s+(?:to|for)\s+(?:the\s+)?(?:owners?|person|human|maintainers?)\b"
    r"|\b(?:is|are)\s+(?:the\s+|a\s+)?(?:owner's|person's|human's|maintainer's)"
    r"|\bwaits?\s+(?:for|on)\s+(?:the\s+)?(?:owners?|person|human|maintainers?)\b"
    r"|\b(?:left|leave|leaves|leaving)\b[^.;]{0,80}?\bto\s+(?:the\s+)?(?:owners?|person|human|maintainers?)\b"
    r"|\bperson[- ]only\b|\b(?:the\s+)?owner's\s+(?:step|call|job|decision|button|action|turn)\b"
    r"|\bfor\s+(?:the\s+)?(?:owners?|person|human)\s+to\b"
    r"|(?:소유자|오너|사람|담당자|관리자)(?:가|이|께서|의)?\s?(?:직접\s?)?(?:합니다|해요|한다|할 일|하는 일|할 것|몫|맡|결정|판단)|사람\s?전용", re.I)
_ASK = re.compile(r"\b(?:please|you|your)\b|주세요|주시면|주시기|바랍니다|부탁", re.I)
# 이 답이 시키거나 묻거나 하려 하는 말. 명사 언급과 종속절의 언급은 이것이 있을 때만 끼어든다.
_QUESTION = re.compile(r"\?|\b(?:can you|could you|would you|will you|shall (?:i|we)|should (?:i|we)|want me to|do you want|let's|go ahead|someone should|don't forget|do not forget|ready (?:to|for)|no need to wait|next\s*:)", re.I)
# 마이그레이션이 라이브 데이터를 건드리는가. 시험용·로컬만 보이면 사람 전용이 아니다.
_LIVE = re.compile(r"\b(?:prod|production|live|real data|customer data)\b|운영|프로덕션|라이브|실서비스", re.I)
_LOCAL = re.compile(r"\b(?:tests?|temp|tmp|fixtures?|in-memory|dry[- ]run|local(?:ly)?|sqlite|scratch)\b|테스트|임시|로컬", re.I)
_DATA = re.compile(r"\b(?:data(?:base)?s?|db|schemas?|tables?|rows|records)\b|데이터|스키마|테이블", re.I)
_RUN_BEFORE = re.compile(r"\b(?:ran|run|runs|running|apply|applied|applying|execute[sd]?|executing|perform(?:ed)?|did|do|start(?:ed)?|kick(?:ed)? off|trigger(?:ed)?)\b[^,;.]{0,30}$", re.I)
_RUN_AFTER = re.compile(r"[^,;.]{0,30}?\b(?:completed|finished|ran|succeeded|is done|was (?:run|applied)|has been (?:run|applied)|went through)\b", re.I)
_KO_RUN = re.compile(r"[^.,;]{0,15}(?:실행|적용|돌렸|돌림|돌려|완료|했|함|진행)")
# 영어 send 가 메시지가 아니라 코드의 요청·헤더를 보내는 설명인가.
_TECH_SEND = re.compile(r"\b(?:requests?|headers?|payloads?|body|bytes|events?|signals?|packets?|quer(?:y|ies)|json|frames?|chunks?|to the (?:server|api|client|backend|hub|model)|over (?:http|the wire))\b", re.I)
_MESSAGE = re.compile(r"\b(?:e-?mails?|mail|messages?|repl(?:y|ies)|dms?|notifications?|newsletters?|invit\w*|announcements?|slack|letters?)\b|메일|메시지|메세지|답장|알림|공지|슬랙|문자|초대|뉴스레터|채널|디엠", re.I)
# push 대상. 태그·버전·main·강제 push 는 사람 전용, 이름 붙은 작업 브랜치는 아니다, 읽지 못하면 사람 전용으로 읽는다.
_PUSH_TAG = re.compile(r"--tags\b|refs/tags/|\btags?\b|태그|(?<![\w/.-])v?\d+\.\d+(?:\.\d+)*(?![\w/.-]*/)", re.I)
_PUSH_MAIN = re.compile(r":(?:refs/heads/)?(?:main|master)\b|\b(?:to|into|onto)\s+(?:origin/|the\s+)?(?:main|master)\b|\borigin\s+(?:main|master)\b|\borigin/(?:main|master)\b|\b(?:main|master)\s+branch\b|\bdefault branch\b|^\s*(?:main|master)\b|\b(?:straight|directly)\s+to\b|--force\b|(?<![\w-])-f\b|\bforce\b|(?:main|master|메인)(?:\s?브랜치)?(?:에|로|으로)", re.I)
_PUSH_BRANCH = re.compile(r"\bbranch(?:es)?\b|브랜치|\bcommits?\b|\bchanges\b|\bthe (?:fix|work|patch)\b|-u\s+origin\s+\S+|[\w.-]+/[\w./-]+", re.I)


# 언급 하나를 읽을 때 앞뒤로 보는 최대 글자 수.
_REACH = 120


def _blank(match: re.Match) -> str:
    return " " * len(match.group(0))


def _prepared(sentence: str) -> tuple[str, str]:
    """(대상 읽기용, 언급 찾기용). 길이는 원문과 같다. 인용·주소·명령 아닌 코드 조각은 비우고, 언급 찾기용은 경로·refspec 도 가린다."""
    def code(match: re.Match) -> str:
        inner = match.group(1)
        return " " + (inner if _COMMAND.match(inner) else " " * len(inner)) + " "
    text = _SINGLE.sub(_blank, _CODE.sub(code, sentence)) if re.search("[`'\"“‘]|https?:", sentence) else sentence
    return text, _TOKEN.sub(lambda m: "x" * len(m.group(0)), text) if re.search("[/:]", text) else text


def _starts(rx: re.Pattern, text: str) -> list[int]:
    return [m.end() for m in rx.finditer(text)]


def _last_before(points: list[int], pos: int) -> int:
    i = bisect.bisect_right(points, pos) - 1
    return points[i] if i >= 0 else 0


def _negated_list_end(text: str, start: int) -> int:
    """start 의 언급이 부정된 나열("did not merge, tag, publish or send")의 첫 항이면 나열의 끝, 아니면 start.

    항은 네 낱말 이하이고, 나열은 or·nor 로 닫혀야 한다. 닫히지 않은 쉼표 나열("did not merge #5, pushed v1")은
    부정을 나눠 갖지 않는다.
    """
    window = text[start:start + 200]
    cut = _LIST_CUT.search(window)
    window = window[:cut.start()] if cut else window
    parts = _LIST_SEP.split(window)
    pos, end = start, start
    for i in range(0, len(parts), 2):
        item = parts[i]
        if len(item.split()) > 4 or not item.strip():
            break
        if i >= 2 and re.search(r"\b(?:or|nor)\b", parts[i - 1], re.I):
            end = pos + len(item)
        pos += len(item) + (len(parts[i + 1]) if i + 1 < len(parts) else 0)
    return end


def _negated(text: str, start: int, end: int, clause_start: int, lang: str) -> bool:
    """이 언급을 부정이 직접 다스리는가. 이중 부정이나 다른 말에 붙은 부정은 아니다."""
    if lang == "ko":
        if _KO_NEG_PRE.search(text[max(0, start - 3):start]):
            return not _KO_DOUBLE.search(text[end:end + 12])
        tail = text[end:end + 16]
        stop = _KO_STOP.search(tail)
        tail = tail[:stop.start()] if stop else tail
        found = _KO_NEG_POST.search(tail)
        return bool(found) and not _KO_DOUBLE.search(text[end + found.end():end + found.end() + 12])
    before = text[max(clause_start, start - 60):start]
    if _NEG_PRE.search(before):
        return len(_NEG_WORD.findall(before)) < 2
    return bool(_NEG_POST.match(text, end, min(len(text), end + 60)))


def _push_target(raw: str, masked: str, start: int, end: int, segment_start: int, segment_end: int, lang: str) -> str:
    """push 한 것: tag · main · branch · unclear. 영어는 동사 뒤, 한국어는 동사 앞에서 읽는다.

    태그·버전은 경로를 가린 글에서 찾는다(feat/tag-cleanup 의 tag 는 대상이 아니다). refspec 은 원문에서 읽는다.
    """
    span = (max(segment_start, start - 80), end) if lang == "ko" else (end, min(segment_end, end + 100))
    window, plain = raw[span[0]:span[1]], masked[span[0]:span[1]]
    head = raw[max(segment_start, start - 30):start]
    if _PUSH_TAG.search(plain) or re.search(r"--tags\b|refs/tags/", window):
        return "tag"
    if _PUSH_MAIN.search(window) or re.search(r"force[- ]?$", head, re.I) or raw[start:end].lower().startswith("force"):
        return "main"
    return "branch" if _PUSH_BRANCH.search(window) else "unclear"


def _act_findings(sentence: str, actions: Iterable[str]) -> list[tuple[str, str, str]]:
    """바깥으로 나가는 사람 전용 행동의 (행동, 구절, 종류). 종류는 claim · request · unclear 다.

    언급은 부정이 그 행동을 직접 다스리거나, 사람이 한다고 적었거나(미룸), 이름 붙은 작업 브랜치의 push 거나,
    짝말·명사·3인칭·종속절의 설명일 때만 빠진다. 나머지는 모두 끼어든다.
    """
    actions = [a for a in actions if _ANY_FORM[a].search(sentence)]
    if not actions:
        return []
    raw, text = _prepared(sentence)
    asked: list[bool] = []

    def requested() -> bool:
        # 문장이 시키거나 묻거나 하려 하는가. 필요할 때 한 번만 본다.
        if not asked:
            asked.append(bool(_INTENT.search(text) or _HANDOFF.search(text) or _MARKER.search(text)
                              or _ASK.search(text) or _QUESTION.search(text)))
        return asked[0]
    clause_starts = _starts(_CLAUSE_END, text)
    segment_starts = _starts(_SEGMENT_END, text)
    # 사람이 한다고 적은 구간을 겹침 없이 모아 둔다. 언급마다 bisect 로 찾으므로 길이에 비례한다.
    attributed: list[list[int]] = []
    for m in _ATTRIBUTED_TO_PERSON.finditer(text):
        lo = max(_last_before(segment_starts, m.start()), m.start() - _REACH)
        if _ASK.search(text[lo:m.end()]):
            continue
        if attributed and lo <= attributed[-1][1]:
            attributed[-1][1] = max(attributed[-1][1], m.end())
        else:
            attributed.append([lo, m.end()])
    attributed_starts = [a[0] for a in attributed]
    has_message, has_live = bool(_MESSAGE.search(text)), bool(_LIVE.search(text))
    local_only, has_data = bool(_LOCAL.search(text)) and not has_live, bool(_DATA.search(text))
    mentions = sorted((m.start(), m.end(), action, lang, form)
                      for action in actions for lang, form, rx in _FORM_RX[action] for m in rx.finditer(text))
    cancelled_until = -1
    out: list[tuple[str, str, str]] = []
    recorded: set[str] = set()
    for start, end, action, lang, form in mentions:
        # 한 문장에서 걸린 행동의 다음 언급은 결과를 바꾸지 못하므로 건너뛴다. 긴 출력에서도 일이 일정하다.
        if action in recorded:
            continue
        word = text[start:end].lower()
        # 절과 구간의 시작은 언급에서 _REACH 글자 안으로 자른다. 긴 출력에서도 언급 하나의 일은 일정하다.
        clause_start = max(_last_before(clause_starts, start), start - _REACH)
        segment_start = max(_last_before(segment_starts, start), start - _REACH)
        nxt = bisect.bisect_right(segment_starts, end)
        segment_end = segment_starts[nxt] if nxt < len(segment_starts) else len(text)
        if start < cancelled_until:
            continue
        if _negated(text, start, end, clause_start, lang):
            # 부정된 동사의 목적어와, or·nor 로 닫힌 나열의 나머지 항도 같은 부정 아래 있다.
            tail = re.split(r"[,;.:?!]|\b(?:and|but|then|so|or|nor)\b", text[end:end + 40], maxsplit=1, flags=re.I)[0]
            reach = max(end + len(tail), _negated_list_end(text, start) if lang == "en" else end)
            cancelled_until = max(cancelled_until, reach)
            continue
        if _COMPOUND.match(text, start) or _KO_COMPOUND.match(text, start):
            continue
        before = text[max(clause_start, start - 60):start]
        past = lang == "en" and bool(re.match(r"[a-z'-]*(?:ed|sent|'d)\b", word))
        done = lang == "ko" and bool(_KO_DONE.match(text, end))
        # 남이 주어인 과거 보고. 이 실행이 같은 절에서 나서면("on their behalf", "제가") 주장이다.
        if past and _OTHER_SUBJECT.search(before[-40:]) and not _SELF_ANY.search(text[clause_start:end + 40]):
            continue
        if done and _KO_OTHER.search(text[segment_start:start]) and not _SELF_ANY.search(text[segment_start:end]):
            continue
        # 사람이 한다고 적은 구간(미룸). 이 실행의 과거형 보고와 "제가" 가 있는 한국어는 지우지 않는다.
        k = bisect.bisect_right(attributed_starts, start) - 1
        if k >= 0 and start < attributed[k][1] and not past and not (
                lang == "ko" and _SELF_ANY.search(text[segment_start:end])):
            continue
        if form == "third" and not _SELF_SUBJECT.search(before[-40:]):
            continue
        noun = form == "noun" or (lang == "en" and form in {"act", "push"} and not past and (
            bool(_DETERMINER.search(before[-16:])) or (bool(_MERGE_KIND.search(before))
                                                 and not _OBJECT.match(text, end)))) or (
            lang == "ko" and bool(_KO_WAITED.match(text, end)))
        if noun and not requested():
            continue
        # 종속절(when·where·if·once …)의 언급은 설명이다. 과거형은 수동("where a tag is pushed")일 때만 설명이다.
        if _SUBORDINATE.search(before) and not requested() and not (past and not _PASSIVE.search(before[-24:])):
            continue
        kind = "claim" if past or done or re.search(
            r"\b(?:ran|executed|invoked|done|completed)\b", text[clause_start:end + 30], re.I) else "request"
        if form == "push":
            target = _push_target(raw, text, start, end, segment_start, segment_end, lang)
            if target == "branch":
                continue
            kind = "unclear" if target == "unclear" else kind
        elif form == "msg" and not has_message:
            continue
        elif (action == "send" and lang == "en" and not has_message
              and _TECH_SEND.search(text[max(clause_start, start - 30):end + 40])):
            continue
        elif action == "migration":
            if local_only:
                continue
            ran = (_RUN_BEFORE.search(before) or _RUN_AFTER.match(text, end)) if lang == "en" \
                else _KO_RUN.match(text, end)
            if form == "run" and not ran and not requested():
                continue
            if form == "data" and not (has_live or has_data):
                continue
        recorded.add(action)
        out.append((action, sentence[max(clause_start, start - 40):min(len(sentence), end + 60)].strip(), kind))
    return out

_KIND_TEXT = {
    "claim": "reads as a report that this person-only action happened",
    "request": "asks for this person-only action or offers to take it",
    "unclear": ("mentions a push whose target is not shown to be a work branch, so it is read as "
                "push_tag (name the branch, as in 'pushed branch fix/x', if that is what it was)"),
    "field": "names it on a PERSON-ONLY line",
    "requested": "is a person-only action the host asked for in person_only_requests",
}


# 사람 전용 행동을 하지 않았거나 사람 몫으로 남길 때 쓰라고 권하는 말. 이 게이트가 통과시키는 말이어야 한다(시험이 본다).
_ADVICE = {
    "merge": ("did not merge", "merging is left to the owner"),
    "push_tag": ("did not push a tag", "pushing the tag is left to the owner"),
    "publish": ("did not publish", "publishing is left to the owner"),
    "send": ("did not send it", "sending is left to the owner"),
    "migration": ("did not run the migration", "running the migration is left to the owner"),
}


def _message(finding: Mapping[str, str]) -> str:
    """거절 한 건을 한 문장으로: 검사, 행동 id, 걸린 구절, 고치는 법."""
    check, action, phrase = finding["check"], finding["action"], finding["phrase"]
    head = f'{check} ({action}): "{phrase}"'
    if finding["kind"] in _KIND_TEXT:
        undone, left = _ADVICE.get(action, ("did not do it", "it is left to the owner"))
        return (head + " " + _KIND_TEXT[finding["kind"]] + ". A person answers before the run goes on. "
                f"If it did not happen, say so with the verb, for example '{undone}'; if it is the "
                f"person's step, say that, for example '{left}'.")
    if finding["kind"] == "unproven":
        return (head + " claims a side effect with no host proof. Accepted proof is a ToolProof the host "
                f"attaches from its own observed call: action='{finding['proof']}', target=<what it ran>, "
                "call_id=<its call id>, success=True, claim=<this whole sentence, exactly>. A channel with no "
                "host proof (vinc_run_submit over MCP) cannot carry one: say what was attempted and where to "
                "read the result, for example 'Attempted to run the tests; the result is in the pull request "
                "checks'. A person-only action (merge, tag push, publish, send, live migration) is not "
                "restated as attempted: say it was not done and is left to the person, for example "
                "'did not merge; merging is left to the owner'.")
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
