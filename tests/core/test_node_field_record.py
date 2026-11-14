# -*- coding: utf-8 -*-
"""변경 추적·동기화 필드 표가 `api.node_schema.NODE_FIELDS` 한 기록에서 나오고, 그 값이 기록으로
옮기기 전의 표와 원소·순서까지 같은가.

아래 `FROZEN_*` 는 옮기기 전 `api/revstamp.py`·`api/sync.py` 에 글자로 적혀 있던 표를 그대로 옮긴
사본이다. 표를 의도해서 바꿀 때는 `NODE_FIELDS` 를 고치고 이 사본도 같은 커밋에서 고친다.
순서도 본다: 동기화 표의 순서는 스냅샷 dict·로컬 SELECT 의 열 순서가 되고, 추적 표의 순서는
노드 쓰기의 현재값 조회 열 순서가 된다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import node_schema, revstamp, sync  # noqa: E402

FROZEN_TRACKED = {
    "Concept": ("title", "domain", "tags", "props", "topic", "one_liner", "status", "stem",
                "content_time"),
    "Episode": ("title", "date", "domain", "summary", "tags", "pinned", "props"),
    "Record": ("title", "domain", "tags", "props", "content_time", "valid_from", "valid_until"),
    "Bridge": ("title", "status", "source_domain", "target_domain", "props"),
    "Entity": ("type", "title", "tags", "theme_id"),
    "Decision": ("title", "domain", "tags", "props"),
}
FROZEN_SYNC_FIELDS = {
    "Concept": ("title", "domain", "tags", "props", "content_time", "one_liner"),
    "Episode": ("title", "date", "domain", "summary", "tags", "props"),
    "Record": ("title", "domain", "tags", "props", "content_time", "valid_from", "valid_until"),
    "Decision": ("title", "domain", "tags", "props"),
}
FROZEN_LATE_SYNC_FIELDS = {"Concept": ("one_liner",), "Episode": ("props",)}


def _same(got: dict, want: dict) -> None:
    # dict 비교는 순서를 안 본다. 라벨 순서와 열 순서를 둘 다 본다.
    assert list(got.items()) == list(want.items())
    assert all(type(v) is tuple for v in got.values())


def test_추적_표는_기록에서_나오고_옮기기_전과_같다():
    _same(node_schema.tracked_fields_table(), FROZEN_TRACKED)
    _same(revstamp.TRACKED, FROZEN_TRACKED)
    assert revstamp.tracked_fields("Concept") == FROZEN_TRACKED["Concept"]


def test_동기화_표는_기록에서_나오고_옮기기_전과_같다():
    _same(node_schema.sync_fields_table(), FROZEN_SYNC_FIELDS)
    _same(sync.SYNC_FIELDS, FROZEN_SYNC_FIELDS)


def test_나중에_든_동기화_필드는_기록에서_나오고_옮기기_전과_같다():
    _same(node_schema.late_sync_fields_table(), FROZEN_LATE_SYNC_FIELDS)
    _same(sync._LATE_SYNC_FIELDS, FROZEN_LATE_SYNC_FIELDS)


def test_기록의_꼴():
    for label, fields in node_schema.NODE_FIELDS.items():
        names = [f.name for f in fields]
        assert len(names) == len(set(names)), label
        for f in fields:
            assert not f.late or f.synced, (label, f.name)   # 나중에 든 필드는 나르는 필드다
