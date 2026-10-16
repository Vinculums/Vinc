# -*- coding: utf-8 -*-
"""노드 발췌를 고르는 순수 함수 둘 — **어느 청크를**(질문과의 거리)과 **청크의 어디를**(노드 토큰 창).

`api.query._node_context` 가 플래그를 읽어 부른다. 둘 다 기본은 꺼짐이고, 꺼져 있으면 발췌는
오늘과 바이트까지 같다(호출부가 이 모듈을 부르지 않는다).

- ``VINC_EXCERPT_ORDER=rank``  — 후보 청크를 **받은 순위 그대로** 둔다. 제목 전문 검색 폴백은
  BM25 순위로 id 를 받고도 `IN $ids` 조회가 그 순서를 버렸다.
- ``VINC_EXCERPT_ORDER=query`` — 링크 방법 등급은 첫 키로 지키고, 그 안에서 **질문 벡터와 가장
  가까운 조각**의 거리로 청크를 줄 세운다. 거리를 못 잰 청크가 하나라도 있으면 통째로 순위
  순서로 돌아가고 사유를 낸다(조용히 섞인 순서를 내지 않는다).
- ``VINC_EXCERPT_WINDOW=node`` — 240자 창을 청크 맨 앞이 아니라 노드의 제목·한 줄 요약 토큰이
  가장 여러 가지 모인 자리에 둔다. 검색 스니펫과 같은 창(`_snippet_start`)이다.

근거: [[vinc.research.rag-anything-lightrag-read-against-vinc-2026-10-17]]
"""
from __future__ import annotations

import os

from store.lexical import lex_tokens
from vinc_rag.retrieval import SNIPPET_CHARS, _snippet_start

ORDER_MODES = ("rank", "query")
WINDOW_MODES = ("node",)

#: 질문 순서를 매길 후보 청크 수의 상한. 발췌 상한(최대 12)보다 넉넉해야 고를 것이 생긴다.
POOL = 24

#: 링크 방법 등급 — `_node_context` 의 MENTIONS 정렬(`ORDER BY CASE m.method`)과 같은 순서다.
_TIER = {"title": 0, "stem": 1}


def _mode(name: str, allowed: tuple[str, ...]) -> str:
    v = str(os.environ.get(name, "")).strip().lower()
    return v if v in allowed else ""


def order_mode() -> str:
    """``VINC_EXCERPT_ORDER`` — ``""``(오늘 순서) · ``"rank"`` · ``"query"``."""
    return _mode("VINC_EXCERPT_ORDER", ORDER_MODES)


def window_mode() -> str:
    """``VINC_EXCERPT_WINDOW`` — ``""``(청크 맨 앞 240자) · ``"node"``."""
    return _mode("VINC_EXCERPT_WINDOW", WINDOW_MODES)


def node_tokens(title: str, one_liner: str = "") -> set[str]:
    """노드를 찾을 토큰 — 제목과 한 줄 요약을 검색 색인과 같은 규칙으로 자른 것."""
    return lex_tokens(f"{title or ''} {one_liner or ''}")


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
                                 key=lambda x: at.get(_cid(x[1]), len(at) + x[0]))]


def _cid(row: dict) -> str:
    return str(row.get("cid") or "")


def by_distance(rows: list[dict], dist: dict[str, float]) -> tuple[list[dict], str]:
    """링크 방법 등급 → 질문과의 거리 → 원래 순번으로 정렬한다.

    반환 ``(rows, note)``. 거리가 없는 청크가 하나라도 있으면 `rows` 를 그대로 돌려주고
    ``note`` 에 사유를 적는다 — 잰 것과 못 잰 것을 섞어 줄 세우면 그 순서는 어느 규칙도 아니다."""
    if not rows:
        return rows, ""
    missing = [_cid(r) for r in rows if _cid(r) not in dist]
    if missing:
        return rows, f"missing_vectors: {len(missing)} of {len(rows)} candidate chunks"
    keyed = sorted(enumerate(rows), key=lambda x: (_TIER.get(str(x[1].get("method") or ""), 2),
                                                   float(dist[_cid(x[1])]), x[0]))
    return [r for _, r in keyed], ""
