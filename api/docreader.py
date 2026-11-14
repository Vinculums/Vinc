# -*- coding: utf-8 -*-
"""문서 리더 도우미 — 저장된 정본 위에서 밑줄 범위·목차·걸린 개념을 계산한다(읽기 전용).

  doc_highlights : 문서 안에서 개념 하나가 언급된 범위(토큰 매칭, 편집기 단위 오프셋).
  doc_context    : 문서가 걸린 개념·헤딩 목차·청크 시작 오프셋을 한 응답으로.
  doc_sections   : 헤딩 목차(순수 함수). 파서는 `api.docextract._headings` 를 같이 쓴다.

쓰기는 없다. 문서 읽기와 소유 가드는 `api.docstore.get_doc` 이 지고, 그 이름은 호출 때
`api.docstore` 에서 읽는다: docstore 가 이 모듈의 이름을 최상위에서 다시 내보내므로 거꾸로
최상위에서 읽으면 순환 임포트가 된다. `docstore.doc_context` 와 이 모듈의 `doc_context` 는
같은 함수 객체다.
"""
from __future__ import annotations

import re
from typing import Any

from engines.mentions_link import LINK_TARGETS, MAX_TITLE_TOKENS, tokens
from store import pkns
from store.chunking import _HEADING_RE

from .docextract import _headings


# 토큰 = 소문자 영숫자/한글 낱말(mentions_link 와 같은 어휘) — 하이라이트 범위
# 계산의 단위. 언급 위치를 **토큰 span** 으로 잡아 char_start/char_end 를 낸다.
_HL_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def highlight_ranges(text: str, terms: set[str], *, max_ranges: int = 60) -> list[dict]:
    """정본 텍스트에서 ``terms``(소문자 토큰 집합)에 일치하는 토큰의 char 범위 목록.

    순수·결정론 — MENTIONS 는 method 만 저장하고 오프셋이 없으므로(F: 보고서 정정
    1), 언급 위치는 **읽기 시점 토큰 매칭으로 계산**한다. 토큰 경계로 잘라
    text[start:end] 가 정확히 그 낱말이라, Reader 전문/발췌에서 밑줄 범위로 쓴다.
    인접·중복 범위는 병합하지 않는다(각 낱말이 독립 밑줄 — 문장 전체를 칠하지
    않는다는 D45-V2 규율)."""
    if not terms:
        return []
    out: list[dict] = []
    for m in _HL_TOKEN_RE.finditer(text or ""):
        if m.group(0).lower() in terms:
            out.append({"char_start": m.start(), "char_end": m.end()})
            if len(out) >= max_ranges:
                break
    return out


def _editor_offsets(text: str, offsets) -> dict[int, int]:
    """코드포인트 오프셋 → 편집기(CodeMirror) 오프셋 표. 편집기는 UTF-16 단위로 세고
    CRLF 를 한 칸으로 줄인다. 그래서 이모지 뒤나 CRLF 문서의 둘째 줄부터 밑줄·목차가
    밀렸다. 저장 오프셋(`char_start`)과 연속성 검사는 코드포인트 그대로다. 응답에서만 바꾼다.

    글자마다 도는 파이썬 루프를 두지 않는다: 요청한 오프셋 사이 구간을 C 단의 encode·count
    로 잰다(CRLF 문서는 Windows 가져오기에서 수 MB 로 온다). 범위 밖 오프셋은 끝으로 자른다."""
    n = len(text)
    want = sorted({int(i) for i in offsets})
    if "\r\n" not in text and len(text.encode("utf-16-le", "surrogatepass")) == 2 * n:
        return {i: max(0, min(i, n)) for i in want}         # 한국어·영어 문서 대부분
    out: dict[int, int] = {}
    prev = acc = 0
    for i in want:
        j = max(0, min(i, n))
        # [prev, j) 의 UTF-16 길이에서, CR 이 이 구간에 있는 CRLF 수만큼 뺀다(LF 가 j 에 있어도).
        acc += (len(text[prev:j].encode("utf-16-le", "surrogatepass")) // 2
                - text.count("\r\n", prev, j + 1))
        out[i], prev = acc, j
    return out


def _concept_terms(title: str, stem: str) -> set[str]:
    """개념의 하이라이트 대상 토큰 — 제목 유의 토큰(앞에서부터 ≤6) + stem.

    제목 토큰은 링커(`engines.mentions_link`)의 것을 그대로 쓴다. 여기서 사본을 두고
    집합의 해시 순서로 여섯을 골랐을 때, 밑줄 낱말이 실행마다 바뀌고 링커가 언급으로
    치는 낱말과도 어긋났다."""
    terms = set(tokens(title)[:MAX_TITLE_TOKENS])
    s = (stem or "").lower().strip()
    if len(s) >= 2:
        terms.add(s)
    return terms


def doc_highlights(store, doc_id: str, concept_id: str, *,
                   owner: str | None = None) -> dict:
    """doc 전문에서 ``concept_id`` 가 언급된 char 범위(Reader 밑줄용, D45-V2 2단계).

    범위는 저장값이 아니라 토큰 매칭 계산(보고서 정정 1). SEC-1: doc·concept 소유
    가드. 반환 {executed, doc_id, concept_id, ranges:[{char_start,char_end}],
    method:'tokens', terms}. ranges 는 **편집기 단위**(UTF-16, CRLF 한 칸)다 —
    파이썬 문자열 슬라이스로 쓰면 이모지·CRLF 뒤에서 어긋난다(`_editor_offsets`)."""
    # docstore 가 이 모듈을 다시 내보내므로 docstore 쪽 이름은 호출 때 읽는다. 그래서
    # docstore 의 이름을 바꿔 끼우면(테스트의 monkeypatch) 여기에도 그대로 닿는다.
    from .docstore import get_doc
    base: dict[str, Any] = {"kind": "doc.highlights", "executed": False,
                            "doc_id": doc_id, "concept_id": concept_id, "ranges": []}
    got = get_doc(store, doc_id, owner=owner)
    if not got.get("executed"):
        base["error"] = got.get("error", "unreadable doc")
        return base
    where_owner = " AND c.owner_id = $owner" if owner else ""
    rows = store.run(
        f"MATCH (c:Concept) WHERE c.concept_id = $id{where_owner} "
        "RETURN c.title AS title, c.stem AS stem",
        # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        {"id": pkns.key(owner, concept_id),
         **({"owner": owner} if owner else {})})
    if not rows:
        base["error"] = f"unknown concept: {concept_id}"
        return base
    terms = _concept_terms(rows[0].get("title") or "", rows[0].get("stem") or "")
    text = got.get("text") or ""
    hl = highlight_ranges(text, terms)
    at = _editor_offsets(text, [v for r in hl for v in r.values()])
    base.update(executed=True, method="tokens", terms=sorted(terms),
                ranges=[{k: at[v] for k, v in r.items()} for r in hl])
    return base


def _heading_level(line: str) -> int:
    """제목 깊이. ATX 는 ``#`` 수. 밑줄식은 2 로 둔다 — `_headings` 가 ``=``(문서
    제목 층)을 이미 걸러 내므로 여기 오는 밑줄은 ``-``·``~`` 계열뿐이다."""
    m = _HEADING_RE.match(line.strip())
    return min(len(m.group(1)), 6) if m else 2


def doc_sections(text: str) -> list[dict]:
    """헤딩 목차 — ``[{title, level, char_start}]``. 리더가 본문 **위에** 내놓는
    문서 자신의 지도. 긴 문서가 여태 지도 없는 스크롤이었다.

    파서는 `_headings` 를 그대로 쓴다(펜스·프론트매터·앞줄 성격 가드가 거기 있고,
    같은 규칙이 개념 추출도 먹인다 — 사본을 만들면 목차와 후보가 갈린다). 깊이만
    전부 열어 준다.

    `_headings` 는 **줄 번호**를 주므로 char 오프셋은 여기서 되센다: 줄 하나당 개행
    한 칸이라 ``split("\\n")`` 조각 길이의 누적합이 곧 그 줄의 시작이다(CRLF 는
    ``\\r`` 이 조각에 남아 길이에 그대로 들어가므로 어긋나지 않는다)."""
    lines = text.split("\n")
    starts: list[int] = []
    at = 0
    for ln in lines:
        starts.append(at)
        at += len(ln) + 1
    return [{"title": title, "level": _heading_level(lines[idx]),
             "char_start": starts[idx]}
            for idx, _body, title in _headings(lines, atx=_HEADING_RE) if title]


def doc_context(store, doc_id: str, *, owner: str | None = None) -> dict:
    """문서 하나가 **무엇에 걸려 있고 어떤 모양인가** — 리더가 본문 앞에 내놓는 것.

    셋을 한 응답으로 준다. 나누지 않는 이유는 셋 다 같은 doc_id 한 벌의 조회이고,
    리더가 세 번 왕복하면 머리가 세 번 나뉘어 그려지기 때문이다.

      ``concepts``  **문서→노드**. 여태 없던 방향이다(노드→문서만 있었다). 문서를
                    열어 놓고 "이건 무엇에 걸려 있나"를 물을 수 없던 것이 그 결손이다.
      ``sections``  헤딩 목차(위 `doc_sections`).
      ``chunks``    청크 시작 오프셋. 검색의 청크 히트가 **그 대목으로** 착지하는 근거.

    ``sections``·``chunks`` 의 오프셋은 편집기 단위(UTF-16, CRLF 한 칸)로 나간다. 저장된
    ``Chunk.char_start`` 는 코드포인트 그대로다(`_editor_offsets`).

    `mention_tally` 를 쓰지 않는 이유: 그것은 **전 코퍼스** 집계라, 문서 하나를
    보려고 owner 의 MENTIONS 를 전부 훑는다. 여기서는 doc 스코프로 좁힌다.

    SEC-1: 문서 소유 가드는 `get_doc` 이, 개념 소유 가드는 아래 술어가 진다. 청크
    소유는 과도기 술어(``owner OR ''`` — v4 이전 파일 파생 레거시)로 `node_context`
    와 같은 모양이다."""
    # docstore 가 이 모듈을 다시 내보내므로 docstore 쪽 이름은 호출 때 읽는다. 그래서
    # docstore 의 이름을 바꿔 끼우면(테스트의 monkeypatch) 여기에도 그대로 닿는다.
    from .docstore import get_doc
    base: dict[str, Any] = {"kind": "doc.context", "executed": False, "doc_id": doc_id,
                            "concepts": [], "sections": [], "chunks": []}
    got = get_doc(store, doc_id, owner=owner)
    if not got.get("executed"):
        base["error"] = got.get("error", "unreadable doc")
        return base
    text = got.get("text") or ""
    secs = doc_sections(text)                   # 목차·청크 착지도 편집기 단위로 낸다
    at = _editor_offsets(text, [s["char_start"] for s in secs])
    base["sections"] = [{**s, "char_start": at[s["char_start"]]} for s in secs]
    # 청크 오프셋. char_start 가 음수인 행은 오프셋을 잃은 레거시라 착지 대상이
    # 아니다(거기로 보내면 문서 맨 위가 아니라 엉뚱한 곳이 열린다) — 빼고 준다.
    try:
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d "
            "RETURN c.chunk_index AS i, c.char_start AS s ORDER BY c.chunk_index",
            {"d": doc_id}) or []
        live = [(int(r.get("i") or 0), int(r.get("s") or 0)) for r in rows
                if int(r.get("s") or 0) >= 0]
        at = _editor_offsets(text, [s for _, s in live])
        base["chunks"] = [{"index": i, "char_start": at[s]} for i, s in live]
    except Exception:
        pass          # 구스토어 — 목차와 개념은 그대로 유효하다(강등, 실패 아님)
    tally: dict[str, dict] = {}
    for label, pk, _ in LINK_TARGETS:
        where = "WHERE ch.doc_id = $d"
        params: dict[str, Any] = {"d": doc_id}
        if owner:
            where += " AND c.owner_id = $o AND (ch.owner_id = $o OR ch.owner_id = '')"
            params["o"] = owner
        try:
            rows = store.run(
                f"MATCH (ch:Chunk)-[:MENTIONS]->(c:{label}) {where} "
                f"RETURN c.{pk} AS id, c.title AS title, c.domain AS domain, count(*) AS n",
                params) or []
        except Exception:
            rows = []   # 그 라벨이 없는 구스토어 — 나머지 라벨은 그대로 센다
        for r in rows:
            cid = r.get("id") or ""
            if not cid:
                continue
            e = tally.setdefault(cid, {"id": cid, "title": r.get("title") or "",
                                       "domain": (r.get("domain") or "").strip(), "count": 0})
            e["count"] += int(r.get("n") or 0)
    # 많이 걸린 것부터. 동수는 제목순 — 같은 문서를 두 번 열면 같은 순서여야 한다.
    base["concepts"] = sorted(tally.values(), key=lambda c: (-c["count"], c["title"], c["id"]))
    base["executed"] = True
    return base
