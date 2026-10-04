"""재구축 완료 파일에서 안전한 상태만 읽는다. 스토어를 열거나 쓰지 않는다."""
from __future__ import annotations

import json
import os
from pathlib import Path

MAX_RESULT_BYTES = 262144


def read_status(base: Path | None = None, *, enabled: bool | None = None) -> dict:
    """로그 꼬리가 사라져도 완료를 판정한다. 원문·소유자·경로는 반환하지 않는다."""
    if enabled is None:
        enabled = os.environ.get("VINC_NODE_MIRROR_REBUILD", "on") == "on"
    if not enabled:
        return {"state": "disabled"}
    base = base or Path(os.environ.get("VINC_NODE_MIRROR_PIDFILE", "/tmp/vinc-node-mirror.pid"))
    try:
        code_path = Path(str(base) + ".code")
        if not code_path.exists():
            return {"state": "pending"}
        with code_path.open("r", encoding="utf-8") as stream:
            code = stream.read(64).strip()
        # curl 실행 중에는 코드 파일이 비어 있다. 부분 본문은 읽지 않는다.
        if not code:
            return {"state": "pending"}
        if code != "200":
            return {"state": "failed", "reason": "request_failed"}
        with Path(str(base) + ".out").open("rb") as stream:
            raw = stream.read(MAX_RESULT_BYTES + 1)
        if len(raw) > MAX_RESULT_BYTES:
            return {"state": "failed", "reason": "result_over_limit"}
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("kind") != "node-mirror.rebuild":
            return {"state": "failed", "reason": "invalid_result"}
        if result.get("executed") is not True:
            return {"state": "failed", "reason": "not_executed"}
        if result.get("index_ok") is not True:
            return {"state": "failed", "reason": "index_unavailable"}
        return {"state": "complete", "index_ok": True}
    except (OSError, ValueError):
        return {"state": "failed", "reason": "unreadable_result"}
