"""공유 Domain 밖에 둔 설명. 개인·팀의 정본 문서와 기존 전환 경로를 쓴다."""
from __future__ import annotations

import hashlib

from api import docstore
from engines import domain_hierarchy
from store import pkns
from store.querybase import DOC_MIRROR_PREFIX

ORIGIN = "vinc:topic-description"
MAX_CHARS = 32768


def read(store, *, owner: str, domain: str) -> dict:
    base = {"executed": False, "status": "query_failed"}
    try:
        # 원문의 id는 전환 때 보존된다. 소유자와 미러의 현재 경로로 찾으므로
        # 공간 전환·토픽 이름 변경 뒤에도 같은 설명을 읽는다.
        rows = store.run(
            "MATCH (d:_DocMeta), (c:Concept) WHERE d.owner_id=$owner "
            "AND c.owner_id=$owner AND d.origin=$origin AND c.domain=$domain "
            "AND c.concept_id=$prefix+d.doc_id "
            "RETURN d.doc_id AS doc_id, d.chunk_count AS chunk_count LIMIT 2",
            {"owner": owner, "origin": ORIGIN, "domain": domain,
             "prefix": pkns.key(owner, DOC_MIRROR_PREFIX)})
        if rows is None or len(rows) > 1:
            return {**base, "error": "topic_description_inconsistent"}
        if not rows:
            return {"executed": True, "status": "ok", "exists": False,
                    "text": "", "content_hash": ""}
        got = docstore.get_doc(store, rows[0]["doc_id"], owner=owner)
        # 기존 정본 검증기는 청크 없는 원문을 verified로 표시하지 않는다.
        # 저장된 빈 설명은 실제 0개 청크와 빈 원문의 해시까지 대조한다.
        empty_verified = (rows[0]["chunk_count"] == 0 and got.get("chunks") == 0
                          and got.get("text") == "" and got.get("content_hash")
                          == hashlib.sha256(b"").hexdigest())
        if not got.get("executed") or not (got.get("verified") or empty_verified):
            return {**base, "error": "topic_description_unverified"}
        return {"executed": True, "status": "ok", "exists": True,
                "doc_id": got["doc_id"], "text": got["text"],
                "content_hash": got["content_hash"]}
    except Exception:
        return {**base, "error": "topic_description_read_failed"}


def put(store, *, owner: str, node: str, text: str, expected_content_hash: str) -> dict:
    """읽은 판을 검사하고 쓴다. 호출자는 기존 단일 writer 큐 안에서 실행한다."""
    from api import query

    base = {"executed": False, "kind": "topic-description"}
    domain = domain_hierarchy.parse_id(node) if isinstance(node, str) else None
    if not domain or not isinstance(owner, str) or not owner:
        return {**base, "status": "unavailable"}
    if not isinstance(text, str) or len(text) > MAX_CHARS \
            or not isinstance(expected_content_hash, str):
        return {**base, "status": "invalid_request"}
    try:
        exact = query.node(node, owner=owner, store=store)
        if not exact.get("executed"):
            return {**base, "status": "query_failed"}
        if not exact.get("found"):
            return {**base, "status": "unavailable"}
        current = read(store, owner=owner, domain=domain)
        if not current["executed"]:
            return {**base, "status": "query_failed"}
        if current["content_hash"] != expected_content_hash:
            return {**base, "status": "conflict"}
        if current["text"] == text:
            return {**current, "kind": "topic-description"}
    except Exception:
        return {**base, "status": "query_failed"}
    try:
        origin_id = docstore.doc_id_for_origin(owner, ORIGIN + ":" + domain)
        result = docstore.put_doc(
            store, owner=owner, text=text, doc_id=current.get("doc_id") or origin_id,
            title=None if current["exists"] else "Topic description: " + domain,
            origin=ORIGIN, topic=domain, link=False)
        if result.get("executed"):
            saved = read(store, owner=owner, domain=domain)
            if saved.get("executed") and saved.get("text") == text:
                return {**saved, "kind": "topic-description"}
    except Exception:
        pass
    # 저장 응답·읽어 본 원문이 확인되지 않으면 입력을 보존하고 재전송하지 않는다.
    return {**base, "status": "unconfirmed", "write_may_have_applied": True}
