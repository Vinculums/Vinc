#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검색 품질 평가 — FTS · 벡터 · RRF 하이브리드를 같은 질의로 비교한다.

**왜 필요한가.** 이 리포에는 검색 평가셋이 없었다. 그래서 "벡터 검색이 FTS 보다 나은가",
"모델을 바꾸면 나아지는가" 를 **누구도 말할 수 없었다**. 실제로 임베딩은 오래
write-only 였는데(호출부가 query_embedding 을 안 넘겼다) 그 사실조차 품질 저하로는
드러나지 않았다. 측정 수단이 없으면 개선도 회귀도 보이지 않는다.

이 도구는 정답 라벨이 없는 상태에서 **부트스트랩 평가셋**을 만들고 세 조건을 잰다.

    조건            어떻게
    fts             q.search(query)                      — 현행 사용자 경험
    vector          store.vector_search(query_vec)        — 의미 팔 단독
    rrf             q.search(query, query_embedding=...)  — 실제 하이브리드 경로

⚠ **질의 생성 방식의 편향을 알고 써야 한다.** 기본(`--from title`)은 문서 제목을 질의로
쓰고 그 문서의 청크를 정답으로 본다. 제목 어휘는 본문에도 자주 나오므로 이 방식은
**어휘 매칭(FTS)에 유리하다** — 즉 벡터의 이점을 **과소평가**한다. 보수적 편향이라
"이 조건에서도 벡터가 이긴다"면 신뢰할 수 있고, 반대로 벡터가 지더라도 그것만으로
벡터가 쓸모없다고 결론 내릴 수는 없다. 사람이 쓴 질의를 `--queries` 로 넣으면 그 편향이
사라진다(권장 — 20~50개면 충분하다).

벡터 조건은 **임베더가 있어야** 돈다. 사이드카에는 모델이 없으므로 워커의 루프백 창구
(`VINC_EMBED_ENCODE`)를 쓴다. 없으면 벡터·RRF 조건을 **조용히 빼지 않고** 그렇다고 적는다.

사용:
    # 부트스트랩(제목 질의) — 편향 있음, 기준선 확보용
    python scripts/eval_search.py --data ~/.vinc/db --owner u

    # 사람이 쓴 질의로 — 권장. JSONL {"query": "...", "doc_id": "..."} 한 줄에 하나
    python scripts/eval_search.py --data ~/.vinc/db --owner u --queries eval.jsonl

    # 벡터 조건까지
    VINC_EMBED_ENCODE=http://127.0.0.1:8791 python scripts/eval_search.py --data ... --owner u
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q            # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402


# ── 지표 ────────────────────────────────────────────────────────────────────
def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def score(ranked_doc_ids: list[str], truth: str, k: int) -> dict:
    """정답 문서가 상위 k 안 어디에 있었나. 정답이 하나뿐인 설정이라
    nDCG 의 이상적 DCG 는 1.0(1위) 이고, 그때 nDCG = 1/log2(rank+1) 이 된다."""
    top = ranked_doc_ids[:k]
    hit = truth in top
    rank = top.index(truth) + 1 if hit else 0
    return {
        "recall": 1.0 if hit else 0.0,
        "mrr": 1.0 / rank if rank else 0.0,
        "ndcg": _dcg([1.0 if d == truth else 0.0 for d in top]) if hit else 0.0,
    }


def _mean(rows: list[dict], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


# ── 임베더(워커 인코딩 창구) ────────────────────────────────────────────────
def encode_query(text: str, url: str, timeout: float = 20.0) -> list[float] | None:
    """`/encode` 클라이언트는 리포에 정확히 셋이다 — 워커 `_RemoteEncoder` ·
    사이드카 `_query_embedding` · 여기. 창구가 루프백을 벗어나며 인증이 붙었을 때
    (vinc-encode 분리, 2026-08-07) 셋 중 둘만 고쳐져 질의 임베딩이 전부 401 이었다.
    **클라이언트를 새로 만들면 토큰부터 실을 것** — 없으면 헤더를 붙이지 않는다
    (루프백 창구는 무토큰이라 빈 헤더가 오히려 401 사유가 된다)."""
    body = json.dumps({"texts": [text], "kind": "query"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    tok = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url.rstrip("/") + "/encode", data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        vecs = (json.load(resp) or {}).get("vectors") or []
    return list(vecs[0]) if vecs else None


# ── 평가셋 ──────────────────────────────────────────────────────────────────
def bootstrap_queries(store, owner: str | None, limit: int) -> list[dict]:
    """문서 제목을 질의로 쓰는 부트스트랩(편향은 모듈 docstring 참조)."""
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title "
        f"ORDER BY d.doc_id LIMIT $lim",
        {"o": owner, "lim": int(limit)} if owner else {"lim": int(limit)}) or []
    return [{"query": (r.get("title") or "").strip(), "doc_id": r["doc_id"]}
            for r in rows if (r.get("title") or "").strip()]


def load_queries(path: str) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        o = json.loads(line)
        if o.get("query") and o.get("doc_id"):
            out.append({"query": str(o["query"]), "doc_id": str(o["doc_id"])})
    return out


def load_evalset(path: str) -> list[dict]:
    """정본 평가셋(`scripts/fixtures/evalset.json`) — `make_queries.py` 산출물.

    JSONL(`--queries`)과 달리 생성 규칙·코퍼스·집계까지 함께 들고 있어, 어떤 조건에서
    만든 질의인지가 파일 하나로 확인된다. 그게 이 파일이 존재하는 이유다(E1)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"query": str(q["query"]), "doc_id": str(q["doc_id"]),
             "lang": q.get("lang")}
            for q in (data.get("queries") or []) if q.get("query") and q.get("doc_id")]


def assert_gold_present(store, qs: list[dict], owner: str | None) -> None:
    """정답 문서가 **이 스토어에 실제로 있는지** 먼저 본다.

    🔴 없으면 즉시 세운다. 평가셋의 doc_id 는 `seed_bilingual.py` 가 심은 코퍼스를
    가리키므로, 다른 스토어에 대고 돌리면 전 질의가 miss 가 되어 Recall 0.000 이
    **정상 출력처럼** 나온다. 이 리포가 반복해 당한 실패 양식(기동 정상·수치 정상 모양·
    내용만 틀림)이 평가 도구에서 재현되는 것이라, 조용한 0 점 대신 오류로 끝낸다."""
    want = {q["doc_id"] for q in qs}
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS id",
                     {"o": owner} if owner else None) or []
    have = {r["id"] for r in rows}
    hit = want & have
    if not hit:
        raise SystemExit(
            f"평가셋의 정답 문서가 이 스토어에 하나도 없다(정답 {len(want)}개, "
            f"스토어 문서 {len(have)}개). scripts/seed_bilingual.py 로 코퍼스를 먼저 "
            f"심거나, --owner 를 확인할 것. 이대로 돌리면 전부 miss 라 Recall 0.000 이 "
            f"정상 결과처럼 보인다.")
    if len(hit) < len(want):
        print(f"⚠ 정답 문서 {len(want) - len(hit)}/{len(want)}개가 스토어에 없다 — "
              f"그 질의는 필연적으로 miss 다(부분 시딩으로 보인다).", file=sys.stderr)


def lang_of(text: str) -> str:
    """질의의 문자 계열. 언어별로 갈라 봐야 토큰 청킹·다국어 모델의 효과가 보인다
    (영어는 512 초과 손실이 전체 토큰의 5.7% 인데 한국어는 82% 다 — 섞어 재면 묻힌다)."""
    ko = sum(1 for c in text if "가" <= c <= "힣")
    cjk = sum(1 for c in text if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    n = max(1, len(text))
    if ko / n > 0.15:
        return "ko"
    if cjk / n > 0.15:
        return "cjk"
    return "en"


# ── 실행 ────────────────────────────────────────────────────────────────────
def _docs_of(store, rows: list[dict]) -> list[str]:
    """검색 결과(청크) → 문서 id 순서(중복 제거, 순위 보존)."""
    out, seen = [], set()
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        got = store.run("MATCH (c:Chunk) WHERE c.chunk_id = $i RETURN c.doc_id AS d",
                        {"i": cid}) or []
        d = got[0].get("d") if got else None
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로(미지정 시 기본 해석)")
    ap.add_argument("--owner")
    ap.add_argument("--queries", help="사람이 쓴 질의 JSONL")
    ap.add_argument("--evalset", nargs="?", const=str(
        Path(__file__).resolve().parent / "fixtures" / "evalset.json"),
        help="정본 평가셋 JSON(권장). 값 없이 주면 scripts/fixtures/evalset.json")
    ap.add_argument("--limit", type=int, default=50, help="부트스트랩 질의 수")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data} if args.data else None)
    store = open_store(cfg)
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 1

    if args.evalset:
        qs, src = load_evalset(args.evalset), args.evalset
    elif args.queries:
        qs, src = load_queries(args.queries), args.queries
    else:
        qs = bootstrap_queries(store, args.owner, args.limit)
        src = f"bootstrap(title, n={len(qs)})"
    if not qs:
        print("질의가 없다 — 코퍼스가 비었거나 제목이 없다", file=sys.stderr)
        return 1
    # 라벨이 있는 평가셋은 정답 실존을 먼저 확인한다(조용한 0 점 금지).
    if args.evalset or args.queries:
        assert_gold_present(store, qs, args.owner)

    encode_url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
    vec_note = ""
    if not encode_url:
        vec_note = ("임베더 없음(VINC_EMBED_ENCODE 미설정) — 벡터·RRF 조건 미측정. "
                    "워커의 인코딩 창구를 띄우고 그 주소를 주면 잰다.")

    per: dict[str, list[dict]] = {"fts": [], "vector": [], "rrf": []}
    by_lang: dict[str, dict[str, list[dict]]] = {}
    failures = 0

    for item in qs:
        text, truth = item["query"], item["doc_id"]
        lang = lang_of(text)
        by_lang.setdefault(lang, {"fts": [], "vector": [], "rrf": []})

        qvec = None
        if encode_url:
            try:
                qvec = encode_query(text, encode_url)
            except Exception as exc:
                failures += 1
                if not vec_note:
                    vec_note = f"임베딩 실패 {failures}건 — 마지막: {type(exc).__name__}"

        res = q.search(text, top_k=args.top_k, owner=args.owner, cfg=cfg, store=store)
        # 🔴 백엔드가 실패를 말했으면 **그것을 측정값으로 바꾸지 않는다.** `search` 는
        # index_missing·extension_unavailable 같은 사유를 `error` 로 정직하게 싣는데,
        # 그걸 무시하면 전 질의가 0 행이 되어 "Recall 0.000" 이라는 **정상 모양의 표**가
        # 나온다. 검색이 나쁜 것과 검색이 죽은 것은 다른 사실이고, 이 리포가 반복해서
        # 당한 실패가 정확히 그 둘을 못 가린 것이다(무음 강등 금지).
        if res.get("error"):
            raise SystemExit(
                f"검색 백엔드가 실패를 보고했다 — 측정이 성립하지 않는다.\n"
                f"  질의: {text!r}\n  사유: {res['error']}\n"
                f"이 상태로 나온 0.000 은 품질이 아니라 고장이다. 원인을 먼저 없앨 것.")
        s = score(_docs_of(store, res.get("rows") or []), truth, args.top_k)
        per["fts"].append(s)
        by_lang[lang]["fts"].append(s)

        if qvec is not None:
            try:
                vrows = store.vector_search(qvec, args.top_k, None)
                s = score(_docs_of(store, vrows), truth, args.top_k)
                per["vector"].append(s)
                by_lang[lang]["vector"].append(s)
            except Exception:
                pass
            res2 = q.search(text, query_embedding=qvec, top_k=args.top_k,
                            owner=args.owner, cfg=cfg, store=store)
            s = score(_docs_of(store, res2.get("rows") or []), truth, args.top_k)
            per["rrf"].append(s)
            by_lang[lang]["rrf"].append(s)

    out = {
        "kind": "eval.search",
        "queries": len(qs),
        "source": src,
        "top_k": args.top_k,
        "note": vec_note,
        "overall": {c: {"n": len(v), "recall": round(_mean(v, "recall"), 4),
                        "mrr": round(_mean(v, "mrr"), 4), "ndcg": round(_mean(v, "ndcg"), 4)}
                    for c, v in per.items() if v},
        "by_lang": {lg: {c: {"n": len(v), "recall": round(_mean(v, "recall"), 4),
                             "mrr": round(_mean(v, "mrr"), 4),
                             "ndcg": round(_mean(v, "ndcg"), 4)}
                         for c, v in conds.items() if v}
                    for lg, conds in by_lang.items()},
    }
    store.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"질의 {out['queries']}개 · 출처 {src} · top_k={args.top_k}")
    if vec_note:
        print(f"⚠ {vec_note}")
    print(f"\n{'조건':<10}{'n':>5}{'Recall@k':>11}{'MRR':>9}{'nDCG':>9}")
    for c, m in out["overall"].items():
        print(f"{c:<10}{m['n']:>5}{m['recall']:>11.3f}{m['mrr']:>9.3f}{m['ndcg']:>9.3f}")
    if len(out["by_lang"]) > 1:
        print("\n언어별 (섞어 재면 CJK 손실이 묻힌다)")
        for lg, conds in sorted(out["by_lang"].items()):
            for c, m in conds.items():
                print(f"  {lg:<4}{c:<10}{m['n']:>5}{m['recall']:>11.3f}"
                      f"{m['mrr']:>9.3f}{m['ndcg']:>9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
