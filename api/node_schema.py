# -*- coding: utf-8 -*-
"""노드 종류·엣지 타입·라벨별 필드 표 — 쓰기·공유·동기화·문서 경로가 같이 읽는 스키마 상수.

이 모듈은 잎이다: 표준 라이브러리 밖에서는 아무것도 import 하지 않는다. 그래서 `api` 의
어느 모듈이든 지연 import 없이 최상위에서 이 모듈을 읽을 수 있다.

`api/fragment.py` 와 `api/docstore.py` 는 사설 이름(`_NODE_MAP`·`_KEPT_FIELDS`·
`_norm_content_time` …)을 이 모듈의 이름으로 그대로 가리키므로 두 이름은 같은 객체다.
값은 여기서 고친다. 따로 적힌 사본(`sync._REL_TO_FRAGMENT`·`query._OWNER_SCOPED_LABELS`)은
`tests/core/test_node_schema.py` 가 이 값과 대조하고, 필드 표는
`tests/core/test_node_field_tables.py` 가 다른 표·실제 열과 대조한다.
"""
from __future__ import annotations

import re
from typing import NamedTuple

#: fragment 타입 → (라벨, PK 열). 전용 테이블을 가진 여섯 종류다. 여기 없는 타입은 개방층
#: `Entity` 로 간다. 순서는 스키마 절이 id 접두사를 늘어놓는 순서다(`graphmd.schema_section`).
NODE_MAP: dict[str, tuple[str, str]] = {
    "record": ("Record", "record_id"),
    "concept": ("Concept", "concept_id"),
    "domain": ("Domain", "name"),
    "bridge": ("Bridge", "bridge_id"),
    "episode": ("Episode", "episode_id"),
    "decision": ("Decision", "decision_id"),
}

#: 의미층(개념·결정) 사이의 fragment 엣지 타입 → rel 테이블.
CC_EDGES: dict[str, str] = {
    "relates_to": "RELATED_TO",
    "prerequisite_of": "PREREQUISITE_OF",
    "derives_from": "DERIVES_FROM",
    "inherits_from": "INHERITS_FROM",
    "returns_to": "RETURNS_TO",
}
#: 사건·결정이 대상을 가리키는 엣지. `CC_EDGES` 에 섞지 않는다: 공유 팩의 엣지 조회
#: (`share._share_edges_plan`)가 `CC_EDGES` 의 rel 마다 개념·결정 끝점 쌍을 펼치므로,
#: 섞이면 스키마에 없는 쌍을 조회한다. 두 표가 만나는 곳은 아래 `REL_TO_FRAGMENT` 하나다.
EC_EDGES: dict[str, str] = {"about": "ABOUT"}

#: 대체 관계의 fragment 엣지 타입과 rel 테이블. 새 주장에서 그것이 대체한 주장으로 향한다.
SUPERSEDES_EDGE, SUPERSEDES_REL = "supersedes", "SUPERSEDES"
#: 대체가 설 수 있는 종류. 양 끝이 같은 종류일 때만 쓰인다.
SUPERSEDES_KINDS: tuple[str, ...] = ("record", "decision", "concept")

#: rel 테이블 → fragment 엣지 타입. `CC_EDGES` 와 `EC_EDGES` 의 역방향이다. 대체와 Record 엣지는
#: 싣지 않는다: 대체는 근거를 함께 나르는 전용 경로가 있고, Record 엣지의 원래 타입은 rel 라벨이
#: 아니라 `ATTACHES_TO` 엣지의 `type` 열에 있다.
REL_TO_FRAGMENT: dict[str, str] = {rel: ft for ft, rel in {**CC_EDGES, **EC_EDGES}.items()}

#: owner_id 열을 가진 라벨. 쓰기(`fragment._merge_node` 의 SEC-3 가드)와 되읽기
#: (`fragment._stored_nodes`)가 같은 목록을 봐야 한다. 갈라지면 되읽기가 0행을 만나고,
#: 호출부는 "방금 쓴 것이 없다"로 읽는다. 빈 owner 행 정리(`owner_fold`)도 이 목록을 읽는다.
OWNER_SCOPED_LABELS: tuple[str, ...] = ("Record", "Concept", "Episode", "Bridge", "Entity",
                                        "Decision")

#: 계약이 선언한 선택 필드 중 **그 라벨이 실제로 열에 쓰는 것**. 쓰기 경로는 이 표 밖의
#: 계약 필드를 실어 오면 버렸다고 경고한다(`fragment._warn_unkept_fields`). 이 표가 없으면
#: 라벨이 안 쓰는 필드를 말없이 버리고 `merged` 는 성공으로 답한다. 결정에는
#: `content_time` 열이 없다: [[vinc.plan.sync-node-time-fields]] §6.2 인벤토리.
#: 되읽기 열도 이 표를 쓴다(`fragment._stored_nodes`).
KEPT_FIELDS: dict[str, frozenset[str]] = {
    "Concept":  frozenset({"title", "domain", "tags", "props", "one_liner", "content_time"}),
    # 유효구간 둘은 Record 에만 있다. 다른 종류에 실으면 경고가 나고 값은 버려진다.
    "Record":   frozenset({"title", "domain", "tags", "props", "content_time",
                           "valid_from", "valid_until"}),
    "Decision": frozenset({"title", "domain", "tags", "props"}),
    "Episode":  frozenset({"title", "domain", "tags", "props", "date", "summary"}),
    # Bridge 에는 tags 열이 없다. 여기 tags 를 두면 SET 이 Binder 로 죽어 노드와
    # 엣지가 함께 떨어지고, 되읽기도 없는 열을 투영해 실패한다.
    "Bridge":   frozenset({"title", "props"}),
    "Domain":   frozenset(),          # 열이 `name` 하나뿐이다
    "Entity":   frozenset({"title", "tags"}),
}
#: 위 표가 보는 필드 전체. 여기 없는 키(오타·상류 메타)는 **묻지 않는다**: 팩 가져오기·
#: 동기화가 부가 키를 싣는 경로가 있고, 모르는 키까지 경고하면 정상 왕복이 소음으로 덮인다.
#: 잡으려는 것은 "계약에 있는 필드를 엉뚱한 종류에 실었다"다.
CONTRACT_FIELDS: frozenset[str] = frozenset({"title", "domain", "tags", "props", "one_liner",
                                             "content_time", "date", "summary",
                                             "valid_from", "valid_until"})

#: 대체 엣지의 구간 경고와 스키마 절이 가리키는 구간 필드 이름. 저작자가 그대로 따를 수 있는
#: 자리여야 한다: Decision 에는 `valid_until` 열이 없어 그 이름으로 보내면 버려지고, 구간은
#: props 에 둔다(`fragment._interval_of` 가 읽는 자리, 읽기 게이트가 보는 자리와 같다).
INTERVAL_FIELDS: dict[str, tuple[str, str]] = {
    "record": ("valid_from", "valid_until"),
    "decision": ("props.valid_from", "props.valid_until"),
}

class NodeField(NamedTuple):
    """라벨의 열 하나가 변경 추적·동기화에서 맡는 자리.

    - 표에 든 열은 전부 **변경 추적** 대상이다(`revstamp.TRACKED`): 그 열만 고친 편집도 rev·sync_seq 를
      올린다.
    - ``synced`` 는 동기화가 나른다는 뜻이다(`sync.SYNC_FIELDS`).
    - ``late`` 는 동기화에 나중에 들었다는 뜻이다(`sync._LATE_SYNC_FIELDS`): 그 필드를 모르는
      클라이언트의 번들에는 키가 없고, apply 는 키가 없으면 그 필드를 말하지 않은 것으로 본다."""
    name: str
    synced: bool = False
    late: bool = False


def _f(name: str, *flags: str) -> NodeField:
    return NodeField(name, synced="sync" in flags or "late" in flags, late="late" in flags)


#: 라벨별 변경 추적·동기화 필드의 정본. 라벨 순서와 라벨 안의 열 순서가 곧 `revstamp.TRACKED` 의
#: 순서다. 동기화 표의 열 순서는 먼저 든 필드(이 순서 그대로) 뒤에 나중에 든 필드다
#: (:func:`sync_fields_table`). 세 표는 이 기록에서만 만든다.
#:
#: 추적 표의 규칙. owner_id(스코프)·created·source_file, 그리고 추적열 자신(updated_at/rev/sync_seq)과
#: preference 신호(access_count/last_access)는 넣지 않는다 — recall 때마다 델타가 생기는 것을 막는다.
#: ⚠ `content_time`(D72)은 **있어야** 한다: 내용 시점만 고친 편집도 변경이고, 안 세면 sync_seq 가 안
#: 올라 그 편집이 이 기기를 못 떠난다. 열이 있는 라벨에만 넣는다(Concept·Record. Decision·Episode·Entity
#: 에는 열이 없어 넣으면 조회가 Binder 로 죽는다 — 2026-08-17 실엔진 확인).
#: ⚠ 반대로 `created`(출생)는 **넣으면 안 된다**. 그것은 편집이 아니라서, 추적하면 출생 백필이 전 노드를
#: 내용 변경으로 보이게 만들어 그래프 전체를 스탬프한다.
#:
#: 동기화 표의 규칙(=merge_fragment 가 실제로 왕복시키는 것만, S3). 급소: **fragment 가 왕복시키는
#: 필드만** 나른다 — 그 밖의 필드(Concept 의 topic·status·stem, Episode 의 pinned)는 동기화하면 유실된다
#: ([[vinc.spec.dataplane §6.1]]). ⚠ 열이 있는 라벨에만 넣을 것 — 없는 라벨에 넣으면
#: `sync._local_snapshot` 의 SELECT 가 Binder 로 죽는다. ⚠ `created`(출생)는 동기화에도 넣지 않는다 —
#: 3-way 병합 대상이 아니고 규칙이 다르다(`sync._reconcile_birth` — 가장 이른 값이 이긴다 ·
#: [[vinc.plan.sync-node-time-fields]]).
NODE_FIELDS: dict[str, tuple[NodeField, ...]] = {
    # Concept 의 one_liner 는 fragment 가 왕복시키므로 동기화에 든다(나중에 든 필드).
    "Concept": (_f("title", "sync"), _f("domain", "sync"), _f("tags", "sync"),
                _f("props", "sync"), _f("topic"), _f("one_liner", "late"), _f("status"),
                _f("stem"), _f("content_time", "sync")),
    # props 도 추적한다. 동기화가 Episode props 를 나르므로, 안 세면 props 만 고친 편집이
    # sync_seq 를 못 올려 이 기기를 못 떠난다(동기화에는 나중에 든 필드).
    "Episode": (_f("title", "sync"), _f("date", "sync"), _f("domain", "sync"),
                _f("summary", "sync"), _f("tags", "sync"), _f("pinned"), _f("props", "late")),
    # Record — 라이브 실측 277건으로 **노드 종류 중 최대**인데 동기화 표에 없어서 기기와 계정
    # 사이를 통째로 못 오갔다. 열은 Concept 과 같다(content_time 은 v7 이 Concept·Record 둘 다에 붙였다).
    # v15(W13) — 유효구간 둘도 추적하고 나른다. content_time 과 같은 이유다: 구간만 고친 편집
    # (무효화가 정확히 그 모양이다)도 변경이고, 안 세거나 안 나르면 그 무효화가 기기를 못 떠나
    # 다른 기기에서 낡은 주장이 유효한 채 남는다.
    "Record": (_f("title", "sync"), _f("domain", "sync"), _f("tags", "sync"), _f("props", "sync"),
               _f("content_time", "sync"), _f("valid_from", "sync"), _f("valid_until", "sync")),
    # Bridge·Entity 는 동기화하지 않는다(`sync._SYNC_TYPES` 의 스코프 결정).
    "Bridge": (_f("title"), _f("status"), _f("source_domain"), _f("target_domain"), _f("props")),
    "Entity": (_f("type"), _f("title"), _f("tags"), _f("theme_id")),
    # v12 — 결정. 열이 Record 와 같은 집합이다(제목·주제·태그·자유 속성). 동기화에서 **빼면 안
    # 된다**: 결정은 여태 Concept 이었고 그래서 동기화되고 있었다. 라벨만 옮기고 안 나르면 기기 간에
    # 결정이 조용히 안 넘어간다(오류도 경고도 없이).
    "Decision": (_f("title", "sync"), _f("domain", "sync"), _f("tags", "sync"), _f("props", "sync")),
}


def tracked_fields_table() -> dict[str, tuple[str, ...]]:
    """라벨 → 변경 추적 열(`revstamp.TRACKED`)."""
    return {label: tuple(f.name for f in fields) for label, fields in NODE_FIELDS.items()}


def sync_fields_table() -> dict[str, tuple[str, ...]]:
    """라벨 → 동기화 열(`sync.SYNC_FIELDS`). 먼저 든 필드 뒤에 나중에 든 필드. 나르는 열이 없는 라벨은 뺀다."""
    out: dict[str, tuple[str, ...]] = {}
    for label, fields in NODE_FIELDS.items():
        early = [f.name for f in fields if f.synced and not f.late]
        late = [f.name for f in fields if f.late]
        if early or late:
            out[label] = tuple(early + late)
    return out


def late_sync_fields_table() -> dict[str, tuple[str, ...]]:
    """라벨 → 동기화에 나중에 든 열(`sync._LATE_SYNC_FIELDS`). 그런 열이 없는 라벨은 뺀다."""
    return {label: tuple(f.name for f in fields if f.late)
            for label, fields in NODE_FIELDS.items() if any(f.late for f in fields)}



_TIME_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def normalize_time(v: str | None) -> str:
    """`YYYY` · `YYYY-MM` · `YYYY-MM-DD` 만 통과시킨다. 나머지는 빈 문자열.

    노드의 `content_time`·유효구간·사건 `date`·결정 구간·`measured_at`·`reviewed_at` 과
    문서의 `content_time` 이 이 검사를 쓴다.
    형식이 곧 계약이다: 이 축의 비교·버킷은 전부 **문자열 사전식**이라(날짜 파싱을
    도입하지 않는다 — `ts` 계약과 동형) 자릿수가 어긋나면 정렬이 조용히 무너진다.
    `1905-6` 처럼 자리를 안 채운 값도 거부하는 이유가 그것이다. 기원전·음수 연도는
    미지원이고, 불합격은 던지지 않고 **비운다** — 저장은 성공하되 축에서 빠진다."""
    s = str(v or "").strip()
    return s if _TIME_RE.match(s) else ""
