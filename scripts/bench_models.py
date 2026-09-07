#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""모델 비교 — **정확 코사인** 순위로 임베딩 모델을 언어별로 잰다.

`eval_search.py` 와 무엇이 다른가: 저쪽은 **프로덕션 경로**(FTS·HNSW 근사·RRF)를 재고
이쪽은 **모델 자체**를 잰다. 모델을 비교할 때 HNSW 근사를 섞으면 인덱스 차이가 모델
차이로 섞여 들어온다. 그래서 여기서는 인덱스를 쓰지 않고 전 청크와 정확 코사인을 잰다
([[vinc.plan.embedding-pipeline]] 부록 B §5).

입력은 `fixtures/evalset.json`(질의·정답)과 코퍼스 텍스트다. 스토어가 없어도 돈다 —
코퍼스를 파일에서 직접 읽는다.

    python scripts/bench_models.py \\
        --model BAAI/bge-base-en-v1.5 \\
        --model intfloat/multilingual-e5-base \\
        --model intfloat/multilingual-e5-base=.cache/embed/e5-int8-rr.onnx

`이름=경로` 형태로 주면 그 ONNX 파일을 쓴다(자체 양자화본 비교용 — `quantize_e5.py`).

⚠ **지표는 반드시 언어별로 갈라 읽는다.** 섞어 재면 CJK 손실이 묻힌다: 512토큰 초과
유실이 영어는 전체 토큰의 5.7% 인데 한국어는 82% 다. 1차 측정이 무효였던 이유도 여기에
있다(라벨한 "한국어" 질의에 한글이 0자였다 — `make_queries.py` 규칙 1 참조).

W10 스파이크(0.9.0 판정 1)부터 추가된 것:
  --evalset 반복       평가셋 병합 — 교차언어 셋(fixtures/evalset_xling.json, 축 ko2en)을
                       동일언어 셋과 한 번에 잰다. 언어 라벨은 파일 것을 그대로 쓴다
  --latency N          단건 인코딩 지연(중앙값·p90 ms). 양자화-커널 게이트는 산출물
                       크기가 아니라 **실기기 지연·품질**로 판정한다(§1.7) — 이 수치가
                       그 지연이다. VINC_EVAL_THREADS=1(기본)로 잰 것만 프로덕션 대비 유효
  --bootstrap B        첫 모델 대비 ΔnDCG 의 짝지은 부트스트랩 95% CI. "잡음 범위를 못
                       넘으면 채택 없음" 판정 규칙의 그 잡음 범위를 수치로 만든다

W16(production-load 게이트)부터 추가된 것:
  --query-latency      평가셋 질의 전부를 **단건 query 인코딩**해 길이 구간별 p50/p95 를
                       잰다. --latency 는 청크(≤2000자, 쓰기 경로) 지연이라 사용자가
                       체감하는 질의(수십 자, 읽기 경로) 지연의 답이 아니다 — 질의
                       경로의 예산은 **상태에 따라 둘**이다(`api/server.py`
                       `_QVEC_TIMEOUT` 1.5초 = 웜 · `_QVEC_COLD_TIMEOUT` 30초 =
                       콜드스타트 한 번). 이 수치는 **웜 예산**과 비교한다 — p95 가
                       1.5초를 넘으면 웜 상태의 매 질의에서 벡터 팔이 조용히 빠지고,
                       콜드일 때만 살아나는 것은 채택 근거가 못 된다.
                       역시 VINC_EVAL_THREADS=1 로 잰 것만 프로덕션 대비 유효
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.docstore import chunk_doc                          # noqa: E402
from encoder import Encoder, _load_production_worker        # noqa: E402
from make_queries import (                                  # noqa: E402
    _CORPORA, _DOC_KO_THRESHOLD, _ROOT, _body_without_frontmatter, lang_of,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "evalset.json"

#: 질의 길이 구간(문자수) — 정본+교차언어 평가셋 118건의 실측 분포에서 자른다
#: (p25=20 · p75=42): 짧은 키워드형 / 보통 소제목형 / 긴 문장형.
_QLEN_BUCKETS = ((0, 20, "short"), (21, 45, "mid"), (46, 10 ** 9, "long"))


def load_corpus(max_chars: int = 2000, gold: set[str] | None = None,
                per_lang_docs: int = 0) -> tuple[list[str], list[str]]:
    """(청크 텍스트, 청크가 속한 doc_id) — doc_id 규칙은 `make_queries.py` 와 같다.

    `per_lang_docs` > 0 이면 언어별 문서 수를 그 값으로 줄인다 — 판정 1 의 스파이크
    규모가 "50문서·60질의"(§1.6)라 전 코퍼스(176문서)는 규모 초과이고, CPU 로 모델
    일곱을 재기엔 시간이 서너 배 든다. 선별은 결정론이다: **정답 문서(gold)는 전량
    포함**하고(빠지면 그 질의가 필연 miss — 조용한 0 점의 재발) 나머지는 정렬 순서로
    채운다. ⚠ 코퍼스가 줄면 교란 문서가 줄어 절대 점수는 부풀지만, 모델 비교는 같은
    코퍼스를 공유하므로 유효하다 — 절대치를 다른 측정과 비교하지 말 것."""
    texts, owners = [], []
    picked: dict[str, int] = {}
    for _, rel in _CORPORA:
        root = _ROOT / rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                raw = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            body = _body_without_frontmatter(raw)
            lang = lang_of(body, _DOC_KO_THRESHOLD)
            doc_id = f"eval/{lang}/{root.name}/{path.relative_to(root).as_posix()}"
            if per_lang_docs > 0 and not (gold and doc_id in gold) \
                    and picked.get(lang, 0) >= per_lang_docs:
                continue
            picked[lang] = picked.get(lang, 0) + 1
            for part in chunk_doc(body, max_chars=max_chars):
                t = (part.get("text") or "").strip()
                if t:
                    texts.append(t)
                    owners.append(doc_id)
    return texts, owners


def _ndcg_single(rank: int) -> float:
    """정답이 하나뿐이므로 이상적 DCG 는 1.0(1위)이고 nDCG = 1/log2(rank+1)."""
    return 1.0 / math.log2(rank + 1) if rank else 0.0


def _pctl(sorted_ms: list[float], p: float) -> float:
    """단순 순위 백분위(선형 보간 없음) — n 수십의 지연 표본에는 이거면 된다."""
    return sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * p))]


def query_latency(enc, queries: list[dict]) -> dict:
    """질의 전건을 단건 query 인코딩해 길이 구간별 p50/p95(ms)를 잰다.

    배치가 아니라 **한 건씩** 부르는 이유: 사이드카의 질의 임베딩이 정확히 그 모양이다
    (`_query_embedding` 은 texts 하나짜리 호출). warmup 1회는 세션 초기화 비용이라
    표본에서 뺀다 — 콜드스타트는 별도 축이다."""
    enc.encode([queries[0]["query"]], "query")          # warmup(표본 제외)
    rows: list[tuple[int, float]] = []
    for q in queries:
        t0 = time.perf_counter()
        enc.encode([q["query"]], "query")
        rows.append((len(q["query"]), (time.perf_counter() - t0) * 1000.0))
    out = {}
    for lo, hi, name in _QLEN_BUCKETS:
        ms = sorted(m for ln, m in rows if lo <= ln <= hi)
        if ms:
            out[name] = {"n": len(ms), "chars": f"{lo}-{'' if hi > 9999 else hi}",
                         "p50_ms": round(_pctl(ms, 0.5), 1),
                         "p95_ms": round(_pctl(ms, 0.95), 1)}
    ms = sorted(m for _, m in rows)
    out["all"] = {"n": len(ms), "p50_ms": round(_pctl(ms, 0.5), 1),
                  "p95_ms": round(_pctl(ms, 0.95), 1)}
    return out


def bench(model_spec: str, queries: list[dict], texts: list[str],
          owners: list[str], top_k: int, latency_n: int = 0,
          q_latency: bool = False) -> dict:
    import numpy as np

    name, _, onnx = model_spec.partition("=")
    # 라벨이 풀링·접두·토크나이저를 고르고 파일은 가중치만 준다. 둘이 어긋나도 아무
    # 것도 안 터지고 그럴듯한 표가 나오므로 여기서 막는다. 규약은 프로덕션의
    # `artifact_name` 하나이고, 그것을 명령줄에 적용한다.
    if onnx:
        want = _load_production_worker().artifact_name(name)
        if Path(onnx).name != want:
            raise SystemExit(f"🔴 라벨과 파일이 어긋난다: {name} 은 {want} 를 기대하는데 "
                             f"{Path(onnx).name} 이 왔다")
    enc = Encoder(name, onnx or None)
    label = f"{name}{' [' + Path(onnx).name + ']' if onnx else ''}"

    print(f"  {label}: 청크 {len(texts)}개 인코딩 중...", flush=True)
    t0 = time.perf_counter()
    P = np.array(enc.encode(texts, "passage"), dtype=np.float32)
    t_corpus = time.perf_counter() - t0
    t0 = time.perf_counter()
    # 질의는 **한 건씩** 인코딩한다 — 프로덕션 질의 경로가 언제나 배치 1 이라서다
    # (`api/server.py`). int8 은 배치 구성이 패딩을 바꿔 벡터를 흔든다(단건 대 배치 8
    # 코사인 0.9912 실측). 배치로 재면 프로덕션이 쓰지 않는 벡터를 재는 것이 된다.
    Q = np.array([enc.encode([q["query"]], "query")[0] for q in queries],
                 dtype=np.float32)
    t_query = time.perf_counter() - t0
    sims = Q @ P.T                       # 둘 다 L2 정규화돼 있으므로 내적 = 코사인

    # 단건 지연 프로브 — 배치 인코딩의 청크/초는 패딩 때문에 지연의 대리가 못 된다.
    # 커널 게이트는 "실기기에서 잰 지연"을 요구하므로(§1.7) 문장 하나짜리 호출을 잰다.
    # ⚠ VINC_EVAL_THREADS=1 로 잰 것만 프로덕션(워커 스레드 1) 대비 유효하다.
    lat = {}
    if latency_n > 0:
        samples = texts[:latency_n]
        times = []
        for s in samples:
            t0 = time.perf_counter()
            enc.encode([s], "passage")
            times.append((time.perf_counter() - t0) * 1000.0)
        times.sort()
        lat = {"n": len(times),
               "median_ms": round(times[len(times) // 2], 1),
               "p90_ms": round(times[int(len(times) * 0.9)], 1)}
    qlat = query_latency(enc, queries) if q_latency else {}

    qrows: list[dict] = []               # 질의 순서 정렬 유지 — 모델 간 짝지은 비교용
    for i, q in enumerate(queries):
        order = np.argsort(-sims[i])
        seen, ranked = set(), []
        for j in order:                  # 청크 순위 → 문서 순위(중복 제거, 순위 보존)
            d = owners[j]
            if d not in seen:
                seen.add(d)
                ranked.append(d)
            if len(ranked) >= top_k:
                break
        rank = ranked.index(q["doc_id"]) + 1 if q["doc_id"] in ranked else 0
        qrows.append({"lang": q.get("lang") or "?",
                      "rank": rank,
                      "recall": 1.0 if rank else 0.0,
                      "mrr": 1.0 / rank if rank else 0.0,
                      "ndcg": _ndcg_single(rank)})

    def agg(rows):
        n = len(rows) or 1
        return {"n": len(rows),
                "recall": round(sum(r["recall"] for r in rows) / n, 4),
                "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
                "ndcg": round(sum(r["ndcg"] for r in rows) / n, 4)}

    per_lang: dict[str, list[dict]] = {}
    for r in qrows:
        per_lang.setdefault(r["lang"], []).append(r)
    return {"model": label,
            "by_lang": {lg: agg(rows) for lg, rows in sorted(per_lang.items())},
            "overall": agg(qrows),
            "timing": {"corpus_s": round(t_corpus, 1), "query_s": round(t_query, 1),
                       "chunks_per_s": round(len(texts) / t_corpus, 2) if t_corpus else 0,
                       **({"latency_single": lat} if lat else {}),
                       **({"latency_query": qlat} if qlat else {})},
            "qrows": qrows}


def paired_bootstrap(results: list[dict], b: int, seed: int = 20260829) -> list[dict]:
    """모델별 nDCG 를 **같은 질의끼리 짝지어** 기준 모델(첫 --model)과의 차이를
    부트스트랩한다. 판정 규칙이 "잡음 범위를 못 넘으면 채택 없음"(§1.6)인데, 그
    잡음 범위를 감으로 두면 규칙이 규칙이 아니게 된다 — 여기서 잰다. 질의를 복원
    추출해 차이 평균의 분포를 만들고 95% CI 를 낸다. CI 가 0 을 물면 그 차이는
    이 평가셋 크기에서 잡음과 구분되지 않는다."""
    import numpy as np

    rng = np.random.default_rng(seed)
    base = results[0]
    langs = sorted({r["lang"] for r in base["qrows"]})
    out: list[dict] = []
    for res in results[1:]:
        row = {"model": res["model"], "baseline": base["model"], "by_lang": {}}
        for lg in langs + ["all"]:
            idx = [i for i, r in enumerate(base["qrows"])
                   if lg == "all" or r["lang"] == lg]
            if not idx:
                continue
            d = np.array([res["qrows"][i]["ndcg"] - base["qrows"][i]["ndcg"]
                          for i in idx])
            boots = np.array([d[rng.integers(0, len(d), len(d))].mean()
                              for _ in range(b)])
            lo, hi = np.percentile(boots, [2.5, 97.5])
            row["by_lang"][lg] = {
                "n": len(d), "delta_ndcg": round(float(d.mean()), 4),
                "ci95": [round(float(lo), 4), round(float(hi), 4)],
                "clears_noise": bool(lo > 0 or hi < 0)}
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True,
                    help="모델 id 또는 `id=onnx경로`. 여러 번 줄 수 있다. "
                         "**첫 모델이 부트스트랩의 기준선**이다")
    ap.add_argument("--evalset", action="append",
                    help="평가셋 JSON. 여러 번 주면 병합한다(언어 라벨은 파일 것을 "
                         "쓴다 — 교차언어 셋은 ko2en 처럼 축 이름을 단다). "
                         "미지정 시 fixtures/evalset.json")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--max-chars", type=int, default=2000,
                    help="청크 크기(프로덕션 기본 2000 — 부모-자식이라 이 값이 정본이다)")
    ap.add_argument("--per-lang-docs", type=int, default=0, metavar="N",
                    help="언어별 코퍼스 문서 상한(0=전량). 정답 문서는 상한과 무관하게 "
                         "전량 포함된다. 판정 1 스파이크 규모(50문서)에 맞출 때 30")
    ap.add_argument("--latency", type=int, default=0, metavar="N",
                    help="모델당 단건 인코딩 지연을 N개 문장으로 잰다(중앙값·p90 ms)")
    ap.add_argument("--query-latency", action="store_true",
                    help="평가셋 질의 전건을 단건 query 인코딩해 길이 구간별 "
                         "p50/p95 ms 를 잰다(읽기 경로 — 웜 예산 1.5초와 비교되는 수치)")
    ap.add_argument("--bootstrap", type=int, default=0, metavar="B",
                    help="첫 모델 대비 nDCG 차이의 짝지은 부트스트랩 95%% CI (B 회)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    queries: list[dict] = []
    for es in (args.evalset or [str(_FIXTURE)]):
        data = json.loads(Path(es).read_text(encoding="utf-8"))
        queries.extend(data.get("queries") or [])
    if not queries:
        print("평가셋이 비었다 — scripts/make_queries.py 를 먼저 돌릴 것", file=sys.stderr)
        return 1
    gold = {q["doc_id"] for q in queries}
    texts, owners = load_corpus(args.max_chars, gold, args.per_lang_docs)
    if not texts:
        print("코퍼스를 못 읽었다", file=sys.stderr)
        return 1
    missing = gold - set(owners)
    if missing:
        print(f"🔴 정답 문서 {len(missing)}/{len(gold)}개가 코퍼스에 없다 — 평가셋과 "
              f"코퍼스가 어긋났다(둘 다 make_queries.py 규칙을 써야 한다). "
              f"예: {sorted(missing)[:2]}", file=sys.stderr)
        return 1

    print(f"질의 {len(queries)}개 · 청크 {len(texts)}개 · top_k={args.top_k}")
    results = [bench(m, queries, texts, owners, args.top_k, args.latency,
                     args.query_latency)
               for m in args.model]
    boots = (paired_bootstrap(results, args.bootstrap)
             if args.bootstrap and len(results) > 1 else [])

    if args.json:
        # qrows 는 원자료로 JSON 에만 싣는다(표 출력에는 과하다).
        print(json.dumps({"results": results, "bootstrap": boots},
                         ensure_ascii=False, indent=2))
        return 0
    langs = sorted({lg for r in results for lg in r["by_lang"]})
    head = "".join(f"{lg + ' nDCG':>12}" for lg in langs)
    print(f"\n{'모델':<46}{head}{'전체 nDCG':>12}")
    for r in results:
        cells = "".join(f"{r['by_lang'].get(lg, {}).get('ndcg', 0):>12.3f}" for lg in langs)
        print(f"{r['model']:<46}{cells}{r['overall']['ndcg']:>12.3f}")
    if any(r.get("timing") for r in results):
        print(f"\n{'모델':<46}{'코퍼스 s':>10}{'청크/s':>8}{'단건 med ms':>12}{'p90 ms':>8}")
        for r in results:
            t = r.get("timing") or {}
            single = t.get("latency_single") or {}
            print(f"{r['model']:<46}{t.get('corpus_s', 0):>10.1f}"
                  f"{t.get('chunks_per_s', 0):>8.2f}"
                  f"{single.get('median_ms', 0):>12.1f}{single.get('p90_ms', 0):>8.1f}")
    if any((r.get("timing") or {}).get("latency_query") for r in results):
        print(f"\n질의 단건 지연 (p50/p95 ms · 웜 예산 1.5s 대비 · 콜드는 30s)")
        for r in results:
            ql = (r.get("timing") or {}).get("latency_query") or {}
            cells = "  ".join(
                f"{name} n={m['n']}: {m['p50_ms']:.0f}/{m['p95_ms']:.0f}"
                for name, m in ql.items())
            print(f"  {r['model']:<46}{cells}")
    for b in boots:
        print(f"\nΔnDCG vs {b['baseline']} — {b['model']}")
        for lg, m in b["by_lang"].items():
            verdict = "잡음 밖" if m["clears_noise"] else "잡음 안(채택 근거 아님)"
            print(f"  {lg:<6} n={m['n']:>3} Δ={m['delta_ndcg']:+.4f} "
                  f"CI95=[{m['ci95'][0]:+.4f}, {m['ci95'][1]:+.4f}] → {verdict}")
    print("\n⚠ 언어별로 읽을 것 — 섞으면 한국어 손실(전체 토큰의 82%)이 묻힌다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
