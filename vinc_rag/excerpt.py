# -*- coding: utf-8 -*-
"""노드 발췌의 창 — 청크의 **어디를** 240자로 보여 줄 것인가, 그리고 폴백 후보의 순서.

`api.query._node_context` 와 `edge_context` 가 부른다.

- 창: 기본은 노드의 제목·한 줄 요약 토큰이 가장 여러 가지 모인 자리다(검색 스니펫과 같은 창,
  `_snippet_start`). ``VINC_EXCERPT_WINDOW=head`` 가 비상구로, 청크 맨 앞 240자를 낸다
  (2026-10-17 이전 동작). 맨 앞만 보이면 노드 문장이 청크 안쪽에 있을 때 발췌에 안 보였다.
- 순서: 제목 전문 검색 폴백은 BM25 순위로 id 를 받는다. `IN $ids` 조회는 그 순서를 지키지
  않으므로 :func:`by_rank` 로 되돌린다. 안 되돌리면 같은 노드의 발췌 순서가 호출마다 달라진다.

근거: [[vinc.research.node-excerpt-order-and-window-rsangmoo-2026-10-17]]
"""
from __future__ import annotations

import os

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
