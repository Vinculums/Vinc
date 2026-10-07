"""VIN-397 prefetch 지연 벤치 — 순차/동시 교대 측정, 어디서 재든 같은 프로토콜.

프로토콜(2026-10-06 기록과 같다): `agent-qa`, 작업 "Review role evidence.", max_chars 600000,
워밍업 4회(버림), 모드(sync·async)마다 순차(concurrency 1)/동시(concurrency 8) N쌍 교대.
비율과 **절대값**(동시 중앙값 ≤ 1.7 s, 결정 D3)을 둘 다 낸다. 비율은 측정 클라이언트의
왕복 시간에 따라 뒤집히므로(허브 가까운 PC 에서 1.09, 190 ms 떨어진 곳에서 0.39) 이 스크립트는
GET /v1/me 왕복도 같이 재서 결과에 남긴다. 근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]

키는 env `VINC_RO`(읽기 전용)에서만 읽고, 출력 JSON 에는 시간·길이·해시·팔 상태만 남긴다.
렌더 본문의 "vector"/"note" 같은 단어는 문서 내용이라 건강 판정에 못 쓴다 → run 앞뒤의
search 프로브(arms, vector_note)로 본다.

사용: python scripts/bench_prefetch.py --pairs 14 --out result.json --label after-p2
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

from vinc_client import AsyncVincClient, VincClient

ROLE, TASK, MAX_CHARS = "agent-qa", "Review role evidence.", 600_000
TARGET_CONCURRENT_S = 1.7          # 결정 D3


def _key() -> str:
    k = os.environ.get("VINC_RO")
    if not k:
        sys.exit("VINC_RO is not set (read-only hub key, env only)")
    return k


def _health(text: str) -> dict:
    return {"chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()[:16]}


def _probe(client: VincClient) -> dict:
    s = client.search(f"{TASK}\nRole topic: vinc/agent/roles", limit=5)
    return {"arms": s.get("arms"), "vector_note": s.get("vector_note"),
            "vector_rebuilding": s.get("vector_rebuilding"), "rows": len(s.get("rows", []))}


def _round_trip(client: VincClient, n: int = 12) -> dict:
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        client._http.get("/v1/me")
        xs.append(time.perf_counter() - t)
    xs = xs[2:]                                   # 처음 둘은 연결 수립
    return {"median_s": statistics.median(xs), "min_s": min(xs), "max_s": max(xs), "n": len(xs)}


def run_sync(pairs: int, warmup: int) -> dict:
    key = _key()
    seq, con = VincClient(key, prefetch_concurrency=1), VincClient(key, prefetch_concurrency=8)
    rows = []

    def one(c, warm):
        t = time.perf_counter()
        out = c.prefetch(ROLE, TASK, max_chars=MAX_CHARS)
        rows.append({"warmup": warm, "mode": "concurrent" if c is con else "sequential",
                     "s": time.perf_counter() - t, **_health(out)})

    for i in range(warmup):
        one(con if i % 2 else seq, True)
    for i in range(pairs):
        for c in ((seq, con) if i % 2 == 0 else (con, seq)):
            one(c, False)
    return {"client": "sync", "rows": rows}


async def run_async(pairs: int, warmup: int) -> dict:
    key = _key()
    seq = AsyncVincClient(key, prefetch_concurrency=1)
    con = AsyncVincClient(key, prefetch_concurrency=8)
    rows = []

    async def one(c, warm):
        t = time.perf_counter()
        out = await c.prefetch(ROLE, TASK, max_chars=MAX_CHARS)
        rows.append({"warmup": warm, "mode": "concurrent" if c is con else "sequential",
                     "s": time.perf_counter() - t, **_health(out)})

    for i in range(warmup):
        await one(con if i % 2 else seq, True)
    for i in range(pairs):
        for c in ((seq, con) if i % 2 == 0 else (con, seq)):
            await one(c, False)
    return {"client": "async", "rows": rows}


def summarize(block: dict) -> dict:
    kept = [r for r in block["rows"] if not r["warmup"]]
    out = {"client": block["client"], "retained": len(kept)}
    for mode in ("sequential", "concurrent"):
        hs = [r for r in kept if r["mode"] == mode]
        xs = sorted(r["s"] for r in hs)
        out[mode] = {"n": len(xs), "median_s": statistics.median(xs),
                     "p95_s": xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))],
                     "distinct_hashes": len({r["sha256"] for r in hs}),
                     "chars": sorted({r["chars"] for r in hs})}
    out["ratio"] = out["concurrent"]["median_s"] / out["sequential"]["median_s"]
    out["below_half"] = out["ratio"] < 0.5
    out["concurrent_at_or_below_target"] = out["concurrent"]["median_s"] <= TARGET_CONCURRENT_S
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=14)
    ap.add_argument("--warmup", type=int, default=4)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--only", choices=("sync", "async"), default=None)
    a = ap.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    probe_client = VincClient(_key())
    probe_before, rtt = _probe(probe_client), _round_trip(probe_client)
    blocks = []
    if a.only in (None, "sync"):
        blocks.append(run_sync(a.pairs, a.warmup))
    if a.only in (None, "async"):
        blocks.append(asyncio.run(run_async(a.pairs, a.warmup)))
    result = {"label": a.label, "started_utc": started,
              "finished_utc": datetime.now(timezone.utc).isoformat(),
              "client_version": importlib.metadata.version("vinc-client"),
              "base_url": probe_client.base_url, "role": ROLE, "task": TASK,
              "max_chars": MAX_CHARS, "pairs": a.pairs, "warmup": a.warmup,
              "round_trip_get_me": rtt, "probe_before": probe_before,
              "probe_after": _probe(probe_client),
              "summary": [summarize(b) for b in blocks], "blocks": blocks}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"client {result['client_version']}  round trip GET /v1/me median {rtt['median_s']:.3f} s")
    print("probe before", probe_before, "after", result["probe_after"])
    for s in result["summary"]:
        print(f"{s['client']}: sequential {s['sequential']['median_s']:.4f} s, "
              f"concurrent {s['concurrent']['median_s']:.4f} s, ratio {s['ratio']:.4f}, "
              f"<0.5 {s['below_half']}, <={TARGET_CONCURRENT_S}s {s['concurrent_at_or_below_target']}, "
              f"chars {s['sequential']['chars']}/{s['concurrent']['chars']}")


if __name__ == "__main__":
    main()
