# -*- coding: utf-8 -*-
"""migrate_pk_namespacing.transform 의 메타 처리.

두 결함을 고정한다. 둘 다 **조용히** 재발하는 종류다 — 하나는 행이 두 벌 남고,
하나는 남의 병합 조상을 물려준다. 어느 쪽도 오류를 내지 않는다.

실측으로 잡힌 경위(2026-09-02, Vinc#136):

  · `_SyncBase` 823행이 라이브 허브에 있어 이관이 예행에서 거절했다. 스크립트가
    "로컬 전용 표라 허브는 0" 을 전제했는데 틀렸다.
  · 그것을 조인으로 풀고 나니 검증이 "2행을 넣었는데 5행이다"로 떨어졌다. 원인은
    `_SyncBase` 가 아니라 **선재 결함**이었다: `_Tombstone` 1행·`_SyncBase` 0행짜리
    대조군도 "1행을 넣었는데 2행이다"로 같이 떨어졌다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import migrate_pk_namespacing as mig   # noqa: E402
from store import pkns                 # noqa: E402

OWNER = "user:a"


def _physical(node_id: str) -> str:
    """기대값을 **글자로** 짓는다. `pkns.compose` 로 지으면 안 된다 — 그것은 플래그가
    꺼져 있으면 항등함수이고, `namespacing_on()` 블록 **밖**에서 부르면 논리 키를
    돌려준다. 즉 검사 대상과 기대값이 같은 함수를 타면서 서로를 봐주게 된다."""
    return f"{OWNER}{pkns.SEP}{node_id}"


def _dump(nodes: list[tuple[str, str]]) -> dict:
    """Concept 만 담은 최소 덤프. `meta` 는 export_store 가 싣는 그대로 넣는다 —
    그 자리가 이중 기입의 출처이므로 비워 두면 검사할 것이 없어진다."""
    return {
        "nodes": {"Concept": [{"concept_id": cid, "owner_id": o, "title": cid}
                              for cid, o in nodes]},
        "edges": [],
        "meta": {"_Tombstone": [{"node_id": "concept:dead", "label": "Concept",
                                 "owner_id": OWNER, "deleted_at": "2026-08-01T00:00:00Z",
                                 "rev": 1, "sync_seq": 1, "origin": ""}]},
    }


def _base(node_id: str) -> dict:
    return {"node_id": node_id, "label": "Concept", "fields": "{}",
            "base_rev": 1, "base_updated_at": "2026-08-07T00:00:00Z"}


def test_dump_meta_is_emptied_so_import_does_not_double_write():
    """import_store 가 덤프의 논리 메타를 적재하고 write_meta 가 합성 메타를 또 쓰면
    PK 가 달라 MERGE 가 안 겹치고 **행이 두 벌 남는다.** `_SyncBase` 와 무관하다 —
    이 덤프에는 `_Tombstone` 만 있다."""
    dump = _dump([("concept:alpha", OWNER)])
    with mig.namespacing_on():
        new_dump, new_meta, _ = mig.transform(dump, dict(dump["meta"]))
    assert new_dump["meta"] == {}, "덤프에 메타가 남으면 import_store 가 논리 키로 또 쓴다"
    # 나르는 것은 write_meta 쪽 한 벌뿐이고, 그쪽 키는 합성돼 있어야 한다.
    assert len(new_meta["_Tombstone"]) == 1
    assert new_meta["_Tombstone"][0]["node_id"] == _physical("concept:dead")


def test_sync_base_node_id_is_composed_from_the_joined_owner():
    """`_SyncBase` 에는 owner 열이 없다. `node_id` 가 노드 id 이고 키가 아직 전역이라
    그 노드의 owner 는 조인으로 나온다 — 추측이 아니다."""
    dump = _dump([("concept:alpha", OWNER)])
    meta = {**dump["meta"], "_SyncBase": [_base("concept:alpha")]}
    with mig.namespacing_on():
        _, new_meta, stats = mig.transform(dump, meta)
    assert [r["node_id"] for r in new_meta["_SyncBase"]] == [_physical("concept:alpha")]
    assert stats["syncbase_orphans_dropped"] == 0


def test_sync_base_orphan_is_dropped_and_counted():
    """노드가 사라진 기준본은 합성할 owner 가 없다. 논리 키로 남기면 플래그가 켜진
    런타임이 영영 못 읽으므로 버린다 — 다만 **세어서** 보고한다."""
    dump = _dump([("concept:alpha", OWNER)])
    meta = {**dump["meta"],
            "_SyncBase": [_base("concept:alpha"), _base("concept:gone")]}
    with mig.namespacing_on():
        _, new_meta, stats = mig.transform(dump, meta)
    assert len(new_meta["_SyncBase"]) == 1
    assert stats["syncbase_orphans_dropped"] == 1


def test_sync_base_refuses_when_the_bases_span_two_owners():
    """좁힌 거절. 소유자가 둘 이상이면 어느 행을 누구의 동기화가 썼는지 이 표에 안
    남아 있어(감사 M3) 남의 조상을 물려줄 수 있다."""
    dump = _dump([("concept:alpha", OWNER), ("concept:beta", "user:b")])
    meta = {**dump["meta"],
            "_SyncBase": [_base("concept:alpha"), _base("concept:beta")]}
    with pytest.raises(SystemExit) as e:
        with mig.namespacing_on():
            mig.transform(dump, meta)
    assert "소유자 2명" in str(e.value)


def test_sync_base_single_owner_passes_where_two_would_not():
    """위 테스트의 짝. 같은 모양인데 소유자만 하나면 통과해야 한다 — 거절이 넓어져
    아무것도 못 옮기게 되는 것을 막는다."""
    dump = _dump([("concept:alpha", OWNER), ("concept:beta", OWNER)])
    meta = {**dump["meta"],
            "_SyncBase": [_base("concept:alpha"), _base("concept:beta")]}
    with mig.namespacing_on():
        _, new_meta, _ = mig.transform(dump, meta)
    assert len(new_meta["_SyncBase"]) == 2
