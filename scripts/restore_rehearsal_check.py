# -*- coding: utf-8 -*-
"""복원 리허설 판정 — 스냅샷 사본을 **실제로 열어** 라벨별 행 수를 센다.

열어보지 않은 백업은 백업이 아니라 추측이다: `db.recovered3.bad-20260809` 는
복사가 성공했고 크기도 맞았는데 열면 죽었다(FTS 손상 — [[vinc.ops.fts-index-drift]]).
그래서 판정은 "파일이 있다"가 아니라 세 관문이다:
  ① 열린다(WAL 재생·확장 로드 포함 — 재생 불가 WAL 이 P5 를 통째로 되감았다)
  ② 노드 테이블이 있다
  ③ 행이 있다 — 임베디드 스토어는 없는 경로를 열면 **빈 스토어를 새로 만들어
    성공**하므로, 0 카운트를 통과로 읽으면 그 사고를 "성공"으로 보고하게 된다

restore-rehearsal.yml 이 부른다. 단독 실행: python scripts/restore_rehearsal_check.py <db 파일 경로>
(스토어는 단일 파일 `db` + `db.wal` — 인자는 디렉터리가 아니라 그 **파일**이다.)
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402

#: show_tables 가 어떤 이유로든 실패할 때의 폴백 — 전용 6종 + 캐리어 + 문서 + 툼스톤.
_FALLBACK_LABELS = ("Concept", "Record", "Episode", "Decision", "Domain", "Bridge",
                    "Entity", "Chunk", "_Tombstone", "_EdgeTombstone")


def _node_labels(store) -> list[str]:
    try:
        rows = store.run("CALL show_tables() RETURN *")
        names = [str(r.get("name", "")) for r in rows
                 if str(r.get("type", "")).upper() == "NODE"]
        if names:
            return sorted(n for n in names if n)
    except Exception as exc:                   # 폴백은 판정을 약화시키므로 사유를 남긴다
        print(f"show_tables unavailable ({exc}) — falling back to fixed label list",
              file=sys.stderr)
    return list(_FALLBACK_LABELS)


def rag_census(store) -> dict[str, int]:
    """문서·청크·벡터가 서로 맞는지 센다(판정이 아니라 기록 — 실패로 치지 않는다).

    RAG 코드 리뷰의 결함(RV-02·12·13·30·36)이 이미 데이터를 망가뜨렸는지 보는 수다.
    ⚠ **수만 낸다.** 이 로그는 CI 에 남으므로 문서 id·소유자를 찍지 않는다.
    엔진 판에 따라 `EXISTS {}` 가 안 될 수 있어 평범한 읽기 셋을 파이썬에서 맞춘다.
    근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]"""
    docs = store.run("MATCH (d:_DocMeta) RETURN d.doc_id AS id, d.owner_id AS owner, "
                     "d.needs_embed AS ne, d.chunk_count AS cc") or []
    chunks = store.run("MATCH (c:Chunk) RETURN c.chunk_id AS id, c.doc_id AS doc, "
                       "c.owner_id AS owner, c.chunk_index AS ix") or []
    vec = {r["id"] for r in store.run(
        "MATCH (v:ChunkVec) RETURN DISTINCT v.chunk_id AS id") or []}
    meta = {d["id"]: d for d in docs}
    by_doc: dict[str, list[dict]] = {}
    for c in chunks:
        by_doc.setdefault(c["doc"], []).append(c)
    owners_of_doc: dict[str, set] = {}
    for d in docs:
        owners_of_doc.setdefault(d["id"], set()).add(d.get("owner") or "")
    for c in chunks:
        owners_of_doc.setdefault(c["doc"], set()).add(c.get("owner") or "")
    holes = sum(1 for d in docs if int(d.get("ne") or 0) == 0
                and any(c["id"] not in vec for c in by_doc.get(d["id"], [])))
    mismatch = sum(1 for d in docs if int(d.get("cc") or 0) >= 0 and (
        len(by_doc.get(d["id"], [])) != int(d.get("cc") or 0)
        or any(int(c.get("ix") or 0) >= int(d.get("cc") or 0) for c in by_doc.get(d["id"], []))))
    return {
        "docs": len(docs), "chunks": len(chunks), "chunk_vectors": len(vec),
        "docs_embedded_with_vector_holes": holes,
        "docs_chunk_count_mismatch_or_tail": mismatch,
        "docs_stuck_in_write_window": sum(1 for d in docs if int(d.get("cc") or 0) < 0),
        "chunks_without_docmeta": sum(1 for c in chunks if c["doc"] not in meta),
        "chunks_owner_differs_from_doc": sum(
            1 for c in chunks if c["doc"] in meta
            and (c.get("owner") or "") != (meta[c["doc"]].get("owner") or "")),
        "chunk_vectors_without_chunk": len(vec - {c["id"] for c in chunks}),
        "docs_with_empty_owner": sum(1 for d in docs if not d.get("owner")),
        "doc_ids_shared_by_two_owners": sum(1 for s in owners_of_doc.values()
                                            if len(s - {""}) > 1),
        "docs_pending_embed": sum(1 for d in docs if int(d.get("ne") or 0) == 1),
    }


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print(f"usage: {sys.argv[0]} <db 파일>  (존재하는 파일이어야 한다 — "
              "없는 경로는 엔진이 빈 스토어를 새로 만들어 '성공'해 버린다)", file=sys.stderr)
        return 2
    store = open_store(load_config({"data_dir": sys.argv[1]}))
    if store is None:
        print("FAIL: store driver unavailable (ladybug 미설치?)")
        return 1
    try:
        total = 0
        for label in _node_labels(store):
            try:
                c = int(store.run(f"MATCH (n:`{label}`) RETURN count(n) AS c")[0]["c"])
            except Exception:
                continue                       # 폴백 목록의 미존재 라벨은 조용히 넘어간다
            total += c
            print(f"{label}\t{c}")
        print(f"TOTAL\t{total}")
        if total == 0:
            print("FAIL: opened but holds zero rows — 경로가 틀렸거나 속 빈 사본이다")
            return 1
        try:
            for key, value in rag_census(store).items():
                print(f"census.{key}\t{value}")
        except Exception as exc:               # 센서스는 기록일 뿐 — 판정을 막지 않는다
            print(f"census unavailable: {type(exc).__name__}: {exc}")
    finally:
        store.close()
    print("OK: the copy opens and holds data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
