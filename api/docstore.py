# -*- coding: utf-8 -*-
"""문서 정본(SSoT) 스토어 — Chunk=SSoT 코어 (D45, P1).

Vinc 의 사용자 원문 텍스트 **정본은 Chunk 다** — 별도 문서 blob·외부 파일에
의존하지 않는다(D45 순수형 결정). 이 모듈이 그 계약의 단일 쓰기/읽기 지점:

  put_doc    : raw 텍스트 → 무손실 경계 청킹 → Chunk MERGE(+ 스테일 청크 삭제)
               → _DocMeta 갱신 → MENTIONS 재링크. **파일 I/O 없음.**
  get_doc    : doc_id 의 Chunk 를 오프셋 연결로 원문 복원 + content_hash 검증.
  delete_doc : doc 단위 청크 전체 DETACH DELETE + 정체성 레지스트리 정리.
  list_docs  : owner 스코프 문서 목록(_DocMeta).

무손실 계약(D45 §복원): 청킹은 **경계 슬라이스로 구성**된다 — 경계 목록
b0=0 < b1 < … < bn=len(text) 에서 chunk_i = text[b_i:b_{i+1}]. 정의상
``"".join(chunks) == text`` 가 항상 성립하고(겹침·탈락 구조적으로 불가),
각 청크에 char_start/char_end 를 기록해 get_doc 이 연속성까지 검증한다.

정체성(D45 §정체성): 문서의 조인 키는 경로가 아니라 **doc_id**(내재 식별자,
_DocMeta 정본)다. source_file/origin 은 출처 **표시**로만 남는다(조인 금지).
chunk_id 는 ``{doc_id}#{index:05d}`` — 편집·재청킹에도 위치 안정(F4 해소).

변경감지(F5 해소): _DocMeta.content_hash(sha256) 비교 — 무변경 재적재는
스킵된다(파일명 기반이던 구 체크포인트와 달리 **내용**으로 판정).

쓰기 순서 = 자기치유: ① 구 청크 삭제 → ② 신 청크 기록 → ③ 메타(해시) 기록.
중간 크래시 시 해시가 갱신되지 않아 재-put 이 스킵되지 않고 전체를 재기록한다
(임베디드 단일 프로세스 — 다중 문 트랜잭션 없이도 수렴).

인덱스 수명주기: 벡터/FTS 인덱스가 걸린 열(text·embedding)은 수정 불가
(§5-4 M6) → 변이 전 drop. 재빌드는 검색 경로의 지연 보장
(``ensure_search_indexes``, T3b 자기복구)에 맡긴다 — 문서 1건 편집마다
전체 HNSW 재빌드로 수 초를 태우지 않기 위함.

임베딩은 선택(``embed`` 콜러블): 없으면 텍스트만 정본으로 적재되고 FTS 가
검색을 담당한다(벡터 인덱스는 embedding 있는 행 기준 — P2 에서 재청킹 시
증분 임베딩과 함께 확장).
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
import uuid
from typing import Any, Callable

from . import graphmd

# doc 단위 기본 청크 크기(문자). 검색 recall 용 세밀 분할은 임베딩 파이프라인
# (토큰 기준)이 담당하고, 여기는 **정본 보관 단위**라 다소 커도 무방하다.
DEFAULT_MAX_CHARS = 2000

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def make_doc_id(owner: str, content_hash: str, title: str = "", origin: str = "") -> str:
    """결정론 doc_id — (owner, 최초 내용 해시, title, origin) 파생. 같은 문서의
    중복 최초-적재가 자연 dedupe 되고(동일 입력 = 동일 id = 멱등 MERGE), 기기 간
    오프라인 재적재도 같은 정체성으로 수렴한다. **편집 후에도 id 는 불변** —
    put_doc 에 doc_id 를 넘겨 갱신하므로 내용이 바뀌어도 정체성은 유지된다."""
    basis = f"{owner}|{title}|{origin}|{content_hash}"
    return "d" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def chunk_id_of(doc_id: str, index: int) -> str:
    """위치 안정 chunk_id(F4) — 내용이 아니라 (doc, 순번) 에 종속. 편집 시 같은
    자리의 청크가 같은 id 로 MERGE 되고, 줄어든 꼬리만 삭제된다."""
    return f"{doc_id}#{index:05d}"


# ── 무손실 경계 청킹(순수 — I/O·DB 없음) ─────────────────────────────────────

# ── 토큰 추정(토크나이저 없는 환경용) ────────────────────────────────────────
# 데스크탑 사이드카는 PyInstaller 번들이라 토크나이저가 없다. 그래도 청킹은 거기서도
# 일어나므로, 실제 토큰 수를 **문자 계열 가중치로 근사**한다.
#
# 값의 출처는 **실제 코퍼스 회귀**다(2026-08-01, 실문서 541청크를 e5 토크나이저로 세어
# 최소자승): 한글 1.034 · CJK 1.440 · 기타 0.307 토큰/문자.
# ⚠ 처음엔 합성 문장 하나로 잡은 값(0.70 / 1.11 / 0.21)을 썼는데 **30% 이상 낮았다** —
# 반복 문장은 흔한 조각이 단일 토큰으로 잡혀 효율을 과대평가한다. 그대로 두면 청크
# 84건이 512 를 넘겼다. 표본은 합성이 아니라 실제 문서여야 한다.
#
# 여기에 **1.3 배 여유**를 곱한다. 회귀값 그대로면 41.6% 가 과소평가되고(청크가 상한을
# 넘겨 임베딩 때 조용히 잘린다 — 이 작업이 없애려는 바로 그 손실), ×1.3 이면 0.6% 로
# 떨어진다. 과대평가의 대가는 청크가 조금 짧아지는 것뿐이라 훨씬 싸다. 남는 0.6% 는
# 목표 400 과 상한 512 사이의 헤드룸(28%)이 흡수한다.
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


# ── DB 경계 ──────────────────────────────────────────────────────────────────

def _meta(store, doc_id: str) -> dict | None:
    rows = store.run(
        "MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.doc_id AS doc_id, "
        "d.owner_id AS owner_id, d.title AS title, d.origin AS origin, "
        "d.content_hash AS content_hash, d.chunk_count AS chunk_count, "
        "d.updated_at AS updated_at, d.needs_embed AS needs_embed, "
        # v7(D72) — put_doc 이 created_at 을 보존하려면 먼저 읽어야 한다.
        "d.created_at AS created_at, d.content_time AS content_time", {"id": doc_id})
    return rows[0] if rows else None


# ── 내용 시점(D72) ───────────────────────────────────────────────────────────
_CONTENT_TIME_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _norm_content_time(v: str | None) -> str:
    """`YYYY` · `YYYY-MM` · `YYYY-MM-DD` 만 통과시킨다. 나머지는 빈 문자열.

    형식이 곧 계약이다: 이 축의 비교·버킷은 전부 **문자열 사전식**이라(날짜 파싱을
    도입하지 않는다 — `ts` 계약과 동형) 자릿수가 어긋나면 정렬이 조용히 무너진다.
    `1905-6` 처럼 자리를 안 채운 값도 거부하는 이유가 그것이다. 기원전·음수 연도는
    미지원이고, 불합격은 던지지 않고 **비운다** — 저장은 성공하되 축에서 빠진다."""
    s = str(v or "").strip()
    return s if _CONTENT_TIME_RE.match(s) else ""


def _stamp_updated_at(store) -> str:
    """정본 갱신 시각 — 스토어 내 단조 HLC(revstamp). 벽시계 스큐에도 동기화
    LWW 비교가 성립하도록. revstamp 미가용(경량 스토어)이면 벽시계로 강등."""
    try:
        from . import revstamp
        return revstamp.hlc_now(store)
    except Exception:
        return _now_iso()


def _next_doc_seq(store, owner: str) -> int:
    """owner별 단조 doc sync_seq(P3 delta 커서). revstamp 카운터 재사용 —
    노드 sync 와 같은 지평의 커서를 공유하지 않도록 'doc:' 네임스페이스로 분리."""
    try:
        from . import revstamp
        return revstamp.next_seq(store, f"doc:{owner}")
    except Exception:
        return 0


def _node_stamp(store, owner: str, rev: int = 0) -> dict:
    """노드 추적열 {updated_at, rev, sync_seq} — 이 모듈이 **생 Cypher 로** 쓰는
    Concept 에 붙인다(위 `_stamp_updated_at` 의 노드판).

    왜 필요한가(실측 2026-08-17): `extract_doc` 은 개념을 fragment 를 거치지 않고
    직접 MERGE 하면서 이 셋을 안 찍었다. `CREATE NODE TABLE` 은 열 기본값을 안 내므로
    값은 0 이 아니라 **NULL** 이 되고, delta 술어 `c.sync_seq > $since` 는 NULL 을
    배제한다 — 그래서 최초 전량 push(`since=0`) 이후에 문서가 만든 개념은 **어느 기기
    에도 영영 가지 않았다**(오류도 경고도 없이 노드 통째로). 재현: 전량 push 로 커서를
    1 로 올린 뒤 추출로 개념을 만들고 `since=1` 을 뽑으면 그 개념만 빠진다.

    fragment 로 라우팅해 재사용하는 편이 깔끔하지만 그쪽은 `status`·`stem`·
    `source_file` 을 못 써서(이 경로가 쓰는 열들) 못 옮긴다. 그래서 열 셋만 여기서 붙인다.

    revstamp 미가용(경량 스토어)이면 `_stamp_updated_at` 과 같은 태도로 강등한다."""
    try:
        from . import revstamp
        return revstamp.make_stamp(store, {"owner": owner, "rev": rev})
    except Exception:
        return {"updated_at": _now_iso(), "rev": int(rev or 0) + 1, "sync_seq": 0}


def doc_id_for_origin(owner: str, origin: str) -> str:
    """origin(파일 경로 등 **안정 외부 식별자**) 기반 결정론 doc_id(P2). 같은
    파일의 재적재가 같은 doc 로 수렴한다(내용 해시 기반 make_doc_id 와 달리 편집에도
    id 불변) — 배치 파일 이관의 정체성 원천."""
    return "d" + hashlib.sha256(f"{owner}|origin|{origin}".encode("utf-8")).hexdigest()[:16]


def doc_hash(store, doc_id: str) -> str | None:
    """doc 의 현재 content_hash(없으면 None) — 배치 증분이 임베딩·청킹 **전에**
    무변경을 판정해 스킵하기 위한 값(F5 대체: 파일명 아닌 내용 기반)."""
    m = _meta(store, doc_id)
    return m.get("content_hash") if m else None


def _clear_doc_tombstone(store, doc_id: str) -> None:
    try:
        store.run("MATCH (t:_DocTombstone) WHERE t.doc_id = $id DELETE t", {"id": doc_id})
    except Exception:
        pass


def _drop_vector_index_if_any(store) -> None:
    """**벡터가 새로 생기는 쓰기** 직전에만 부른다(멱등). 재빌드는 검색 경로의
    ensure_search_indexes 지연 보장(T3b)이 담당 — 여기서 재빌드하지 않는다.

    ⚠ 이름과 범위가 2026-07-31 에 좁아졌다. 예전엔 `_drop_indexes_if_any` 로 **FTS 까지**
    드롭했고 문서 저장·삭제·임베딩 적용 네 경로가 전부 그걸 불렀다. 실측하니 그 설계가
    **임베딩이 도는 동안 키워드 검색을 100% 실패시키고 있었다**(60라운드 중 FTS 성공 0회).
    지금 유일하게 동작하는 검색이 FTS 인데 배경 작업이 주기적으로 그걸 끄고 있었다.

    벡터만 드롭해야 하는 이유와, 그마저도 모든 경로에서 필요하지는 않은 이유는
    :meth:`store.embedded_backend.EmbeddedBackend.drop_vector_index` 주석에 실측과 함께 있다.
    요약: **"벡터가 없던 자리에 벡터가 생기는 것"만 위험하다**(HNSW 삽입 → 수백 건에서
    세그폴트). 벡터 없는 청크 삽입·대량 삭제·기존 벡터 값 변경은 전부 안전하다(실측).

    확장 미가용 환경에서는 로드가 실패하는데 그런 스토어엔 인덱스가 존재할 수도 없다 —
    **정본 쓰기를 막을 이유가 아니다**(SSoT 는 검색 확장 없이도 성립해야 한다).
    구 백엔드 호환: `drop_vector_index` 가 없으면 넓은 쪽으로 물러난다."""
    fn = getattr(store, "drop_vector_index", None) or getattr(store, "drop_search_indexes", None)
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


def _with_fts_repair(store, fn: Callable[[], Any]) -> Any:
    """FTS 색인 불일치로 실패하면 색인을 재빌드하고 **1회만** 재시도한다.

    왜 필요한가(2026-08-09 실측). ladybug 0.18.0 의 FTS 증분 유지가 시간이 지나며
    존재하는 행의 문서를 잃는다. 그 행을 지우려는 순간 엔진이 이렇게 던진다:

        FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
        missing during delete. Drop and recreate the FTS index.

    처방을 오류 문구가 직접 말해 주고, 백엔드에는 `rebuild_fts_index()` 가 있는데,
    **라이브 허브에서 그걸 부를 길이 없었다.** 그래서 6초짜리 조작이 세 번 다 하드다운
    + 파일 수술이 됐다(08-04 · 08-07 · 08-09). 이 함수가 그 간극이다.

    설계 의도 두 가지:

    - **새 표면을 만들지 않는다.** 관리용 엔드포인트나 MCP 도구를 늘리면 프록시
      허용목록을 세 곳에 등재해야 하고(웹 패리티), 어긋나면 그 기능만 조용히 죽는다.
      이건 쓰기 경로 안에서 끝난다.
    - **조건을 좁게 잡는다.** 엔진이 스스로 "inconsistent" 라고 말한 경우에만 돈다
      (:meth:`EmbeddedBackend.is_fts_inconsistent`). 아무 예외에나 재빌드를 걸면
      진짜 결함을 5.8초짜리 재시도로 덮어 버린다.

    재시도는 1회다. 재빌드 후에도 같은 오류면 그건 다른 문제이므로 그대로 올린다.

    이 패턴은 새로 만든 게 아니다 — `ensure_search_indexes`(색인 **부재**를 검색
    경로에서 지연 복구, T3b)의 형제 조건이다. 그쪽은 없는 색인을 만들고 이쪽은
    어긋난 색인을 다시 만든다."""
    try:
        return fn()
    except Exception as exc:
        checker = getattr(store, "is_fts_inconsistent", None)
        rebuild = getattr(store, "rebuild_fts_index", None)
        if rebuild is None or checker is None or not checker(exc):
            raise
        # 조용히 고치지 않는다 — 재빌드가 돌았다는 사실이 로그에 남아야 이 결함이
        # 얼마나 자주 나는지 셀 수 있다. 무음 자기복구는 "안 나는 것"처럼 보인다.
        print(f"[WARN] FTS 색인 불일치 — 재빌드 후 1회 재시도: "
              f"{str(exc).splitlines()[0][:200]}", file=sys.stderr)
        rebuild()
        return fn()


def put_doc(store, *, owner: str, text: str, doc_id: str | None = None,
            title: str = "", origin: str = "", layer: str = "ssot",
            max_chars: int = DEFAULT_MAX_CHARS,
            boundaries: list[int] | None = None,
            embed: Callable[[list[str]], list[list[float]]] | None = None,
            link: bool = True, updated_at: str | None = None,
            created_at: str | None = None, content_time: str | None = None) -> dict:
    """raw 텍스트를 doc 단위로 적재/갱신(파일 I/O 없음) — Chunk=SSoT 의 쓰기 정본.

    - 신규: doc_id 미지정 시 결정론 파생(make_doc_id). 반환 doc_id 로 이후 편집.
    - 갱신: 기존 doc_id + 변경 텍스트 → 구 청크 삭제 후 재청킹(고아 0, F3 해소).
    - 무변경: content_hash 동일 → skipped(F5 — 내용 기반 변경감지).
    - SEC-1: 기존 문서의 owner 불일치 → 거부(남의 정본을 재-put 으로 탈취 불가).

    ``boundaries`` (P2): 외부 청커(chonkie)의 시작 오프셋 후보 — 무손실 정규화 후
    검색 청크로 사용(없으면 내장 경계). ``layer``: 검색 pre-filter 태그(파일 배치
    이관 시 원 레이어 보존). ``updated_at`` (P3): 동기화 적용 시 원격 HLC 를 그대로
    박기 위한 주입점(로컬 쓰기는 None → 스토어 HLC 스탬프)."""
    base: dict[str, Any] = {"kind": "doc.put", "executed": False}
    if not owner or not isinstance(owner, str):
        base["error"] = "owner is required — 정본(SSoT)은 무소유로 두지 않는다(SEC-1)"
        return base
    if not isinstance(text, str):
        base["error"] = "text must be a string"
        return base

    content_hash = _sha(text)
    doc_id = doc_id or make_doc_id(owner, content_hash, title, origin)
    base["doc_id"] = doc_id

    prev = _meta(store, doc_id)
    if prev is not None:
        if prev.get("owner_id") and prev["owner_id"] != owner:
            base["error"] = "owner mismatch — 남의 문서에는 쓸 수 없다(SEC-1)"
            return base
        if prev.get("content_hash") == content_hash and prev.get("title") == title:
            base.update(executed=True, skipped=True, chunks=int(prev.get("chunk_count") or 0),
                        content_hash=content_hash)
            return base

    chunks = chunk_doc(text, max_chars=max_chars, boundaries=boundaries)
    vectors = embed([c["text"] for c in chunks]) if embed and chunks else None
    stamp = updated_at or _stamp_updated_at(store)
    seq = _next_doc_seq(store, owner)

    # 벡터를 **함께 싣는 경로일 때만** 드롭한다. embed 콜러블이 없으면(허브·데스크탑
    # 기본) 청크는 벡터 없이 들어가고, 그건 HNSW 를 건드리지 않는다 — 실측으로 2,000청크
    # 삽입·삭제 모두 무사했다. 그때까지 FTS·벡터 인덱스를 살려 두면 저장 중에도 검색이
    # 계속 답한다. 벡터는 나중에 워커가 apply_embeddings 로 채우고, 그쪽이 드롭을 진다.
    if vectors is not None:
        _drop_vector_index_if_any(store)
    # ① 구 청크 삭제(재청킹으로 개수가 줄어도 고아가 남지 않게 — 부착 MENTIONS 도
    #    DETACH 로 함께 소멸). ② 신 청크 기록. ③ 메타(해시) 기록 — 이 순서가
    # 중간 크래시 자기치유의 근거다(모듈 독스트링).
    removed = 0
    if prev is not None:
        cnt = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN count(c) AS c",
                        {"d": doc_id})
        removed = int(cnt[0].get("c")) if cnt else 0
        # 자식 벡터(v8)도 함께 — 재청킹으로 청크가 줄면 옛 자식이 없는 부모를 가리켜
        # 고아가 된다. 새 벡터는 워커가 apply_embeddings 로 다시 만든다.
        store.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc_id})
        _with_fts_repair(store, lambda: store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc_id}))

    for i, c in enumerate(chunks):
        # ⚠ 파라미터명 주의: start/end 는 Cypher 예약어 — $start/$end 는 파서가
        # 거부한다(실측, ladybug 0.18). $cs/$ce 로 우회.
        params = {
            "id": chunk_id_of(doc_id, c["index"]), "doc": doc_id, "owner": owner,
            "origin": origin, "sec": c["section_title"], "idx": c["index"],
            "cs": c["start"], "ce": c["end"], "cc": len(c["text"]), "text": c["text"],
        }
        params["lyr"] = layer
        sets = ("c.doc_id = $doc, c.owner_id = $owner, c.source_file = $origin, "
                "c.section_title = $sec, c.chunk_index = $idx, c.layer = $lyr, "
                "c.char_start = $cs, c.char_end = $ce, c.char_count = $cc, "
                "c.token_count = 0, c.text = $text")
        if vectors is not None:
            sets += ", c.embedding = $emb"
            params["emb"] = list(vectors[i])
        store.run(f"MERGE (c:Chunk {{chunk_id: $id}}) SET {sets}", params)

    # needs_embed: 임베딩 없이 적재됐으면 1(앱 편집·동기화 수신 경로) — 벡터 검색이
    # 낡음을 표식하고 백그라운드 재임베딩 잡의 대상이 된다. embed 를 받은 경로(배치)는 0.
    needs_embed = 0 if vectors is not None else 1
    # v7(D72) — created_at 은 **최초 1회**만 찍고 편집이 보존한다. 여태 문서에 남는
    # 시각이 updated_at 하나뿐이라, 고칠 때마다 "언제 만든 글인가"가 지워졌다.
    # 레거시 행은 원천이 없으므로 '' 로 남긴다(오늘로 채우면 그게 곧 거짓 시각이다).
    if prev is None:
        born = created_at or stamp          # 신규 — 지금이 곧 최초 시각
    else:
        born = str(prev.get("created_at") or "")   # 편집 — 있으면 보존, 없으면 ''
    ctime = _norm_content_time(content_time) if content_time is not None \
        else str((prev or {}).get("content_time") or "")
    store.run(
        "MERGE (d:_DocMeta {doc_id: $id}) SET d.owner_id = $owner, d.title = $title, "
        "d.origin = $origin, d.content_hash = $hash, d.chunk_count = $n, "
        "d.updated_at = $ts, d.sync_seq = $seq, d.needs_embed = $ne, "
        "d.created_at = $born, d.content_time = $ctime",
        {"id": doc_id, "owner": owner, "title": title, "origin": origin,
         "hash": content_hash, "n": len(chunks), "ts": stamp, "seq": seq,
         "ne": needs_embed, "born": born, "ctime": ctime})
    # 재생성 = 로컬 doc 툼스톤 상쇄(낡은 삭제가 동기화에서 재전파되지 않게, P3).
    _clear_doc_tombstone(store, doc_id)

    linked = 0
    if link:
        linked = _relink_mentions(store, doc_id, owner=owner, origin=origin)

    base.update(executed=True, skipped=False, chunks=len(chunks), replaced=removed,
                content_hash=content_hash, mentions=linked, updated_at=stamp,
                sync_seq=seq, needs_embed=bool(needs_embed),
                note="search indexes dropped — 검색 경로가 지연 재빌드(T3b)")
    return base


def _relink_mentions(store, doc_id: str, *, owner: str, origin: str) -> int:
    """이 doc 의 청크만 대상으로 :MENTIONS 재생성. 링크 규칙·우선순위는 E-G1
    링커(engines.mentions_link)를 그대로 재사용한다 — 규칙 분기 금지(SSoT 하나).
    구 링크는 put_doc 의 DETACH DELETE 가 이미 지웠다. 실패는 적재를 무효화하지
    않는다(증강 — embed_pipeline 의 링크 단계와 동일한 태도)."""
    try:
        from engines.mentions_link import compute_links, merge_links, _fetch_concepts
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS chunk_id, "
            "c.text AS text", {"d": doc_id})
        chunks = [{"chunk_id": r["chunk_id"], "source_file": origin,
                   "text": r.get("text") or ""} for r in rows or []]
        concepts = _fetch_concepts(store, owner=owner)
        links = compute_links(chunks, concepts)
        # 쓰기도 링커의 것을 쓴다 — 여기 MERGE 를 복제하고 있었고, 그 사본에
        # `(co:Concept)` 이 박혀 있어 결정으로 향하는 링크가 조용히 0건이 됐다.
        return merge_links(store, links, concepts)
    except Exception:
        return 0


def get_doc(store, doc_id: str, *, owner: str | None = None) -> dict:
    """doc_id 의 원문을 청크 오프셋 연결로 복원 + 무손실 검증.

    verified = (연속성: b0=0 · char_start_i == char_end_{i-1}) AND
               (sha256(복원문) == _DocMeta.content_hash).
    레거시 청크(오프셋 -1)는 복원 불가로 보고한다(검색만 가능)."""
    base: dict[str, Any] = {"kind": "doc.get", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is None:
        # F7 — 여태 `"unknown doc_id"` 한 줄이었다. 오타 하나에 다음 수가 없었다.
        base["error"] = "unknown doc_id"
        cands = near_docs(store, doc_id, owner=owner)
        base["hint"] = ("doc_id must match exactly. "
                        + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                        + "Use vinc_doc_list to browse, or vinc_search to find a "
                          "document by its content.")
        if cands:
            base["candidates"] = cands
        return base
    if owner is not None and meta.get("owner_id") != owner:
        base["error"] = "owner mismatch — 남의 문서는 읽을 수 없다(SEC-1)"
        return base
    rows = store.run(
        "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_index AS idx, "
        "c.char_start AS cs, c.char_end AS ce, c.text AS text "
        "ORDER BY c.chunk_index", {"d": doc_id})
    rows = rows or []

    def _off(v) -> int:   # ⚠ `v or -1` 금지 — 정당한 오프셋 0 이 falsy 로 강등된다
        return -1 if v is None else int(v)

    contiguous = bool(rows) and _off(rows[0].get("cs")) == 0
    prev_end = 0
    parts: list[str] = []
    for r in rows:
        s, e = _off(r.get("cs")), _off(r.get("ce"))
        if s != prev_end or e < s:
            contiguous = False
        prev_end = e
        parts.append(r.get("text") or "")
    text = "".join(parts)
    verified = contiguous and _sha(text) == meta.get("content_hash")
    base.update(executed=True, text=text, title=meta.get("title") or "",
                origin=meta.get("origin") or "", owner_id=meta.get("owner_id"),
                content_hash=meta.get("content_hash"), chunks=len(rows),
                updated_at=meta.get("updated_at"), verified=verified,
                needs_embed=bool(int(meta.get("needs_embed") or 0)),
                # v7(D72) — 편집기가 내용 시점을 편집하려면 지금 값을 알아야 한다.
                created_at=meta.get("created_at") or "",
                content_time=meta.get("content_time") or "")
    if not verified:
        base["note"] = ("복원 검증 실패 — 레거시(오프셋 미기록) 또는 부분 기록. "
                        "re-put 으로 치유 가능")
    return base


def near_docs(store, needle: str, *, owner: str | None = None,
              limit: int = 3) -> list[str]:
    """놓친 doc_id 에 가까운 문서 후보(F7). id 와 제목 양쪽을 본다 — 사용자는 둘 중
    아무거나 기억하고 있다. stdlib `difflib` 뿐이라 새 색인이 필요 없다."""
    import difflib
    try:
        where = "WHERE d.owner_id = $o " if owner else ""
        rows = store.run(
            f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title",
            {"o": owner} if owner else None) or []
    except Exception:                                             # noqa: BLE001
        return []                      # 힌트를 만들다 본 응답을 죽이지 않는다
    ids = [r.get("doc_id") for r in rows if r.get("doc_id")]
    hits = difflib.get_close_matches(needle, ids, n=limit, cutoff=0.5)
    if len(hits) < limit:              # id 로 못 찾으면 제목으로 한 번 더
        by_title = {(r.get("title") or ""): r.get("doc_id") for r in rows}
        for t in difflib.get_close_matches(needle, list(by_title), n=limit, cutoff=0.5):
            if by_title[t] not in hits:
                hits.append(by_title[t])
    return hits[:limit]


def delete_doc(store, doc_id: str, *, owner: str | None = None,
               tombstone: bool = True) -> dict:
    """doc 단위 완전 삭제 — 청크(부착 MENTIONS 포함 DETACH)와 정체성 레지스트리.
    owner 지정 시 소유 가드(SEC-1). 멱등.

    ``tombstone`` (P3): 삭제 표식을 남겨 동기화가 상대 기기에도 삭제를 전파하게
    한다(하드삭제만으론 다음 delta 에서 부활). 동기화 apply 의 하드삭제는 False."""
    base: dict[str, Any] = {"kind": "doc.delete", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is not None and owner is not None and meta.get("owner_id") != owner:
        base["error"] = "owner mismatch — 남의 문서는 지울 수 없다(SEC-1)"
        return base
    cnt = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN count(c) AS c",
                    {"d": doc_id})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        # 드롭하지 않는다 — 문서 하나 지우자고 색인을 끊으면 그동안 검색이 죽는다
        # (예전 설계가 그랬고, 임베딩이 도는 동안 키워드 검색 성공률이 0이었다).
        #
        # ⚠ 여기 있던 "삭제는 인덱스를 깨지 않는다(실측: 2,000청크를 인덱스 살아 있는
        # 채로 지워도 무사)" 는 **2026-08-09 에 반증됐다.** 그 실측은 갓 만든 색인에
        # 대한 것이었고, 오래 증분 유지된 색인은 존재하는 행의 문서를 잃는다. 그 행을
        # 지우면 엔진이 불일치로 던진다 — 라이브에서 실제로 났고 사본으로 재현했다.
        # 그래서 아래 삭제를 `_with_fts_repair` 로 감싼다. 낡은 실측을 근거로 남겨
        # 두면 다음 사람이 또 "여긴 안전하다"고 읽는다.
        #
        # ⚠ 자식 벡터(v8)를 **먼저** 지운다. 남기면 검색이 없는 문서를 가리키는 고아가
        # 되고, 그건 부모 조인이 빈 결과를 내 조용히 사라지는 게 아니라 인덱스에만
        # 남아 상위 k 자리를 잡아먹는다.
        store.run("MATCH (v:ChunkVec) WHERE v.doc_id = $d DETACH DELETE v", {"d": doc_id})
        _with_fts_repair(store, lambda: store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc_id}))
    owner_id = (meta or {}).get("owner_id") or owner or ""
    if meta is not None:
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id DELETE d", {"id": doc_id})
    if tombstone and (meta is not None or n):
        store.run(
            "MERGE (t:_DocTombstone {doc_id: $id}) SET t.owner_id = $o, "
            "t.deleted_at = $ts, t.sync_seq = $seq",
            {"id": doc_id, "o": owner_id, "ts": _stamp_updated_at(store),
             "seq": _next_doc_seq(store, owner_id)})
    base.update(executed=True, deleted_chunks=n, deleted_meta=meta is not None)
    if meta is None and not n:
        # F7 — 여태 없는 문서를 지워도 응답이 성공과 **똑같았다**. 멱등이라 오류는
        # 아니지만, 오타로 지운 줄 알고 넘어가는 것이 실제 피해다.
        base["found"] = False
        base["error"] = "unknown doc_id — nothing was deleted"
        cands = near_docs(store, doc_id, owner=owner)
        base["hint"] = ("The delete is idempotent, so this is not an error — but "
                        "nothing matched. "
                        + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                        + "Use vinc_doc_list to check the id.")
        if cands:
            base["candidates"] = cands
    else:
        base["found"] = True
    return base


class MentionTally:
    """문서 → 언급 개념 집계. `list_docs` 와 `query.nav_tree` 의 **공유 원천**.

    두 표면(모든 문서 목록·좌측 문서 트리)이 같은 문서를 다른 주제/다른 노드에
    걸면 사용자에게는 그냥 버그다. 그래서 집계를 한 군데서만 만든다.

    - `dom[doc_id][domain]`  = 그 주제 개념들의 언급 횟수 합 → 대표 주제(`topic`)
    - `con[doc_id][cid]`     = 개념별 언급 횟수 → 대표 개념(`primary_concept_id`)
    - `cdom[cid]` · `ctitle[cid]` = 개념의 주제·제목

    **대표 개념은 대표 주제 안에서 고른다**(전역 최다가 아니라). 문서가 주제 T 밑에
    걸려 있는데 눌렀더니 다른 주제의 노드로 날아가면 트리가 자기를 반박한다."""

    __slots__ = ("dom", "con", "cdom", "ctitle")

    def __init__(self) -> None:
        self.dom: dict[str, dict[str, int]] = {}
        self.con: dict[str, dict[str, int]] = {}
        self.cdom: dict[str, str] = {}
        self.ctitle: dict[str, str] = {}

    def topics(self, doc_id: str) -> list[str]:
        """걸린 주제 전부, 많은 순(동률은 이름순 — 결정론)."""
        t = self.dom.get(doc_id) or {}
        return [k for k, _ in sorted(t.items(), key=lambda kv: (-kv[1], kv[0]))]

    def primary(self, doc_id: str, topic: str) -> tuple[str, str]:
        """`topic` 안에서 가장 많이 언급된 개념 `(id, title)`. 없으면 `("", "")`.

        동률은 개념 id 순(결정론). `topic` 이 대표 주제인 한 후보는 반드시 하나
        이상 존재한다 — 그 주제가 뽑힌 근거가 곧 그 주제 개념의 언급이므로."""
        c = self.con.get(doc_id) or {}
        best = sorted(((cid, n) for cid, n in c.items() if self.cdom.get(cid, "") == topic),
                      key=lambda kv: (-kv[1], kv[0]))
        return (best[0][0], self.ctitle.get(best[0][0], "")) if best else ("", "")


def mention_tally(store, *, owner: str | None = None) -> MentionTally:
    """`(Chunk)-[:MENTIONS]->(Concept)` 를 **한 번의 집계 쿼리로** 전 문서 분 수집.

    문서마다 조회하지 않는다. MENTIONS 미구축·구스토어에서는 빈 집계로 강등한다
    (실패가 아니다 — 주제 없는 목록은 여전히 유효하다)."""
    tally = MentionTally()
    # ⚠ try 는 **쿼리에만** 건다. 집계 루프까지 감싸면 프로그래밍 오류가 "구스토어
    # 강등"으로 위장돼 조용히 사라진다(실제로 그렇게 한 번 당했다 — 아래 주석 참조).
    try:
        mrows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
            + ("WHERE c.owner_id = $o AND (ch.owner_id = $o OR ch.owner_id = '') " if owner else "")
            + "RETURN ch.doc_id AS doc_id, c.concept_id AS cid, c.title AS title, "
              "c.domain AS domain, count(*) AS n",
            {"o": owner} if owner else None) or []
    except Exception:
        mrows = []   # MENTIONS 미구축·구스토어 — 주제 없이 목록만(강등, 실패 아님)
    for r in mrows:
        did = r.get("doc_id") or ""
        cid = r.get("cid") or ""
        if not did or not cid:
            continue
        n = int(r.get("n") or 0)
        dom = (r.get("domain") or "").strip()
        tally.cdom[cid] = dom
        tally.ctitle[cid] = r.get("title") or ""
        # ⚠ `t.setdefault(d, {})[k] = t[d].get(k, 0) + n` 로 쓰지 말 것: 파이썬은
        # 대입문의 **오른쪽을 먼저** 평가하므로 첫 등장에서 KeyError 가 난다.
        cbucket = tally.con.setdefault(did, {})
        cbucket[cid] = cbucket.get(cid, 0) + n
        if dom:   # 주제 집계는 주제 있는 개념만(무주제 개념이 대표 주제를 못 만든다)
            dbucket = tally.dom.setdefault(did, {})
            dbucket[dom] = dbucket.get(dom, 0) + n
    return tally


def list_docs(store, *, owner: str | None = None, limit: int | None = None,
              cursor: str | None = None, detail: str = "full") -> dict:
    """문서 목록(_DocMeta) + **주제·대표 개념 파생**. owner 지정 시 소유 스코프.

    ⚠ **기본값은 종전 그대로다**(`detail="full"` · 전량). `nav_tree` 가 이 함수를
    그대로 쓰면서 `topic`·`primary_concept_id` 를 요구하기 때문이다 — 기본을 요약으로
    바꾸면 좌측 문서 트리가 조용히 무너진다(F5 를 도구 쪽에서만 바꾸는 이유).

    F5 — MCP 표면은 `detail="summary"` · `limit=30` 으로 부른다. 실측 51,537B
    (약 12,900토큰)이 문서 120건에서 나왔고, 200건이 되면 쓸 수 없는 수준이 된다.
    요약형은 `mention_tally` 를 **아예 계산하지 않는다** — 크기뿐 아니라 왕복도 준다.
    커서·has_more 규약은 `vinc_search`(GM-14)와 같은 형태다.

    ⚠ 문서 자체에는 주제(domain) 열이 없다 — `_DocMeta` 는 정체성·변경감지 메타만
    갖는다(D45). 그래서 주제는 **문서가 언급하는 개념에서 유도**한다:
    `(Chunk)-[:MENTIONS]->(Concept)` 를 타고 그 개념들의 domain 을 세어, 가장 많이
    걸린 주제를 그 문서의 주제로 삼는다. 링크가 아직 없는 문서는 주제가 없다(빈 문자열)
    — 억지로 배정하지 않는다(D45-V "없으면 없다고 말한다").

    반환 doc 에 싣는 것:
    - `topic`(대표 주제) · `topics`(걸린 주제 전부, 많은 순)
    - `primary_concept_id`·`primary_concept_title`(D69) — 그 문서를 눌렀을 때
      **착지할 그래프 노드**. 문서 목록·좌측 트리의 클릭이 여기로 간다. 언급이
      없으면 빈 문자열이고, 그 문서는 착지 대상이 없다(편집기로만 열린다)."""
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title, "
        "d.origin AS origin, d.chunk_count AS chunks, d.updated_at AS updated_at, "
        "d.needs_embed AS needs_embed, "
        "d.created_at AS created_at, d.content_time AS content_time "   # v7(D72)
        "ORDER BY d.updated_at DESC", {"o": owner} if owner else None)
    # 관례(GRAPH.md)는 자료가 아니라 **자료에 대한 규약**이다. 문서 목록·좌측 트리
    # (nav_tree 가 이 함수를 그대로 쓴다)에서 빼서 사용자의 실제 자료 사이에 끼지 않게
    # 한다. 전용 자리는 앱의 관례 시트이고, 어시스턴트는 vinc_conventions 로 읽는다.
    # 정확한 id 로는 여전히 읽고 지울 수 있다(get_doc/delete_doc 은 그대로).
    docs = [{**r, "needs_embed": bool(int(r.get("needs_embed") or 0))}
            for r in (rows or []) if r.get("doc_id") != graphmd.CONVENTIONS_DOC_ID]

    total = len(docs)
    off = 0
    if cursor:
        # 순환 임포트 회피 — query 가 이 모듈을 이미 쓴다(nav_tree). 지연 임포트로
        # 커서 규약을 **한 벌만** 유지한다(검색과 같은 코덱).
        from .query import cursor_decode
        data = cursor_decode(cursor)        # 무효면 ValueError — 호출부가 표면화
        off = max(0, int(data.get("off") or 0))
    page = docs[off:off + limit] if limit else docs[off:]

    if detail == "summary":
        # 요약형은 언급 집계를 **건너뛴다.** topics·primary_concept 는 전 문서의
        # MENTIONS 를 훑어야 나오는 파생값이라, 목록을 보려고 그것까지 계산하면
        # 크기와 시간을 함께 낸다.
        page = [{"doc_id": d["doc_id"], "title": d.get("title") or "",
                 "updated_at": d.get("updated_at") or "", "chunks": d.get("chunks")}
                for d in page]
    else:
        tally = mention_tally(store, owner=owner)
        for d in page:
            ordered = tally.topics(d["doc_id"])
            d["topics"] = ordered
            d["topic"] = ordered[0] if ordered else ""
            cid, ctitle = tally.primary(d["doc_id"], d["topic"])
            d["primary_concept_id"] = cid
            d["primary_concept_title"] = ctitle

    out = {"kind": "doc.list", "executed": True, "docs": page, "count": len(page)}
    if limit or cursor:
        # 페이지를 자를 때만 항해 정보를 싣는다 — 전량 호출(nav_tree·CLI)의 응답
        # 형태를 바꾸지 않기 위해서다.
        from .query import cursor_encode
        nxt = off + len(page)
        out["total_count"] = total
        out["has_more"] = nxt < total
        out["next_cursor"] = (cursor_encode({"v": 1, "off": nxt, "tc": total})
                              if nxt < total else None)
    return out


def reembed_doc(store, doc_id: str, *, owner: str | None,
                embed: Callable[[list[str]], list[list[float]]]) -> dict:
    """문서의 기존 청크에 임베딩을 채운다(D45-E 후속3 — 재청킹 없이 벡터만 갱신).

    앱 편집/동기화 수신 경로는 임베딩 없이 청크를 적재해 needs_embed=1 이다. 이
    잡이 그 청크의 text 를 임베딩해 embedding 열을 SET 하고 needs_embed=0 으로
    내린다. 인덱스가 걸린 열 변이라 drop→SET→(검색 경로 지연 재빌드). SEC-1 가드."""
    base: dict[str, Any] = {"kind": "doc.reembed", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is None:
        base["error"] = "unknown doc_id"
        return base
    if owner is not None and meta.get("owner_id") != owner:
        base["error"] = "owner mismatch (SEC-1)"
        return base
    rows = store.run(
        "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id, c.text AS text "
        "ORDER BY c.chunk_index", {"d": doc_id}) or []
    if not rows:
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
                  {"id": doc_id})
        base.update(executed=True, embedded=0)
        return base
    vectors = embed([r.get("text") or "" for r in rows])
    # 여기는 드롭이 **필수**다 — 벡터가 없던(또는 낡은) 청크에 벡터를 채우는 경로라
    # HNSW 삽입이 발생하고, 그게 수백 건에서 세그폴트를 낸다(실측). FTS 는 살려 둔다.
    _drop_vector_index_if_any(store)
    for r, v in zip(rows, vectors):
        store.run("MATCH (c:Chunk) WHERE c.chunk_id = $id SET c.embedding = $emb",
                  {"id": r["id"], "emb": list(v)})
    store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
              {"id": doc_id})
    # note 가 오래 "search indexes dropped" 였는데 **드롭되는 건 벡터뿐이다**(바로 위
    # 주석대로 FTS 는 일부러 살려 둔다). 그 부정확한 문구가 호출부에 "FTS 도 없어졌으니
    # 다시 만들어야 한다"고 읽혔고, 실제로 `/doc/reembed` 가 그 뒤에 전량 build 를
    # 불러 'already exists' 로 터지고 있었다(2026-08-09).
    base.update(executed=True, embedded=len(rows),
                note="vector index dropped — 검색 경로가 지연 재빌드 (FTS 는 유지)")
    return base


def pending_embed_chunks(store, *, limit: int = 16) -> list[dict]:
    """임베딩이 필요한(needs_embed=1) 문서를 **doc 단위**(최대 ``limit`` 개 문서)로,
    각 문서의 전 청크 text 와 함께 낸다 — **허브 자체호스팅 임베딩 워커**용
    (D45-E 후속·B). 워커가 이 text 를 로컬 모델로 임베딩해 :func:`apply_embeddings`
    로 돌려보낸다(원문이 서버 밖으로 안 나감).

    doc 단위로 내는 이유: 문서의 전 청크가 한 배치에 함께 가야 apply 가 needs_embed
    를 깔끔히 내린다(청크 캡으로 쪼개면 큰 문서가 영영 안 끝난다). owner 스코프
    아님 — 허브 서버사이드 유지보수 잡이라 전 소유자 대상. **루프백 내부 표면**
    (공개·프록시 미노출)에서만 호출."""
    docs = store.run(
        "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 "
        "RETURN d.doc_id AS doc_id ORDER BY d.doc_id LIMIT $lim", {"lim": int(limit)}) or []
    out: list[dict] = []
    for d in docs:
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d "
            "RETURN c.chunk_id AS chunk_id, c.text AS text ORDER BY c.chunk_index",
            {"d": d["doc_id"]}) or []
        for r in rows:
            out.append({"doc_id": d["doc_id"], "chunk_id": r["chunk_id"],
                        "text": r.get("text") or ""})
    return out


def apply_embeddings(store, doc_id: str, embeddings: dict[str, Any]) -> dict:
    """워커가 계산한 청크 임베딩을 저장한다(chunk_id 키 — 순서 무관, 편집 재정렬에 강건).

    **값은 벡터 하나 또는 벡터 목록**이다(v8 — 부모-자식). 청크는 2000자 단위인데 모델
    입력 상한은 512토큰이라 한 벡터로는 앞부분만 담긴다(실측: 한국어 42.6%·영어 12.3%
    유실). 그래서 워커가 청크를 ≤500토큰 조각으로 나눠 조각별 벡터를 보내고, 여기서
    `ChunkVec` 자식 행으로 편다. 청크 자체는 **손대지 않는다** — 재청킹이 아니므로
    MENTIONS·발췌·주제 유도가 전부 기준선 그대로다.

    구 형식(벡터 하나)도 받는다 — 자식 1개짜리로 정규화한다(하위호환).
    문서의 모든 청크가 채워지면 needs_embed=0. 벡터가 새로 생기므로 인덱스 드롭 필수."""
    base: dict[str, Any] = {"kind": "doc.embed.apply", "executed": False, "doc_id": doc_id}
    if not embeddings:
        base["error"] = "no embeddings"
        return base
    rows = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d "
                     "RETURN c.chunk_id AS id, c.owner_id AS owner", {"d": doc_id}) or []
    all_ids = {r["id"] for r in rows}
    owner_of = {r["id"]: (r.get("owner") or "") for r in rows}
    if not all_ids:
        base["error"] = "unknown doc_id"
        return base
    # 워커가 채운 벡터를 박는 자리 — 없던 벡터가 생기므로 드롭이 **필수**다(위와 동종).
    # FTS 는 안 건드리므로 임베딩이 도는 동안에도 키워드 검색이 계속 답한다.
    _drop_vector_index_if_any(store)
    applied = vecs = 0
    for cid, val in embeddings.items():
        if cid not in all_ids:
            continue
        # 하나든 여럿이든 목록으로 정규화(첫 원소가 스칼라면 단일 벡터).
        many = val if (val and isinstance(val[0], (list, tuple))) else [val]
        # ⚠ 먼저 **기존 자식을 지운다**. 안 그러면 재임베딩 때 옛 조각이 남아
        # 구·신 벡터가 한 색인에 섞인다 — 에러 없이 검색만 나빠지는 그 사고다.
        store.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $id DETACH DELETE v", {"id": cid})
        for seq, vec in enumerate(many):
            store.run(
                "MERGE (v:ChunkVec {vec_id: $vid}) SET v.chunk_id = $cid, v.doc_id = $doc, "
                "v.owner_id = $own, v.seq = $seq, v.embedding = $emb",
                {"vid": f"{cid}#{seq:03d}", "cid": cid, "doc": doc_id,
                 "own": owner_of.get(cid, ""), "seq": seq, "emb": list(vec)})
            vecs += 1
        applied += 1
    base["vectors"] = vecs
    # 전 청크가 채워졌을 때만 needs_embed 내림(부분 배치는 다음 라운드가 마저).
    if all_ids <= set(embeddings.keys()):
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
                  {"id": doc_id})
        base["cleared"] = True
    # 🔴 **워커가 마지막으로 실제 일한 시각**을 남긴다. 이게 없으면 밖에서 "백로그가
    # 줄고 있는 중"과 "워커가 죽어 영원히 그대로"를 구분할 수 없다 — 2026-08-05~07 에
    # 정확히 그 구분이 안 돼서 워커의 죽음을 38시간 몰랐다. `/health` 가 이 값을 싣고,
    # pending 이 있는데 이 시각이 오래됐으면 그 자체가 알람이다.
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_APPLIED_KEY, "v": _now_iso()})
    base.update(executed=True, embedded=applied)
    return base


#: 벡터를 만든 모델의 식별자를 적어 두는 자리(`_SchemaMeta`).
_EMBED_MODEL_KEY = "embed_model"
#: 워커가 마지막으로 `apply_embeddings` 를 성공시킨 시각(ISO). 백로그가 **줄고 있는지**
#: 를 밖에서 판정하는 유일한 신호다 — `/health` 가 이 값을 싣는다.
_EMBED_APPLIED_KEY = "embed_last_applied_at"


def embed_model_state(store) -> dict:
    """현재 색인이 **어느 모델로 만들어졌는가**, 그리고 이행 중인가.

    반환 `{"stored", "migrating", "pending", "docs", "last_applied_at"}`.
    `stored` 가 빈 문자열이면 아직 아무것도 기록되지 않은 상태다(구 설치·신규 설치).

    ⚠ **예외를 삼키고 기본값을 돌려준다.** 그 관용이 :func:`switch_embed_model` 과
    묶이면 위험하다 — 읽기가 조용히 실패하면 `stored` 가 영원히 빈 문자열이고, 그러면
    전환이 매 라운드 "바뀌었다"고 판정해 전 문서를 다시 큐에 넣는다. 그래서 실패
    사유를 버리지 않고 `error` 로 싣는다(`/health` 에 그대로 나간다)."""
    out: dict[str, Any] = {"stored": "", "migrating": False, "pending": 0,
                           "docs": 0, "last_applied_at": ""}
    try:
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                         {"k": _EMBED_MODEL_KEY}) or []
        out["stored"] = (rows[0].get("v") or "") if rows else ""
        at = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                       {"k": _EMBED_APPLIED_KEY}) or []
        out["last_applied_at"] = (at[0].get("v") or "") if at else ""
        cnt = store.run("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c") or []
        out["pending"] = int(cnt[0].get("c")) if cnt else 0
        tot = store.run("MATCH (d:_DocMeta) RETURN count(d) AS c") or []
        out["docs"] = int(tot[0].get("c")) if tot else 0
    except Exception as exc:      # noqa: BLE001 — 진단이 호출부를 죽이지 않게
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["migrating"] = bool(out["stored"]) and out["pending"] > 0
    return out


def switch_embed_model(store, model: str) -> dict:
    """임베딩 모델이 바뀌었으면 **전량 재임베딩을 건다**(멱등).

    🔴 이게 없으면 모델을 바꿔도 아무 일도 일어나지 않는다. `needs_embed` 는 문서를
    저장할 때만 켜지므로, 모델만 갈아끼우면 옛 모델로 만든 벡터가 그대로 남아 새 쿼리
    벡터와 **다른 공간에서** 비교된다 — 에러 없이 검색만 나빠지는, 이 리포가 반복해서
    당해 온 실패 양식이다. (같은 뿌리의 다른 사례: 청킹 정책을 바꿔도 `content_hash`
    가 그대로라 재청킹이 아예 일어나지 않는다.)

    **기록이 없을 때(`stored == ""`)도 바뀐 것으로 친다.** 그 벡터가 어느 모델로
    만들어졌는지 알 수 없기 때문이다 — 지금 배포가 정확히 그 경우다(옛 bge 벡터 +
    기록 없음). 모르는 것을 맞다고 가정하느니 지우고 다시 만든다. 신규 설치는 문서가
    없어 no-op 이고, 재기동은 기록이 남아 있어 두 번 지우지 않는다.

    바뀌었을 때 하는 일 셋:
      1. **옛 자식 벡터를 전부 지운다.** 남겨 두고 새것을 섞으면 그게 곧 혼재다.
         지우면 그 구간의 벡터 검색은 비지만, 비는 편이 틀린 편보다 낫다(발주자 결정:
         "안전한 방향"). FTS 는 그대로 살아 있어 검색 자체가 죽지는 않는다.
      2. 전 문서 `needs_embed = 1` — 워커가 순차로 다시 채운다.
      3. 새 모델 식별자를 기록 — 다음 기동에서 같은 값이면 아무 일도 하지 않는다(멱등).
    """
    base: dict[str, Any] = {"kind": "embed.model.switch", "executed": False, "model": model}
    if not model:
        base["error"] = "model is required"
        return base
    st = embed_model_state(store)
    base["previous"] = st["stored"]
    if st["stored"] == model:
        base.update(executed=True, changed=False)
        return base
    # 인덱스가 걸린 채로 대량 삭제해도 안전하다(실측 2,000청크) — 다만 곧이어 워커가
    # 벡터를 새로 만들 것이므로 어차피 드롭이 필요하다. 여기서 한 번에 처리한다.
    _drop_vector_index_if_any(store)
    store.run("MATCH (v:ChunkVec) DETACH DELETE v")
    store.run("MATCH (d:_DocMeta) SET d.needs_embed = 1")
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_MODEL_KEY, "v": model})
    cnt = store.run("MATCH (d:_DocMeta) RETURN count(d) AS c") or []
    base.update(executed=True, changed=True, requeued=int(cnt[0].get("c")) if cnt else 0)
    return base


def stale_docs(store, *, owner: str | None = None, limit: int = 50) -> list[str]:
    """재임베딩이 필요한 문서 id(needs_embed=1) — 배경 잡이 순회할 대상."""
    where = "d.needs_embed = 1"
    if owner:
        where += " AND d.owner_id = $o"
    rows = store.run(
        f"MATCH (d:_DocMeta) WHERE {where} RETURN d.doc_id AS id "
        f"ORDER BY d.updated_at LIMIT {max(1, min(int(limit), 500))}",
        {"o": owner} if owner else None) or []
    return [r["id"] for r in rows]


def new_doc_id() -> str:
    """비결정론 doc_id 가 필요할 때(내용 무관 신규 정체성)의 대안 — 기본 경로는
    make_doc_id(결정론)다."""
    return "d" + uuid.uuid4().hex[:16]


# ── 정본→노드 파생 (D45-V — 옵션1 결정론 + 옵션2 확인 흐름의 공용 백엔드) ────────
# 옵션1(결정론): 헤딩 구조에서 개념 후보를 기계적으로 뽑는다 — LLM 없음·오프라인·
# 멱등. 옵션2(LLM+확인): LLM(예: Claude via MCP vinc_doc_get)이 후보를 만들되,
# **반영은 같은 apply 계약**을 지나므로 확인 UI 는 후보의 출처를 가리지 않는다.
# 자동 반영은 하지 않는다 — extract 는 후보 제안이 기본이고 apply 는 명시 opt-in
# (편집이 사용자가 다듬은 개념을 조용히 덮어쓰지 않게, status='proposed' 로 표식).

_SLUG_RE = re.compile(r"[^a-z0-9가-힣]+")
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


def _concept_terms(title: str, stem: str) -> set[str]:
    """개념의 하이라이트 대상 토큰 — 제목 유의 토큰(≤6) + stem(mentions_link 규칙과
    같은 어휘). 너무 짧은 토큰(<2)은 소음이라 제외."""
    toks = {t for t in _HL_TOKEN_RE.findall((title or "").lower()) if len(t) >= 2}
    terms = set(list(toks)[:6])
    s = (stem or "").lower().strip()
    if len(s) >= 2:
        terms.add(s)
    return terms


def doc_highlights(store, doc_id: str, concept_id: str, *,
                   owner: str | None = None) -> dict:
    """doc 전문에서 ``concept_id`` 가 언급된 char 범위(Reader 밑줄용, D45-V2 2단계).

    범위는 저장값이 아니라 토큰 매칭 계산(보고서 정정 1). SEC-1: doc·concept 소유
    가드. 반환 {executed, doc_id, concept_id, ranges:[{char_start,char_end}],
    method:'tokens', terms}."""
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
        {"id": concept_id, **({"owner": owner} if owner else {})})
    if not rows:
        base["error"] = f"unknown concept: {concept_id}"
        return base
    terms = _concept_terms(rows[0].get("title") or "", rows[0].get("stem") or "")
    base.update(executed=True, ranges=highlight_ranges(got.get("text") or "", terms),
                method="tokens", terms=sorted(terms))
    return base


#: MENTIONS 가 향할 수 있는 라벨. 정본은 ``engines.mentions_link._LINK_TARGETS`` 이고
#: 여기 사본을 두는 이유는 그 모듈이 없어도 **조회는 서야** 하기 때문이다(docstore 는
#: 링커를 선택 의존으로 다룬다 — `_relink_mentions` 의 지연 임포트 참조).
#: ⚠ ``Concept`` 만 보면 **결정만 언급한 문서가 빈손이 된다.** 같은 함정이 링커의
#: MERGE 사본에서 이미 한 번 났고(그때는 링크가 통째로 0건이었다), 그 사본을 지운 것이
#: `_relink_mentions` 의 주석이다.
_MENTION_TARGETS = (("Concept", "concept_id"), ("Decision", "decision_id"))


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

    `mention_tally` 를 쓰지 않는 이유: 그것은 **전 코퍼스** 집계라, 문서 하나를
    보려고 owner 의 MENTIONS 를 전부 훑는다. 여기서는 doc 스코프로 좁힌다.

    SEC-1: 문서 소유 가드는 `get_doc` 이, 개념 소유 가드는 아래 술어가 진다. 청크
    소유는 과도기 술어(``owner OR ''`` — v4 이전 파일 파생 레거시)로 `node_context`
    와 같은 모양이다."""
    base: dict[str, Any] = {"kind": "doc.context", "executed": False, "doc_id": doc_id,
                            "concepts": [], "sections": [], "chunks": []}
    got = get_doc(store, doc_id, owner=owner)
    if not got.get("executed"):
        base["error"] = got.get("error", "unreadable doc")
        return base
    base["sections"] = doc_sections(got.get("text") or "")
    # 청크 오프셋. char_start 가 음수인 행은 오프셋을 잃은 레거시라 착지 대상이
    # 아니다(거기로 보내면 문서 맨 위가 아니라 엉뚱한 곳이 열린다) — 빼고 준다.
    try:
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d "
            "RETURN c.chunk_index AS i, c.char_start AS s ORDER BY c.chunk_index",
            {"d": doc_id}) or []
        base["chunks"] = [{"index": int(r.get("i") or 0), "char_start": int(r.get("s") or 0)}
                          for r in rows if int(r.get("s") or 0) >= 0]
    except Exception:
        pass          # 구스토어 — 목차와 개념은 그대로 유효하다(강등, 실패 아님)
    tally: dict[str, dict] = {}
    for label, pk in _MENTION_TARGETS:
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


def extract_concepts(text: str, *, max_candidates: int = 24) -> list[dict]:
    """순수 결정론 추출(옵션1) — 헤딩마다 개념 후보 1개.

    헤딩 = 마크다운 ATX(``##``/``###``) + 밑줄식(setext·RST). 후보 =
    {title, one_liner(헤딩 다음 첫 비빈 줄 ≤400자 — concepts_ingest 의 _one_liner
    관례), slug}. H1(``#``·``===`` 밑줄)은 문서 제목으로 보고 제외. 같은 slug 는 첫
    등장만(멱등). LLM·네트워크·스토어 없음 — 어디서든 같은 입력 = 같은 후보."""
    out: list[dict] = []
    seen: set[str] = set()
    lines = text.splitlines()
    heads = _headings(lines)
    for k, (_, body, title) in enumerate(heads):
        slug = _slug(title)
        if not slug or slug in seen:
            continue
        # 다음 헤딩의 제목 줄에서 멈춘다 — 안 그러면 밑줄이나 다음 제목이
        # one_liner 로 새어 들어온다.
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        one_liner = ""
        for nxt in lines[body:stop]:
            s = nxt.strip()
            if s.startswith("#"):
                break
            if s:
                one_liner = s.lstrip("*").strip()[:400]
                break
        seen.add(slug)
        out.append({"title": title, "one_liner": one_liner, "slug": slug})
        if len(out) >= max_candidates:
            break
    return out


def extract_doc(store, doc_id: str, *, owner: str, apply: bool = False,
                candidates: list[dict] | None = None) -> dict:
    """doc 의 개념 후보를 제안하고(apply=False), 승인되면 반영한다(apply=True).

    - 후보 미지정 → 결정론 추출(extract_concepts, 옵션1). 지정 → 그 목록을 반영
      (옵션2 — LLM/사용자가 다듬은 후보. [{title, one_liner?}] 만 신뢰하고 id 는
      서버가 slug 로 재파생 — 외부 id 주입 금지).
    - 반영 = Concept MERGE(concept_id = 'concept:{slug}', status='proposed',
      domain='' — 도메인 귀속은 후속 편집/UI 몫) + 이 doc 의 MENTIONS 재링크
      (새 개념이 즉시 뷰·검색 그래프 암에 합류). **기존 개념(matched)은 title/
      one_liner 를 덮어쓰지 않는다** — 사용자가 다듬은 노드 보호(읽고 스킵).
    - SEC-1: doc 소유 가드. 반환 {executed, doc_id, candidates, applied:{created,
      matched}, mentions}."""
    base: dict[str, Any] = {"kind": "doc.extract", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is None:
        base["error"] = "unknown doc_id"
        return base
    if meta.get("owner_id") != owner:
        base["error"] = "owner mismatch — 남의 문서에서 추출할 수 없다(SEC-1)"
        return base
    if candidates is None:
        got = get_doc(store, doc_id, owner=owner)
        if not got.get("executed"):
            base["error"] = got.get("error", "unreadable doc")
            return base
        cands = extract_concepts(got.get("text") or "")
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
    if not apply:
        return base
    created = matched = 0
    # v7(D72) — 문서의 내용 시점을 파생 개념에 물려준다. 개념은 그 문서에서 나왔으므로
    # 문서가 가리키는 시점이 곧 그 개념이 말하는 시점이다. **비파괴**다: 이미 값이
    # 있는 개념은 건드리지 않는다(사용자 손질 보호 — 아래 matched 규칙과 같은 태도).
    ctime = _norm_content_time(meta.get("content_time"))
    for c in cands:
        cid = f"concept:{c['slug']}"
        exists = store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.owner_id AS o, "
            "n.content_time AS ct, n.rev AS rev", {"id": cid})
        if exists:
            if exists[0].get("o") not in (owner, "", None):
                continue   # 남의 개념 — 건드리지 않는다(SEC-1)
            matched += 1
            # 내 기존 개념 — 덮어쓰기 금지. 단 **비어 있는 내용 시점만** 채운다:
            # 문서에 시점을 뒤늦게 적었을 때 이미 뽑힌 개념이 영영 축에서 빠지지 않게.
            # 이것도 **변경이므로 스탬프한다** — 안 찍으면 채운 값이 이 기기 밖으로
            # 나가지 못한다(다음 delta 가 이 노드를 안 본다).
            if ctime and not (exists[0].get("ct") or ""):
                st = _node_stamp(store, owner, int(exists[0].get("rev") or 0))
                store.run("MATCH (n:Concept) WHERE n.concept_id = $id "
                          "SET n.content_time = $ct, n.updated_at = $ua, n.rev = $rev, "
                          "n.sync_seq = $seq",
                          {"id": cid, "ct": ctime, "ua": st["updated_at"],
                           "rev": st["rev"], "seq": st["sync_seq"]})
            continue
        # 추적열(updated_at·rev·sync_seq)을 함께 찍는다 — 없으면 이 개념은 delta 에
        # 안 실려 다른 기기로 못 간다(:func:`_node_stamp` 주석의 실측).
        st = _node_stamp(store, owner)
        store.run(
            "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, n.one_liner = $ol, "
            "n.owner_id = $owner, n.status = 'proposed', n.domain = '', "
            "n.stem = $slug, n.source_file = $origin, n.created = $now, "
            "n.content_time = $ct, n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
            {"id": cid, "t": c["title"], "ol": c["one_liner"], "owner": owner,
             "slug": c["slug"], "origin": meta.get("origin") or "",
             # 시간 원천을 비워 두면 타임라인이 이 개념을 "만진 때"로만 알게 된다.
             # 벽시계가 아니라 **스탬프의 HLC** 를 쓴다: 출생 시각은 기기 간 비교
             # (`x.created > $born`)로 화해되는데 그 비교가 사전식이라, 초 단위
             # `_now_iso` 와 밀리초 HLC 가 섞이면 같은 순간이 다르게 정렬된다.
             "now": st["updated_at"], "ct": ctime,
             "ua": st["updated_at"], "rev": st["rev"], "seq": st["sync_seq"]})
        created += 1
    linked = _relink_mentions(store, doc_id, owner=owner,
                              origin=meta.get("origin") or "")
    ev = backfill_evidence(store, owner=owner, doc_id=doc_id)
    base.update(applied={"created": created, "matched": matched}, mentions=linked,
                evidence=ev)
    return base


# 근거 문장 상한 — 툴팁·패널 한 문단 분량. 더 길면 읽히지 않고 저장만 부푼다.
_EVIDENCE_MAX = 300


def _sentences(text: str) -> list[str]:
    """문장 분해 — 마침표·물음표·느낌표·줄바꿈 기준의 결정론 분해. NLP 없음."""
    import re as _re
    parts = _re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if p.strip()]


def backfill_evidence(store, *, owner: str, doc_id: str | None = None,
                      limit: int = 400) -> dict:
    """관계의 **근거 문장**을 정본에서 결정론으로 채운다(E-G6 생산자, U2).

    여태 스키마에는 `evidence`/`confidence` 열이 있는데 **채우는 곳이 하나도
    없었다** — 팩도 추출도 저작하지 않아, 관계 패널이 언제나 "근거 없음"이었다
    (실측: 방향 엣지 80개 중 보유 0). 여기서 그 공백을 메운다.

    규칙(LLM 없음·오프라인·재현 가능):
      한 청크가 두 개념을 **함께 언급**하면(MENTIONS 두 줄), 그 청크에서 두 제목이
      **같은 문장** 안에 나타나는 첫 문장을 근거로 삼는다. 같은 문장이 없으면
      근거로 치지 않는다 — 같은 문서에 있었다는 사실만으로는 관계의 근거가 아니다.

    confidence 는 그 문장의 밀도로 매긴다(가까울수록 높다): 두 제목 사이 거리가
    짧을수록 1.0 에 가깝고, 문장이 길게 벌어지면 0.5 로 수렴한다. 이미 근거가
    있는 엣지는 건드리지 않는다(사용자·상류 저작 보호).

    ``doc_id`` 를 주면 그 문서가 언급하는 개념들 사이 엣지만 훑는다(편집 직후
    증분). 없으면 owner 전체. 반환 {executed, filled, scanned}."""
    out: dict[str, Any] = {"kind": "evidence.backfill", "executed": False,
                           "filled": 0, "scanned": 0}
    try:
        # ① 후보 엣지 — 근거가 비어 있는 개념 간 관계(양 끝 owner 스코프, SEC-1).
        scope = ""
        params: dict[str, Any] = {"owner": owner}
        if doc_id:
            scope = (" AND EXISTS { MATCH (ch:Chunk)-[:MENTIONS]->(a) "
                     "WHERE ch.doc_id = $doc } ")
            params["doc"] = doc_id
        rows = store.run(
            "MATCH (a:Concept)-[r]->(b:Concept) "
            "WHERE a.owner_id = $owner AND b.owner_id = $owner "
            f"AND (r.evidence IS NULL OR r.evidence = ''){scope}"
            "RETURN a.concept_id AS aid, a.title AS at, "
            "b.concept_id AS bid, b.title AS bt, label(r) AS rel "
            f"LIMIT {int(limit)}", params) or []
    except Exception as exc:
        out["error"] = out["note"] = f"{type(exc).__name__}: {exc}"
        return out
    out["executed"] = True
    out["scanned"] = len(rows)
    for row in rows:
        at, bt = (row.get("at") or "").strip(), (row.get("bt") or "").strip()
        if len(at) < 2 or len(bt) < 2 or at.lower() == bt.lower():
            continue
        try:
            chunks = store.run(
                "MATCH (ch:Chunk)-[:MENTIONS]->(a:Concept), (ch)-[:MENTIONS]->(b:Concept) "
                "WHERE a.concept_id = $aid AND b.concept_id = $bid "
                "AND (ch.owner_id = $owner OR ch.owner_id = '') "
                "RETURN ch.text AS text ORDER BY ch.chunk_id LIMIT 6",
                {"aid": row["aid"], "bid": row["bid"], "owner": owner}) or []
        except Exception:
            continue
        found: tuple[str, float] | None = None
        for ch in chunks:
            for sent in _sentences(ch.get("text") or ""):
                low = sent.lower()
                ia, ib = low.find(at.lower()), low.find(bt.lower())
                if ia < 0 or ib < 0:
                    continue
                gap = abs(ia - ib)
                # 가까울수록 높게, 멀어지면 0.5 로 수렴(상한 0.9 — 결정론 근사이지
                # 사람이 확인한 근거가 아니라는 뜻을 수치가 스스로 말한다).
                conf = round(max(0.5, 0.9 - gap / 400.0), 3)
                found = (" ".join(sent.split())[:_EVIDENCE_MAX], conf)
                break
            if found:
                break
        if not found:
            continue
        try:
            store.run(
                f"MATCH (a:Concept)-[r:{row['rel']}]->(b:Concept) "
                "WHERE a.concept_id = $aid AND b.concept_id = $bid "
                "AND a.owner_id = $owner AND b.owner_id = $owner "
                "SET r.evidence = $ev, r.confidence = $cf",
                {"aid": row["aid"], "bid": row["bid"], "owner": owner,
                 "ev": found[0], "cf": found[1]})
            out["filled"] += 1
        except Exception:
            continue
    return out
