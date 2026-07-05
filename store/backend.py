# -*- coding: utf-8 -*-
"""GraphStore abstraction — the one place a store backend is opened.

설계 정본 [[weg.c.memory-standalone]] §4 (P0 — 선행 필수). v1 유산에서는 7 엔진과
`api/query.py`·`store/provision.py` 가 각자 드라이버를 열었다. 추상화 후에는
**드라이버(또는 임베디드 스토어)를 여는 곳이 이 파일 1곳**뿐이다.

구현 2종:
  - `Neo4jBackend`      — 현행 Neo4j 동작 이관(회귀 0 목표, 마이그레이션 경로).
  - `EmbeddedBackend`   — 인프로세스 LadybugDB (P1, `embedded_backend.py`).
선택은 `cfg.backend`.

원자성 계약(§4-2): `GraphStore` 는 다중 `run()` 호출에 걸친 트랜잭션을 **보장하지
않는다.** 다중 문장 적재가 중간 실패하면 부분 상태가 남을 수 있으며, 복구 수단은
**MERGE 멱등성에 기반한 재실행**이다(같은 입력 재적재 시 수렴).

필터 계약(§4-2): `vector_search`/`fulltext_search` 의 `filters` 는 스코프 1차 불변
원칙(§1)의 검색 경로 구현이다. 백엔드는 필터를 **인덱스 질의 단계에 술어로**
밀어넣는 것을 원칙으로 한다(pre-filter — 단일 사용자 소규모, v0.4 확정). "top_k 선
추출 → 사후 필터로 축소"는 recall 붕괴를 일으키므로 계약 위반이다.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Protocol, runtime_checkable

from .config import StoreConfig, load_config


@runtime_checkable
class GraphStore(Protocol):
    """의도적으로 최소 표면 (설계 §4-2).

    두 접근 표면:
      - `run(query, params)` — 단발 실행(플랜 실행기 `api/query.py` 가 사용).
      - `session()` — 컨텍스트 매니저. 다중 `session.run(cypher, **kwargs)` 를 내는
        엔진 적재부가 사용한다(§4-3 최소 diff: 획득부만 store 로 바꾸고 호출부 보존).
        yield 되는 세션은 neo4j 세션과 동형(`.run(cypher, **kwargs)` → 반복 가능한
        레코드 결과, `.single()` 지원). 백엔드가 드라이버/스토어를 여는 유일 지점.
    """

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        """읽기/쓰기 공용 Cypher 실행 → 행 리스트(dict)."""
        ...

    def session(self):
        """`with store.session() as session:` — neo4j 동형 세션 컨텍스트."""
        ...

    def provision(self, *, dim: int) -> list[str]:
        """스키마 보장(멱등). 적용한(또는 적용할) 문장 리스트 반환."""

    def vector_search(self, embedding: list[float], top_k: int,
                      filters: dict | None = None) -> list[dict]:
        """벡터 인덱스 검색. filters = {"owner_id","domain","tenant_id"} (pre-filter)."""

    def fulltext_search(self, text: str, top_k: int,
                        filters: dict | None = None) -> list[dict]:
        """BM25 풀텍스트 인덱스 검색. filters 동일(pre-filter)."""

    def snapshot(self, dest_dir: str) -> str:
        """데이터 디렉토리의 일관 스냅샷(backup, §3-1-1). 산출 경로 반환."""

    def close(self) -> None:
        ...


# ── Neo4j backend (현행 동작 이관 — 회귀 0) ──────────────────────────────────

class Neo4jBackend:
    """Neo4j 드라이버 래퍼. `api/query.py._driver` + `store/provision.py._connect`
    의 로직을 단일 지점으로 이관한다. 동작은 현행과 동일(회귀 0)."""

    def __init__(self, driver, cfg: StoreConfig):
        self._driver = driver
        self._cfg = cfg

    @classmethod
    def open(cls, cfg: StoreConfig) -> "Neo4jBackend | None":
        """드라이버를 열어 백엔드를 반환한다. neo4j 패키지 부재·비밀번호 부재·
        연결 실패 시 **None**(호출부는 executed=False 로 강등)."""
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError:
            return None
        if not cfg.password:
            return None
        try:
            drv = GraphDatabase.driver(cfg.uri, auth=cfg.auth())
            drv.verify_connectivity()
        except Exception:
            return None
        return cls(drv, cfg)

    @contextmanager
    def session(self):
        """neo4j 세션을 그대로 yield — 엔진의 `session.run(cypher, **kwargs)` 무변경."""
        kw = {"database": self._cfg.database} if self._cfg.database else {}
        session = self._driver.session(**kw)
        try:  # pragma: no cover - requires live db
            yield session
        finally:
            session.close()

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        kw = {"database": self._cfg.database} if self._cfg.database else {}
        with self._driver.session(**kw) as session:  # pragma: no cover - requires live db
            return [dict(r) for r in session.run(query, **(params or {}))]

    def provision(self, *, dim: int) -> list[str]:
        from .schema import MIGRATIONS, render
        statements = render(MIGRATIONS, dim=dim)
        for stmt in statements:  # pragma: no cover - requires live db
            self.run(stmt)
        return statements

    def vector_search(self, embedding: list[float], top_k: int,
                      filters: dict | None = None) -> list[dict]:
        """Neo4j 벡터 인덱스 질의. 필터는 인덱스 결과에 술어로 적용(pre-filter 지향).
        상세 하이브리드 융합(RRF)은 백엔드 밖 `api/query.py` 순수 함수(§4-2)."""
        params: dict = {"emb": embedding, "top_k": int(top_k)}
        where = _filter_clause("node", filters, params)
        cypher = (
            "CALL db.index.vector.queryNodes('chunk_embedding', $top_k, $emb) "
            "YIELD node, score "
            f"{where}"
            "RETURN node.id AS id, node.text AS text, score "
            "ORDER BY score DESC LIMIT $top_k"
        )
        return self.run(cypher, params)

    def fulltext_search(self, text: str, top_k: int,
                        filters: dict | None = None) -> list[dict]:
        params: dict = {"q": text, "top_k": int(top_k)}
        where = _filter_clause("node", filters, params)
        cypher = (
            "CALL db.index.fulltext.queryNodes('chunk_text_ft', $q) "
            "YIELD node, score "
            f"{where}"
            "RETURN node.id AS id, node.text AS text, score "
            "ORDER BY score DESC LIMIT $top_k"
        )
        return self.run(cypher, params)

    def snapshot(self, dest_dir: str) -> str:
        # 서버 Neo4j 는 파일 스냅샷 대상이 아니다(서버가 데이터를 소유).
        raise NotImplementedError(
            "Neo4jBackend 는 파일 스냅샷 미지원 — backup 은 embedded 백엔드 전용(§3-1-1)")

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass


def _filter_clause(var: str, filters: dict | None, params: dict) -> str:
    """스코프 필터를 WHERE 술어로 렌더(pre-filter). params 를 in-place 로 채운다.
    빈 필터면 빈 문자열."""
    if not filters:
        return ""
    preds = []
    for key in ("owner_id", "domain", "tenant_id"):
        val = filters.get(key)
        if val is not None:
            preds.append(f"{var}.{key} = ${key}")
            params[key] = val
    return ("WHERE " + " AND ".join(preds) + " ") if preds else ""


# ── Factory ──────────────────────────────────────────────────────────────────

def open_store(cfg: StoreConfig | None = None) -> GraphStore | None:
    """cfg.backend 에 따라 스토어를 연다. 도달 불가 시 None(executed=False 강등).
    **드라이버/스토어를 여는 유일한 지점**(설계 §4-1)."""
    cfg = cfg or load_config()
    backend = (cfg.backend or "neo4j").lower()
    if backend == "neo4j":
        return Neo4jBackend.open(cfg)
    if backend == "embedded":
        try:
            from .embedded_backend import EmbeddedBackend  # P1
        except ImportError:
            return None
        return EmbeddedBackend.open(cfg)
    raise ValueError(f"unknown store backend: {cfg.backend!r}")
