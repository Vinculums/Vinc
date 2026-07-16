# -*- coding: utf-8 -*-
"""vinc_mcp — Vinc 의 두 번째 프론트 도어(DEC D11, 스켈레톤 vinc.mcp.skeleton).

얇은 브리지: 비즈니스 로직 0 — MCP tool 계약 검증 → 루프백 사이드카 HTTP 호출
→ 결과 반환이 전부다. 의미 판단·구조화·dedup 은 상류(Claude)와 하류(엔진)에
있다. 패키지명이 `mcp` 가 아닌 이유는 스켈레톤 §5 델타(공식 SDK 이름 충돌 회피).

전송 2종(P5 선행 A·B):
  - stdio  — 로컬 등록(Claude Code·Desktop). `vinc mcp serve`.
  - Streamable HTTP — 원격 프로파일(D36)용 루프백 서버. `vinc mcp serve-http`.

구현은 표준 라이브러리 전용(F5: 오프라인·외부 의존 0 — 사이드카 http.server 와
동일 원칙, PyInstaller 동결에 추가 의존을 넣지 않는다). 공식 SDK(FastMCP)는
스켈레톤 원안이었으나 P5 에서 stdlib 구현으로 확정 — 근거는 스켈레톤 §10 갱신.
"""
__version__ = "0.1.9"
