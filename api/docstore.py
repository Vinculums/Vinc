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
import time
import uuid
from typing import Any, Callable

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

def plan_boundaries(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[int]:
    """청크 시작 오프셋 목록(b0=0, 오름차순, len 미포함). **경계 슬라이스 전제**라
    어떤 입력에도 ``"".join(text[b_i:b_{i+1}]) == text`` 가 정의상 성립한다.

    분할 선호: 문단 경계("\\n\\n" 런 직후) > 행 경계 > 하드 컷(max_chars).
    문단·행 경계 산출이 어그러져도 하드 컷이 상한을 보장한다(손실은 구조적 불가 —
    경계가 어긋나면 '못생긴 분할'이지 '깨진 원문'이 아니다)."""
    n = len(text)
    if n == 0:
        return [0]
    # 후보 경계: 문단(빈 줄 런 끝) 우선, 그다음 개행 직후.
    para = {m.end() for m in re.finditer(r"\n\s*\n", text)}
    lines = {m.end() for m in re.finditer(r"\n", text)}
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
              boundaries: list[int] | None = None) -> list[dict]:
    """텍스트 → [{index, start, end, text, section_title}] (순수). 복원 계약의
    생산자 — start/end 는 반개구간 오프셋, 연결하면 원문과 바이트 동일.

    ``boundaries`` 지정 시 외부 청커의 경계를 정규화해 사용(P2 — chonkie 통합);
    없으면 내장 문단/행/하드컷 경계(plan_boundaries)."""
    if boundaries is not None:
        bounds = normalize_boundaries(text, boundaries) if text else [0]
    else:
        bounds = plan_boundaries(text, max_chars=max_chars)
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
        "d.updated_at AS updated_at, d.needs_embed AS needs_embed", {"id": doc_id})
    return rows[0] if rows else None


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


def _drop_indexes_if_any(store) -> None:
    # 인덱스 걸린 열(text·embedding) 변이 전 drop(멱등). 재빌드는 검색 경로의
    # ensure_search_indexes 지연 보장(T3b)이 담당 — 여기서 재빌드하지 않는다.
    # 확장(vector/fts) 미가용 환경에서는 로드 자체가 실패하는데, 그런 스토어엔
    # 인덱스가 존재할 수도 없다 — **정본 쓰기를 막을 이유가 아니다**(SSoT 는
    # 검색 확장 없이도 성립해야 한다). 삼켜지는 건 확장 로드 실패뿐, 인덱스가
    # 실재하면 로드는 성공하고 DROP 이 정상 수행된다.
    if hasattr(store, "drop_search_indexes"):
        try:
            store.drop_search_indexes()
        except Exception:
            pass


def put_doc(store, *, owner: str, text: str, doc_id: str | None = None,
            title: str = "", origin: str = "", layer: str = "ssot",
            max_chars: int = DEFAULT_MAX_CHARS,
            boundaries: list[int] | None = None,
            embed: Callable[[list[str]], list[list[float]]] | None = None,
            link: bool = True, updated_at: str | None = None) -> dict:
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

    _drop_indexes_if_any(store)
    # ① 구 청크 삭제(재청킹으로 개수가 줄어도 고아가 남지 않게 — 부착 MENTIONS 도
    #    DETACH 로 함께 소멸). ② 신 청크 기록. ③ 메타(해시) 기록 — 이 순서가
    # 중간 크래시 자기치유의 근거다(모듈 독스트링).
    removed = 0
    if prev is not None:
        cnt = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN count(c) AS c",
                        {"d": doc_id})
        removed = int(cnt[0].get("c")) if cnt else 0
        store.run("MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc_id})

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
    store.run(
        "MERGE (d:_DocMeta {doc_id: $id}) SET d.owner_id = $owner, d.title = $title, "
        "d.origin = $origin, d.content_hash = $hash, d.chunk_count = $n, "
        "d.updated_at = $ts, d.sync_seq = $seq, d.needs_embed = $ne",
        {"id": doc_id, "owner": owner, "title": title, "origin": origin,
         "hash": content_hash, "n": len(chunks), "ts": stamp, "seq": seq,
         "ne": needs_embed})
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
        from engines.mentions_link import compute_links, _fetch_concepts
        rows = store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS chunk_id, "
            "c.text AS text", {"d": doc_id})
        chunks = [{"chunk_id": r["chunk_id"], "source_file": origin,
                   "text": r.get("text") or ""} for r in rows or []]
        concepts = _fetch_concepts(store, owner=owner)
        links = compute_links(chunks, concepts)
        for lk in links:
            store.run(
                "MATCH (ch:Chunk {chunk_id: $cid}), (co:Concept {concept_id: $koid}) "
                "MERGE (ch)-[m:MENTIONS]->(co) SET m.method = $method",
                {"cid": lk["chunk_id"], "koid": lk["concept_id"],
                 "method": lk["method"]})
        return len(links)
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
        base["error"] = "unknown doc_id"
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
                needs_embed=bool(int(meta.get("needs_embed") or 0)))
    if not verified:
        base["note"] = ("복원 검증 실패 — 레거시(오프셋 미기록) 또는 부분 기록. "
                        "re-put 으로 치유 가능")
    return base


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
        _drop_indexes_if_any(store)
        store.run("MATCH (c:Chunk) WHERE c.doc_id = $d DETACH DELETE c", {"d": doc_id})
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
    return base


def list_docs(store, *, owner: str | None = None) -> dict:
    """문서 목록(_DocMeta) + **주제 파생**. owner 지정 시 소유 스코프.

    ⚠ 문서 자체에는 주제(domain) 열이 없다 — `_DocMeta` 는 정체성·변경감지 메타만
    갖는다(D45). 그래서 주제는 **문서가 언급하는 개념에서 유도**한다:
    `(Chunk)-[:MENTIONS]->(Concept)` 를 타고 그 개념들의 domain 을 세어, 가장 많이
    걸린 주제를 그 문서의 주제로 삼는다. 링크가 아직 없는 문서는 주제가 없다(빈 문자열)
    — 억지로 배정하지 않는다(D45-V "없으면 없다고 말한다").

    반환 doc 에 `topic`(대표 주제)과 `topics`(걸린 주제 전부, 많은 순)를 싣는다."""
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title, "
        "d.origin AS origin, d.chunk_count AS chunks, d.updated_at AS updated_at, "
        "d.needs_embed AS needs_embed "
        "ORDER BY d.updated_at DESC", {"o": owner} if owner else None)
    docs = [{**r, "needs_embed": bool(int(r.get("needs_embed") or 0))} for r in (rows or [])]

    # 주제 파생 — 한 번의 집계 쿼리로 전 문서를 덮는다(문서마다 조회하지 않는다).
    tally: dict[str, dict[str, int]] = {}
    # ⚠ try 는 **쿼리에만** 건다. 집계 루프까지 감싸면 프로그래밍 오류가 "구스토어
    # 강등"으로 위장돼 조용히 사라진다(실제로 그렇게 한 번 당했다 — 아래 주석 참조).
    try:
        mrows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) "
            + ("WHERE c.owner_id = $o AND (ch.owner_id = $o OR ch.owner_id = '') " if owner else "")
            + "RETURN ch.doc_id AS doc_id, c.domain AS domain, count(*) AS n",
            {"o": owner} if owner else None) or []
    except Exception:
        mrows = []   # MENTIONS 미구축·구스토어 — 주제 없이 목록만(강등, 실패 아님)
    for r in mrows:
        dom = (r.get("domain") or "").strip()
        did = r.get("doc_id") or ""
        if not dom or not did:
            continue
        # ⚠ `tally.setdefault(d, {})[k] = tally[d].get(k, 0) + n` 로 쓰지 말 것:
        # 파이썬은 대입문의 **오른쪽을 먼저** 평가하므로 첫 등장에서 KeyError 가 난다.
        bucket = tally.setdefault(did, {})
        bucket[dom] = bucket.get(dom, 0) + int(r.get("n") or 0)

    for d in docs:
        t = tally.get(d["doc_id"]) or {}
        # 많은 순, 동률이면 이름순(결정론 — 같은 데이터는 늘 같은 대표 주제).
        ordered = [k for k, _ in sorted(t.items(), key=lambda kv: (-kv[1], kv[0]))]
        d["topics"] = ordered
        d["topic"] = ordered[0] if ordered else ""
    return {"kind": "doc.list", "executed": True, "docs": docs, "count": len(docs)}


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
    _drop_indexes_if_any(store)
    for r, v in zip(rows, vectors):
        store.run("MATCH (c:Chunk) WHERE c.chunk_id = $id SET c.embedding = $emb",
                  {"id": r["id"], "emb": list(v)})
    store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
              {"id": doc_id})
    base.update(executed=True, embedded=len(rows),
                note="search indexes dropped — 검색 경로가 지연 재빌드")
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


def apply_embeddings(store, doc_id: str, embeddings: dict[str, list[float]]) -> dict:
    """워커가 계산한 청크 임베딩을 저장한다(chunk_id 키 — 순서 무관, 편집 재정렬에
    강건). 문서의 모든 청크가 임베딩되면 needs_embed=0. 인덱스 열 변이라 drop →
    SET → 검색 경로 지연 재빌드. 벡터는 워커가 만들었을 뿐 원문은 스토어에만 있다."""
    base: dict[str, Any] = {"kind": "doc.embed.apply", "executed": False, "doc_id": doc_id}
    if not embeddings:
        base["error"] = "no embeddings"
        return base
    ids = store.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_id AS id",
                    {"d": doc_id}) or []
    all_ids = {r["id"] for r in ids}
    if not all_ids:
        base["error"] = "unknown doc_id"
        return base
    _drop_indexes_if_any(store)
    applied = 0
    for cid, vec in embeddings.items():
        if cid not in all_ids:
            continue
        store.run("MATCH (c:Chunk) WHERE c.chunk_id = $id SET c.embedding = $emb",
                  {"id": cid, "emb": list(vec)})
        applied += 1
    # 전 청크가 채워졌을 때만 needs_embed 내림(부분 배치는 다음 라운드가 마저).
    if all_ids <= set(embeddings.keys()):
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id SET d.needs_embed = 0",
                  {"id": doc_id})
        base["cleared"] = True
    base.update(executed=True, embedded=applied)
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


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.strip().lower()).strip("-")[:60]


def extract_concepts(text: str, *, max_candidates: int = 24) -> list[dict]:
    """순수 결정론 추출(옵션1) — 마크다운 헤딩(##/###)마다 개념 후보 1개.

    후보 = {title, one_liner(헤딩 다음 첫 비빈 줄 ≤400자 — concepts_ingest 의
    _one_liner 관례), slug}. H1 은 문서 제목으로 보고 제외. 같은 slug 는 첫
    등장만(멱등). LLM·네트워크·스토어 없음 — 어디서든 같은 입력 = 같은 후보."""
    out: list[dict] = []
    seen: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{2,3})\s+(.+)$", line.strip())
        if not m:
            continue
        title = m.group(2).strip().lstrip("*").strip()
        slug = _slug(title)
        if not slug or slug in seen:
            continue
        one_liner = ""
        for nxt in lines[i + 1:]:
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
    for c in cands:
        cid = f"concept:{c['slug']}"
        exists = store.run(
            "MATCH (n:Concept) WHERE n.concept_id = $id RETURN n.owner_id AS o",
            {"id": cid})
        if exists:
            if exists[0].get("o") not in (owner, "", None):
                continue   # 남의 개념 — 건드리지 않는다(SEC-1)
            matched += 1
            continue       # 내 기존 개념 — 덮어쓰기 금지(사용자 손질 보호)
        store.run(
            "MERGE (n:Concept {concept_id: $id}) SET n.title = $t, n.one_liner = $ol, "
            "n.owner_id = $owner, n.status = 'proposed', n.domain = '', "
            "n.stem = $slug, n.source_file = $origin, n.created = $now",
            {"id": cid, "t": c["title"], "ol": c["one_liner"], "owner": owner,
             "slug": c["slug"], "origin": meta.get("origin") or "",
             # 시간 원천을 비워 두면 타임라인이 이 개념을 "만진 때"로만 알게 된다.
             "now": _now_iso()})
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
