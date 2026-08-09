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

import json
import os
from contextlib import contextmanager

from .config import StoreConfig
from .embedded_schema import (DOMAIN_HIERARCHY_VERSION,
                              EPISODE_TIME_BACKFILL_VERSION, SCHEMA_VERSION,
                              migration_statements, render_embedded_ddl)


# ── v9 데이터 마이그레이션 — Episode props.date/summary → 열 (F9) ─────────────
#: 백필 대상 열. Episode 의 열 이름과 props 키 이름이 같다는 것이 전제다.
_EPISODE_PROMOTED_COLS: tuple[str, ...] = ("date", "summary")


def backfill_episode_time_fields(store) -> int:
    """`props{}` 안에만 있던 Episode 의 date/summary 를 동명 열로 승격(멱등).

    왜 필요한가: `vinc_ingest` 의 노드 계약에 `date` 가 선언돼 있지 않아 상류가
    날짜를 `props` 에 실었고, `api/fragment` 는 top-level 만 읽었다. 그래서 `date`
    열이 빈 에피소드가 쌓였다(실측 2026-08-08 · 호스티드 16/55). recall 이
    `ORDER BY e.date DESC` 로 정렬하고 타임라인이 `coalesce(e.date, e.updated_at)`
    를 시간축으로 쓰는데, 그 열이 비면 정렬이 무의미해진다.

    **계약**
    - 멱등: 열이 이미 차 있으면 건드리지 않는다. 두 번 돌려도 결과가 같다.
    - **없는 날짜를 지어내지 않는다**: props 에 그 키가 없거나 문자열이 아니거나
      공백뿐이면 그 행은 그대로 둔다(v7 `content_time` 백필의 원칙과 같다 — 구 행에
      오늘을 찍으면 타임라인이 마이그레이션한 날에 몰린다).
    - props 원본은 지우지 않는다(상류가 보낸 자유 속성의 보존 계약).
    - **동기화 스탬프(updated_at/rev/sync_seq)를 찍지 않는다.** 이건 사용자의 편집이
      아니라 이미 스토어에 있던 값의 파생이다. 찍으면 모든 피어의 다음 pull 에
      "그 날 전 에피소드를 고쳤다"는 가짜 델타가 실린다(S0 의 '멱등 재적재 무스탬프'
      규칙과 같은 이유). 대가는 안다: Episode 의 `props` 는 동기화 필드가 아니라
      (`api.sync.SYNC_FIELDS`) props 를 못 받은 피어는 이 백필로 date 를 얻지 못하고,
      스탬프가 없으니 delta 로도 안 실린다 — 그 행은 다음 재적재 때 `_merge_node` 의
      승격이 정상 스탬프와 함께 고친다.

    반환: 실제로 열을 채운 노드 수(백필이 아무것도 안 했으면 0).
    """
    try:
        rows = store.run(
            "MATCH (e:Episode) WHERE e.props IS NOT NULL AND e.props <> '' "
            "RETURN e.episode_id AS id, e.props AS props, "
            + ", ".join(f"e.{c} AS {c}" for c in _EPISODE_PROMOTED_COLS))
    except Exception:
        # 미프로비저닝·방언 차이로 스캔이 안 되면 마이그레이션 전체를 죽이지 않는다
        # (스키마 ALTER 는 이미 끝났고, 백필은 다음 기동에 다시 시도되지 않는다는
        # 점은 감수한다 — 재시도가 필요하면 schema_version 을 내리면 된다).
        return 0
    filled = 0
    for r in rows or []:
        nid = r.get("id")
        if not nid:
            continue
        try:
            props = json.loads(r.get("props") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(props, dict):
            continue
        sets, params = [], {"id": nid}
        for col in _EPISODE_PROMOTED_COLS:
            if str(r.get(col) or "").strip():
                continue                      # 이미 값이 있다 — 멱등·비파괴
            cand = props.get(col)
            if not isinstance(cand, str) or not cand.strip():
                continue                      # 없는 값을 지어내지 않는다
            sets.append(f"e.{col} = ${col}")
            params[col] = cand.strip()
        if not sets:
            continue
        try:
            store.run(f"MATCH (e:Episode) WHERE e.episode_id = $id "
                      f"SET {', '.join(sets)}", params)
        except Exception:
            continue                          # 행 하나 실패가 배치를 날리지 않는다
        filled += 1
    return filled


def _import_driver():
    """임베디드 드라이버 단일 임포트 지점(§2-2 R1 — 결합 상한). **ladybug 전용**
    (v0.5.3 — kuzu 제거, 안내는 모듈 docstring 참조). 미설치(개발 환경 등)면
    (None, None) — 호출부(`EmbeddedBackend.open`)가 스토어 unreachable 로 강등
    (§3-4), 프로세스는 크래시 없이 살아 `/health` 로 원인을 노출한다."""
    try:
        return __import__("ladybug"), "ladybug"
    except ImportError:
        return None, None


def _driver_kwargs(cfg) -> dict:
    """`Database()` 에 넘길 인자 — **열기 경로 전부가 이 하나를 쓴다.**

    최초 open 과 스냅샷 후 재오픈이 각자 kwargs 를 짜고 있었고, 그래서 나중에 는
    노브가 재오픈 쪽에만 빠지는 사고가 났다(2026-08-09 발견). 한 벌로 묶는다.

    - ``max_db_size``/``buffer_pool_size`` — Database 하나가 가상주소 8 TiB 를
      예약해 Windows 에서는 한 프로세스가 14~15개까지만 연다(그 다음은
      ``MemoryError: bad allocation``). 앱은 하나만 열지만 테스트는 수십 개를 연다.
    - ``checkpoint_threshold`` — WAL 이 이만큼 자라면 엔진이 스스로 체크포인트한다.
      곧 **최대 손실 창**이다(StoreConfig 참조).
    - ``throw_on_wal_replay_failure`` — 기본 엄격. 프로세스가 쓰기 도중 죽으면 WAL 이
      완결 안 된 채 남고, 그 뒤로는 **어느 리비전이 새로 열어도** 같은 예외로 open()
      이 실패한다(락 경합과 증상이 같아 보이지만 다른 문제다). ``VINC_WAL_REPLAY_
      TOLERANT`` 로 "깨진 꼬리만 버리고 나머지는 살린다"로 전환한다 — 다만 그것은
      복구가 아니라 **폐기**이므로 켜면 무엇을 잃었는지 반드시 남겨야 한다.

    미지정 항목은 넣지 않는다 — 드라이버 기본값을 그대로 쓴다.
    """
    kwargs: dict = {}
    if getattr(cfg, "max_db_size", 0):
        kwargs["max_db_size"] = int(cfg.max_db_size)
    if getattr(cfg, "buffer_pool_mb", 0):
        kwargs["buffer_pool_size"] = int(cfg.buffer_pool_mb) * 1024 * 1024
    if getattr(cfg, "checkpoint_threshold_mb", 0):
        kwargs["checkpoint_threshold"] = int(cfg.checkpoint_threshold_mb) * 1024 * 1024
    wal_tolerant = os.environ.get("VINC_WAL_REPLAY_TOLERANT", "").strip().lower() \
        in ("1", "true", "on")
    kwargs["throw_on_wal_replay_failure"] = not wal_tolerant
    return kwargs


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


#: 디코드 사다리의 마지막 칸 — **시스템 ANSI 코드페이지**. Windows 에서만 뜻이 있다
#: (`mbcs` 는 Windows 전용 별칭이라 다른 OS 에는 코덱 자체가 없다. 한국어 Windows =
#: cp949). 모듈 상수로 꺼내 둔 이유: 이 사다리는 **플랫폼 가정**이고, 테스트가 그걸
#: 갈아 끼워야 하기 때문이다. `os.name` 만 "nt" 로 바꾸는 것으로는 부족하다 —
#: Linux 러너에는 `mbcs` 코덱이 아예 없어 복원 경로가 조용히 죽고, 그러면 CI 는
#: "복원했다"가 아니라 "복원할 수 없었다"를 통과시킨다.
_ANSI_FALLBACKS: tuple[str, ...] = ("mbcs",) if os.name == "nt" else ()


def _decode_native(raw) -> str:
    """OS/엔진이 만든 바이트열을 사람이 읽는 문자열로. UTF-8 → 로케일 → ANSI 순서."""
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw)
    import locale
    cands = ["utf-8", locale.getpreferredencoding(False), *_ANSI_FALLBACKS]
    for enc in cands:
        if not enc:
            continue
        try:
            return bytes(raw).decode(enc).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return bytes(raw).decode("utf-8", "replace").strip()


def _driver_error_text(exc: BaseException) -> str:
    """드라이버 예외 → **사람이 읽는 사유**. 못 읽는 예외를 그대로 흘리지 않는다.

    **왜 이 함수가 필요한가 (2026-08-07 실측 — ladybug 0.18.0 · win_amd64).**
    ladybug 의 pybind 경계(`Connection.execute` → `py_connection.query`)는 엔진이
    만든 `std::string` 을 **strict UTF-8** 로만 디코드한다. 그런데 그 문자열에는
    엔진이 아니라 **OS 가 만든 문구**가 섞여 들어온다 — `LOAD EXTENSION` 이
    실패할 때 Windows `LoadLibrary` 의 오류 문구가 대표적이고, 그건 UTF-8 이
    아니라 **시스템 ANSI 코드페이지**다(한국어 Windows = cp949). 그러면 디코드가
    터지고 드라이버는 원래 예외 **대신** `UnicodeDecodeError` 를 올린다:

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc1 in position 165

    즉 진짜 사유가 예외 하나로 통째로 증발한다. 실제로 증발한 원문은 이거였다 —
    `IO exception: Failed to load library: <경로> which is needed by extension:
    vector. Error: 지정된 모듈을 찾을 수 없습니다.`(= Windows 126, 확장이 링크한
    의존 DLL 미해결). 파일은 멀쩡히 있는데 말이다.

    이걸 복구하지 않으면 **호출부의 사유 분류가 전부 빗나간다**: `_load_ext` 는
    `"transaction" in msg` 같은 부분문자열로 사유를 가르는데, 손에 들어오는
    문자열이 코덱 얘기뿐이라 어느 갈래에도 안 걸리고 전부 마지막 갈래("확장
    미가용 — 오프라인 배포 시 번들 필요")로 떨어진다. 그건 이 파일이 2026-07-31
    에 하지 말자고 못 박은 **바로 그 오진**이다(운영자를 패키징 문제로 보낸다).

    다행히 원문 바이트는 `UnicodeDecodeError.object` 에 그대로 남아 있다 — 여기서
    되살린다. 예외 체인도 따라간다(드라이버가 감싸 올릴 수 있으므로)."""
    cur: BaseException | None = exc
    for _ in range(5):        # 체인 추적 상한 — 순환/과도 추적 방지
        if cur is None:
            break
        if isinstance(cur, UnicodeDecodeError):
            return (f"{_decode_native(cur.object)} "
                    f"[드라이버가 {cur.encoding} 로 디코드하지 못해 버린 원문을 복원함]")
        cur = cur.__cause__ or cur.__context__
    return str(exc)


def _filter_clause(var: str, filters: dict | None, params: dict, *,
                   keyword: str = "WHERE") -> str:
    """스코프 필터를 술어로 렌더(pre-filter, §4-2 — 스코프 1차 §1). params 를
    in-place 로 채운다. QUERY_*_INDEX 의 YIELD 와 RETURN 사이에 붙인다.

    ⚠ `keyword` 는 **붙는 자리에 이미 WHERE 가 있는가**로 정한다. `vector_search` 는
    자식→부모 되돌리기(`MATCH (p:Chunk) WHERE p.chunk_id = node.chunk_id`) 때문에
    WHERE 를 이미 갖고 있어서, 기본값을 그대로 쓰면 `WHERE … WHERE …` 가 되어
    **파서가 통째로 거부한다**. 실제로 그렇게 났다(2026-08-07): F2 가 청크 팔에
    owner 를 넘기기 시작하자 벡터 팔이 `Parser exception` 으로 죽었다. 그전에는
    filters 가 None 이라 이 함수가 빈 문자열을 돌려줘 결함이 가려져 있었고, 질의
    임베딩 자체가 없어 벡터 팔이 아예 안 돌던 것도 겹쳤다."""
    if not filters:
        return ""
    preds = []
    for key in ("owner_id", "domain", "tenant_id"):
        val = filters.get(key)
        if val is not None:
            preds.append(f"{var}.{key} = ${key}")
            params[key] = val
    return (f"{keyword} " + " AND ".join(preds) + " ") if preds else ""


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
    `api.query.search` 에 store 로 주입된다. 쓰기는 잡 워커의 writer Connection 으로만
    직렬화된다(R4).

    ⚠ **"쓰기"는 SET 만이 아니다.** 이 자리에 오래 `쓰기(SET) 는 이 뷰로 흘리지
    않는다` 라고 적혀 있었고, 그 좁은 문구 때문에 두 가지가 조용히 새 나갔다 —
    지연 인덱스 빌드(DDL)와 `LOAD EXTENSION`. 둘 다 SET 이 아니지만 **쓰기
    트랜잭션을 요구**하고, 엔진은 그걸 하나만 허용하므로 리더가 붙드는 동안
    라이터의 평범한 MERGE 가 거절된다(실측: 라이터 실패 26.0%). 기준은 SET 이
    아니라 **쓰기 트랜잭션을 요구하는가**다: DML·DDL·LOAD 전부 해당한다."""

    #: 청크 검색(FTS·벡터)에 `owner_id` 필터를 **실제로 적용한다**는 선언(SEC-1).
    #: `api.query.search` 가 owner 스코프 요청에서 이 표식을 확인하고, 없으면 청크
    #: 팔을 아예 돌리지 않는다(fail-closed). 표식을 안 다는 구현은 필터를 무시할 수도
    #: 있고, 그 경우 "스코프를 걸었다고 믿는 무스코프 조회"가 되기 때문이다 —
    #: 위험 감사 1.1 이 정확히 그 모양이었다. 실제 적용은 :func:`_filter_clause`.
    scoped_chunk_search = True

    def __init__(self, backend: "EmbeddedBackend", conn, writer=None):
        self._b = backend
        self._conn = conn
        # 읽기 요청 중에 **불가피하게 쓰기가 필요한** 연산을 쓰기 지점으로 돌려보내는
        # 통로(R4). 서버가 잡 큐에 태우는 `(label, fn, timeout) -> result` 를 주입한다.
        # 없으면(CLI·테스트 — 동시 라이터가 없는 단일 스레드) 그 자리에서 실행한다.
        # 해당 연산은 둘뿐이다: 지연 인덱스 빌드(DDL)와 그래프 알고리즘(project_graph
        # DDL). 여기에 새 항목을 더하기 전에 정말 읽기 중에 써야 하는지 먼저 의심할 것.
        self._writer = writer

    def _at_write_point(self, label: str, fn, timeout: float):
        if self._writer is None:
            return fn()
        return self._writer(label, fn, timeout)

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
        return self._at_write_point(
            "search.index", lambda: self._b.ensure_search_indexes(dim=dim) or {}, 60.0)

    def graph_algo(self, algo: str, **kw) -> list[dict]:
        """그래프 알고리즘(E-G3 WCC·Louvain·PageRank) — **쓰기 지점으로 보낸다**(R4).

        여태 `_ReaderView` 가 이걸 아예 노출하지 않았고, `api.query._run_algo` 는
        `getattr(store, "graph_algo", None)` 로 확인하므로 **조회 경로에서 상시
        `algo_unsupported` 로 강등**돼 있었다(2026-07-31 실측: 서버 read_diagnose 가
        `executed=False, components=[]`, 같은 데이터에 writer 로 직접 부르면 컴포넌트
        3개 정상 검출). 즉 상태 화면의 "연결 안 된 섬 n개"와 suggest_ties 후보 소스가
        배포된 채로 죽어 있었다. 게다가 사유 문구가 "store has no graph_algo (backend
        fallback)" 라 **백엔드 탓으로 오진**돼, 확장이 번들돼 있어도 그렇게 보였다.

        그냥 노출만 하면 안 된다 — `graph_algo` 는 `project_graph`/`drop_projected_graph`
        DDL 을 돌리므로 리더 커넥션에서 실행하면 그 순간 라이터가 막힌다(이 파일이
        방금 고친 바로 그 사고). 인덱스 빌드와 같은 통로로 잡 큐에 태운다.

        대가: 진단 조회가 앞선 쓰기 잡을 기다릴 수 있다(상한 60초). 진단은 상태 화면의
        비-핫 경로라 수용한다 — 검색 지연 빌드와 같은 판단이다.

        ⚠ 잡 결과는 **dict 여야 한다**(`api.jobs` 워커가 비-dict 를 `{"result": ...}`
        로 감싼다). 리스트를 그대로 넘기면 호출부가 dict 키(str)를 행으로 순회해
        `'str' object has no attribute 'get'` 로 터진다 — 명시적으로 싸고 푼다."""
        out = self._at_write_point(
            "graph.algo", lambda: {"rows": self._b.graph_algo(algo, **kw)}, 60.0)
        return out.get("rows") or []

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
        #: 이 Database 에 로드된 확장 이름. `LOAD EXTENSION` 은 **Database 스코프**라
        #: (실측: 로드 뒤 새로 만든 커넥션도 자기 LOAD 없이 FTS 호출 성공) 프로세스당
        #: 1회면 충분하다. 이 집합이 없으면 매 검색이 LOAD 를 재발행하고, LOAD 는
        #: 쓰기 트랜잭션을 요구하므로 라이터와 정면 충돌한다 — :meth:`_load_ext` 참조.
        self._exts: set[str] = set()
        #: 벡터 인덱스가 **지금 드롭된 상태인가**(이 프로세스가 아는 한).
        #: 임베딩 워커는 한 라운드에 문서 16개를 각각 `apply_embeddings` 로 넣고, 그
        #: 하나하나가 드롭을 부른다. DDL 이 라운드당 16번 나가고 그 사이 검색이 오면
        #: 매번 재빌드를 문다(위험 감사 6.6 — "벡터 팔이 사실상 상시 꺼져 있을 수
        #: 있다"). 드롭은 멱등이지만 **공짜가 아니다**. 이 플래그로 라운드 첫 번째만
        #: 실제 DDL 을 내보내고 나머지는 접는다. 만든 쪽이 다시 False 로 내린다.
        self._vec_index_dropped = False

    @classmethod
    def open(cls, cfg: StoreConfig) -> "EmbeddedBackend | None":
        data_dir = cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        drv, name = _import_driver()
        if drv is None:
            return None
        os.makedirs(os.path.dirname(data_dir) or ".", exist_ok=True)
        kwargs = _driver_kwargs(cfg)
        try:
            db = drv.Database(data_dir, **kwargs)
            conn = drv.Connection(db)
        except Exception as exc:
            # 스토어 열기 실패(락·손상·자원 고갈) → None 강등(§3-4). 호출부는
            # executed=False 로 처리한다. ⚠ 이유를 **삼키지 않는다**: 여태 None 만
            # 돌려줘 "no reachable store" 한 줄로 뭉개졌고, 자원 고갈(MemoryError)이
            # 스코프 격리 실패처럼 보이는 오진을 낳았다(로컬 Windows 10건의 정체).
            # `_driver_error_text` 를 거친다 — open 실패 사유에도 OS 문구(경로·권한·
            # WAL 재생)가 섞이고, 그게 비 UTF-8 이면 드라이버가 UnicodeDecodeError 로
            # 바꿔 올려 여기 남는 게 코덱 문구뿐이 된다. 그럼 "이유를 삼키지 않는다"
            # 는 이 자리의 목적이 그대로 무산된다(§ _driver_error_text).
            cls.last_open_error = f"{type(exc).__name__}: {_driver_error_text(exc)}"
            # stdout 에도 남긴다 — 이전엔 이 자리에 없어서 2026-08-03 장애 때 실제
            # 예외 문구를 어디서도 못 봤다(배너의 "driver": null 한 줄뿐).
            print(f"[embedded_backend] store open failed: {cls.last_open_error}",
                  flush=True)
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
    def reader_view(self, *, writer=None) -> "_ReaderView":
        """이 Database 에서 새 Connection 을 파생한 read 전용 뷰(R4). execute·search 가
        소비하는 표면(run/fulltext_search/vector_search/graph_algo/close)만 제공한다.
        요청 단위로 생성·close 한다(단일 사용자 — §12 R5, per-request 비용 무시 가능).

        ``writer``: 읽기 요청 중 불가피한 쓰기(지연 인덱스 빌드·그래프 알고리즘의
        project_graph)를 **쓰기 지점으로 되돌려 보내는** 콜러블
        ``(label, fn, timeout) -> result``. 서버가 잡 큐 진입점을 주입한다."""
        drv, _ = _import_driver()
        return _ReaderView(self, drv.Connection(self._db), writer=writer)

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
        # 데이터 스텝(v9, F9) — DDL 로 표현할 수 없는 이관은 여기서 돈다. 열 ALTER 가
        # 끝난 **뒤**여야 하고 버전 기록보다는 **앞**이어야 한다(중간에 죽으면 다음
        # 기동이 다시 시도한다). 백필 자체가 멱등이라 중복 실행도 무해하다.
        backfilled = 0
        if cur < EPISODE_TIME_BACKFILL_VERSION:
            backfilled = backfill_episode_time_fields(self)
        # 데이터 스텝(v11, E-1) — 계층 엣지는 기존 `domain` 열에서 파생되는데, 그것도
        # Cypher DDL 로는 못 쓴다(문자열을 잘라 노드·엣지를 만들어야 한다). 여기 두는
        # 이유는 v9 와 같다: **사이드카가 DB 락을 쥐고 있어 별도 프로세스로는 못 연다.**
        # 유일 writer 안에서, 서비스 시작 전에, 한 번 돈다.
        domain_links = 0
        if cur < DOMAIN_HIERARCHY_VERSION:
            from engines.domain_hierarchy import backfill as _dh_backfill
            domain_links = int(_dh_backfill(self).get("linked") or 0)
        if cur < target:
            self.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = $v",
                     {"v": str(target)})
        # 검색 확장을 **여기서** 로드한다. 프로비저닝은 단일 스레드 기동 시점이라
        # writer 커넥션 사용이 안전하고, 이 한 번으로 이후 모든 리더 커넥션이 자기
        # LOAD 없이 검색한다(Database 스코프 — :meth:`_load_ext`). 실패는 삼킨다:
        # 확장 부재(오프라인 미번들)가 스키마 마이그레이션 실패로 둔갑하면 안 된다.
        # 그 경우 검색만 강등되고 이유는 검색 응답의 extension_unavailable 로 나온다.
        for _e in ("fts", "vector"):
            try:
                self._load_ext(_e)
            except Exception:
                pass
        return {"migrated_from": cur, "migrated_to": target,
                "episode_time_backfilled": backfilled,
                "domain_links_backfilled": domain_links}

    def provision(self, *, dim: int) -> list[str]:
        """호환 진입점 — migrate() 로 위임(기존 호출부 무변경으로 마이그레이션 획득)."""
        self.migrate(dim=dim)
        return render_embedded_ddl(dim=dim)

    # ── 검색 인덱스 (M5/M6) ────────────────────────────────────────────────
    # Kùzu 계보의 벡터(HNSW)·FTS(BM25)는 확장 기능이다. ladybug 는 별도 확장 →
    # **오프라인 배포 시 번들 필수**(§9, G0). ⚠ 인덱스가 embedding 열에 걸려 있으면
    # 그 열을 수정할 수 없다 — 인덱스는 **적재 후 build**, 재적재 전 **drop**
    # (embed 잡이 관리, §3-1-1).
    #: SEC-1 — 청크 팔이 owner_id 필터를 적용한다(`_ReaderView.scoped_chunk_search` 주석).
    scoped_chunk_search = True
    _VEC_INDEX = "chunk_vec"
    # 자식 과추출 배수 — 한 부모의 조각들이 상위 k 를 다 차지하지 않게(:meth:`vector_search`).
    _CHILD_FANOUT = 4
    _FTS_INDEX = "chunk_ft"
    _OVERSAMPLE = 5   # pre-filter 보장용 과추출 배수(§4-2 — 스코프 필터 선택도 대비)

    def _load_ext(self, ext: str, *, reader: bool = False) -> None:
        """벡터/FTS/algo 확장 로드(ladybug 는 확장 기능 — 정적 링크가 아님).

        **G0(오프라인 확장 번들) — 실증 확정(2026-07-20):** ladybug 확장은 기본 런타임
        다운로드(`~/.lbdb/extension/<ver>/<plat>/<ext>/lib<ext>.lbug_extension`)라 오프라인
        불변식(§1)을 깬다. 실측: 캐시 부재 시 `LOAD EXTENSION <name>` 은 실패하나
        **`LOAD EXTENSION '<번들 경로>'` 는 네트워크 없이 로드 성공**(이후 CREATE_VECTOR_INDEX
        정상). → 번들된 `.lbug_extension` 을 명시 경로로 로드해 오프라인 즉시 동작을 보장한다.
        번들 미발견(dev 등)은 이름 기반으로 강등(설치돼 있으면 로드).

        **LOAD 는 쓰기다 — 읽기 경로에서 부르면 안 된다(2026-07-31 실측).** 여태 검색
        3종이 **매 호출마다** 리더 커넥션으로 LOAD 를 재발행했다. 측정해 보니:

        ① `LOAD` 는 **Database 스코프**다. 한 커넥션이 로드하면 그 뒤에 만든 커넥션도
           자기 LOAD 없이 `QUERY_FTS_INDEX` 를 부른다 — 즉 재발행은 애초에 불필요했다.
        ② 그런데 **이미 로드된 확장을 다시 LOAD 해도 쓰기 트랜잭션을 요구한다.** 라이터가
           트랜잭션을 쥔 순간 `Cannot start a new write transaction` 로 거절된다.
        ③ 그래서 라이터가 도는 동안 **검색의 49.3% 가 실패**했고(사용자에게 error 로
           노출), 리더의 LOAD 가 트랜잭션을 뺏는 바람에 **라이터 MERGE 도 26.0% 실패**
           했다. LOAD 를 생략하니 둘 다 0.0%, 검색 중앙 지연도 7.42ms → 4.68ms.

        따라서 로드는 **쓰기 지점에서만** 한다(provision·잡 워커의 인덱스 연산). 읽기
        경로(`reader=True`)는 미로드 시 **로드하지 않고 강등**한다 — 읽기가 쓰기를 하지
        않는다는 R4 를 지키는 유일한 방법이고, 어차피 ①에 의해 정상 기동이면 항상
        로드돼 있다. 대가: 기동 시 확장이 없었다면 프로세스 수명 동안 강등이 유지된다
        (재시작이 복구 수단). 매 검색 재시도로 얻던 우연한 자가치유를 실패율 49% 와
        맞바꾸지 않는다."""
        if ext in self._exts:
            return
        if reader:
            # 읽기 경로 — 여기서 LOAD 하면 쓰기 트랜잭션이 잡 큐를 우회한다(R4 위반).
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9·G0). 기동 시 로드되지 않았다(재시작 후 재확인).")
        c = self._conn
        p = _bundled_ext_path(ext)
        bundled_err = ""
        if p:
            try:
                c.execute(f"LOAD EXTENSION '{p}'")
                self._exts.add(ext)
                return
            except Exception as exc:
                # ⚠ 사유 판별은 **반드시** `_driver_error_text` 를 거친다 — 드라이버가
                # 원문을 못 읽고 UnicodeDecodeError 로 바꿔 올리면 `str(exc)` 는 코덱
                # 얘기라 여기 "already" 도, 아래 "transaction" 도 영영 안 걸린다.
                bundled_err = _driver_error_text(exc)
                if "already" in bundled_err.lower():
                    self._exts.add(ext)
                    return
                # 번들 경로 로드 실패 → 아래 이름 기반 폴백(dev)
        try:
            c.execute(f"LOAD EXTENSION {ext}")
            self._exts.add(ext)
        except Exception as exc:
            text = _driver_error_text(exc)
            low = text.lower()
            if "already" in low or "statically linked" in low:
                self._exts.add(ext)
                return
            # 두 경로가 다 실패했으면 **둘 다** 말한다. 이전엔 이름 기반 실패만
            # 올려서, 번들 경로에서 난 진짜 사유(예: 의존 DLL 미해결)가 캐시 부재
            # 문구에 덮여 사라졌다 — 정확히 반대 방향을 가리키는 진단이 된다.
            detail = text
            if bundled_err and bundled_err != text:
                detail = f"번들 경로({p}) → {bundled_err} · 이름 기반 → {text}"
            # ⚠ **아는 것만 말한다**(2026-07-31 실측). LOAD 가 실패하는 이유가 확장
            # 부재만은 아니다: 다른 커넥션이 쓰기 트랜잭션을 쥐고 있으면 엔진이
            # "Cannot start a new write transaction" 로 거절한다(실측 — 리더의 지연
            # 인덱스 빌드와 겹칠 때). 그걸 "확장 미가용 — 오프라인 배포 시 번들 필요"
            # 로 감싸면 운영자를 패키징 문제로 보내는 **틀린 진단**이 된다. 확장과
            # 무관한 사유는 원문 그대로 올린다.
            if "transaction" in low:
                if text != str(exc):
                    # 원본 예외는 읽을 수 없는 형태다(코덱 문구뿐) — 복원본으로 올린다.
                    raise RuntimeError(text) from exc
                raise
            # 확장 **파일은 찾았는데** 그게 링크한 라이브러리가 안 잡힌 경우(실측
            # 2026-08-07, Windows 126). 여기서 "번들 필요" 라고 하면 이미 있는 파일을
            # 또 넣게 만든다 — 필요한 건 의존 라이브러리다. 처방을 바꿔 말한다.
            #
            # ⚠ 판정은 `low`(이름 기반)가 아니라 **`detail`(두 경로 합본)**으로 한다.
            # 번들 경로에서 의존 실패가 나고 이름 기반은 "설치 안 됨"으로 떨어지는
            # 조합이 실재하는데(G0 테스트: 캐시를 치워 두고 번들만 준다), 이름 기반만
            # 보면 "번들 필요" 라고 답하게 된다 — 번들은 있었고 본문에 그 실패가
            # 그대로 찍혀 있는데도. 스스로 모순된 진단이다.
            if "failed to load library" in detail.lower():
                # Windows 에서 이 갈래에 떨어지는 사유는 **하나로 수렴한다**(2026-08-07
                # 실측, ladybug 0.18.0 · win_amd64). 확장 3종(algo·fts·vector)의 PE
                # 임포트 테이블을 전수로 읽으면 비시스템 의존 중 못 잡히는 것이
                # `libssl-3-x64.dll`·`libcrypto-3-x64.dll` 둘뿐이고(오류 126) 나머지
                # (MSVCP140·VCRUNTIME140·api-ms-win-crt-*)는 전부 잡힌다. 확장은 그
                # **비변형 이름**을 요구하는데 ladybug 휠은 delvewheel 이 변형한 이름
                # (`libssl-3-x64-<hash>.dll`)만 `ladybug.libs` 에 동봉한다. 즉 상류
                # 바이너리의 의존 이름 불일치이고 Vinc 코드 결함이 아니다.
                #
                # 그래서 처방이 갈린다: 번들을 더 넣어도 안 되고, **OpenSSL 3 을 DLL
                # 검색 경로에 올리면 그 자리에서 해소된다**(실측: PATH 에 올린 직후
                # `LOAD EXTENSION vector` 성공). Git for Windows 가 그 이름 그대로
                # `mingw64/bin` 에 두므로 Git Bash 에서 돌리면 우연히 통과하고
                # PowerShell 에서는 실패한다 — 같은 리비전, 같은 머신인데 셸에 따라
                # 결과가 갈리는 정체가 이것이다.
                hint = ""
                if os.name == "nt":
                    hint = (" Windows 에서는 대개 OpenSSL 3(libssl-3-x64.dll·"
                            "libcrypto-3-x64.dll)이 DLL 검색 경로에 없어서다 — "
                            "그 둘이 있는 디렉터리를 PATH 에 올려라.")
                raise RuntimeError(
                    f"임베디드 {ext} 확장 미가용({self.driver_name}) — 확장 파일은 찾았으나 "
                    f"그것이 요구하는 의존 라이브러리를 로드하지 못했다(번들 누락이 아니다)."
                    f"{hint} 원인: {detail}") from exc
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9·G0). 원인: {detail}") from exc

    def drop_vector_index(self) -> None:
        """**벡터(HNSW) 인덱스만** 제거(멱등). 쓰기 경로가 불러야 하는 건 이쪽이다.

        **왜 FTS 는 안 건드리나 (2026-07-31 실측).** 오랫동안 쓰기 경로가 둘 다 드롭했고
        재빌드는 검색 경로에 위임했다. 그 결과 **임베딩이 도는 동안 키워드 검색이
        전부 실패했다**(실측: 임베딩 60라운드 중 FTS 성공 0회). 지금 유일하게 동작하는
        검색 경로가 FTS 인데 배경 작업이 그걸 주기적으로 끄고 있었던 셈이다.
        FTS 는 mutable 이고(삽입·삭제가 즉시 반영) 아래 크래시 경로와도 무관하므로
        드롭할 이유가 없다. 벡터만 드롭하면 2,000청크 적재 중 FTS 검색이 200/200 성공한다.

        **왜 벡터는 여전히 드롭해야 하나.** 엔진 인덱스가 mutable 로 바뀌어 "인덱스 걸린
        embedding 열은 수정 불가"라는 옛 전제는 문자 그대로는 거짓이다 — SET 이 성공한다.
        그런데 **성공한 뒤 죽는다**: 벡터가 없던 자리에 벡터가 생기면(= HNSW 삽입) 수백 건
        지점에서 **세그폴트**가 난다(실측 exit 139, 2,000청크 적재 시 문서 50~60 부근).
        같은 행의 벡터 **값만** 바꾸는 것은 2,000회 무사하고, embedding 없는 행 삽입과
        대량 삭제도 각각 2,000청크까지 무사하다. 즉 위험한 것은 오직 "없던 벡터가 생기는" 것.

        하필 프로덕션 흐름이 정확히 그쪽이다 — `put_doc` 은 embed 콜러블이 없으면 청크를
        벡터 없이 넣고 워커가 나중에 `apply_embeddings` 로 채운다. 세그폴트는 파이썬 예외가
        아니라 프로세스 사망이라 `except` 로 못 막고, 허브에선 사이드카 사망 =
        컨테이너(=MCP) 사망이다. 그래서 그 경로에서는 **드롭이 방어 장치**다.
        (해당 지점을 지나면 `ensure_search_indexes` 가 검색 시 다시 만든다.)

        **라운드당 1회로 접는다(위험 감사 6.6).** 이미 드롭해 둔 것을 아는 동안에는
        DDL 을 다시 내보내지 않는다. 워커 한 라운드가 문서 16개를 각각 apply 하므로
        그 접기가 없으면 5분마다 DDL 이 16번 나가고, 그 사이에 들어온 검색이 매번
        재빌드를 물어 벡터 팔이 사실상 상시 꺼져 있게 된다. 다시 만드는 쪽
        (:meth:`ensure_search_indexes`·:meth:`build_search_indexes`)이 플래그를 내린다."""
        if self._vec_index_dropped:
            return
        self._load_ext("vector")
        try:
            self._conn.execute(f"CALL DROP_VECTOR_INDEX('ChunkVec','{self._VEC_INDEX}')")
        except Exception:
            pass  # 미존재 — 무시(멱등)
        self._vec_index_dropped = True

    def drop_search_indexes(self) -> None:
        """벡터+FTS 인덱스 **둘 다** 제거(멱등). 전량 재적재·재빌드 구간 전용이다.

        쓰기 경로(문서 저장·임베딩 적용)에서는 이걸 부르지 말 것 — FTS 까지 끊겨
        그동안 키워드 검색이 죽는다. 그쪽은 :meth:`drop_vector_index` 를 쓴다."""
        self.drop_vector_index()
        self._load_ext("fts")
        try:
            self._conn.execute(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')")
        except Exception:
            pass  # 미존재 — 무시(멱등)

    #: 엔진이 FTS 색인 불일치를 **스스로 식별해** 던지는 문구의 판별 표지.
    #: 실측 원문(ladybug 0.18.0, 2026-08-09 라이브·사본 양쪽):
    #:   "FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
    #:    missing during delete. Drop and recreate the FTS index."
    #: 문구 전체가 아니라 두 표지의 **동시 등장**으로 본다 — 오프셋·색인명은 매번 다르고,
    #: 한쪽만 보면 무관한 오류를 물어 엉뚱하게 재빌드한다.
    _FTS_INCONSISTENT_MARKS = ("fts index", "inconsistent")

    @classmethod
    def is_fts_inconsistent(cls, exc: BaseException) -> bool:
        """이 예외가 **그** 불일치인가. 재빌드로 고쳐지는 조건만 참이어야 한다."""
        m = str(exc).lower()
        return all(k in m for k in cls._FTS_INCONSISTENT_MARKS)

    def rebuild_fts_index(self) -> None:
        """FTS 색인**만** 드롭 후 재생성(벡터는 건드리지 않는다).

        왜 `drop_search_indexes()` + `build_search_indexes()` 를 쓰지 않나: 그 짝은
        벡터까지 함께 다룬다. 여기서 벡터를 드롭하면 HNSW 재빌드가 딸려 오는데 그건
        비교도 안 되게 비싸고, 애초에 벡터 색인은 멀쩡하다. 반대로 벡터를 드롭하지 않은
        채 `build_search_indexes` 를 부르면 `CREATE_VECTOR_INDEX` 가 'already exists'
        로 터진다(그 메서드는 그걸 흡수하지 않는다).

        비용: 실측 **5.8초**(청크 995건, 라이브 DB 사본, linux/amd64). 재빌드하는 동안
        키워드 검색이 잠깐 비므로 **정기 작업으로 돌리지 말 것** — 불일치를 실제로
        만났을 때만 부른다(:func:`api.docstore._with_fts_repair`).

        ⚠ 이건 엔진 결함의 **대증요법**이다. 원인(증분 유지가 문서를 잃는 것)은 엔진
        안에 있고 우리 쪽에서 고칠 수 있는 지점이 아니다. 2026-08-09 실측: 재빌드
        전후로 같은 데이터에서 히트가 늘었다(graph 224→225 · vinc 305→307) — 색인이
        존재하는 행의 문서를 갖고 있지 않았다는 뜻이고, 그 행의 삭제가 터진다."""
        self._load_ext("fts")
        try:
            self._conn.execute(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')")
        except Exception:
            pass  # 미존재 — 그냥 만든다(멱등)
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['text'])")

    def build_search_indexes(self, *, dim: int) -> None:
        """**빈 자리에** 벡터(HNSW)+FTS(BM25) 인덱스 생성. `drop_search_indexes()` 와
        **짝으로만** 쓴다.

        ⚠ **'already exists' 를 흡수하지 않는다.** 둘 중 하나라도 이미 있으면
        `Binder exception: Index <이름> already exists in table <표>` 로 던진다
        (2026-08-09 linux/amd64 실엔진 실측: 벡터만 드롭한 상태에서 부르면 FTS 에서,
        아무것도 안 드롭하면 벡터에서 먼저 터진다).

        ⚠ 그래서 **프로덕션 호출부는 이 메서드를 쓰지 않는다.** 한동안 두 곳이 썼는데
        (`/doc/reembed` · `embed_pipeline`) 둘 다 벡터만 드롭하는 경로라 살아 있는 FTS
        위에서 터지고 있었다 — 그것도 임베딩과 벡터 색인을 다 만든 **뒤에**. 지금은
        둘 다 :meth:`ensure_search_indexes` 를 쓴다. 새로 쓸 일이 생기면 정말
        **드롭이 선행되는가**부터 볼 것. 아니면 `ensure_` 다.

        (이 메서드를 멱등하게 만들지 않은 이유: 그러면 `ensure_search_indexes` 와
        뜻이 같아진다. 이름이 다른 같은 것 둘은 드리프트의 출발점이다.)"""
        self._load_ext("vector")
        self._load_ext("fts")
        self._conn.execute(
            f"CALL CREATE_VECTOR_INDEX('ChunkVec','{self._VEC_INDEX}','embedding')")
        self._vec_index_dropped = False
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['text'])")

    def ensure_search_indexes(self, *, dim: int | None = None, conn=None) -> dict:
        """검색 인덱스 **지연 보장**(멱등) — 부재 시에만 생성한다(T3b 실사용 사고 대응).

        `build_search_indexes` 와 달리 이미 있으면 'already exists' 를 흡수해 재빌드하지
        않는다 — search 경로가 '인덱스 부재'로 실패했을 때 1회만 부르는 자동 복구용
        (`api.query.search`). FTS(BM25)는 항상, 벡터(HNSW)는 `dim` 이 주어질 때만
        보장한다(쿼리 임베딩 검색이 필요할 때만 벡터 인덱스 생성). 반환 {fts, vector}
        = 이번 호출에서 실제 생성했는지.

        ⚠ 이 메서드는 **쓰기 지점에서만** 불린다 — reader 뷰는 잡 큐로 되돌려 보낸다
        (:meth:`_ReaderView.ensure_search_indexes`). 예전엔 reader 가 자기 커넥션을
        `conn` 으로 넘겨 여기서 DDL 을 돌렸는데, 그게 라이터를 막았다(2026-07-31
        실측). `conn` 은 그 시절 잔재로 지금은 넘기는 호출부가 없다 — 남겨 두되
        (하위호환) 새 호출부가 쓰지 않게 한다."""
        c = conn or self._conn
        built = {"fts": False, "vector": False}

        def _create(ext: str, call: str) -> bool:
            # 인덱스 생성은 쓰기다 — 이 경로는 잡 워커(또는 CLI 단일 스레드)에서만
            # 돈다(`_ReaderView.ensure_search_indexes` 가 잡 큐로 되돌린다). 따라서
            # 여기서는 writer 로 로드해도 R4 를 어기지 않는다.
            self._load_ext(ext)
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
                "vector",
                f"CALL CREATE_VECTOR_INDEX('ChunkVec','{self._VEC_INDEX}','embedding')")
            # 방금 만들었든 이미 있었든(`already exists` 흡수) 결과는 같다 — 인덱스는
            # 존재한다. 드롭 접기 플래그를 내려 다음 쓰기가 제대로 한 번 드롭하게 한다.
            self._vec_index_dropped = False
        return built

    def vector_search(self, embedding, top_k, filters=None, *, conn=None) -> list[dict]:
        """벡터 검색 — **자식(ChunkVec)에서 찾아 부모(Chunk)로 되돌린다**(v8).

        인덱스가 청크가 아니라 그 조각에 걸려 있다. 청크 하나가 여러 벡터를 가지므로
        상위 k 개 자식이 같은 부모를 여러 번 가리킬 수 있다 — **부모 단위로 접어야**
        호출부(RRF 융합·발췌)가 지금과 같은 계약을 본다. 접을 때는 **가장 가까운 자식의
        distance** 를 부모 점수로 쓴다(최소값). 평균이 아니라 최소인 이유: 긴 청크의
        한 대목만 질의와 맞아도 그 청크는 답이다 — 나머지 조각이 무관하다고 점수를
        깎으면 긴 문서가 부당하게 밀린다.

        그래서 자식은 **넉넉히 뽑는다**(k × _CHILD_FANOUT). 안 그러면 한 부모의 조각들이
        상위 k 를 다 차지해 부모 다양성이 줄어든다."""
        c = conn or self._conn
        self._load_ext("vector", reader=conn is not None)
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)  # pre-filter 과추출(§4-2)
        kk = k * self._CHILD_FANOUT
        params: dict = {"emb": list(embedding), "k": kk}
        # 필터는 부모(Chunk)의 열을 본다 — 자식엔 layer 도 text 도 없다.
        # ⚠ `AND` 다. 아래 MATCH 가 자식→부모 되돌리기로 WHERE 를 **이미** 쓰고 있어서
        #    WHERE 를 또 붙이면 파서가 쿼리를 통째로 거부한다(실측 2026-08-07).
        where = _filter_clause("p", filters, params, keyword="AND")
        cypher = (
            f"CALL QUERY_VECTOR_INDEX('ChunkVec','{self._VEC_INDEX}', $emb, $k) "
            "MATCH (p:Chunk) WHERE p.chunk_id = node.chunk_id "
            f"{where}"
            "RETURN p.chunk_id AS id, p.text AS text, distance "
            "ORDER BY distance"
        )
        rows = _exec_to_dicts(c, cypher, params)
        best: dict[str, dict] = {}
        for r in rows:                       # 이미 distance 오름차순 — 첫 등장이 최소
            best.setdefault(r.get("id"), r)
        return list(best.values())[:int(top_k)]

    def fulltext_search(self, text, top_k, filters=None, *, conn=None) -> list[dict]:
        c = conn or self._conn
        self._load_ext("fts", reader=conn is not None)
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
        self._load_ext("algo", reader=conn is not None)
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

        복사 전 **CHECKPOINT** 로 WAL 을 본 파일에 반영한다 — 자동 체크포인트는
        WAL 이 임계값을 넘고 활성 트랜잭션이 없을 때만 도므로, 지금 이 순간의 WAL 이
        본 파일에 들어가 있다는 보장이 없다. 그대로 복사하면 미반영(빈 테이블) 사본이
        나온다(P2 스냅샷 복원 왕복 실측, 2026-07-10). 미지원 방언은 무시하고 진행.

        ⚠ 2026-08-08 사고의 오진을 여기 적어 둔다. 이 자리에 원래 "Kùzu 계보는 close
        시점에 체크포인트한다"고 적혀 있었는데 **사실이 아니다**. ladybug 0.18.0 의
        `SystemConfig` 는 `autoCheckpoint=true, checkpointThreshold=16MB` 가 기본이라
        close 없이도 임계값에서 스스로 체크포인트한다(v0.18.0 소스·공식 문서 확인,
        로컬 실엔진 재현). 그 틀린 주석을 근본원인으로 채택해 사고 보고서까지 갔다 —
        주석 하나가 오진의 발원지가 된 사례다.

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
                self._db = drv.Database(src, **_driver_kwargs(self._cfg))
                self._conn = drv.Connection(self._db)
                # 새 Database 인스턴스는 확장 카탈로그가 비어 있다 — 캐시가 남아
                # 있으면 리더가 "로드됨"으로 믿고 QUERY_FTS_INDEX 를 불러 정체
                # 불명의 'function not defined' 를 맞는다. 무효화 후 재로드한다.
                self._exts.clear()
                for _e in ("fts", "vector"):
                    try:
                        self._load_ext(_e)
                    except Exception:
                        pass
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
