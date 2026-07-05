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


def _import_driver(prefer: str | None = None):
    """임베디드 드라이버 단일 임포트 지점(§2-2 R1 — 포크 결합 상한).
    prefer="kuzu"|"ladybug" 로 선호 지정 가능(config.embedded_driver). 기본 자동:
    ladybug(v0.18.0) → kuzu(0.11.3 폴백). (모듈, 이름) 또는 (None, None)."""
    order = {
        "kuzu": ["kuzu", "ladybug"],
        "ladybug": ["ladybug", "kuzu"],
    }.get(prefer, ["ladybug", "kuzu"])
    for name in order:
        try:
            return __import__(name), name
        except ImportError:
            continue
    return None, None


def _filter_clause(var: str, filters: dict | None, params: dict) -> str:
    """스코프 필터를 WHERE 술어로 렌더(pre-filter, §4-2 — 스코프 1차 §1). params
    를 in-place 로 채운다. QUERY_*_INDEX 의 YIELD 와 RETURN 사이 WHERE 로 붙인다."""
    if not filters:
        return ""
    preds = []
    for key in ("owner_id", "domain", "tenant_id"):
        val = filters.get(key)
        if val is not None:
            preds.append(f"{var}.{key} = ${key}")
            params[key] = val
    return ("WHERE " + " AND ".join(preds) + " ") if preds else ""


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
        drv, name = _import_driver(cfg.embedded_driver)
        if drv is None:
            return None
        import os
        data_dir = cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        os.makedirs(os.path.dirname(data_dir) or ".", exist_ok=True)
        try:
            db = drv.Database(data_dir)
            conn = drv.Connection(db)
        except Exception:
            # 스토어 열기 실패(락·손상·드라이버 초기화) → None 강등(§3-4).
            # 호출부(execute)는 executed=False 로 처리.
            return None
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

    # ── 검색 인덱스 (M5/M6) ────────────────────────────────────────────────
    # Kùzu 계보의 벡터(HNSW)·FTS(BM25)는 확장 기능이다. kuzu 0.11.3 은 정적 링크
    # (오프라인 즉시), ladybug v0.18.0 은 별도 확장 → **오프라인 배포 시 번들 필수**
    # (§9). ⚠ 인덱스가 embedding 열에 걸려 있으면 그 열을 수정할 수 없다 —
    # 인덱스는 **적재 후 build**, 재적재 전 **drop**(embed 잡이 관리, §3-1-1).
    _VEC_INDEX = "chunk_vec"
    _FTS_INDEX = "chunk_ft"
    _OVERSAMPLE = 5   # pre-filter 보장용 과추출 배수(§4-2 — 스코프 필터 선택도 대비)

    def _load_ext(self, ext: str) -> None:
        """벡터/FTS 확장 로드. kuzu 는 정적 링크(무연산), ladybug 는 다운로드 필요.
        확장 부재 시 명확한 오류로 강등(오프라인 미번들 상황 — §9)."""
        try:
            self._conn.execute(f"LOAD EXTENSION {ext}")
        except Exception as exc:
            if "already" in str(exc).lower() or "statically linked" in str(exc).lower():
                return
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9). 원인: {exc}") from exc

    def build_search_indexes(self, *, dim: int) -> None:
        """적재 후 벡터+FTS 인덱스 (재)생성. 멱등(기존 drop 후 재생성)."""
        self._load_ext("vector")
        self._load_ext("fts")
        for call in (f"CALL DROP_VECTOR_INDEX('Chunk','{self._VEC_INDEX}')",
                     f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')"):
            try:
                self._conn.execute(call)
            except Exception:
                pass  # 미존재 — 무시(멱등)
        self._conn.execute(f"CALL CREATE_VECTOR_INDEX('Chunk','{self._VEC_INDEX}','embedding')")
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['text'])")

    def vector_search(self, embedding, top_k, filters=None) -> list[dict]:
        self._load_ext("vector")
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)  # pre-filter 과추출(§4-2)
        params: dict = {"emb": list(embedding), "k": k}
        where = _filter_clause("node", filters, params)
        cypher = (
            f"CALL QUERY_VECTOR_INDEX('Chunk','{self._VEC_INDEX}', $emb, $k) "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, distance "
            "ORDER BY distance LIMIT $k"
        )
        return _exec_to_dicts(self._conn, cypher, params)[:int(top_k)]

    def fulltext_search(self, text, top_k, filters=None) -> list[dict]:
        self._load_ext("fts")
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)
        params: dict = {"q": text, "k": k}
        where = _filter_clause("node", filters, params)
        cypher = (
            f"CALL QUERY_FTS_INDEX('Chunk','{self._FTS_INDEX}', $q) "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, score "
            "ORDER BY score DESC LIMIT $k"
        )
        return _exec_to_dicts(self._conn, cypher, params)[:int(top_k)]

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
