# -*- coding: utf-8 -*-
"""EmbeddedBackend — 인프로세스 그래프 스토어 (LadybugDB, Kùzu 계보).

설계 정본 [[weg.c.memory-standalone]] §4·§5. 코드가 특정 패키지명에 결합되지
않도록 드라이버 임포트는 이 파일 1곳에만 둔다(§2-2 리스크 대응). 후보 임포트 순서:
`ladybug`(LadybugDB v0.18.0, PyPI `ladybug` — P1-0 실측 확인) → `kuzu`(0.11.3 핀 폴백).

스키마 선행(§5): Neo4j 의 schema-free LPG 와 달리 노드/관계 테이블을 먼저 선언한다.
`provision(dim)` 이 §5 테이블 DDL 을 렌더·적용한다.

세션 표면(§4-3): `session()` 은 neo4j 동형 어댑터를 yield 해 엔진의
`session.run(cypher, **kwargs)` 호출부를 무변경으로 통과시킨다.
"""
from __future__ import annotations

from contextlib import contextmanager

from .config import StoreConfig
from .embedded_schema import render_embedded_ddl


def _import_driver():
    """임베디드 드라이버 단일 임포트 지점. (모듈, 이름) 반환 또는 (None, None)."""
    try:
        import ladybug as drv  # LadybugDB v0.18.0 (PyPI `ladybug`)
        return drv, "ladybug"
    except ImportError:
        pass
    try:
        import kuzu as drv  # 폴백 — Kùzu 0.11.3 핀
        return drv, "kuzu"
    except ImportError:
        return None, None


class _Result:
    """neo4j Result 동형 — 반복 가능 + `.single()`, 레코드는 dict(컬럼명 키)."""

    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None

    def data(self) -> list[dict]:
        return list(self._records)


class _Session:
    """neo4j Session 동형 — `.run(cypher, **kwargs)` → `_Result`."""

    def __init__(self, conn):
        self._conn = conn

    def run(self, cypher: str, **params):
        return _Result(_exec_to_dicts(self._conn, cypher, params or None))

    def close(self):
        pass


def _exec_to_dicts(conn, cypher: str, params: dict | None) -> list[dict]:
    """드라이버 QueryResult → dict 레코드 리스트(컬럼명 키). 다중 문장 세미콜론 분리."""
    res = conn.execute(cypher, params) if params else conn.execute(cypher)
    # ladybug/kuzu 는 다중 문장 시 결과 리스트를 줄 수 있음 — 마지막 결과만 소비.
    results = res if isinstance(res, list) else [res]
    out: list[dict] = []
    for r in results:
        try:
            cols = r.get_column_names()
        except Exception:
            cols = None
        while r.has_next():
            row = r.get_next()
            out.append(dict(zip(cols, row)) if cols else {"_": row})
    return out


class EmbeddedBackend:
    """LadybugDB/Kùzu 어댑터. `GraphStore` 구현(§4-2)."""

    def __init__(self, db, conn, driver_name: str, cfg: StoreConfig):
        self._db = db
        self._conn = conn
        self.driver_name = driver_name
        self._cfg = cfg

    @classmethod
    def open(cls, cfg: StoreConfig) -> "EmbeddedBackend | None":
        drv, name = _import_driver()
        if drv is None:
            return None
        import os
        data_dir = cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        os.makedirs(os.path.dirname(data_dir) or ".", exist_ok=True)
        db = drv.Database(data_dir)
        conn = drv.Connection(db)
        return cls(db, conn, name, cfg)

    @contextmanager
    def session(self):
        session = _Session(self._conn)
        try:
            yield session
        finally:
            session.close()

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        return _exec_to_dicts(self._conn, query, params or None)

    def provision(self, *, dim: int) -> list[str]:
        statements = render_embedded_ddl(dim=dim)
        for stmt in statements:
            # CREATE ... IF NOT EXISTS 부재 방언 대비 — 이미 존재 시 무시(멱등).
            try:
                self._conn.execute(stmt)
            except Exception as exc:  # 테이블 이미 존재 등
                if "already exists" not in str(exc).lower():
                    raise
        return statements

    def vector_search(self, embedding, top_k, filters=None) -> list[dict]:
        raise NotImplementedError("벡터 인덱스 질의는 P1 후속(§6-2 M6)")

    def fulltext_search(self, text, top_k, filters=None) -> list[dict]:
        raise NotImplementedError("FTS 질의는 P1 후속(§6-2 M5)")

    def snapshot(self, dest_dir: str) -> str:
        """데이터 디렉토리 일관 스냅샷(§3-1-1). 쓰기 정지는 잡 큐가 보장(P2)."""
        import os
        import shutil
        src = self._cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(src.rstrip("/")) or "store"
        dest = os.path.join(dest_dir, base)
        if os.path.isdir(src):
            shutil.copytree(src, dest, dirs_exist_ok=True)
        elif os.path.exists(src):
            shutil.copy2(src, dest)
        return dest

    def close(self) -> None:
        # ladybug/kuzu Connection/Database 는 GC 로 정리 — 명시 close 없음.
        self._conn = None
        self._db = None
