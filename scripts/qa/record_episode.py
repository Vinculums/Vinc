#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write QA run results to the graph as episode + failure records (via MCP bridge)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: record_episode.py <run_dir>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    results = json.loads((run_dir / "results_structured.json").read_text(encoding="utf-8"))
    run_id = meta["run_id"]
    episode_id = f"episode:qa-run-{run_id}"
    summary = (
        f"QA {meta['batch_ids']}: "
        + ", ".join(f"{k}={v}" for k, v in meta["outcomes"].items() if v)
    )
    print(json.dumps({
        "episode_id": episode_id,
        "summary": summary,
        "props": {
            "task_type": meta.get("task_type"),
            "batch_ids": meta.get("batch_ids"),
            "model": "cloud-agent",
            "host": "linux-ci",
            "browser_profile": meta.get("browser_profile"),
            "env_gate": meta.get("env_gate", "not_run"),
            "outcome_counts": meta.get("outcomes"),
            "failure_buckets": meta.get("failure_buckets"),
            "run_dir": str(run_dir),
        },
        "failures": [
            {"record_id": f"record:qa-failure-{r['tc_id']}-{run_id}",
             "tc_id": r["tc_id"], "bucket_id": r.get("bucket_id"),
             "evidence": r.get("evidence")}
            for r in results if r["outcome"] not in ("Pass", "Skip")
        ],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
