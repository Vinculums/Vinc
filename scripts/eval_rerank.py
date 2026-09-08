#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RRF 뒤 리랭커 · 같은 질의·같은 스토어에서 `rrf` 와 `rrf→rerank@K` 를 댄다.

`eval_search.py` 와의 관계. 그쪽은 프로덕션 검색 경로(FTS·HNSW·RRF)를 재는 계기이고
여기는 그 경로 **뒤에 한 단계를 더 붙였을 때**를 잰다. 지표(`score`)·정답 실존 검증
(`assert_gold_present`)·문서 접기(`_docs_of`)·언어 판정(`lang_of`)은 그 파일 것을 그대로
가져다 쓴다: 숫자가 비교 가능하려면 같은 함수여야 한다. 이 파일이 더하는 것은 조건뿐이다.

    조건            어떻게
    fts             q.search(query)                                  eval_search 와 같다
    rrf             q.search(query, query_embedding=qvec)            delivered · 10행 페이지
    rrf_w<v>        같은 두 팔을 벡터 가중치 v 로 다시 융합            리랭커 없이 융합만 손본 대조군
    rerank@K        rrf 후보 K행 → 크로스인코더 → 상위 10행
    gated:<p>@K     게이트 p 가 켠 질의만 rerank, 나머지는 rrf 그대로  (적응형)

모든 조건은 **청크 10행 → 문서로 접기 → k=10 에서 채점**이다(eval_search 의 delivered 와
같은 정의). 리랭커가 재정렬한 것도 10행을 자른 뒤 접는다.

지표. eval_search 의 셋(Recall@10 · MRR · nDCG@10)에 둘을 더한다.
    recall@1 · recall@3   정답 문서가 첫 1건·3건 안에 있나 = **해석까지 호출 수**의 대리
                          지표. 에이전트는 검색 뒤 문서를 위에서부터 연다(vinc_doc_get).
                          정답이 n위면 n번째 호출에서 해석된다.
    mean_rank             맞힌 질의에서 정답의 평균 순위(작을수록 좋다).

임베더. `VINC_EMBED_ENCODE`(워커 루프백 · 프로덕션 형상)가 있으면 그것을 쓰고, 없으면
`--encoder 이름=onnx` 로 프로세스 안에서 인코딩한다(`scripts/encoder.py` · 벡터는
`--selfcheck` 로 프로덕션과 같음이 보장된다). 안에서 인코딩하면 `enc` 시계에 HTTP 왕복이
빠진다: **enc 는 프로덕션 수치와 비교하지 말 것.** 순위 지표에는 영향이 없다.

시계. `rerank@K` 는 토크나이즈+추론이고 `pairs` 는 실제로 잰 쌍의 수다. 지연은
VINC_EVAL_THREADS=1 로 잰 것만 프로덕션(1 vCPU)과 비교 가능하다. 순위는 스레드와 무관하다.

🔴 검색 백엔드가 `error` 를 보고하면 측정을 세운다(eval_search 와 같은 규칙: 조용한 0 점
금지). 벡터 팔이 빠진 창(`arms` = fts)은 `arms_census` 에 남긴다. 그 창에서 잰 rerank 는
"FTS 후보의 rerank" 이지 이 도구가 재려는 것이 아니다.

사용:
    python scripts/eval_rerank.py --data work/db.m3 --owner eval \\
        --encoder BAAI/bge-m3=.cache/embed/bge-m3-int8-rr.onnx \\
        --rerank BAAI/bge-reranker-v2-m3=.cache/rerank/art/bge-reranker-v2-m3/model-int8-rr.onnx \\
        --depth 10,20,50 --window first --gate top-agree --out work/perf/rerank-v2m3.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import query as q                                   # noqa: E402
from store.backend import open_store                         # noqa: E402
from store.config import load_config                         # noqa: E402

import eval_search as es                                     # noqa: E402
from reranker import (Reranker, gate, parse_model_spec, peak_rss_mb,  # noqa: E402
                      pick_piece, rerank_rows)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DEFAULT_EVALSETS = [str(_FIXTURES / n) for n in
                     ("evalset.json", "evalset_xling.json", "evalset_xling_en2ko.json")]
#: eval_search 와 같은 페이지 크기. 모든 조건이 이 행수를 자른 뒤 문서로 접는다.
PAGE = 10


# ── 순수 함수 ──────────────────────────────────────────────────────────────
def weighted_rrf(ranked_lists: list[list[dict]], weights: list[float], *, k: int = 60,
                 id_key: str = "id") -> list[dict]:
    """`api.query.rrf_fuse` 의 가중 판. 팔 i 의 기여 = w_i/(k+rank). w 가 전부 1 이면
    원본과 같은 순서다(정렬 키는 `search()` 와 같은 (점수 내림차순, id 오름차순)).
    리랭커 없이 융합만 손봤을 때 얼마나 회복되는지를 보는 **대조군**용이다."""
    if len(ranked_lists) != len(weights):
        raise ValueError("lists and weights differ in length")
    scores: dict = {}
    rows_by_id: dict = {}
    for rows, w in zip(ranked_lists, weights):
        for rank, row in enumerate(rows):
            rid = row.get(id_key)
            if rid is None:
                continue
            scores[rid] = scores.get(rid, 0.0) + float(w) / (k + rank + 1)
            rows_by_id.setdefault(rid, row)
    fused = sorted(rows_by_id.values(),
                   key=lambda r: (-scores[r[id_key]], str(r[id_key])))
    out = []
    for r in fused:
        row = dict(r)
        row["rrf_score"] = scores[r[id_key]]
        out.append(row)
    return out


#: 가중 RRF 대조군의 기본값. `api.query.rrf_fuse` 의 k 와 같은 수이고, 프로덕션에서는
#: 아무 호출부도 이 인자를 안 넘긴다 — 그래서 60 이 측정된 적 없는 기본값이다.
#: 근거와 스윕 범위: [[vinc.plan.retrieval-research-2026-09-08 §4]]
DEFAULT_RRF_K = 60


def cond_name(w: float, k: int, depth: int) -> str:
    """가중 RRF 조건의 이름. **기본값이면 접미를 안 붙인다.**

    이 플래그가 생기기 전에 잰 결과 JSON 의 조건 이름이 `rrf_w1` 이다. 기본 조건에도
    `_k60_d0` 을 붙이면 같은 조건이 다른 이름을 갖게 되어 옛 측정과 나란히 못 놓는다
    (계획 §2 의 비교 규칙은 이름이 아니라 매니페스트로 판정하지만, 이름이 갈리면
    사람이 표를 못 읽는다)."""
    n = f"rrf_w{w:g}"
    if k != DEFAULT_RRF_K:
        n += f"_k{k}"
    if depth:
        n += f"_d{depth}"
    return n


def score_ext(ranked_doc_ids: list[str], truth: str, k: int) -> dict:
    """eval_search.score 에 recall@1 · recall@3 · rank 를 더한다. 앞의 셋은 그 함수의
    값을 **그대로** 쓴다(따로 계산하면 언젠가 갈라진다)."""
    s = es.score(ranked_doc_ids, truth, k)
    top = ranked_doc_ids[:k]
    rank = top.index(truth) + 1 if truth in top else 0
    s["r1"] = 1.0 if rank == 1 else 0.0
    s["r3"] = 1.0 if 0 < rank <= 3 else 0.0
    s["rank"] = rank
    return s


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    hits = [r["rank"] for r in rows if r["rank"]]
    return {"n": n,
            "recall": round(sum(r["recall"] for r in rows) / n, 4),
            "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
            "ndcg": round(sum(r["ndcg"] for r in rows) / n, 4),
            "r1": round(sum(r["r1"] for r in rows) / n, 4),
            "r3": round(sum(r["r3"] for r in rows) / n, 4),
            "mean_rank": round(sum(hits) / len(hits), 3) if hits else None}


def paired_bootstrap(a: list[float], b: list[float], *, n: int = 1000, seed: int = 7
                     ) -> dict:
    """b - a 의 짝지은 부트스트랩 95% CI(같은 질의끼리). `bench_models --bootstrap` 과
    같은 규칙: CI 가 0 을 품으면 "잡음 범위를 못 넘었다"다."""
    if len(a) != len(b) or not a:
        return {}
    rng = random.Random(seed)
    m = len(a)
    deltas = [y - x for x, y in zip(a, b)]
    point = sum(deltas) / m
    reps = []
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            s += deltas[rng.randrange(m)]
        reps.append(s / m)
    reps.sort()
    return {"delta": round(point, 4),
            "ci95": [round(reps[int(0.025 * n)], 4), round(reps[min(n - 1, int(0.975 * n))], 4)],
            "n": m}


def _pct(v: list[float], frac: float):
    if not v:
        return None
    srt = sorted(v)
    return round(srt[min(int(len(srt) * frac), len(srt) - 1)], 2)


# ── 실행 ────────────────────────────────────────────────────────────────────
#: 자식 레벨 벡터 조회. `vector_search` 는 부모로 접으면서 seq 를 버리므로 여기서
#: 직접 부른다. 접는 규칙은 백엔드와 같다: 이미 distance 오름차순이니 첫 등장이 이긴다.
#: ⚠ 인덱스 이름은 **LITERAL 이어야 한다** — `$idx` 로 넘기면 `Binder exception:
#: $idx has type PARAMETER but LITERAL was expected` 다(실측). 백엔드도 같은 이유로
#: f-string 으로 끼운다(`store/embedded_backend.py::vector_search`). 이름이 코드에
#: 섞이므로 식별자 형태만 허용한다.
_CHILD_CY = ("CALL QUERY_VECTOR_INDEX('ChunkVec', '{idx}', $emb, $k) "
             "RETURN node.chunk_id AS id, node.seq AS seq, distance ORDER BY distance")


def child_seqs(store, qvec: list[float], k: int) -> dict[str, int]:
    """부모 chunk_id -> 이긴 자식의 seq. 실패하면 **죽는다**(조용한 빈 맵이면 `child`
    창이 `first` 로 강등되는데 출력에서 둘을 구분할 방법이 없다)."""
    idx = getattr(store, "_VEC_INDEX", "chunk_vec")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(idx)):
        raise SystemExit(f"인덱스 이름이 식별자가 아니다: {idx!r}")
    try:
        rows = store.run(_CHILD_CY.format(idx=idx), {"emb": list(qvec), "k": int(k)})
    except Exception as e:                       # noqa: BLE001
        raise SystemExit(f"자식 seq 조회 실패({type(e).__name__}: {e}) - child 창은 "
                         "이게 없으면 first 와 같아진다. 스토어가 --split 로 만들어졌나?")
    out: dict[str, int] = {}
    for r in rows:
        i = r.get("id")
        if i is not None and i not in out:
            out[i] = int(r.get("seq") or 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로")
    ap.add_argument("--owner")
    ap.add_argument("--evalset", action="append", default=[],
                    help="평가셋 JSON(반복 가능). 없으면 fixtures 의 셋 전부")
    ap.add_argument("--limit", type=int, default=0, help="앞 N 질의만(연기용)")
    ap.add_argument("--encoder", help="이름=onnx · 프로세스 안 임베더(VINC_EMBED_ENCODE 없을 때)")
    ap.add_argument("--rerank", required=True, help="이름=onnx · 크로스인코더")
    ap.add_argument("--depth", default="10,20,50", help="후보 깊이 K 목록")
    ap.add_argument("--window", choices=("first", "max", "child"), default="first")
    ap.add_argument("--split-count", choices=("tokenizer", "estimate"), default="tokenizer",
                    help="child 창에서 조각을 셀 때 무엇으로 세나. tokenizer=리랭커 어휘"
                         "(워커와 같은 종류) · estimate=프로덕션 질의 경로의 근사")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--gate", action="append", default=[],
                    help="적응형 게이트 정책(반복 가능): top-agree · margin:0.2")
    ap.add_argument("--vec-weight", action="append", type=float, default=[],
                    help="가중 RRF 대조군의 벡터 팔 가중치(반복 가능)")
    ap.add_argument("--rrf-k", action="append", type=int, default=[],
                    help=f"가중 RRF 의 k(반복 가능, 미지정={DEFAULT_RRF_K}). 프로덕션은 "
                         "이 인자를 아무도 안 넘겨 60 이 한 번도 측정된 적 없다")
    ap.add_argument("--arm-depth", action="append", type=int, default=[],
                    help="팔마다 융합에 넣을 후보 수(반복 가능, 미지정=SEARCH_POOL_CAP 전량). "
                         "두 팔을 같은 깊이로 자른다 — 재조회는 안 한다")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--out", help="결과 JSON 경로(없으면 stdout 표)")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data} if args.data else None)
    store = open_store(cfg)
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 1

    paths = args.evalset or _DEFAULT_EVALSETS
    qs: list[dict] = []
    for p in paths:
        qs.extend(es.load_evalset(p))
    if args.limit:
        qs = qs[:args.limit]
    if not qs:
        print("질의가 없다", file=sys.stderr)
        return 1
    es.assert_gold_present(store, qs, args.owner)

    depths = sorted({int(x) for x in args.depth.split(",") if x.strip()})
    max_k = max(depths)
    # 미지정이면 각각 한 값짜리 목록 = 예전과 똑같은 한 조건. 스윕은 옵트인이다.
    rrf_ks = sorted({int(x) for x in (args.rrf_k or [DEFAULT_RRF_K])})
    arm_depths = sorted({int(x) for x in (args.arm_depth or [0])})
    filters = {"owner_id": args.owner} if args.owner else None

    # 임베더: 루프백 창구(프로덕션 형상) 우선, 없으면 프로세스 안.
    encode_url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
    local_enc = None
    if not encode_url:
        if not args.encoder:
            print("임베더가 없다: VINC_EMBED_ENCODE 나 --encoder 를 줄 것", file=sys.stderr)
            return 1
        from encoder import Encoder
        name, path = parse_model_spec(args.encoder)
        local_enc = Encoder(name, path)
        local_enc.encode(["warmup"], kind="query")

    def encode(text: str) -> list[float]:
        if local_enc is not None:
            return local_enc.encode([text], kind="query")[0]
        v = es.encode_query(text, encode_url)
        if v is None:
            raise RuntimeError("encode service returned no vector")
        return v

    rname, rpath = parse_model_spec(args.rerank)
    rr = Reranker(rname, rpath, max_tokens=args.max_tokens)
    warm_s = rr.warm()

    conds = ["fts", "rrf"] + [cond_name(w, kk, d) for w in args.vec_weight
                              for kk in rrf_ks for d in arm_depths]
    for K in depths:
        conds.append(f"rerank@{K}")
        for g in args.gate:
            conds.append(f"gated:{g}@{K}")
    per: dict[str, list[dict]] = {c: [] for c in conds}
    by_lang: dict[str, dict[str, list[dict]]] = {}
    tm: dict[str, list[float]] = {"enc": [], "search_fts": [], "search_pool": []}
    pairs: dict[str, list[int]] = {}
    gate_hits: dict[str, int] = {}
    arm_notes: dict[str, int] = {}
    per_query: list[dict] = []
    align = {"ok": 0, "mismatch": 0, "found": 0, "candidates": 0}
    vec_n: dict[str, int] = {}

    def split_count(text: str) -> list[str]:
        """`--split-count` 가 무엇으로 세느냐를 정한다. estimate 는 프로덕션 질의 경로가
        가진 것(토크나이저 없음 -> `api/docstore.estimate_tokens`, 1.3배 여유)이고
        tokenizer 는 워커가 쓴 것이다. 둘의 불일치율이 곧 프로덕션 이식 비용이다."""
        if args.split_count == "tokenizer":
            return rr.split(text)
        from api.docstore import chunk_doc
        parts = chunk_doc(text, max_tokens=rr.piece_tokens)
        return [p["text"] for p in parts if p["text"].strip()] or [text]


    # 워밍업(eval_search 와 같은 이유: 확장 LOAD 와 HNSW 첫 접촉을 표본에서 뺀다).
    _w = qs[0]["query"]
    try:
        q.search(_w, query_embedding=encode(_w), top_k=PAGE, owner=args.owner, cfg=cfg, store=store)
    except Exception:
        pass

    for item in qs:
        text, truth = item["query"], item["doc_id"]
        lang = item.get("lang") or es.lang_of(text)
        by_lang.setdefault(lang, {c: [] for c in conds})
        rec: dict = {"query": text, "doc_id": truth, "lang": lang}

        _t = time.perf_counter()
        qvec = encode(text)
        tm["enc"].append((time.perf_counter() - _t) * 1000)

        _t = time.perf_counter()
        res = q.search(text, top_k=PAGE, owner=args.owner, cfg=cfg, store=store)
        tm["search_fts"].append((time.perf_counter() - _t) * 1000)
        if res.get("error"):
            raise SystemExit(f"검색 백엔드가 실패를 보고했다: {res['error']} (질의 {text!r})")
        s = score_ext(es._docs_of(store, res.get("rows") or []), truth, PAGE)
        per["fts"].append(s); by_lang[lang]["fts"].append(s); rec["fts"] = s["rank"]

        # 후보 풀: delivered 경로 그대로, 페이지만 K 로 넓힌다(정렬 안정 · 앞 10행이 곧 rrf).
        _t = time.perf_counter()
        res2 = q.search(text, query_embedding=qvec, top_k=max_k, detail="full",
                        owner=args.owner, cfg=cfg, store=store)
        tm["search_pool"].append((time.perf_counter() - _t) * 1000)
        if res2.get("error"):
            raise SystemExit(f"RRF 조건이 실패를 보고했다: {res2['error']}")
        _a = res2.get("arms") if isinstance(res2.get("arms"), str) else "?"
        _k = f"{_a}|{res2.get('vector_note') or ''}"
        arm_notes[_k] = arm_notes.get(_k, 0) + 1
        pool = res2.get("rows") or []
        s = score_ext(es._docs_of(store, pool[:PAGE]), truth, PAGE)
        per["rrf"].append(s); by_lang[lang]["rrf"].append(s); rec["rrf"] = s["rank"]

        # 대조군: 같은 두 팔을 다시 융합(프로덕션 `_chunk_arms` 와 같은 호출).
        if args.vec_weight:
            kw = store.fulltext_search(text, q.SEARCH_POOL_CAP, filters)
            try:
                vec = store.vector_search(qvec, q.SEARCH_POOL_CAP, filters)
            except Exception:
                vec = []
            kw = [r for r in kw if not str(r.get("id") or "").startswith(q._CONV_PREFIX)]
            vec = [r for r in vec if not str(r.get("id") or "").startswith(q._CONV_PREFIX)]
            for w in args.vec_weight:
                for kk in rrf_ks:
                    for d in arm_depths:
                        # 깊이는 이미 받아 둔 풀을 자르기만 한다 — 팔을 다시 조회하면
                        # 스토어를 depth 수만큼 더 때리는데 얻는 것이 없다.
                        fused = weighted_rrf([kw[:d] if d else kw,
                                              vec[:d] if d else vec], [1.0, w], k=kk)
                        s = score_ext(es._docs_of(store, fused[:PAGE]), truth, PAGE)
                        c = cond_name(w, kk, d)
                        per[c].append(s); by_lang[lang][c].append(s); rec[c] = s["rank"]

        # 리랭크: 가장 깊은 K 를 **한 번만** 재고 얕은 K 는 그 점수의 접두를 쓴다. 쌍의
        # 점수는 서로 독립이라 결과는 K 마다 따로 잰 것과 같다(질의당 쌍 수가 곧 비용이라
        # 깊이마다 다시 재면 배터리가 깊이 수만큼 길어진다). 시계는 max_k 조건에만 남고
        # `ms_per_pair` 가 얕은 깊이의 비용을 준다. 깊이별 지연을 직접 재려면 `--depth`
        # 하나로 따로 돌린다(지연 패스 · VINC_EVAL_THREADS=1).
        cand_all = pool[:max_k]
        cand_texts = [r.get("text") or "" for r in cand_all]
        seqs = None
        if args.window == "child":
            smap = child_seqs(store, qvec, max_k * 8)
            seqs = [smap.get(str(r.get("id") or "")) for r in cand_all]
            align["found"] += sum(x is not None for x in seqs)
            # 조각 수 대조 = 토크나이저 불일치 측정. 하네스가 세는 조각 수와 스토어에
            # 실제로 들어간 자식 수가 다르면 seq 는 **다른 조각**을 가리킨다.
            for r, t in zip(cand_all, cand_texts):
                cid = str(r.get("id") or "")
                if cid not in vec_n:
                    rows_n = store.run("MATCH (v:ChunkVec) WHERE v.chunk_id = $i "
                                       "RETURN count(v) AS c", {"i": cid})
                    vec_n[cid] = int(rows_n[0]["c"]) if rows_n else 0
                mine = len(split_count(t))
                align["ok" if mine == vec_n[cid] else "mismatch"] += 1
        _t = time.perf_counter()
        scores_all, n_pairs = rr.score_texts(text, cand_texts,
                                             window=args.window, seqs=seqs)
        dt = (time.perf_counter() - _t) * 1000
        tm.setdefault(f"rerank@{max_k}", []).append(dt)
        pairs.setdefault(f"rerank@{max_k}", []).append(n_pairs)
        align["candidates"] += len(cand_all)
        for K in depths:
            cand = cand_all[:K]
            c = f"rerank@{K}"
            ranked = rerank_rows(cand, scores_all[:K])
            s_rr = score_ext(es._docs_of(store, ranked[:PAGE]), truth, PAGE)
            per[c].append(s_rr); by_lang[lang][c].append(s_rr); rec[c] = s_rr["rank"]
            for g in args.gate:
                gc = f"gated:{g}@{K}"
                on = gate(cand, g)
                gate_hits[gc] = gate_hits.get(gc, 0) + (1 if on else 0)
                s_g = s_rr if on else per["rrf"][-1]
                per[gc].append(s_g); by_lang[lang][gc].append(s_g); rec[gc] = s_g["rank"]
        per_query.append(rec)

    stats = {}
    for key, cy in (("docs", "MATCH (d:_DocMeta) RETURN count(d) AS c"),
                    ("chunks", "MATCH (c:Chunk) RETURN count(c) AS c"),
                    ("vecs", "MATCH (v:ChunkVec) RETURN count(v) AS c")):
        try:
            r = store.run(cy)
            stats[key] = int(r[0]["c"]) if r else 0
        except Exception:
            stats[key] = -1
    # 코퍼스 지문·split 여부·청커·런타임·CPU. `store` 셋만으로는 두 실행이
    # 같은 것 위에서 났는지 말할 수 없다([[vinc.plan.retrieval-research-2026-09-08 §3]]).
    manifest = es.run_manifest(store, args.owner, args.data)
    store.close()

    out = {
        "kind": "eval.rerank",
        "queries": len(qs), "source": paths, "top_k": PAGE,
        "store": {"data": args.data, "owner": args.owner, **stats},
        "manifest": manifest,
        "encoder": (encode_url or args.encoder),
        "reranker": {"model": rname, "onnx": rpath, "window": args.window,
                     "max_tokens": args.max_tokens, "load_s": round(rr.load_s or 0, 3),
                     "warm_s": round(warm_s, 3),
                     "threads": int(os.environ.get("VINC_EVAL_THREADS", "1") or "1")},
        "depths": depths, "gates": args.gate, "vec_weights": args.vec_weight,
        "rrf_ks": rrf_ks, "arm_depths": arm_depths,
        "overall": {c: summarize(v) for c, v in per.items() if v},
        "by_lang": {lg: {c: summarize(v) for c, v in cs.items() if v}
                    for lg, cs in by_lang.items()},
        "timing_ms": {k: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95)}
                      for k, v in tm.items() if v},
        "pairs": {k: {"mean": round(sum(v) / len(v), 2), "max": max(v)} for k, v in pairs.items()},
        "ms_per_pair": {k: round(sum(tm[k]) / max(1, sum(pairs[k])), 2) for k in pairs},
        "gate_stats": {g: {"triggered": n, "total": len(qs)} for g, n in gate_hits.items()},
        "arms_census": arm_notes,
        "piece_align": ({**align, "split_count": args.split_count,
                         "rate": round(align["mismatch"]
                                       / max(1, align["ok"] + align["mismatch"]), 4)}
                        if args.window == "child" else None),
        "peak_rss_mb": peak_rss_mb(),
        "per_query": per_query,
    }
    # rrf 대비 ΔnDCG 의 짝지은 부트스트랩(전체 · 언어별).
    base = [r["ndcg"] for r in per["rrf"]]
    out["bootstrap_vs_rrf"] = {c: paired_bootstrap(base, [r["ndcg"] for r in v], n=args.bootstrap)
                               for c, v in per.items() if c != "rrf" and v}
    out["bootstrap_vs_rrf_by_lang"] = {
        lg: {c: paired_bootstrap([r["ndcg"] for r in cs["rrf"]], [r["ndcg"] for r in v],
                                 n=args.bootstrap)
             for c, v in cs.items() if c != "rrf" and v}
        for lg, cs in by_lang.items()}

    if args.window == "child":
        pa = out["piece_align"]
        if pa["rate"] > 0.10:
            raise SystemExit(
                f"조각 수 불일치 {pa['rate']:.1%}(> 10%) - seq 가 임베드된 것과 다른 "
                f"조각을 가리킨다. 이 조건의 수치는 읽을 수 없다. "
                f"ok={pa['ok']} mismatch={pa['mismatch']} split_count={pa['split_count']}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {args.out}")

    print(f"질의 {len(qs)}개 · 리랭커 {rname} ({args.window}, ≤{args.max_tokens}tok) · "
          f"스토어 docs={stats.get('docs')} chunks={stats.get('chunks')} vecs={stats.get('vecs')}")
    print(f"arms: {arm_notes}")
    hdr = f"{'조건':<22}{'n':>4}{'R@10':>7}{'MRR':>7}{'nDCG':>7}{'R@1':>6}{'R@3':>6}{'rank':>6}"
    print("\n" + hdr)
    for c, m in out["overall"].items():
        print(f"{c:<22}{m['n']:>4}{m['recall']:>7.3f}{m['mrr']:>7.3f}{m['ndcg']:>7.3f}"
              f"{m['r1']:>6.2f}{m['r3']:>6.2f}{(m['mean_rank'] or 0):>6.2f}")
    if len(out["by_lang"]) > 1:
        for lg, cs in sorted(out["by_lang"].items()):
            print(f"\n[{lg}]")
            for c, m in cs.items():
                print(f"  {c:<20}{m['n']:>4}{m['recall']:>7.3f}{m['mrr']:>7.3f}{m['ndcg']:>7.3f}"
                      f"{m['r1']:>6.2f}{m['r3']:>6.2f}{(m['mean_rank'] or 0):>6.2f}")
    print("\ntiming_ms:", json.dumps(out["timing_ms"]))
    print("pairs:", json.dumps(out["pairs"]), " ms/pair:", json.dumps(out["ms_per_pair"]))
    print("gates:", json.dumps(out["gate_stats"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
