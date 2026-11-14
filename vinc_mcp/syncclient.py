# -*- coding: utf-8 -*-
"""원격 동기화 클라이언트(S1) — `mcp.vincs.io/sync/{push,pull}` 왕복.

`remote.py` 와 왜 분리하나: 그쪽은 `tools/call` 단건 프록시(60s, owner strip)이고,
동기화는 **대량·다청크·긴 작업**이라 요구가 다르다. 특히:

- **4MB 바디 상한**(`hub_http.MAX_BODY`) — 초과하면 허브가 본문을 읽지 않고 413 으로 거절한다. 거절된
  묶음은 올라가지 않았으므로 `SyncError` 로 올리고, 부르는 쪽은 커서도 base 도 옮기지 않는다. 그래서
  청킹은 선택이 아니라 필수다(api/sync.chunk_bundle).
- **쿼터 면제 경로** — `/sync/*` 는 Dispatcher 를 우회하므로 도구 쿼터를 안 먹는다.

키는 env 가 아니라 **호출부가 넘긴다**(`/sync` 바디로 사이드카에 전달되는 회원 키) —
레포·디스크에 두지 않는다(D36).
"""
from __future__ import annotations

import json

from . import hubhttp

DEFAULT_URL = "https://mcp.vincs.io"
TIMEOUT = 120.0


class SyncError(RuntimeError):
    pass


#: 업로드마다 허브에 밝히는, 이 클라이언트가 알아듣는 답(`api/revstamp.py` `HANDLES`). `clock_skew` 를 밝히지
#: 않은 업로드는 허브가 받지 않은 항목이 있으면 청크째 실패시킨다. 이 클라이언트는 그 목록을 돌려주고 부르는
#: 쪽이 다시 보낸다.
HANDLES = {"handles": ["clock_skew"]}


def _post(base: str, path: str, token: str, payload: dict, *, timeout: float) -> dict:
    try:
        return hubhttp.post_json(base.rstrip("/") + path, token=token, payload=payload,
                                 timeout=timeout)
    except hubhttp.HubHTTPError as e:
        if e.status is None:
            raise SyncError(f"remote {path} unreachable: {type(e.cause).__name__}") from None
        detail = ""
        try:
            detail = json.loads(e.body).get("error", "")
        except Exception:
            pass
        if e.status == 401:
            raise SyncError("unauthorized — 계정 키(vinc_sk_…)가 없거나 폐기됨") from None
        raise SyncError(f"remote {path} failed ({e.status}) {detail}".strip()) from None
    except Exception as e:
        raise SyncError(f"remote {path} unreachable: {type(e).__name__}") from None


def _push_body(chunk: dict) -> dict:
    return {"bundle": {**chunk, **HANDLES}}


def push_bytes(chunk: dict) -> int:
    """`push` 가 청크 하나를 허브에 싣는 요청 본문의 바이트 수. 허브의 본문 상한(`hub_http.MAX_BODY`)과 견줄 때 쓴다."""
    return len(hubhttp.encode_json(_push_body(chunk)))


def push(bundle_chunks: list[dict], *, token: str, base: str = DEFAULT_URL,
         timeout: float = TIMEOUT) -> dict:
    """청크를 순차 전송. 각 청크는 자기완결·멱등이라 중단 후 재전송해도 안전하다.

    허브가 받지 않은 항목(`clock_skew`, 보낸 기기의 시계가 앞섬)은 청크마다 모아 그대로 돌려준다.
    부르는 쪽(`api/sync.push_local`)이 그 목록으로 base 를 옮기지 않고 다음 동기화에 다시 보낸다."""
    applied = skipped = edges = kept_deleted = 0
    warnings: list[str] = []
    refused: list = []
    for i, ch in enumerate(bundle_chunks):
        out = _post(base, "/sync/push", token, _push_body(ch), timeout=timeout)
        if not out.get("executed"):
            raise SyncError(f"chunk {i + 1}/{len(bundle_chunks)} rejected: "
                            f"{out.get('note') or out.get('error') or 'unknown'}")
        applied += int(out.get("applied") or 0)
        skipped += int(out.get("skipped_stale") or 0)
        edges += int(out.get("edges") or 0)
        kept_deleted += int(out.get("kept_deleted") or 0)
        warnings += out.get("warnings") or []
        refused += [r for r in out.get("clock_skew") or [] if r not in refused]
    merged = {"executed": True, "chunks": len(bundle_chunks), "applied": applied,
              "skipped_stale": skipped, "edges": edges, "kept_deleted": kept_deleted,
              "warnings": warnings}
    if refused:
        merged["clock_skew"] = refused
    return merged


def pull(*, since: int, token: str, base: str = DEFAULT_URL,
         timeout: float = TIMEOUT, everything: bool = False) -> dict:
    """원격 delta 한 페이지. 커서 전진은 호출부(오케스트레이터)가 **적용 성공 후에만**.

    `everything` 은 주제가 비어 있는 노드까지 받는다(모드 전환의 전량 받기, VIN-130).
    그 플래그를 모르는 옛 허브는 무시하고 종전대로 답한다(주제 있는 노드만)."""
    payload: dict = {"since": int(since)}
    if everything:
        payload["all"] = True
    out = _post(base, "/sync/pull", token, payload, timeout=timeout)
    if not out.get("executed"):
        raise SyncError(out.get("note") or out.get("error") or "pull failed")
    return out


def docs_pull(*, since: int, token: str, base: str = DEFAULT_URL,
              timeout: float = TIMEOUT, everything: bool = False) -> dict:
    """문서 정본 delta(D45 P3). docsync 응답에는 `executed` 키가 없다 — `error` 로 판정.

    `everything` 은 순번과 무관하게 전부를 한 번에 받는다(VIN-130). 응답에 `everything`
    이 없으면 옛 허브가 플래그를 모르고 델타로 답한 것이다."""
    payload: dict = {"since": int(since)}
    if everything:
        payload["all"] = True
    out = _post(base, "/sync/docs-pull", token, payload, timeout=timeout)
    if out.get("error"):
        raise SyncError(str(out["error"]))
    return out


def _docs_push_body(payload: dict) -> dict:
    return {"payload": {**payload, **HANDLES}}


def docs_push_bytes(payload: dict) -> int:
    """`docs_push(payload)` 가 허브에 싣는 요청 본문의 바이트 수. 허브의 본문 상한(`hub_http.MAX_BODY`)과 견줄 때 쓴다."""
    return len(hubhttp.encode_json(_docs_push_body(payload)))


def docs_push(payload: dict, *, token: str, base: str = DEFAULT_URL,
              timeout: float = TIMEOUT) -> dict:
    """문서 정본 delta 전송. 조각내기는 호출부(api/sync._chunk_docs) 책임 —
    4MB 바디 상한을 넘기면 허브가 413 으로 거절하고 `SyncError` 가 된다. 문서 하나가 혼자서 상한을 넘으면
    조각낼 수 없으므로 호출부가 보내지 않는다."""
    out = _post(base, "/sync/docs-push", token, _docs_push_body(payload), timeout=timeout)
    if out.get("error"):
        raise SyncError(str(out["error"]))
    return out
