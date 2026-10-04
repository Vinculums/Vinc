"""완료 상태를 한정된 시간 동안 관측한다. 재구축 요청은 보내지 않는다."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def observe(url: str, *, timeout: float = 720, interval: float = 20) -> int:
    """완료면 즉시 종료한다. 실패·꺼짐·미확인은 각각 고정 문구로 보고한다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            remaining = max(1, deadline - time.monotonic())
            with urllib.request.urlopen(url, timeout=min(20, remaining)) as response:
                data = json.loads(response.read(262145))
            status = data.get("node_mirror") if isinstance(data, dict) else None
            if isinstance(status, dict):
                state = status.get("state")
                if state == "complete" and status.get("index_ok") is True:
                    print("node-mirror: complete; vector index ready")
                    return 0
                if state == "disabled":
                    print("::notice::node-mirror rebuild is disabled")
                    return 0
                if state == "failed":
                    print("::warning::node-mirror rebuild did not complete successfully")
                    return 1
                if state != "pending":
                    print("::warning::node-mirror completion status invalid")
                    return 1
            else:
                print("::warning::node-mirror completion status unavailable")
                return 1
        except (urllib.error.URLError, TimeoutError, ValueError):
            print("::warning::node-mirror health request failed; result unconfirmed")
            return 1
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    print("::warning::node-mirror completion was not observed within the deadline")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://mcp.vincs.io/health")
    args = parser.parse_args()
    raise SystemExit(observe(args.url))
