#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""모델 비교 — **정확 코사인** 순위로 임베딩 모델을 언어별로 잰다.

`eval_search.py` 와 무엇이 다른가: 저쪽은 **프로덕션 경로**(FTS·HNSW 근사·RRF)를 재고
이쪽은 **모델 자체**를 잰다. 모델을 비교할 때 HNSW 근사를 섞으면 인덱스 차이가 모델
차이로 섞여 들어온다. 그래서 여기서는 인덱스를 쓰지 않고 전 청크와 정확 코사인을 잰다
([[vinc.plan.embedding-retrieval]] 부록 B §5).

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
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api.docstore import chunk_doc                          # noqa: E402
from encoder import Encoder                                 # noqa: E402
from make_queries import (                                  # noqa: E402
    _CORPORA, _DOC_KO_THRESHOLD, _ROOT, _body_without_frontmatter, lang_of,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "evalset.json"


def load_corpus(max_chars: int = 2000) -> tuple[list[str], list[str]]:
    """(청크 텍스트, 청크가 속한 doc_id) — doc_id 규칙은 `make_queries.py` 와 같다."""
    texts, owners = [], []
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
            for part in chunk_doc(body, max_chars=max_chars):
                t = (part.get("text") or "").strip()
                if t:
                    texts.append(t)
                    owners.append(doc_id)
    return texts, owners


def _ndcg_single(rank: int) -> float:
    """정답이 하나뿐이므로 이상적 DCG 는 1.0(1위)이고 nDCG = 1/log2(rank+1)."""
    return 1.0 / math.log2(rank + 1) if rank else 0.0


def bench(model_spec: str, queries: list[dict], texts: list[str],
          owners: list[str], top_k: int) -> dict:
    import numpy as np

    name, _, onnx = model_spec.partition("=")
    enc = Encoder(name, onnx or None)
    label = f"{name}{' [' + Path(onnx).name + ']' if onnx else ''}"

    print(f"  {label}: 청크 {len(texts)}개 인코딩 중...", flush=True)
    P = np.array(enc.encode(texts, "passage"), dtype=np.float32)
    Q = np.array(enc.encode([q["query"] for q in queries], "query"), dtype=np.float32)
    sims = Q @ P.T                       # 둘 다 L2 정규화돼 있으므로 내적 = 코사인

    per_lang: dict[str, list[dict]] = {}
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
        per_lang.setdefault(q.get("lang") or "?", []).append({
            "recall": 1.0 if rank else 0.0,
            "mrr": 1.0 / rank if rank else 0.0,
            "ndcg": _ndcg_single(rank)})

    def agg(rows):
        n = len(rows) or 1
        return {"n": len(rows),
                "recall": round(sum(r["recall"] for r in rows) / n, 4),
                "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
                "ndcg": round(sum(r["ndcg"] for r in rows) / n, 4)}

    allrows = [r for rows in per_lang.values() for r in rows]
    return {"model": label,
            "by_lang": {lg: agg(rows) for lg, rows in sorted(per_lang.items())},
            "overall": agg(allrows)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", required=True,
                    help="모델 id 또는 `id=onnx경로`. 여러 번 줄 수 있다")
    ap.add_argument("--evalset", default=str(_FIXTURE))
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--max-chars", type=int, default=2000,
                    help="청크 크기(프로덕션 기본 2000 — 부모-자식이라 이 값이 정본이다)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.evalset).read_text(encoding="utf-8"))
    queries = data.get("queries") or []
    if not queries:
        print("평가셋이 비었다 — scripts/make_queries.py 를 먼저 돌릴 것", file=sys.stderr)
        return 1
    texts, owners = load_corpus(args.max_chars)
    if not texts:
        print("코퍼스를 못 읽었다", file=sys.stderr)
        return 1

    gold = {q["doc_id"] for q in queries}
    missing = gold - set(owners)
    if missing:
        print(f"🔴 정답 문서 {len(missing)}/{len(gold)}개가 코퍼스에 없다 — 평가셋과 "
              f"코퍼스가 어긋났다(둘 다 make_queries.py 규칙을 써야 한다). "
              f"예: {sorted(missing)[:2]}", file=sys.stderr)
        return 1

    print(f"질의 {len(queries)}개 · 청크 {len(texts)}개 · top_k={args.top_k}")
    results = [bench(m, queries, texts, owners, args.top_k) for m in args.model]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    langs = sorted({lg for r in results for lg in r["by_lang"]})
    head = "".join(f"{lg + ' nDCG':>12}" for lg in langs)
    print(f"\n{'모델':<46}{head}{'전체 nDCG':>12}")
    for r in results:
        cells = "".join(f"{r['by_lang'].get(lg, {}).get('ndcg', 0):>12.3f}" for lg in langs)
        print(f"{r['model']:<46}{cells}{r['overall']['ndcg']:>12.3f}")
    print("\n⚠ 언어별로 읽을 것 — 섞으면 한국어 손실(전체 토큰의 82%)이 묻힌다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
