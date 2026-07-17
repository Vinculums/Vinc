# -*- coding: utf-8 -*-
"""필드단위 3-way 병합(S3) — 발주자 핵심 요구.

문제: 노트북과 웹에서 **서로 다른 필드**를 고쳤을 때 둘 다 살아야 한다. 단순 LWW
(노드 통째로 최신승)는 늦게 진 쪽의 필드 편집을 통째로 날린다. 그래서 필드 단위로,
**마지막 동기화 기준본(base)**을 놓고 3-way 병합한다:

    base   = 이 기기가 원격과 마지막으로 맞춘 값(_SyncBase, 로컬 전용)
    local  = 지금 로컬 값
    remote = 방금 pull 한 원격 값

각 필드 f 에 대해:
  - localΔ  = local[f] ≠ base[f]      (로컬이 그 필드를 건드림)
  - remoteΔ = remote[f] ≠ base[f]     (원격이 그 필드를 건드림)
  둘 다 아니면 base 그대로. 한쪽만 바뀌면 그쪽 채택(겹치지 않음 → 둘 다 보존).
  **둘 다 바뀌고 값이 다르면**(진짜 충돌) updated_at 최신승, 동률이면 노드 rev,
  또 동률이면 id 로 결정론적 타이브레이크.

이 모듈은 **순수 함수**다(스토어·시간 의존 0) — 어디서든 단위 테스트된다. base 의
읽기·쓰기와 스토어 반영은 api/sync.py 가 한다.
"""
from __future__ import annotations

import json

from . import revstamp


def _norm(v):
    """비교용 정규화. list(STRING[])→순서보존 tuple, dict(props)→정렬 JSON(키 순서
    무관 비교), None→''(백필 default '' 와 미설정 None 이 가짜 충돌 안 나게)."""
    if isinstance(v, list):
        return tuple(v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    if v is None:
        return ""
    return v


def parse_base(fields_json: str | None) -> dict:
    """_SyncBase.fields(JSON 문자열) → dict. 없으면 빈 dict."""
    if not fields_json:
        return {}
    try:
        d = json.loads(fields_json)
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def dump_base(fields: dict) -> str:
    return json.dumps(fields, ensure_ascii=False, sort_keys=True)


def tracked_of(label: str, node: dict) -> dict:
    """노드에서 라벨의 추적필드만 뽑는다(base 스냅샷·비교 단위)."""
    return {f: node.get(f) for f in revstamp.tracked_fields(label) if f in node}


def three_way(base: dict, local: dict, remote: dict, *,
              remote_newer: bool) -> tuple[dict, list[str]]:
    """필드 병합 결과와 '원격이 이긴 충돌 필드' 목록을 돌려준다.

    `remote_newer`: 노드 수준 타이브레이크 결과(updated_at→rev→id). 진짜 충돌 필드에서
    **원격을 채택할지**를 이 한 값으로 정한다(같은 노드의 두 필드가 서로 다른 방향으로
    이기는 일은 없다 — 노드 단위 스탬프라 필드별 시각이 없다).

    반환 merged 는 **local 을 베이스로** 시작해, remote 만 바꾼 필드는 remote 값으로,
    충돌 필드는 승자 값으로 덮는다. local 만 바꾼 필드는 그대로 둔다(둘 다 보존).
    """
    merged = dict(local)
    conflicts: list[str] = []
    fields = set(base) | set(local) | set(remote)
    for f in fields:
        b, l, r = _norm(base.get(f)), _norm(local.get(f)), _norm(remote.get(f))
        local_delta = l != b
        remote_delta = r != b
        if not remote_delta:
            continue                      # 원격이 안 건드림 → local 유지(이미 merged)
        if not local_delta:
            merged[f] = remote.get(f)     # 원격만 건드림 → 원격 채택(겹치지 않음)
            continue
        if l == r:
            continue                      # 양쪽이 같은 값으로 수렴 → 충돌 아님
        # 진짜 충돌: 둘 다 base 에서 서로 다르게 바꿈 → 노드 타이브레이크로 승자
        if remote_newer:
            merged[f] = remote.get(f)
            conflicts.append(f)
    return merged, conflicts


def maps_equal(a: dict, b: dict) -> bool:
    """두 필드맵을 정규화 비교(list·dict·None 표현차 흡수). 병합이 로컬을 실제로
    바꿨는지 판정하는 데 쓴다."""
    for k in set(a) | set(b):
        if _norm(a.get(k)) != _norm(b.get(k)):
            return False
    return True


def node_remote_newer(local_rev: int, local_ua: str, remote_rev: int, remote_ua: str,
                      local_id: str, remote_id: str = "") -> bool:
    """노드 수준 승자 판정 — updated_at(HLC ISO, 고정폭 사전순=시간순) → rev → id.
    원격이 이기면 True. 동률까지 가면 id 로 결정론(양쪽에서 같은 결론)."""
    if (remote_ua or "") != (local_ua or ""):
        return (remote_ua or "") > (local_ua or "")
    if int(remote_rev or 0) != int(local_rev or 0):
        return int(remote_rev or 0) > int(local_rev or 0)
    return (remote_id or local_id) > local_id
