# -*- coding: utf-8 -*-
"""GraphStore abstraction — the one place a store backend is opened.

설계 정본 [[vinc.canon.principles]] (P0). v1 유산에서는 7 엔진과
`api/query.py` 가 각자 드라이버를 열었다. 추상화 후에는 **스토어를 여는 곳이 이
파일 1곳**뿐이다.

기본 백엔드는 **임베디드**다(설계 §1 — 오프라인·외부 인프라 없음). 구현체는 지금
`store/embedded_backend.py`(LadybugDB/Kùzu 계보) 하나다. 사용자가 운영하는 외부 엔진
(Neo4j)의 어댑터도 이 `GraphStore` 뒤에 붙는다. 그래서 엔진 방언은 어댑터 파일에만
둔다 — `scripts/gate_engine_dialect.py` 가 어댑터 밖에서 늘지 못하게 막는다.

원자성 계약(§4-2): `GraphStore` 는 다중 `run()`/`session.run()` 에 걸친 트랜잭션을
**보장하지 않는다.** 복구 수단은 MERGE 멱등성 기반 재실행이다.

필터 계약(§4-2): `vector_search`/`fulltext_search` 의 `filters` 는 스코프 1차 불변
원칙(§1)의 검색 경로 구현 — 인덱스 질의에 술어로 밀어넣는다(pre-filter, v0.4 확정).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .config import StoreConfig, load_config


@runtime_checkable
class GraphStore(Protocol):
    """의도적으로 최소 표면 (설계 §4-2).

    두 접근 표면:
      - `run(query, params)` — 단발 실행(플랜 실행기 `api/query.py` 가 사용).
      - `session()` — 컨텍스트 매니저. 다중 `session.run(cypher, **kwargs)` 를 내는
        엔진 적재부가 사용(§4-3 최소 diff). yield 세션은 `.run(cypher, **kwargs)` →
        반복 가능 레코드 결과 + `.single()`.
    """

    def run(self, query: str, params: dict | None = None) -> list[dict]: ...
    def session(self): ...
    def provision(self, *, dim: int) -> list[str]: ...
    def vector_search(self, embedding: list[float], top_k: int,
                      filters: dict | None = None) -> list[dict]: ...
    def fulltext_search(self, text: str, top_k: int,
                        filters: dict | None = None) -> list[dict]: ...
    def snapshot(self, dest_dir: str) -> str: ...
    def close(self) -> None: ...


# ── 선택 능력 ─────────────────────────────────────────────────────────────────
# `GraphStore` 밖의 연산은 백엔드마다 있을 수도 없을 수도 있다. 백엔드는 가진 것을
# `capabilities`(frozenset) 로 선언하고, 호출부는 속성을 직접 더듬지 않고 아래 함수로 묻는다.
# ⚠ 선언은 실제 메서드와 같아야 한다: `tests/core/test_store_capabilities.py` 가 백엔드마다 대조한다.

#: 선택 연산. 있으면 호출하고 없으면 건너뛰는 메서드 이름이다.
OPTIONAL_OPERATIONS = frozenset({
    "current_batch", "write_batch", "in_manual_transaction",          # 쓰기 묶음
    "drop_vector_index", "drop_search_indexes", "ensure_search_indexes",
    "schedule_search_indexes", "is_fts_inconsistent", "rebuild_fts_index",  # 색인 수명
    "heal_fts_index", "heal_vector_index", "heal_node_vector_index",  # 기동 수리
    "migrate_lex_index", "table_columns", "snapshot",
})

#: 상태 표지. 없으면 False 로 읽는 속성이다(색인 잔해, 청크 팔의 owner 필터, 읽기 전용).
STATE_FLAGS = frozenset({
    "fts_index_remnant", "vector_index_remnant", "node_vector_index_remnant",
    "scoped_chunk_search", "read_only",
})


def capabilities(store) -> frozenset[str]:
    """``store`` 가 가진 선택 연산. 선언이 있으면 그것이고, 없으면(테스트 가짜·래퍼) 속성으로 센다."""
    declared = getattr(store, "capabilities", None)
    if isinstance(declared, frozenset):
        return declared
    return frozenset(n for n in OPTIONAL_OPERATIONS if getattr(store, n, None) is not None)


def operation(store, name: str):
    """선택 연산 ``name`` 의 바운드 메서드, 없으면 None. 모르는 이름은 던진다(오타가 조용히 '없음'이 되지 않게).

    선언한 백엔드는 선언에 없는 연산을 없다고 답한다. 선언이 없으면 속성이 있는가를 본다."""
    if name not in OPTIONAL_OPERATIONS:
        raise KeyError(f"unknown optional store operation: {name}")
    declared = getattr(store, "capabilities", None)
    if isinstance(declared, frozenset) and name not in declared:
        return None
    return getattr(store, name, None)


def flag(store, name: str):
    """상태 표지 ``name`` 의 값, 없으면 False. 모르는 이름은 던진다."""
    if name not in STATE_FLAGS:
        raise KeyError(f"unknown store state flag: {name}")
    return getattr(store, name, False)


def open_store(cfg: StoreConfig | None = None) -> GraphStore | None:
    """cfg.backend 에 따라 스토어를 연다. 드라이버 미설치 등 도달 불가 시 None
    (호출부는 executed=False 로 강등). **스토어를 여는 유일한 지점**(설계 §4-1)."""
    cfg = cfg or load_config()
    global _blocked
    _clear_open_errors()
    _blocked = str((cfg.extra or {}).get("open_blocked") or "")
    if _blocked:
        # 설정 자체가 틀려 열 대상이 없다(저장 위치 검사 실패). 다른 스토어로 물러서지 않는다.
        return None
    backend = (cfg.backend or "embedded").lower()
    if backend == "embedded":
        from .embedded_backend import EmbeddedBackend
        return EmbeddedBackend.open(cfg)
    if backend == "neo4j":
        from .neo4j_backend import Neo4jBackend
        return Neo4jBackend.open(cfg)
    raise ValueError(
        f"unknown/unsupported store backend: {cfg.backend!r} (supported: embedded, neo4j)")


#: 설정이 막아 열지 않은 사유(`open_blocked`). 마지막 `open_store` 호출의 것이다.
_blocked = ""


def _clear_open_errors() -> None:
    """새로 열 때 지난 시도의 사유를 지운다. 다른 엔진의 옛 사유가 이번 실패의 사유로 읽히면 안 된다."""
    global _blocked
    _blocked = ""
    from .embedded_backend import EmbeddedBackend
    from .neo4j_backend import Neo4jBackend
    EmbeddedBackend.last_open_error = ""
    Neo4jBackend.last_open_error = ""


def last_open_error() -> str:
    """마지막으로 스토어를 못 연 사유(어느 엔진이든). 없으면 빈 문자열."""
    from .embedded_backend import EmbeddedBackend
    from .neo4j_backend import Neo4jBackend
    return _blocked or Neo4jBackend.last_open_error or EmbeddedBackend.last_open_error
