#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`Dockerfile.m3probe` 의 폭이 제품의 표와 같은가.

`encode-m3-probe.yml` 이 굽기 전에 부른다. 폭을 워크플로에 하드코딩하면 그 파일이
`store.config.EMBEDDING_DIM_BY_MODEL` 의 또 다른 독자가 되고, 갈려도 아무도 모른다 —
갈린 채로 구우면 벡터 폭이 다른 이미지가 나오고, 그것으로 잰 수치는 다른 모델의 것이다.

Dockerfile 을 **읽어서** 대조한다. 두 곳을 손으로 맞추는 대신 한 곳이 다른 곳을 검사한다.
"""
from __future__ import annotations

import pathlib
import re
import sys

try:
    from scripts._root import ROOT
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT

from store.config import EMBEDDING_DIM_BY_MODEL  # noqa: E402

_DOCKERFILE = ROOT / "Dockerfile.m3probe"


def read_triple(path: pathlib.Path = _DOCKERFILE) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^ENV (VINC_EMBED_(?:MODEL|MODEL_PATH|DIM))=(.+)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def check(triple: dict[str, str], table: dict[str, int]) -> list[str]:
    bad: list[str] = []
    model = triple.get("VINC_EMBED_MODEL")
    dim = triple.get("VINC_EMBED_DIM")
    path = triple.get("VINC_EMBED_MODEL_PATH")
    if not model or not dim or not path:
        return ["Dockerfile.m3probe 에서 ENV 삼중을 다 못 읽었다: " + repr(triple)]
    if model not in table:
        bad.append(f"제품 표가 {model} 를 모른다 — 등록하지 않은 모델로 굽는 중이다")
    elif str(table[model]) != dim:
        bad.append(f"폭이 갈렸다: 표는 {model} 를 {table[model]} 로 알고 "
                   f"Dockerfile.m3probe 는 {dim} 라고 적는다")
    # basename 도 본다: 굽기 스텝이 이 이름으로 파일을 만들고 COPY 가 같은 이름을 넣는다.
    if not path.endswith("/" + path.rsplit("/", 1)[-1]):
        bad.append(f"MODEL_PATH 가 절대경로가 아니다: {path}")
    return bad


def main() -> int:
    triple = read_triple()
    bad = check(triple, EMBEDDING_DIM_BY_MODEL)
    for b in bad:
        print(f"::error::{b}", file=sys.stderr)
    if bad:
        return 1
    print(f"{triple['VINC_EMBED_MODEL']} 폭 {triple['VINC_EMBED_DIM']} 확인 "
          f"(제품 표와 일치) · {triple['VINC_EMBED_MODEL_PATH']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
