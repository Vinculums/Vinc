# -*- coding: utf-8 -*-
"""동기화 변경추적 헬퍼(S0) — HLC 시각·추적필드·변경감지.

왜 필요한가: 양방향 동기화는 "지난 동기화 이후 무엇이 바뀌었나"를 알아야 한다.
현 스키마엔 수정시각·버전이 없었다(S0 마이그레이션이 updated_at/rev/sync_seq 추가).
이 모듈은 쓰기 경로가 그 셋을 **의미 있게** 찍도록 돕는다:

  - **멱등 재적재는 안 찍는다** — 값이 같으면 스탬프 생략(안 그러면 매 재적재가 가짜
    충돌을 만든다). `differs()` 로 인바운드와 현재값을 비교한 뒤에만 bump.
  - **HLC**(하이브리드 논리시계) — 로컬/서버 벽시계 스큐에도 스토어 내 **단조** 보장.
    updated_at 은 사람이 읽는 정렬가능 ISO(밀리초)지만, 밑바탕은 max(물리ms, last+1).
  - **sync_seq** — owner별 단조 카운터. push/pull delta(since) 커서의 원천.

상태(last_hlc·seq:{owner})는 `_SyncMeta` 노드 테이블에 보관. 단일 사용자 규모라
쓰기당 메타 왕복 2회는 무시 가능.
"""
from __future__ import annotations

import datetime
import time

from .syncmerge import _norm

# 라벨별 **추적필드**(동기화되는 콘텐츠). owner_id(스코프)·created·source_file,
# 그리고 추적열 자신(updated_at/rev/sync_seq)과 preference 신호(access_count/
# last_access)는 제외 — recall 때마다 델타가 생기는 것을 막는다.
# ⚠ `content_time`(D72)은 여기 **있어야** 한다: 내용 시점만 고친 편집도 변경이고,
# 안 세면 sync_seq 가 안 올라 그 편집이 이 기기를 못 떠난다. 열이 있는 라벨에만 넣는다
# (Concept·Record. Decision·Episode·Entity 에는 열이 없어 넣으면 조회가 Binder 로 죽는다
# — 2026-08-17 실엔진 확인).
# ⚠ 반대로 `created`(출생)는 **넣으면 안 된다**. 그것은 편집이 아니라서, 추적하면
# 출생 백필이 전 노드를 내용 변경으로 보이게 만들어 그래프 전체를 스탬프한다.
TRACKED: dict[str, tuple[str, ...]] = {
    "Concept": ("title", "domain", "tags", "props", "topic", "one_liner", "status", "stem",
                "content_time"),
    "Episode": ("title", "date", "domain", "summary", "tags", "pinned"),
    # v15(W13) — 유효구간 둘도 추적한다. content_time 과 같은 이유다: 구간만 고친
    # 편집(무효화가 정확히 그 모양이다)도 변경이고, 안 세면 그 무효화가 기기를 못 떠난다.
    "Record": ("title", "domain", "tags", "props", "content_time",
               "valid_from", "valid_until"),
    "Bridge": ("title", "status", "source_domain", "target_domain", "props"),
    "Entity": ("type", "title", "tags", "theme_id"),
    # v12 — 결정. 열이 Record 와 같은 집합이다(제목·주제·태그·자유 속성).
    "Decision": ("title", "domain", "tags", "props"),
}


def tracked_fields(label: str) -> tuple[str, ...]:
    return TRACKED.get(label, ())


class _SessionRunner:
    """neo4j 동형 session(`run(cypher, **kwargs)`)을 store 인터페이스
    (`run(cypher, params_dict) -> list[dict]`)로 감싼다.

    왜 필요한가: 엔진(engines/*)은 session 으로 쓰고, revstamp 헬퍼는 store 규약을
    쓴다. 읽기·스탬프·쓰기를 **같은 커넥션**에 묶어야 한 배치 안의 연속 쓰기가 서로를
    본다(별도 커넥션은 MVCC 스냅샷이 달라 같은 id 재적재를 신규로 오판할 수 있다)."""

    def __init__(self, session):
        self._s = session

    def run(self, cypher: str, params: dict | None = None) -> list[dict]:
        return list(self._s.run(cypher, **(params or {})))


def session_runner(session) -> _SessionRunner:
    return _SessionRunner(session)


def _meta_get(store, key: str, default: str = "0") -> str:
    rows = store.run("MATCH (m:_SyncMeta) WHERE m.key = $k RETURN m.value AS v", {"k": key})
    v = rows[0].get("v") if rows else None
    return default if v in (None, "") else str(v)


def _meta_set(store, key: str, value) -> None:
    store.run("MERGE (m:_SyncMeta {key: $k}) SET m.value = $v", {"k": key, "v": str(value)})


def _fmt(ms: int) -> str:
    """epoch-ms → 정렬가능·가독 ISO(밀리초, UTC). 고정폭이라 사전순=시간순."""
    dt = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms % 1000:03d}Z"


def hlc_now(store) -> str:
    """스토어 내 단조 HLC 타임스탬프(ISO 밀리초). 벽시계 후퇴/동시쓰기에도 전진."""
    phys = int(time.time() * 1000)
    last = int(_meta_get(store, "last_hlc", "0"))
    cur = phys if phys > last else last + 1
    _meta_set(store, "last_hlc", cur)
    return _fmt(cur)


def observe_hlc(store, remote_updated_at: str) -> None:
    """원격에서 받은 updated_at 로 last_hlc 전진(pull apply 시). 파싱 실패는 무시."""
    if not remote_updated_at:
        return
    try:
        base = remote_updated_at.rstrip("Z")
        head, _, ms = base.partition(".")
        dt = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
        val = int(dt.timestamp() * 1000) + (int(ms) if ms.isdigit() else 0)
    except Exception:
        return
    last = int(_meta_get(store, "last_hlc", "0"))
    if val > last:
        _meta_set(store, "last_hlc", val)


def next_seq(store, owner: str) -> int:
    """owner별 단조 sync_seq(delta 커서 원천). owner 없으면 로컬 기본."""
    key = f"seq:{owner or 'user:local'}"
    cur = int(_meta_get(store, key, "0")) + 1
    _meta_set(store, key, cur)
    return cur


def make_stamp(store, current: dict) -> dict:
    """현재 행({rev, owner})에서 다음 스탬프 {updated_at, rev, sync_seq} 생성.

    호출부가 **실제 변경임을 이미 판정한 뒤**에만 부른다(pin/tag 등 단일필드 편집).
    fragment 경로는 자체 변경감지를 하므로 이 헬퍼를 쓰지 않는다."""
    owner = current.get("owner") or "user:local"
    return {"updated_at": hlc_now(store),
            "rev": int(current.get("rev") or 0) + 1,
            "sync_seq": next_seq(store, owner)}


def differs(current: dict, incoming: dict) -> bool:
    """incoming(쓰려는 추적필드 값)이 current(현재 DB 값)와 하나라도 다르면 True.
    incoming 에 실린 키만 비교 — 부분 쓰기라 미기재 필드는 '변경 아님'으로 본다.

    값 비교는 동기화 병합과 **같은 정규화**(`syncmerge._norm`)를 쓴다: props 키 순서만
    바뀐 쓰기나 None 과 '' 의 교체는 변경이 아니다. 둘이 갈리면 sync 가 "변경 없음"으로
    보는 쓰기가 여기서는 rev 를 올려 `expected_rev` 가 헛되이 어긋난다."""
    for k, v in incoming.items():
        if _norm(current.get(k)) != _norm(v):
            return True
    return False
