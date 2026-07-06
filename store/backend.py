# -*- coding: utf-8 -*-
"""GraphStore abstraction — the one place a store backend is opened.

설계 정본 [[weg.c.memory-standalone]] §4 (P0). v1 유산에서는 7 엔진과
`api/query.py` 가 각자 드라이버를 열었다. 추상화 후에는 **스토어를 여는 곳이 이
파일 1곳**뿐이다.

Vinc 는 **임베디드 전용**(설계 §1 — 오프라인·외부 인프라 없음). 구현체는
`store/embedded_backend.py`(LadybugDB/Kùzu 계보) 하나다. `GraphStore` 추상화는
계보 포크(LadybugDB/RyuGraph/Kùzu) 간 교체를 어댑터 1파일로 상한 짓는 보험으로
유지된다(§2-2 R1). 서버 Neo4j 백엔드는 `weg-9000-memory` 레포 소관 — Vinc 미탑재.

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


def open_store(cfg: StoreConfig | None = None) -> GraphStore | None:
    """cfg.backend 에 따라 스토어를 연다. 드라이버 미설치 등 도달 불가 시 None
    (호출부는 executed=False 로 강등). **스토어를 여는 유일한 지점**(설계 §4-1)."""
    cfg = cfg or load_config()
    backend = (cfg.backend or "embedded").lower()
    if backend == "embedded":
        from .embedded_backend import EmbeddedBackend
        return EmbeddedBackend.open(cfg)
    raise ValueError(
        f"unknown/unsupported store backend: {cfg.backend!r} "
        "(Vinc 는 임베디드 전용 — 서버 Neo4j 는 weg-9000-memory 레포 소관)")
