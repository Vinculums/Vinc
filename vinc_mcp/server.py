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
        "정확-id MERGE 만 백스톱 — 유사도 dedup 없음). 타입은 전역 고정 enum 이 아니라 "
        "주제(theme)별 사용자 저작 어휘다 — vinc_ingest 는 theme_id 필수. 어휘 밖 타입은 "
        "proposed 로 편입되어 응답 proposed_types 에 담기니, 사용자에게 재가(vinc_types_"
        "ratify)를 유도하라. **쓰기 도구(vinc_types_ratify/reject/rename, vinc_themes_"
        "create/activate)는 사용자 승인 후에만 호출**한다(호스트 측 규약 — 시스템이 몰래 "
        "어휘를 확정하지 않는다, THESIS T1)."))

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
    def vinc_ingest(fragment: dict, theme_id: str) -> dict:
        """graph fragment ({nodes, edges, owner?}) 를 theme_id 어휘 기준으로 MERGE 적재
        (그래프 쓰기 표면). theme_id 필수. 어휘 밖 type 은 proposed 로 편입되어 응답
        proposed_types 에 담긴다(스킵 아님) — 사용자에게 vinc_types_ratify 재가 유도."""
        return tools.vinc_ingest(fragment, theme_id)

    # ── 주제 CRUD (headless) ──────────────────────────────────────────────
    @app.tool()
    def vinc_themes_list() -> dict:
        """주제 목록·활성 주제 조회(읽기 전용)."""
        return tools.vinc_themes_list()

    @app.tool()
    def vinc_themes_create(theme_id: str, title: str = "",
                           guiding_questions: list[str] | None = None) -> dict:
        """새 주제 생성 + 시작 어휘 초안 시드(구 enum → proposed). guiding_questions 는
        선택(T9). **사용자 승인 후 호출**."""
        return tools.vinc_themes_create(theme_id, title, guiding_questions)

    @app.tool()
    def vinc_themes_activate(theme_id: str) -> dict:
        """활성 주제 지정. **사용자 승인 후 호출**."""
        return tools.vinc_themes_activate(theme_id)

    # ── 타입 어휘 (어휘 쓰기 표면 — 그래프와 분리된 단일 write) ─────────────────
    @app.tool()
    def vinc_types_list(theme_id: str, status: str | None = None) -> dict:
        """주제 타입 어휘 조회(읽기 전용). status=proposed 로 재가 대기분 필터."""
        return tools.vinc_types_list(theme_id, status)

    @app.tool()
    def vinc_types_ratify(theme_id: str, name: str) -> dict:
        """proposed → authored 재가. **사용자 승인 후에만 호출**(THESIS T1)."""
        return tools.vinc_types_ratify(theme_id, name)

    @app.tool()
    def vinc_types_reject(theme_id: str, name: str) -> dict:
        """어휘에서 타입 제거(기존 그래프 인스턴스는 미변경). **사용자 승인 후에만 호출**."""
        return tools.vinc_types_reject(theme_id, name)

    @app.tool()
    def vinc_types_rename(theme_id: str, from_name: str, to_name: str) -> dict:
        """타입 개명 → authored(new). **사용자 승인 후에만 호출**."""
        return tools.vinc_types_rename(theme_id, from_name, to_name)

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
