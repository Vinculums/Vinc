"""명시적으로 선택한 문서 미러의 원문 근거를 제한된 읽기 전용 조회로 검토한다.

열린 스토어를 쓰며 provision·복구·재연결·운영 DB 열기를 수행하지 않는다.
의미적 고립 여부는 호출자의 선택이고 이 모듈은 집계하지 않는다.
MENTIONS 근거는 의미적 연결 성분 수의 변화를 뜻하지 않는다. 원문은 반환하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from engines.mentions_link import MENTIONS_RULE, compute_links
from store import pkns

MAX_MIRRORS = 32
MAX_CHUNKS = 256


def review_mirrors(store, *, owner: str, isolated_mirror_ids: list[str],
                   max_chunks: int = MAX_CHUNKS) -> dict:
    """선택한 고립 미러의 근거를 반환한다. 수리는 수행하지 않는다.

    MENTIONS 누락은 현재 결정론 규칙과의 차이이며 의미적 링크 추가 승인이 아니다.
    현재 MENTIONS에는 추출 스팬이 없으므로 별도 승인된 수리 전 원문 검토가 필요하다.
    다른 소유자의 객체와 접근 불가능한 객체는 구별하지 않는다.
    """
    if not owner or not isinstance(owner, str):
        raise ValueError("explicit owner required")
    if not 1 <= max_chunks <= MAX_CHUNKS:
        raise ValueError("max_chunks must be between 1 and 256")
    if not 1 <= len(isolated_mirror_ids) <= MAX_MIRRORS:
        raise ValueError("select between 1 and 32 mirrors")
    if len(set(isolated_mirror_ids)) != len(isolated_mirror_ids):
        raise ValueError("duplicate mirror IDs")
    for mid in isolated_mirror_ids:
        if not isinstance(mid, str) or not mid.startswith("concept:doc.") or pkns.SEP in mid:
            raise ValueError("logical concept:doc.<doc_id> IDs required")
        if not mid[len("concept:doc."):]:
            raise ValueError("empty source document ID")
    findings = [_review_one(store, owner, mid, max_chunks) for mid in isolated_mirror_ids]
    return {"kind": "mirror.provenance.review", "read_only": True,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rule": MENTIONS_RULE, "selection": "caller-selected isolated mirrors",
            "semantic_connectivity": "not measured; no census or component inference",
            "extraction_span_evidence": "unavailable in current MENTIONS schema",
            "repair_authorized": False, "findings": findings}


def _review_one(store, owner, mid, cap):
    params = {"id": pkns.key(owner, mid), "owner": owner}
    mirror_query = ("MATCH (n:Concept) WHERE n.concept_id = $id AND n.owner_id = $owner "
                    "RETURN n.concept_id AS concept_id, n.title AS title, "
                    "n.stem AS stem, n.source_file AS source_file")
    mirrors = store.run(mirror_query, params) or []
    base = {"mirror_id": mid, "doc_id": mid[len("concept:doc."):],
            "classification": "incomplete_or_unverified_evidence"}
    if not mirrors:
        return dict(base, reason="mirror unavailable in owner scope")
    doc_params = {"doc": base["doc_id"], "owner": owner}
    meta_query = ("MATCH (d:_DocMeta) WHERE d.doc_id = $doc AND d.owner_id = $owner "
                  "RETURN d.content_hash AS content_hash, d.chunk_count AS chunk_count, "
                  "d.updated_at AS updated_at")
    metas = store.run(meta_query, doc_params) or []
    if not metas:
        return dict(base, classification="missing_source_document",
                    reason="source document unavailable in owner scope; no cross-owner existence inference")
    meta = metas[0]
    base["source"] = dict(meta)
    chunks = store.run(
        "MATCH (c:Chunk) WHERE c.doc_id = $doc AND c.owner_id = $owner "
        "RETURN c.chunk_id AS chunk_id, c.chunk_index AS idx, c.char_start AS cs, "
        "c.char_end AS ce, c.text AS text, c.source_file AS source_file "
        "ORDER BY c.chunk_index LIMIT " + str(cap + 1), doc_params) or []
    base["observed_chunks"] = len(chunks)
    if len(chunks) > cap:
        return dict(base, reason="chunk bound exceeded; no partial-body conclusion")
    if meta["chunk_count"] is None or int(meta["chunk_count"]) != len(chunks):
        return dict(base, reason="chunk count mismatch or in-progress write")
    end, contiguous = 0, bool(chunks)
    for i, chunk in enumerate(chunks):
        start, stop = chunk.get("cs"), chunk.get("ce")
        if (chunk.get("idx") != i or start != end or stop is None
                or stop < (start if start is not None else 0)
                or start is None or stop - start != len(chunk.get("text") or "")):
            contiguous = False
        end = stop
    digest = sha256("".join(c.get("text") or "" for c in chunks).encode("utf-8")).hexdigest()
    base["observed_content_hash"] = digest
    if not contiguous or digest != meta.get("content_hash"):
        return dict(base, reason="body offsets or canonical SHA256 unverified")
    expected = compute_links(chunks, mirrors)
    actual = store.run(
        "MATCH (c:Chunk)-[r:MENTIONS]->(n:Concept) WHERE c.doc_id = $doc "
        "AND c.owner_id = $owner AND n.owner_id = $owner AND n.concept_id = $id "
        "RETURN c.chunk_id AS chunk_id, r.method AS method LIMIT " + str(cap + 1),
        {**doc_params, **params}) or []
    # 검토 도중 원문이나 미러가 바뀌면 결론을 내리지 않는다.
    # 제한된 낙관적 재검사이며 원자적 스냅샷·내구성 증거를 뜻하지 않는다.
    if ((store.run(meta_query, doc_params) or []) != metas
            or (store.run(mirror_query, params) or []) != mirrors):
        return dict(base, reason="source or mirror changed during review")
    if len(actual) > cap:
        return dict(base, reason="MENTIONS bound exceeded")
    actual_pairs = {(pkns.strip(r["chunk_id"]), r.get("method")) for r in actual}
    expected_pairs = {(pkns.strip(r["chunk_id"]), r["method"]) for r in expected}
    missing = [{"chunk_id": pkns.strip(r["chunk_id"]), "expected_method": r["method"]}
               for r in expected if (pkns.strip(r["chunk_id"]), r["method"]) not in actual_pairs]
    base.update(body_verified=True, expected_mentions=len(expected),
                observed_mentions=len(actual), missing_expected_mentions=missing)
    if missing:
        return dict(base, classification="missing_expected_mentions",
                    reason="current-rule pair/method mismatch; review evidence before repair")
    if actual_pairs - expected_pairs:
        return dict(base, reason="unexpected or stale MENTIONS needs source review; no deletion inferred")
    return dict(base, classification="normal_isolation",
                reason="verified source; no missing current-rule MENTIONS; semantic isolation alone is legitimate")
