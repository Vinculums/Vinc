# -*- coding: utf-8 -*-
"""vinc_mcp — Vinc 의 두 번째 프론트 도어(DEC D11, 스켈레톤 [[vinc.spec.mcp-server]]).

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
#: MCP 핸드셰이크의 `serverInfo.version`(`rpc.py`). **제품 버전과 같아야 한다** —
#: 클라이언트가 화면에 그리는 유일한 Vinc 버전이 이 값이고, 이 자리만 v0.3.0(2026-07-18)
#: 에 멈춰 있어서 제품이 0.9.1 인 동안 어시스턴트마다 0.3.0 이라고 적혀 있었다.
#: 릴리스 게이트가 못 잡은 이유는 `scripts/version_sync.py` 의 자리 목록에 이 파일이
#: 없었던 것이고, 그 목록에 더했으니 이제 여기만 뒤처지면 태그 런이 멈춘다.
#: 손으로 고치지 말고 `python scripts/version_sync.py --set X.Y.Z` 로 함께 올릴 것.
__version__ = "0.9.4"
