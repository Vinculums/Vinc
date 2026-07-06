# -*- coding: utf-8 -*-
"""도구 호출 로그 — [[vinc.mcp.skeleton]] §6. `data_dir/logs/mcp-audit.jsonl` 에
1줄/건 append. 관찰 패널(후속 UI)이 이 파일을 tail·집계한다.

`args_digest` 는 원문 미포함 — 개수·domain 등 요약만(로그 비대·민감정보 회피, §6)."""
from __future__ import annotations

import json
from pathlib import Path


def _log_path(data_dir: str) -> Path:
    return Path(data_dir) / "logs" / "mcp-audit.jsonl"


def digest_args(tool: str, args: dict) -> dict:
    """원문 미포함 요약(§6) — vinc_ingest 는 개수·domain 목록, 나머지는 키 존재 여부만."""
    if tool == "vinc_ingest":
        nodes = args.get("nodes") or []
        edges = args.get("edges") or []
        domains = sorted({n.get("domain") for n in nodes if isinstance(n, dict) and n.get("domain")})
        return {"nodes": len(nodes), "edges": len(edges), "domains": domains}
    return {"keys": sorted(k for k, v in args.items() if v is not None)}


def record(data_dir: str, *, tool: str, source: str, session: str, args: dict,
           result: dict, warnings: list | None, duration_ms: float, ts: str) -> None:
    path = _log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": ts, "tool": tool, "source": source, "session": session,
        "args_digest": digest_args(tool, args), "result": result,
        "warnings": list(warnings or []), "duration_ms": round(duration_ms, 1),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
