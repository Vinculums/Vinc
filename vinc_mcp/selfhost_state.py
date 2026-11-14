# -*- coding: utf-8 -*-
"""셀프호스트 허브의 상태 파일이 사는 디렉터리.

로그인(`selfhost_web`)·좌석(`selfhost_seats`)·사람(`selfhost_users`)이 같은 곳을 쓰므로 어느 쪽에도
속하지 않는 이 모듈이 정한다. 이 모듈은 `vinc_mcp` 안의 다른 모듈을 최상위에서 가져오지 않는다.
"""
from __future__ import annotations

import os


def state_dir() -> str:
    """세션 파일과 비밀번호 파일이 사는 곳: `VINC_SELFHOST_STATE`, 없으면 OAuth 스냅샷 옆
    (컨테이너에서는 영속 볼륨), 그것도 없으면 OAuth 작업본 경로."""
    explicit = os.environ.get("VINC_SELFHOST_STATE")
    if explicit:
        root = explicit
    elif os.environ.get("VINC_MCP_STATE_SNAPSHOT"):
        root = os.path.dirname(os.environ["VINC_MCP_STATE_SNAPSHOT"])
    else:
        from .hub_state import resolve_state_dir
        root = resolve_state_dir()
    os.makedirs(root, exist_ok=True)
    return root
