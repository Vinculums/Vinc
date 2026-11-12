# -*- coding: utf-8 -*-
"""허브 쪽 입구가 실행 기록(`episode:run-*`)의 프로토콜 상태 규칙을 부르는 자리.

규칙 한 벌은 `store/run_state.py` 에 있다(`api` 와 `vinc_mcp` 가 같이 닿는 맨 아래 층). MCP·REST 의
`vinc_ingest`(tools.py)와 웹 프록시의 `ingest-fragment`·`ingest-pack`(http_server.proxy_op)은
`writes` 로 그런 쓰기를 거절한다. 기기 동기화 `/sync/push` 는 거절하지 않는다: 사이드카의 동기화
적용(`api/sync.py` `apply_delta`)이 받는 스토어의 값을 남기고, 남긴 실행을 결과의 `CODE` 필드와
경고로 알린다.
"""
from __future__ import annotations

from store.run_state import CODE, props_of, run_id, writes

__all__ = ["CODE", "props_of", "push", "run_id", "writes"]


def push(bridge, owner: str, bundle) -> dict:
    """기기가 보낸 동기화 묶음을 허브 스토어에 적용한다.

    잠금도 미리 읽기도 없다: 상태를 남기는 일은 적용 안에서, 저장된 행을 읽은 그 쓰기 잡 안에서
    끝난다. 사이드카의 쓰기는 잡 워커 하나로 줄 서므로 실행 프로토콜의 쓰기가 그 사이에 끼지 못한다."""
    return bridge.request("POST", "/sync/apply-delta", body={
        "owner": owner, "bundle": bundle if isinstance(bundle, dict) else {}})
