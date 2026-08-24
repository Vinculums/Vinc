# -*- coding: utf-8 -*-
"""원격 동기화 클라이언트(S1) — `mcp.vincs.io/sync/{push,pull}` 왕복.

`remote.py` 와 왜 분리하나: 그쪽은 `tools/call` 단건 프록시(60s, owner strip)이고,
동기화는 **대량·다청크·긴 작업**이라 요구가 다르다. 특히:

- **4MB 바디 상한**(http_server `MAX_BODY`) — 초과하면 서버가 빈 바디로 400 을 낸다.
  즉 **조용히 실패**한다. 그래서 청킹은 선택이 아니라 필수다(api/sync.chunk_bundle).
- **쿼터 면제 경로** — `/sync/*` 는 Dispatcher 를 우회하므로 도구 쿼터를 안 먹는다.

키는 env 가 아니라 **호출부가 넘긴다**(`/sync` 바디로 사이드카에 전달되는 회원 키) —
레포·디스크에 두지 않는다(D36).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_URL = "https://mcp.vincs.io"
TIMEOUT = 120.0


class SyncError(RuntimeError):
    pass


def _post(base: str, path: str, token: str, payload: dict, *, timeout: float) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        if e.code == 401:
            raise SyncError("unauthorized — 계정 키(vinc_sk_…)가 없거나 폐기됨") from None
        raise SyncError(f"remote {path} failed ({e.code}) {detail}".strip()) from None
    except Exception as e:
        raise SyncError(f"remote {path} unreachable: {type(e).__name__}") from None


def push(bundle_chunks: list[dict], *, token: str, base: str = DEFAULT_URL,
         timeout: float = TIMEOUT) -> dict:
    """청크를 순차 전송. 각 청크는 자기완결·멱등이라 중단 후 재전송해도 안전하다."""
    applied = skipped = edges = 0
    warnings: list[str] = []
    for i, ch in enumerate(bundle_chunks):
        out = _post(base, "/sync/push", token, {"bundle": ch}, timeout=timeout)
        if not out.get("executed"):
            raise SyncError(f"chunk {i + 1}/{len(bundle_chunks)} rejected: "
                            f"{out.get('note') or out.get('error') or 'unknown'}")
        applied += int(out.get("applied") or 0)
        skipped += int(out.get("skipped_stale") or 0)
        edges += int(out.get("edges") or 0)
        warnings += out.get("warnings") or []
    return {"executed": True, "chunks": len(bundle_chunks), "applied": applied,
            "skipped_stale": skipped, "edges": edges, "warnings": warnings}


def pull(*, since: int, token: str, base: str = DEFAULT_URL,
         timeout: float = TIMEOUT) -> dict:
    """원격 delta 한 페이지. 커서 전진은 호출부(오케스트레이터)가 **적용 성공 후에만**."""
    out = _post(base, "/sync/pull", token, {"since": int(since)}, timeout=timeout)
    if not out.get("executed"):
        raise SyncError(out.get("note") or out.get("error") or "pull failed")
    return out


def docs_pull(*, since: int, token: str, base: str = DEFAULT_URL,
              timeout: float = TIMEOUT) -> dict:
    """문서 정본 delta(D45 P3). docsync 응답에는 `executed` 키가 없다 — `error` 로 판정."""
    out = _post(base, "/sync/docs-pull", token, {"since": int(since)}, timeout=timeout)
    if out.get("error"):
        raise SyncError(str(out["error"]))
    return out


def docs_push(payload: dict, *, token: str, base: str = DEFAULT_URL,
              timeout: float = TIMEOUT) -> dict:
    """문서 정본 delta 전송. 조각내기는 호출부(api/sync._chunk_docs) 책임 —
    4MB 바디 상한을 넘기면 서버가 빈 바디 400 으로 **조용히** 거절한다."""
    out = _post(base, "/sync/docs-push", token, {"payload": payload}, timeout=timeout)
    if out.get("error"):
        raise SyncError(str(out["error"]))
    return out
