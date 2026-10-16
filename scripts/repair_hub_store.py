#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 스토어 **사본**의 데이터 수리 — 키 모양 · 도장 · 재큐 되돌리기 · 소프트 재큐 · 고아 벡터 ·
멈춘 쓰기 창.

RAG 코드 리뷰 센서스(restore-rehearsal, `restore_rehearsal_check.rag_census`)가 허브에서
센 것을 고친다. 그것을 만든 코드 결함은 이미 고쳐 배포됐고 이것은 **남은 데이터**만 만진다.
근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]

도는 순서는 keyform → stamp → unrequeue → requeue → orphans → stuck 으로 고정이다(고른 것만
돈다. 키를 먼저 고쳐야 뒤 단계가 붙은 청크를 본다). 기본은 stamp,unrequeue 이고, requeue 와
unrequeue 는 서로를 되돌리므로 함께 고르면 거절한다.

  stamp     도장(encoder_runtime) 없는 ChunkVec·NodeVec 에 허브 인코더의 지금 지문을 찍는다.
            값은 `Dockerfile.hub` 의 고정 판으로 짓는다(`hub_runtime`). 벡터는 그대로다.
  unrequeue 전 청크가 같은 소유자·저장값 조인으로 벡터를 가졌고 그 벡터가 전부 지금 도장인
            문서만 needs_embed=0. 벡터가 하나라도 빠진 문서는 pending 으로 남긴다.

  keyform  청크는 물리 키(owner U+001F id)·그 벡터는 논리 키인 쌍의 청크를 논리 키로 다시
           세운다. 벡터는 이미 논리 id 라 재임베딩 없이 저장값 조인에 붙는다. 대상은 그
           모양 **하나뿐**이다: 같은 소유자의 벡터가 있고 전부 논리 키 · 키 앞머리가 소유자 ·
           논리 키가 Chunk 에 아직 없음(있으면 건너뛰고 센다). 나머지 모양은 세기만 한다.
           PK 는 SET 이 안 되므로 옛 행의 모든 열을 엔진 안에서 복사해 새 행을 만들고, 관계
           (MENTIONS)를 옮기고, 옛 행을 `_with_fts_repair` 로 지운다. `_DocMeta` 는 안 건드린다.
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

⚠ 그래서 **고아 수가 센서스와 다를 수 있다.** 센서스(`chunk_vectors_without_chunk`·벡터 구멍)는
검색 조인(`p.chunk_id = node.chunk_id`)처럼 저장값 문자열로 대조한다. 청크는 물리 키, 벡터는
논리 키로 남은 쌍은 센서스에는 고아+구멍이고 이 수리에는 고아가 아니다 — 같은 소유자의 청크가
있으므로 지우지 않는다. 그 차이는 `key_forms_*` 가 원인별로 센다. 그 청크는 검색의 의미 팔이
못 닿고, 재큐로도 안 붙는다(워커가 벗긴 논리 id 로 다시 쓴다) — 붙이는 것은 keyform 이다.
근거: [[record:the-hub-census-orphan-vectors-are-chunks-left-on-physical-keys-not-orphans-2026-09-30]]

원본은 읽기만 한다: --data(+ .wal)를 --out 으로 복사하고 --out 을 고친다. 판정이 하나라도
어긋나면 --out 을 지우고 1로 끝난다(반쯤 고친 파일은 올라가지 않는다).

⚠ 출력은 **수만** 낸다. CI 로그는 공유되므로 문서 id·제목·소유자를 찍지 않는다.

    python -X utf8 scripts/repair_hub_store.py --data work/db.x --out work/db.x.repaired
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore                                  # noqa: E402
from store import encoder_runtime, pkns                   # noqa: E402
from store.embedded_schema import node_table_specs, rel_tables_on  # noqa: E402

from rebuild_store_native import _files, _open            # noqa: E402
from restore_rehearsal_check import rag_census            # noqa: E402

REPAIRS = ("keyform", "stamp", "unrequeue", "requeue", "orphans", "stuck")
#: 워크플로 입력의 기본값과 같다(2026-09-30 재큐를 되돌리는 실행 모양).
DEFAULT_REPAIRS = ("stamp", "unrequeue")
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
                 "v.owner_id AS o, v.doc_id AS doc", raw=True) or []


def _phys(k) -> bool:
    return bool(k) and pkns.SEP in k


def key_forms(s) -> dict:
    """센서스의 `chunk_vectors_without_chunk`(저장값 비교, 소유자 무시)를 원인별로 가른다.

    `census_vectors_without_chunk` 는 센서스와 같은 수여야 하고(repair 가 대조한다), 그 수 =
    `of_which_key_form_only`(같은 소유자의 청크가 다른 키 모양으로 있다: 고아 아님, 조인만
    못 닿는다) + `of_which_no_chunk_under_owner`(진짜 고아). 거꾸로 센서스가 못 보는 고아
    (저장값이 남의 소유자 청크와 같은 벡터)는 `orphans_the_census_does_not_count` 다."""
    chunks, vecs = _chunks(s), _vectors(s)
    raw_of = {_key(c["o"], c["cid"]): c["cid"] for c in chunks}
    raw_chunk = {c["cid"] for c in chunks}
    miss = [v for v in vecs if v["cid"] not in raw_chunk]
    form = [v for v in miss if _key(v["o"], v["cid"]) in raw_of]
    shapes: dict[str, set] = {}
    for v in form:
        c_raw = raw_of[_key(v["o"], v["cid"])]
        shape = (f"chunk_{'physical' if _phys(c_raw) else 'logical'}_"
                 f"vector_{'physical' if _phys(v['cid']) else 'logical'}")
        shapes.setdefault(shape, set()).add(v["cid"])
    missing_ids, form_ids = {v["cid"] for v in miss}, {v["cid"] for v in form}
    return {
        "chunks_with_physical_key": sum(1 for c in chunks if _phys(c["cid"])),
        "vectors_with_physical_chunk_id": sum(1 for v in vecs if _phys(v["cid"])),
        "census_vectors_without_chunk": len(missing_ids),
        "of_which_key_form_only": len(form_ids),
        "of_which_no_chunk_under_owner": len(missing_ids - form_ids),
        "key_form_vectors": len(form),
        "key_form_docs": len({v["doc"] for v in form}),
        "key_form_chunk_ids_by_shape": {k: len(v) for k, v in sorted(shapes.items())},
        "orphans_the_census_does_not_count": sum(
            1 for v in vecs if v["cid"] in raw_chunk and _key(v["o"], v["cid"]) not in raw_of),
    }


def measure(s) -> dict:
    """수리 전후를 대는 수. 구멍은 needs_embed 와 **무관하게** 센다 — 센서스의 구멍은
    needs_embed=0 문서만 세므로 재큐 뒤 0 이 되지만, 빠진 벡터는 워커가 돌 때까지 그대로다."""
    chunks, vecs = _chunks(s), _vectors(s)
    has_chunk = {_key(c["o"], c["cid"]) for c in chunks}
    has_vec = {_key(v["o"], v["cid"]) for v in vecs}
    docs = s.run("MATCH (d:_DocMeta) RETURN d.needs_embed AS ne, d.chunk_count AS cc",
                 raw=True) or []
    return {
        "docs": len(docs),
        "docs_needs_embed_0": sum(1 for d in docs if int(d.get("ne") or 0) == 0),
        "docs_pending_embed": sum(1 for d in docs if int(d.get("ne") or 0) == 1),
        "docs_stuck_in_write_window": sum(1 for d in docs if int(d.get("cc") or 0) < 0),
        "chunks": len(chunks),
        "vectors": len(vecs),
        # 수리의 대상: 같은 소유자 밑에 (키 모양과 무관하게) 청크가 없는 벡터 행.
        "orphan_vectors": sum(1 for v in vecs if _key(v["o"], v["cid"]) not in has_chunk),
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


def _keyform_plan(s) -> dict:
    """keyform 의 대상과, 대상이 아닌 물리 키 청크의 모양별 수. 대상은 모양 하나뿐이다:
    같은 소유자·논리 id 의 벡터가 있고 **전부** 논리 키 · 키 앞머리가 그 청크의 owner_id ·
    논리 키가 어느 Chunk 의 PK 로도 없고 다른 대상과도 안 겹친다."""
    chunks, vecs = _chunks(s), _vectors(s)
    raw_chunk = {c["cid"] for c in chunks}
    vec_forms: dict[tuple[str, str], set[bool]] = {}
    for v in vecs:
        vec_forms.setdefault(_key(v["o"], v["cid"]), set()).add(_phys(v["cid"]))
    plan: dict = {"physical_chunks": 0, "skipped_collision": 0,
                  "skipped_mixed_vector_forms": 0, "skipped_prefix_not_owner": 0,
                  "physical_without_vectors": 0, "physical_with_physical_vectors": 0}
    cands: list[tuple[str, str, str]] = []
    for c in chunks:
        if not _phys(c["cid"]):
            continue
        plan["physical_chunks"] += 1
        k = _key(c["o"], c["cid"])
        forms = vec_forms.get(k)
        if not forms:
            plan["physical_without_vectors"] += 1      # 재큐 뒤 워커가 논리 키로 채운다 — 역시 못 붙는다
        elif forms == {True}:
            plan["physical_with_physical_vectors"] += 1
        elif True in forms:
            plan["skipped_mixed_vector_forms"] += 1    # 바꾸면 지금 붙은 물리 키 벡터가 떨어진다
        elif c["cid"].split(pkns.SEP, 1)[0] != (c["o"] or ""):
            plan["skipped_prefix_not_owner"] += 1
        elif k[1] in raw_chunk:
            plan["skipped_collision"] += 1
        else:
            cands.append((c["cid"], k[1], c["doc"]))
    seen: dict[str, int] = {}
    for _, new, _ in cands:
        seen[new] = seen.get(new, 0) + 1
    plan["rewrite"] = [t for t in cands if seen[t[1]] == 1]
    plan["skipped_collision"] += len(cands) - len(plan["rewrite"])
    return plan


def _fts_probe(s, pairs) -> tuple[int, int]:
    """FTS 가 청크를 찾는가 — 청크 본문의 낱말로 그 문서 안에서 찾아 본다. (물어본 수, 찾은 수)."""
    probed = found = 0
    for cid, doc in pairs:
        text = (s.run("MATCH (c:Chunk) WHERE c.chunk_id = $id RETURN c.text AS t",
                      {"id": cid}, raw=True) or [{}])[0].get("t") or ""
        words = re.findall(r"\w{3,}", text)[:8]
        if not words:
            continue
        probed += 1
        hits = s.fulltext_search(" ".join(words), 200, {"doc_id": doc})
        found += any(pkns.strip(h.get("id") or "") == pkns.strip(cid) for h in hits)
    return probed, found


def _edges(s, cid: str) -> int:
    rows = s.run("MATCH (c:Chunk)-[r]-() WHERE c.chunk_id = $id RETURN count(r) AS n",
                 {"id": cid}, raw=True) or []
    return int(rows[0]["n"]) if rows else 0


def _rekey_chunk(s, old: str, new: str, cols: list[str]) -> str | None:
    """청크 하나의 PK 를 바꾼다. 옛 행은 새 행의 열·관계가 옛 것과 같을 때만 지운다.
    어긋나면 그 사유(수만)를 돌려주고 옛 행을 남긴다."""
    proj = ", ".join(f"c.{c} AS {c}" for c in cols)
    read = lambda cid: (s.run(f"MATCH (c:Chunk) WHERE c.chunk_id = $id RETURN {proj}",  # noqa: E731
                              {"id": cid}, raw=True) or [None])[0]
    before, edges = read(old), _edges(s, old)
    # PK 는 SET 이 안 된다 — 옛 행의 열을 엔진 안에서 그대로 옮겨 새 행을 만든다(파이썬 왕복이
    # 없어 값·타입·NULL 이 그대로다). FTS 는 새 행을 삽입으로 받는다.
    s.run(f"MATCH (o:Chunk) WHERE o.chunk_id = $old CREATE (n:Chunk {{chunk_id: $new, "
          + ", ".join(f"{c}: o.{c}" for c in cols) + "})", {"old": old, "new": new})
    for rel in rel_tables_on("Chunk"):
        body = ", ".join(f"{p}: r.{p}" for p in rel["props"])
        body = f" {{{body}}}" if body else ""
        for src, dst in rel["pairs"]:
            if src == "Chunk":
                pat, make = f"(o:Chunk)-[r:{rel['label']}]->(t:{dst})", f"(n)-[:{rel['label']}{body}]->(t)"
            elif dst == "Chunk":
                pat, make = f"(t:{src})-[r:{rel['label']}]->(o:Chunk)", f"(t)-[:{rel['label']}{body}]->(n)"
            else:
                continue
            s.run(f"MATCH {pat}, (n:Chunk) WHERE o.chunk_id = $old AND n.chunk_id = $new "
                  f"CREATE {make}", {"old": old, "new": new})
    if read(new) != before:
        return "columns_differ"
    if _edges(s, new) != edges:
        return "edges_differ"
    # 옛 행 삭제는 스토어의 청크 삭제와 같은 길이다 — 오래 증분 유지된 FTS 는 삭제 때 불일치로
    # 던질 수 있고, 그때만 색인을 다시 세우고 한 번 재시도한다.
    docstore._with_fts_repair(s, lambda: s.run(
        "MATCH (c:Chunk) WHERE c.chunk_id = $old DETACH DELETE c", {"old": old}))
    return None


def keyform(s) -> tuple[dict, list[str]]:
    before, plan = measure(s), _keyform_plan(s)
    todo = plan.pop("rewrite")
    out: dict = {"before": before, "plan": {**plan, "to_rewrite": len(todo)}}
    if not todo:
        out.update(after=before, rewritten=0)
        return out, []
    try:
        out["fts_index"] = s.heal_fts_index(dim=s._stored_vec_dim(1024)).get("state")
    except Exception as exc:                      # noqa: BLE001 — 판정으로 보고한다
        return out, [f"keyform: FTS index heal failed ({type(exc).__name__})"]
    refusal = docstore._remnant_refusal(s, chunks=True, vectors=False)
    if refusal:
        return out, [f"keyform refused: {refusal}"]
    # 열은 스키마 명세가 아니라 **표 자신**에서 읽는다 — 명세에 없는 열이 표에 있으면 그 값이
    # 새 행에서 사라진다. 명세에만 있고 표에 없는 열이면 복사가 엔진 오류로 멈춘다.
    cols = [str(r.get("name")) for r in s.run("CALL TABLE_INFO('Chunk') RETURN *") or []
            if r.get("name") and r.get("name") != "chunk_id"]
    if set(node_table_specs()["Chunk"]["cols"]) - {"chunk_id"} - set(cols):
        return out, ["keyform refused: the Chunk table lacks a column the schema declares"]
    done, problems = [], []
    for old, new, doc in todo:
        why = _rekey_chunk(s, old, new, cols)
        if why:
            problems.append(f"keyform stopped: a rewritten chunk's {why.replace('_', ' ')}")
            break
        done.append((new, doc))
    after, left = measure(s), _keyform_plan(s)
    # 검색 조인(`p.chunk_id = node.chunk_id`)과 같은 저장값 비교를 파이썬에서 한다 — 두 표를
    # 엔진에서 곱하면 허브 사본에서 1억 쌍이 넘는다.
    joined = {v["cid"] for v in _vectors(s)}
    docs = {d for _, d in done}
    per_doc: dict[str, list[str]] = {}
    for c in _chunks(s):
        if c["doc"] in docs:
            per_doc.setdefault(c["doc"], []).append(c["cid"])
    probed, found = _fts_probe(s, done)
    out.update(after=after, rewritten=len(done), docs_rewritten=len(docs),
               docs_fully_joined=sum(1 for ids in per_doc.values()
                                     if all(i in joined for i in ids)),
               rewritten_chunks_joined=sum(1 for new, _ in done if new in joined),
               fts_probe={"probed": probed, "found": found},
               left={k: v for k, v in left.items() if k != "rewrite"})
    for k in ("chunks", "vectors", "orphan_vectors", "docs_with_a_chunk_lacking_a_vector"):
        if after[k] != before[k]:
            problems.append(f"keyform changed {k}: {before[k]} -> {after[k]}")
    if out["rewritten_chunks_joined"] != len(done):
        problems.append(f"keyform: {len(done) - out['rewritten_chunks_joined']} rewritten "
                        "chunks do not join a vector")
    if not problems and left["rewrite"]:
        problems.append(f"keyform: {len(left['rewrite'])} chunks still to rewrite")
    if done and (not probed or found != probed):
        problems.append(f"keyform: full-text search found {found} of {probed} rewritten chunks")
    # 센서스의 '청크 없는 벡터' 가운데 이 모양으로 남는 것은 건너뛴 청크(섞인 벡터·앞머리)뿐이어야
    # 한다. 충돌로 건너뛴 청크의 벡터는 같은 저장값의 다른 청크에 붙어 있어 센서스에 안 잡힌다.
    left_shape = key_forms(s)["key_form_chunk_ids_by_shape"].get(
        "chunk_physical_vector_logical", 0)
    expected = plan["skipped_mixed_vector_forms"] + plan["skipped_prefix_not_owner"]
    out["census_key_form_left"] = left_shape
    if left_shape != expected:
        problems.append(f"keyform: {left_shape} chunk ids still split physical/logical, "
                        f"expected {expected} (the skipped ones)")
    return out, problems


#: 허브 이미지(사이드카·vinc-encode 가 같은 이미지로 뜬다)의 설치 줄 — 인코더 판의 정본.
_DOCKERFILE = ROOT / "Dockerfile.hub"
_VEC_TABLES = ("ChunkVec", "NodeVec")


def hub_runtime() -> str:
    """허브 인코더가 보고하는 지문을 이미지의 고정 판으로 짓는다.

    허브에서는 `store.encoder_runtime.fingerprint()` 가 설치된 판을 읽는다. 여기(러너)에는 그
    판이 없으므로 같은 모양(`ort=…;tok=…;np=…`, 같은 라벨·순서)을 `Dockerfile.hub` 의 고정
    판에서 짓는다. 하나라도 고정이 없으면 던진다 — 추측한 도장은 도장이 아니다."""
    lines = [ln for ln in _DOCKERFILE.read_text(encoding="utf-8").splitlines()
             if ln.startswith("RUN pip install") and "onnxruntime" in ln]
    if len(lines) != 1:
        raise ValueError(f"Dockerfile.hub 에 onnxruntime 설치 줄이 {len(lines)}개다")
    pins = dict(re.findall(r"(?<![\w-])([A-Za-z][\w.-]*)==(\d[\w.]*)", lines[0]))
    parts = []
    for package, label in encoder_runtime._PACKAGES:
        if package not in pins:
            raise ValueError(f"Dockerfile.hub 가 {package} 의 판을 고정하지 않는다")
        parts.append(f"{label}={pins[package]}")
    return ";".join(parts)


def _runtimes(s) -> dict[str, dict[str, int]]:
    """벡터 표별 도장 분포(도장 → 행 수). 도장은 판 문자열이라 싣는다('' = 도장 없음)."""
    out = {}
    for table in _VEC_TABLES:
        rows = s.run(f"MATCH (v:{table}) RETURN coalesce(v.encoder_runtime, '') AS r, "
                     "count(v) AS n", raw=True) or []
        out[table] = {str(r["r"]): int(r["n"]) for r in rows}
    return out


def _vector_probe(s, per_table: int = 20) -> dict[str, dict[str, int]]:
    """HNSW 가 제 행을 찾는가 — 표마다 고르게 뽑은 행의 벡터로 그 표의 색인을 되묻는다."""
    out = {}
    for table, index in (("ChunkVec", s._VEC_INDEX), ("NodeVec", s._NODE_VEC_INDEX)):
        ids = [r["id"] for r in s.run(f"MATCH (v:{table}) RETURN v.vec_id AS id "
                                      "ORDER BY v.vec_id", raw=True) or []]
        step = max(1, len(ids) // per_table)
        probed = found = 0
        for vid in ids[::step][:per_table]:
            emb = s.run(f"MATCH (v:{table}) WHERE v.vec_id = $id RETURN v.embedding AS e",
                        {"id": vid}, raw=True)[0]["e"]
            probed += 1
            try:
                hits = s.run(f"CALL QUERY_VECTOR_INDEX('{table}', '{index}', $emb, 50) "
                             "RETURN node.vec_id AS id", {"emb": list(emb)}, raw=True) or []
            except Exception:                     # noqa: BLE001 — 색인 없음도 '못 찾음'이다
                hits = []
            found += any(h.get("id") == vid for h in hits)
        out[table] = {"probed": probed, "found": found}
    return out


def stamp(s) -> tuple[dict, list[str]]:
    """도장 없는 벡터(ChunkVec·NodeVec)에 허브 인코더의 지금 지문을 찍는다. 벡터 값은 그대로다.

    ⚠ 이 도장은 **추정**이다: 도장이 없는 벡터가 어느 판에서 나왔는지는 벡터에 안 남는다.
    허브 이미지는 2026-09-10 부터 onnxruntime 1.30.0 이고 09-11 전량 재인코딩이 그 이미지에서
    돌았으며 판은 그 뒤로 고정이다 — 그래서 없는 도장 = 지금 판으로 본다. 다른 도장이 이미
    찍힌 벡터는 건드리지 않는다.
    근거: [[record:the-hub-encodes-queries-and-vectors-with-onnxruntime-1-30-since-2026-09-10]]

    SET 은 색인이 걸린 열(embedding)이 아니라 문자열 열 하나라 HNSW 삽입이 아니다 — 색인을
    내리지 않는다. 잔해 위에서는 어떤 SET 도 세그폴트이므로 기동 수리를 먼저 돌리고, 대신
    SET 앞뒤로 색인이 제 행을 찾는지 되묻는다(`_vector_probe`). 줄면 1."""
    value = hub_runtime()
    before, rt_before = measure(s), _runtimes(s)
    out: dict = {"value": value, "source": "Dockerfile.hub", "runtimes_before": rt_before}
    dim = s._stored_vec_dim(1024)
    try:
        out["vector_index"] = s.heal_vector_index(dim=dim).get("state")
        out["node_vector_index"] = s.heal_node_vector_index(
            dim=s._stored_vec_dim(dim, "NodeVec")).get("state")
    except Exception as exc:                      # noqa: BLE001
        return out, [f"stamp: vector index heal failed ({type(exc).__name__})"]
    if s.vector_index_remnant or s.node_vector_index_remnant:
        return out, ["stamp refused: a vector index remnant is left after the heal"]
    sample = [(c["cid"], c["doc"]) for c in _chunks(s)][::max(1, before["chunks"] // 20)][:20]
    probe_v, probe_f = _vector_probe(s), _fts_probe(s, sample)
    for table in _VEC_TABLES:
        s.run(f"MATCH (v:{table}) WHERE coalesce(v.encoder_runtime, '') = '' "
              "SET v.encoder_runtime = $r", {"r": value})
    after, rt_after = measure(s), _runtimes(s)
    probe_v2, probe_f2 = _vector_probe(s), _fts_probe(s, sample)
    out.update(after=after, runtimes_after=rt_after,
               stamped={t: rt_before[t].get("", 0) for t in _VEC_TABLES},
               vector_probe={"before": probe_v, "after": probe_v2},
               fts_probe={"before": list(probe_f), "after": list(probe_f2)})
    problems = []
    unstamped = sum(rt_after[t].get("", 0) for t in _VEC_TABLES)
    if unstamped:
        problems.append(f"stamp: {unstamped} vectors are still unstamped")
    for t in _VEC_TABLES:
        if sum(rt_after[t].values()) != sum(rt_before[t].values()):
            problems.append(f"stamp changed the {t} row count")
        if probe_v2[t]["found"] < probe_v[t]["found"]:
            problems.append(f"stamp: the {t} index found {probe_v2[t]['found']} of its own "
                            f"rows after, {probe_v[t]['found']} before")
    for k in ("chunks", "vectors", "orphan_vectors", "docs_with_a_chunk_lacking_a_vector"):
        if after[k] != before[k]:
            problems.append(f"stamp changed {k}: {before[k]} -> {after[k]}")
    if probe_f2 != probe_f:
        problems.append(f"stamp: full-text search found {probe_f2[1]} of {probe_f2[0]} sampled "
                        f"chunks after, {probe_f[1]} of {probe_f[0]} before")
    return out, problems


def _pending_reasons(s, value: str) -> tuple[list[str], dict[str, int]]:
    """needs_embed=1 문서를 가른다: 되돌려도 되는 것(전 청크가 같은 소유자·저장값 조인으로
    벡터를 가졌고 그 벡터가 전부 지금 도장) · 남길 것(사유별 수)."""
    chunks_of: dict[str, list[tuple[str, str]]] = {}
    for c in _chunks(s):
        chunks_of.setdefault(c["doc"], []).append((c["o"] or "", c["cid"]))
    stamps: dict[tuple[str, str], set[str]] = {}
    for v in s.run("MATCH (v:ChunkVec) RETURN v.chunk_id AS cid, v.owner_id AS o, "
                   "coalesce(v.encoder_runtime, '') AS r", raw=True) or []:
        stamps.setdefault((v["o"] or "", v["cid"]), set()).add(str(v["r"]))
    reasons = {"missing_vector": 0, "vector_not_stamped_with_hub_runtime": 0, "no_chunks": 0,
               "chunks_do_not_match_meta": 0}
    ok: list[str] = []
    for d in s.run("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN d.doc_id AS id, "
                   "d.chunk_count AS cc", raw=True) or []:
        keys = chunks_of.get(d["id"], [])
        if not keys:
            reasons["no_chunks"] += 1
        elif len(keys) != int(d.get("cc") or 0):      # 쓰기 창 표식(-1)·꼬리·빠진 청크
            reasons["chunks_do_not_match_meta"] += 1
        elif any(k not in stamps for k in keys):
            reasons["missing_vector"] += 1
        elif any(stamps[k] != {value} for k in keys):
            reasons["vector_not_stamped_with_hub_runtime"] += 1
        else:
            ok.append(d["id"])
    return ok, reasons


def unrequeue(s) -> tuple[dict, list[str]]:
    """재큐를 되돌린다: 벡터를 이미 다 가진 문서만 needs_embed=0. 벡터가 하나라도 빠진 문서
    (재큐 뒤 새로 쓴 문서가 여기 든다)는 pending 으로 남긴다. `_DocMeta.needs_embed` 만 쓴다.
    근거: [[record:a-soft-requeue-keeps-semantic-search-off-because-every-apply-rebuilds-the-vector-index-2026-09-30]]"""
    value = hub_runtime()
    before = measure(s)
    ok, reasons = _pending_reasons(s, value)
    for i in range(0, len(ok), _BATCH):
        s.run("MATCH (d:_DocMeta) WHERE d.doc_id IN $ids SET d.needs_embed = 0",
              {"ids": ok[i:i + _BATCH]})
    after = measure(s)
    left, reasons_after = _pending_reasons(s, value)
    out = {"before": before, "after": after, "pending_before": before["docs_pending_embed"],
           "reset": len(ok), "still_pending": after["docs_pending_embed"],
           "still_pending_by_reason": reasons_after}
    problems = []
    if left:
        problems.append(f"unrequeue: {len(left)} pending documents still qualify for reset")
    if after["docs_pending_embed"] != sum(reasons_after.values()):
        problems.append("unrequeue: pending documents do not add up to the reasons")
    if reasons_after != reasons:
        problems.append("unrequeue: the reasons changed for documents it did not touch")
    for k in ("docs", "chunks", "vectors", "orphan_vectors", "docs_with_a_chunk_lacking_a_vector"):
        if after[k] != before[k]:
            problems.append(f"unrequeue changed {k}: {before[k]} -> {after[k]}")
    return out, problems


_RUN = {"keyform": keyform, "stamp": stamp, "unrequeue": unrequeue, "requeue": requeue,
        "orphans": orphans, "stuck": stuck}


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
        report["key_forms_before"] = key_forms(s)
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
        report["key_forms_after"] = key_forms(s)
        # 원인별 가름이 센서스와 같은 수를 가르고 있는지 — 다르면 위 설명이 틀린 것이다.
        for when in ("before", "after"):
            census = report[f"census_{when}"]["chunk_vectors_without_chunk"]
            mine = report[f"key_forms_{when}"]["census_vectors_without_chunk"]
            if census != mine:
                problems.append(f"key_forms_{when} splits {mine} vectors without a chunk, "
                                f"the census counts {census}")
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
    if {"requeue", "unrequeue"} <= asked:
        raise SystemExit("--repairs: requeue 와 unrequeue 는 서로를 되돌린다 — 하나만 고를 것")
    return [r for r in REPAIRS if r in asked]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, help="원본 DB 파일(.wal 은 옆에서 찾는다)")
    ap.add_argument("--out", required=True, help="고친 사본을 쓸 경로(없어야 한다)")
    ap.add_argument("--repairs", default=",".join(DEFAULT_REPAIRS),
                    help="쉼표로(도는 순서는 이 목록 순서로 고정): " + ",".join(REPAIRS))
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
