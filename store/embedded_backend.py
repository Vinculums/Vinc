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

    **기본값 = kuzu 0.11.3**(설계 §13 D14 결정): 벡터/FTS 가 정적 링크라 설치 즉시
    오프라인 동작(§1 핵심 경로). ladybug v0.18.0 은 확장을 런타임 다운로드하므로
    오프라인 배포엔 부적합 — `embedded_driver="ladybug"` 노브로 옵트인만 유지(R1).
    prefer 로 선호 지정. ⚠ ladybug·kuzu 는 한 프로세스 공존 불가(네이티브 타입 충돌)
    이므로 첫 성공 임포트에서 즉시 반환한다. (모듈, 이름) 또는 (None, None)."""
    order = {
        "kuzu": ["kuzu", "ladybug"],
        "ladybug": ["ladybug", "kuzu"],
    }.get(prefer, ["kuzu", "ladybug"])   # 기본 kuzu-first (D14)
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


class _ReaderView:
    """단일 Database 의 별도 read Connection 뷰(R4 — 서버 조회 경로). `GraphStore` 의
    read 표면(run/fulltext_search/vector_search/close)만 노출해 `api.query.execute`·
    `api.query.search` 에 store 로 주입된다. 쓰기(SET) 는 이 뷰로 흘리지 않는다 —
    쓰기는 잡 워커의 writer Connection 으로만 직렬화된다(R4)."""

    def __init__(self, backend: "EmbeddedBackend", conn):
        self._b = backend
        self._conn = conn

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        return _exec_to_dicts(self._conn, query, params or None)

    def fulltext_search(self, text, top_k, filters=None) -> list[dict]:
        return self._b.fulltext_search(text, top_k, filters, conn=self._conn)

    def vector_search(self, embedding, top_k, filters=None) -> list[dict]:
        return self._b.vector_search(embedding, top_k, filters, conn=self._conn)

    def ensure_search_indexes(self, *, dim: int | None = None) -> dict:
        """검색 인덱스 지연 보장(T3b) — **reader 자기 커넥션**에서 빌드해, 지연 빌드
        직후 같은 뷰가 곧바로 재검색하게 한다(cross-connection 가시성 문제 회피)."""
        return self._b.ensure_search_indexes(dim=dim, conn=self._conn)

    def close(self) -> None:
        self._conn = None   # 요청 종료 시 커넥션 해제(Database 는 서버가 계속 소유)


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

    def run(self, query: str, params: dict | None = None, *, conn=None) -> list[dict]:
        return _exec_to_dicts(conn or self._conn, query, params or None)

    # ── 서버 read 경로 — 별도 Connection(R4) ──────────────────────────────────
    # 사이드카(§11 P2)는 단일 Database 를 프로세스 수명 동안 소유하고, 쓰기는 잡
    # 워커의 단일 writer Connection 으로 직렬화한다(R4 쓰기 지점). 조회는 동일
    # Database 에서 파생한 **별도 reader Connection** 으로 수행해, 쓰기 잡 진행
    # 중에도 응답성을 유지한다(§11 P2 게이트 — "잡 실행 중 조회 응답성"). Kùzu 계보는
    # Database 당 다중 Connection + MVCC 로 writer 진행 중 reader 스냅샷 일관을
    # 보장한다(P2 착수 시 실측 확정 — 불성립 시 reader 직렬화로 폴백).
    def reader_view(self) -> "_ReaderView":
        """이 Database 에서 새 Connection 을 파생한 read 전용 뷰(R4). execute·search 가
        소비하는 표면(run/fulltext_search/vector_search/close)만 제공한다. 요청 단위로
        생성·close 한다(단일 사용자 규모 — §12 R5, per-request 커넥션 비용 무시 가능)."""
        drv, _ = _import_driver(self._cfg.embedded_driver)
        return _ReaderView(self, drv.Connection(self._db))

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

    def _load_ext(self, ext: str, conn=None) -> None:
        """벡터/FTS 확장 로드. kuzu 는 정적 링크(무연산), ladybug 는 다운로드 필요.
        확장 부재 시 명확한 오류로 강등(오프라인 미번들 상황 — §9). reader Connection
        경유 검색 시엔 그 conn 에 로드한다(확장 로드는 커넥션 스코프일 수 있음)."""
        c = conn or self._conn
        try:
            c.execute(f"LOAD EXTENSION {ext}")
        except Exception as exc:
            if "already" in str(exc).lower() or "statically linked" in str(exc).lower():
                return
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9). 원인: {exc}") from exc

    def drop_search_indexes(self) -> None:
        """벡터+FTS 인덱스 제거(멱등). ⚠ **적재 전 호출 필수** — 인덱스가 걸린
        embedding 열은 수정 불가하므로, 재적재 시 먼저 drop 해야 MERGE 가 가능하다."""
        self._load_ext("vector")
        self._load_ext("fts")
        for call in (f"CALL DROP_VECTOR_INDEX('Chunk','{self._VEC_INDEX}')",
                     f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')"):
            try:
                self._conn.execute(call)
            except Exception:
                pass  # 미존재 — 무시(멱등)

    def build_search_indexes(self, *, dim: int) -> None:
        """**적재 후** 벡터(HNSW)+FTS(BM25) 인덱스 생성. 재적재 시엔 사전
        `drop_search_indexes()` 로 기존 인덱스를 먼저 제거해야 한다(embed 잡 순서)."""
        self._load_ext("vector")
        self._load_ext("fts")
        self._conn.execute(f"CALL CREATE_VECTOR_INDEX('Chunk','{self._VEC_INDEX}','embedding')")
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['text'])")

    def ensure_search_indexes(self, *, dim: int | None = None, conn=None) -> dict:
        """검색 인덱스 **지연 보장**(멱등) — 부재 시에만 생성한다(T3b 실사용 사고 대응).

        `build_search_indexes` 와 달리 이미 있으면 'already exists' 를 흡수해 재빌드하지
        않는다 — search 경로가 '인덱스 부재'로 실패했을 때 1회만 부르는 자동 복구용
        (`api.query.search`). FTS(BM25)는 항상, 벡터(HNSW)는 `dim` 이 주어질 때만
        보장한다(쿼리 임베딩 검색이 필요할 때만 벡터 인덱스 생성). 반환 {fts, vector}
        = 이번 호출에서 실제 생성했는지. `conn` 미지정 시 writer 커넥션 — reader 뷰는
        자기 커넥션을 넘겨(:meth:`_ReaderView.ensure_search_indexes`) 같은 커넥션이
        곧바로 재검색하게 한다(실측: reader 커넥션 빌드도 catalog 에 반영돼 writer 도
        본다)."""
        c = conn or self._conn
        built = {"fts": False, "vector": False}

        def _create(ext: str, call: str) -> bool:
            self._load_ext(ext, c)
            try:
                c.execute(call)
                return True
            except Exception as exc:      # 이미 존재 → 무시(멱등), 그 외는 전파
                if "already exists" in str(exc).lower():
                    return False
                raise

        built["fts"] = _create("fts", f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['text'])")
        if dim is not None:
            built["vector"] = _create(
                "vector", f"CALL CREATE_VECTOR_INDEX('Chunk','{self._VEC_INDEX}','embedding')")
        return built

    def vector_search(self, embedding, top_k, filters=None, *, conn=None) -> list[dict]:
        c = conn or self._conn
        self._load_ext("vector", c)
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)  # pre-filter 과추출(§4-2)
        params: dict = {"emb": list(embedding), "k": k}
        where = _filter_clause("node", filters, params)
        cypher = (
            f"CALL QUERY_VECTOR_INDEX('Chunk','{self._VEC_INDEX}', $emb, $k) "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, distance "
            "ORDER BY distance LIMIT $k"
        )
        return _exec_to_dicts(c, cypher, params)[:int(top_k)]

    def fulltext_search(self, text, top_k, filters=None, *, conn=None) -> list[dict]:
        c = conn or self._conn
        self._load_ext("fts", c)
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)
        params: dict = {"q": text, "k": k}
        where = _filter_clause("node", filters, params)
        cypher = (
            f"CALL QUERY_FTS_INDEX('Chunk','{self._FTS_INDEX}', $q) "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, score "
            "ORDER BY score DESC LIMIT $k"
        )
        return _exec_to_dicts(c, cypher, params)[:int(top_k)]

    def snapshot(self, dest_dir: str) -> str:
        """데이터 디렉토리 일관 스냅샷(§3-1-1). 쓰기 정지는 잡 큐가 보장(P2).

        복사 전 **CHECKPOINT** 로 WAL 을 본 파일에 반영한다 — kuzu 는 close 시점에
        체크포인트하므로, 열린 스토어를 그대로 복사하면 미반영(빈 테이블) 사본이
        나온다(P2 스냅샷 복원 왕복 실측, 2026-07-10). 미지원 방언은 무시하고 진행."""
        import os
        import shutil
        try:
            self.run("CHECKPOINT")
        except Exception:
            pass   # 방언 미지원 시 — close 경로 스냅샷(단발 CLI)은 이미 일관
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
