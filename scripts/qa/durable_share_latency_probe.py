"""Measure generation publication on synthetic bytes using the resident hub runtime.

This measures persistence mechanics, not complete HTTP write latency. It opens no
database, reads no operating file, and changes no production durability setting.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path.cwd()))
from store import durable_generation as dg


def resource_limits(root: Path = Path('/sys/fs/cgroup')) -> dict:
    """ACA currently exposes v1; developer and newer hosts may expose v2."""
    values = {'cpu_max': None, 'memory_max': None}
    for key, filename in (('cpu_max', 'cpu.max'), ('memory_max', 'memory.max')):
        path = root / filename
        if path.is_file():
            values[key] = path.read_text().strip()
    if values['cpu_max'] is None:
        for directory in ('cpu', 'cpu,cpuacct'):
            quota = root / directory / 'cpu.cfs_quota_us'
            period = root / directory / 'cpu.cfs_period_us'
            if quota.is_file() and period.is_file():
                values['cpu_max'] = quota.read_text().strip() + ' ' + period.read_text().strip()
                break
    if values['memory_max'] is None:
        path = root / 'memory' / 'memory.limit_in_bytes'
        if path.is_file():
            values['memory_max'] = path.read_text().strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persistent-root", type=Path, required=True)
    parser.add_argument("--base-bytes", type=int, default=1089253376)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    if not 16 * 1024**2 <= args.base_bytes <= 1100 * 1024**2:
        parser.error("base size must be between 16 and 1100 MiB")
    if not 2 <= args.samples <= 30:
        parser.error("samples must be between 2 and 30")
    share = args.persistent_root.resolve()
    if not args.persistent_root.is_absolute() or not share.parent.is_dir() or share.exists():
        parser.error("a fresh directory below an existing mount is required")
    if not share.name.startswith("vin82-latency-"):
        parser.error("the synthetic directory must use the vin82-latency prefix")
    local = Path(tempfile.mkdtemp(prefix="vin82-latency-"))
    records = []
    result = {"passed": False, "base_bytes": args.base_bytes,
              "samples": args.samples, "measurement": "synthetic generation publication",
              "complete_http_write_latency": False, "operating_database_opened": False,
              "cpu_max": None, "memory_max": None, "cleanup_errors": []}
    result.update(resource_limits())
    share_created = False
    digest, copy = dg._digest, dg._copy_synced
    counters = {}

    def measured_digest(path):
        path = Path(path)
        kind = "share_validation" if path.is_relative_to(share) else "local_validation"
        start = time.perf_counter()
        value = digest(path)
        counters[kind + "_bytes"] = counters.get(kind + "_bytes", 0) + value["size"]
        counters[kind + "_seconds"] = counters.get(kind + "_seconds", 0) + time.perf_counter() - start
        return value

    def measured_copy(source, destination):
        start = time.perf_counter()
        value = copy(source, destination)
        counters["uploaded_bytes"] = counters.get("uploaded_bytes", 0) + value["size"]
        counters["copy_seconds"] = counters.get("copy_seconds", 0) + time.perf_counter() - start
        return value

    try:
        if hasattr(signal, "SIGALRM"):
            def expired(_signal, _frame):
                raise TimeoutError("bounded synthetic measurement exceeded 1200 seconds")
            signal.signal(signal.SIGALRM, expired)
            signal.alarm(1200)
        if shutil.disk_usage(local).free < args.base_bytes + 128 * 1024**2:
            raise RuntimeError("insufficient ephemeral disk for the bounded synthetic base")
        base = local / "db"
        block = os.urandom(1024**2)
        with base.open("xb") as out:
            remaining = args.base_bytes
            while remaining:
                piece = block[:min(len(block), remaining)]
                out.write(piece)
                remaining -= len(piece)
        wal = Path(str(base) + ".wal")
        wal.write_bytes(block)
        share.mkdir(exist_ok=False)
        share_created = True
        dg._digest, dg._copy_synced = measured_digest, measured_copy
        counters = {}
        start = time.perf_counter()
        dg.publish(share, base, wal=wal, sequence=1)
        records.append({"kind": "full", "seconds": time.perf_counter() - start, **counters})
        print(json.dumps({"progress": records[-1]}), flush=True)
        for index in range(args.samples):
            with wal.open("ab") as out:
                out.write(block[:65536])
            counters = {}
            start = time.perf_counter()
            dg.publish_wal(share, base, sequence=index + 2)
            elapsed = time.perf_counter() - start
            dg.prune(share, keep=2)
            records.append({"kind": "wal", "seconds": elapsed, **counters})
            print(json.dumps({"progress": records[-1]}), flush=True)
        durations = sorted(row["seconds"] for row in records if row["kind"] == "wal")
        result.update(passed=True, trials=records, wal_p50_seconds=statistics.median(durations),
                      wal_p95_seconds=durations[math.ceil(len(durations) * .95) - 1],
                      uploaded_bytes=sum(row.get("uploaded_bytes", 0) for row in records),
                      share_validation_bytes=sum(row.get("share_validation_bytes", 0) for row in records))
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}: {exc}", trials=records)
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        dg._digest, dg._copy_synced = digest, copy
        # 이 실행이 직접 만든 두 합성 폴더만 지운다. 원본이나 다른 실행의 폴더는 대상이 아니다.
        for path in ([share] if share_created else []) + [local]:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                result["cleanup_errors"].append(f"{type(exc).__name__}: {exc}")
        if result["cleanup_errors"]:
            result["passed"] = False
    print("VIN82_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
