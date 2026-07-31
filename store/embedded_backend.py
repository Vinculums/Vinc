# -*- coding: utf-8 -*-
"""EmbeddedBackend — 인프로세스 그래프 스토어 (LadybugDB, Kùzu 계보).

설계 정본 [[weg.c.memory-standalone]] §4·§5. 코드가 특정 패키지명에 결합되지
않도록 드라이버 임포트는 이 파일 1곳에만 둔다(§2-2 리스크 대응). **드라이버 =
`ladybug` 단일(v0.5.3, 2026-07-21 — kuzu 제거)**: 테스트 기간·기존 사용자 없음
확인(발주자) → 듀얼드라이버(마커·자동감지·이관 레일) 전체 폐기, 단일 무조건 호출로
단순화. 폐기 배경: v0.5.1/v0.5.2 는 `hiddenimports=["ladybug"]` 만으로 빌드했는데,
ladybug 가 네이티브 서브모듈(`._lbug`/`._lbug_capi`)을
`importlib.import_module("._lbug", __package__)` 로 **동적** 로드해(`ladybug/
_backend.py`) PyInstaller 정적 분석이 못 보고 통째로 누락 — `import ladybug` 는
성공하나 `Database()` 호출이 `ModuleNotFoundError` 로 실패해 스토어가 열리지 않는
회귀가 있었다(실측 확정·수정: `packaging/sidecar.spec` 의 `collect_all("ladybug")`).

스키마 선행(§5): Neo4j 의 schema-free LPG 와 달리 노드/관계 테이블을 먼저 선언한다.
`provision(dim)` 이 §5 테이블 DDL 을 렌더·적용한다.

세션 표면(§4-3): `session()` 은 neo4j 동형 어댑터를 yield 해 엔진의
`session.run(cypher, **kwargs)` 호출부를 무변경으로 통과시킨다.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from .config import StoreConfig
from .embedded_schema import SCHEMA_VERSION, migration_statements, render_embedded_ddl


def _import_driver():
    """임베디드 드라이버 단일 임포트 지점(§2-2 R1 — 결합 상한). **ladybug 전용**
    (v0.5.3 — kuzu 제거, 안내는 모듈 docstring 참조). 미설치(개발 환경 등)면
    (None, None) — 호출부(`EmbeddedBackend.open`)가 스토어 unreachable 로 강등
    (§3-4), 프로세스는 크래시 없이 살아 `/health` 로 원인을 노출한다."""
    try:
        return __import__("ladybug"), "ladybug"
    except ImportError:
        return None, None


def _bundled_ext_path(ext: str) -> str | None:
    """번들된 ladybug 확장(`lib<ext>.lbug_extension`) 경로 해석 — G0 오프라인 로드용.

    탐색 순서: ① PyInstaller frozen 추출 dir(`sys._MEIPASS/lbug_extensions/`)
    ② `VINC_LBUG_EXT_DIR` env(개발/테스트 오버라이드) ③ 사이드카 실행 파일 옆
    `lbug_extensions/`(onedir·수동 배치). 미발견 시 None → 이름 기반 폴백(dev 는
    설치본 사용). 경로는 Cypher 문자열 리터럴용으로 슬래시 정규화."""
    import os
    import sys
    fname = f"lib{ext}.lbug_extension"
    cands: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, "lbug_extensions"))
    envdir = os.environ.get("VINC_LBUG_EXT_DIR")
    if envdir:
        cands.append(envdir)
    try:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "lbug_extensions"))
    except Exception:
        pass
    for d in cands:
        p = os.path.join(d, fname)
        if os.path.exists(p):
            return p.replace("\\", "/")
    return None


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

    def __init__(self, backend: "EmbeddedBackend", conn, build=None):
        self._b = backend
        self._conn = conn
        # 지연 인덱스 빌드를 **쓰기 지점으로 돌려보내는** 통로(R4). 서버가 잡 큐에
        # 태우는 콜러블을 주입한다. 없으면(CLI·테스트 — 동시 라이터가 없는 단일
        # 스레드) 백엔드의 writer 커넥션으로 직접 빌드한다.
        self._build = build

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        return _exec_to_dicts(self._conn, query, params or None)

    def fulltext_search(self, text, top_k, filters=None) -> list[dict]:
        return self._b.fulltext_search(text, top_k, filters, conn=self._conn)

    def vector_search(self, embedding, top_k, filters=None) -> list[dict]:
        return self._b.vector_search(embedding, top_k, filters, conn=self._conn)

    def ensure_search_indexes(self, *, dim: int | None = None) -> dict:
        """검색 인덱스 지연 보장(T3b) — **쓰기 지점으로 되돌려 보낸다**(R4).

        여태 이걸 **리더 자기 커넥션**에서 실행했다. 이유는 "지연 빌드 직후 같은 뷰가
        곧바로 재검색하게(cross-connection 가시성 회피)"였는데, 2026-07-31 실측으로
        두 전제가 다 뒤집혔다:

        ① **가시성은 문제가 아니다.** 인덱스가 없을 때 연 리더 커넥션도, 라이터가
           만든 인덱스를 곧바로 본다(실측: 기존 리더가 즉시 fulltext_search 성공).
        ② **리더의 DDL 은 라이터를 망가뜨린다.** 엔진은 쓰기 트랜잭션을 하나만
           허용해서, 리더가 인덱스를 만드는 ~0.5초 동안 라이터의 **평범한 MERGE 조차**
           `Cannot start a new write transaction` 로 거절된다(실측). 즉 이건 드문
           DDL-DDL 경합이 아니라 저장·삭제가 통째로 실패할 수 있는 상시 위험이었다.

        읽기 경로가 쓰기를 하면 안 된다 — 그게 R4 다. 주입된 통로가 있으면 잡 큐로
        보내고, 없으면(CLI·테스트) writer 커넥션으로 직접 만든다."""
        if self._build is not None:
            return self._build(dim)
        return self._b.ensure_search_indexes(dim=dim)

    def close(self) -> None:
        """요청 종료 시 커넥션 해제(Database 는 서버가 계속 소유).

        ⚠ 참조만 버리면 **해제가 아니다**(2026-07-31 실측 정정). 드라이버
        `Connection` 에는 `close()` 가 실재하고(실측: close 뒤 execute 는
        RuntimeError), `__del__` 은 **없다** — 즉 참조를 놓는 것만으로는 언제
        풀릴지 알 수 없다. 요청마다 커넥션을 새로 만드는 구조라 이 누수는 부하에
        비례해 쌓인다. 명시적으로 닫는다."""
        c, self._conn = self._conn, None
        if c is None:
            return
        try:
            c.close()
        except Exception:
            pass   # 이미 닫혔거나 구 드라이버 — 참조를 놓은 것으로 충분하다


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

    #: 마지막 open 실패 사유(진단용). 성공하면 빈 문자열.
    last_open_error: str = ""

    def __init__(self, db, conn, driver_name: str, cfg: StoreConfig):
        self._db = db
        self._conn = conn
        self.driver_name = driver_name
        self._cfg = cfg

    @classmethod
    def open(cls, cfg: StoreConfig) -> "EmbeddedBackend | None":
        data_dir = cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        drv, name = _import_driver()
        if drv is None:
            return None
        os.makedirs(os.path.dirname(data_dir) or ".", exist_ok=True)
        # 드라이버 자원 상한 — 지정된 것만 넘긴다(미지정 = 드라이버 기본값 유지).
        # 왜 필요한가: Database 하나가 가상주소 8 TiB 를 예약해, Windows 에서는 한
        # 프로세스가 14~15개까지만 연다(그 다음은 MemoryError: bad allocation).
        # 앱은 하나만 열지만 테스트는 수십 개를 연다 — StoreConfig 참조.
        kwargs: dict = {}
        if getattr(cfg, "max_db_size", 0):
            kwargs["max_db_size"] = int(cfg.max_db_size)
        if getattr(cfg, "buffer_pool_mb", 0):
            kwargs["buffer_pool_size"] = int(cfg.buffer_pool_mb) * 1024 * 1024
        try:
            db = drv.Database(data_dir, **kwargs)
            conn = drv.Connection(db)
        except Exception as exc:
            # 스토어 열기 실패(락·손상·자원 고갈) → None 강등(§3-4). 호출부는
            # executed=False 로 처리한다. ⚠ 이유를 **삼키지 않는다**: 여태 None 만
            # 돌려줘 "no reachable store" 한 줄로 뭉개졌고, 자원 고갈(MemoryError)이
            # 스코프 격리 실패처럼 보이는 오진을 낳았다(로컬 Windows 10건의 정체).
            cls.last_open_error = f"{type(exc).__name__}: {exc}"
            return None
        cls.last_open_error = ""
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
    # 중에도 응답성을 유지한다(§11 P2 게이트 — "잡 실행 중 조회 응답성").
    #
    # **MVCC 실측 확정(2026-07-31)** — 미검증으로 남아 있던 가정을 측정했다. 라이터가
    # 쉬지 않고 MERGE 하는 동안 리더의 조회 지연은 중앙 1.33ms · 최대 2.32ms 로,
    # 유휴 시(중앙 1.25ms)와 사실상 같다. 즉 **읽기는 쓰기에 막히지 않는다** — 예고된
    # 폴백("불성립 시 reader 직렬화")은 필요 없고, 도입해서도 안 된다(그게 곧 P2
    # 게이트를 깨는 일이다).
    #
    # ⚠ 단 **쓰기 트랜잭션은 여전히 하나뿐이다.** 리더 커넥션이 DDL(지연 인덱스
    # 빌드)을 실행하면 그동안 라이터의 평범한 MERGE 조차 "Cannot start a new write
    # transaction" 으로 거절된다(실측). 그래서 읽기 경로는 **쓰기를 하면 안 된다** —
    # :meth:`_ReaderView.ensure_search_indexes` 주석 참조.
    def reader_view(self, *, build=None) -> "_ReaderView":
        """이 Database 에서 새 Connection 을 파생한 read 전용 뷰(R4). execute·search 가
        소비하는 표면(run/fulltext_search/vector_search/close)만 제공한다. 요청 단위로
        생성·close 한다(단일 사용자 규모 — §12 R5, per-request 커넥션 비용 무시 가능).

        ``build``: 지연 인덱스 빌드를 쓰기 지점으로 되돌려 보내는 콜러블
        ``(dim) -> dict``. 서버가 잡 큐 진입점을 주입한다
        (:meth:`_ReaderView.ensure_search_indexes` 참조)."""
        drv, _ = _import_driver()
        return _ReaderView(self, drv.Connection(self._db), build=build)

    def _exec_idem(self, stmt: str) -> None:
        """DDL 멱등 실행 — 'already' 계열(테이블/속성 이미 존재)만 흡수, 그 외 전파.
        Kùzu 0.11.3 는 CREATE ... IF NOT EXISTS 부재, ALTER ADD 중복 시 'already' 메시지."""
        try:
            self._conn.execute(stmt)
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise

    def _schema_version(self) -> int:
        """_SchemaMeta 의 schema_version(없으면 0 — 마이그레이션 이전 설치)."""
        try:
            rows = self.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                            "RETURN m.value AS v")
        except Exception:
            return 0
        if rows and rows[0].get("v") not in (None, ""):
            try:
                return int(rows[0]["v"])
            except (TypeError, ValueError):
                return 0
        return 0

    def migrate(self, *, dim: int) -> dict:
        """스키마 제자리 마이그레이션(S0). 신규 설치·구 설치 모두 최종형으로 수렴.

        1) base DDL(멱등) — 신규 설치는 추적열 포함 테이블 + 메타 테이블 생성; 구 설치는
           기존 테이블 skip + 신규 메타 테이블만 생성.
        2) 버전 게이트 — _SchemaMeta 의 기록 버전 이후 ALTER 스텝 적용(구 설치의 기존
           테이블에 추적열 ADD+DEFAULT 백필; 신규 설치는 이미 있어 no-op 흡수).
        3) 버전 기록. **ALTER ADD 는 additive → 롤백 시 구코드가 새 열을 단순 무시.**"""
        for stmt in render_embedded_ddl(dim=dim):
            self._exec_idem(stmt)
        cur = self._schema_version()
        target = SCHEMA_VERSION
        for stmt in migration_statements(cur):
            self._exec_idem(stmt)
        if cur < target:
            self.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = $v",
                     {"v": str(target)})
        return {"migrated_from": cur, "migrated_to": target}

    def provision(self, *, dim: int) -> list[str]:
        """호환 진입점 — migrate() 로 위임(기존 호출부 무변경으로 마이그레이션 획득)."""
        self.migrate(dim=dim)
        return render_embedded_ddl(dim=dim)

    # ── 검색 인덱스 (M5/M6) ────────────────────────────────────────────────
    # Kùzu 계보의 벡터(HNSW)·FTS(BM25)는 확장 기능이다. ladybug 는 별도 확장 →
    # **오프라인 배포 시 번들 필수**(§9, G0). ⚠ 인덱스가 embedding 열에 걸려 있으면
    # 그 열을 수정할 수 없다 — 인덱스는 **적재 후 build**, 재적재 전 **drop**
    # (embed 잡이 관리, §3-1-1).
    _VEC_INDEX = "chunk_vec"
    _FTS_INDEX = "chunk_ft"
    _OVERSAMPLE = 5   # pre-filter 보장용 과추출 배수(§4-2 — 스코프 필터 선택도 대비)

    def _load_ext(self, ext: str, conn=None) -> None:
        """벡터/FTS/algo 확장 로드(ladybug 는 확장 기능 — 정적 링크가 아님).

        **G0(오프라인 확장 번들) — 실증 확정(2026-07-20):** ladybug 확장은 기본 런타임
        다운로드(`~/.lbdb/extension/<ver>/<plat>/<ext>/lib<ext>.lbug_extension`)라 오프라인
        불변식(§1)을 깬다. 실측: 캐시 부재 시 `LOAD EXTENSION <name>` 은 실패하나
        **`LOAD EXTENSION '<번들 경로>'` 는 네트워크 없이 로드 성공**(이후 CREATE_VECTOR_INDEX
        정상). → 번들된 `.lbug_extension` 을 명시 경로로 로드해 오프라인 즉시 동작을 보장한다.
        번들 미발견(dev 등)은 이름 기반으로 강등(설치돼 있으면 로드)."""
        c = conn or self._conn
        p = _bundled_ext_path(ext)
        if p:
            try:
                c.execute(f"LOAD EXTENSION '{p}'")
                return
            except Exception as exc:
                if "already" in str(exc).lower():
                    return
                # 번들 경로 로드 실패 → 아래 이름 기반 폴백(dev)
        try:
            c.execute(f"LOAD EXTENSION {ext}")
        except Exception as exc:
            low = str(exc).lower()
            if "already" in low or "statically linked" in low:
                return
            # ⚠ **아는 것만 말한다**(2026-07-31 실측). LOAD 가 실패하는 이유가 확장
            # 부재만은 아니다: 다른 커넥션이 쓰기 트랜잭션을 쥐고 있으면 엔진이
            # "Cannot start a new write transaction" 로 거절한다(실측 — 리더의 지연
            # 인덱스 빌드와 겹칠 때). 그걸 "확장 미가용 — 오프라인 배포 시 번들 필요"
            # 로 감싸면 운영자를 패키징 문제로 보내는 **틀린 진단**이 된다. 확장과
            # 무관한 사유는 원문 그대로 올린다.
            if "transaction" in low:
                raise
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9·G0). 원인: {exc}") from exc

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

    # ── 그래프 알고리즘 (E-G3) ─────────────────────────────────────────────────
    # algo 확장(ladybug 는 다운로드형 — 오프라인 배포 시 §9 번들 필수)으로 구조 진단을
    # 네이티브 엔진에 위임(리서치 ④ — 구조 연산은 알고리즘에, 모델은 컨트롤러).
    # project_graph 를 멱등 생성→실행→드랍한다(T3b 지연 빌드 패턴과 동형의 관리).
    # 개념 관계 그래프 위 WCC(고아·컴포넌트)·Louvain(커뮤니티)·PageRank(전역 중요도).
    _ALGO_METRIC = {   # algo → 반환 지표 컬럼명(kuzu 0.11.3 실측)
        "weakly_connected_components": "group_id",
        "strongly_connected_components": "group_id",
        "louvain": "louvain_id",
        "page_rank": "rank",
    }
    # 기본 프로젝션 = Concept + 개념-개념 관계 4종(방향 무관 구조 연결성).
    _ALGO_NODE_TABLES = ("Concept",)
    _ALGO_REL_TABLES = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM")

    def graph_algo(self, algo: str, *, node_tables: tuple | None = None,
                   rel_tables: tuple | None = None, id_col: str = "concept_id",
                   graph_name: str = "_vinc_algo", conn=None) -> list[dict]:
        """algo 확장 그래프 알고리즘 실행(E-G3). 반환 ``[{id, value}]`` — id 는
        ``node.<id_col>``, value 는 알고리즘 지표(group_id/louvain_id/rank). 확장
        미가용(오프라인 미번들) 시 :meth:`_load_ext` 가 RuntimeError 로 강등한다
        (호출부가 executed=False 로 표면화). project_graph 는 전체 테이블을 투영하므로
        (owner 필터 불가) 소유자 스코프는 **호출부가 결과를 owner 개념 집합과 교차**
        해 적용한다(교차소유자 엣지 부재 → 컴포넌트/커뮤니티는 owner-clean)."""
        c = conn or self._conn
        metric = self._ALGO_METRIC.get(algo)
        if metric is None:
            raise ValueError(f"unsupported graph algo: {algo!r} "
                             f"(지원: {sorted(self._ALGO_METRIC)})")
        self._load_ext("algo", c)
        nt = "[" + ", ".join(f"'{t}'" for t in (node_tables or self._ALGO_NODE_TABLES)) + "]"
        rt = "[" + ", ".join(f"'{t}'" for t in (rel_tables or self._ALGO_REL_TABLES)) + "]"

        def _drop():
            try:
                c.execute(f"CALL drop_projected_graph('{graph_name}')")
            except Exception:
                pass   # 미존재 — 무시(멱등)

        _drop()   # 이전 잔여 프로젝션 정리(멱등 생성)
        c.execute(f"CALL project_graph('{graph_name}', {nt}, {rt})")
        try:
            return _exec_to_dicts(
                c, f"CALL {algo}('{graph_name}') "
                   f"RETURN node.{id_col} AS id, {metric} AS value", None)
        finally:
            _drop()   # 실행 후 프로젝션 정리(카탈로그 누수 방지)

    def snapshot(self, dest_dir: str) -> str:
        """데이터 디렉토리 일관 스냅샷(§3-1-1). 쓰기 정지는 잡 큐가 보장(P2).

        복사 전 **CHECKPOINT** 로 WAL 을 본 파일에 반영한다 — Kùzu 계보는 close
        시점에 체크포인트하므로, 열린 스토어를 그대로 복사하면 미반영(빈 테이블)
        사본이 나온다(P2 스냅샷 복원 왕복 실측, 2026-07-10). 미지원 방언은 무시하고 진행.

        ⚠ Windows: 열려 있는 DB 파일에는 **바이트 범위 락**이 걸려 있어 복사가
        `WinError 33` 으로 실패한다(읽기조차 막힌다 — 실측). 리눅스는 같은 코드가
        그냥 되므로 여태 드러나지 않았다. 그래서 복사 직전에 드라이버 핸들을 닫고,
        복사 후 **같은 경로로 다시 연다** — 온디스크 형식이 그대로라 복원 경로
        (`IMPORT` 아님, 폴더 교체)도 그대로다. 재오픈까지가 이 메서드의 계약이다."""
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

        def _copy() -> None:
            if os.path.isdir(src):
                shutil.copytree(src, dest, dirs_exist_ok=True)
            elif os.path.exists(src):
                shutil.copy2(src, dest)
                # WAL 도 함께 — 본 파일만 복사하면 체크포인트 이후 쓰기를 잃는다.
                wal = src + ".wal"
                if os.path.exists(wal):
                    shutil.copy2(wal, dest + ".wal")

        try:
            _copy()
            return dest
        except (PermissionError, OSError):
            pass   # 락으로 막혔다 — 아래에서 닫고 다시 시도한다

        drv, _name = _import_driver()
        for obj in (self._conn, self._db):
            try:
                closer = getattr(obj, "close", None)
                if callable(closer):
                    closer()
            except Exception:
                pass
        self._conn = None
        self._db = None
        try:
            _copy()
        finally:
            # 무슨 일이 있어도 스토어를 다시 연다 — 스냅샷 실패가 서비스를 죽이지 않게.
            if drv is not None:
                kwargs: dict = {}
                if getattr(self._cfg, "max_db_size", 0):
                    kwargs["max_db_size"] = int(self._cfg.max_db_size)
                if getattr(self._cfg, "buffer_pool_mb", 0):
                    kwargs["buffer_pool_size"] = int(self._cfg.buffer_pool_mb) * 1024 * 1024
                self._db = drv.Database(src, **kwargs)
                self._conn = drv.Connection(self._db)
        return dest

    def close(self) -> None:
        """커넥션·데이터베이스를 **명시적으로** 닫는다.

        ⚠ 여태 참조만 끊고(`= None`) GC 에 맡겼는데, 드라이버는 Database 소멸 시점에
        데이터 디렉토리의 **프로세스 락**을 푼다. GC 가 즉시 돌지 않는 Windows 에서는
        락이 남아, 같은 경로를 다시 열면 `IO exception: Could not set lock on file` 로
        실패한다 — 그러면 `open()` 이 None 을 돌려주고 호출부는 "스토어 도달 불가"로
        강등돼, **쓴 데이터가 안 보이는** 증상이 된다(로컬 Windows 테스트 10건의
        정체이자, 닫았다 여는 조회 경로의 잠재 사고). 드라이버가 close() 를 제공하므로
        그것을 부른다. 이미 닫혔거나 미지원이면 조용히 참조만 끊는다(하위호환)."""
        for obj in (self._conn, self._db):
            try:
                closer = getattr(obj, "close", None)
                if callable(closer):
                    closer()
            except Exception:
                pass   # 닫기 실패가 호출부를 깨뜨리지 않는다 — 참조는 아래에서 끊는다
        self._conn = None
        self._db = None
