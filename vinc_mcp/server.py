# -*- coding: utf-8 -*-
"""vinc-mcp stdio 진입점 — 5 tool 등록 + 수명주기 배선([[vinc.mcp.skeleton]] §2·§5).

    python -m vinc_mcp.server

공식 MCP Python SDK(`mcp`, extra `vinc[mcp]`)로 stdio JSON-RPC 를 처리한다 — 이
패키지가 최상위 `mcp/` 가 아니라 `vinc_mcp/` 인 이유는 [[vinc_mcp/__init__.py]]
참조(이름 충돌 회피).
"""
from __future__ import annotations

import os
import uuid

from mcp.server.fastmcp import FastMCP

from .bridge import SidecarBridge
from .lifecycle import SidecarHandle, attach_or_spawn, release
from .tools import VincTools


def _data_dir() -> str:
    from store.config import load_config
    cfg = load_config()
    return cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")


def build_app(*, data_dir: str | None = None,
              handle: SidecarHandle | None = None) -> FastMCP:
    """FastMCP 앱을 구성한다. `handle` 을 주입하면 spawn/attach 를 건너뛴다(테스트용)."""
    data_dir = data_dir or _data_dir()
    handle = handle or attach_or_spawn(data_dir, parent_pid=os.getpid())
    bridge = SidecarBridge(handle.host, handle.port, handle.token)
    session_id = f"mcp-{uuid.uuid4().hex[:12]}"
    tools = VincTools(bridge, data_dir=data_dir, session_id=session_id)

    app = FastMCP("vinc", instructions=(
        "Vinc — 오프라인 개인 그래프 메모리. vinc_ingest 전에 반드시 vinc_search/"
        "vinc_recall 로 기존 id 를 조회하고 있으면 재사용하라(read-before-write, "
        "정확-id MERGE 만 백스톱 — 유사도 dedup 없음)."))

    @app.tool()
    def vinc_search(query: str, limit: int = 20, domain: str | None = None) -> dict:
        """벡터+BM25 하이브리드 검색(읽기 전용)."""
        return tools.vinc_search(query, limit=limit, domain=domain)

    @app.tool()
    def vinc_recall(domain: str | None = None, tag: str | None = None,
                    owner: str | None = None, limit: int = 50) -> dict:
        """소유자 스코프 에피소드 회상(읽기 전용)."""
        return tools.vinc_recall(domain=domain, tag=tag, owner=owner, limit=limit)

    @app.tool()
    def vinc_graph(seed_id: str, depth: int = 2) -> dict:
        """서브그래프 조회(현재 쿼리 계층에 seed 기반 순회 미구현 — 후속)."""
        return tools.vinc_graph(seed_id, depth=depth)

    @app.tool()
    def vinc_decide(topic: str, depth: int = 2) -> dict:
        """결정 지원 그래프 순회(읽기 전용, 결정론)."""
        return tools.vinc_decide(topic, depth=depth)

    @app.tool()
    def vinc_ingest(fragment: dict) -> dict:
        """graph fragment ({nodes, edges, owner?}) 를 MERGE 적재(유일 쓰기 도구)."""
        return tools.vinc_ingest(fragment)

    app._vinc_data_dir = data_dir   # shutdown 시 release() 용
    app._vinc_handle = handle
    return app


def main() -> None:
    app = build_app()
    try:
        app.run(transport="stdio")
    finally:
        release(app._vinc_data_dir, app._vinc_handle)


if __name__ == "__main__":
    main()
