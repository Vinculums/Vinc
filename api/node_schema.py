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
