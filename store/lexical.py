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
