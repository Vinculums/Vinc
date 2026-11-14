"""JSON 파일 하나를 읽는 쪽이 반쪽을 보지 않게 바꿔 쓰는 단 하나의 함수.

셀프호스트 허브의 좌석·사람·세션 파일(`vinc_mcp/selfhost_*.py`)이 같은 규칙으로 쓴다. 임시 파일은
처음부터 0600 으로 만들어서 내용이 쓰이는 동안에도 다른 사용자가 읽을 틈이 없다.

`store/` 는 맨 아래 층이라(`scripts/gate_ownership.py` 의 `LAYER_BANS`) 이 모듈은 표준 라이브러리만
가져온다.
"""
from __future__ import annotations

import json
import os


def write_json_0600(path: str, data: object) -> None:
    """`data` 를 JSON(UTF-8, 끝 줄바꿈 없음)으로 `path` 에 바꿔 쓴다. 파일 모드는 0600 이다.

    `path` 옆 임시 파일에 다 쓴 뒤 `os.replace` 로 옮긴다. 쓰는 중에 실패하면 임시 파일을 지우고
    예외를 그대로 올린다: `path` 는 이전 내용 그대로이고 반쪽 파일은 어디에도 남지 않는다."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
