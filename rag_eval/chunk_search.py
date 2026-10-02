#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""청크 검색 평가 — 고정 스토어 하나에서 프로덕션 검색 경로를 그대로 재고, 전후를 짝으로 댄다.

`rag_eval.search` 와 같은 채점이다(청크 10행 → 문서로 접기 → k=10 에서 recall·MRR·nDCG).
다른 것은 둘이다. ① 스토어를 **락으로 고정**한다: 열기 전 바이트 해시를 대조하고, 옆에 WAL 이
있거나 질의 인코더 런타임이 저장 벡터와 다르면 거부한다(G0 러너 `evalkit.run_nodes` 와 같은
규칙). 실행은 언제나 **사본**에서 한다. ② `--baseline` 으로 앞선 결과를 주면 같은 질의끼리
짝 부트스트랩을 낸다 — "점수가 움직였는데 안 움직였다고 말하는" 머지를 막는 게 목적이다.

`--migrate-lex` 는 사본에서 v21 데이터 단계(`migrate_lex_index`)를 돌린 뒤 잰다. 이관 전의
스토어는 오늘 색인 그대로 답하므로, 이 옵션 없이 새 코드를 재면 새 색인을 잰 것이 아니다.

    python -m rag_eval.chunk_search --store full176-m3-2026-09-09 \\
        --onnx .cache/embed/baked-dispatched/bge-m3-int8-rr.onnx --out _scratch/chunk/base
    python -m rag_eval.chunk_search --store full176-m3-2026-09-09 --onnx ... \\
        --migrate-lex --baseline _scratch/chunk/base/result.json --out _scratch/chunk/p1

근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §4]]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent if ROOT.parent.name != "_wt" else ROOT.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from rag_eval import search as es            # noqa: E402
from rag_eval.evalkit import runtime         # noqa: E402

PAGE = 10


def paired(base: list[float], new: list[float], *, n: int = 2000, seed: int = 7) -> dict:
    """같은 질의끼리 차이의 평균과 95% 구간(부트스트랩). 구간이 0 을 벗어나면 움직인 것이다."""
    if len(base) != len(new) or not base:
        raise ValueError("paired: 길이가 다르거나 비었다")
    d = [b - a for a, b in zip(base, new)]
    rng = random.Random(seed)
    m = len(d)
    boot = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return {"delta": round(sum(d) / m, 4), "lo": round(boot[int(0.025 * n)], 4),
            "hi": round(boot[int(0.975 * n)], 4)}


def compare(base: dict, new: dict, metric: str = "ndcg") -> dict:
    """두 결과의 같은 셋·같은 질의를 짝짓는다. 질의 목록이 다르면 비교하지 않는다."""
    out = {}
    for name, rows in new["per_query"].items():
        b = base["per_query"].get(name)
        if b is None or [r["query"] for r in b] != [r["query"] for r in rows]:
            out[name] = {"error": "질의 목록이 다르다 — 비교하지 않는다"}
            continue
        out[name] = paired([r[metric] for r in b], [r[metric] for r in rows])
    return out


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True, help="락의 스토어 이름")
    ap.add_argument("--onnx", required=True, help="질의 인코더 아티팩트")
    ap.add_argument("--out", required=True, help="결과 디렉터리(사본도 여기 만든다)")
    ap.add_argument("--migrate-lex", action="store_true", help="사본에서 v21 색인 이관 뒤 잰다")
    ap.add_argument("--baseline", help="앞선 result.json — 짝 비교")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--allow-runtime-mismatch", action="store_true")
    a = ap.parse_args(argv)

    entry = runtime.lock_entry(a.store)
    fp = runtime.fingerprint()
    mism = runtime.runtime_mismatch(entry, fp)
    if mism and not a.allow_runtime_mismatch:
        print("질의 인코더 런타임이 저장 벡터와 다르다 — 거부:\n  " + "\n  ".join(mism), file=sys.stderr)
        return 1
    pinned = WORKSPACE / entry["path"]
    if pathlib.Path(str(pinned) + ".wal").exists():
        print(f"핀 옆에 WAL 이 있다 — 열린 적 있는 바이트다: {pinned}", file=sys.stderr)
        return 1
    sha = runtime.file_sha256(pinned)
    if sha != entry["sha256"]:
        print(f"핀 해시가 락과 다르다: {sha[:12]} != {entry['sha256'][:12]}", file=sys.stderr)
        return 1
    onnx_sha = runtime.file_sha256(pathlib.Path(a.onnx))
    if entry.get("encoder_sha256") and onnx_sha != entry["encoder_sha256"]:
        print(f"인코더 해시가 락과 다르다: {onnx_sha[:12]}", file=sys.stderr)
        return 1

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    copy = out / "store.db"
    for f in out.glob("store.db*"):
        f.unlink()
    shutil.copyfile(pinned, copy)
    copy.chmod(0o644)

    from encoder import Encoder                  # noqa: PLC0415
    from store.backend import open_store         # noqa: PLC0415
    from store.config import load_config         # noqa: PLC0415
    from api import query as q                   # noqa: PLC0415
    cfg = load_config({"data_dir": str(copy), "embedding_model": entry["encoder"]})
    store = open_store(cfg)
    if store is None:
        print("사본을 열 수 없다", file=sys.stderr)
        return 1
    header = {"store": a.store, "sha256": sha, "encoder_sha256": onnx_sha, "commit": _commit(),
              "runtime": fp, "runtime_mismatch": mism, "migrate_lex": None}
    try:
        t = time.perf_counter()
        store.provision(dim=cfg.embedding_dim)
        header["provision_s"] = round(time.perf_counter() - t, 2)
        if a.migrate_lex:
            if not hasattr(store, "migrate_lex_index"):
                print("이 코드에는 v21 이관이 없다 — --migrate-lex 를 뺄 것", file=sys.stderr)
                return 1
            header["migrate_lex"] = store.migrate_lex_index()
        header["lex_ready"] = bool(getattr(store, "lex_ready", lambda: False)())
        enc = Encoder(entry["encoder"], onnx_path=a.onnx, batch=8)
        enc.encode(["warmup"], kind="query")
        per_query: dict[str, list[dict]] = {}
        census = {"degraded": 0, "keyword_gated": 0, "queries": 0}
        for rel in entry["evalsets"]:
            name = pathlib.Path(rel).name
            qs = es.load_evalset(str(ROOT / rel))[: a.limit or None]
            rows_out = []
            for item in qs:
                qv = enc.encode([item["query"]], kind="query")[0]
                res = q.search(item["query"], query_embedding=qv, top_k=PAGE,
                               owner=entry["owner"], store=store)
                docs = es._docs_of(store, (res.get("rows") or [])[:PAGE])
                sc = es.score(docs, item["doc_id"], PAGE)
                census["queries"] += 1
                if res.get("vector_note"):
                    census["degraded"] += 1
                if res.get("keyword_gated"):
                    census["keyword_gated"] += 1
                rows_out.append({"query": item["query"], "doc_id": item["doc_id"], **sc,
                                 "keyword_gated": bool(res.get("keyword_gated")),
                                 "vector_note": res.get("vector_note") or ""})
            per_query[name] = rows_out
            print(name, len(rows_out), flush=True)
    finally:
        store.close()
    means = {name: {k: round(sum(r[k] for r in rows) / len(rows), 4)
                    for k in ("recall", "mrr", "ndcg")} for name, rows in per_query.items() if rows}
    result = {"header": header, "census": census, "means": means, "per_query": per_query}
    if a.baseline:
        base = json.loads(pathlib.Path(a.baseline).read_text(encoding="utf-8"))
        result["paired_ndcg_vs_baseline"] = compare(base, result, "ndcg")
        result["baseline"] = {"path": a.baseline, "commit": base["header"].get("commit")}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in out.glob("store.db*"):
        f.unlink()
    print(json.dumps({"means": means, "census": census,
                      "paired_ndcg_vs_baseline": result.get("paired_ndcg_vs_baseline")},
                     ensure_ascii=False, indent=1))
    if census["degraded"]:
        print(f"⚠ 의미 팔이 빠진 응답 {census['degraded']}건 — 이 실행은 완전한 측정이 아니다",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
