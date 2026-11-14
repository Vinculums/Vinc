# -*- coding: utf-8 -*-
"""정본 → 개념 후보 추출 — 헤딩 휴리스틱과 그 반영(`extract_doc`).

  extract_concepts : 순수 결정론. 헤딩마다 개념 후보 하나({title, one_liner, slug}).
  extract_doc      : 문서의 후보를 제안하고(apply=False) 승인되면 Concept 로 반영한다.
  _headings        : ATX·밑줄식 헤딩 파서. 리더 목차(`api.docreader.doc_sections`)도 쓴다.

최상위에서는 표준 라이브러리와 `engines`·`store`, 잎인 `api.node_schema` 만 import 한다.
`api.docstore` 의 이름(`get_doc`·`_meta`·`_node_stamp`·`backfill_evidence` …)은 함수 안에서
호출 때 읽는다: docstore 가 이 모듈의 이름을 최상위에서 다시 내보내기 때문이다. `docstore.extract_doc` 과
이 모듈의 `extract_doc` 은 같은 함수 객체다.
"""
from __future__ import annotations

import re
from typing import Any

from engines import domain_hierarchy
from store import pkns

from .node_schema import normalize_time


# ── 정본→노드 파생 (D45-V — 옵션1 결정론 + 옵션2 확인 흐름의 공용 백엔드) ────────
# 옵션1(결정론): 헤딩 구조에서 개념 후보를 기계적으로 뽑는다 — LLM 없음·오프라인·
# 멱등. 옵션2(LLM+확인): LLM(예: Claude via MCP vinc_doc_get)이 후보를 만들되,
# **반영은 같은 apply 계약**을 지나므로 확인 UI 는 후보의 출처를 가리지 않는다.
# 자동 반영은 하지 않는다 — extract 는 후보 제안이 기본이고 apply 는 명시 opt-in
# (편집이 사용자가 다듬은 개념을 조용히 덮어쓰지 않게, status='proposed' 로 표식).

_SLUG_RE = re.compile(r"[^a-z0-9가-힣]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.strip().lower()).strip("-")[:60]


_ATX_RE = re.compile(r"^(#{2,3})\s+(.+)$")
# 밑줄식 헤딩(마크다운 setext · reStructuredText 섹션) — 한 종류의 구두점만 3자
# 이상 반복된 줄. `*`·`+` 는 뺐다: 마크다운에서 `***` 는 구분선이고 `+ ` 는 목록
# 이라 오탐이 실익보다 크다. 백틱도 뺐다: ``` 은 사실상 언제나 코드펜스다.
# `=` 는 매칭은 하되 문서 제목 층으로 보고 버린다.
_UNDERLINE_RE = re.compile(r"^([=\-~^:'\"])\1{2,}$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_LIST_RE = re.compile(r"^([-*+]\s|\d+[.)]\s)")
# 헤딩은 짧다 — 긴 문단 아래에 우연히 놓인 구분선이 그 문단을 통째로 개념으로
# 만드는 일을 막는 상한(문자 수).
_MAX_HEADING_LEN = 120


def _headings(lines: list[str], *,
              atx: "re.Pattern[str]" = _ATX_RE) -> list[tuple[int, int, str]]:
    """``(제목 줄, 본문 시작 줄, 제목)`` 목록 — ATX(``##``/``###``)와 밑줄식 양쪽.

    ``atx`` 를 갈아 끼울 수 있는 이유: 개념 추출은 H2/H3 만 후보로 삼지만(깊이 컷은
    의도된 것) **목차는 전 깊이를 보여야 한다**. 규칙만 다르고 가드(프론트매터·
    코드펜스·앞줄 성격)는 같으므로, 사본을 만들지 않고 여기 한 벌을 나눠 쓴다.

    밑줄식을 받는 이유: 같은 내용을 setext(``제목\\n---``)나 RST(``제목\\n~~~``)로
    쓴 문서가 ATX 만 보던 시절엔 후보 **0개**였다. 결정론 층의 깊이를 늘리는 가장
    싼 방법이고 LLM 도 새 의존성도 필요 없다.

    오탐을 막는 세 가드가 핵심이다:
      · **프론트매터** — 닫는 ``---`` 가 바로 위 ``tags: [...]`` 를 제목으로 둔갑시킨다.
      · **코드펜스** — 코드 예시 안의 ``## foo`` 나 ``---`` 는 헤딩이 아니다
        (ATX 에도 함께 적용된다 — 원래 새던 구멍).
      · **앞줄 성격** — 빈 줄·목록·표(``|``)·다른 밑줄·과길이는 제목이 아니다.
    """
    out: list[tuple[int, int, str]] = []
    in_fence = False
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    for i in range(start, len(lines)):
        s = lines[i].strip()
        prev = lines[i - 1].strip() if i > start else ""
        if in_fence:
            if _FENCE_RE.match(s):
                in_fence = False
            continue
        if _FENCE_RE.match(s):
            # `~~~` 는 RST 섹션 밑줄과 글자가 겹친다. 펜스는 블록이라 앞이 비어
            # 있고 밑줄은 제목 **바로 아래**에 붙는다 — 그 차이로 가른다.
            if not (s[0] == "~" and _title_shaped(prev)):
                in_fence = True
                continue
        m = atx.match(s)
        if m:
            out.append((i, i + 1, m.group(2).strip().lstrip("*").strip()))
            continue
        if not _UNDERLINE_RE.match(s) or s[0] == "=" or not prev:
            continue                      # `=` = 문서 제목 층(ATX 의 `#` 과 동일 취급)
        if not _title_shaped(prev):
            continue
        out.append((i - 1, i + 1, prev.lstrip("*").strip()))
    return out


def _title_shaped(line: str) -> bool:
    """밑줄 위에 놓였을 때 제목으로 받아줄 만한 줄인가. 빈 줄·목록·표 구분행·
    다른 밑줄·과길이 문단은 제목이 아니다."""
    return bool(line) and len(line) <= _MAX_HEADING_LEN and not line.startswith("#") \
        and "|" not in line and not _LIST_RE.match(line) \
        and not _UNDERLINE_RE.match(line) and not _FENCE_RE.match(line)


# 번호·글머리 접두 — "1. 결정들"·"3.1 두 형식"·"A) 개요"·"(2) 그 다음".
# 숫자 하나만으로는 벗기지 않는다: 끊어 주는 문장부호가 있거나(`1.`) 점으로 이어진
# 절 번호(`3.1`)일 때만이다. 안 그러면 "12 factors of X" 같은 제목에서 12 를 잃는다.
_HEAD_NUM_RE = re.compile(r"^\(?(?:\d+(?:\.\d+)+|\d+[.)]|[A-Za-z][.)])\s+")
# 인라인 마크. `[[wiki]]`·`[텍스트](주소)` 는 텍스트만 남기고, `**`·`*`·백틱은 벗긴다.
_MD_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]|\[([^\]]+)\]\([^)]*\)")
_MD_EMPH_RE = re.compile(r"\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*")
# 줄머리의 인용부호·여백. 인용문으로 시작하는 절의 one_liner 가 `> ` 를 달고 나왔다.
_MD_PREFIX_RE = re.compile(r"^[>\s]+")
# 문장의 끝 — 마침표·물음표·느낌표(전각 포함)와 그 뒤의 닫는 따옴표·괄호까지.
_SENT_END_RE = re.compile(r"[.!?。？！][\"')\]]*$")
#: one_liner 상한. 툴팁 한 문단 분량.
_ONE_LINER_MAX = 400


def _strip_md(s: str) -> str:
    """인라인 마크를 벗긴다(결정론). 밑줄 강조(``_x_``)는 **일부러 두지 않는다** —
    이 코퍼스는 `doc_id`·`snake_case` 로 가득해서 벗기면 식별자를 망가뜨린다."""
    s = _MD_LINK_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    for _ in range(2):        # 한 겹 중첩(``**`x`**``)까지 푼다
        s = _MD_EMPH_RE.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_heading(title: str) -> str:
    """헤딩 → 개념 제목. 번호 접두를 벗기고 인라인 마크를 푼다.

    ⚠ slug 가 제목에서 나오므로 **id 가 바뀐다**("1. The decisions" 는 예전에
    `concept:1-the-decisions` 였다). 바뀌는 쪽이 옳다: 절 번호는 그 문서 안에서만
    뜻이 있고 개념 id 는 문서 밖에서 재사용되는 이름이다."""
    return _strip_md(_HEAD_NUM_RE.sub("", title.strip()))


# 구조어 — 코퍼스 관례가 만드는 절 이름들(방법·출처·평결…). 문서의 뼈대라서
# 개념이 될 수 없다. 근거: [[vinc.ops.topic-assignment-eval]] §2
_STRUCT_SLUGS = frozenset({
    "method", "methods", "provenance", "verdict", "overall-verdict", "summary",
    "overview", "introduction", "background", "conclusion", "results", "discussion",
    "notes", "caveats", "scope", "status", "references", "related-concepts",
    "appendix", "open-questions", "next-steps", "todo", "changelog", "history",
})
# 절 서두어 — 이걸로 시작하는 헤딩은 이름이 아니라 문장이다("What is on the table").
_CLAUSE_OPENERS = frozenset({
    "what", "how", "why", "when", "where", "whether", "if", "is", "are", "was",
    "were", "does", "do", "did", "can", "should", "it", "this", "that", "these",
})
# 멘션 링커의 제목 토큰 상한(engines/mentions_link 의 6)과 같은 값. 그보다 긴
# 제목의 개념은 링커가 영영 못 맞춰 MENTIONS 가 생기지 않는다 — 주제 유도에
# 죽은 무게라서 애초에 후보로 내지 않는다.
_MAX_TITLE_WORDS = 6


def _concept_shaped(clean: str, slug: str) -> bool:
    """헤딩이 **개념 이름**으로 설 수 있는가. 구조어·절 문장·과길이 제목은 아니다."""
    words = clean.split()
    if not words or len(words) > _MAX_TITLE_WORDS:
        return False
    if slug in _STRUCT_SLUGS:
        return False
    return words[0].lower() not in _CLAUSE_OPENERS


def _one_liner(lines: list[str], body: int, stop: int) -> str:
    """헤딩 다음의 **첫 문장**. 첫 비지 않은 줄에서 시작해 문장이 끝날 때까지 잇는다.

    한 줄만 잘라 오던 것이 결함이었다: 대부분의 문서는 문장 중간에서 줄을 바꾸므로
    one_liner 가 반 문장으로 끝났다. 줄 **안**에서는 자르지 않는다 — 이미 문장부호로
    끝나는 줄은 그 자리가 곧 경계다.

    문단이 끝나거나(빈 줄) 이어지는 줄이 목록·표·펜스면 멈춘다. 그런 줄은 앞 문장의
    계속이 아니라서, 붙이면 문장이 아니라 잡동사니가 된다."""
    buf = ""
    for ln in lines[body:stop]:
        s = _MD_PREFIX_RE.sub("", ln).strip()
        if s.startswith("#"):
            break
        if not s:
            if buf:
                break
            continue
        if buf and (_LIST_RE.match(s) or "|" in s or _UNDERLINE_RE.match(s)
                    or _FENCE_RE.match(s)):
            break
        buf = f"{buf} {s}" if buf else s
        if len(buf) >= _ONE_LINER_MAX or _SENT_END_RE.search(buf):
            break
    return _strip_md(buf)[:_ONE_LINER_MAX]


def extract_concepts(text: str, *, max_candidates: int = 24) -> list[dict]:
    """순수 결정론 추출(옵션1) — 헤딩마다 개념 후보 1개.

    헤딩 = 마크다운 ATX(``##``/``###``) + 밑줄식(setext·RST). 후보 =
    {title, one_liner, slug}. H1(``#``·``===`` 밑줄)은 문서 제목으로 보고 제외.
    같은 slug 는 첫 등장만(멱등). LLM·네트워크·스토어 없음 — 어디서든 같은 입력 =
    같은 후보.

    제목은 :func:`_clean_heading` 으로 번호 접두·인라인 마크를 벗기고, one_liner 는
    :func:`_one_liner` 로 첫 문장까지 잇는다. **one_liner 가 빈 후보는 버린다** —
    설명 없는 개념은 상류가 판단할 재료가 없고, Ask 는 그걸 제목만으로 내민다
    (추출 계약은 [[vinc.canon.chunk-ssot]] §6.1). 구조어·절 문장·6단어 초과 헤딩도
    버린다(:func:`_concept_shaped` — 근거: [[vinc.ops.topic-assignment-eval]] §2)."""
    out: list[dict] = []
    seen: set[str] = set()
    lines = text.splitlines()
    heads = _headings(lines)
    for k, (_, body, title) in enumerate(heads):
        clean = _clean_heading(title)
        slug = _slug(clean)
        if not slug or slug in seen:
            continue
        # 구조어·절 문장·6단어 초과는 후보가 아니다(:func:`_concept_shaped`).
        if not _concept_shaped(clean, slug):
            continue
        # 다음 헤딩의 제목 줄에서 멈춘다 — 안 그러면 밑줄이나 다음 제목이
        # one_liner 로 새어 들어온다.
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        one_liner = _one_liner(lines, body, stop)
        # 버린 후보는 `seen` 에도 넣지 않는다 — 같은 제목이 뒤에서 설명을 달고 다시
        # 나오면 그때는 받는다. 입력이 같으면 결과도 같으므로 멱등은 그대로다.
        if not one_liner:
            continue
        seen.add(slug)
        out.append({"title": clean, "one_liner": one_liner, "slug": slug})
        if len(out) >= max_candidates:
            break
    return out


# 프론트매터의 `domain:` 줄 — 값에 공백이 없는 도메인 문자열만 받는다(관례).
_FM_DOMAIN_RE = re.compile(r"^domain:\s*['\"]?([^'\"\s]+)['\"]?\s*$", re.M)


def _front_domain(text: str) -> str:
    """프론트매터가 스스로 밝힌 주제(`domain:`). 없으면 ''.

    doc_put 은 프론트매터를 해석하지 않는다 — 본문은 원문 그대로가 정본이다(D45).
    여기서만 읽는 이유: 추출이 만드는 개념은 주제를 가져야 하는데, 문서가 주제를
    적어 뒀다면 그것이 가장 정확한 답이라서다."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end < 0:
        return ""
    m = _FM_DOMAIN_RE.search(text[:end])
    return m.group(1).strip() if m else ""


def _doc_topic(store, doc_id: str, *, owner: str) -> str:
    """이 문서의 주제 하나. 적힌 주제(미러 domain)가 있으면 그것, 없으면 파생.
    :func:`mention_tally` 와 **같은 규칙**의 단일 문서판이어야 한다 — 두 경로가
    갈리면 목록과 상세가 서로를 반박한다(MentionTally 클래스 주석의 이유)."""
    # docstore 가 이 모듈을 다시 내보내므로 docstore 쪽 이름은 호출 때 읽는다. 그래서
    # docstore 의 이름을 바꿔 끼우면(테스트의 monkeypatch) 여기에도 그대로 닿는다.
    from .docstore import _DOC_MIRROR_PREFIX
    try:
        arow = store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
            "AND c.domain IS NOT NULL AND c.domain <> '' RETURN c.domain AS domain",
            {"id": pkns.key(owner, _DOC_MIRROR_PREFIX + doc_id), "o": owner}) or []
        if arow:
            return (arow[0].get("domain") or "").strip()
    except Exception:
        pass
    try:
        rows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
            "WHERE ch.doc_id = $d AND c.owner_id = $o "
            "AND (ch.owner_id = $o OR ch.owner_id = '') "
            "AND c.domain IS NOT NULL AND c.domain <> '' "
            "RETURN c.domain AS domain, count(*) AS n",
            {"d": doc_id, "o": owner}) or []
    except Exception:
        return ""   # MENTIONS 미구축·구스토어 — 주제 없음으로 강등(실패 아님)
    best = sorted(((r.get("domain") or "", int(r.get("n") or 0)) for r in rows),
                  key=lambda kv: (-kv[1], kv[0]))
    return best[0][0] if best else ""


def extract_doc(store, doc_id: str, *, owner: str, apply: bool = False,
                candidates: list[dict] | None = None,
                approved_candidate_ids: list[str] | None = None) -> dict:
    """doc 의 개념 후보를 제안하고(apply=False), 승인되면 반영한다(apply=True).

    - 후보 미지정 → 결정론 추출(extract_concepts, 옵션1). 지정 → 그 목록을 반영
      (옵션2 — LLM/사용자가 다듬은 후보. [{title, one_liner?}] 만 신뢰하고 id 는
      서버가 slug 로 재파생 — 외부 id 주입 금지).
    - 반영 = Concept MERGE(concept_id = 'concept:{slug}', status='proposed',
      domain = 문서의 주제) + 이 doc 의 MENTIONS 재링크(새 개념이 즉시 뷰·검색
      그래프 암에 합류). 주제 원천은 프론트매터 `domain:` → 문서의 파생 주제 순.
      **둘 다 없으면 반영을 거부한다** — 빈 domain 으로 만들면 무주제 문서 1개가
      무주제 개념 N개가 되고 문서 주제는 그대로 비었다(실측 11건, 같은 날 삭제.
      근거: [[vinc.ops.topic-assignment-eval]] §2). **기존 개념(matched)은 title/one_liner 를
      덮어쓰지 않는다** — 사용자가 다듬은 노드 보호(읽고 스킵). 단 비어 있는
      content_time·domain 은 채운다(아래).
    - SEC-1: doc 소유 가드. 반환 {executed, doc_id, candidates, applied:{created,
      matched}, mentions}."""
    # docstore 가 이 모듈을 다시 내보내므로 docstore 쪽 이름은 호출 때 읽는다. 그래서
    # docstore 의 이름을 바꿔 끼우면(테스트의 monkeypatch) 여기에도 그대로 닿는다.
    from .docstore import (_link_domain, _meta, _node_stamp, _relink_mentions,
                           backfill_evidence, get_doc)
    base: dict[str, Any] = {"kind": "doc.extract", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    # 남의 문서는 없는 문서와 같게 답한다(SEC-1).
    if meta is None or meta.get("owner_id") != owner:
        base["error"] = "unknown doc_id"
        return base
    got = get_doc(store, doc_id, owner=owner)
    if not got.get("executed"):
        base["error"] = got.get("error", "unreadable doc")
        return base
    text = got.get("text") or ""
    # 본문 후보는 일반 제목 추출과 섞어 자동 반영하지 않는다. 선택 ID와 원문 버전을
    # 명시한 별도 계약이다. 모델 실행·원문 전송은 이 경로에서 하지 않는다.
    body_mode = approved_candidate_ids is not None or (
        isinstance(candidates, list) and any(isinstance(c, dict) and c.get("source") == "body"
                                            for c in candidates))
    if body_mode:
        return _extract_body_doc(store, doc_id, owner=owner, apply=apply,
                                 candidates=candidates, approved_ids=approved_candidate_ids)
    if candidates is None:
        cands = extract_concepts(text)
    else:
        cands = []
        for c in candidates:
            title = str(c.get("title") or "").strip()
            slug = _slug(title)
            if title and slug:
                cands.append({"title": title,
                              "one_liner": str(c.get("one_liner") or "")[:400],
                              "slug": slug})
    base.update(executed=True, candidates=cands)
    dom = _front_domain(text) or _doc_topic(store, doc_id, owner=owner)
    if not apply:
        base["domain"] = dom
        return base
    # 추출 개념은 주제를 갖고 태어난다 — 원천: 프론트매터 `domain:` → 파생 주제.
    # 둘 다 없으면 거부: 주제 없는 반영은 문서를 낫게 하지 못하고 무주제 노드만
    # 늘린다. 근거: [[vinc.ops.topic-assignment-eval]] §2
    if not dom:
        base["executed"] = False
        base["error"] = (
            "no topic to inherit — the document has no front-matter `domain:` line "
            "and no derived topic yet. Give it one first (add `domain:` to its front "
            "matter, or mention a topic-carrying concept in the body and re-put), "
            "then apply again.")
        return base
    base["domain"] = dom
    # 개념 열·엣지에 넣는 값은 정규화한다(`_set_authored_topic` 과 같은 규약).
    ndom = domain_hierarchy.normalize(dom)
    created = matched = 0
    # v7(D72) — 문서의 내용 시점을 파생 개념에 물려준다. 개념은 그 문서에서 나왔으므로
    # 문서가 가리키는 시점이 곧 그 개념이 말하는 시점이다. **비파괴**다: 이미 값이
    # 있는 개념은 건드리지 않는다(사용자 손질 보호 — 아래 matched 규칙과 같은 태도).
    ctime = normalize_time(meta.get("content_time"))
    for c in cands:
        cid = f"concept:{c['slug']}"
        # PK 네임스페이싱(design A) — 스토어 바인딩용 물리 키. 사용자 대면 id 는
        # `cid` 그대로다. `normalize=False` 인 이유는 api/fragment._key 주석과 같다
        # (정규화 표는 마이그레이션용이고 여기 owner 는 이미 실 계정이다).
        # 근거: [[vinc.plan.node-pk-namespacing]] §7
        key = pkns.compose(owner, cid, normalize=False) if owner else cid
        exists = store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.owner_id AS o, "
            "n.content_time AS ct, n.domain AS dom, n.rev AS rev", {"id": key})
        if exists:
            if exists[0].get("o") not in (owner, "", None):
                continue   # 남의 개념 — 건드리지 않는다(SEC-1)
            matched += 1
            # 내 기존 개념 — 덮어쓰기 금지. 단 **비어 있는 것만** 채운다: 내용 시점과
            # 주제. 시점은 문서에 뒤늦게 적혔을 때 개념이 축에서 영영 빠지지 않게,
            # 주제는 옛 추출이 만든 무주제 개념이 이번 반영으로 낫게(같은 문서에서
            # 나온 개념의 주제는 그 문서의 주제다). 둘 다 **변경이므로 스탬프한다**
            # — 안 찍으면 채운 값이 이 기기 밖으로 나가지 못한다.
            fill_ct = bool(ctime) and not (exists[0].get("ct") or "")
            fill_dom = not (exists[0].get("dom") or "")
            if fill_ct or fill_dom:
                st = _node_stamp(store, owner, int(exists[0].get("rev") or 0))
                sets = ["n.updated_at = $ua", "n.rev = $rev", "n.sync_seq = $seq"]
                prm = {"id": key, "ua": st["updated_at"],
                       "rev": st["rev"], "seq": st["sync_seq"]}
                if fill_ct:
                    sets.append("n.content_time = $ct"); prm["ct"] = ctime
                if fill_dom:
                    sets.append("n.domain = $dom"); prm["dom"] = ndom
                store.run("MATCH (n:Concept) WHERE n.concept_id = $id SET "
                          + ", ".join(sets), prm)
                if fill_dom:
                    _link_domain(store, key, ndom)
            continue
        # 추적열(updated_at·rev·sync_seq)을 함께 찍는다 — 없으면 이 개념은 delta 에
        # 안 실려 다른 기기로 못 간다(`docstore._node_stamp` 주석 참조).
        st = _node_stamp(store, owner)
        store.run(
            "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, n.one_liner = $ol, "
            "n.owner_id = $owner, n.status = 'proposed', n.domain = $dom, "
            "n.stem = $slug, n.source_file = $origin, n.created = $now, "
            "n.content_time = $ct, n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
            {"id": key, "t": c["title"], "ol": c["one_liner"], "owner": owner,
             "dom": ndom, "slug": c["slug"], "origin": meta.get("origin") or "",
             # 시간 원천을 비워 두면 타임라인이 이 개념을 "만진 때"로만 알게 된다.
             # 벽시계가 아니라 **스탬프의 HLC** 를 쓴다: 출생 시각은 기기 간 비교
             # (`x.created > $born`)로 화해되는데 그 비교가 사전식이라, 초 단위
             # `now_iso` 와 밀리초 HLC 가 섞이면 같은 순간이 다르게 정렬된다.
             "now": st["updated_at"], "ct": ctime,
             "ua": st["updated_at"], "rev": st["rev"], "seq": st["sync_seq"]})
        _link_domain(store, key, ndom)
        created += 1
    linked = _relink_mentions(store, doc_id, owner=owner,
                              origin=meta.get("origin") or "")
    ev = backfill_evidence(store, owner=owner, doc_id=doc_id)
    base.update(applied={"created": created, "matched": matched}, mentions=linked,
                evidence=ev)
    return base


def _extract_body_doc(store, doc_id: str, *, owner: str, apply: bool,
                      candidates, approved_ids) -> dict:
    from engines.body_candidates import prepare_candidates
    from engines.mentions_link import merge_body_links
    from store.embedded_backend import DDL_GUARD, EmbeddedBackend
    from store.embedded_schema import body_mentions_ready
    # docstore 가 이 모듈을 다시 내보내므로 docstore 쪽 이름은 호출 때 읽는다. 그래서
    # docstore 의 이름을 바꿔 끼우면(테스트의 monkeypatch) 여기에도 그대로 닿는다.
    from .docstore import _node_stamp, get_doc

    base = {"kind": "doc.extract", "executed": False, "doc_id": doc_id,
            "source": "body", "model_quality": "not_run"}
    if apply and not isinstance(store, EmbeddedBackend):
        return {**base, "error": "body apply requires the embedded writer"}
    # 1개 writer connection의 수동 트랜잭션 안에서 원문을 재검증하고 노드·엣지를 함께 쓴다.
    with DDL_GUARD.exclusive():
        if apply and not body_mentions_ready(store):
            return {**base, "error": "body mentions schema is not enabled"}
        transaction = False
        try:
            from .operation_receipts import in_transaction
            if apply and not in_transaction.get():
                store.run("BEGIN TRANSACTION")
                transaction = True
            got = get_doc(store, doc_id, owner=owner)
            if not got.get("executed") or not got.get("verified"):
                raise ValueError("body candidates require a verified canonical document")
            prepared = prepare_candidates(got["text"], doc_id, candidates)
            for c in prepared:
                c["slug"] = _slug(c["title"])
                if not c["slug"]:
                    raise ValueError("body candidate title has no concept slug")
            dom = _front_domain(got["text"]) or _doc_topic(store, doc_id, owner=owner)
            base.update(candidates=prepared, domain=dom, source_hash=got["content_hash"])
            if not apply:
                base["executed"] = True
                return base
            if (not isinstance(approved_ids, list) or not approved_ids
                    or any(not isinstance(i, str) for i in approved_ids)
                    or len(set(approved_ids)) != len(approved_ids)):
                raise ValueError("body apply requires explicit selected candidate ids")
            known = {c["candidate_id"]: c for c in prepared}
            if any(i not in known for i in approved_ids):
                raise ValueError("approved candidate id is not in the source-verified candidates")
            chosen = [dict(known[i]) for i in approved_ids]
            if not dom:
                raise ValueError("no topic to inherit: give the document a topic first")
            ndom = domain_hierarchy.normalize(dom)
            created = matched = 0
            for c in chosen:
                cid = f"concept:{c['slug']}"
                key = pkns.key(owner, cid)
                existing = store.run("MATCH (n:Concept) WHERE n.concept_id=$id "
                                     "RETURN n.owner_id AS owner", {"id": key}) or []
                if existing:
                    if existing[0].get("owner") != owner:
                        raise ValueError("body candidate concept is unavailable")
                    matched += 1  # 사람이 고친 기존 노드의 열·속성은 덮지 않는다.
                else:
                    stamp = _node_stamp(store, owner)
                    store.run(
                        "CREATE (n:Concept {concept_id:$id, title:$title, one_liner:$ol, "
                        "owner_id:$owner, status:'proposed', domain:$domain, stem:$slug, "
                        "source_file:$origin, created:$created, updated_at:$updated, "
                        "rev:$rev, sync_seq:$seq, content_time:$ct})",
                        {"id": key, "title": c["title"], "ol": c["one_liner"], "owner": owner,
                         "domain": ndom, "slug": c["slug"], "origin": got["origin"],
                         "created": stamp["updated_at"], "updated": stamp["updated_at"],
                         "rev": stamp["rev"], "seq": stamp["sync_seq"],
                         "ct": normalize_time(got.get("content_time"))})
                    # 이 승인 경로는 주제 엣지 실패도 전체 rollback한다.
                    domain_hierarchy.link_node(store, "Concept", key, ndom)
                    created += 1
                c["concept_id"] = cid
                c["approved"] = True
            chunks = store.run("MATCH (ch:Chunk) WHERE ch.doc_id=$doc AND ch.owner_id=$owner "
                               "RETURN ch.chunk_id AS chunk_id, ch.char_start AS char_start, "
                               "ch.char_end AS char_end", {"doc": doc_id, "owner": owner}) or []
            linked = merge_body_links(store, owner=owner, doc_id=doc_id, chunks=chunks,
                                      candidates=chosen)
            if not linked:
                raise ValueError("body candidates have no owner-scoped source chunks")
            if transaction:
                store.run("COMMIT")
            transaction = False
            base.update(executed=True, applied={"created": created, "matched": matched},
                        approved_candidate_ids=approved_ids, mentions=linked)
            return base
        except Exception as exc:
            if transaction:
                try:
                    store.run("ROLLBACK")
                except Exception:
                    pass
            # 엔진 메시지에는 원문·운영 경로가 실릴 수 있으므로 계약 오류만 돌려준다.
            base["error"] = str(exc) if isinstance(exc, ValueError) else "body candidate apply failed"
            return base
