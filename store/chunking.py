# -*- coding: utf-8 -*-
"""무손실 경계 청킹. 순수 함수만 둔다(I/O·DB 없음).

`api.docstore.put_doc` 이 정본 청크를 자르고, 임베딩 워커와 리랭커(`vinc_rag`)가 같은 함수로
벡터 조각을 자른다. 둘이 같은 경계를 써야 "벡터가 본 것과 같은 조각"이 성립하므로 한 벌만
둔다. `api/docstore.py` 에 있던 것을 `vinc_rag` 가 import 할 수 있게 여기로 내렸다(VIN-244:
`vinc_rag` 는 `api` 를 import 하지 않는다).
"""
from __future__ import annotations

import re
from typing import Callable

# doc 단위 기본 청크 크기(문자). 검색 recall 용 세밀 분할은 임베딩 파이프라인
# (토큰 기준)이 담당하고, 여기는 **정본 보관 단위**라 다소 커도 무방하다.
DEFAULT_MAX_CHARS = 2000

#: `#` 뒤 공백은 줄 안에서만 센다. `\s+` 였을 때 빈 제목(`#` 한 줄)이 다음 줄을 제목으로 삼았다.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*)$", re.MULTILINE)


# ── 무손실 경계 청킹(순수 — I/O·DB 없음) ─────────────────────────────────────

# ── 토큰 추정(토크나이저 없는 환경용) ────────────────────────────────────────
# 데스크탑 사이드카는 토크나이저 없이도 청킹을 하므로 문자 계열 가중치로 근사한다.
# 값은 실제 코퍼스 최소자승(한글 1.034 · CJK 1.440 · 기타 0.307)에 **1.3 배 여유**.
# ⚠ 합성 문장으로 다시 잡지 말 것, 여유 배수를 빼지도 말 것 — 둘 다 청크가 상한을
# 넘겨 임베딩 때 조용히 잘리는 쪽으로 간다. 수치와 유래:
# [[record:token-estimate-weights-came-from-a-real-corpus]].
_RE_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣᄀ-ᇿ]")
_RE_CJK = re.compile(r"[぀-ヿ一-鿿㐀-䶿]")
_EST_MARGIN = 1.3
_TOK_PER_HANGUL = 1.034 * _EST_MARGIN
_TOK_PER_CJK = 1.440 * _EST_MARGIN
_TOK_PER_OTHER = 0.307 * _EST_MARGIN


def estimate_tokens(s: str) -> int:
    """문자 계열 가중 토큰 추정 — 토크나이저가 없을 때의 폴백.

    **정확하지 않다.** 혼합 언어 문서에서 특히 그렇고, 그래서 목표 토큰 수에 여유를
    두는 것이 전제다(상한 512 에 목표 400 이면 헤드룸 108). 정확한 값이 필요하면
    `chunk_doc(count=...)` 로 진짜 토크나이저를 주입한다."""
    if not s:
        return 0
    ko = len(_RE_HANGUL.findall(s))
    cjk = len(_RE_CJK.findall(s))
    return int(ko * _TOK_PER_HANGUL + cjk * _TOK_PER_CJK
               + max(0, len(s) - ko - cjk) * _TOK_PER_OTHER) + 1


def plan_boundaries(text: str, *, max_chars: int = DEFAULT_MAX_CHARS,
                    max_tokens: int = 0,
                    count: Callable[[str], int] | None = None) -> list[int]:
    """청크 시작 오프셋 목록(b0=0, 오름차순, len 미포함). **경계 슬라이스 전제**라
    어떤 입력에도 ``"".join(text[b_i:b_{i+1}]) == text`` 가 정의상 성립한다.

    분할 선호: 문단 경계("\\n\\n" 런 직후) > 행 경계 > 하드 컷.
    문단·행 경계 산출이 어그러져도 하드 컷이 상한을 보장한다(손실은 구조적 불가 —
    경계가 어긋나면 '못생긴 분할'이지 '깨진 원문'이 아니다).

    ``max_tokens`` 를 주면 **문자가 아니라 토큰**을 기준으로 자른다. 문자 기준은
    언어에 따라 토큰 수가 5배 이상 벌어져(실측: 같은 400토큰 창이 한국어 482자 ·
    영어 2,660자) 어떤 단일 문자값도 두 언어를 동시에 만족시키지 못한다. 창 크기는
    실측된 자/토큰 비로 적응시키고, 문서 안에서도 구간마다 갱신한다(혼합 언어 대응).

    ``count`` 미지정 시 :func:`estimate_tokens` 폴백(데스크탑처럼 토크나이저가 없는 곳).
    """
    n = len(text)
    if n == 0:
        return [0]
    max_chars = max(1, int(max_chars))     # 0 이하면 아래 루프가 끝나지 않는다
    # 후보 경계: 문단(빈 줄 런 끝) 우선, 그다음 개행 직후.
    para = {m.end() for m in re.finditer(r"\n\s*\n", text)}
    lines = {m.end() for m in re.finditer(r"\n", text)}

    if max_tokens <= 0:
        bounds = [0]
        pos = 0
        while n - pos > max_chars:
            window_end = pos + max_chars
            # 창 안 가장 뒤의 문단 경계 → 없으면 행 경계 → 없으면 하드 컷.
            cut = max((b for b in para if pos < b <= window_end), default=0) \
                or max((b for b in lines if pos < b <= window_end), default=0) \
                or window_end
            bounds.append(cut)
            pos = cut
        return bounds

    tok = count or estimate_tokens
    cpt = max(1.0, len(text) / max(1, tok(text[:4000] or text)))   # 초기 자/토큰
    bounds = [0]
    pos = 0
    guard = 0
    while pos < n and guard < 100_000:
        guard += 1
        if tok(text[pos:]) <= max_tokens:
            break                                   # 남은 전부가 한 청크에 들어간다
        window_end = min(n, pos + max(1, int(max_tokens * cpt)))
        cut = max((b for b in para if pos < b <= window_end), default=0) \
            or max((b for b in lines if pos < b <= window_end), default=0) \
            or window_end
        # ⚠ **토큰 수는 부분합이 아니다** — tokenize(A+B) ≠ tokenize(A)+tokenize(B).
        # 그래서 창 크기를 자/토큰으로 잡아 놓고 끝내면 안 되고, 실제로 세어 보고
        # 넘치면 줄여야 한다. 이 수리 루프가 없으면 상한 초과가 그대로 새 나간다.
        shrink = 0
        while cut > pos + 1 and tok(text[pos:cut]) > max_tokens and shrink < 40:
            shrink += 1
            span = cut - pos
            target = pos + max(1, int(span * 0.85))
            cut = max((b for b in para if pos < b <= target), default=0) \
                or max((b for b in lines if pos < b <= target), default=0) \
                or target
        if cut <= pos:                              # 진전이 없으면 하드 컷으로 탈출
            cut = min(n, pos + max(1, int(max_tokens * cpt)))
        piece_tok = max(1, tok(text[pos:cut]))
        cpt = 0.7 * cpt + 0.3 * ((cut - pos) / piece_tok)   # 구간별 갱신(혼합 언어)
        bounds.append(cut)
        pos = cut
    return bounds


def normalize_boundaries(text: str, starts: list[int]) -> list[int]:
    """외부 청커(chonkie 등)의 시작 오프셋 후보를 **무손실 경계**로 정규화한다.

    P2 통합의 핵심: chonkie 는 좋은 검색 청크(토큰 기준)를 주지만 경계에서 공백을
    떨구거나 오프셋이 어긋날 수 있어 그대로 쓰면 복원이 깨진다. 후보를 (a) [0, len)
    범위로 클램프 (b) 정렬·중복제거 (c) b0=0 강제 한 뒤 chunk_i = text[b_i:b_{i+1}]
    로 슬라이스하면 — 공백이 어느 청크엔가 반드시 흡수되어 ``"".join == text`` 가
    **구성으로** 성립한다(외부 청커의 손실을 정규화가 봉합)."""
    n = len(text)
    valid = sorted({0} | {int(s) for s in starts if 0 < int(s) < n})
    return valid


def _section_title(chunk: str) -> str:
    m = _HEADING_RE.search(chunk)
    return m.group(2).strip() if m else ""


def chunk_doc(text: str, *, max_chars: int = DEFAULT_MAX_CHARS,
              boundaries: list[int] | None = None,
              max_tokens: int = 0,
              count: Callable[[str], int] | None = None) -> list[dict]:
    """텍스트 → [{index, start, end, text, section_title}] (순수). 복원 계약의
    생산자 — start/end 는 반개구간 오프셋, 연결하면 원문과 바이트 동일.

    ``boundaries`` 지정 시 외부 청커의 경계를 정규화해 사용(P2 — chonkie 통합);
    없으면 내장 문단/행/하드컷 경계(plan_boundaries).
    ``max_tokens`` 를 주면 문자가 아니라 **토큰** 기준으로 자른다(:func:`plan_boundaries`)."""
    if boundaries is not None:
        bounds = normalize_boundaries(text, boundaries) if text else [0]
    else:
        bounds = plan_boundaries(text, max_chars=max_chars,
                                 max_tokens=max_tokens, count=count)
    out = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        piece = text[start:end]
        out.append({"index": i, "start": start, "end": end,
                    "text": piece, "section_title": _section_title(piece)})
    return out
