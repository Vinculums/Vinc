# -*- coding: utf-8 -*-
"""vinc-mcp — Vinc MCP 서버 · Push MVP 얇은 브리지([[vinc.mcp]] D11 · [[vinc.mcp.skeleton]]).

패키지명은 `vinc_mcp`다(스켈레톤 문서의 `mcp/` 표기와 다름): 최상위 `mcp/` 로 두면
레포 루트가 `sys.path` 에 오를 때(다른 엔진들처럼) 공식 MCP SDK(PyPI `mcp`)를 이
패키지 자신이 가려 `import mcp...` 가 깨진다 — 이름 충돌 회피가 유일한 목적이고,
그 외 파일 구성은 스켈레톤 §5 표와 1:1(`server.py`·`tools.py`·`bridge.py`·
`lifecycle.py`·`audit.py`)이다.
"""
