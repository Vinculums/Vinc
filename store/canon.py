# -*- coding: utf-8 -*-
"""정규 JSON 과 sha256 지문의 한 벌. 표준 라이브러리만 쓴다(어느 층에서든 import 가능).

- ``canonical_json``: 키 정렬 · ``ensure_ascii=False`` · ``allow_nan=False`` · 공백 없는
  구분자. 같은 값이면 키 순서와 무관하게 같은 문자열이 나오고, NaN·무한대는 거절한다.
- ``digest``: 그 문자열의 UTF-8 sha256 16진수.
- ``canonical_json_ascii_v1``·``digest_ascii_v1``: 토픽 이전(``store/topic_transfer``)
  계열이 쓰는 **옛 형식**. ``ensure_ascii=True`` 이고 NaN 을 막지 않는다. 이 형식으로
  만든 지문이 계정 DB(``space_conversions.fingerprint``)·``_SyncMeta`` 저널·봉인된
  매니페스트에 저장돼 다시 대조되므로, 형식을 바꾸면 진행 중인 변환이 거절된다.
  두 형식을 함께 받는 이행이 생기기 전에는 이 둘을 바꾸지도 합치지도 말 것.
- ``file_sha256``: 파일을 덩어리로 읽어 해시한다(통째 읽기와 같은 값, 메모리는 덩어리만큼).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK = 1024 * 1024


def canonical_json(value) -> str:
    """키 정렬·비ASCII 그대로·NaN 거절·공백 없는 JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    """``canonical_json`` 의 UTF-8 sha256 16진수."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_json_ascii_v1(value) -> str:
    """토픽 이전 계열의 옛 형식. 비ASCII 를 ``\\uXXXX`` 로 쓰고 NaN 을 막지 않는다."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest_ascii_v1(value) -> str:
    """``canonical_json_ascii_v1`` 의 sha256 16진수(저장된 이전 지문과 같은 값)."""
    return hashlib.sha256(canonical_json_ascii_v1(value).encode()).hexdigest()


def file_sha256_size(path, chunk: int = CHUNK) -> tuple[str, int]:
    """파일을 ``chunk`` 바이트씩 읽어 (sha256 16진수, 읽은 바이트 수)를 낸다."""
    h = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            size += len(block)
            h.update(block)
    return h.hexdigest(), size


def file_sha256(path, chunk: int = CHUNK) -> str:
    """파일을 ``chunk`` 바이트씩 읽어 sha256 16진수를 낸다."""
    return file_sha256_size(path, chunk)[0]
