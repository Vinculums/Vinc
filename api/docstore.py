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

import calendar
import hashlib
import re
import sys
import time
import uuid
from typing import Any, Callable

from engines import domain_hierarchy
from store import pkns
from store.embedded_backend import DDL_GUARD

from . import graphmd

# doc 단위 기본 청크 크기(문자). 검색 recall 용 세밀 분할은 임베딩 파이프라인
# (토큰 기준)이 담당하고, 여기는 **정본 보관 단위**라 다소 커도 무방하다.
DEFAULT_MAX_CHARS = 2000

#: `concept:doc.<doc_id>` — 문서의 개념 그래프 쪽 자리(미러). 그 `domain` 은 **사람이
#: 적은 주제**이고, 본문 매칭에서 나오는 파생 주제보다 우선한다.
#: ⚠ `api.query._DOC_CONCEPT_PREFIX` 와 같은 문자열이어야 한다 — 두 모듈이 서로를
#: 모듈 수준에서 임포트하지 않으므로 사본이 둘이고, 테스트가 바이트로 고정한다
#: (`_ALLOWED_PAIRS_HINT` 와 같은 처방).
_DOC_MIRROR_PREFIX = "concept:doc."

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _age_s(iso_ts: str) -> float | None:
    """`_now_iso()` 문자열 → 지금까지 경과 초. 비어 있거나 못 읽으면 None(모른다).

    `time.gmtime()` 으로 찍은 문자열이라 되돌릴 때도 지역시간 가정이 없는
    `calendar.timegm` 을 쓴다(`time.mktime` 은 로컬 타임존을 가정해 틀린다)."""
    if not iso_ts:
        return None
    try:
        return max(0.0, time.time() - calendar.timegm(time.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return None


def make_doc_id(owner: str, content_hash: str, title: str = "", origin: str = "") -> str:
    """결정론 doc_id — (owner, 최초 내용 해시, title, origin) 파생. 같은 문서의
    중복 최초-적재가 자연 dedupe 되고(동일 입력 = 동일 id = 멱등 MERGE), 기기 간
    오프라인 재적재도 같은 정체성으로 수렴한다. **편집 후에도 id 는 불변** —
    put_doc 에 doc_id 를 넘겨 갱신하므로 내용이 바뀌어도 정체성은 유지된다."""
    basis = f"{owner}|{title}|{origin}|{content_hash}"
    return "d" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def resolve_conventions_doc_id(store, owner: str | None) -> str:
    """이 owner 의 관례 문서가 **실제로 앉아 있는** doc_id — 읽기와 쓰기가 같이 쓴다.

    구 고정 id(`graph.md`)는 전 시스템에 행이 하나뿐이라 먼저 쓴 owner 가 점유한다.
    그 행을 owner 파생 id 로 **옮기지 않는다**: 옮김은 마이그레이션이고, 여기서 얻을
    것이 id 의 생김새뿐인데 잃을 것은 되돌릴 수 없는 쓰기다. 대신 점유자는 제자리를
    계속 쓰고, 그 밖의 전원은 owner 파생 id 를 쓴다 — 그것만으로 충돌이 닫힌다.
    근거: [[vinc.ops.risk-audit-2026-08-05 §3.4]]

    ⚠ 이 판정을 건너뛰고 `graphmd.conventions_doc_id` 를 직접 쓰면, 점유자의 기존
    관례가 안 보이고 둘째 사본이 생긴다."""
    if owner:
        legacy = _meta(store, graphmd.LEGACY_CONVENTIONS_DOC_ID)
        if legacy is not None and legacy.get("owner_id") == owner:
            return graphmd.LEGACY_CONVENTIONS_DOC_ID
    return graphmd.conventions_doc_id(owner)


def chunk_id_of(doc_id: str, index: int) -> str:
    """위치 안정 chunk_id(F4) — 내용이 아니라 (doc, 순번) 에 종속. 편집 시 같은
    자리의 청크가 같은 id 로 MERGE 되고, 줄어든 꼬리만 삭제된다."""
    return f"{doc_id}#{index:05d}"


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


#: 본문 첫머리 프런트매터가 스스로 밝히는 doc_id. **그 값은 스토어의 id 가 아니다** —
#: 선언일 뿐이고, 실제 id 는 `put_doc` 에 준 것(또는 생략 시 합성된 해시)이다.
_FM_DOC_ID_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
_FM_ID_LINE_RE = re.compile(r"^doc_id\s*:\s*(\S+)\s*$", re.M)


def _declared_doc_id(text: str) -> str:
    """본문이 선언한 doc_id(없으면 ''). **읽기만 한다 — 본문은 절대 안 고친다.**

    text 는 Chunk=SSoT 의 정본이고 `get_doc` 의 `verified` 는 이 바이트의 sha256 이다.
    여기서 한 글자만 고쳐도 그 검증이 거짓이 된다."""
    m = _FM_DOC_ID_RE.match(text or "")
    if not m:
        return ""
    line = _FM_ID_LINE_RE.search(m.group(1))
    return (line.group(1).strip() if line else "").strip("\"'")


def _doc_id_warnings(text: str, doc_id: str) -> list[str]:
    """본문이 선언한 id 와 실제로 쓰는 id 가 다르면 말한다.

    2026-08-25 에 이 침묵이 사본을 하나 만들었다. `d4ab94561078498e3` 의 본문은
    `doc_id: vinc.ops.graph-claim-decay` 라고 선언하는데 그건 그 문서의 id 가 아니다.
    그 값을 읽어 `put_doc` 에 넘겼더니 고쳐지지 않고 **제목이 같은 문서가 하나 더**
    생겼고, 신호는 성공 응답 안의 `replaced: 0` 뿐이었다.

    거부하지 않는 이유: 동기화(`api/docsync`)와 팩 가져오기가 같은 깔때기를 쓰고,
    거기 오는 문서들은 프런트매터에 정당하게 자기 이름을 적고 있다. 막으면 복제가
    선다. 침묵을 고치는 데 필요한 것은 거부가 아니라 말이다."""
    declared = _declared_doc_id(text)
    if declared and doc_id and declared != doc_id:
        return [f"the body declares doc_id '{declared}' but this write targets "
                f"'{doc_id}' — the body's declaration is not the store id, and "
                f"using it as one creates a second document"]
    return []


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
    except TimeoutError:
        # DDL 가드를 못 잡았다 = 인덱스가 **아직 걸려 있다.** 여기서 삼키고 벡터를 넣으면
        # 그게 세그폴트 경로다. 쓰기를 실패시킨다(잡이 재시도한다).
        # 근거: [[record:hub-segfault-loop-index-ddl-races-readers-2026-09-25]]
        raise
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
        # 소요 시간을 남긴다 — 로컬 실측(5.8초)이 허브 코퍼스에서도 같은지는
        # 추정이 아니라 로그로 알아야 한다. 이 호출이 도는 동안 /health 가 굶을
        # 수 있다는 것이 이 재구축의 실제 위험이므로(entrypoint.sh 의
        # sidecar_healthy 주석 참고) 소요 시간이 그대로 진단이다.
        # 근거: [[record:editing-the-conventions-document-restarts-the-hub-2026-09-09]]
        _t0 = time.monotonic()
        rebuild()
        print(f"[WARN] FTS 재빌드 완료 — {time.monotonic() - _t0:.1f}s", file=sys.stderr)
        return fn()


def put_doc(store, *, owner: str, text: str, doc_id: str | None = None,
            title: str | None = None, origin: str = "", layer: str = "ssot",
            max_chars: int = DEFAULT_MAX_CHARS,
            boundaries: list[int] | None = None,
            embed: Callable[[list[str]], list[list[float]]] | None = None,
            link: bool = True, updated_at: str | None = None,
            created_at: str | None = None, content_time: str | None = None,
            topic: str | None = None) -> dict:
    """raw 텍스트를 doc 단위로 적재/갱신(파일 I/O 없음) — Chunk=SSoT 의 쓰기 정본.

    - 신규: doc_id 미지정 시 결정론 파생(make_doc_id). 반환 doc_id 로 이후 편집.
    - 갱신: 기존 doc_id + 변경 텍스트 → 구 청크 삭제 후 재청킹(고아 0, F3 해소).
    - 무변경: content_hash 동일 → skipped(F5 — 내용 기반 변경감지).
    - SEC-1: 기존 문서의 owner 불일치 → 거부(남의 정본을 재-put 으로 탈취 불가).

    ``boundaries`` (P2): 외부 청커(chonkie)의 시작 오프셋 후보 — 무손실 정규화 후
    검색 청크로 사용(없으면 내장 경계). ``layer``: 검색 pre-filter 태그(파일 배치
    이관 시 원 레이어 보존). ``updated_at`` (P3): 동기화 적용 시 원격 HLC 를 그대로
    박기 위한 주입점(로컬 쓰기는 None → 스토어 HLC 스탬프)."""
    # 어느 소유자로 썼는지 밝힌다(fragment.merge_fragment 의 같은 주석 참조 —
    # 문서 정본도 owner 스코프이고 오배치가 조용하기는 마찬가지다).
    base: dict[str, Any] = {"kind": "doc.put", "executed": False, "owner": owner}
    if not owner or not isinstance(owner, str):
        base["error"] = "owner is required — 정본(SSoT)은 무소유로 두지 않는다(SEC-1)"
        return base
    if not isinstance(text, str):
        base["error"] = "text must be a string"
        return base
    # PK 네임스페이싱(design A) 판별자 U+001F 가 id·문자열 필드에 섞이면 안 된다
    # (strip 이 이 바이트로 owner 를 잘라내므로 물리 키로 오인된다). text 는 원문
    # 정본(D45)이라 제외 — 검열이 아니라 키 재료가 되는 필드만 본다.
    # 근거: [[vinc.plan.node-pk-namespacing]] §7
    if pkns.has_sep((owner, doc_id, title or "", origin)):
        base["error"] = "owner/doc_id/title/origin must not contain U+001F (reserved for PK namespacing)"
        return base

    content_hash = _sha(text)
    doc_id = doc_id or make_doc_id(owner, content_hash, title or "", origin)
    base["doc_id"] = doc_id

    prev = _meta(store, doc_id)
    # title 은 `content_time` 과 같은 3값이다: **키 부재(None)=기존 보존 · ""=비움 ·
    # 값=설정.** 여태 무조건 SET 이라 본문만 재전송한 호출이 제목을 빈 문자열로 덮었고,
    # 그 덮임이 아래 skip 비교("해시와 제목이 둘 다 같을 때만 skip")를 어긋내
    # **청크 전량 삭제·재작성**까지 끌고 갔다 — 무해해야 할 재전송이 파괴적 쓰기가 된다.
    # 노드 쪽은 `fragment._KEPT_FIELDS` 로 같은 수술이 끝나 있었고 문서 쪽만 남아 있었다.
    kept_title = title if title is not None else str((prev or {}).get("title") or "")
    if prev is not None:
        if prev.get("owner_id") and prev["owner_id"] != owner:
            # 거절은 그대로다(덮어쓰지도 만들지도 않는다). 문구만 소유자를 밝히지
            # 않는다 — doc_id 는 허브 전체가 한 이름공간이라 이 id 는 쓸 수 없다는
            # 것까지만 말한다(vinc_ingest 의 id_conflicts 와 같은 대접)(SEC-1).
            base["error"] = ("doc_id unavailable: this id cannot be written from this "
                             "account; choose a different doc_id")
            return base
        # `chunk_count < 0` 은 아래 ⓪ 이 찍고 크래시가 남긴 **쓰기 창 표식**이다. 그
        # 문서는 지금 읽히지 않으므로 무변경이라도 스킵하면 안 된다 — 스킵하면 표식이
        # 남은 채 `executed:true`(chunks:-1)가 나가고 그 문서는 영영 안 읽힌다.
        if prev.get("content_hash") == content_hash and prev.get("title") == kept_title \
                and int(prev.get("chunk_count") or 0) >= 0:
            base.update(executed=True, skipped=True, created=False,
                        chunks=int(prev.get("chunk_count") or 0), title=kept_title,
                        content_hash=content_hash, warnings=_doc_id_warnings(text, doc_id))
            # ⚠ 주제는 본문과 **독립**이다. 본문이 그대로라고 "이 문서를 X 밑에 둬라"를
            # 버리면 그 지시가 조용히 사라진다 — 부르는 쪽에서 가장 흔한 형태가
            # "본문은 그대로 두고 주제만 적는다" 인데 그것이 정확히 안 먹혔다
            # (실측 2026-09-06, 라이브에서 skipped=True 이고 topic=None 이 돌아왔다).
            if topic:
                base["topic"] = _set_authored_topic(store, doc_id, topic, owner=owner,
                                                    title=kept_title)
            return base

    chunks = chunk_doc(text, max_chars=max_chars, boundaries=boundaries)
    vectors = embed([c["text"] for c in chunks]) if embed and chunks else None
    stamp = updated_at or _stamp_updated_at(store)
    seq = _next_doc_seq(store, owner)

    # 벡터를 **함께 싣는 경로일 때만** 드롭한다 — 벡터 없는 삽입은 HNSW 를 안 건드리고,
    # 인덱스를 살려 두면 저장 중에도 검색이 답한다([[vinc.ops.fts-index-drift]] §3).
    if vectors is not None:
        _drop_vector_index_if_any(store)
    # ① 구 청크 삭제(재청킹으로 개수가 줄어도 고아가 남지 않게 — 부착 MENTIONS 도
    #    DETACH 로 함께 소멸). ② 신 청크 기록. ③ 메타(해시) 기록 — 이 순서가
    # 중간 크래시 자기치유의 근거다(모듈 독스트링).
    removed = 0
    if prev is not None:
        # ⓪ 창 표식. ①②를 도는 동안 메타는 아직 **옛** chunk_count 를 들고 있어서,
        #    청크가 **늘어나는** re-put 은 살아 있는 행 수가 0→새 수 를 훑으며 옛 수와
        #    정확히 한 번 일치한다 — get_doc 의 행 수 비교가 그 한 순간을 성공으로
        #    답했다(실측: 1→2청크 re-put 중 3,600자 문서가 2,000자를 깨끗한 산문으로
        #    돌려줬다). 어떤 행 수와도 안 맞는 값을 찍어 루프 **전체**를 덮는다.
        #    ③ 의 메타 MERGE 가 진짜 수로 되돌린다 — 치유는 그 한 줄이 전부다.
        # ⚠ 크래시가 이 표식을 디스크에 남기면 그 문서는 **다음 re-put 까지 계속 거절**
        #    된다. 무청크 잔해(모듈 독스트링 "쓰기 순서 = 자기치유")와 같은 갈래다:
        #    조용히 산문으로 읽히는 일이 없고, 되살리는 길도 전문 re-put 하나로 같다.
        #    그래서 위 무변경 skip 이 음수 표식 앞에서 물러선다 — 안 그러면 같은 본문
        #    re-put 이 표식을 남긴 채 성공을 답해 그 문서가 영영 안 읽힌다.
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.chunk_count = -1",
                  {"id": doc_id})
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
        {"id": doc_id, "owner": owner, "title": kept_title, "origin": origin,
         "hash": content_hash, "n": len(chunks), "ts": stamp, "seq": seq,
         "ne": needs_embed, "born": born, "ctime": ctime})
    # 재생성 = 로컬 doc 툼스톤 상쇄(낡은 삭제가 동기화에서 재전파되지 않게, P3).
    _clear_doc_tombstone(store, doc_id)

    # 주제를 **적는다**(선택). 링커보다 **먼저** 해야 미러 제목이 이번 본문 스캔의
    # 대상이 된다.
    authored = _set_authored_topic(store, doc_id, topic, owner=owner,
                                   title=kept_title) if topic else ""

    linked = 0
    if link:
        linked = _relink_mentions(store, doc_id, owner=owner, origin=origin)

    base.update(executed=True, skipped=False, created=prev is None,
                chunks=len(chunks), replaced=removed, title=kept_title,
                content_hash=content_hash, mentions=linked, updated_at=stamp,
                sync_seq=seq, needs_embed=bool(needs_embed),
                warnings=_doc_id_warnings(text, doc_id),
                note="search indexes dropped — 검색 경로가 지연 재빌드(T3b)")
    if topic:
        base["topic"] = authored
    return base


def _set_authored_topic(store, doc_id: str, topic: str, *, owner: str,
                        title: str) -> str:
    """문서의 주제를 **적는다** — `concept:doc.<doc_id>` 미러의 `domain` 에.

    파생 주제(본문 매칭 → 개념 → 그 개념의 domain)는 아무도 단언한 적 없는 값이라
    문서와 어긋날 수 있다. 실측 2026-09-06: 미러가 있는 117건 중 39건이 파생과 달랐고
    배포 런북이 `vinc/engineering/account` 로 걸려 있었다. 근거:
    [[record:the-document-subject-relation-is-inferred-not-authored-2026-09-06]]

    이미 있는 미러는 **domain 만** 갱신한다. 제목·one_liner 를 덮지 않는 것은
    `extract_doc` 과 같은 태도다(다듬어 둔 값을 쓰기가 지우면 안 된다). 없으면 만들고,
    그때만 문서 제목을 물려받는다 — 그 물림이 Ask 에서 주제 개념과 겹쳐 보이는 위험은
    [[record:risk-doc-title-collides-with-topic]] 에 적혀 있다.

    빈 문자열·공백만이면 아무것도 하지 않는다(주제를 **지우는** 것은 이 인자의 일이
    아니다 — 지우려면 미러를 지우거나 domain 을 직접 고친다)."""
    # 열에도 정규화된 값을 넣는다 — `IN_DOMAIN` 이 가리키는 Domain 이름과 글자까지
    # 같아야 열 집계와 엣지 조회가 같은 답을 준다(`fragment._merge_node` 와 같은 규약).
    dom = domain_hierarchy.normalize(topic)
    if not dom:
        return ""
    # 물리 키로 바인딩한다(`extract_doc` 과 같은 규약) — 논리 id 로 쓰면 네임스페이싱이
    # 켜진 스토어에서 남의 스코프에 걸리거나 조회에서 안 잡힌다.
    mid = pkns.key(owner, _DOC_MIRROR_PREFIX + doc_id)
    st = _node_stamp(store, owner)
    rows = store.run("MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.concept_id AS id",
                     {"id": mid}) or []
    if rows:
        store.run("MATCH (n:Concept) WHERE n.concept_id = $id SET n.domain = $dom, "
                  "n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                  {"id": mid, "dom": dom, "ua": st["updated_at"], "rev": st["rev"],
                   "seq": st["sync_seq"]})
    else:
        store.run(
            "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, n.one_liner = '', "
            "n.owner_id = $owner, n.domain = $dom, n.created = $now, "
            "n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
            {"id": mid, "t": title or doc_id, "owner": owner, "dom": dom,
             "now": st["updated_at"], "ua": st["updated_at"], "rev": st["rev"],
             "seq": st["sync_seq"]})
    _link_domain(store, mid, dom)
    return dom


def _link_domain(store, key: str, dom: str) -> None:
    """Concept 의 `IN_DOMAIN` 을 방금 쓴 `domain` 열에 맞춘다(옛 엣지는 `link_node` 가 지운다).

    `n.domain` 을 쓰는 곳은 이 엣지도 같이 옮겨야 한다. 안 그러면 `vinc_domains`(열
    집계)는 노드를 세는데 `vinc_neighbors domain:…`(엣지 조회)는 못 찾는다 — 다음
    기동 백필까지 둘이 갈린다. 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S9.

    실패는 삼킨다: 계층은 열에서 언제든 다시 파생되는 파생물이고(`scripts/
    relink_domains.py`), 여기서 죽으면 문서 쓰기가 통째로 실패한다. `fragment` 와 같은 태도다.
    ``key`` 는 **물리 키**다(`link_node` 가 PK 로 바인딩한다)."""
    try:
        domain_hierarchy.link_node(store, "Concept", key, dom)
    except Exception as exc:
        print(f"[docstore] domain hierarchy not updated ({key}): {exc}", file=sys.stderr)


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
        return merge_links(store, links, concepts, owner)
    except Exception:
        return 0


def get_doc(store, doc_id: str, *, owner: str | None = None) -> dict:
    """doc_id 의 원문을 청크 오프셋 연결로 복원 + 무손실 검증.

    verified = (연속성: b0=0 · char_start_i == char_end_{i-1}) AND
               (sha256(복원문) == _DocMeta.content_hash).
    레거시 청크(오프셋 -1)는 복원 불가로 보고한다(검색만 가능).
    살아 있는 청크 수가 메타의 `chunk_count` 와 어긋나면(쓰기 창 · 삭제 창 · 크래시
    잔해) 본문 자리를 주지 않고 `executed=False` 로 거절한다. 쓰기 창은 put_doc 이
    `chunk_count = -1` 로 표식하므로 루프 **전체**가 덮인다 — 아래 주석 참조."""
    base: dict[str, Any] = {"kind": "doc.get", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    # 남의 문서는 **없는 문서와 똑같이** 답한다(SEC-1). "owner mismatch" 는 그 id 가
    # 이 허브에 있다는 사실을 남에게 알려 주었다. 후보(near_docs)는 부르는 쪽 소유로만
    # 좁혀지므로 남의 id 가 거기로 새지 않는다.
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]]
    if meta is None or (owner is not None and meta.get("owner_id") != owner):
        return _unknown_doc(base, store, doc_id, owner)
    rows = store.run(
        "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_index AS idx, "
        "c.char_start AS cs, c.char_end AS ce, c.text AS text "
        "ORDER BY c.chunk_index", {"d": doc_id})
    rows = rows or []

    # 살아 있는 청크 행 수가 메타의 `chunk_count` 와 다르면 이 문서는 **지금 교체되는
    # 중이거나 중간에 끊긴 것**이다. put_doc 은 구 청크를 지우고 하나씩 다시 MERGE 한 뒤
    # 마지막에 메타를 갱신하고, delete_doc 은 청크를 지운 뒤 메타를 지운다. 두 창과
    # 크래시 잔해가 여기서 같은 모양으로 잡힌다 — 그래서 판정을 두 쓰기 경로가 아니라
    # **둘이 만나는 이 자리**에 둔다(한쪽에만 두면 절반만 고친다).
    # 안 하면 창 안의 읽기가 빈 본문이나 **앞부분만**을 executed:true 로 답한다(실측
    # 2026-09-06: 27청크 문서가 chunks:0 · 3,802자 문서가 1,802자). `verified` 로는 못
    # 가른다 — 오프셋 잃은 레거시 문서도 false 이고 그쪽은 행 수가 맞아 여기 안 걸린다.
    # ⚠ 기다렸다 다시 읽지 않고 **답을 거절한다**: 이 상태는 크래시가 남기면 다음 re-put
    # 까지 지속되므로(모듈 독스트링 "쓰기 순서 = 자기치유") 기다림으로는 낫지 않는다.
    # ⚠ 행 수 비교 **하나만으로는 창을 다 못 덮는다** — 청크가 늘어나는 re-put 은 메타가
    # 아직 옛 수를 들고 있어서 행 수가 그 값을 스치고 지나간다. 그 한 순간을 위해
    # put_doc 이 창 진입 때 `chunk_count = -1`(음수 = 표식)을 찍고, 그 값이 여기서
    # 어떤 행 수와도 안 맞아 루프 전체가 덮인다. delete_doc 은 표식이 필요 없다:
    # 청크 삭제가 **한 문**이라 행 수가 n→0 으로 한 번에 떨어지고, 그 0 은 메타의 n 과
    # 항상 어긋난다(중간 값이 없다).
    # 계약: [[vinc.canon.chunk-ssot]]
    expected = int(meta.get("chunk_count") or 0)
    if expected and len(rows) != expected:
        base["chunks"], base["chunk_count"] = len(rows), expected
        base["error"] = ("document is being rewritten: its chunks are mid-replacement"
                         if expected < 0 else
                         "document is incomplete: %d of %d chunks are present"
                         % (len(rows), expected))
        base["hint"] = ("A write replaces chunks one at a time and a delete removes "
                        "them before the metadata, so a read landing in either window "
                        "sees this. Read it again in a moment. If it stays this way "
                        "the last write was interrupted: re-put the full text to heal "
                        "it. This is not an empty document, and not a missing one.")
        return base

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


def _unknown_doc(base: dict, store, doc_id: str, owner: str | None) -> dict:
    """없는 doc_id 의 읽기 응답 — get_doc 과 남의 문서 거절이 **같은 꼴**을 내도록
    한 자리로 모은 것. F7: 오타 하나에 다음 수가 없던 한 줄 오류를 후보로 보강한다."""
    base["error"] = "unknown doc_id"
    cands = near_docs(store, doc_id, owner=owner)
    base["hint"] = ("doc_id must match exactly. "
                    + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                    + "Use vinc_doc_list to browse, or vinc_search to find a "
                      "document by its content.")
    if cands:
        base["candidates"] = cands
    return base


def _nothing_deleted(base: dict, store, doc_id: str, owner: str | None) -> dict:
    """지울 것이 없던 삭제의 응답 — 없는 문서와 남의 문서가 같은 꼴을 낸다."""
    base.update(executed=True, deleted_chunks=0, deleted_meta=False, found=False)
    # F7 — 여태 없는 문서를 지워도 응답이 성공과 **똑같았다**. 멱등이라 오류는
    # 아니지만, 오타로 지운 줄 알고 넘어가는 것이 실제 피해다.
    base["error"] = "unknown doc_id — nothing was deleted"
    cands = near_docs(store, doc_id, owner=owner)
    base["hint"] = ("The delete is idempotent, so this is not an error — but "
                    "nothing matched. "
                    + ("Did you mean: " + ", ".join(cands) + "? " if cands else "")
                    + "Use vinc_doc_list to check the id.")
    if cands:
        base["candidates"] = cands
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
    한다(하드삭제만으론 다음 delta 에서 부활). 동기화 apply 의 하드삭제는 False.

    ⚠ **미러 개념(`concept:doc.<doc_id>`)은 일부러 안 지운다.** 그래서 지운 문서의
    미러가 그래프에 남고 그 주제의 공유 팩마다 실린다(`documents: 0` 인 껍데기).
    아는 천장이다 — 여기서 지우려면 `query.delete_node` 급의 파괴적 삭제여야 하고
    (사용자가 그 노드에 그린 엣지와 다듬은 제목이 함께 사라지며, `_Tombstone` 으로
    다른 기기에도 전파된다) 그것은 P11c(공유 시트 제외) 범위 밖의 제품 판정이다.
    당장의 피해는 팩 쪽에서 막혀 있다: `share._docs_for_concepts` 가 부재 doc_id 를
    걸러 빈 문서를 안 싣는다(`tests/test_share.py`
    `test_a_mirror_whose_document_is_gone_ships_no_document` 가 그 상태를 고정한다)."""
    base: dict[str, Any] = {"kind": "doc.delete", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    if meta is not None and owner is not None and meta.get("owner_id") != owner:
        # 거절은 그대로, 답은 없는 문서와 같게(SEC-1) — 아무것도 지우지 않는다.
        return _nothing_deleted(base, store, doc_id, owner)
    cnt = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN count(c) AS c",
                    {"d": doc_id})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        # 인덱스를 드롭하지 않는다 — 문서 하나 지우자고 끊으면 그동안 검색이 죽는다.
        # 대신 삭제를 `_with_fts_repair` 로 감싼다: 오래 증분 유지된 FTS 색인은 존재하는
        # 행의 문서를 잃고, 그 행을 지우면 엔진이 불일치로 던진다([[vinc.ops.fts-index-drift]]).
        # ⚠ 자식 벡터(v8)를 **먼저** 지운다 — 남으면 조용히 사라지는 게 아니라 인덱스에
        # 남아 상위 k 자리를 잡아먹는다.
        # put_doc 과 달리 여기엔 창 표식이 필요 없다: 청크 삭제가 **한 문**이라 행 수가
        # n→0 으로 한 번에 떨어지고, 중간 값이 없으니 메타의 n 과 항상 어긋난다
        # (get_doc 이 그 어긋남으로 거절한다). 이 줄을 청크별 루프로 바꾸면 그 보장이
        # 깨지므로, 그때는 put_doc 처럼 표식을 찍어야 한다.
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
        _nothing_deleted(base, store, doc_id, owner)
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

    __slots__ = ("dom", "con", "cdom", "ctitle", "authored")

    def __init__(self) -> None:
        self.dom: dict[str, dict[str, int]] = {}
        self.con: dict[str, dict[str, int]] = {}
        self.cdom: dict[str, str] = {}
        self.ctitle: dict[str, str] = {}
        #: doc_id → (domain, mirror_cid, mirror_title) — 사람이 **적은** 주제.
        self.authored: dict[str, tuple[str, str, str]] = {}

    def topics(self, doc_id: str) -> list[str]:
        """걸린 주제 전부, 많은 순(동률은 이름순 — 결정론).

        ⚠ **적힌 주제가 있으면 그것이 맨 앞이다.** 나머지는 매칭이 찾아낸 것이라
        발견으로서 유효하지만 대표는 아니다. 근거·실측:
        [[record:the-document-subject-relation-is-inferred-not-authored-2026-09-06]]
        (미러 domain 이 있는 문서 117건 중 39건에서 파생 주제와 어긋났고, 그중에는
        배포 런북이 `vinc/engineering/account` 로 걸린 것도 있었다)."""
        t = self.dom.get(doc_id) or {}
        found = [k for k, _ in sorted(t.items(), key=lambda kv: (-kv[1], kv[0]))]
        auth = (self.authored.get(doc_id) or ("", "", ""))[0]
        if not auth:
            return found
        return [auth] + [k for k in found if k != auth]

    def primary(self, doc_id: str, topic: str) -> tuple[str, str]:
        """`topic` 안에서 가장 많이 언급된 개념 `(id, title)`. 없으면 `("", "")`.

        동률은 개념 id 순(결정론). `topic` 이 대표 주제인 한 후보는 반드시 하나
        이상 존재한다 — 그 주제가 뽑힌 근거가 곧 그 주제 개념의 언급이므로."""
        c = self.con.get(doc_id) or {}
        best = sorted(((cid, n) for cid, n in c.items() if self.cdom.get(cid, "") == topic),
                      key=lambda kv: (-kv[1], kv[0]))
        if best:
            return (best[0][0], self.ctitle.get(best[0][0], ""))
        # 적힌 주제로 왔는데 그 주제에 언급된 개념이 없을 수 있다(주제를 적기만 하고
        # 본문은 개념 제목을 안 담은 문서). 그때는 **그 문서 자신의 미러**가 답이다 —
        # 빈 값을 주면 트리에서 눌러도 갈 곳이 없다.
        dom, cid, ctitle = self.authored.get(doc_id) or ("", "", "")
        return (cid, ctitle) if dom and dom == topic and cid else ("", "")


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
    _load_authored_topics(store, tally, owner=owner)
    return tally


def _load_authored_topics(store, tally: MentionTally, *, owner: str | None) -> None:
    """`concept:doc.<doc_id>` 미러의 domain 을 그 문서의 **적힌 주제**로 싣는다.

    미러는 문서의 개념 그래프 쪽 자리이고 그 domain 은 사람이 고른 값이다. 파생
    주제는 본문 문자열 매칭에서 나오므로 둘은 다를 수 있고, 실제로 어긋난다. 미러가
    없거나 domain 이 비면 종전대로 파생만 쓴다(하위호환 — 이 함수는 더하기만 한다).

    실패는 강등한다: 미러 조회가 안 되는 구스토어에서도 목록은 나와야 한다."""
    # ⚠ `STARTS WITH 'concept:doc.'` 로 거르지 않는다. PK 네임스페이싱(design A)이
    # 켜지면 물리 키가 `owner논리id` 라 그 접두가 안 맞고, 접두를 합성해 넣으면
    # 이번엔 꺼진 스토어에서 안 맞는다. 주제 있는 개념 전부를 받아 파이썬에서
    # `pkns.strip`(SEP 없으면 항등) 후에 거르면 **두 모드에서 같은 코드가 돈다**.
    # 행 수는 MENTIONS 집계(문서×개념)보다 작으므로 대가가 없다.
    try:
        rows = store.run(
            "MATCH (c:Concept) "
            + ("WHERE c.owner_id = $o AND " if owner else "WHERE ")
            + "c.domain IS NOT NULL AND c.domain <> '' "
              "RETURN c.concept_id AS cid, c.title AS title, c.domain AS domain",
            {"o": owner} if owner else None) or []
    except Exception:
        return
    for r in rows:
        cid = r.get("cid") or ""
        logical = pkns.strip(cid)
        if not logical.startswith(_DOC_MIRROR_PREFIX):
            continue
        did = logical[len(_DOC_MIRROR_PREFIX):]
        if did:
            tally.authored[did] = ((r.get("domain") or "").strip(), cid,
                                   r.get("title") or "")


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
            for r in (rows or []) if not graphmd.is_conventions_doc_id(r.get("doc_id") or "")]

    total = len(docs)
    off = 0
    # 순환 임포트 회피 — query 가 이 모듈을 이미 쓴다(nav_tree). 지연 임포트로
    # 커서 규약을 **한 벌만** 유지한다(검색과 같은 코덱·같은 지문).
    from .query import _query_fingerprint, cursor_decode, cursor_encode
    # 커서에 **어느 목록의 것인가**를 싣는다(검색 커서의 qh 와 같은 방식). 여태 오프셋
    # 하나뿐이라 남의 owner·다른 detail 로 발급된 커서, 음수 오프셋(`off:-5` 를 0 으로
    # 조용히 당겼다)이 전부 통과했다. 이제 셋 다 ValueError("invalid_cursor: …").
    # 근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] S10.
    qh = _query_fingerprint("doc.list", owner, None,
                            "summary" if detail == "summary" else "full")
    if cursor:
        data = cursor_decode(cursor)        # 무효면 ValueError — 호출부가 표면화
        if data.get("qh") != qh:
            raise ValueError(
                "invalid_cursor: cursor was issued for a different query/scope")
        raw_off = data.get("off", 0)
        if not isinstance(raw_off, int) or isinstance(raw_off, bool) or raw_off < 0:
            raise ValueError("invalid_cursor: offset is not a non-negative integer")
        off = raw_off
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
        nxt = off + len(page)
        out["total_count"] = total
        out["has_more"] = nxt < total
        out["next_cursor"] = (cursor_encode({"v": 1, "off": nxt, "tc": total, "qh": qh})
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
    # 남의 문서는 없는 문서와 같게 답한다(SEC-1).
    if meta is None or (owner is not None and meta.get("owner_id") != owner):
        base["error"] = "unknown doc_id"
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
    # 드롭되는 건 **벡터뿐**이라고 정확히 적는다 — "search indexes dropped" 라고 적혀
    # 있던 동안 호출부가 FTS 까지 다시 만들려다 터졌다([[vinc.ops.fts-index-drift]] §3).
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
    """임베딩 적용 전체를 **배타로** 돈다(허브·CLI 공통 단일 지점).

    지금까지의 허브 크래시는 전부 이 적용 도중에 났다. 인덱스 드롭만이 아니라 ChunkVec
    삭제·삽입과 메타 갱신(커밋)까지 다른 커넥션의 스캔과 겹치지 않게 한다. 안의 드롭은
    같은 스레드 재진입이라 교착하지 않는다. 대가: 적용하는 동안 읽기가 기다린다.
    근거: [[record:hub-segfault-loop-index-ddl-races-readers-2026-09-25]]"""
    with DDL_GUARD.exclusive():
        return _apply_embeddings(store, doc_id, embeddings)


def _apply_embeddings(store, doc_id: str, embeddings: dict[str, Any]) -> dict:
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
    # 잔해 색인이 있는 `ChunkVec` 에 행을 넣으면 프로세스가 **세그폴트**로 죽는다(예외가
    # 아니다). 거절하면 needs_embed 가 남아 수리(다음 기동) 뒤에 워커가 다시 넣는다.
    # 근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]
    if getattr(store, "vector_index_remnant", False):
        base["error"] = "vector_index_remnant"
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
        # 이행 표식 내리기 — 백로그가 0 이 된 라운드에서. 여기(워커 apply, writer
        # 경로)가 내리는 이유: `embed_model_state` 는 리더 경로의 진단 전용이라
        # 쓰면 안 된다. 드레인 직후 크래시로 표식이 남는 창은 다음 편집 →
        # 다음 apply 라운드가 같은 분기로 치유한다(그동안 게이트가 한 번 더
        # 내려가 있을 뿐, 방향은 안전하다).
        left = store.run(
            "MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c") or []
        if not (left and int(left[0].get("c") or 0)):
            store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k DELETE m",
                      {"k": _EMBED_MIGRATING_KEY})
    # 🔴 **워커가 마지막으로 실제 일한 시각**. pending 이 있는데 이 값이 오래됐으면 그
    # 자체가 알람이다 — 진행 지표만으로는 정지를 못 본다
    # ([[record:worker-heartbeat-separates-backlog-from-death]]).
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_APPLIED_KEY, "v": _now_iso()})
    base.update(executed=True, embedded=applied)
    return base


#: 벡터를 만든 모델의 식별자를 적어 두는 자리(`_SchemaMeta`).
_EMBED_MODEL_KEY = "embed_model"
#: 워커가 마지막으로 `apply_embeddings` 를 성공시킨 시각(ISO). 백로그가 **줄고 있는지**
#: 를 밖에서 판정하는 유일한 신호다 — `/health` 가 이 값을 싣는다.
_EMBED_APPLIED_KEY = "embed_last_applied_at"
#: **모델 이행이 진행 중**이라는 표식(값 = 이행 대상 모델). `switch_embed_model` 이
#: 전량 재큐와 함께 세우고, `apply_embeddings` 가 백로그를 다 비운 라운드에 내린다.
#: 이것이 있어야 "pending 의 출처"가 갈린다 — 모델 이행(전량, 반쪽 커버리지가 순위를
#: 기울임 → 벡터 팔 차단이 발주자 결정)과 일상 문서 편집(한두 건, 그 문서만 의미
#: 검색에서 빠질 뿐 나머지 커버리지는 온전) 이 같은 needs_embed 신호를 쓰기 때문에,
#: 표식 없이는 편집 1건이 의미 검색 전체를 끈다(실측 2026-09-02: "1 docs pending"
#: 창마다 전역 FTS-only 강등).
_EMBED_MIGRATING_KEY = "embed_migration_model"


#: pending 이 있는데 이보다 오래 진행(=성공한 apply)이 없으면 정체(stall)로 본다.
#: 기본 30분(워커 기본 주기 5분, `packaging/embed_worker.py` `--interval`의 6배):
#: 콜드스타트(모델 다운로드·양자화, 수 분 걸릴 수 있다)·일시적 부하는 견디면서
#: 죽거나 멈춘 워커는 한 시간 안에 걸린다. 이 모듈은 순수 계산이라 env 를 안
#: 읽는다. 조정하려면 :func:`embed_model_state` 호출부(`api/server.py`)에서
#: `stall_after_s` 를 넘긴다(W14 범위 밖, 현재 호출부는 기본값을 쓴다).
_EMBED_STALL_DEFAULT_S = 1800.0


def embed_model_state(store, *, stall_after_s: float = _EMBED_STALL_DEFAULT_S) -> dict:
    """현재 색인이 **어느 모델로 만들어졌는가**, 그리고 이행 중인가.

    반환 `{"stored", "migrating", "migration_model", "pending", "docs",
    "last_applied_at", "age_s", "stalled"}`. `stored` 가 빈 문자열이면 아직
    아무것도 기록되지 않은 상태다(구 설치·신규 설치).

    `migrating` 은 **모델 이행 중**(전량 재큐, `_EMBED_MIGRATING_KEY` 표식 보유)
    에만 True 다 — 일상 문서 편집의 needs_embed 는 표식을 안 세우므로 여기 안
    걸린다. 검색의 벡터 팔 게이트가 이 값을 본다: 편집 1건이 의미 검색 전체를
    끄던 과잉을 이 구분이 걷어낸다(pending 자체는 그대로 보고되고, `stalled` 도
    표식과 무관하게 pending 기준이라 워커 정체 알람은 안 무뎌진다).
    ⚠ 이 배포 전에 시작된 이행은 표식이 없어 `migrating=False` 로 읽힌다 —
    라이브에 진행 중 이행이 없음을 확인하고 들어간 변경이다(2026-09-02).

    `age_s` 는 `last_applied_at`(워커가 마지막으로 청크 임베딩을 실제로 저장에
    성공한 시각, `apply_embeddings` 가 찍는다) 이후 경과 초, 한 번도 성공한 적
    없으면 None. **이 값은 store 에 영속되므로 워커 프로세스가 몇 번을 죽었다
    재기동해도 사라지지 않는다**: "떴다 죽는 프로세스는 확인 순간마다 살아
    있어서 실패 카운터가 매번 리셋된다"는 함정(허브 워치독이 2026-08-28 에 실제로
    물린 것과 같은 모양)이 여기서는 성립하지 않는다. `pid` 가 아니라 **진행**을
    재는 것도 같은 이유다: 살아 있음과 건강함은 다르다
    ([[record:worker-heartbeat-separates-backlog-from-death]]).

    `stalled` 는 `pending > 0` 이고 `age_s >= stall_after_s` 일 때 True. `age_s`
    가 None(한 번도 성공한 적 없음)이면 `stalled=False` 로 둔다: 모델 전환 직후
    콜드스타트와 "태어날 때부터 멈춤"을 이 신호 하나로는 구분 못 하기 때문이다
    (ponytail: 필요해지면 `switch_embed_model` 에 requeued_at 도 찍어 그때부터
    잰다. 지금은 오탐이 알람 신뢰를 깎는 쪽이 더 나쁘다고 판단해 보류).

    이 함수는 **진단만** 한다: 정체를 발견해도 워커를 죽이거나 재기동하지
    않는다. 멈춘 프로세스가 단일 writer 락을 쥔 채일 수 있어, 그걸 죽이는 설계는
    이 리포가 이미 여러 번 다친 자리다(`docker-entrypoint.sh` 사이드카 워치독
    주석 참조). 알람은 `/health` 로 노출되고, 끄는 판단은 사람이 한다.

    ⚠ **예외를 삼키고 기본값을 돌려준다.** 그 관용이 :func:`switch_embed_model` 과
    묶이면 위험하다 — 읽기가 조용히 실패하면 `stored` 가 영원히 빈 문자열이고, 그러면
    전환이 매 라운드 "바뀌었다"고 판정해 전 문서를 다시 큐에 넣는다. 그래서 실패
    사유를 버리지 않고 `error` 로 싣는다(`/health` 에 그대로 나간다)."""
    out: dict[str, Any] = {"stored": "", "migrating": False, "migration_model": "",
                           "pending": 0, "docs": 0, "last_applied_at": "",
                           "age_s": None, "stalled": False}
    try:
        rows = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                         {"k": _EMBED_MODEL_KEY}) or []
        out["stored"] = (rows[0].get("v") or "") if rows else ""
        mig = store.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                        {"k": _EMBED_MIGRATING_KEY}) or []
        out["migration_model"] = (mig[0].get("v") or "") if mig else ""
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
    out["migrating"] = bool(out["migration_model"]) and out["pending"] > 0
    age = _age_s(out["last_applied_at"])
    out["age_s"] = None if age is None else round(age)
    out["stalled"] = bool(age is not None and out["pending"] > 0 and age >= stall_after_s)
    # `pending` 만 보면 "곧 줄어드는 큐"로 읽힌다. 그런데 이 스토어에 벡터를 만들어
    # 넣는 워커가 **아예 붙지 않은 설치**에서는 그 수가 영원히 안 줄어든다 — 데스크탑
    # 번들은 인코더 스택을 이름으로 제외한다(`packaging/sidecar.spec` 의 excludes).
    # 그 경우 정체는 결함이 아니라 설계이고, `stalled` 알람도 age_s 가 None 이라 안
    # 뜬다. 즉 밖에서 보는 사람에게는 **아무 신호 없이 124 라는 수만** 남는다.
    # 여기서 둘을 가려 주지는 못한다(사이드카는 워커의 존재를 모른다). 대신 두 읽기를
    # 나란히 말한다 — 어느 쪽인지는 워커가 붙었는지로 사람이 가른다.
    if not out["stored"] and not out["last_applied_at"] and out["pending"] > 0:
        out["note"] = ("이 스토어는 벡터를 한 번도 받은 적이 없다. 임베딩 워커가 붙은 "
                       "설치라면 첫 실행 전이고, 안 붙은 설치(데스크탑 기본)라면 이 수는 "
                       "줄지 않는다 — 그때 검색은 키워드 팔 하나로 답한다.")
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
    # 이행 표식 — 이 pending 은 **전량 재큐**다. 검색 게이트는 이 표식이 있을 때만
    # 벡터 팔을 내린다(발주자 결정 "재임베딩 기간 FTS만" 은 이 경우를 말한 것이고,
    # 일상 편집의 needs_embed 는 이 표식을 안 세우므로 팔이 산다). 내리는 쪽은
    # `apply_embeddings`(백로그 0 이 된 라운드).
    store.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = $v",
              {"k": _EMBED_MIGRATING_KEY, "v": model})
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
        # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        {"id": pkns.key(owner, concept_id),
         **({"owner": owner} if owner else {})})
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
#: ⚠ ``Concept`` 만 보면 **결정만 언급한 문서가 빈손이 된다** — 의미층이 늘 때 이
#: 사본도 같이 는다: [[record:mention-targets-must-include-every-semantic-label]].
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
#: one_liner 상한(종전과 같다). 툴팁 한 문단 분량.
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
# 개념이 될 수 없다. 실측: 캐논형 문서에서 후보 11/11 이 전부 이런 헤딩이었고,
# 반영하니 무주제 문서 1개가 무주제 개념 11개가 됐다. 근거: [[vinc.ops.topic-assignment-eval]] §2
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
                candidates: list[dict] | None = None) -> dict:
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
    base: dict[str, Any] = {"kind": "doc.extract", "executed": False, "doc_id": doc_id}
    meta = _meta(store, doc_id)
    # 남의 문서는 없는 문서와 같게 답한다(SEC-1).
    if meta is None or meta.get("owner_id") != owner:
        base["error"] = "unknown doc_id"
        return base
    text = ""
    if candidates is None or apply:
        got = get_doc(store, doc_id, owner=owner)
        if not got.get("executed"):
            base["error"] = got.get("error", "unreadable doc")
            return base
        text = got.get("text") or ""
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
    if not apply:
        return base
    # 추출 개념은 주제를 갖고 태어난다 — 원천: 프론트매터 `domain:` → 파생 주제.
    # 둘 다 없으면 거부: 주제 없는 반영은 문서를 낫게 하지 못하고 무주제 노드만
    # 늘린다. 근거: [[vinc.ops.topic-assignment-eval]] §2
    dom = _front_domain(text) or _doc_topic(store, doc_id, owner=owner)
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
    ctime = _norm_content_time(meta.get("content_time"))
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
        # 안 실려 다른 기기로 못 간다(:func:`_node_stamp` 주석의 실측).
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
             # `_now_iso` 와 밀리초 HLC 가 섞이면 같은 순간이 다르게 정렬된다.
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

    규칙(LLM 없음·오프라인·재현 가능) — 문장 판정 2단, 정확한 쪽 우선:
      ① 한 청크가 두 개념을 **함께 언급**하면(MENTIONS 두 줄), 그 청크에서 두 제목이
        **같은 문장** 안에 verbatim 으로 나타나는 문장을 근거로 삼는다.
      ② ①이 그 문장에서 안 잡히면, 두 제목의 **유의 토큰**(각 ≤6, `engines.
        mentions_link` 와 같은 어휘 — 규칙 분기 금지)이 모두 그 문장에 있을 때
        근거로 삼는다. 첫 owner 전체 실행이 scanned 202 · filled 0 이었던 이유가
        ①뿐이어서다 — 이 코퍼스의 주장형 긴 제목은 verbatim 공기가 물리적으로
        없다. 완화는 [[decision:relax-the-evidence-producer-rule]] 로 비준됐다.
      같은 문장이 없으면 근거로 치지 않는다 — 같은 문서에 있었다는 사실만으로는
      관계의 근거가 아니다.

    confidence 는 **출처 장부를 겸한다**(비준 조건 — backfill 산은 저작분과 구별
    가능해야 한다): ①은 두 제목의 거리로 0.5~0.9(가까울수록 높다), ②는 고정 0.4.
    0.5 미만이면 토큰 완화 백필이라는 뜻이고, 저작 evidence 는 저자가 준 값(또는
    없음)을 그대로 가진다. 이미 근거가 있는 엣지는 건드리지 않는다(사용자·상류
    저작 보호).

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
        # ② 완화 판정용 제목 토큰 — mentions_link 의 어휘·상한을 그대로 재사용한다
        #    (규칙 분기 금지). 토큰셋이 같으면 아무 문장이나 잡히므로 완화는 건너뛴다.
        from engines.mentions_link import _MAX_TITLE_TOKENS, _tokens as _ml_tokens
        ta = set(_ml_tokens(at)[:_MAX_TITLE_TOKENS])
        tb = set(_ml_tokens(bt)[:_MAX_TITLE_TOKENS])
        relaxable = bool(ta) and bool(tb) and ta != tb
        found: tuple[str, float] | None = None
        for ch in chunks:
            for sent in _sentences(ch.get("text") or ""):
                low = sent.lower()
                ia, ib = low.find(at.lower()), low.find(bt.lower())
                if ia >= 0 and ib >= 0:
                    gap = abs(ia - ib)
                    # 가까울수록 높게, 멀어지면 0.5 로 수렴(상한 0.9 — 결정론 근사이지
                    # 사람이 확인한 근거가 아니라는 뜻을 수치가 스스로 말한다).
                    conf = round(max(0.5, 0.9 - gap / 400.0), 3)
                    found = (" ".join(sent.split())[:_EVIDENCE_MAX], conf)
                    break
                if relaxable:
                    stoks = set(_ml_tokens(sent))
                    if ta <= stoks and tb <= stoks:
                        # 고정 0.4 는 값이자 표식이다 — 0.5 미만 = 토큰 완화 백필.
                        # 비준 조건(저작분과 구별 가능)이 이 한 값으로 성립한다.
                        found = (" ".join(sent.split())[:_EVIDENCE_MAX], 0.4)
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
