# -*- coding: utf-8 -*-
"""문서 정본(SSoT) 동기화 — doc 단위 last-writer-wins (D45 P3).

Chunk 가 정본이 되며 다기기 동기화가 필요해졌다([[vinc.c.chunk-ssot]] §5-P3).
그런데 노드 동기화(api/sync)는 **fragment 노드**(Concept/Episode/…) 중심의
필드 3-way 병합이라, Chunk 를 그 레일에 억지로 태우면 (a) 재청킹으로 chunk_id
집합이 통째 바뀌고 (b) embedding 이 delta 번들(3MB 한계)을 잠식한다.

그래서 **doc 를 동기화 단위**로 삼는다:
  - 전송 = 문서 정체성(_DocMeta) + **복원 텍스트만**(embedding·청크는 안 보냄).
  - 수신 = put_doc 로 **로컬 재청킹·재임베딩**(embedding 은 기기별 재생성).
  - 충돌 = doc 단위 LWW(updated_at HLC 비교) — 필드 병합 없음(문서는 원자 단위).
  - 삭제 = _DocTombstone 전파(하드삭제만으론 상대 delta 에 실려 부활).
커서는 노드 sync 와 같은 revstamp 카운터(‘doc:{owner}’ 네임스페이스)라 한 스토어
안에서 단조가 보장된다. 이 모듈은 sync.py 를 건드리지 않는 독립 채널이다.
"""
from __future__ import annotations

from typing import Any, Callable

from . import docstore as ds
from . import revstamp


def export_docs(store, *, owner: str, since: int = 0) -> dict:
    """``owner`` 의 문서 중 sync_seq > since 인 것 + 삭제 표식을 델타로 낸다.

    문서 payload 는 복원 텍스트(get_doc)까지 실어 수신측이 재청킹하게 한다 —
    embedding·청크 오프셋은 **보내지 않는다**(수신측 재생성). 반환 커서(cursor)는
    이번 배치의 최대 sync_seq(docs·tombstones 통틀어) — 다음 pull 의 since."""
    rows = store.run(
        "MATCH (d:_DocMeta) WHERE d.owner_id = $o AND d.sync_seq > $s "
        "RETURN d.doc_id AS doc_id, d.title AS title, d.origin AS origin, "
        "d.content_hash AS content_hash, d.updated_at AS updated_at, "
        # v7(D72) — 시간축 두 열도 함께 나른다. 안 실으면 수신 기기가 자기 시각으로
        # created_at 을 새로 찍어, 같은 문서가 기기마다 다른 "만든 날"을 갖게 된다.
        "d.created_at AS created_at, d.content_time AS content_time, "
        "d.sync_seq AS sync_seq ORDER BY d.sync_seq",
        {"o": owner, "s": int(since)})
    docs = []
    cursor = int(since)
    for r in rows or []:
        got = ds.get_doc(store, r["doc_id"])
        if not got.get("executed"):
            continue
        docs.append({
            "doc_id": r["doc_id"], "title": r.get("title") or "",
            "origin": r.get("origin") or "", "content_hash": r.get("content_hash"),
            "updated_at": r.get("updated_at") or "", "text": got.get("text") or "",
            "created_at": r.get("created_at") or "",
            "content_time": r.get("content_time") or "",
        })
        cursor = max(cursor, int(r.get("sync_seq") or 0))
    trows = store.run(
        "MATCH (t:_DocTombstone) WHERE t.owner_id = $o AND t.sync_seq > $s "
        "RETURN t.doc_id AS doc_id, t.deleted_at AS deleted_at, t.sync_seq AS sync_seq "
        "ORDER BY t.sync_seq", {"o": owner, "s": int(since)})
    tombstones = []
    for r in trows or []:
        tombstones.append({"doc_id": r["doc_id"], "deleted_at": r.get("deleted_at") or ""})
        cursor = max(cursor, int(r.get("sync_seq") or 0))
    return {"kind": "docsync.export", "owner": owner, "since": int(since),
            "cursor": cursor, "docs": docs, "tombstones": tombstones}


def _local(store, doc_id: str) -> dict | None:
    rows = store.run(
        "MATCH (d:_DocMeta) WHERE d.doc_id = $id RETURN d.updated_at AS updated_at, "
        "d.content_hash AS content_hash, d.owner_id AS owner_id", {"id": doc_id})
    return rows[0] if rows else None


def import_docs(store, *, owner: str, payload: dict,
                embed: Callable[[list[str]], list[list[float]]] | None = None) -> dict:
    """원격 델타를 로컬에 적용(doc 단위 LWW). 각 인바운드 문서를 로컬과 비교:

    - 로컬 없음 → put_doc(로컬 재청킹·재임베딩, 원격 updated_at 보존).
    - 로컬 있음 → **updated_at(HLC) 비교**: 원격이 더 새면 덮어쓰고, 아니면 무시
      (동률·구버전은 로컬 유지 — 결정론). 내용 동일(content_hash)이면 no-op.
    - SEC-1: 인바운드 owner 는 호출 owner 로 고정(payload 를 신뢰하지 않는다).
    삭제 표식은 로컬 문서가 더 새로 쓰이지 않았을 때만 적용(재생성이 삭제를 이긴다).
    """
    applied = updated = skipped = deleted = 0
    for d in payload.get("docs", []) or []:
        doc_id = d.get("doc_id")
        if not doc_id:
            continue
        remote_ua = d.get("updated_at") or ""
        cur = _local(store, doc_id)
        if cur is not None:
            if cur.get("owner_id") and cur["owner_id"] != owner:
                skipped += 1
                continue
            if cur.get("content_hash") == d.get("content_hash"):
                skipped += 1
                continue
            if not _newer(remote_ua, cur.get("updated_at") or ""):
                skipped += 1          # 로컬이 같거나 더 새다 → LWW 로 로컬 유지
                continue
        # v7(D72) — 원격의 시간 두 열을 그대로 박는다. created_at 은 로컬에 없을
        # 때만 의미가 있고(put_doc 이 기존 값을 보존한다), 구 송신자는 키가 없어
        # None 이 되므로 로컬 규칙이 그대로 적용된다(하위호환).
        res = ds.put_doc(store, owner=owner, text=d.get("text") or "", doc_id=doc_id,
                         title=d.get("title") or "", origin=d.get("origin") or "",
                         embed=embed, link=True, updated_at=remote_ua or None,
                         created_at=d.get("created_at") or None,
                         content_time=d.get("content_time"))
        if res.get("executed") and not res.get("skipped"):
            if cur is None:
                applied += 1
            else:
                updated += 1
        # 원격 시각으로 HLC 전진(이후 로컬 쓰기가 그보다 뒤로 가지 않게).
        try:
            revstamp.observe_hlc(store, remote_ua)
        except Exception:
            pass
    for t in payload.get("tombstones", []) or []:
        doc_id = t.get("doc_id")
        if not doc_id:
            continue
        cur = _local(store, doc_id)
        if cur is not None and _newer(cur.get("updated_at") or "", t.get("deleted_at") or ""):
            continue                  # 로컬 재생성이 원격 삭제보다 새다 → 삭제 무시
        r = ds.delete_doc(store, doc_id, owner=owner, tombstone=False)
        if r.get("executed") and (r.get("deleted_meta") or r.get("deleted_chunks")):
            deleted += 1
    return {"kind": "docsync.import", "owner": owner, "applied": applied,
            "updated": updated, "skipped": skipped, "deleted": deleted}


def _newer(a: str, b: str) -> bool:
    """a 가 b 보다 새로운 HLC 인가(문자열 정렬 = HLC 단조 정렬, 빈 값은 최과거)."""
    return (a or "") > (b or "")
