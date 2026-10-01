# -*- coding: utf-8 -*-
"""툼스톤의 소유자 스코프(감사 2026-08-18 M3).

`_Tombstone` 의 PK 는 `node_id` 하나이고 노드 id 는 전역 네임스페이스라, 슬러그가
겹치는 두 소유자는 한 행을 나눠 쓰게 된다. 소유자마다 자기 행을 갖는 것은 PK 를
바꾸는 일이라 H1 재구축에 태우고, 여기서는 **열이 이미 있는 쪽**을 닫는다:
남의 행을 읽지도, 지우지도, 덮지도 않는다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import sync   # noqa: E402

A = "user:aaa"
B = "user:bbb"


class _Rows:
    """툼스톤 표 한 장을 흉내 낸다 — PK 는 node_id 하나(실제 스키마와 같다)."""

    def __init__(self):
        self.row: dict | None = None
        self.seq = 0

    def run(self, cypher, params=None):
        p = params or {}
        c = " ".join(cypher.split())
        if "_SyncMeta" in c or "seq" in c.lower() and "MERGE (m" in c:
            return []
        if c.startswith("MATCH (t:_Tombstone)") and "RETURN t.owner_id" in c:
            return [{"o": self.row["owner_id"]}] if self.row else []
        if c.startswith("MATCH (t:_Tombstone)") and "RETURN t.deleted_at" in c:
            if not self.row:
                return []
            if "t.owner_id = $owner" in c and self.row["owner_id"] != p.get("owner"):
                return []
            return [{"da": self.row["deleted_at"], "rev": self.row["rev"]}]
        if c.startswith("MATCH (t:_Tombstone)") and "DELETE" in c:
            if self.row and ("t.owner_id = $owner" not in c
                             or self.row["owner_id"] == p.get("owner")):
                self.row = None
            return []
        if "MERGE (t:_Tombstone" in c:
            self.row = {"node_id": p["id"], "owner_id": p["o"],
                        "deleted_at": p["da"], "rev": p["rev"]}
            return []
        return []


def _put(s, owner, when):
    return sync.put_tombstone(s, "concept:x", "Concept", owner, when, 1)


def test_a_second_owner_does_not_clobber_the_first():
    s = _Rows()
    assert _put(s, A, "2026-01-01T00:00:00Z") is True
    assert _put(s, B, "2026-02-01T00:00:00Z") is False, "남의 툼스톤을 덮었다"
    assert s.row["owner_id"] == A
    assert s.row["deleted_at"] == "2026-01-01T00:00:00Z"


def test_one_owner_cannot_cancel_anothers_pending_delete():
    s = _Rows()
    _put(s, A, "2026-01-01T00:00:00Z")
    sync.del_tombstone(s, "concept:x", B)
    assert s.row is not None, "B 가 A 의 대기 중 삭제를 취소했다"
    sync.del_tombstone(s, "concept:x", A)
    assert s.row is None


def test_reading_is_scoped():
    s = _Rows()
    _put(s, A, "2026-01-01T00:00:00Z")
    assert sync.get_tombstone(s, "concept:x", A) is not None
    assert sync.get_tombstone(s, "concept:x", B) is None
    # owner 를 안 주면 예전처럼 전역 조회(호출부가 아직 없는 경로의 하위호환)
    assert sync.get_tombstone(s, "concept:x") is not None


def test_same_owner_still_updates_its_own_row():
    s = _Rows()
    _put(s, A, "2026-01-01T00:00:00Z")
    assert _put(s, A, "2026-03-01T00:00:00Z") is True
    assert s.row["deleted_at"] == "2026-03-01T00:00:00Z"
