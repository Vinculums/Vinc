# -*- coding: utf-8 -*-
"""전문 색인이 담는 말 — 한글은 겹치는 두 글자 묶음, 나머지는 소문자 낱말 그대로.

색인 쪽(`Chunk.lex` 를 쓰는 `put_doc` 과 백필)과 질의 쪽(`fulltext_search`)이 **같은
함수**를 부른다. 둘이 갈라지면 그 자체가 결과를 바꾼다 — 질의만 조사를 떼었을 때 키워드
팔이 MRR@10 -0.16 으로 무너졌다. 근거: [[vinc.research.korean-retrieval-2026-10-01 §3]]

왜 두 글자 묶음인가: 색인이 `승부는` 을 통째로 담으면 질의 `승부` 는 한 건도 못 맞춘다.
조사 절단(R1c)은 `전문가`→`전문` 같은 과잉 절단을 하고, 형태소 분석은 사전과 의존성이
필요하다. 두 글자 묶음은 사전 없이 둘 다 이겼다(한국어 방송 말뭉치, 질의 네 형태 전부).

순수 함수만 둔다 — 스토어도 설정도 읽지 않는다.
"""
from __future__ import annotations

import re

#: 낱말 경계. 연구 측정에 쓴 분할과 같다(`·…→|` 와 `-_` 에서도 끊는다).
_SPLIT = re.compile(r"[\s,./?!;:()\[\]{}\"'`~·…→⇒|<>=+*&^%$#@\-_]+")
_HANGUL = re.compile(r"[가-힣]+")


def _tokens(text: str):
    for w in _SPLIT.split(text or ""):
        if not w:
            continue
        pos = 0
        for m in _HANGUL.finditer(w):
            if m.start() > pos:
                yield w[pos:m.start()].lower()
            run = m.group(0)
            if len(run) == 1:
                yield run
            else:
                for i in range(len(run) - 1):
                    yield run[i:i + 2]
            pos = m.end()
        if pos < len(w):
            yield w[pos:].lower()


def _is_hangul(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _edge(ch: str) -> bool:
    """한글 아닌 토큰의 이웃으로 괜찮은가 — `_tokens` 가 거기서 끊는 글자."""
    return _is_hangul(ch) or bool(_SPLIT.fullmatch(ch))


def find_tokens(text: str, tokens: set[str]) -> list[tuple[int, str]]:
    """`tokens` 가 `text` 의 어디에 토큰으로 나오는가 — (오프셋, 토큰), 오프셋 순.

    글 전체를 토큰화하지 않고 찾을 토큰만 `str.find` 로 찾는다(발췌가 행마다 부르므로 2,000자
    청크를 파이썬으로 토큰화하면 20행 페이지에 30ms 가 더 든다). 결과는 `lex_text` 의 토큰과
    같다: 한글 두 글자는 붙어 있는 자리가 곧 그 토큰이고(한글끼리 붙어 있으면 같은 낱말이다),
    한 글자 한글은 앞뒤가 한글이 아닐 때만, 그 밖의 토큰은 앞뒤가 낱말 경계나 한글일 때만 센다."""
    if not text or not tokens:
        return []
    low = text.lower()
    if len(low) != len(text):           # `İ` 처럼 소문자가 길어지는 글자가 있으면 오프셋이 밀린다
        low = text
    out: list[tuple[int, str]] = []
    n = len(text)
    for tok in tokens:
        if not tok:
            continue
        hangul = all(_is_hangul(c) for c in tok)
        at = low.find(tok)
        while at >= 0:
            end = at + len(tok)
            prev = text[at - 1] if at else " "
            nxt = text[end] if end < n else " "
            if hangul and len(tok) == 2:
                ok = True
            elif hangul:
                ok = not _is_hangul(prev) and not _is_hangul(nxt)
            else:
                ok = _edge(prev) and _edge(nxt)
            if ok:
                out.append((at, tok))
            at = low.find(tok, at + 1)
    out.sort()
    return out


def lex_text(text: str) -> str:
    """색인할 문자열, 그리고 질의가 될 문자열. 토큰을 공백으로 잇는다(엔진 토크나이저가
    공백에서 다시 자른다). 영어 낱말은 그대로라 엔진의 어간 처리를 계속 받는다."""
    return " ".join(_tokens(text))


def lex_tokens(text: str) -> set[str]:
    return set(_tokens(text))


def coverage(query_tokens: set[str], row_lex: str) -> float:
    """질의 토큰 가운데 그 행의 `lex` 에 있는 비율(0~1). 질의 토큰이 없으면 0."""
    if not query_tokens:
        return 0.0
    have = set((row_lex or "").split())
    return len(query_tokens & have) / len(query_tokens)
