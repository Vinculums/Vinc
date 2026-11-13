# -*- coding: utf-8 -*-
"""노드 발췌의 창 — 청크의 **어디를** 240자로 보여 줄 것인가, 그리고 폴백 후보의 순서.

`api.node_read._node_context` 와 `api.nav.edge_context` 가 부른다.

- 창: 기본은 노드의 제목·한 줄 요약 토큰이 가장 여러 가지 모인 자리다(검색 스니펫과 같은 창,
  `_snippet_start`). ``VINC_EXCERPT_WINDOW=head`` 가 비상구로, 청크 맨 앞 240자를 낸다
  (2026-10-17 이전 동작). 맨 앞만 보이면 노드 문장이 청크 안쪽에 있을 때 발췌에 안 보였다.
- 순서: 제목 전문 검색 폴백은 BM25 순위로 id 를 받는다. `IN $ids` 조회는 그 순서를 지키지
  않으므로 :func:`by_rank` 로 되돌린다. 안 되돌리면 같은 노드의 발췌 순서가 호출마다 달라진다.

근거: [[vinc.research.node-excerpt-order-and-window-rsangmoo-2026-10-17]]
"""
from __future__ import annotations

import os
import re

from store.lexical import lex_tokens
from vinc_rag.retrieval import SNIPPET_CHARS, _snippet_start


def window_mode() -> str:
    """``"node"``(기본) 또는 ``"head"``(``VINC_EXCERPT_WINDOW=head``). 그 밖의 값은 기본이다."""
    v = str(os.environ.get("VINC_EXCERPT_WINDOW", "")).strip().lower()
    return "head" if v == "head" else "node"


def node_tokens(title: str, one_liner: str = "") -> set[str]:
    """노드를 찾을 토큰 — 제목과 한 줄 요약을 검색 색인과 같은 규칙으로 자른 것."""
    return lex_tokens(f"{title or ''} {one_liner or ''}")


def excerpt(text: str, title: str, one_liner: str = "") -> str:
    """청크 하나의 발췌. 창 모드에 따라 노드 토큰 자리 또는 맨 앞 240자다."""
    if window_mode() == "head":
        return " ".join(str(text or "").split())[:SNIPPET_CHARS]
    return focus(text, node_tokens(title, one_liner))


def focus(text: str, tokens: set[str] | None) -> str:
    """청크에서 `tokens` 가 가장 여러 가지 든 240자. 맞는 토큰이 없으면 맨 앞 240자다.
    맨 앞이 아니면 ``…`` 로 시작한다(검색 스니펫과 같은 표기)."""
    flat = " ".join(str(text or "").split())
    start = _snippet_start(flat, tokens)
    return ("…" if start else "") + flat[start:start + SNIPPET_CHARS]


def by_rank(rows: list[dict], ranked_ids: list[str]) -> list[dict]:
    """`rows` 를 `ranked_ids` 순서로. 목록에 없는 행은 원래 순서대로 뒤에 둔다."""
    at = {cid: i for i, cid in enumerate(ranked_ids)}
    return [r for _, r in sorted(enumerate(rows),
                                 key=lambda x: at.get(str(x[1].get("cid") or ""), len(at) + x[0]))]


#: 절 하나의 상한(자). 질문이 가리키는 청크는 늘 안에 남긴다.
SECTION_CHARS = 6000


def section_span(chunks: list[dict], at: int, cap: int = SECTION_CHARS) -> tuple[int, int, str, bool]:
    """청크 ``at`` 이 속한 절. ``chunks`` 는 한 문서의 청크를 순서대로 {sec, text} 로 준 것이다.

    절은 ``at`` 에서 앞뒤로 같은 절 제목(``sec``)이 이어지는 청크까지다. 제목이 비면 그 청크
    하나다. 반환은 (첫 청크, 끝 청크, 본문, 잘렸는가). 본문이 ``cap`` 을 넘으면 ``at`` 청크가
    앞 3분의 1쯤에 오도록 ``cap`` 자를 자른다."""
    sec = str(chunks[at].get("sec") or "")
    lo = hi = at
    while sec and lo > 0 and str(chunks[lo - 1].get("sec") or "") == sec:
        lo -= 1
    while sec and hi + 1 < len(chunks) and str(chunks[hi + 1].get("sec") or "") == sec:
        hi += 1
    text = "".join(str(c.get("text") or "") for c in chunks[lo:hi + 1])
    if len(text) <= cap:
        return lo, hi, text, False
    before = len("".join(str(c.get("text") or "") for c in chunks[lo:at]))
    start = max(0, min(before - cap // 3, len(text) - cap))
    return lo, hi, text[start:start + cap], True


# ── 인용 단위 ────────────────────────────────────────────────────────────────
# 답을 쓰는 쪽이 그대로 따올 수 있는 글자 그대로의 한두 문장. 발췌(240자 창)와 다른 계약이다:
# 창은 문장 경계와 상관없이 잘리고 앞 문장이 빠져, 가리키는 말("this", "the three")이 무엇을
# 가리키는지 인용문 안에서 안 보인다. 단위는 가리키는 말로 열리면 앞 문장을 함께 싣는다.
# 근거: [[vinc.manual.rag]]

#: 단위 하나의 상한(자). 넘으면 낱말 경계에서 잘라 앞부분만 싣는다(그래도 글자 그대로다).
QUOTE_CHARS = 520
#: 앞 문장을 붙이는 횟수의 상한 — 가리키는 말이 줄줄이 이어져도 단위가 문단이 되지 않게 한다.
_WIDEN_MAX = 2

_SENT_BREAK = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9가-힣])|(?<=[다요죠함음]\.)\s+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# 첫 낱말들이 앞 문장을 가리키는 문장. 영어는 지시어·정관사 + 수/비교어, 한국어는 이·그 계열.
_POINTS_BACK = re.compile(
    r"^[\W_]*(?:this|these|that|those|it|its|they|their|them|such|there|here|both|"
    r"the\s+(?:former|latter|two|three|four|five|same|above|following|other|first|second|third|last)|"
    r"all\s+(?:two|three|four|five)|(?:but|and|so|thus|hence|then|however|also|which|whereas|while|because)\b|"
    r"이|그|이러한|그러나|또한|따라서|이때|이는|이것|그것)",
    re.I)


def _sentences(text: str) -> list[list[str]]:
    """본문을 블록(줄 단위, 제목 줄은 뺀다)마다 문장으로 나눈다. 공백은 한 칸으로 접는다."""
    blocks: list[list[str]] = []
    for line in str(text or "").splitlines():
        if not line.strip() or _HEADING.match(line):
            continue
        flat = " ".join(line.split())
        parts = [p.strip() for p in _SENT_BREAK.split(flat) if p and p.strip()]
        if parts:
            blocks.append(parts)
    return blocks


def _points_back(sentence: str, is_first_in_block: bool) -> bool:
    """이 문장이 앞 문장 없이는 무엇을 말하는지 안 보이는가."""
    s = sentence.strip()
    if _POINTS_BACK.match(s):
        return True
    if s[:1].islower():
        return True
    return is_first_in_block and bool(_BULLET.match(sentence)) and len(s) < 60


def _cut(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = text[:cap]
    sp = head.rfind(" ")
    return (head[:sp] if sp > cap // 2 else head).rstrip()


def quote_units(text: str, query: str, *, n: int = 2, cap: int = QUOTE_CHARS) -> list[dict]:
    """`text`(한 청크 또는 한 절)에서 질문 토큰이 가장 여러 가지 든 문장 `n` 개를 단위로 낸다.

    단위 = {"text", "widened"}. text 는 본문의 이어진 글자 그대로다(공백만 접는다).
    가리키는 말로 열리거나 토막인 문장은 앞 문장을 함께 실어(최대 두 번) `widened` 를 세운다.
    질문 토큰이 하나도 안 걸리면 맨 앞 문장 하나를 낸다. 겹치는 단위는 점수가 낮은 쪽을 버린다."""
    from store.lexical import find_tokens, lex_tokens
    blocks = _sentences(text)
    if not blocks:
        return []
    toks = lex_tokens(query)
    scored = []
    for bi, parts in enumerate(blocks):
        for si, sent in enumerate(parts):
            hit = len({t for _, t in find_tokens(sent, toks)}) if toks else 0
            scored.append((hit, bi, si))
    top = [s for s in sorted(scored, key=lambda s: (-s[0], s[1], s[2])) if s[0] > 0][:max(1, n) * 2]
    if not top:
        top = [(0, 0, 0)]
    units: list[dict] = []
    used: set[tuple[int, int]] = set()
    for _, bi, si in top:
        if len(units) >= n:
            break
        lo = si
        widened = 0
        while lo > 0 and widened < _WIDEN_MAX and _points_back(blocks[bi][lo], lo == 0):
            lo -= 1
            widened += 1
        span = {(bi, k) for k in range(lo, si + 1)}
        if span & used:
            continue
        used |= span
        units.append({"text": _cut(" ".join(blocks[bi][lo:si + 1]), cap), "widened": widened > 0})
    return units
