# -*- coding: utf-8 -*-
"""필드단위 3-way 병합(S3) — 발주자 핵심 요구의 순수 로직.

"서로 다른 필드를 고치면 둘 다 보존, 같은 필드 충돌만 최신승"을 여기서 못박는다.
데이터 손실 급소라 각 갈래를 명시적으로 검사한다. 순수 함수라 어디서든 실행된다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import syncmerge  # noqa: E402


def test_divergent_fields_both_survive():
    """핵심: 노트북이 title, 웹이 summary 를 고침 → 둘 다 남는다."""
    base = {"title": "T0", "summary": "S0", "domain": "d"}
    local = {"title": "T-laptop", "summary": "S0", "domain": "d"}   # 로컬이 title
    remote = {"title": "T0", "summary": "S-web", "domain": "d"}     # 원격이 summary
    merged, conflicts = syncmerge.three_way(base, local, remote, remote_newer=True)
    assert merged["title"] == "T-laptop"    # 로컬 편집 보존
    assert merged["summary"] == "S-web"     # 원격 편집 보존
    assert conflicts == []                  # 겹치지 않음 = 충돌 아님


def test_same_field_conflict_lww_remote_wins():
    base = {"title": "T0"}
    local = {"title": "T-laptop"}
    remote = {"title": "T-web"}
    merged, conflicts = syncmerge.three_way(base, local, remote, remote_newer=True)
    assert merged["title"] == "T-web" and conflicts == ["title"]


def test_same_field_conflict_lww_local_wins():
    base = {"title": "T0"}
    local = {"title": "T-laptop"}
    remote = {"title": "T-web"}
    merged, conflicts = syncmerge.three_way(base, local, remote, remote_newer=False)
    assert merged["title"] == "T-laptop"    # 로컬이 더 최신 → 로컬 값
    assert conflicts == []                  # 원격이 짐 → conflicts 에 안 담음(로컬 유지)


def test_remote_only_change_taken():
    base = {"title": "T0", "domain": "d0"}
    local = {"title": "T0", "domain": "d0"}          # 로컬 무변경
    remote = {"title": "T0", "domain": "d-new"}      # 원격만 domain
    merged, _ = syncmerge.three_way(base, local, remote, remote_newer=False)
    assert merged["domain"] == "d-new"               # remote_newer=False 여도 겹치지 않으면 채택


def test_local_only_change_preserved():
    base = {"title": "T0", "domain": "d0"}
    local = {"title": "T-laptop", "domain": "d0"}    # 로컬만 title
    remote = {"title": "T0", "domain": "d0"}         # 원격 무변경
    merged, _ = syncmerge.three_way(base, local, remote, remote_newer=True)
    assert merged["title"] == "T-laptop"             # 원격이 안 건드림 → 로컬 유지


def test_converge_same_value_no_conflict():
    """양쪽이 같은 값으로 바꿈 → 충돌 아님(우연의 일치)."""
    base = {"title": "T0"}
    merged, conflicts = syncmerge.three_way(
        base, {"title": "SAME"}, {"title": "SAME"}, remote_newer=True)
    assert merged["title"] == "SAME" and conflicts == []


def test_empty_base_treats_differences_as_conflict():
    """공통 조상 없음(base={}) → 값이 다르면 LWW, 같으면 수렴."""
    merged, conflicts = syncmerge.three_way(
        {}, {"title": "A"}, {"title": "B"}, remote_newer=True)
    assert merged["title"] == "B" and conflicts == ["title"]
    merged2, conflicts2 = syncmerge.three_way(
        {}, {"title": "A"}, {"title": "A"}, remote_newer=True)
    assert merged2["title"] == "A" and conflicts2 == []


def test_list_and_dict_fields_normalized():
    """tags(list)·props(dict)는 표현차(순서·키순서)를 흡수해 비교한다."""
    base = {"tags": ["a", "b"], "props": {"x": 1, "y": 2}}
    local = {"tags": ["a", "b"], "props": {"y": 2, "x": 1}}       # 키 순서만 다름 = 무변경
    remote = {"tags": ["a", "b", "c"], "props": {"x": 1, "y": 2}}  # 원격이 tags 추가
    merged, conflicts = syncmerge.three_way(base, local, remote, remote_newer=False)
    assert merged["tags"] == ["a", "b", "c"]     # 원격 tags 채택(로컬 무변경)
    assert conflicts == []                        # props 키순서차는 충돌 아님


def test_maps_equal_absorbs_representation():
    assert syncmerge.maps_equal({"t": ["a"], "p": None}, {"t": ["a"], "p": ""}) is True
    assert syncmerge.maps_equal({"p": {"a": 1, "b": 2}}, {"p": {"b": 2, "a": 1}}) is True
    assert syncmerge.maps_equal({"t": "x"}, {"t": "y"}) is False


def test_base_roundtrip():
    fields = {"title": "T", "tags": ["a"], "props": {"k": "v"}}
    assert syncmerge.parse_base(syncmerge.dump_base(fields)) == fields
    assert syncmerge.parse_base(None) == {} and syncmerge.parse_base("bad{") == {}
