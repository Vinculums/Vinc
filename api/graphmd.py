# -*- coding: utf-8 -*-
"""GRAPH.md 생성·검증 — 어시스턴트 쓰기 관례 계약 (설계정본 [[vinc.canon.graph-conventions]]).

파일 한 장이 **두 구역**으로 나뉜다:

- **산문 구역** — 사람이 쓴다. 노드 정의·입도 예시·금지. 스토어가 모르는 것들이라
  여기서 생성하지 않는다(자동 생성 컨텍스트 파일이 오히려 성능을 떨어뜨린다는 외부
  연구를 이 경계로 흡수한다 — 설계정본 §5).
- **생성 구역** — 마커 사이. 도메인 목록과 재가된 어휘를 스토어에서 그대로 옮겨 적는다.
  **정본은 스토어**이고 이 블록은 그 사본이다. 손으로 고치면 다음 build 가 지운다.

한 문장 규정: **스토어가 아는 것은 스토어가 쓰고, 스토어가 모르는 것만 사람이 쓴다.**

`check` 는 **형상·참조 무결성만** 본다. "입도가 맞는가·관계가 의미상 옳은가"는 검증하지
않는다 — 의미 판단은 어시스턴트의 몫이라는 제품 원칙을 검증기가 침범하면 안 된다.

exit code 는 기존 게이트와 동형: 0 PASS · 1 FIX-only · 2 BLOCK.
"""
from __future__ import annotations

import hashlib
import re

from store import theme_store

# 관례 문서 id 판정은 store 로 내렸다(검색 팔이 api 를 부르지 않게). 근거: [[vinc.plan.team-split]]
from store.querybase import (  # noqa: E402,F401  (재수출)
    LEGACY_CONVENTIONS_DOC_ID,
    _CONVENTIONS_PREFIX,
    _OWNER_TAG_RE,
    is_conventions_doc_id,
    is_conventions_chunk_id,
)

SPEC_VERSION = 1

#: 그 문서의 출처 표식(일반 자료와 구분하는 유일한 표시).
CONVENTIONS_ORIGIN = "vinc:conventions"


def conventions_doc_id(owner: str | None) -> str:
    """이 owner 가 **새로 쓸 때** 쓰는 관례 문서 id.

    owner 가 없으면(단일 사용자 로컬 스토어) 구 고정 id 그대로다 — 가를 소유자가
    없는 자리에 해시를 붙이면 읽을 수 없는 id 만 남는다. 동기화에서 제외된다
    (`docsync`) — 로컬 그래프와 계정 그래프는 **각자의 관례**를 갖는다는 것이
    불변식이다([[vinc.canon.graph-conventions]] §6.3).

    ⚠ 이미 구 id 에 관례를 써 둔 owner 는 이 값이 아니라 **그 자리**를 계속 쓴다.
    판정은 `docstore.resolve_conventions_doc_id` 하나이고, 여기를 직접 부르는 곳은
    그 함수뿐이어야 한다."""
    if not owner:
        return LEGACY_CONVENTIONS_DOC_ID
    tag = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
    return _CONVENTIONS_PREFIX + tag





HEADER_RE = re.compile(r"^<!--\s*vinc-graph-md:\s*(\d+)\b(.*)-->\s*$")
GEN_START_RE = re.compile(r"^<!--\s*vinc:generated start\b.*-->\s*$")
GEN_END_RE = re.compile(r"^<!--\s*vinc:generated end\s*-->\s*$")
# GM-11a — 태그는 **가변성** 3분류: [스키마 고정](이 파일로 못 바꾼다) ·
# [이 그래프가 정함](이 그래프의 관례다) · [주제마다 다름](주제에 맞춰 고쳐 써라).
# 구 2분류 [core]/[vinc] 는 **출처**(범용/바인딩)를 말해 독자가 가변성을 거꾸로
# 읽었다([vinc] 가 오히려 못 바꾸는 것). 기존 사용자 파일이 구 태그를 달고 있으므로
# 파서는 신·구 어휘를 모두 받는다 — 템플릿이 내보내는 것은 신 태그뿐이다.
HEADING_RE = re.compile(
    r"^##\s+(.*?)\s*(?:\[(core|vinc|schema-fixed|this graph decides|varies by topic|"
    r"스키마 고정|이 그래프가 정함|주제마다 다름)\])?\s*$")
TOOL_RE = re.compile(r"\bvinc_[a-z_]+\b")

#: wire 계약의 엣지 이름(`api/fragment.py` `_EDGE_TYPES`). 재가 어휘와 별개로 항상 유효하다.
#: 뒤의 둘(supersedes·returns_to)은 템플릿 Relations 절의 여섯 밖에 있지만 스키마 엣지라
#: 제안이 아니다. 둘 다 근거가 없으면 쓰기가 건너뛴다. 생성 구역이 그 사실을 적는다.
WIRE_EDGES = ("relates_to", "prerequisite_of", "derives_from",
              "inherits_from", "connects_via", "about", "supersedes", "returns_to")

#: 절 — (key, (EN 제목, ko 제목)). 순서가 곧 검사 순서다.
SECTIONS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("what", ("What this is", "이 파일은 무엇인가")),
    ("read", ("Read before you write", "쓰기 전에 읽을 것")),
    ("nodes", ("Node kinds", "노드 종류")),
    ("size", ("How big is one concept", "개념 하나의 크기")),
    ("relations", ("Relations", "관계")),
    ("writing", ("How a write behaves", "쓰기가 하는 일")),
    ("documents", ("Documents", "문서")),
    ("time", ("Time fields", "시간 필드")),
    ("validity", ("Validity", "유효구간")),
    ("provenance", ("Claim provenance", "주장의 출처")),
    ("roles", ("Roles", "역할")),
    ("topics", ("Topics", "주제")),
    ("domains", ("Domains in this graph", "이 그래프의 도메인")),
    ("vocab", ("Ratified vocabulary", "재가된 어휘")),
    ("donot", ("Do not", "하지 말 것")),
)
#: 있으면 순서를 보고, 없어도 결함이 아닌 절. roles·topics·validity 는 2026-09-05·09 에,
#: writing·time·provenance 는 2026-11-05 에 더했다 — 필수로 넣으면 그 전에 만들어진 모든
#: 파일이 BLOCK 이 된다(산문은 build 가 안 채운다. 스키마 절은 읽을 때 채워진다).
OPTIONAL_SECTIONS = frozenset({"roles", "topics", "validity", "writing", "time", "provenance"})

#: 코드가 정하는 절. 이 절들은 **손으로 쓰지 않는다**: 읽을 때마다 아래 렌더러가 코드의 상수에서
#: 다시 만들어, 템플릿이든 소유자 문서든 그 자리를 갈아 끼운다(없으면 제자리에 끼운다).
#: 손으로 쓴 스키마 설명은 코드와 어긋났고(evidence 필수 범위·relates_to 끝점·Entity 의 손실),
#: 소유자 문서가 템플릿을 통째로 대신하므로 템플릿을 고쳐도 그 사람에게 닿지 않았다.
#: 근거: [[decision:graph-md-schema-sections-are-generated-from-code-on-every-read-2026-11-05]]
SCHEMA_KEYS = ("nodes", "relations", "writing", "documents", "time", "roles")


def _title(key: str, lang: str) -> str:
    en, ko = dict(SECTIONS)[key]
    return ko if lang == "ko" else en


# ── 스키마 절(코드에서 생성) ──────────────────────────────────────────────────
# 문장은 여기 있고 **값은 코드 상수에서 온다**: 종류별 보존 필드(`fragment._KEPT_FIELDS`),
# 결정의 구간 자리(`fragment._INTERVAL_FIELDS`), 엣지 끝점 쌍(`fragment._ALLOWED_PAIRS_HINT`,
# 쓰기 경로가 거절할 때 내는 바로 그 문장), 근거를 저장하는 관계(`embedded_schema.EDGE_EVIDENCE_RELS`),
# 역할 계약(`role_contract`). 상수가 바뀌면 이 절도 함께 바뀐다(테스트가 대조한다).
# EN/ko 는 줄 단위로 1:1 이다(test_templates_are_line_for_line_parallel).

#: 종류별 한 줄 — (종류 키, 라벨, EN 뜻, ko 뜻). 순서가 화면 순서다.
_KIND_LINES = (
    ("concept", "Concept", "one reusable idea", "재사용되는 하나의 관념"),
    ("decision", "Decision", "what was chosen at a point in time, and why; tie it with about",
     "그때 무엇을 골랐고 왜인지. about 으로 대상에 잇는다"),
    ("record", "Record", "a note or a measurement", "메모나 측정"),
    ("episode", "Episode", "something that happened; give its date", "일어난 일. date 를 적는다"),
    ("bridge", "Bridge", "a junction between topics", "주제 사이의 매듭"),
)
#: 필드 표시 순서(집합을 그대로 늘어놓으면 매번 순서가 바뀐다).
_FIELD_ORDER = ("title", "domain", "tags", "one_liner", "summary", "date", "content_time",
                "valid_from", "valid_until", "props")
#: 관계 뜻 — 의미는 제품 계약이고 끝점은 아래 쌍 목록이 말한다.
_REL_MEANING = (
    ("relates_to", "loose association, undirected", "느슨한 연관, 무방향"),
    ("prerequisite_of", "A comes before B", "A 가 B 보다 먼저"),
    ("derives_from", "A comes out of B", "A 가 B 에서 나왔다"),
    ("inherits_from", "A inherits from B", "A 가 B 를 상속한다"),
    ("connects_via", "a Bridge to what it joins", "Bridge 에서 그것이 잇는 대상으로"),
    ("about", "an Episode or Decision to what it is about", "Episode 나 Decision 에서 그 대상으로"),
    ("supersedes", "the new claim to the old one it replaces", "새 주장에서 그것이 대체한 옛 주장으로"),
    ("returns_to", "work handed back between two roles", "두 역할 사이에서 일을 되돌려 보냄"),
)
#: 근거를 **반드시** 요구하는 엣지(없으면 그 엣지가 건너뛰어진다 — `fragment` 의 supersedes·returns_to 분기).
_EVIDENCE_REQUIRED = ("supersedes", "returns_to")


def _kept(label: str) -> list[str]:
    from . import fragment as _fr
    have = _fr._KEPT_FIELDS.get(label, frozenset())
    return [f for f in _FIELD_ORDER if f in have]


def _evidence_edges() -> list[str]:
    """근거 열이 있는 관계를 쓰기 이름으로. ATTACHES_TO 는 Record 의 엣지 넷이 쓰는 테이블이다."""
    from store.embedded_schema import EDGE_EVIDENCE_RELS
    from . import fragment as _fr
    back = {v: k for k, v in {**_fr._CC_EDGES, **_fr._EC_EDGES}.items()}
    back.update({_fr._SUPERSEDES_REL: _fr._SUPERSEDES_EDGE, "ATTACHES_TO": "record edges"})
    return [back.get(r, r.lower()) for r in EDGE_EVIDENCE_RELS]


def _pairs() -> tuple[str, list[str]]:
    """`_ALLOWED_PAIRS_HINT` → (머리말, 쌍 목록). 쓰기 경로의 거절 문장과 같은 출처다."""
    from . import fragment as _fr
    head, _sep, rest = _fr._ALLOWED_PAIRS_HINT.partition(": ")
    return head, [p.strip() for p in rest.split("; ") if p.strip()]


def schema_section(key: str, lang: str) -> list[str]:
    """코드에서 만든 스키마 절 하나(제목 줄 포함)."""
    from . import fragment as _fr
    from . import role_contract as _rc
    ko = lang == "ko"
    tag = "[스키마 고정]" if ko else "[schema-fixed]"
    out = [f"## {_title(key, lang)} {tag}"]
    if key == "nodes":
        prefixes = ", ".join(f"{k}:" for k in _fr._NODE_MAP)
        out.append(f"id 접두사가 테이블을 정한다({prefixes}). 접두사와 다른 type 은 건너뛴다(prefix_mismatch)."
                   if ko else
                   f"The id prefix picks the table ({prefixes}). A type that disagrees with it is skipped (prefix_mismatch).")
        for k, label, en, kr in _KIND_LINES:
            fields = ", ".join(_kept(label))
            out.append(f"- {label}: {kr}. 보존: {fields}." if ko else f"- {label}: {en}. Keeps {fields}.")
        ent = ", ".join(_kept(_fr._ENTITY_LABEL))
        out.append(f"- 그 밖의 type 은 Entity: {ent} 만 보존하고 domain·props 는 경고와 함께 버린다. Entity 끼리만 잇는다."
                   if ko else
                   f"- Any other type is an Entity: keeps only {ent}; domain and props are dropped with a warning. It links only to other Entities.")
        out.append("- Domain 은 주제 경로에서 저절로 생긴다. 노드로 쓰지 않는다."
                   if ko else "- Domain nodes are made from topic paths. Do not write them.")
        out.append("종류가 보존하지 않는 필드는 경고와 함께 버려진다. 제목 없는 Record·Episode 는 빈 제목, Entity 는 건너뜀."
                   if ko else
                   "A field a kind does not keep is dropped with a warning. A Record or Episode without a title stays blank; an Entity without one is skipped.")
        claim = ", ".join(_fr._CLAIM_PROPS)
        out.append(f"Record 의 예약 props: {claim}. subject_path 는 repo:path 로 쓴다(주장의 출처 절)."
                   if ko else
                   f"Reserved Record props: {claim}. Write subject_path as repo:path (see Claim provenance).")
    elif key == "relations":
        for name, en, kr in _REL_MEANING:
            out.append(f"- {name}: {kr}" if ko else f"- {name}: {en}")
        head, pairs = _pairs()
        out.append("허용되는 끝점 쌍(쓰기 경로가 그대로 강제한다. concept 과 decision 은 서로 바꿔 설 수 있다):"
                   if ko else f"Endpoint pairs the write path accepts {head.replace('allowed pairs ', '')}:")
        out.extend(f"  {p}" for p in pairs)
        req = (" · " if ko else " and ").join(_EVIDENCE_REQUIRED)
        stored = ", ".join(e for e in _evidence_edges() if e not in _EVIDENCE_REQUIRED)
        out.append(f"evidence 필수: {req}(없으면 그 엣지를 건너뜀). 저장만 함: {stored}. 그 밖의 엣지에서는 경고와 함께 버린다."
                   if ko else
                   f"Evidence is required on {req} (the edge is skipped without it), stored on {stored}, and dropped with a warning elsewhere.")
        out.append("맞는 쌍이 없으면 방향을 뒤집지 말고 Record 나 Episode 로 그 사실을 적는다."
                   if ko else "When no pair fits, never flip a direction to make it fit; write a Record or Episode instead.")
    elif key == "writing":
        out += (["빠진 필드는 보존된다. props 는 보낸 것이 통째로 저장된다(빠진 키는 사라진다).",
                 "키 일부만 바꾸려면 props_patch 와 vinc_node 에서 읽은 expected_rev 를 함께 보낸다.",
                 "응답을 읽는다: stored 가 남은 값이고, warnings 가 버려지거나 건너뛴 것을 모두 말하며, partial 은 일부만 들어갔다는 뜻이다. merged 수는 증거가 아니다.",
                 "create_only 는 이미 있는 id 를 거절한다."]
                if ko else
                ["Omitted fields are kept. props is stored whole as sent (keys you leave out are gone).",
                 "To change some keys only, send props_patch with the expected_rev you read from vinc_node.",
                 "Read the reply: stored is what was kept, warnings name everything dropped or skipped, partial means not all of it landed. merged counts are not proof.",
                 "create_only refuses an id that already exists."])
    elif key == "documents":
        out += (["정본 텍스트는 vinc_doc_put 으로 저장하고, 개념은 문서에 잇는다. 본문을 Record 에 복사하지 않는다.",
                 "valid_from·valid_until·superseded_by 는 도구 인자로 보낸다(본문 머리말은 읽지 않는다). needs_embed 가 true 면 벡터가 아직이다. 문서를 지워도 concept:doc 미러는 남는다."]
                if ko else
                ["Store canonical text through vinc_doc_put and link concepts to the document; do not copy the text into a Record.",
                 "Pass valid_from, valid_until and superseded_by as tool arguments (front matter is not read). needs_embed true means vectors are pending. Deleting a document keeps its concept:doc mirror."])
    elif key == "time":
        ct = " · ".join(lb for _k, lb, *_ in _KIND_LINES if "content_time" in _kept(lb))
        rec, dec = _fr._INTERVAL_FIELDS["record"], _fr._INTERVAL_FIELDS["decision"]
        out += ([f"- content_time({ct}): 기록이 가리키는 일이 일어난 때. YYYY[-MM[-DD]].",
                 f"- 성립 구간: Record 는 {rec[0]}·{rec[1]}, Decision 은 {dec[0]}·{dec[1]}. 다른 종류에는 구간이 없다.",
                 "- Episode 는 date 하나다. 생성 시각이 없어 known_at 이 거르지 못한다.",
                 "- as_of 는 그때 성립하던 것(구간), known_at 은 그때 그래프가 알던 것(생성 시각)을 읽는다. 현재·과거 상태는 저장하지 않고 날짜에서 계산한다."]
                if ko else
                [f"- content_time ({ct}): when the described thing happened. YYYY[-MM[-DD]].",
                 f"- Validity: a Record uses {rec[0]} and {rec[1]}, a Decision {dec[0]} and {dec[1]}. Other kinds have no interval.",
                 "- An Episode has its date only. It has no creation time, so known_at cannot filter it.",
                 "- as_of reads what held during a period (intervals); known_at reads what the graph had recorded by then (creation time). Current or past is computed from dates, never stored."])
    elif key == "roles":
        req, tiers, rungs = ", ".join(_rc.REQUIRED), "|".join(_rc.TIERS), "|".join(_rc.RUNGS)
        topics = " · ".join(_rc.TOPICS)
        out += ([f"Agent 역할은 Concept 이다: id 는 {_rc.ID_PREFIX}<이름>, 제목은 직무명(\"Agent role:\" 접두 없이), 주제는 {topics} 이거나 그 바로 아래. 이 접두사는 역할 전용이다.",
                 f"필수 props: {req}. tier 는 {tiers}, rung 은 {rungs}. 목록은 목록으로 둔다.",
                 "props 는 통째로 바뀌므로 props_patch 로 고치거나 되읽어 합쳐 보낸다. 변경은 그 역할에 대한 Decision 과 함께 간다."]
                if ko else
                [f"An Agent role is a Concept: id {_rc.ID_PREFIX}<name>, titled with the job name (no \"Agent role:\" prefix), topic {topics} or one level below. The prefix is reserved for roles.",
                 f"Required props: {req}. tier is {tiers}, rung is {rungs}. Lists stay lists.",
                 "props is replaced whole, so change it with props_patch or read, merge and resend. A change ships with a Decision about the role."])
    else:
        raise KeyError(key)
    return out


#: 경고 60 → 70(2026-09-05). Roles·Topics 두 절이 아홉 줄을 더했고, 그만큼을 예시
#: 절에서 깎는 것은 규격이 "규칙 아니라 예시로" 두기로 한 것을 깎는 일이었다.
#: 근거: [[vinc.canon.graph-conventions §2]] (F2 개정) 와 그 결정 노드.
#:
#: 70 → 75(2026-09-09). Validity 절 넉 줄. 같은 판단이다 — 채택된 시간 정책이 집
#: 관례가 됐으니 이 파일에 있어야 하고([[decision:temporal-model-operational-policy-2026-09-09]]),
#: 자리를 만들려고 예시를 깎는 것은 위와 같은 이유로 거절한다. 하드 상한(100)은 그대로다.
LINE_WARN, LINE_FAIL = 75, 100
#: 화면 노출 금지 문자(D38). 이 템플릿은 문서 사이트에 그대로 실린다.
FORBIDDEN_GLYPHS = ("—",)   # em-dash


# ── 템플릿 ────────────────────────────────────────────────────────────────────
# 이 상수가 템플릿 정본이다. **본문 사본은 이제 없다** — 사이트 문서 재구성(Vinc-homepage
# #155) 이 `site/docs/graph-md/spec/index.html` 의 템플릿 원문(EN 코드블록 · ko `tpl:`)을
# 걷어 내고 **절 표**만 남겼다. 그러니 절을 더하거나 이름을 바꾸면 그 표를 함께 고친다.
# 절 안의 문구만 바꿀 때는 고칠 사본이 없다(2026-09-25 확인).

_TPL_EN = """# GRAPH.md
{header}

## What this is [this graph decides]
The house style for writing into this knowledge graph. Read it before your first write
in a session. Judging meaning is your job. Sections marked [schema-fixed] are written
from the code on every read; the others are this graph's to change.

## Read before you write [this graph decides]
Search first (vinc_search), reuse the ids you get back, and prefer attaching to an
existing concept over creating a near duplicate. Ids carry their kind as a prefix:
concept:optimistic-locking, decision:drop-the-cache, record:pricing-note.
Titles: pick one language for this graph and keep to it.

## How big is one concept [varies by topic]
GOOD "Optimistic concurrency control": reusable, self standing, one idea.
GOOD "Sunk cost fallacy": same shape, different field.
BAD "Database stuff": several ideas in a bag. Split it.
BAD "What I read today": not a concept. That is an Episode.
BAD "PostgreSQL" and "Postgres" as two nodes: the same thing. Keep one.
Test: if you cannot finish the sentence "X is ..." in two sentences, it is too big.
People and products pass that test but are not ideas: write them as a Record.

## Validity [this graph decides]
A Record asserting a present state carries valid_from; retire it with valid_until instead of deleting history.
Use props.entity, predicate, scope and value to identify comparable claims; change existing props with props_patch.
An unstated valid_from reads as current for legacy visibility, so it is not hidden; temporal metadata stays unknown. Recording time is not validity.
Confirm replacement before setting valid_until and an evidenced new-to-old supersedes edge. Advisories never do it. Derive currency from intervals; never store a status of stale, current or new.

## Claim provenance [this graph decides]
- measured_at is the day the code was actually looked at. A migration or a backfill never overwrites it.
- measured_ref is the branch or commit looked at, one per record; records written in bulk do not share one.
- reviewed_at is set only after the record was checked against the code again, never by a batch job.
- content_time is when the described event or measurement happened.
- A date inferred from a title, a creation date or an author date is marked "inferred" in props.reconciliation_note and stays until its author confirms it.

## Topics [this graph decides]
A topic is the domain field. Shape it area/discipline/subject, two or three levels, lowercase.
Paths are case-sensitive and a new path creates its whole chain at once, so pick a leaf with vinc_domains first.
A leaf holds ten to sixty nodes: past sixty split it, under five fold it into its parent.

{generated}

## Do not [this graph decides]
- Do not ratify your own proposal. A person does that.
- Do not create a node for something you did not search for first.
- Do not start a new topic before checking vinc_domains for a leaf that fits.
"""

_TPL_KO = """# GRAPH.md
{header}

## 이 파일은 무엇인가 [이 그래프가 정함]
이 지식그래프에 쓸 때의 관례입니다. 세션에서 첫 쓰기를 하기 전에 읽으십시오.
의미를 판단하는 것은 쓰는 사람의 일입니다. [스키마 고정] 표시가 붙은 절은 읽을 때마다
코드에서 다시 만들어지고, 나머지 절은 이 그래프가 정합니다.

## 쓰기 전에 읽을 것 [이 그래프가 정함]
먼저 검색하고(vinc_search), 돌려받은 id 를 재사용하고, 비슷한 개념을 새로 만들기보다
기존 개념에 붙이십시오. id 는 종류를 접두사로 답니다:
concept:optimistic-locking, decision:drop-the-cache, record:pricing-note.
제목: 이 그래프의 언어를 하나로 정해 지키십시오.

## 개념 하나의 크기 [주제마다 다름]
GOOD "낙관적 동시성 제어": 재사용되고, 자립적이고, 하나의 관념입니다.
GOOD "매몰비용 오류": 같은 모양, 다른 분야.
BAD "데이터베이스 관련": 여러 관념을 한 자루에 담았습니다. 쪼개십시오.
BAD "오늘 읽은 것": 개념이 아닙니다. Episode 입니다.
BAD "PostgreSQL" 과 "Postgres" 를 각각: 같은 것입니다. 하나만 두십시오.
기준: "X 는 ...이다" 를 두 문장으로 못 끝내면 너무 큽니다.
인물·제품은 이 기준을 통과하지만 관념이 아닙니다: Record 로 넣으십시오.

## 유효구간 [이 그래프가 정함]
현재 상태를 주장하는 Record 에는 valid_from 을 적고, 과거 기록을 지우는 대신 valid_until 로 유효기간을 닫으십시오.
비교할 주장에는 props.entity, predicate, scope, value 를 적으십시오. 기존 props 는 props_patch 로 고치십시오.
valid_from 을 안 적으면 기존 가시성 정책에서 current 로 읽으므로 숨기지 않습니다. 시간 정보는 unknown 이며, 기록 시각은 유효기간이 아닙니다.
대체를 확인한 뒤 valid_until 과 근거 있는 새 주장→이전 주장 supersedes 관계를 쓰십시오. 검토 권고는 이를 자동 변경하지 않습니다. 현재성은 구간에서 판단하고 stale·current·new 를 저장하지 마십시오.

## 주장의 출처 [이 그래프가 정함]
- measured_at 은 코드를 실제로 확인한 날입니다. 이관이나 백필이 덮어쓰지 않습니다.
- measured_ref 는 확인한 브랜치나 커밋이고 기록마다 하나입니다. 일괄로 쓴 기록들이 하나를 나눠 쓰지 않습니다.
- reviewed_at 은 기록을 코드와 다시 대조한 뒤에만 적습니다. 일괄 작업은 적지 않습니다.
- content_time 은 기록이 가리키는 사건이나 측정이 일어난 때입니다.
- 제목·생성일·작성자 날짜로 추정한 날짜는 props.reconciliation_note 에 "추정"으로 표시하고, 작성자가 확인하기 전까지 그대로 둡니다.

## 주제 [이 그래프가 정함]
주제는 domain 필드입니다. 영역/분야/소재로, 두세 단계, 소문자로 짓습니다.
경로는 대소문자를 가리고 새 경로는 사슬 전체를 한 번에 만듭니다. 먼저 vinc_domains 에서 잎을 고르십시오.
잎 하나에 노드 열에서 예순: 예순을 넘으면 쪼개고, 다섯 아래면 부모로 접으십시오.

{generated}

## 하지 말 것 [이 그래프가 정함]
- 자기 제안을 스스로 재가하지 마십시오. 그것은 사람이 합니다.
- 검색해 보지 않은 것을 노드로 만들지 마십시오.
- vinc_domains 에서 맞는 잎을 찾아보기 전에 새 주제를 만들지 마십시오.
"""

_HEADER = {
    "en": "<!-- vinc-graph-md: {v} · built {day} · the store is canonical, this file is a view of it -->",
    "ko": "<!-- vinc-graph-md: {v} · built {day} · 정본은 스토어이고 이 파일은 그 사본입니다 -->",
}
_GEN_HEAD = {
    "en": "<!-- vinc:generated start · copied from the ontology below this line -->",
    "ko": "<!-- vinc:generated start · 여기부터 그래프에서 옮겨 적습니다 -->",
}
_GEN_TAIL = "<!-- vinc:generated end -->"
_GEN_BODY = {
    # ⚠ 줄 예산은 LINE_WARN 이다. 한 줄을 더하면 EN/ko 에 같이 더한다(1:1,
    # test_templates_are_line_for_line_parallel). "소문자 영어"는 Topics 절로 옮겼다.
    # 엣지 줄 뒤의 "Built in" 문장은 Relations 절의 여섯 밖에서 쓰기 경로가 받는 스키마 엣지 둘이다
    # (`api/fragment.py` `_EDGE_TYPES`·`WIRE_EDGES`). 빼면 Validity 절이 쓰라는 supersedes 를
    # 이 절이 부정하게 된다.
    "en": ("## Domains in this graph\n{domains}\n"
           "A slash makes a tree: 'vinc/design' files that node "
           "under 'vinc', with nothing extra to author. "
           "Walk it with vinc_neighbors('domain:vinc').\n\n"
           "## Ratified vocabulary\n"
           "node types: {nodes}\n"
           "edge types: {edges} (beyond the eight in Relations).\n"
           "Anything else you write is kept and marked proposed, for a person to ratify."),
    "ko": ("## 이 그래프의 도메인\n{domains}\n"
           "슬래시가 계층이 됩니다: 'vinc/design' 은 그 노드를 'vinc' "
           "아래 둡니다. 따로 저작할 것이 없습니다. "
           "vinc_neighbors('domain:vinc') 로 걸어 내려가십시오.\n\n"
           "## 재가된 어휘\n"
           "노드 타입: {nodes}\n"
           "엣지 타입: {edges}(관계 절의 여덟 외에).\n"
           "그 밖에 쓰는 것은 보존되고 제안으로 표시되어, 사람의 재가를 기다립니다."),
}
_EMPTY = {
    "en": {"domains": "No domains yet. Your first import creates them.",
           "nodes": "none beyond the kinds above", "edges": "none ratified yet"},
    "ko": {"domains": "아직 도메인이 없습니다. 첫 적재가 도메인을 만듭니다.",
           "nodes": "위 종류 외에 없음", "edges": "아직 재가된 것 없음"},
}
_MORE = {"en": "and {n} more", "ko": "외 {n}개"}

#: 도메인 목록의 상한. 넘치면 "외 N개" — 목록이 70줄 예산을 먹게 두지 않는다.
DOMAIN_LIMIT = 12


def _lang_of(text: str) -> str:
    """문서의 언어를 제목에서 판별한다(EN·ko 2종 출하). 모르면 en."""
    for _key, (_en, ko) in SECTIONS:
        if re.search(r"^##\s+" + re.escape(ko), text, re.M):
            return "ko"
    return "en"


# ── 생성 ──────────────────────────────────────────────────────────────────────
def collect(store, *, owner: str | None = None, themes=None) -> dict:
    """생성 구역이 쓰는 값만 스토어에서 모은다 — 도메인·재가 어휘, 그 둘뿐이다."""
    from . import query as q

    doms: list[str] = []
    res = q.domains(owner=owner, store=store)
    if res.get("executed"):
        doms = [str(r.get("domain")) for r in res.get("rows", []) if r.get("domain")]

    nodes: list[str] = []
    edges: list[str] = []
    if themes is not None:
        # 소유자 자기 테마가 있으면 그것을, 없으면 활성 테마를 본다. 호스티드에서는
        # 쓰기가 소유자 파생 테마로 가므로(fragment._ThemeFold) 읽기도 같은 자리를
        # 봐야 한다 — 안 그러면 자기가 제안한 타입이 자기 GRAPH.md 에 안 보인다.
        # 로컬은 자기 테마가 없어 활성("default")로 떨어진다: 기존 동작 그대로.
        theme_id = ""
        if owner:
            mine = theme_store.owner_theme_id(owner)
            if themes.get_theme(mine):
                theme_id = mine
        theme_id = theme_id or themes.active_theme_id()
        if theme_id:
            for t in themes.list_types(theme_id, status="authored"):
                (nodes if t.get("kind") == "node" else edges).append(str(t.get("name")))
    return {"domains": doms, "node_types": sorted(nodes), "edge_types": sorted(edges),
            "domain_counts": {str(r.get("domain")): int(r.get("total") or 0)
                              for r in (res.get("rows") or []) if r.get("domain")}}


def generated_block(facts: dict, *, lang: str = "en") -> str:
    """스토어 사실 → 생성 구역 텍스트(마커 포함)."""
    e = _EMPTY[lang]
    doms = list(facts.get("domains") or [])
    counts = facts.get("domain_counts") or {}
    if doms:
        shown = doms[:DOMAIN_LIMIT]
        parts = [f"{d} ({counts[d]})" if d in counts else d for d in shown]
        line = " · ".join(parts)
        if len(doms) > DOMAIN_LIMIT:
            line += " · " + _MORE[lang].format(n=len(doms) - DOMAIN_LIMIT)
    else:
        line = e["domains"]
    body = _GEN_BODY[lang].format(
        domains=line,
        nodes=", ".join(facts.get("node_types") or []) or e["nodes"],
        edges=", ".join(facts.get("edge_types") or []) or e["edges"])
    return _GEN_HEAD[lang] + "\n" + body + "\n" + _GEN_TAIL


def _split(text: str) -> tuple[str, str, str] | None:
    """(마커 앞, 생성 구역, 마커 뒤). 마커가 정상 쌍이 아니면 None."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if GEN_START_RE.match(ln)]
    ends = [i for i, ln in enumerate(lines) if GEN_END_RE.match(ln)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return ("\n".join(lines[:starts[0]]),
            "\n".join(lines[starts[0]:ends[0] + 1]),
            "\n".join(lines[ends[0] + 1:]))


def build(store, *, owner: str | None = None, themes=None, existing: str | None = None,
          lang: str | None = None, day: str = "", restamp: bool = True) -> dict:
    """GRAPH.md 를 만들거나(신규) 생성 구역만 갈아 끼운다(갱신).

    **산문은 절대 건드리지 않는다.** 기존 파일을 주면 마커 사이만 교체하고 헤더의 날짜를
    갱신한다 — 사람이 쓴 입도 예시·금지 목록이 build 로 사라지면 아무도 이 명령을 두 번
    쓰지 않는다. 기존 파일의 마커가 깨져 있으면 교체하지 않고 오류로 돌린다(추측 금지).

    ``restamp=False`` 는 헤더 줄을 그대로 둔다 — **저장하지 않고 읽기 위해** 생성
    구역만 새로 계산하는 자리(`/conventions` 조회)가 쓴다. 거기서 날짜를 새로 찍으면
    저장된 문서에 없는 날을 응답이 말하게 된다. 저장된 built 날짜는 그대로 라벨이다.
    """
    # 언어는 우리가 아는 둘뿐이다. 모르는 값은 무시한다 — HTTP 로 `?lang=fr` 이 들어와
    # KeyError 로 500 이 나면, 관례를 읽으려던 어시스턴트가 빈손으로 돌아간다.
    lang = lang if lang in ("en", "ko") else None
    facts = collect(store, owner=owner, themes=themes)
    if existing:
        use = lang or _lang_of(existing)
        parts = _split(existing)
        if parts is None:
            return {"kind": "graphmd.build", "executed": False,
                    "error": "generated markers missing or unbalanced — 생성 구역을 찾지 못했다",
                    "facts": facts}
        head, _old, tail = parts
        if restamp:
            head = _restamp(head, use, day)
        text = head + "\n" + generated_block(facts, lang=use) + "\n" + tail
        text, _ins = with_schema_sections(text)
        return {"kind": "graphmd.build", "executed": True, "text": _tidy(text),
                "lang": use, "replaced": True, "facts": facts}
    use = lang or "en"
    tpl = _TPL_KO if use == "ko" else _TPL_EN
    text = tpl.format(header=_HEADER[use].format(v=SPEC_VERSION, day=day),
                      generated=generated_block(facts, lang=use))
    text, _ins = with_schema_sections(text)
    return {"kind": "graphmd.build", "executed": True, "text": _tidy(text),
            "lang": use, "replaced": False, "facts": facts}


def _is_heading(line: str, key: str) -> bool:
    """이 줄이 그 절의 제목인가. 두 언어의 제목을 다 본다(한 파일에 섞일 수 있다)."""
    return any(re.match(r"^##\s+" + re.escape(t) + r"(\s|\[|$)", line)
               for t in dict(SECTIONS)[key])


def with_schema_sections(text: str, keys: tuple[str, ...] = SCHEMA_KEYS) -> tuple[str, list[str]]:
    """스키마 절을 코드에서 만든 내용으로 갈아 끼운 **읽기용** 본문. (본문, 새로 끼운 절 키들).

    이미 있는 절은 태그가 무엇이든 본문째 바꾼다 — 이 절들은 파일이 정하는 것이 아니라
    쓰기 경로가 정하는 것이라, 손으로 고친 문장은 곧 틀린 문장이다. 없는 절은 규격 순서의
    제자리(다음에 오는 절 앞, 없으면 생성 구역 앞, 그것도 없으면 끝)에 끼운다.
    언어는 문서의 언어를 따른다. 근거: [[decision:graph-md-schema-sections-are-generated-from-code-on-every-read-2026-11-05]]"""
    lang = _lang_of(text)
    lines = text.splitlines()
    inserted: list[str] = []
    order = [k for k, _t in SECTIONS]
    for key in keys:
        sec = schema_section(key, lang)
        at = next((i for i, ln in enumerate(lines) if _is_heading(ln, key)), None)
        if at is not None:
            end = next((i for i in range(at + 1, len(lines))
                        if lines[i].startswith("## ") or GEN_START_RE.match(lines[i])), len(lines))
            while end > at + 1 and not lines[end - 1].strip():
                end -= 1
            lines = lines[:at] + sec + lines[end:]
            continue
        later = order[order.index(key) + 1:]
        # 다음 절 앞, 또는 생성 구역 앞 가운데 이른 쪽. 생성 구역 **안**의 제목(Domains)은 기준이
        # 아니다 — 그 앞에 끼우면 절이 마커 사이로 들어가 다음 build 가 지운다.
        nxt = next((i for i, ln in enumerate(lines) if any(_is_heading(ln, k) for k in later)), None)
        gen = next((i for i, ln in enumerate(lines) if GEN_START_RE.match(ln)), None)
        at = min((x for x in (nxt, gen) if x is not None), default=len(lines))
        lead = [""] if at > 0 and lines[at - 1].strip() else []
        lines = lines[:at] + lead + sec + [""] + lines[at:]
        inserted.append(key)
    out = "\n".join(lines).rstrip("\n") + ("\n" if text.endswith("\n") else "")
    return out, inserted


def with_roles_section(text: str) -> tuple[str, bool]:
    """Roles 절만 코드에서 만든 내용으로 맞춘다(호환용). (본문, 새로 끼웠나)."""
    out, inserted = with_schema_sections(text, ("roles",))
    return out, "roles" in inserted


def _restamp(head: str, lang: str, day: str) -> str:
    """헤더 줄의 생성 날짜만 새로 찍는다. 헤더가 없으면 그대로 둔다(사람의 파일이다)."""
    out = []
    done = False
    for ln in head.splitlines():
        if not done and HEADER_RE.match(ln):
            out.append(_HEADER[lang].format(v=SPEC_VERSION, day=day))
            done = True
        else:
            out.append(ln)
    return "\n".join(out)


def _tidy(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n"))
    return text.rstrip() + "\n"


# ── 검증 ──────────────────────────────────────────────────────────────────────
BLOCK, FIX = "BLOCK", "FIX"


def _f(rule: str, level: str, message: str, line: int | None = None) -> dict:
    out = {"rule": rule, "level": level, "message": message}
    if line is not None:
        out["line"] = line
    return out


def _headings(lines: list[str]) -> list[tuple[int, str]]:
    out = []
    for i, ln in enumerate(lines, 1):
        m = HEADING_RE.match(ln)
        if m:
            out.append((i, m.group(1).strip()))
    return out


def _section_body(lines: list[str], title: str) -> list[str]:
    """제목이 같은 절의 본문 줄(다음 `##` 전까지)."""
    body, inside = [], False
    for ln in lines:
        m = HEADING_RE.match(ln)
        if m:
            inside = m.group(1).strip() == title
            continue
        if inside:
            body.append(ln)
    return body


def prose_line_count(text: str) -> int:
    """사람이 쓴 줄 수. 스키마 절(`SCHEMA_KEYS`)과 생성 구역(마커 포함)은 빼고 센다."""
    lines = text.replace("\r\n", "\n").rstrip().split("\n")
    skip, in_gen = False, False
    n = 0
    for ln in lines:
        if GEN_START_RE.match(ln):
            in_gen = True
        if ln.startswith("## "):
            skip = any(_is_heading(ln, k) for k in SCHEMA_KEYS)
        if not (skip or in_gen):
            n += 1
        if GEN_END_RE.match(ln):
            in_gen = False
    return n


def check(text: str, *, store=None, themes=None, owner: str | None = None) -> dict:
    """형상 9규칙. `store` 를 주면 스토어 대조 규칙(4·5·6)까지 돈다.

    의미 검증은 하지 않는다 — 이 파일이 **좋은 관례인가**는 기계가 답할 수 없고,
    답하는 척하면 "의미 판단은 어시스턴트가 한다"는 원칙을 검증기가 어긴다.
    """
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    lang = _lang_of(text)
    findings: list[dict] = []

    # 1. header-marker
    header_ok = any(HEADER_RE.match(ln) for ln in lines[:4])
    if not header_ok:
        findings.append(_f("header-marker", BLOCK,
                           "first lines have no `<!-- vinc-graph-md: N ... -->` marker"))

    # 3. generated-block (2 보다 먼저 판정한다 — 절 검사가 이 결과를 쓰지 않지만
    #    마커가 깨졌으면 4·6 을 돌릴 수 없다).
    parts = _split(text)
    if parts is None:
        findings.append(_f("generated-block", BLOCK,
                           "generated markers are missing, duplicated, or out of order"))

    # 2. section-order
    heads = _headings(lines)
    titles = [t for _n, t in heads]
    idx = {t: i for i, t in enumerate(titles)}
    want = [ko if lang == "ko" else en for _k, (en, ko) in SECTIONS]
    required = [ko if lang == "ko" else en for k, (en, ko) in SECTIONS
                if k not in OPTIONAL_SECTIONS]
    present = [t for t in want if t in idx]
    if len(present) < 2:
        findings.append(_f("section-order", FIX,
                           "no recognisable sections (this checker knows en and ko only)"))
    else:
        for t in required:
            if t not in idx:
                findings.append(_f("section-order", BLOCK, f"missing section: {t}"))
        order = [idx[t] for t in present]
        if order != sorted(order):
            findings.append(_f("section-order", BLOCK,
                               "sections are out of order relative to the spec"))

    # 8. length — 공백 줄도 읽는 사람의 시간을 쓴다. 있는 그대로 센다. 다만 예산은 **사람이 쓰는
    #    문장**의 것이다: 코드가 만드는 스키마 절과 생성 구역은 세지 않는다(그것을 줄이려고 사람이
    #    고칠 수 있는 것이 없다).
    n = prose_line_count(text)
    if n > LINE_FAIL:
        findings.append(_f("length", BLOCK, f"{n} lines (hard limit {LINE_FAIL})"))
    elif n > LINE_WARN:
        findings.append(_f("length", FIX, f"{n} lines (budget {LINE_WARN})"))

    # 9. forbidden-glyph
    for i, ln in enumerate(lines, 1):
        for g in FORBIDDEN_GLYPHS:
            if g in ln:
                findings.append(_f("forbidden-glyph", FIX,
                                   "em-dash is not allowed in on-screen text (D38)", i))
                break

    # 7. stale-tool
    try:
        from vinc_mcp.tools import TOOLS
        known = {t["name"] for t in TOOLS}
    except Exception:
        known = set()
    if known:
        seen: set[str] = set()
        for i, ln in enumerate(lines, 1):
            for name in TOOL_RE.findall(ln):
                if name not in known and name not in seen:
                    seen.add(name)
                    findings.append(_f("stale-tool", FIX, f"unknown tool name: {name}", i))

    facts = collect(store, owner=owner, themes=themes) if store is not None else None

    # 5. unknown-edge
    rel_title = _title("relations", lang)
    allowed = set(WIRE_EDGES) | set((facts or {}).get("edge_types") or [])
    for ln in _section_body(lines, rel_title):
        m = re.match(r"^-\s+([a-z][a-z0-9_]*)\s*:", ln)
        if m and m.group(1) not in allowed:
            findings.append(_f("unknown-edge", BLOCK,
                               f"relation not in the wire contract or ratified vocabulary: {m.group(1)}"))

    if facts is not None and parts is not None:
        # 4. generated-drift
        if parts[1].strip() != generated_block(facts, lang=lang).strip():
            findings.append(_f("generated-drift", BLOCK,
                               "generated region does not match the store — rebuild it"))
        # 6. unknown-domain
        dom_title = _title("domains", lang)
        listed = _parse_domains(_section_body(parts[1].split("\n"), dom_title))
        for d in listed:
            if d not in set(facts.get("domains") or []):
                findings.append(_f("unknown-domain", FIX, f"domain not present in the store: {d}"))

    blocks = [x for x in findings if x["level"] == BLOCK]
    fixes = [x for x in findings if x["level"] == FIX]
    return {"kind": "graphmd.check", "executed": True, "lang": lang,
            "ok": not findings, "exit_code": 2 if blocks else (1 if fixes else 0),
            "blocks": len(blocks), "fixes": len(fixes), "findings": findings}


def _parse_domains(body: list[str]) -> list[str]:
    """`research (142) · product (88) · 외 3개` → ["research", "product"].

    빈 그래프 문장("No domains yet...")은 조각마다 공백이 들어가 전부 걸러진다 —
    그래서 별도 분기 없이 빈 목록이 된다."""
    for ln in body:
        s = ln.strip()
        if not s:
            continue
        out = []
        for part in s.split("·"):
            name = part.strip().split("(")[0].strip()
            if name and " " not in name:   # "외 3개" / "and 3 more" 는 이름이 아니다
                out.append(name)
        return out          # 목록 줄은 절의 첫 줄 하나뿐이다
    return []


def analyse(text: str, **kw) -> dict:
    """`check` 의 별칭 — 호출부 가독성용(검사 결과를 화면에 얹는 자리)."""
    return check(text, **kw)
