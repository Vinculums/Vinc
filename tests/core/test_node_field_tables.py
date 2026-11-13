# -*- coding: utf-8 -*-
"""라벨별 필드 표 다섯 벌이 서로 맞는지 본다.

같은 "라벨 → 필드" 표가 다섯 곳에 따로 적혀 있다.

1. ``kept``     — `api.fragment._KEPT_FIELDS`: 쓰기 경로가 열에 실제로 남기는 필드.
2. ``tracked``  — `api.revstamp.TRACKED`: 고치면 변경 스탬프(`sync_seq`)가 오르는 필드.
3. ``synced``   — `api.sync.SYNC_FIELDS`: 기기와 계정 사이를 오가는 필드.
4. ``exported`` / ``bundled`` — `api.sync` 의 `_<종류>s_delta_plan` 이 읽는 열과
   `_node_from_<종류>` 가 번들 노드에 싣는 키. 둘이 합쳐 내보내기 한 벌이다.
5. ``schema``   — `store.embedded_schema.node_table_specs()`: 테이블의 실제 열.

필드 하나를 더할 때 한 곳이라도 빠지면 오류 없이 값이 사라진다(동기화되지 않거나,
스탬프가 안 올라 기기를 못 떠나거나, 없는 열을 읽어 조회가 Binder 로 죽는다).
이 파일은 표를 합치지 않는다. 지금 표들이 어떻게 다른지를 `KNOWN_DIFFERENCES` 에
데이터로 적고, 거기 없는 차이가 생기거나 적힌 차이가 사라지면 실패한다. 실패 메시지는
어느 표의 어느 라벨에서 어느 필드가 어긋났는지 말한다.

표를 한 벌로 합치는 패키지는 아래 `_tables()` 한 곳만 새 이름으로 바꾸고
`KNOWN_DIFFERENCES` 를 줄여 가면 된다.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment  # noqa: E402
from api import revstamp  # noqa: E402
from api import sync  # noqa: E402
from store import embedded_schema  # noqa: E402

#: 내보내기 SELECT 에서 필드가 아닌 장부 열. 노드 id·추적열 셋·출생은 본문이 아니라
#: 스탬프로 따로 다뤄진다(`_rev`·`_updated_at`·`_seq`·`_created`).
_EXPORT_BOOKKEEPING = frozenset({"id", "updated_at", "rev", "sync_seq", "created"})
#: 번들 노드에서 필드가 아닌 키. `_` 로 시작하는 스탬프는 따로 거른다.
_BUNDLE_BOOKKEEPING = frozenset({"id", "type"})
#: 내보내기 SELECT 의 `<변수>.<열> AS <이름>` 한 항목.
_SELECT_ITEM = re.compile(r"\b[a-z]\.([a-z_]+) AS ([a-z_]+)\b")

#: 라벨 자체가 한쪽 표에만 있다는 표시(필드 자리에 쓴다).
LABEL = "*"

#: 오늘 표들 사이의 차이 전부. 한 항목은 `(A, B, 라벨, 필드)` 이고 "A 의 그 라벨에는
#: 그 필드가 있는데 B 에는 없다"는 뜻이다. 필드가 `LABEL` 이면 라벨이 B 에 없다.
#: 모두 의도된 범위 차이다(이유는 묶음마다 적는다). 여기 없는 차이는 새 불일치다.
KNOWN_DIFFERENCES: frozenset[tuple[str, str, str, str]] = frozenset({
    # 노드가 아닌 라벨: Domain 은 열이 `name` 하나라 추적·동기화할 본문이 없다.
    ("kept", "tracked", "Domain", LABEL),
    ("kept", "synced", "Domain", LABEL),
    ("kept", "exported", "Domain", LABEL),
    ("kept", "bundled", "Domain", LABEL),
    # Bridge·Entity 는 동기화 범위 밖이다(`sync._SYNC_TYPES` 의 스코프 결정).
    ("kept", "synced", "Bridge", LABEL),
    ("kept", "exported", "Bridge", LABEL),
    ("kept", "bundled", "Bridge", LABEL),
    ("kept", "synced", "Entity", LABEL),
    ("kept", "exported", "Entity", LABEL),
    ("kept", "bundled", "Entity", LABEL),
    ("tracked", "synced", "Bridge", LABEL),
    ("tracked", "exported", "Bridge", LABEL),
    ("tracked", "bundled", "Bridge", LABEL),
    ("tracked", "synced", "Entity", LABEL),
    ("tracked", "exported", "Entity", LABEL),
    ("tracked", "bundled", "Entity", LABEL),
    # fragment 가 쓰지 않는 열을 다른 경로가 쓰고, 그 편집도 변경으로 센다.
    ("tracked", "kept", "Concept", "topic"),
    ("tracked", "kept", "Concept", "status"),
    ("tracked", "kept", "Concept", "stem"),
    ("tracked", "kept", "Episode", "pinned"),
    ("tracked", "kept", "Bridge", "status"),
    ("tracked", "kept", "Bridge", "source_domain"),
    ("tracked", "kept", "Bridge", "target_domain"),
    ("tracked", "kept", "Entity", "type"),
    ("tracked", "kept", "Entity", "theme_id"),
    # 같은 열을 동기화는 나르지 않는다. fragment 가 왕복시키지 못해 실으면 유실된다.
    ("tracked", "synced", "Concept", "topic"),
    ("tracked", "synced", "Concept", "status"),
    ("tracked", "synced", "Concept", "stem"),
    ("tracked", "synced", "Episode", "pinned"),
    ("tracked", "bundled", "Concept", "topic"),
    ("tracked", "bundled", "Concept", "status"),
    ("tracked", "bundled", "Concept", "stem"),
    ("tracked", "bundled", "Episode", "pinned"),
    # 내보내기는 topic·pinned 를 읽기만 하고 번들에는 싣지 않는다.
    ("exported", "kept", "Concept", "topic"),
    ("exported", "synced", "Concept", "topic"),
    ("exported", "bundled", "Concept", "topic"),
    ("exported", "kept", "Episode", "pinned"),
    ("exported", "synced", "Episode", "pinned"),
    ("exported", "bundled", "Episode", "pinned"),
    # status·stem 은 내보내기가 읽지도 않는다.
    ("tracked", "exported", "Concept", "status"),
    ("tracked", "exported", "Concept", "stem"),
})


def _tables() -> dict[str, dict[str, frozenset[str]]]:
    """다섯 표를 같은 모양(라벨 → 필드 집합)으로 읽는다. 표가 옮겨 가면 여기만 고친다."""
    synced_types = sync._SYNC_TYPES          # fragment 타입 → 라벨
    exported: dict[str, frozenset[str]] = {}
    bundled: dict[str, frozenset[str]] = {}
    for ntype, label in synced_types.items():
        plan = getattr(sync, f"_{ntype}s_delta_plan")(owner="o", domains=None, since=0)
        exported[label] = frozenset(
            alias for _col, alias in _SELECT_ITEM.findall(plan.cypher)
        ) - _EXPORT_BOOKKEEPING
        node = getattr(sync, f"_node_from_{ntype}")({"id": f"{ntype}:x"})
        bundled[label] = frozenset(
            k for k in node if not k.startswith("_")
        ) - _BUNDLE_BOOKKEEPING
    return {
        "kept": {lbl: frozenset(f) for lbl, f in fragment._KEPT_FIELDS.items()},
        "tracked": {lbl: frozenset(f) for lbl, f in revstamp.TRACKED.items()},
        "synced": {lbl: frozenset(f) for lbl, f in sync.SYNC_FIELDS.items()},
        "exported": exported,
        "bundled": bundled,
    }


def _schema_columns() -> dict[str, frozenset[str]]:
    return {lbl: frozenset(spec["cols"])
            for lbl, spec in embedded_schema.node_table_specs().items()}


def _differences(tables: dict[str, dict[str, frozenset[str]]]) -> set[tuple[str, str, str, str]]:
    """표 두 벌씩, 양쪽 방향으로, 한쪽에만 있는 라벨과 필드를 모은다."""
    out: set[tuple[str, str, str, str]] = set()
    for a, ta in tables.items():
        for b, tb in tables.items():
            if a == b:
                continue
            for label, fields in ta.items():
                if label not in tb:
                    out.add((a, b, label, LABEL))
                    continue
                out.update((a, b, label, f) for f in fields - tb[label])
    return out


def _describe(diff: tuple[str, str, str, str]) -> str:
    a, b, label, field = diff
    if field == LABEL:
        return f"{a} 표에는 라벨 {label} 이 있는데 {b} 표에는 없다"
    return f"{a} 표의 {label} 에는 '{field}' 가 있는데 {b} 표의 {label} 에는 없다"


class FieldTablesAgree(unittest.TestCase):

    def test_no_new_difference_between_the_tables(self):
        found = _differences(_tables())
        new = sorted(found - KNOWN_DIFFERENCES)
        if new:
            self.fail("새 불일치(한 표에만 더했거나 뺐다):\n  "
                      + "\n  ".join(_describe(d) for d in new))

    def test_every_known_difference_still_exists(self):
        # 표를 맞춰 차이가 사라졌으면 기록도 지운다. 남은 기록은 다음 불일치를 가린다.
        gone = sorted(KNOWN_DIFFERENCES - _differences(_tables()))
        if gone:
            self.fail("KNOWN_DIFFERENCES 에서 지울 항목(이제 차이가 없다):\n  "
                      + "\n  ".join(_describe(d) for d in gone))

    def test_every_field_is_a_real_column(self):
        # 없는 열을 SET·SELECT 하면 그 조회 전체가 Binder 로 죽는다.
        schema = _schema_columns()
        missing = []
        for name, table in _tables().items():
            for label, fields in sorted(table.items()):
                if label not in schema:
                    missing.append(f"{name} 표의 라벨 {label} 은 스키마에 테이블이 없다")
                    continue
                missing.extend(f"{name} 표의 {label} 에 '{f}' 가 있는데 스키마의 {label} 에는 그 열이 없다"
                               for f in sorted(fields - schema[label]))
        if missing:
            self.fail("\n  ".join(["스키마에 없는 필드:", *missing]))

    def test_synced_labels_are_the_sync_types(self):
        # 동기화 필드 표와 동기화 대상 라벨이 따로 적혀 있다. 한쪽만 늘면 그 라벨이 안 오간다.
        self.assertEqual(set(sync.SYNC_FIELDS), set(sync._SYNC_TYPES.values()),
                         "sync.SYNC_FIELDS 의 라벨과 sync._SYNC_TYPES 의 라벨이 다르다")
        node_labels = {lbl for lbl, _pk in fragment._NODE_MAP.values()}
        self.assertLessEqual(set(sync.SYNC_FIELDS), node_labels,
                             "sync.SYNC_FIELDS 에 fragment._NODE_MAP 이 모르는 라벨이 있다")

    def test_late_sync_fields_are_sync_fields(self):
        # 늦게 든 필드는 동기화 필드의 부분집합이어야 apply 가 "말하지 않음"으로 읽는다.
        for label, fields in sync._LATE_SYNC_FIELDS.items():
            extra = set(fields) - set(sync.SYNC_FIELDS.get(label, ()))
            self.assertFalse(extra, f"sync._LATE_SYNC_FIELDS 의 {label} 에 동기화 필드가 아닌 "
                             f"{sorted(extra)} 가 있다")

    def test_contract_fields_cover_every_kept_field(self):
        # 계약 필드가 판정 대상의 전부다. 여기 빠진 필드는 엉뚱한 라벨에 실어도 경고가 안 난다.
        kept = set().union(*fragment._KEPT_FIELDS.values())
        self.assertEqual(kept, set(fragment._CONTRACT_FIELDS),
                         "fragment._KEPT_FIELDS 의 필드 합집합과 fragment._CONTRACT_FIELDS 가 다르다")

    def test_the_parser_reads_every_export_plan(self):
        # SELECT 를 못 읽으면 위 비교가 빈 집합끼리 맞았다고 답한다.
        for label, fields in _tables()["exported"].items():
            self.assertIn("title", fields, f"{label} 내보내기 SELECT 를 읽지 못했다")


if __name__ == "__main__":
    unittest.main()
