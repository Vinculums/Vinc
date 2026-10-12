"""계정 읽기 경로로 실제 roots가 ready인지 검사한다. 로그에는 상태와 개수만 남긴다."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = "https://vincs.io/api/graph"


class VerificationError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read(path: str, key: str, timeout: float) -> dict:
    request = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + key,
                                     "Cache-Control": "no-store"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(64 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise VerificationError(f"account read returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise VerificationError("account read could not complete") from None
    if len(data) > 64 * 1024 * 1024:
        raise VerificationError("account reply exceeded the verification byte limit")
    try:
        result = json.loads(data)
    except ValueError:
        raise VerificationError("account reply was not JSON") from None
    if not isinstance(result, dict) or result.get("executed") is not True:
        raise VerificationError("account read was not executed")
    return result


def coverage(roots: dict, graph: dict) -> dict | None:
    if not roots.get("marker") or roots["marker"] != graph.get("marker"):
        return None
    answer = roots.get("answer") or {}
    positions = {}
    for row in answer.get("nodes") or []:
        if (not isinstance(row, list) or len(row) != 6 or not isinstance(row[0], str)
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in row[1:])):
            raise VerificationError("stored coordinates were invalid")
        positions[row[0]] = row[1:]
    if any(n["id"] not in positions for n in graph.get("nodes") or []):
        raise VerificationError("stored coordinates did not cover the graph")
    routes = {row[0] for row in answer.get("edges") or []}
    counts = defaultdict(int)
    for edge in graph.get("edges") or []:
        base = "\0".join((edge["source"], edge["target"], edge.get("type") or ""))
        key = base + "\0" + str(counts[base])
        counts[base] += 1
        if key not in routes:
            raise VerificationError("stored routes did not cover the graph")
    return {"state": "ready", "marker_matches": True, "nodes": len(graph.get("nodes") or []),
            "edges": len(graph.get("edges") or [])}


def verify(key: str, *, get=read, clock=time.monotonic, sleep=time.sleep, wait_s=600) -> dict:
    if not key:
        raise VerificationError("VINC_GRAPH_READONLY_KEY is absent")
    deadline = clock() + wait_s
    while clock() < deadline:
        timeout = max(1, min(30, deadline - clock()))
        roots = get("/graph/roots?basis=recorded", key, timeout)
        state = roots.get("state")
        if state == "ready":
            graph = get("/graph?layout=hub&marker=1", key, max(1, min(30, deadline - clock())))
            result = coverage(roots, graph)
            if result:
                return result
        elif state not in ("pending", "stale"):
            raise VerificationError(f"stored layout state is {state if state in ('off', 'failed') else 'invalid'}")
        sleep(min(10, max(0, deadline - clock())))
    raise VerificationError("stored layout did not become ready within the verification deadline")


if __name__ == "__main__":
    try:
        print(json.dumps(verify(os.environ.get("VINC_GRAPH_READONLY_KEY", "")), sort_keys=True))
    except VerificationError as exc:
        print(f"layout verification stopped: {exc}")
        raise SystemExit(1)
