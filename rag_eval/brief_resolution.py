#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N1 — `brief` 가 질문이 가리키는 **노드**를 맞히는가.

**왜 이것이 첫 항목인가.** 그 아래 모든 측정(재료 품질·답변 정확도)이 해소된 노드 위에
선다. 노드가 틀리면 N2 는 그래프 품질이라 부르며 **해소 정확도를 재게 된다.**

**여태 없던 것.** 이 리포의 평가셋은 전부 문서나 절을 정답으로 단다
(`evalset_human` 의 `doc_id`, `evalset.json` 의 제목 질의 …). 노드 단위 채점은
2026-08-30 재실행의 14문항에서 손으로 한 번 했을 뿐이고 하네스가 없었다.
근거: [[record:no-node-level-gold-set-exists-2026-09-09]]

**무엇을 재는가.** `api.query.brief` 의 반환이 지표와 1:1이라 새 계약이 필요 없다:

    status      resolved | choose | none
    node.id     확정된 노드 · node.kind 종류
    candidates  확정 못 했을 때의 후보(순위 있음)
    grounds     답변 재료의 근거 id 들
    fell_through / unreadable   매치했으나 못 연 후보

⚠ **답 가능/불가를 가른다.** 그래프에 답이 없는 질문에 `resolved` 를 주는 것은 낮은
정확도가 아니라 **다른 고장**이다(세 호스트에서 재현됐다). 그래서 `answerable: false`
행의 정답은 노드가 아니라 "none 또는 choose" 다.

⚠ **정답 질문은 그래프 밖에 산다.** 질문을 노드 제목에 인용하면 그 노드가 그 질문을
영원히 이긴다(smoke 2026-09-06 A5). 이 파일도 예시 질문을 담지 않는다.

정답 파일(JSONL 또는 JSON 배열), 한 줄에 하나:

    {"query": "...", "gold_node_id": "concept:...", "gold_kind": "concept",
     "language_axis": "ko", "answerable": true, "gold_doc": "...", "note": "..."}

    answerable=false 행은 `gold_node_id` 를 비운다.

사용:
    python -m rag_eval.brief_resolution --data <store> --owner <owner> \\
        --gold rag_eval/fixtures/brief_gold.jsonl --out out.json

종료코드 1: 채점할 것이 없다(정답 0건이거나 전 행이 실패).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from api import query as q            # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402

# 채점은 전송과 무관한 순수 함수라 evalkit.grading 에 산다 — CLI·v1 경로와 같은 것을 쓴다.
from rag_eval.evalkit.grading import (LOW_REUSE, RANKS, UNANSWERABLE_KINDS,  # noqa: E402,F401
                             check_gold, grade_one, load_gold, rank_of,
                             summarize, union_rank_of)


def title_reuse(query: str, title: str) -> float:
    """질의가 정답 제목의 토큰을 얼마나 나르나 (0.0~1.0).

    **이 수 없이는 축끼리 비교가 안 된다.** 후보 진입을 정하는 것은 제목 팔의 점수이고
    그 점수는 맞은 토큰 수다. 그래서 "한국어가 나쁘다"처럼 보이는 것이 사실은 "그 축의
    질문이 제목 낱말을 덜 남겼다"인 경우가 있다 — 2026-09-09 에 실제로 그랬고, 겹침을
    통제하니 축 넷이 거의 같은 자리로 모였다.

    제목 팔이 쓰는 토크나이저 그대로 잰다. 다른 것으로 재면 랭커가 보는 양이 아니라
    비슷한 다른 양을 재게 된다.
    근거: [[record:node-candidacy-tracks-title-token-reuse-not-language-2026-09-09]]"""
    tt = set(q.normalize_query_tokens(title or ""))
    if not tt:
        return 0.0
    return round(len(set(q.normalize_query_tokens(query or "")) & tt) / len(tt), 4)


def gold_titles(store, rows: list[dict], owner: str) -> dict:
    """정답 노드의 제목. 존재 확인과 같은 프로브를 쓴다 — 조회를 두 번 하지 않는다."""
    out: dict = {}
    for r in rows:
        nid = r.get("gold_node_id")
        if not nid or nid in out:
            continue
        probe = q.execute(q.seed_lookup_plan(nid, owner=owner), None, store=store)
        for x in probe.get("rows", []) if probe.get("executed") else []:
            if x.get("id") == nid:
                out[nid] = x.get("title") or ""
    return out


def gold_present(store, rows: list[dict], owner: str) -> list[str]:
    """정답 노드가 이 스토어에 실재하는가. **없으면 그 행은 필연 오답이고, 점수는
    그냥 낮게 나올 뿐이라 '해소가 나쁘다'로 오독된다**(조용한 0점 — 코퍼스 씨딩이
    같은 함정을 이미 한 번 냈다)."""
    missing = []
    for r in rows:
        nid = r.get("gold_node_id")
        if not nid:
            continue
        probe = q.execute(q.seed_lookup_plan(nid, owner=owner), None, store=store)
        if not (probe.get("executed")
                and any(x.get("id") == nid for x in probe.get("rows", []))):
            missing.append(nid)
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="스토어 경로(사본을 쓸 것 — 단일 라이터)")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--gold", action="append", required=True, help="정답 파일(반복 가능)")
    ap.add_argument("--out", help="결과 JSON 경로(없으면 요약만 stdout)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--encoder", metavar="이름=onnx",
                    help="질의를 프로세스 안에서 임베딩해 의미 팔을 켠다. 없으면 어휘·"
                         "그래프 경로만 잰다(그 사실이 결과의 arms_note 에 적힌다)")
    ap.add_argument("--allow-missing-gold", action="store_true",
                    help="정답 노드가 스토어에 없어도 진행(기본은 거부 — 조용한 0점 방지)")
    a = ap.parse_args(argv)

    rows = load_gold(a.gold)
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print("정답이 0건이다 — 잴 것이 없다", file=sys.stderr)
        return 1
    for b in check_gold(rows):
        print(f"정답셋 오류 — {b}", file=sys.stderr)
    if check_gold(rows):
        return 1

    # 스토어는 본 파일과 `<data>.wal` 두 파일이다(store/embedded_backend.py). WAL 이
    # 남아 있으면 본 파일 지문이 내용을 대표하지 못하므로 재지 않고 거부한다.
    wal = pathlib.Path(a.data + ".wal")
    if wal.exists():
        print(f"WAL 이 남아 있다({wal}) — 본 파일 지문이 스토어 내용을 대표하지 못한다. "
              f"WAL 없는 새 사본으로 돌릴 것", file=sys.stderr)
        return 1
    # 스토어 지문은 **열기 전에** 잰다(여는 것만으로 바이트가 바뀔 수 있다). 기대값은
    # `rag_eval/fixtures/eval_store.lock.json` 에 있다.
    from rag_eval.answer_material import file_sha            # noqa: PLC0415
    store_sha = file_sha(pathlib.Path(a.data))
    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        print(f"스토어에 도달할 수 없다: {a.data}", file=sys.stderr)
        return 1

    missing = gold_present(store, rows, a.owner)
    if missing and not a.allow_missing_gold:
        print(f"정답 노드 {len(missing)}건이 이 스토어에 없다 — 평가가 무효다. "
              f"예: {missing[:3]}", file=sys.stderr)
        store.close()
        return 1

    # 🔴 질의 임베딩은 **선택**이고 기본은 끈 것이다. 벡터가 없는 스토어에 임베딩을
    # 넘기면 없는 팔을 있는 척 재게 된다. 켠 경우에는 프로덕션과 같은 창구를 쓰지
    # 않는다는 것을 결과에 적는다 — 여기 것은 프로세스 안 인코더다.
    enc = None
    if a.encoder:
        from encoder import Encoder                      # noqa: PLC0415
        name, path = a.encoder.split("=", 1) if "=" in a.encoder else (a.encoder, None)
        enc = Encoder(name, path)
        enc.encode(["warmup"], kind="query")   # 적재 비용을 첫 질의 밖으로 뺀다

    titles = gold_titles(store, rows, a.owner)
    for r in rows:
        r["_title_reuse"] = title_reuse(r.get("query", ""),
                                        titles.get(r.get("gold_node_id") or "", ""))

    graded, t0 = [], time.perf_counter()
    for r in rows:
        # 🔴 `query_embedding` 을 안 넘긴다. 이 스토어에는 벡터가 없을 수 있고(데스크탑
        # 스냅샷은 ChunkVec 0), 넘기면 없는 팔을 있는 척 재게 된다. N1 이 재는 것은
        # 해소이지 벡터 검색이 아니다 — 그 사실을 결과에 적는다.
        qvec = enc.encode([r["query"]], kind="query")[0] if enc else None
        res = q.brief(r["query"], owner=a.owner, store=store, query_embedding=qvec)
        # 같은 풀에서 잰다 — brief 가 내부적으로 요구하는 깊이 그대로다(집 규칙 5:
        # 다른 풀에서 잰 두 팔은 단계가 아니라 풀을 잰다).
        hits = (q.search(r["query"], top_k=q._BRIEF_TOP_K, owner=a.owner,
                         store=store, query_embedding=qvec).get("node_hits") or [])
        graded.append(grade_one(res, r, hits))
    wall = time.perf_counter() - t0
    store.close()

    summ = summarize(graded)
    summ["wall_s"] = round(wall, 2)
    summ["arms_note"] = (
        f"질의 임베딩: 프로세스 안 인코더 {a.encoder} — 의미 팔이 돌았다. 프로덕션은 "
        f"HTTP 창구를 쓰므로 지연은 비교 불가이고, 순위는 같은 벡터라 비교 가능하다"
        if enc else
        "query_embedding 미전달 — 의미 팔 없음. 이 수치는 어휘·그래프 경로만의 해소 정확도다")
    summ["encoder"] = a.encoder or ""
    summ["store_sha256"] = store_sha
    summ["missing_gold_nodes"] = missing
    out = {"kind": "brief.resolution", "store": a.data, "owner": a.owner,
           "encoder": a.encoder or "",
           "gold_files": a.gold, "summary": summ, "per_query": graded}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
