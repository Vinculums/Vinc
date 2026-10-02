# -*- coding: utf-8 -*-
"""실행 지문 — 엔진 판과 인코더 런타임 판. **평가 결과는 이것 없이 비교할 수 없다.**

같은 int8 bge-m3 모델이라도 onnxruntime 1.29 → 1.30 이면 질의 벡터 81개가 전부 코사인
0.009~0.022 움직이고(의미 팔 판정 격차 `_SEM_GAP` 0.010 보다 크다) 핀 스토어 G0 가 5문항
움직였다. 2026-09-28 에 이것을 모르고 "엔진 탓"으로 한 번 오진했다 — 새 venv 가 조용히
다른 onnxruntime 을 깔았기 때문이다. 그래서 실행 헤더에 판을 싣고, **저장 벡터를 만든
런타임과 질의 런타임이 다르면 실행을 거부한다.**
근거: [[record:the-eval-drift-to-41-14-4-follows-the-query-encoder-runtime-not-the-engine-2026-09-28]]
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import pathlib
import platform
import sys

#: 벡터 값을 바꿀 수 있는 패키지. 락의 `encoder_runtime` 과 이 셋을 대조한다.
RUNTIME_PACKAGES = ("onnxruntime", "tokenizers", "numpy")

LOCK = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "eval_store.lock.json"


def _version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return ""


def fingerprint() -> dict:
    """이 인터프리터의 판. 질의 인코딩과 스토어 엔진이 모두 이 프로세스(또는 이
    인터프리터로 띄운 자식)에서 돌기 때문에 이 판이 곧 실행의 판이다."""
    return {"engine": {"ladybug": _version("ladybug")},
            "encoder_runtime": {p: _version(p) for p in RUNTIME_PACKAGES},
            "python": platform.python_version(),
            "executable": sys.executable}


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def lock_entry(key: str) -> dict:
    stores = json.loads(LOCK.read_text(encoding="utf-8"))["stores"]
    if key not in stores:
        raise KeyError(f"락에 스토어 {key!r} 가 없다 — 있는 것: {sorted(stores)}")
    return stores[key]


def runtime_mismatch(entry: dict, fp: dict) -> list[str]:
    """락이 적은 저장 벡터 런타임과 이 실행의 런타임이 다른 패키지들. 빈 목록이면 같다.
    락에 판이 없으면 그것 자체를 불일치로 센다 — 모르는 것을 같다고 가정하지 않는다."""
    want = entry.get("encoder_runtime") or {}
    got = fp["encoder_runtime"]
    return [f"{p}: store {want.get(p) or '?'} / query {got.get(p) or '?'}"
            for p in RUNTIME_PACKAGES if not want.get(p) or want.get(p) != got.get(p)]
