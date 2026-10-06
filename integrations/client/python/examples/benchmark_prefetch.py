"""Compare fresh sequential and concurrent prefetch; synthetic replay is explicitly labelled."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter, sleep

import httpx
from vinc_client import AsyncVincClient, VincClient, __version__


def replay():
    root = Path(__file__).resolve().parents[4]
    selected = next(r for r in json.loads((root / "agents/roles.json").read_text())["roles"]
                    if r["id"] == "concept:agent-qa")
    sizes = {d["doc_id"]: d["chars"] for d in json.loads((Path(__file__).resolve().parents[1]
        / "tests/fixtures/prefetch_document_sizes.json").read_text())["documents"]}
    documents = {}
    for doc_id in selected["props"]["reads"]:
        text = "x" * sizes[doc_id]
        documents[doc_id] = {"doc_id": doc_id, "executed": True, "verified": True,
            "text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()}

    def answer(request):
        if request.url.path.startswith("/v1/roles/"):
            return 0.014, {"found": True, "id": selected["id"], "props": selected["props"]}
        if request.url.path.startswith("/v1/documents/"):
            return 0.012, documents[request.url.path.rsplit("/", 1)[-1]]
        return 0.0475, {"status": "none"}
    def sync(request):
        delay, data = answer(request)
        sleep(delay)
        return httpx.Response(200, json=data)
    async def async_handler(request):
        delay, data = answer(request)
        await asyncio.sleep(delay)
        return httpx.Response(200, json=data)
    return httpx.MockTransport(sync), httpx.MockTransport(async_handler)


async def measure(args):
    key = "vinc_ro_" + "a" * 32 if args.synthetic else os.environ["VINC_API_KEY"]
    samples = {"sync": {1: [], 8: []}, "async": {1: [], 8: []}}
    sync_transport, async_transport = replay() if args.synthetic else (None, None)
    clients = {n: VincClient(key, space=args.space, timeout=60,
                            prefetch_concurrency=n, transport=sync_transport) for n in (1, 8)}
    async_clients = {n: AsyncVincClient(key, space=args.space, timeout=60,
                            prefetch_concurrency=n, transport=async_transport) for n in (1, 8)}
    try:
        for iteration in range(args.iterations):
            for mode in ("sync", "async"):
                # 한 쪽이 항상 먼저 달리지 않게 순서를 바꾼다. 캐시는 두 경로 모두 없다.
                for concurrency in ((1, 8) if iteration % 2 == 0 else (8, 1)):
                    start = perf_counter()
                    if mode == "sync":
                        block = clients[concurrency].prefetch(args.role, "Review role evidence.", max_chars=600000)
                    else:
                        block = await async_clients[concurrency].prefetch(
                            args.role, "Review role evidence.", max_chars=600000)
                    elapsed = perf_counter() - start
                    samples[mode][concurrency].append(elapsed)
                    print(json.dumps({"synthetic": args.synthetic, "client_version": __version__,
                        "mode": mode, "iteration": iteration, "role": args.role,
                        "concurrency": concurrency, "elapsed_s": elapsed,
                        "context_chars": len(block)}), flush=True)
    finally:
        for client in clients.values():
            client.close()
        for client in async_clients.values():
            await client.aclose()
    passed = True
    for mode, timings in samples.items():
        before, after = median(timings[1]), median(timings[8])
        passed = passed and after < before / 2
        print(json.dumps({"synthetic": args.synthetic, "mode": mode,
            "sequential_median_s": before, "concurrent_median_s": after,
            "ratio": after / before, "below_half": after < before / 2}), flush=True)
    return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--role", default="concept:agent-qa")
    parser.add_argument("--space", default="personal")
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("Use at least three paired iterations.")
    if not args.synthetic and not os.environ.get("VINC_API_KEY", "").startswith("vinc_ro_"):
        parser.error("VINC_API_KEY must contain an environment-only read-only vinc_ro_ key.")
    if args.synthetic and args.role != "concept:agent-qa":
        parser.error("Synthetic replay supports only the captured agent-qa role.")
    raise SystemExit(0 if asyncio.run(measure(args)) else 1)


if __name__ == "__main__":
    main()
