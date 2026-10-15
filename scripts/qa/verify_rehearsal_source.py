"""승인된 사본 실행 전에 정확한 소스 커밋의 두 검증 런을 확인한다.

근거: [[vinc.plan.large-topic-ownership-conversion-2026-10-14]] G6.
"""
import json
from pathlib import Path
import re
import sys

REQUIRED = (".github/workflows/ci.yml", ".github/workflows/topic-copy-rehearsal.yml")


def verify(payload, sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid_source_commit")
    runs = payload.get("workflow_runs", [])
    if payload.get("total_count") != len(runs):
        raise ValueError("incomplete_source_checks")
    evidence = {}
    for path in REQUIRED:
        candidates = [run for run in runs if run.get("head_sha") == sha and run.get("path") == path
                      and run.get("event") in ("push", "pull_request")]
        if not candidates:
            raise ValueError("source_check_missing")
        latest = max(candidates, key=lambda run: run["id"])
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise ValueError("source_check_not_successful")
        evidence[path] = latest["id"]
    return evidence


if __name__ == "__main__":
    evidence = verify(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), sys.argv[2])
    print(json.dumps({"source_checks": evidence}, sort_keys=True))
