# -*- coding: utf-8 -*-
"""PK 네임스페이싱(v0.8.4 axis 1, design A) — 물리 키는 owner 를 안고, 사용자 대면 id 는
그대로 슬러그다. 합성은 여기 한 곳, 분해는 store/embedded_backend.py 의 한 곳
(`_exec_to_dicts`, PR-B)뿐이다. 근거: [[d11223a9a5d109de5]] §7.

`VINC_PK_NAMESPACED` 가 꺼져 있으면(로컬 스토어는 영구히 꺼진 채) compose/strip 은
항등함수다 — 데스크탑은 이 파일이 존재하는 것도 몰라야 한다.
"""
from __future__ import annotations

import json
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


def ekey(owner: str, src: str, rel: str, dst: str) -> str:
    """엣지 툼스톤·엣지 동기화 키 — **논리** ``src|REL|dst`` 를 짓고 **한 번만** 합성한다.

    끝점을 먼저 :func:`strip` 하는 것이 요점이다. 호출부는 자리에 따라 논리 슬러그를
    들고 있기도 하고 이미 합성된 물리 키를 들고 있기도 한데, 물리 키를 그대로 이어
    붙이면 ``owner\\x1fsrc|REL|owner\\x1fdst`` 라는 **재합성된** 키가 나와 같은 엣지가
    두 개의 서로 다른 툼스톤 키를 갖게 된다(그러면 삭제 표식이 상쇄되지 않는다).
    여기서 strip 을 먼저 하므로 어느 형태를 넘겨도 결과가 같다. 근거:
    [[d11223a9a5d109de5]] §7"""
    return compose(owner, f"{strip(src)}|{rel}|{strip(dst)}", normalize=False)


#: owner 토큰 길이 상한 — :func:`looks_physical` 의 좁히기용. 실제 owner 는
#: ``user:`` + UUID(41자)나 ``tenant:local`` 이라 넉넉하다.
_OWNER_MAX = 128


def looks_physical(v: str) -> bool:
    """이 문자열이 :func:`compose` 가 만든 물리 키처럼 보이나. SEP 이 **정확히 한 번**
    나오고 그 앞이 owner 토큰(비지 않고, 짧고, **공백이 전혀 없다**)일 때만 참이다.

    왜 "SEP 이 있으면 무조건 자른다"가 아닌가: **문서 본문은 U+001F 가드 밖이다**
    (D45 — Chunk=SSoT 는 검열하지 않으므로 `put_doc` 은 id·title·origin 만 거른다).
    본문에 그 바이트가 섞인 문서를 읽을 때 무조건 자르면 읽기가 본문을 파괴한다 —
    이 훅이 막으려던 것보다 나쁜 고장이다. 그래서 판별자를 여기서 좁힌다.

    공백 금지가 그 좁히기의 핵이다. owner 는 ``user:``+UUID 나 ``tenant:local`` 이라
    공백이 없고, 산문은 SEP 앞 몇 글자 안에 거의 반드시 공백이나 줄바꿈을 둔다."""
    if v.count(SEP) != 1:
        return False
    head = v.split(SEP, 1)[0]
    return bool(head) and len(head) <= _OWNER_MAX and not any(c.isspace() for c in head)


def strip_deep(value):
    """읽기 경계에서 물리 키를 논리 id 로 되돌린다 — 리스트(`collect()` 산출)와
    중첩 dict(드라이버가 노드를 구조체로 줄 때)까지 재귀한다.

    SEP 이 아예 없으면 **받은 객체를 그대로 돌려준다** — 읽기 경로의 거의 모든 행이
    그 경우라 새 dict/list 를 만들지 않는다.

    ⚠ **JSON 문자열 분기는 두지 않는다**(계획서의 "JSON string props" 항목에서
    의도적으로 벗어난 자리다). 두 방향 다 실측으로 닫혀 있어서 도달 불가 코드가 된다:
    ``json.dumps`` 는 ensure_ascii 와 무관하게 제어문자를 ``\\u001f`` 로 이스케이프하므로
    `props` 열에 저장된 JSON 에는 literal SEP 이 없고(→ `has_sep` False), 반대로 손으로
    literal SEP 을 넣은 JSON 문자열은 ``json.loads`` 가 strict 모드에서 거부한다
    (`Invalid control character`). 즉 물리 키를 품은 JSON **문자열**은 이 스토어에
    존재할 수 없다. 드라이버가 구조체를 그대로 주는 경우는 dict 분기가 받는다."""
    if not has_sep(value):
        return value
    if isinstance(value, dict):
        return {k: strip_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(strip_deep(v) for v in value)
    if isinstance(value, str):
        return strip(value) if looks_physical(value) else value
    return value


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
        # ekey: 논리로 짓고 한 번만 합성한다 — 물리 끝점을 넘겨도 같은 키.
        logical = ekey("u", "concept:a", "RELATED_TO", "concept:b")
        assert logical == "u\x1fconcept:a|RELATED_TO|concept:b"
        assert ekey("u", compose("u", "concept:a", normalize=False), "RELATED_TO",
                    compose("u", "concept:b", normalize=False)) == logical
        # strip_deep: 리스트(collect)·중첩 dict, 그리고 안 건드리는 것들.
        assert strip_deep(["u\x1fconcept:a", "concept:b"]) == ["concept:a", "concept:b"]
        assert strip_deep({"n": {"id": "u\x1fconcept:a"}}) == {"n": {"id": "concept:a"}}
        # json.dumps 가 제어문자를 이스케이프하므로 props JSON 에는 SEP 이 안 남는다.
        props = json.dumps({"ref": "u\x1fconcept:a"}, ensure_ascii=False)
        assert SEP not in props and strip_deep(props) is props
        body = "line one\nline \x1ftwo"         # 문서 본문 — 자르면 안 된다(D45)
        assert strip_deep(body) == body
    finally:
        del os.environ["VINC_PK_NAMESPACED"]
    assert compose("", "concept:x") == "concept:x"   # off = identity, no validation
    assert strip("concept:x") == "concept:x"
    assert has_sep({"a": ["ok", "b\x1fc"]}) and not has_sep({"a": ["ok"]})
    print("pkns self-check OK")


if __name__ == "__main__":
    _demo()
