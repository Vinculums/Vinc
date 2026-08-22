# -*- coding: utf-8 -*-
"""PK 네임스페이싱(v0.8.4 axis 1, design A) — 물리 키는 owner 를 안고, 사용자 대면 id 는
그대로 슬러그다. 합성은 여기 한 곳, 분해는 store/embedded_backend.py 의 한 곳
(`_exec_to_dicts`, PR-B)뿐이다. 근거: [[d11223a9a5d109de5]] §7.

`VINC_PK_NAMESPACED` 가 꺼져 있으면(로컬 스토어는 영구히 꺼진 채) compose/strip 은
항등함수다 — 데스크탑은 이 파일이 존재하는 것도 몰라야 한다.
"""
from __future__ import annotations

import os

#: 물리 키의 owner\x1fid 구분자. 사용자 저작 문자열에 나타나면 안 된다 — 나타나면
#: 물리 키로 오인되어 strip 이 owner 를 잘라낸다(PR-B 의 판별자가 바로 이 바이트다).
SEP = "\x1f"

#: W0 가 실측한(episode:2026-08-22-v084-w0-pk-measurement) 레거시 기본 owner
#: 정규화 표. tenant:local 은 로컬/데스크탑 스토어에서 이미 실제 owner 라 자기
#: 자신으로 매핑한다(실측 스토어 163개 행 전부가 이 상태). user:local 과
#: tenant:shared 는 이 실행에서 한 번도 관측되지 않아 값을 추측하지 않는다 — 없는
#: 채로 둬서 compose 가 raise 하게 한다. 이 행을 채우는 것은 PR-E(허브 실측)의 일.
_NORMALIZE = {
    "tenant:local": "tenant:local",
}


def enabled() -> bool:
    """네임스페이싱이 켜져 있나. 로컬 스토어는 영구히 off(vinc.plan.node-pk-namespacing §7)."""
    return str(os.environ.get("VINC_PK_NAMESPACED") or "").strip().lower() in (
        "1", "true", "yes", "on")


def compose(owner: str, id: str, normalize: bool = True) -> str:
    """논리 id → 물리 키. 꺼져 있으면 항등. 멱등: 이미 물리 키(SEP 포함)면 그대로
    반환한다 — 재합성이 owner\x1fowner\x1fid 를 만들면 안 된다.

    ``normalize``: True(기본)면 owner 를 정규화 표에서 찾아 실owner 로 바꾼다 —
    표 밖 owner(빈 문자열 포함)는 raise. 이 표는 지금 레거시 기본값 셋뿐이라
    실제 계정 owner 를 이 경로로 합성하려면 normalize=False 로 표를 건너뛴다.
    """
    if not enabled():
        return id
    if SEP in id:
        return id   # 이미 물리 키 — 멱등
    if normalize:
        if not owner or owner not in _NORMALIZE:
            raise ValueError(
                f"pkns.compose: owner {owner!r} is not in the normalization map — "
                "PR-E measures the hub and fills this row; do not guess it")
        owner = _NORMALIZE[owner]
    elif not owner:
        raise ValueError("pkns.compose: owner must not be empty")
    return f"{owner}{SEP}{id}"


def strip(v: str) -> str:
    """물리 키 → 논리 id. SEP 이 없으면(꺼진 스토어, 또는 이미 논리 id) 그대로."""
    if SEP not in v:
        return v
    return v.split(SEP, 1)[1]


def has_sep(value) -> bool:
    """value(또는 그 안에 중첩된 str/list/dict)가 SEP 를 담고 있나 — ingest 가
    사용자 입력을 물리 키로 오인하지 않도록 쓰기 전에 거르는 판별자(PR-A)."""
    if isinstance(value, str):
        return SEP in value
    if isinstance(value, dict):
        return any(has_sep(k) or has_sep(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(has_sep(v) for v in value)
    return False


def _demo() -> None:
    os.environ["VINC_PK_NAMESPACED"] = "on"
    try:
        assert compose("tenant:local", "concept:x") == "tenant:local\x1fconcept:x"
        composed = compose("tenant:local", "concept:x")
        assert compose("tenant:local", composed) == composed          # idempotent
        assert strip(composed) == "concept:x"
        assert compose("user:x", "concept:x", normalize=False) == "user:x\x1fconcept:x"
        for bad in ("", "user:local", "tenant:shared"):
            try:
                compose(bad, "concept:x")
                raise AssertionError(f"expected raise for owner={bad!r}")
            except ValueError:
                pass
    finally:
        del os.environ["VINC_PK_NAMESPACED"]
    assert compose("", "concept:x") == "concept:x"   # off = identity, no validation
    assert strip("concept:x") == "concept:x"
    assert has_sep({"a": ["ok", "b\x1fc"]}) and not has_sep({"a": ["ok"]})
    print("pkns self-check OK")


if __name__ == "__main__":
    _demo()
