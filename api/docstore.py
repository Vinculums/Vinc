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


def _section_title(chunk: str) -> str:
    m = _HEADING_RE.search(chunk)
    return m.group(2).strip() if m else ""


def chunk_doc(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[dict]:
    """텍스트 → [{index, start, end, text, section_title}] (순수). 복원 계약의
    생산자 — start/end 는 반개구간 오프셋, 연결하면 원문과 바이트 동일."""
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
        "d.updated_at AS updated_at", {"id": doc_id})
    return rows[0] if rows else None


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
            title: str = "", origin: str = "",
            max_chars: int = DEFAULT_MAX_CHARS,
            embed: Callable[[list[str]], list[list[float]]] | None = None,
            link: bool = True) -> dict:
    """raw 텍스트를 doc 단위로 적재/갱신(파일 I/O 없음) — Chunk=SSoT 의 쓰기 정본.

    - 신규: doc_id 미지정 시 결정론 파생(make_doc_id). 반환 doc_id 로 이후 편집.
    - 갱신: 기존 doc_id + 변경 텍스트 → 구 청크 삭제 후 재청킹(고아 0, F3 해소).
    - 무변경: content_hash 동일 → skipped(F5 — 내용 기반 변경감지).
    - SEC-1: 기존 문서의 owner 불일치 → 거부(남의 정본을 재-put 으로 탈취 불가).
    """
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

    chunks = chunk_doc(text, max_chars=max_chars)
    vectors = embed([c["text"] for c in chunks]) if embed and chunks else None

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
        sets = ("c.doc_id = $doc, c.owner_id = $owner, c.source_file = $origin, "
                "c.section_title = $sec, c.chunk_index = $idx, c.layer = 'ssot', "
                "c.char_start = $cs, c.char_end = $ce, c.char_count = $cc, "
                "c.token_count = 0, c.text = $text")
        if vectors is not None:
            sets += ", c.embedding = $emb"
            params["emb"] = list(vectors[i])
        store.run(f"MERGE (c:Chunk {{chunk_id: $id}}) SET {sets}", params)

    store.run(
        "MERGE (d:_DocMeta {doc_id: $id}) SET d.owner_id = $owner, d.title = $title, "
        "d.origin = $origin, d.content_hash = $hash, d.chunk_count = $n, "
        "d.updated_at = $ts",
        {"id": doc_id, "owner": owner, "title": title, "origin": origin,
         "hash": content_hash, "n": len(chunks), "ts": _now_iso()})

    linked = 0
    if link:
        linked = _relink_mentions(store, doc_id, owner=owner, origin=origin)

    base.update(executed=True, skipped=False, chunks=len(chunks), replaced=removed,
                content_hash=content_hash, mentions=linked,
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
                updated_at=meta.get("updated_at"), verified=verified)
    if not verified:
        base["note"] = ("복원 검증 실패 — 레거시(오프셋 미기록) 또는 부분 기록. "
                        "re-put 으로 치유 가능")
    return base


def delete_doc(store, doc_id: str, *, owner: str | None = None) -> dict:
    """doc 단위 완전 삭제 — 청크(부착 MENTIONS 포함 DETACH)와 정체성 레지스트리.
    owner 지정 시 소유 가드(SEC-1). 멱등."""
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
    if meta is not None:
        store.run("MATCH (d:_DocMeta) WHERE d.doc_id = $id DELETE d", {"id": doc_id})
    base.update(executed=True, deleted_chunks=n, deleted_meta=meta is not None)
    return base


def list_docs(store, *, owner: str | None = None) -> dict:
    """문서 목록(_DocMeta). owner 지정 시 소유 스코프."""
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title, "
        "d.origin AS origin, d.chunk_count AS chunks, d.updated_at AS updated_at "
        "ORDER BY d.updated_at DESC", {"o": owner} if owner else None)
    return {"kind": "doc.list", "executed": True, "docs": rows or [],
            "count": len(rows or [])}


def new_doc_id() -> str:
    """비결정론 doc_id 가 필요할 때(내용 무관 신규 정체성)의 대안 — 기본 경로는
    make_doc_id(결정론)다."""
    return "d" + uuid.uuid4().hex[:16]
