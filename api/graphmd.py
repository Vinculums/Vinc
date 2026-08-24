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

import re

from store import theme_store

SPEC_VERSION = 1

#: 스토어에 관례를 문서로 둘 때의 고정 doc_id. 동기화에서 제외된다(`docsync`) —
#: 로컬 그래프와 계정 그래프는 **각자의 관례**를 갖는다는 것이 불변식이다.
CONVENTIONS_DOC_ID = "graph.md"
#: 그 문서의 출처 표식(일반 자료와 구분하는 유일한 표시).
CONVENTIONS_ORIGIN = "vinc:conventions"

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

#: wire 계약의 엣지 이름(`api/fragment.py`). 재가 어휘와 별개로 항상 유효하다.
WIRE_EDGES = ("relates_to", "prerequisite_of", "derives_from",
              "inherits_from", "connects_via", "about")

#: 필수 절 — (key, (EN 제목, ko 제목)). 순서가 곧 검사 순서다.
SECTIONS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("what", ("What this is", "이 파일은 무엇인가")),
    ("read", ("Read before you write", "쓰기 전에 읽을 것")),
    ("nodes", ("Node kinds", "노드 종류")),
    ("size", ("How big is one concept", "개념 하나의 크기")),
    ("relations", ("Relations", "관계")),
    ("documents", ("Documents", "문서")),
    ("domains", ("Domains in this graph", "이 그래프의 도메인")),
    ("vocab", ("Ratified vocabulary", "재가된 어휘")),
    ("donot", ("Do not", "하지 말 것")),
)

LINE_WARN, LINE_FAIL = 60, 100
#: 화면 노출 금지 문자(D38). 이 템플릿은 문서 사이트에 그대로 실린다.
FORBIDDEN_GLYPHS = ("—",)   # em-dash


# ── 템플릿 ────────────────────────────────────────────────────────────────────
# 이 상수가 템플릿 정본이다. 사본이 **Vinc-homepage 에 하나** 있으므로 함께 고친다:
# `site/docs/graph-md/spec/index.html` — EN 은 코드블록에, ko 는 그 페이지 스크립트의
# `tpl:` 문자열에 통째로 박혀 있다. (경로가 `/docs/mcp/graph-md/` 로 적혀 있었는데
# 지금은 `mcp/` 가 빠진 자리다. 2026-08-25 에 그 옛 경로만 보고 "사본 없음"으로
# 잘못 판정했다 — 그래서 실제 경로를 적어 둔다.)

_TPL_EN = """# GRAPH.md
{header}

## What this is [this graph decides]
The house style for writing into this knowledge graph. Read it before your first write
in a session. Judging meaning is your job. This file only fixes the conventions.

## Read before you write [this graph decides]
Search first (vinc_search), reuse the ids you get back, and prefer attaching to an
existing concept over creating a near duplicate. Ids carry their type as a prefix:
concept:optimistic-locking, decision:drop-the-cache, record:pricing-note.
Write in English, titles and bodies alike, whatever language you are thinking in.

## Node kinds [schema-fixed]
- Concept: one reusable idea. If it will not be reused, do not create it.
- Episode: something that happened (a session, an event). It is tied to a time.
- Record: a note hanging off a concept, or a measurement about code: then add props.subject_path (repo-relative), measured_at, measured_ref.
- Decision: what you chose at a point in time, and why. Tie it with `about`.
- Bridge: a junction that ties domains together.
- Entity: anything else you author. Unknown types save here and enter the
  vocabulary as proposed. If you cannot decide, use Concept and say so.

## How big is one concept [varies by topic]
GOOD "Optimistic concurrency control": reusable, self standing, one idea.
GOOD "Sunk cost fallacy": same shape, different field.
BAD "Database stuff": several ideas in a bag. Split it.
BAD "What I read today": not a concept. That is an Episode.
BAD "PostgreSQL" and "Postgres" as two nodes: the same thing. Keep one.
Test: if you cannot finish the sentence "X is ..." in two sentences, it is too big.
People and products pass that test but are not ideas: write them as an Entity or record.

## Relations [schema-fixed]
- relates_to: loose association. Undirected (the reverse folds into the same edge).
- prerequisite_of: A comes before B. The direction carries the meaning.
- derives_from: A comes out of B. The direction carries the meaning.
- inherits_from: A inherits from B. The direction carries the meaning.
- connects_via: Bridge to Concept or Decision.
- about: Episode or Decision to Concept or Decision.
Record to either: any of the first four. Those edges and about must carry `evidence`.
Not sure: use relates_to. Never flip a directed edge to make it fit.

## Documents [schema-fixed]
Source text goes in through vinc_doc_put and the store keeps the canonical copy. Do
not paste a whole document into a Record; link concepts through the document, not a copy.

{generated}

## Do not [this graph decides]
- Do not ratify your own proposal. A person does that.
- Do not create a node for something you did not search for first.
- Do not invent a domain name inline. Propose it and say why.
"""

_TPL_KO = """# GRAPH.md
{header}

## 이 파일은 무엇인가 [이 그래프가 정함]
이 지식그래프에 쓸 때의 관례입니다. 세션에서 첫 쓰기를 하기 전에 읽으십시오.
의미를 판단하는 것은 당신의 일입니다. 이 파일은 관례만 고정합니다.

## 쓰기 전에 읽을 것 [이 그래프가 정함]
먼저 검색하고(vinc_search), 돌려받은 id 를 재사용하고, 비슷한 개념을 새로 만들기보다
기존 개념에 붙이십시오. id 는 종류를 접두사로 답니다:
concept:optimistic-locking, decision:drop-the-cache, record:pricing-note.
제목도 본문도 **영어로** 쓰십시오. 당신이 어떤 언어로 생각하든 그렇습니다.

## 노드 종류 [스키마 고정]
- Concept: 재사용되는 하나의 관념. 재사용되지 않을 것이면 만들지 마십시오.
- Episode: 일어난 일(세션·사건). 시간에 묶입니다.
- Record: 개념에 붙는 메모, 또는 코드에 대한 측정: 그때는 props.subject_path(리포 기준 상대경로)·measured_at·measured_ref 를 함께 적습니다.
- Decision: 그때 무엇을 골랐고 왜 그랬는지. `about` 으로 그 대상에 이으십시오.
- Bridge: 도메인을 가로질러 묶는 매듭.
- Entity: 그 밖의 것. 미지 타입도 여기 저장되어 어휘에 제안으로 편입됩니다.
  정하지 못하겠으면 Concept 으로 넣고 그렇게 했다고 답에 적으십시오.

## 개념 하나의 크기 [주제마다 다름]
GOOD "낙관적 동시성 제어": 재사용되고, 자립적이고, 하나의 관념입니다.
GOOD "매몰비용 오류": 같은 모양, 다른 분야.
BAD "데이터베이스 관련": 여러 관념을 한 자루에 담았습니다. 쪼개십시오.
BAD "오늘 읽은 것": 개념이 아닙니다. Episode 입니다.
BAD "PostgreSQL" 과 "Postgres" 를 각각: 같은 것입니다. 하나만 두십시오.
기준: "X 는 ...이다" 를 두 문장으로 못 끝내면 너무 큽니다.
인물·제품은 이 기준을 통과하지만 관념이 아닙니다: Entity 나 record 로 넣으십시오.

## 관계 [스키마 고정]
- relates_to: 느슨한 연관. 무방향입니다(역방향은 같은 간선으로 접힙니다).
- prerequisite_of: A 가 B 보다 먼저입니다. 방향이 의미입니다.
- derives_from: A 가 B 에서 나왔습니다. 방향이 의미입니다.
- inherits_from: A 가 B 를 상속합니다. 방향이 의미입니다.
- connects_via: Bridge 에서 Concept 이나 Decision 으로.
- about: Episode 나 Decision 에서 Concept 이나 Decision 으로.
Record 에서 둘 중 어디로든 위 넷 중 아무거나. 그 엣지들과 about 은 `evidence` 를 답니다.
확신이 없으면 relates_to. 맞추려고 방향을 뒤집지 마십시오.

## 문서 [스키마 고정]
원문은 vinc_doc_put 으로 넣고, 정본은 스토어가 보관합니다. 문서 전문을 Record 에
붙여넣지 마십시오. 개념은 문서를 통해 연결하고, 사본을 통해 연결하지 마십시오.

{generated}

## 하지 말 것 [이 그래프가 정함]
- 자기 제안을 스스로 재가하지 마십시오. 그것은 사람이 합니다.
- 검색해 보지 않은 것을 노드로 만들지 마십시오.
- 도메인 이름을 즉석에서 만들지 마십시오. 제안하고 이유를 적으십시오.
"""

_HEADER = {
    "en": "<!-- vinc-graph-md: {v} · built {day} · the store is canonical, this file is a view of it -->",
    "ko": "<!-- vinc-graph-md: {v} · built {day} · 정본은 스토어이고 이 파일은 그 사본입니다 -->",
}
_GEN_HEAD = {
    "en": "<!-- vinc:generated start · copied from the ontology below this line -->",
    "ko": "<!-- vinc:generated start · 여기부터 온톨로지에서 옮겨 적습니다 -->",
}
_GEN_TAIL = "<!-- vinc:generated end -->"
_GEN_BODY = {
    # ⚠ 줄 예산(LINE_WARN=60)이 꽉 차 있다 — 한 줄을 더하려면 어딘가에서 한 줄을 빼야
    # 하고, EN/ko 는 1:1 이어야 한다(test_templates_are_line_for_line_parallel).
    "en": ("## Domains in this graph\n{domains}\n"
           "Lowercase english. A slash makes a tree: 'vinc/design' files that node "
           "under 'vinc', with nothing extra to author. "
           "Walk it with vinc_neighbors('domain:vinc').\n\n"
           "## Ratified vocabulary\n"
           "node types: {nodes}\n"
           "edge types: {edges}\n"
           "Anything else you write is kept and marked proposed, for a person to ratify."),
    "ko": ("## 이 그래프의 도메인\n{domains}\n"
           "소문자 영어. 슬래시가 계층이 됩니다: 'vinc/design' 은 그 노드를 'vinc' "
           "아래 둡니다. 따로 저작할 것이 없습니다. "
           "vinc_neighbors('domain:vinc') 로 걸어 내려가십시오.\n\n"
           "## 재가된 어휘\n"
           "노드 타입: {nodes}\n"
           "엣지 타입: {edges}\n"
           "그 밖에 쓰는 것은 보존되고 제안으로 표시되어, 사람의 재가를 기다립니다."),
}
_EMPTY = {
    "en": {"domains": "No domains yet. Your first import creates them.",
           "nodes": "none beyond the six above", "edges": "none beyond the six above"},
    "ko": {"domains": "아직 도메인이 없습니다. 첫 적재가 도메인을 만듭니다.",
           "nodes": "위 여섯 외에 없음", "edges": "위 여섯 외에 없음"},
}
_MORE = {"en": "and {n} more", "ko": "외 {n}개"}

#: 도메인 목록의 상한. 넘치면 "외 N개" — 목록이 60줄 예산을 먹게 두지 않는다.
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
        return {"kind": "graphmd.build", "executed": True, "text": _tidy(text),
                "lang": use, "replaced": True, "facts": facts}
    use = lang or "en"
    tpl = _TPL_KO if use == "ko" else _TPL_EN
    text = tpl.format(header=_HEADER[use].format(v=SPEC_VERSION, day=day),
                      generated=generated_block(facts, lang=use))
    return {"kind": "graphmd.build", "executed": True, "text": _tidy(text),
            "lang": use, "replaced": False, "facts": facts}


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
    present = [t for t in want if t in idx]
    if len(present) < 2:
        findings.append(_f("section-order", FIX,
                           "no recognisable sections (this checker knows en and ko only)"))
    else:
        for t in want:
            if t not in idx:
                findings.append(_f("section-order", BLOCK, f"missing section: {t}"))
        order = [idx[t] for t in present]
        if order != sorted(order):
            findings.append(_f("section-order", BLOCK,
                               "sections are out of order relative to the spec"))

    # 8. length — 공백 줄도 읽는 사람의 시간을 쓴다. 있는 그대로 센다.
    n = len(text.rstrip().split("\n"))
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
    rel_title = SECTIONS[4][1][1 if lang == "ko" else 0]
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
        dom_title = SECTIONS[6][1][1 if lang == "ko" else 0]
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
