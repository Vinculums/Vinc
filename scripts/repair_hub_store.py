#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 스토어 **사본**의 데이터 수리 셋 — 소프트 재큐 · 고아 벡터 · 멈춘 쓰기 창.

RAG 코드 리뷰 센서스(restore-rehearsal, `restore_rehearsal_check.rag_census`)가 허브에서
센 것을 고친다. 그것을 만든 코드 결함은 이미 고쳐 배포됐고 이것은 **남은 데이터**만 만진다.
근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]

  requeue  needs_embed=0 인 문서 전부를 1로. 저장 모델 기록·이행 표식·벡터는 안 건드린다
           — 옛 벡터가 새 벡터로 바뀔 때까지 계속 답하고, 벡터 팔 게이트
           (`docstore.embed_model_state` 의 migrating: 이행 표식이 있고 pending 이 문턱을
           넘을 때만 참)가 안 걸린다. 표식이 **이미** 있으면 이 재큐가 팔을 끄므로 거절한다.
  orphans  Chunk 가 없는 ChunkVec 을 소유자별로 지운다. 벡터 표에는 FTS 가 없고, 스토어의
           삭제(put_doc·delete_doc)와 같이 HNSW 를 건 채 지운다 — 잔해만 위험하므로 기동
           수리(`heal_vector_index`)를 먼저 돌리고 `_remnant_refusal` 을 본다.
  stuck    chunk_count=-1 로 남은 문서 중, 살아 있는 청크(0..n-1 · 한 소유자 = 메타 소유자 ·
           오프셋 연속)가 **메타가 기록한 본문 해시를 그대로 재현하는 것만** 마무리한다
           (chunk_count=n · needs_embed=1). 해시가 다르면 완성된 새 본문인지 새 본문의 앞부분
           인지 청크 행으로는 못 가른다 — 소유자 re-put 으로 남긴다(re-put 은 둘 다 고친다).
           근거: [[record:a-stuck-write-window-is-finalized-only-when-its-chunks-rebuild-the-recorded-hash-2026-09-30]]

키는 **저장된 그대로(raw)** 읽는다. `VINC_PK_NAMESPACED` 가 켜져 있으면 기본 읽기가 물리 키를
벗겨 내서 지울 `vec_id` 가 저장값과 달라지고, 센서스도 리허설(플래그 없이 돈다)과 다른 수를
낸다. 청크·벡터 대조는 (소유자, 논리 chunk_id) 로 해서 두 키 모양을 같이 받는다.

원본은 읽기만 한다: --data(+ .wal)를 --out 으로 복사하고 --out 을 고친다. 판정이 하나라도
어긋나면 --out 을 지우고 1로 끝난다(반쯤 고친 파일은 올라가지 않는다).

⚠ 출력은 **수만** 낸다. CI 로그는 공유되므로 문서 id·제목·소유자를 찍지 않는다.

    python -X utf8 scripts/repair_hub_store.py --data work/db.x --out work/db.x.repaired
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore                                  # noqa: E402
from store import pkns                                    # noqa: E402

from rebuild_store_native import _files, _open            # noqa: E402
from restore_rehearsal_check import rag_census            # noqa: E402

REPAIRS = ("requeue", "orphans", "stuck")
_BATCH = 500


class _Raw:
    """물리 키 그대로 읽는 창 — 센서스를 이것으로 돌리면 플래그와 무관하게 리허설과 같은 수다."""

    def __init__(self, s):
        self._s = s

    def run(self, q, params=None):
        return self._s.run(q, params, raw=True)


def _key(owner, cid) -> tuple[str, str]:
    return (owner or "", pkns.strip(cid or ""))


def _chunks(s) -> list[dict]:
    return s.run("MATCH (c:Chunk) RETURN c.chunk_id AS cid, c.doc_id AS doc, "
                 "c.owner_id AS o", raw=True) or []


def _vectors(s) -> list[dict]:
    return s.run("MATCH (v:ChunkVec) RETURN v.vec_id AS vid, v.chunk_id AS cid, "
                 "v.owner_id AS o", raw=True) or []


def measure(s) -> dict:
    """수리 전후를 대는 수. 구멍은 needs_embed 와 **무관하게** 센다 — 센서스의 구멍은
    needs_embed=0 문서만 세므로 재큐 뒤 0 이 되지만, 빠진 벡터는 워커가 돌 때까지 그대로다."""
    chunks, vecs = _chunks(s), _vectors(s)
    has_chunk = {_key(c["o"], c["cid"]) for c in chunks}
    has_vec = {_key(v["o"], v["cid"]) for v in vecs}
    raw_chunk = {c["cid"] for c in chunks}
    docs = s.run("MATCH (d:_DocMeta) RETURN d.needs_embed AS ne, d.chunk_count AS cc",
                 raw=True) or []
    return {
        "docs": len(docs),
        "docs_needs_embed_0": sum(1 for d in docs if int(d.get("ne") or 0) == 0),
        "docs_pending_embed": sum(1 for d in docs if int(d.get("ne") or 0) == 1),
        "docs_stuck_in_write_window": sum(1 for d in docs if int(d.get("cc") or 0) < 0),
        "chunks": len(chunks),
        "vectors": len(vecs),
        "orphan_vectors": sum(1 for v in vecs if _key(v["o"], v["cid"]) not in has_chunk),
        # 청크는 있는데 키 모양(물리·논리)이 달라 검색의 조인(`p.chunk_id = node.chunk_id`,
        # 저장값 비교)이 못 닿는 벡터. 고아가 아니라서 안 지운다 — 수만 보인다.
        "vectors_unreachable_by_key_form": sum(
            1 for v in vecs if _key(v["o"], v["cid"]) in has_chunk and v["cid"] not in raw_chunk),
        "docs_with_a_chunk_lacking_a_vector": len(
            {c["doc"] for c in chunks if _key(c["o"], c["cid"]) not in has_vec}),
    }


def requeue(s) -> tuple[dict, list[str]]:
    before, st = measure(s), docstore.embed_model_state(s)
    if st.get("error"):
        return {"before": before, "refused": "embed_state_unreadable"}, [
            "requeue refused: the embed model state is unreadable"]
    if st["migration_model"]:
        # 표식이 있는 채로 전량을 올리면 pending 이 문턱을 넘어 벡터 팔이 백로그가 빌 때까지
        # 꺼진다. 표식은 이행 상태라 여기서 지우지 않는다.
        return {"before": before, "refused": "migration_marker_present"}, [
            "requeue refused: a model migration marker is present, so a full requeue "
            "would turn the vector arm off until the backlog drains"]
    s.run("MATCH (d:_DocMeta) WHERE coalesce(d.needs_embed, 0) = 0 SET d.needs_embed = 1")
    after, st2 = measure(s), docstore.embed_model_state(s)
    problems = []
    if after["docs_needs_embed_0"]:
        problems.append(f"requeue: {after['docs_needs_embed_0']} documents still have needs_embed 0")
    for k in ("vectors", "docs_with_a_chunk_lacking_a_vector"):
        if after[k] != before[k]:
            problems.append(f"requeue changed {k}: {before[k]} -> {after[k]}")
    if (st2["stored"], st2["migration_model"]) != (st["stored"], st["migration_model"]):
        problems.append("requeue changed the stored model or the migration marker")
    if st2["migrating"]:
        problems.append("requeue: the store now reports migrating, the vector arm would be off")
    return {"before": before, "after": after, "requeued": before["docs_needs_embed_0"],
            "vector_arm_gate_after": "off" if st2["migrating"] else "on"}, problems


def orphans(s) -> tuple[dict, list[str]]:
    before = measure(s)
    out: dict = {"before": before}
    if not before["orphan_vectors"]:
        out.update(after=before, deleted=0)
        return out, []
    try:
        out["vector_index"] = s.heal_vector_index(dim=s._stored_vec_dim(1024)).get("state")
    except Exception as exc:                      # noqa: BLE001 — 판정으로 보고한다
        return out, [f"orphans: vector index heal failed ({type(exc).__name__})"]
    refusal = docstore._remnant_refusal(s, chunks=False)
    if refusal:
        return out, [f"orphans refused: {refusal}"]
    has_chunk = {_key(c["o"], c["cid"]) for c in _chunks(s)}
    by_owner: dict[str, list[str]] = {}
    for v in _vectors(s):
        if _key(v["o"], v["cid"]) not in has_chunk:
            by_owner.setdefault(v["o"] or "", []).append(v["vid"])
    for owner, vids in by_owner.items():
        for i in range(0, len(vids), _BATCH):
            s.run("MATCH (v:ChunkVec) WHERE v.owner_id = $o AND v.vec_id IN $ids "
                  "DETACH DELETE v", {"o": owner, "ids": vids[i:i + _BATCH]})
    after = measure(s)
    deleted = before["vectors"] - after["vectors"]
    out.update(after=after, deleted=deleted)
    problems = []
    if after["orphan_vectors"]:
        problems.append(f"orphans: {after['orphan_vectors']} orphan vectors remain")
    if deleted != before["orphan_vectors"]:
        problems.append(f"orphans: deleted {deleted}, expected {before['orphan_vectors']}")
    if after["docs_with_a_chunk_lacking_a_vector"] != before["docs_with_a_chunk_lacking_a_vector"]:
        problems.append("orphans: deleting orphans changed the vector holes")
    return out, problems


def _intact(rows: list[dict], owner: str) -> bool:
    """0..n-1 이 빠짐없이 · 전부 메타 소유자 · 오프셋이 이어진다(get_doc 의 verified 조건)."""
    if not owner or not rows:
        return False
    end = 0
    for i, r in enumerate(rows):
        ix, cs, ce = r.get("ix"), r.get("cs"), r.get("ce")
        if None in (ix, cs, ce) or int(ix) != i or r.get("o") != owner \
                or int(cs) != end or int(ce) < int(cs):
            return False
        end = int(ce)
    return True


def stuck(s) -> tuple[dict, list[str]]:
    before = measure(s)
    metas = s.run("MATCH (d:_DocMeta) WHERE d.chunk_count < 0 RETURN d.doc_id AS id, "
                  "d.owner_id AS o, d.content_hash AS h", raw=True) or []
    done: list[str] = []
    differs = broken = 0
    for m in metas:
        rows = s.run("MATCH (c:Chunk) WHERE c.doc_id = $d RETURN c.chunk_index AS ix, "
                     "c.char_start AS cs, c.char_end AS ce, c.owner_id AS o, c.text AS t "
                     "ORDER BY c.chunk_index", {"d": m["id"]}, raw=True) or []
        if not _intact(rows, m.get("o") or ""):
            broken += 1
        elif docstore._sha("".join(r.get("t") or "" for r in rows)) != m.get("h"):
            differs += 1
        else:
            s.run("MATCH (d:_DocMeta) WHERE d.doc_id = $d "
                  "SET d.chunk_count = $n, d.needs_embed = 1", {"d": m["id"], "n": len(rows)})
            done.append(m["id"])
    # 마무리한 것은 스토어 자신의 읽기로 확인한다 — 본문을 돌려주고 검증까지 통과해야 한다.
    unreadable = sum(1 for d in done if not docstore.get_doc(s, d).get("verified"))
    after = measure(s)
    reput = differs + broken
    out = {"before": before, "after": after, "finalized": len(done),
           "needs_owner_reput": reput, "reput_text_differs_from_meta": differs,
           "reput_chunks_not_intact": broken}
    problems = []
    if after["docs_stuck_in_write_window"] != reput:
        problems.append(f"stuck: {after['docs_stuck_in_write_window']} stuck after, "
                        f"expected {reput}")
    if unreadable:
        problems.append(f"stuck: {unreadable} finalized documents do not read back verified")
    return out, problems


_RUN = {"requeue": requeue, "orphans": orphans, "stuck": stuck}


def repair(src: str, dst: str, repairs: list[str]) -> dict:
    # 없는 경로를 열면 엔진이 빈 스토어를 새로 만들어 '성공'한다 — 파일이어야 한다.
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    if _files(dst):
        raise FileExistsError(f"--out 은 없는 경로여야 한다: {dst}")
    t0 = time.monotonic()
    for f in _files(src):
        shutil.copy2(f, dst + f[len(src):])
    report: dict = {"repairs": repairs}
    problems: list[str] = []
    s = _open(dst)
    try:
        report["census_before"] = rag_census(_Raw(s))
        for name in repairs:
            t = time.monotonic()
            try:
                out, probs = _RUN[name](s)
            except Exception as exc:              # noqa: BLE001 — 판정으로 보고한다
                # 문구는 싣지 않는다 — 쓰기의 엔진 오류는 행 값(id)을 담을 수 있다.
                out, probs = {}, [f"{name} failed ({type(exc).__name__})"]
            out["seconds"] = round(time.monotonic() - t, 1)
            report[name] = out
            problems += probs
        report["census_after"] = rag_census(_Raw(s))
        s.run("CHECKPOINT")
    finally:
        s.close()
    # 올리는 것은 본 파일 하나다(hub-data-repair.yml) — 체크포인트가 WAL 을 비웠어야 한다.
    wal = dst + ".wal"
    if os.path.exists(wal) and os.path.getsize(wal) > 0:
        problems.append("the repaired store kept a non-empty WAL after checkpoint")
    report["repair_seconds"] = round(time.monotonic() - t0, 1)
    report["problems"] = problems
    report["ok"] = not problems
    return report


def _parse(arg: str) -> list[str]:
    asked = {a.strip() for a in arg.split(",") if a.strip()}
    unknown = asked - set(REPAIRS)
    if unknown or not asked:
        raise SystemExit(f"--repairs 는 {','.join(REPAIRS)} 중에서 고른다: {sorted(unknown)}")
    return [r for r in REPAIRS if r in asked]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, help="원본 DB 파일(.wal 은 옆에서 찾는다)")
    ap.add_argument("--out", required=True, help="고친 사본을 쓸 경로(없어야 한다)")
    ap.add_argument("--repairs", default=",".join(REPAIRS), help="쉼표로: " + ",".join(REPAIRS))
    a = ap.parse_args()
    repairs = _parse(a.repairs)
    existed = bool(_files(a.out))                 # 이미 있던 파일은 이 스크립트의 것이 아니다
    try:
        report = repair(a.data, a.out, repairs)
    except Exception as exc:                      # noqa: BLE001 — 판정으로 보고한다
        # 수리 밖(복사·열기·센서스)의 실패다. 쓰기가 없으니 문구에 행 값이 섞이지 않는다.
        report = {"ok": False, "problems": [
            f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"]}
    if not report["ok"] and not existed:
        for f in _files(a.out):                   # 반쯤 고친 것은 남기지 않는다
            os.remove(f)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
