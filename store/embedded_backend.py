# -*- coding: utf-8 -*-
"""EmbeddedBackend — 인프로세스 그래프 스토어 (LadybugDB, Kùzu 계보).

설계 정본 [[vinc.canon.principles]]. 코드가 특정 패키지명에 결합되지
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

import functools
import json
import os
import re
import sys
import threading
import time
import weakref
from contextlib import contextmanager, nullcontext

from . import pkns, request_timing
from .open_diagnostics import emit_open_failure
from .config import StoreConfig
from .embedded_schema import (DOMAIN_HIERARCHY_VERSION,
                              EPISODE_TIME_BACKFILL_VERSION, LEX_INDEX_KEY,
                              NODE_VEC_REKEY_VERSION, SCHEMA_VERSION,
                              migration_statements, node_table_specs,
                              rel_tables_on, render_embedded_ddl,
                              render_node_table, render_rel_table)


class IndexRemnant(RuntimeError):
    """표에 카탈로그 없는 같은 이름 확장 색인(벡터·FTS)이 남아 그 색인을 만들 수 없고,
    그 표의 행 **쓰기가 전부** 세그폴트다 — 삽입·삭제·아무 열의 SET 까지(실측).

    재시도로 풀리지 않는다. 기동 수리가 표를 다시 떠서 고치고, 그 전까지는 백엔드가 그
    표에 쓰는 문장을 엔진에 보내기 전에 거절한다(:meth:`EmbeddedBackend.run`).
    ``table`` 이 어느 표인지다.
    근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]"""

    def __init__(self, message: str, *, table: str = ""):
        super().__init__(message)
        self.table = table


class VectorIndexRemnant(IndexRemnant):
    """`ChunkVec`(또는 노드 미러 `NodeVec`)에 벡터 색인 잔해가 있다.

    기동 때 :meth:`EmbeddedBackend.heal_vector_index` 가 표를 다시 떠서 고친다."""


class FtsIndexRemnant(IndexRemnant):
    """`Chunk` 에 FTS 색인 잔해가 있다 — 문서 저장·삭제가 전부 멈춘다.

    기동 때 :meth:`EmbeddedBackend.heal_fts_index` 가 `Chunk` 와 거기 걸린 관계 표
    (`MENTIONS`)를 함께 다시 떠서 고친다."""


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
            # PK 네임스페이싱 — 이 조회가 주는 id 는 읽기 훅(_exec_to_dicts)이 논리로
            # 되돌린 슬러그다. 아래에서 **다시 바인딩**하므로 각 행의 owner 로 합성해야
            # 한다. 그래서 owner_id 를 함께 싣는다. 근거: [[vinc.plan.node-pk-namespacing]] §7
            "RETURN e.episode_id AS id, e.props AS props, e.owner_id AS owner_id, "
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
        sets, params = [], {"id": pkns.key(r.get("owner_id") or "", nid)}
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


#: 엔진의 계획 캐시 범위(`enable_cached_prepared_statement`, 0.20.2 부터 있다). 값은
#: `NONE|WRITES|READS|BOTH` 이고 엔진 기본은 `BOTH` 다. **기본을 `NONE` 으로 내린다.**
#: 같은 매개변수 FTS 질의(`QUERY_FTS_INDEX ... $q`)를 한 커넥션에서 두 번 돌리면 두 번째가
#: 세그폴트한다(0.20.0~0.20.4 · 결과가 한 행이라도 있을 때 · `READS`·`BOTH` 에서만). 모든
#: 검색이 그 문장을 지나고, 세그폴트는 `except` 로 못 막는다. `WRITES` 도 살아남지만 아직
#: 전수 측정 전이라 `NONE` 으로 둔다 — 바꿔 재 볼 때만 이 env 를 쓴다.
#: 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2.1
_PLAN_CACHE_SCOPE = os.environ.get("VINC_LBUG_PLAN_CACHE", "NONE").strip().upper() or "NONE"

#: algo 확장이 라이브러리 로드에서 실패할 때 붙이는 사유. 0.20.x 의 algo 확장(v0.20.0)은
#: NetworKit·Arrow 공유 라이브러리를 링크하면서 싣지 않는다 — 리눅스 `libnetworkit.so`·
#: `libarrow.so.2500`, 맥 `libnetworkit.dylib`·`libarrow.2500.dylib`, 윈도우
#: `networkit_state.dll`·`arrow.dll`. 상류 서버·릴리스 어디에도 없고, 짝이 맞는 icebug 13.1 은
#: PyPI 에 없다(13.0 은 로드 중 세그폴트, 13.2 는 심볼 누락 — 2026-09-27 실측). 그래서 싣지
#: 않고 **분명히 강등한다**: 구조 진단만 멈추고 나머지는 그대로 돈다.
#: 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2.3
_ALGO_UNAVAILABLE_NOTE = (
    "ladybug 0.20.x 의 algo 확장은 NetworKit·Arrow 공유 라이브러리를 링크하지만 싣지 않는다"
    "(상류 LadybugDB/ladybug#857). 구조 진단(vinc_diagnose 의 components·communities·orphans 와"
    " 그 위의 기능)은 이 빌드에서 쓸 수 없다 — 번들을 더 넣어도 풀리지 않는다.")

#: 스키마를 바꾸는 문장(인덱스·표·열·투영 그래프·확장 로드). 이것이 지나가면 같은
#: 스토어의 모든 커넥션의 파이썬 문장 캐시를 비운다(:class:`_Conn`).
_DDL_STMT = re.compile(
    r"^\s*(?:(?:CREATE|DROP|ALTER)\s+(?:NODE\s+|REL\s+)?TABLE\b"
    r"|CALL\s+(?:CREATE|DROP)_\w+|CALL\s+(?:PROJECT_GRAPH|DROP_PROJECTED_GRAPH)\b"
    r"|LOAD\s+EXTENSION\b|INSTALL\b)", re.IGNORECASE)

#: 벡터 표(`ChunkVec`·`NodeVec`)를 바꿀 수 있는 문장 = 표 이름 + 쓰기·DDL 절. 일부러 넓다:
#: 더 잡으면 도장 캐시가 한 번 더 훑을 뿐이고, 덜 잡으면 캐시가 옛 답을 준다.
_VEC_TABLE = re.compile(r"\b(?:ChunkVec|NodeVec)\b", re.IGNORECASE)
_VEC_WRITE = re.compile(r"\b(?:CREATE|MERGE|SET|DELETE|REMOVE|COPY|DROP|ALTER)\b", re.IGNORECASE)
_vector_generation = 0
_vector_generation_lock = threading.Lock()


def vector_generation() -> int:
    """벡터 표 쓰기 세대(프로세스 전역). 벡터를 바꾼 문장이 **돌고 난 뒤** 오른다(:class:`_Conn`).

    캐시하는 쪽은 훑기 **전에** 이 값을 읽어 답에 붙인다. 그러면 붙인 세대가 지금과 같은
    답은 그 뒤에 끝난 벡터 쓰기를 놓친 적이 없다(쓰기는 커밋 뒤에 올리고 읽기는 그 뒤에
    시작했으므로). 다른 프로세스의 쓰기는 모른다: 스토어를 쓰는 프로세스는 하나다(I4)."""
    return _vector_generation


def _bump_vector_generation() -> None:
    global _vector_generation
    with _vector_generation_lock:
        _vector_generation += 1


def _forget_prepared(raw) -> None:
    """드라이버 커넥션의 **파이썬 쪽** 암묵 문장 캐시를 비운다(0.18.1 부터 있다, 0.18.0 은 무동작).

    ladybug 의 `Connection.execute` 는 매개변수가 있는 문장을 `(질의, 매개변수 모양)` 키로
    커넥션마다 캐시하는데, **바인드에 실패한 문장도 캐시하고 DDL 로 무효화하지 않는다.**
    그래서 한 커넥션에서 "색인 없음 → 색인 생성 → 같은 질의 재시도"를 밟으면 재시도가
    옛 바인더 오류를 그대로 돌려준다 — 지연 FTS 빌드가 정확히 그 순서다. 공개 API 가 없어
    사적 속성을 만진다: 잠금은 드라이버의 것을 그대로 쥐고(실행 중인 문장과 겹치지 않게)
    참조만 놓는다. 항목은 자기 소멸자로 정리된다(pybind 는 shared_ptr, C API 는 `__del__`).
    근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24]] §2.2"""
    cache = getattr(raw, "_pybind_implicit_prepared_cache", None)
    if not cache:
        return
    lock = getattr(raw, "_prepared_cache_lock", None)
    if lock is None:
        cache.clear()
        return
    with lock:
        cache.clear()


class _Conn:
    """드라이버 커넥션의 얇은 겉감 — 문장 캐시 가드(B)의 **유일한** 자리.

    백엔드가 여는 커넥션은 전부 이것을 거친다(:func:`_open_connection`). 호출부 27곳이
    `self._conn.execute(...)` 를 직접 부르므로 거기마다 캐시를 비우는 대신 여기서 한 번 본다:

    - 매개변수 문장이 **실패하면** 그 커넥션의 캐시를 비운다(실패한 바인드를 들고 있지 않게).
    - **DDL 이 지나가면**(성공이든 실패든) 같은 스토어의 **모든** 커넥션 캐시를 비운다 —
      리더 커넥션은 라이터가 만든 색인을 자기 캐시 너머로 봐야 한다.
    - **벡터 표를 바꾼 문장이 지나가면** 벡터 쓰기 세대를 올린다(:func:`vector_generation`).
      문장마다 자동 커밋이라 돌고 난 뒤가 곧 커밋 뒤다. 쓰기 경로(적용·미러·삭제·purge·
      소유자 접기·마이그레이션)를 하나하나 고치지 않고 여기 하나로 받는다.

    그 밖의 속성(`close` 등)은 드라이버 커넥션으로 그대로 넘긴다."""

    def __init__(self, raw, family: "weakref.WeakSet | None" = None):
        self._raw = raw
        self._family = family
        if family is not None:
            family.add(self)

    def execute(self, query, parameters=None):
        try:
            # 드라이버 호출에는 네이티브 실행·내부 대기도 포함된다. 스레드 CPU만으로
            # 이 둘을 구별하지 않는다. 근거: [[vinc.manual.core §7]]
            with request_timing.measure("db_execute"):
                if parameters:
                    return self._raw.execute(query, parameters)
                return self._raw.execute(query)
        except Exception:
            if parameters:
                _forget_prepared(self._raw)
            raise
        finally:
            if isinstance(query, str) and _DDL_STMT.match(query):
                for c in list(self._family or (self,)):
                    _forget_prepared(c._raw)
            if isinstance(query, str) and _VEC_TABLE.search(query) and _VEC_WRITE.search(query):
                _bump_vector_generation()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def _open_connection(drv, db, family=None) -> _Conn:
    """백엔드의 커넥션을 여는 **유일한** 길 — 라이터·리더·스냅샷 재오픈이 모두 이것을 쓴다.

    계획 캐시(A)는 커넥션 단위 설정이라 여기서 매번 끈다. 켜지 못하면 던진다: 조용히
    지나가면 첫 두 번째 검색이 프로세스를 죽인다. 단 스위치가 아예 없는 엔진(0.20.2 이전
    — 0.18.0 등)은 그 결함도 없으므로 그대로 둔다."""
    with request_timing.measure("connection_open"):
        raw = drv.Connection(db)
    try:
        with request_timing.measure("connection_config"):
            raw.execute(f"CALL enable_cached_prepared_statement='{_PLAN_CACHE_SCOPE}'")
    except Exception as exc:
        if _PLAN_CACHE_SCOPE != "BOTH" and _plan_cache_switch_exists(drv):
            raise RuntimeError(
                f"계획 캐시를 {_PLAN_CACHE_SCOPE} 로 내리지 못했다(검색이 세그폴트한다): "
                f"{_driver_error_text(exc)}") from exc
    return _Conn(raw, family)


def _plan_cache_switch_exists(drv) -> bool:
    """`enable_cached_prepared_statement` 가 있는 엔진인가(0.20.2 이상)."""
    ver = str(getattr(drv, "__version__", "") or "")
    try:
        parts = tuple(int(p) for p in ver.split(".")[:3])
    except ValueError:
        return True          # 모르는 판 — 있다고 보고 실패를 올린다(삼키지 않는다)
    return parts >= (0, 20, 2)


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


def _stable_ext_dir() -> str:
    """`_MEIPASS` 사본을 두는 **수명이 안정한** 디렉터리 — `~/.vinc/lbug_extensions/<ver>/`.

    데이터 홈(`~/.vinc`, D43 · `store.config.resolve_data_dir`) 밑에 두는 것이 핵심이다.
    이 경로를 기록하는 것이 **그 밑의 스토어 WAL** 이므로 둘의 수명이 같아야 한다 —
    스토어가 살아 있는 한 경로도 살아 있고, 홈을 지우면 같이 사라진다.

    드라이버 버전으로 한 칸 더 가른다. 확장 바이너리는 엔진 버전에 묶이고, 앱 업데이트
    직후에는 **이전 버전 때 쓰인 WAL** 이 아직 남아 있을 수 있다. 그래서 옛 버전 디렉터리는
    **지우지 않는다** — 수 MB 로 "업그레이드 직후 첫 기동이 안 됨"을 산다."""
    import os
    import pathlib
    try:
        ver = getattr(__import__("ladybug"), "__version__", "") or "unknown"
    except Exception:   # noqa: BLE001 — 드라이버 미설치면 어차피 여기까지 안 온다
        ver = "unknown"
    return str(pathlib.Path.home() / ".vinc" / "lbug_extensions" / str(ver))


def _mirror_to_stable(src: str, fname: str) -> str:
    """`_MEIPASS` 의 확장 파일을 안정 디렉터리로 한 번 복사하고 그 경로를 준다.

    **왜 이 복사가 필요한가 (2026-08-14 실증).** `LOAD EXTENSION '<절대경로>'` 는 쓰기라
    **WAL 에 그 절대경로를 문자열 그대로 남긴다**(실측: 깨끗한 종료 후 `db` 에는 0건,
    급사 후 `db.wal` 에는 확장 수만큼 평문으로 박혀 있다). 다음 열기의 WAL 재생이 그
    경로로 LOAD 를 **재실행**하고, 실패하면 열기 자체가 중단된다.

    PyInstaller onefile 의 `sys._MEIPASS` 는 **실행마다 바뀌는** `%TEMP%/_MEI<랜덤>` 이다.
    즉 기동할 때마다 "다음 기동에는 없을지도 모르는 경로"를 WAL 에 심는 셈이다. 깨끗한
    종료는 체크포인트로 그 기록을 지우지만, 급사하면 남고 — 그 뒤 `_MEI<A>` 가 사라지거나
    (임시폴더 청소·앱 업데이트·백신 격리) 그 프로세스가 아닌 무언가가 스토어를 열면
    (dev 파이썬 · `api.cli backup` · MCP CLI) **스토어가 안 열린다. 그리고 영구적이다** —
    이후 모든 열기가 같은 WAL 을 재생한다. `VINC_WAL_REPLAY_TOLERANT` 는 이 실패를 덮지
    **못한다**(실측). 남는 복구는 기록된 경로를 되살리거나 `db.wal` 을 버리는 것뿐이다.

    그래서 고칠 것은 로드 방식이 아니라 **경로의 수명**이다. 이름 기반 로드(`LOAD
    EXTENSION fts`)는 WAL 에 경로를 안 남기지만 `~/.lbdb` 캐시를 타므로 G0(오프라인
    즉시 동작)를 깬다 — 이 함정을 그것과 맞바꾸지 않는다.

    복사에 실패하면 **원본 경로를 그대로 돌려준다**: 그 편이 오늘 동작(취약하지만 로드는
    됨)이고, 여기서 None 을 주면 확장이 통째로 강등된다. 나쁜 쪽으로 가더라도 덜 나쁜 쪽."""
    import os
    import shutil
    try:
        dstdir = _stable_ext_dir()
        os.makedirs(dstdir, exist_ok=True)
        dst = os.path.join(dstdir, fname)
        # 이미 온전한 사본이 있으면 건너뛴다. 크기 비교는 앞선 복사가 중간에 끊긴
        # 경우(0바이트·잘린 파일)를 잡는다 — 버전 디렉터리가 내용은 이미 갈라 준다.
        if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
            # 임시로 떨군 뒤 자리로 옮긴다. 두 인스턴스가 동시에 기동해도 서로의
            # 반쯤 쓰인 파일을 로드하지 않는다(pid 로 이름을 가른다).
            tmp = f"{dst}.{os.getpid()}.part"
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        return dst
    except Exception:   # noqa: BLE001 — 읽기전용 홈·디스크 부족 등
        return src


#: `_volatile_wal_note` 를 이미 부른 WAL 경로. **프로세스당 한 번**만 스캔한다 —
#: `EmbeddedBackend.open` 은 기동 시 한 번이 아니다(`api/query.py` 의 조회 함수들이
#: store=None 이면 각자 연다). 매 요청마다 수 MB WAL 을 읽으면 진단이 비용이 된다.
_WAL_SCANNED: set[str] = set()

#: WAL 안의 휘발성 확장 경로. 앞의 `[^\x00-\x1f]` 는 길이 접두(널 바이트)에서 자연히
#: 멈춘다 — 그래서 경로만 깔끔히 떨어진다. 고바이트를 막지 않는 이유: 사용자 이름이
#: 한글이면 `%TEMP%` 경로가 UTF-8 멀티바이트다(ASCII 로 좁히면 그 기계에서만 못 잡는다).
_WAL_VOLATILE_EXT_RE = re.compile(
    rb"[^\x00-\x1f]{0,400}_MEI[^\x00-\x1f]{0,300}\.lbug_extension")


def _volatile_wal_note(data_dir: str) -> str:
    """WAL 이 **휘발성 `_MEI…` 확장 경로**를 물고 있으면 사람이 읽을 한 줄. 없으면 ""(빈).

    `_mirror_to_stable` 은 **앞으로 심기는 것**을 막을 뿐, 이미 심긴 것은 못 뺀다.
    2026-08-14 이전에 패키징본이 급사하며 남긴 WAL 은 여전히 `%TEMP%/_MEI<랜덤>` 을
    가리키고, 그 디렉터리가 사라지는 순간(임시폴더 청소·앱 업데이트·백신 격리) 또는
    그 프로세스가 아닌 무언가가 열려는 순간 스토어가 안 열린다. **그리고 영구적이다.**

    그러니 아직 열리는 동안 말해 주는 것이 요점이다 — 그때가 유일하게 값싼 시점이다.
    깨끗한 종료 한 번이면 체크포인트가 이 기록을 지우고 경고도 같이 사라진다. 이미
    깨진 뒤에 남는 복구는 기록된 경로를 되살리거나(비손실) `db.wal` 을 버리는 것뿐이고
    (`VINC_WAL_REPLAY_TOLERANT` 는 이 실패를 **덮지 못한다** — 실측), 후자는 체크포인트
    안 된 쓰기를 통째로 잃는다.

    진단이 기동을 막지 않는다: 무슨 일이 나도 ""(경고 없음)로 조용히 빠진다."""
    wal = data_dir + ".wal"
    try:
        if not os.path.exists(wal):
            return ""
        with open(wal, "rb") as fh:
            blob = fh.read()
    except Exception:   # noqa: BLE001 — 진단이 열기를 막지 않는다
        return ""
    seen: list[str] = []
    for m in _WAL_VOLATILE_EXT_RE.finditer(blob):
        p = m.group(0).decode("utf-8", "replace")
        if p not in seen:
            seen.append(p)
    if not seen:
        return ""
    # **존재 여부까지 본다.** "무장됐지만 아직 살아 있다"와 "이번 열기가 곧 깨진다"는
    # 처방이 다르다 — 전자는 정상 종료 한 번이면 끝나고, 후자는 손으로 되살려야 한다.
    # 처방은 **상태에 맞는 것 하나만** 준다. 둘 다 늘어놓으면 이미 깨진 사람에게
    # "정상 종료하세요"를 읽히게 되는데, 그건 할 수 없는 일이다.
    # (이 문구는 `/health` 의 open_error 로도 나가 화면에 닿을 수 있다 — em-dash 금지.)
    gone = [p for p in seen if not os.path.exists(p)]
    if gone:
        head = f"WAL 이 이미 사라진 확장 경로를 가리킨다(기록 {len(seen)}건 중 유실 {len(gone)}건)"
        fix = (f"복구: 그 경로에 확장 파일을 되살리거나(비손실), "
               f"'{wal}' 을 지운다(체크포인트 안 된 쓰기 유실).")
    else:
        head = f"WAL 이 휘발성 확장 경로를 물고 있다(기록 {len(seen)}건)"
        fix = "지금 정상 종료를 한 번 하면 체크포인트가 이 기록을 지운다."
    return (f"{head}: {(gone or seen)[0]} · 패키징본이 급사하며 남긴 "
            f"'_MEI<랜덤>' 경로다. {fix}")


def _bundled_ext_path(ext: str) -> str | None:
    """번들된 ladybug 확장(`lib<ext>.lbug_extension`) 경로 해석 — G0 오프라인 로드용.

    탐색 순서: ① PyInstaller frozen 추출 dir(`sys._MEIPASS/lbug_extensions/`)
    ② `VINC_LBUG_EXT_DIR` env(개발/테스트 오버라이드) ③ 사이드카 실행 파일 옆
    `lbug_extensions/`(onedir·수동 배치). 미발견 시 None → 이름 기반 폴백(dev 는
    설치본 사용). 경로는 Cypher 문자열 리터럴용으로 슬래시 정규화.

    ⚠ ①만 **안정 디렉터리로 복사한 뒤 그 경로**를 준다 — `_MEIPASS` 는 실행마다 바뀌는데
    이 경로가 WAL 에 남기 때문이다(사유는 `_mirror_to_stable`). ②③ 은 이미 수명이 안정한
    경로라 손대지 않는다."""
    import os
    import sys
    fname = f"lib{ext}.lbug_extension"
    cands: list[tuple[str, bool]] = []   # (디렉터리, 휘발성인가)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append((os.path.join(meipass, "lbug_extensions"), True))
    envdir = os.environ.get("VINC_LBUG_EXT_DIR")
    if envdir:
        cands.append((envdir, False))
    try:
        cands.append(
            (os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "lbug_extensions"), False))
    except Exception:
        pass
    for d, volatile in cands:
        p = os.path.join(d, fname)
        if os.path.exists(p):
            if volatile:
                p = _mirror_to_stable(p, fname)
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


#: 청크 검색 필터가 받는 키 — Chunk 표에 실제로 있는 열이다.
_CHUNK_FILTER_KEYS = ("owner_id", "layer", "doc_id")


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
    임베딩 자체가 없어 벡터 팔이 아예 안 돌던 것도 겹쳤다.

    받는 키는 Chunk 의 실제 열(:data:`_CHUNK_FILTER_KEYS`)뿐이고, 모르는 키는 던진다.
    조용히 버리면 `/search?doc=X` 가 같은 소유자의 다른 문서를 섞어 내고(`doc_id` 가
    그렇게 버려졌다), Chunk 에 없는 열을 받으면 엔진이 Binder 오류로 쿼리를 죽인다."""
    if not filters:
        return ""
    unknown = sorted(set(filters) - set(_CHUNK_FILTER_KEYS))
    if unknown:
        raise ValueError(f"unsupported chunk search filter: {', '.join(unknown)} "
                         f"(Chunk columns: {', '.join(_CHUNK_FILTER_KEYS)})")
    preds = []
    for key in _CHUNK_FILTER_KEYS:
        val = filters.get(key)
        if val is not None:
            preds.append(f"{var}.{key} = ${key}")
            params[key] = val
    return (f"{keyword} " + " AND ".join(preds) + " ") if preds else ""


# ── DDL 배타 가드 — 인덱스·스키마 DDL 은 혼자 돈다 ─────────────────────────────
# 쓰기는 잡 큐로 한 줄이지만 읽기는 쓰기와 동시에 돈다(R4 — 그게 P2 게이트다).
# 그런데 인덱스 DDL(벡터·FTS 생성/드롭, project_graph, 스키마 ALTER) 이 **다른
# 커넥션의 스캔과 겹치면** 엔진이 힙을 깨고 프로세스가 죽는다(파이썬 예외가 아니라
# SIGABRT — except 로 못 막는다). 그래서 DDL 은 배타, 모든 문장 실행은 공유로 잡는다.
# 공유는 `_exec_to_dicts` 한 곳에서 잡으므로 호출부는 이 가드를 모른다.
# 근거: [[record:hub-segfault-loop-index-ddl-races-readers-2026-09-25]]
#
# 규칙(깨면 교착이나 크래시다):
#   - 배타 대기 중이면 **새** 공유는 기다린다(라이터 우선 — DDL 이 굶지 않게).
#   - 같은 스레드의 중첩은 막지 않는다: 배타 보유 스레드의 읽기는 그냥 통과하고,
#     공유 보유 스레드의 재진입 공유는 대기 없이 센다.
#   - 공유를 쥔 스레드가 배타를 원하면 쥔 것을 **내려놓고** 배타를 잡은 뒤, 풀 때
#     되돌려 받는다(승격 교착 방지). 공유는 문장 단위라 실제로는 거의 안 일어난다.
#   - 대기는 유한하다. 넘으면 `DdlGuardTimeout`(TimeoutError) — 매달리지 않는다.
# ponytail: 프로세스 전역 락 하나. Database 가 둘 이상 열려도 서로 막는다(테스트만 해당).
EXCLUSIVE_WAIT_S = 30.0
SHARED_WAIT_S = 60.0


class DdlGuardTimeout(TimeoutError):
    """가드를 제한 시간 안에 못 잡았다. 삼키면 안 된다 — 특히 드롭 실패를 삼키면
    인덱스가 걸린 채 벡터를 넣게 되고, 그게 세그폴트 경로다."""


class _DdlGuard:
    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0          # 공유 보유 수(재진입 포함)
        self._owner = None         # 배타 보유 스레드 id
        self._depth = 0            # 배타 재진입 깊이
        self._waiting = 0          # 배타 대기 수(라이터 우선)
        self._local = threading.local()

    def _mine(self) -> int:
        return getattr(self._local, "n", 0)

    def _retake(self, n: int) -> None:
        # 내려놓았던 공유를 되돌려 받는다. 배타 보유자가 없을 때만 셀 수 있다.
        # _cond 를 쥔 상태에서 부른다.
        if n:
            self._cond.wait_for(lambda: self._owner is None)
            self._readers += n
            self._local.n = n

    @contextmanager
    def shared(self, timeout: float | None = None):
        me = threading.get_ident()
        if self._owner == me:      # 배타 보유 중의 읽기 — 자기 자신과는 경합하지 않는다
            yield
            return
        with self._cond:
            if not self._mine() and not self._cond.wait_for(
                    lambda: self._owner is None and not self._waiting,
                    SHARED_WAIT_S if timeout is None else timeout):
                raise DdlGuardTimeout("store busy: index/schema DDL held the store "
                                      f"longer than {SHARED_WAIT_S if timeout is None else timeout}s")
            self._readers += 1
            self._local.n = self._mine() + 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._local.n -= 1
                if not self._readers:
                    self._cond.notify_all()

    @contextmanager
    def exclusive(self, timeout: float | None = None):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                held = None
            else:
                held = self._mine()
                self._readers -= held
                self._local.n = 0
                self._waiting += 1
                try:
                    ok = self._cond.wait_for(
                        lambda: self._owner is None and not self._readers,
                        EXCLUSIVE_WAIT_S if timeout is None else timeout)
                finally:
                    self._waiting -= 1
                if not ok:
                    self._cond.notify_all()
                    self._retake(held)
                    raise DdlGuardTimeout(
                        "index/schema DDL could not get the store alone within "
                        f"{EXCLUSIVE_WAIT_S if timeout is None else timeout}s "
                        f"({self._readers} statements still reading)")
                self._owner, self._depth = me, 1
        try:
            yield
        finally:
            with self._cond:
                self._depth -= 1
                if not self._depth:
                    self._owner = None
                    self._retake(held or 0)
                    self._cond.notify_all()


#: 프로세스에 하나. 테스트가 직접 쓴다.
DDL_GUARD = _DdlGuard()


#: 임베딩 적용이 청크 벡터 색인을 **드롭하지 않아도 되는** 엔진 판(VIN-236). 이 판의 HNSW
#: 증분 삽입은 혼자서는 살지만, 삽입이 남긴 색인을 다른 커넥션이 읽는 동안 자동 체크포인트가
#: 돌면 세그폴트한다. 그래서 드롭을 빼는 대신 벡터 질의를 문장마다 배타로 돌린다.
#: 측정하지 않은 판에서는 플래그가 켜져 있어도 드롭으로 물러난다.
#: 근거: [[record:ladybug-0-20-4-hnsw-insert-survives-alone-but-a-reader-during-a-checkpoint-segfaults-2026-09-30]] ·
#: [[record:keep-index-mode-survived-104-hub-condition-trials-while-the-unguarded-control-crashed-3-of-20-2026-10-03]]
_KEEP_VEC_INDEX_ENGINES = ("0.20.4",)


@functools.lru_cache(maxsize=None)
def _keep_vec_engine_ok(ver: str) -> bool:
    ok = ver in _KEEP_VEC_INDEX_ENGINES
    if not ok:
        print(f"vinc: ⚠ VINC_KEEP_VECTOR_INDEX 가 켜졌지만 엔진 {ver or '?'} 은 측정한 판"
              f"({', '.join(_KEEP_VEC_INDEX_ENGINES)})이 아니다 — 쓰기마다 벡터 색인을 드롭한다",
              file=sys.stderr, flush=True)
    return ok


def keep_vector_index() -> bool:
    """`VINC_KEEP_VECTOR_INDEX=on` 이고 엔진이 측정한 판일 때만 참(기본 거짓).

    참이면 두 자리가 함께 바뀐다: 임베딩 적용이 색인을 드롭하지 않고
    (`api.docstore._apply_embeddings`), 벡터 질의가 배타 가드 안에서 돈다
    (:meth:`EmbeddedBackend.vector_search` · :meth:`EmbeddedBackend.node_vector_search`).
    하나만 바꾸면 세그폴트 자리로 돌아간다."""
    if os.environ.get("VINC_KEEP_VECTOR_INDEX", "").strip().lower() not in ("1", "true", "on"):
        return False
    try:
        ver = str(getattr(__import__("ladybug"), "__version__", "") or "")
    except Exception:          # noqa: BLE001 — 드라이버가 없으면 색인도 없다
        return False
    return _keep_vec_engine_ok(ver)


# 노드 미러가 이 행 수 밑이면 HNSW 를 건너뛰고 정확 코사인으로 훑는다(:meth:`EmbeddedBackend.node_vector_search`).
# ponytail: 정확 스캔은 행 수에 선형이다. 대략 10만 행을 넘으면 HNSW 가 다시 이기므로 그 전에 이 값을 다시 잰다.
# 근거: [[vinc.plan.vin397-prefetch-half-latency-2026-10-07]]
_NODE_VEC_EXACT_MAX_ROWS = 50_000


def _vector_query_guard():
    """벡터 질의 한 문장을 감싼다 — 색인을 유지하는 동안만 배타다(:func:`keep_vector_index`)."""
    return (request_timing.wait("ddl_exclusive_wait", DDL_GUARD.exclusive())
            if keep_vector_index() else nullcontext())


def _exclusive_ddl(fn):
    """이 메서드는 DDL 을 돈다 — 도는 동안 다른 스레드의 문장 실행을 막는다."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with DDL_GUARD.exclusive():
            return fn(*a, **kw)
    return wrapper


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

    def __init__(self, conn, guard=None):
        self._conn = conn
        self._guard = guard      # 잔해 표 쓰기 거절(`EmbeddedBackend._refuse_remnant_write`)

    def run(self, cypher: str, **params):
        if self._guard is not None:
            self._guard(cypher)
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
        self._request_guard = None
        self._scheduled_index = None

    def _enter_request_guard(self):
        guard = request_timing.wait("ddl_shared_wait", DDL_GUARD.shared(timeout=1.0))
        guard.__enter__()
        self._request_guard = guard

    def _leave_request_guard(self):
        guard, self._request_guard = self._request_guard, None
        if guard is not None:
            guard.__exit__(None, None, None)

    @contextmanager
    def request_read_scope(self):
        """요청의 후속 읽기를 DDL에서 보호한다. 유지보수 대기는 1초 뒤 재시도한다."""
        self._enter_request_guard()
        try:
            yield
        finally:
            self._leave_request_guard()

    @contextmanager
    def graph_snapshot_scope(self, control):
        """One owned reader: shared snapshot, cooperative cancellation/query deadline.

        Never interrupts the writer or another reader. Each native query receives the remaining timeout;
        Python shaping/layout checks cancellation at phase ends.
        Shutdown still joins the producer before closing its database.
        """
        self._graph_control = control
        begun = False
        try:
            self.run("BEGIN TRANSACTION READ ONLY")
            begun = True
            yield
            control.check()
        finally:
            self._graph_control = None
            self._conn.set_query_timeout(0)
            if begun:
                self.run("ROLLBACK")

    def _at_write_point(self, label: str, fn, timeout: float):
        if self._writer is None:
            return fn()
        # 동기 DDL은 다른 스레드가 실행한다. 공유를 쥔 채 기다리면 서로 잠긴다.
        held = self._request_guard is not None
        if held:
            self._leave_request_guard()
        try:
            return self._writer(label, fn, timeout)
        finally:
            if held:
                self._enter_request_guard()

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        control = getattr(self, "_graph_control", None)
        if control is not None:
            control.check()
            self._conn.set_query_timeout(max(1, int(control.remaining() * 1000)))
        rows = _exec_to_dicts(self._conn, query, params or None)
        if control is not None:
            control.check()
        return rows

    def fulltext_search(self, text, top_k, filters=None) -> list[dict]:
        return self._b.fulltext_search(text, top_k, filters, conn=self._conn)

    def vector_search(self, embedding, top_k, filters=None) -> list[dict]:
        return self._b.vector_search(embedding, top_k, filters, conn=self._conn)

    def node_vector_search(self, embedding, top_k, *, owner=None,
                           use_index: bool = False) -> list[dict]:
        """노드 미러(NodeVec) 의미 검색 — **이 뷰가 노출해야 조회 경로에서 산다.**

        ⚠ 2026-09-10 에 이 자리에서 세 번째로 물렸다(앞의 둘은 아래 `graph_algo` 주석에
        있다). 미러도 팔도 다 있었는데 이 줄이 없어서, 사이드카를 지나는 **모든** 검색이
        `node_error: '_ReaderView' object has no attribute 'node_vector_search'` 로
        의미 팔을 잃었다. 팔이 기본으로 꺼져 있던 동안에는 아무도 못 봤고, 켠 첫날
        라이브 질의 한 번에 드러났다.

        `graph_algo`·`ensure_search_indexes` 와 달리 **쓰기 지점으로 보내지 않는다** —
        이건 순수 조회이고 DDL 을 안 돌린다. 인덱스가 없으면 백엔드가 빈 목록을 준다."""
        return self._b.node_vector_search(embedding, top_k, owner=owner,
                                          conn=self._conn, use_index=use_index)

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
        보내고, 없으면(CLI·테스트) writer 커넥션으로 직접 만든다.

        FTS 잔해가 확인된 스토어에서는 잡을 걸지 않고 곧바로 던진다 — 빌드는 FTS 에서
        먼저 서므로 검색마다 실패할 잡을 거는 셈이다(벡터 잔해 때 허브가 겪은 스냅샷 폭주)."""
        if getattr(self._b, "fts_index_remnant", False):
            raise FtsIndexRemnant(EmbeddedBackend._remnant_note("Chunk"), table="Chunk")
        return self._at_write_point(
            "search.index", lambda: self._b.ensure_search_indexes(dim=dim) or {}, 60.0)

    def schedule_search_indexes(self, *, dim: int | None = None) -> None:
        """같은 빌드를 **기다리지 않고** 예약한다(:func:`api.query._schedule_vector_index`).

        벡터(HNSW) 인덱스는 문서를 쓸 때마다 드롭되므로(`drop_vector_index` — 세그폴트
        방어) 쓰기 직후의 검색이 반드시 재빌드를 문다. 위 `ensure_search_indexes` 는
        상한 60초로 동기 대기라, 앞에 적재 잡이 있으면 **조회가 그만큼 매달렸다.**
        검색은 그동안 FTS 로 답할 수 있다 — 기다릴 이유가 없다.

        타임아웃 0 = 예약(잡만 걸고 즉시 반환)이라는 약속을 쓰기 지점이 안다
        (`api.server.VincApp._at_write_point`). 통로가 없으면(CLI·테스트) 기다릴
        요청 스레드도 없으므로 그 자리에서 만든다.

        잔해가 확인된 스토어에서는 **예약하지 않는다.** 실패할 빌드를 검색마다 잡으로 걸면
        잡마다 스냅샷이 떠서, 허브는 812MB 복사를 85초마다 되풀이하며 CPU 한도에
        붙었다(2026-09-25). 수리는 기동이 한다(:meth:`EmbeddedBackend.heal_vector_index`)."""
        if getattr(self._b, "vector_index_remnant", False) or \
                getattr(self._b, "fts_index_remnant", False):
            return          # FTS 잔해도 같다 — 빌드가 FTS 에서 먼저 선다
        build = lambda: self._b.ensure_search_indexes(dim=dim) or {}
        if self._writer is None:
            build()
        else:
            # 제출 즉시 시작한 DDL이 같은 검색의 후속 읽기를 막지 않게 요청 종료로 미룬다.
            self._scheduled_index = build

    @property
    def vector_index_remnant(self) -> bool:
        return getattr(self._b, "vector_index_remnant", False)

    @property
    def fts_index_remnant(self) -> bool:
        return getattr(self._b, "fts_index_remnant", False)

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
        finally:
            self._leave_request_guard()
        build, self._scheduled_index = self._scheduled_index, None
        if build is not None:
            self._writer("search.index", build, 0.0)


def _exec_to_dicts(conn, cypher: str, params: dict | None, *, raw: bool = False) -> list[dict]:
    """드라이버 QueryResult → dict 레코드 리스트(컬럼명 키). 다중 문장 세미콜론 분리.

    ``raw=True`` 는 아래 PK 분해 훅을 **건너뛴다** — 물리 키(owner U+001F id)가 그대로
    나온다. 소비자는 하나다: 재구축의 export(`store/migrate.py`). 읽기 경로가 벗긴 논리
    id 를 새 스토어에 그대로 쓰면 정확 id 조회가 전부 죽고 같은 논리 id 를 가진 소유자
    둘이 한 행으로 접힌다 — 2026-09-11 라이브에서 그렇게 됐다."""
    out: list[dict] = []
    # 모든 문장 실행이 여기를 지난다 — 그래서 DDL 가드의 공유 쪽은 여기 하나다.
    # 결과 소비까지 쥔다(행을 읽는 동안에도 스캔은 살아 있다). 가드 설명은 `_DdlGuard` 위.
    with request_timing.wait("ddl_shared_wait", DDL_GUARD.shared()):
        res = conn.execute(cypher, params) if params else conn.execute(cypher)
        # ladybug/kuzu 는 다중 문장 시 결과 리스트를 줄 수 있음 — 마지막 결과만 소비.
        results = res if isinstance(res, list) else [res]
        if request_timing.active():
            # 엔진 통계도 CPU 시간이 아니다. 드라이버가 통계를 못 주어도 결과는 지킨다.
            for r in results:
                for phase, getter in (("db_compile", "get_compiling_time"),
                                      ("db_native_exec", "get_execution_time")):
                    try:
                        request_timing.observe(phase, getattr(r, getter)())
                    except Exception:
                        pass
        with request_timing.measure("db_fetch"):
            for r in results:
                try:
                    cols = r.get_column_names()
                except Exception:
                    cols = None
                while r.has_next():
                    row = r.get_next()
                    out.append(dict(zip(cols, row)) if cols else {"_": row})
    # PK 네임스페이싱(design A)의 **유일한** 분해 지점. 프로젝션 64개·별칭 10개를
    # 하나하나 고치지 않는 이유가 이것이다 — `run()`·`_Session.run`·reader 커넥션·
    # 전문/벡터 검색이 전부 여기를 지난다. 판별자는 U+001F 존재이고, 좁히기는
    # `pkns.looks_physical` 이 진다(문서 본문 보호). 근거: [[vinc.plan.node-pk-namespacing]] §7
    #
    # 플래그가 꺼져 있으면 리스트를 손대지 않는다 — 로컬/데스크탑은 영구히 그쪽이고,
    # 이 함수는 모든 읽기의 뜨거운 길이라 행마다 재귀를 도는 비용을 물릴 이유가 없다.
    #
    # ⚠ 이 훅은 **돌려주는 값**만 고쳐 쓴다 — Cypher **술어**는 물리 열을 그대로 본다.
    # 그래서 `WHERE pk CONTAINS $sep` 로 이관 진척을 세는 것은 여기서 안 막힌다(PR-D 는
    # 훅 우회 읽기 경로도 행별 표식도 필요 없다). 실측:
    # tests/core/test_pk_compose_write.py::test_a_cypher_predicate_still_sees_the_separator
    if pkns.enabled() and not raw:
        with request_timing.measure("db_decode"):
            out = [pkns.strip_deep(row) for row in out]
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
        #: 이 프로세스에서 **다시 시도해도 안 풀리는** 로드 실패(확장 → 사유). 지금은 algo 의
        #: 상류 의존 누락 하나뿐이다(`_ALGO_UNAVAILABLE_NOTE`). 일시적 실패(쓰기 트랜잭션 충돌
        #: 등)는 여기 넣지 않는다 — 넣으면 다음 시도가 성공할 수 있는 것을 막는다.
        self._ext_failed: dict[str, str] = {}
        #: 벡터 인덱스가 **지금 드롭된 상태인가**(이 프로세스가 아는 한).
        #: 임베딩 워커는 한 라운드에 문서 16개를 각각 `apply_embeddings` 로 넣고, 그
        #: 하나하나가 드롭을 부른다. DDL 이 라운드당 16번 나가고 그 사이 검색이 오면
        #: 매번 재빌드를 문다(위험 감사 6.6 — "벡터 팔이 사실상 상시 꺼져 있을 수
        #: 있다"). 드롭은 멱등이지만 **공짜가 아니다**. 이 플래그로 라운드 첫 번째만
        #: 실제 DDL 을 내보내고 나머지는 접는다. 만든 쪽이 다시 False 로 내린다.
        self._vec_index_dropped = False
        #: 잔해 색인이 있어 색인을 **만들 수 없다고 확인된** 표(`_REMNANT_INDEXES` 의 키).
        #: 들어 있으면 재빌드 예약을 멈추고(실패하는 빌드를 검색마다 다시 돌리지 않게) 그
        #: 표에 쓰는 문장을 거절한다(쓰기는 세그폴트다 — :meth:`run`). 기동 때 수리가 뺀다.
        self._remnants: set[str] = set()
        #: 이 Database 에서 연 커넥션들(:class:`_Conn`) — DDL 이 지나가면 전부의 문장 캐시를
        #: 비운다. `open` 이 채운다(직접 생성한 테스트 더블은 빈 집합).
        self._conns: "weakref.WeakSet" = weakref.WeakSet()
        #: 소유자별 저장 벡터 도장 `{owner: (벡터 쓰기 세대, frozenset)}`. 채우고 읽는 것은
        #: `api.query._stored_runtimes` 이고, 요청마다 새로 뜨는 리더 뷰도 이 백엔드의 것을 쓴다.
        self.runtime_stamps: dict = {}
        #: 전문 색인이 `Chunk.lex` 위에 섰는가(`LEX_INDEX_KEY` 표지). None = 아직 안 읽었다.
        #: 참이 되면 내려가지 않는다(표지를 지우는 경로가 없다). 리더 뷰도 이 값을 쓴다.
        self._lex_ready: bool | None = None
        #: WAL 재생 뒤 FTS 재구축이 끝나기 전에는 낡은 색인을 검색하지 않는다.
        self._fts_recovery_path: str | None = None

    @classmethod
    def open(cls, cfg: StoreConfig) -> "EmbeddedBackend | None":
        data_dir = cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        drv, name = _import_driver()
        if drv is None:
            return None
        os.makedirs(os.path.dirname(data_dir) or ".", exist_ok=True)
        # 기동 점검 — WAL 에 남은 휘발성 `_MEI…` 확장 경로(사유는 `_volatile_wal_note`).
        # **여는 것보다 먼저** 본다: 열기가 그것 때문에 실패하면 아래 except 가 이 문구를
        # 사유에 덧대야 하는데, 실패한 뒤에 읽으면 드라이버가 파일을 물고 있을 수 있다.
        wal_note = ""
        if data_dir not in _WAL_SCANNED:
            _WAL_SCANNED.add(data_dir)
            wal_note = _volatile_wal_note(data_dir)
        kwargs = _driver_kwargs(cfg)
        try:
            recovery = data_dir + ".fts-replay-pending"
            try:
                replay = os.path.getsize(data_dir + ".wal") > 0
            except FileNotFoundError:
                replay = False
            if replay:
                # 열기가 WAL을 체크포인트해도 미완료 수리 표식은 다음 기동까지 남는다.
                # 근거: [[vinc.ops.index-remnant-heal]]
                with open(recovery, "a", encoding="utf-8") as marker:
                    marker.flush()
                    os.fsync(marker.fileno())
                if os.name != "nt":
                    fd = os.open(os.path.dirname(os.path.abspath(recovery)), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            db = drv.Database(data_dir, **kwargs)
            family = weakref.WeakSet()
            conn = _open_connection(drv, db, family)
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
            # 이 열기가 **왜** 깨졌는지 아는 경우에는 처방까지 붙인다. 원문("Failed to
            # load library: …/_MEI…")만으로는 번들이 빠진 것처럼 읽혀 사람을 패키징
            # 쪽으로 보낸다 — 실제로 필요한 것은 그 경로를 되살리거나 WAL 을 버리는
            # 것이다. `last_open_error` 에 넣으므로 `/health` 의 open_error 로도 나간다.
            if wal_note:
                cls.last_open_error += f" | {wal_note}"
            # stderr 에 남긴다 — 이전엔 이 자리에 없어서 2026-08-03 장애 때 실제
            # 예외 문구를 어디서도 못 봤다(배너의 "driver": null 한 줄뿐).
            if emit_open_failure.get():
                print(f"[embedded_backend] store open failed: {cls.last_open_error}",
                      file=sys.stderr, flush=True)
            return None
        cls.last_open_error = ""
        # 열리긴 했다 — 그래도 무장돼 있으면 지금 말한다. **지금이 유일하게 값싼 시점**
        # 이다(정상 종료 한 번이면 체크포인트가 지운다). 깨진 뒤에 말하면 이미 늦다.
        if wal_note:
            print(f"[embedded_backend] ⚠ {wal_note}", file=sys.stderr, flush=True)
        backend = cls(db, conn, name, cfg)
        backend._conns = family
        backend._fts_recovery_path = recovery if os.path.exists(recovery) else None
        return backend

    @contextmanager
    def session(self):
        session = _Session(self._conn, guard=self._refuse_remnant_write)
        try:
            yield session
        finally:
            session.close()

    def run(self, query: str, params: dict | None = None, *, conn=None,
            raw: bool = False) -> list[dict]:
        # ``raw``: 물리 키 그대로(PK 분해 훅 생략). 재구축 export 전용 — `_exec_to_dicts` 주석.
        self._refuse_remnant_write(query)
        return _exec_to_dicts(conn or self._conn, query, params or None, raw=raw)

    #: 행을 바꾸는 절. 잔해 표를 이름으로 부르는 문장에 이것이 있으면 거절한다.
    _WRITE_CLAUSE = re.compile(r"\b(?:CREATE|MERGE|SET|DELETE|REMOVE|COPY)\b", re.IGNORECASE)

    def _refuse_remnant_write(self, query: str) -> None:
        """잔해가 확인된 표에 쓰는 문장이면 **엔진에 보내기 전에** 던진다.

        그 표의 쓰기는 예외가 아니라 세그폴트라 `except` 로 못 막고, 허브에서는 사이드카
        사망이 곧 컨테이너 사망이다. 쓰기 경로를 하나하나 막는 것으로는 모자랐다 — #407 은
        임베딩 적용만 막았는데 문서 저장·삭제도 `ChunkVec` 행을 지운다(실측: 잔해 위 삭제·
        아무 열의 SET·벡터 없는 삽입 전부 세그폴트). 그래서 모든 문장이 지나는 여기서 본다.

        판정은 넓다: 그 표를 라벨로 부르고(``:Chunk``) 쓰기 절이 하나라도 있으면 거절한다.
        그 표를 **지나기만** 하는 관계 쓰기(`(c:Chunk)-[:MENTIONS]->…` 의 MERGE·DELETE)도
        함께 막힌다 — 관계 쓰기는 무사하다는 실측이 있지만 문장을 파싱하지 않고는 가를 수
        없고, 막혀서 잃는 것은 수리 전까지의 링크 갱신뿐이다. 잔해가 없으면 비용이 없다.
        근거: [[record:a-remnant-segfaults-every-write-to-its-table-not-only-inserts-2026-09-25]]"""
        if not self._remnants:
            return
        for table in self._remnants:
            # 엔진 라벨은 대소문자를 가리지 않는다(`MATCH (x:chunk)` 도 Chunk 를 센다, 실측).
            if re.search(rf"(?::\s*|\bCOPY\s+){table}\b", query, re.IGNORECASE) \
                    and self._WRITE_CLAUSE.search(query):
                raise self._remnant_error(table)

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
        return _ReaderView(self, _open_connection(drv, self._db, self._conns), writer=writer)

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

    @_exclusive_ddl
    def migrate(self, *, dim: int) -> dict:
        """스키마 제자리 마이그레이션(S0). 신규 설치·구 설치 모두 최종형으로 수렴.

        1) base DDL(멱등) — 신규 설치는 추적열 포함 테이블 + 메타 테이블 생성; 구 설치는
           기존 테이블 skip + 신규 메타 테이블만 생성.
        2) 버전 게이트 — _SchemaMeta 의 기록 버전 이후 ALTER 스텝 적용(구 설치의 기존
           테이블에 추적열 ADD+DEFAULT 백필; 신규 설치는 이미 있어 no-op 흡수).
        3) 버전 기록. **ALTER ADD 는 additive → 롤백 시 구코드가 새 열을 단순 무시.**"""
        # 0) **base DDL 보다 먼저** 도는 유일한 스텝(v18). NodeVec 의 기본키가 바뀌는데
        #    기본키는 ALTER 로 못 고친다. 미러는 배포마다 통째로 다시 만드는 파생물이라
        #    버리는 것이 옳은 이행이고(잃을 원본이 없다), 버린 자리는 바로 아래 base DDL
        #    이 새 모양으로 다시 만든다. 뒤에 두면 방금 만든 것을 도로 지운다.
        #    근거: [[record:the-node-mirror-key-must-hold-one-id-per-space-2026-09-10]]
        rekeying = 0 < self._schema_version() < NODE_VEC_REKEY_VERSION
        if rekeying:
            try:
                self.drop_node_vector_index()
            except Exception:
                pass        # 여기선 행을 넣지 않는다. 드롭 실패는 아래 재키잉 확인이 잡는다
            try:
                self._conn.execute("DROP TABLE NodeVec")
            except Exception:
                pass        # 없으면 없는 대로 — base DDL 이 만든다
        for stmt in render_embedded_ddl(dim=dim):
            self._exec_idem(stmt)
        # 🔴 **버렸다고 믿지 말고 확인한다.** 드롭이 실패하면(인덱스가 안 떨어졌다거나
        #    확장이 안 올라왔다거나) 그 예외는 위에서 삼켜지고, base DDL 은 "이미 있다"를
        #    또 삼킨다. 그러면 옛 모양 그대로인 테이블에 새 버전만 기록되어 **다시는
        #    고칠 기회가 없는 반쪽 상태**가 남는다 — 재구축은 계속 죽고 이유는 안 보인다.
        #    확인이 실패하면 버전을 올리지 않는다: 다음 기동이 그대로 다시 시도한다.
        if rekeying and not self._node_vec_rekeyed():
            print("vinc-store: ⚠ NodeVec 재키잉 실패 — 옛 기본키가 남아 있다. 스키마"
                  " 버전을 올리지 않는다(다음 기동이 다시 시도한다). 의미 팔은 그동안"
                  " 빈 목록을 낸다.", file=sys.stderr, flush=True)
            rekeying = False
            target = self._schema_version()      # 올리지 않는다
        else:
            target = SCHEMA_VERSION
        cur = self._schema_version()
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
        lex_fresh = False
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
        # v21 — **청크가 하나도 없는 스토어는 처음부터 `lex` 위에 선다.** 옮길 행이 없으니
        # 데이터 단계가 할 일은 색인을 다시 만드는 것뿐이고, 행이 0 이면 그건 즉시 끝난다.
        # 새 설치가 첫날부터 이관된 스토어와 같은 검색을 하게 하는 자리다.
        # 표지는 **여기서 먼저 읽어 둔다**(기동 = 단일 스레드). 비워 두면 리더 스레드의 첫
        # 검색이 쓰기 커넥션으로 표지를 읽는다.
        self._lex_ready = None
        if not self.lex_ready() and "fts" in self._exts:
            try:
                empty = int(self.run("MATCH (c:Chunk) RETURN count(c) AS c")[0]["c"]) == 0
            except Exception:
                empty = False
            if empty:
                self._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')",
                                 "Chunk FTS 색인")
                self._set_lex_ready()
                lex_fresh = True
        return {"migrated_from": cur, "migrated_to": target,
                "episode_time_backfilled": backfilled,
                "domain_links_backfilled": domain_links,
                "lex_index_fresh": lex_fresh}

    # ── v21 한국어 색인 (Chunk.lex) ──────────────────────────────────────────
    def lex_ready(self) -> bool:
        """전문 색인이 `Chunk.lex` 위에 섰는가 — `_SchemaMeta` 표지를 한 번 읽어 둔다."""
        if self._lex_ready is None:
            try:
                rows = self.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                                {"k": LEX_INDEX_KEY})
                self._lex_ready = bool(rows) and rows[0].get("v") == "ready"
            except Exception:
                return False                  # 읽지 못했으면 오늘처럼 `text` 로 간다
        return bool(self._lex_ready)

    def _set_lex_ready(self) -> None:
        self.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = 'ready'", {"k": LEX_INDEX_KEY})
        self._lex_ready = True

    def _fts_columns(self) -> list[str]:
        """FTS 색인이 걸리는 열 — **코드가 아니라 스토어가 정한다**(표지). 색인을 만드는
        모든 자리(재빌드·빌드·지연 보장·잔해 수리)가 이것 하나를 읽는다."""
        return ["lex"] if self.lex_ready() else ["text"]

    @_exclusive_ddl
    def migrate_lex_index(self, *, batch: int = 200) -> dict:
        """전문 색인을 `Chunk.lex` 로 옮긴다(데이터 단계, 멱등). 표지가 서 있으면 아무것도 안 한다.

        순서: ① FTS 색인**만** 내린다(HNSW 는 `ChunkVec` 위라 그대로 둔다) ② `lex` 가 빈
        행을 배치로 채운다 ③ `lex` 위에 FTS 를 만든다 ④ 표지. ①과 ② 사이가 중요하다 —
        FTS 가 걸린 채 기존 Chunk 행에 SET 하면 프로세스가 예외 없이 죽는다(2026-09-08 함정).
        ④ 전에 죽으면 표지가 없어 질의는 `text` 로 가고(색인이 없으면 지연 보장이 `text` 로
        다시 만든다) 다음 실행이 처음부터 다시 한다.

        ③ 이 비싸다: 두 글자 묶음 색인은 같은 행에서 `text` 색인의 7배쯤 걸린다(1,712청크
        81초 · 12초). 그 동안 이 메서드가 가드를 쥐어 **프로세스의 다른 문장이 전부 기다린다**
        — 그래서 기동 경로에서 부르지 않는다(`VINC_LEX_BACKFILL`).
        근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §2.6]]"""
        if self.lex_ready():
            return {"migrated": False, "reason": "already ready"}
        from .lexical import lex_text
        t0 = time.perf_counter()
        self._load_ext("fts")
        self._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')", "Chunk FTS 색인")
        # raw — 읽은 키를 그대로 MATCH 에 되돌린다. PK 분해 훅을 거치면 읽은 값과 저장된 값이
        # 갈라질 수 있다(2026-09-11 재구축이 키를 벗긴 자리). 청크 id 는 지금 물리 키가 아니지만
        # 이 경로가 그 가정에 기대지 않게 한다.
        rows = self.run("MATCH (c:Chunk) WHERE coalesce(c.lex, '') = '' "
                        "RETURN c.chunk_id AS id, c.text AS text", raw=True) or []
        for i in range(0, len(rows), max(1, int(batch))):
            part = [{"id": r["id"], "lex": lex_text(r.get("text") or "")}
                    for r in rows[i:i + batch]]
            self.run("UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.id}) SET c.lex = r.lex",
                     {"rows": part})
        t1 = time.perf_counter()
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',['lex'])")
        t2 = time.perf_counter()
        self._set_lex_ready()
        return {"migrated": True, "backfilled": len(rows),
                "backfill_s": round(t1 - t0, 2), "index_s": round(t2 - t1, 2)}

    def _node_vec_rekeyed(self) -> bool:
        """미러가 실제로 새 기본키를 갖는가(v18). 열이 없으면 엔진이 바인딩에서 던진다 —
        빈 테이블이어도 던지므로 행 수와 무관하게 판정된다."""
        try:
            self.run("MATCH (n:NodeVec) RETURN n.vec_id AS v LIMIT 1")
            return True
        except Exception:
            return False

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
    #: v17 — 노드 미러의 HNSW. Chunk 쪽과 **다른 인덱스**다: 라이프사이클이 다르고
    #: (노드 미러는 문서 쓰기마다 흔들리지 않는다) 드롭 범위를 섞으면 문서 한 건 쓰기가
    #: 노드 색인까지 무너뜨린다.
    _NODE_VEC_INDEX = "node_vec"
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
        if ext in self._ext_failed:
            raise RuntimeError(self._ext_failed[ext])
        if reader:
            # 읽기 경로 — 여기서 LOAD 하면 쓰기 트랜잭션이 잡 큐를 우회한다(R4 위반).
            raise RuntimeError(
                f"임베디드 {ext} 확장 미가용({self.driver_name}) — 오프라인 배포 시 "
                f"번들 필요(§9·G0). 기동 시 로드되지 않았다(재시작 후 재확인).")
        with DDL_GUARD.exclusive():     # LOAD 도 쓰기 트랜잭션이다 — DDL 과 같이 혼자 돈다
            self._load_ext_now(ext)

    def _load_ext_now(self, ext: str) -> None:
        if ext in self._exts:           # 배타를 기다리는 사이 다른 스레드가 로드했다
            return
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
            if ext == "algo" and "failed to load library" in detail.lower():
                # 상류 결함이라 이 프로세스 안에서는 풀리지 않는다 — 실패를 기억해 두고
                # 호출마다 LOAD(쓰기 트랜잭션)를 다시 내지 않는다(:meth:`_load_ext`).
                # 원인은 라이브러리 로드 쪽 문구만 싣는다. 이름 기반 폴백의 "Binder
                # exception: ... has not been installed" 는 번들이 있는 한 헛갈림이고,
                # 호출부는 "Binder" 를 투영 결함의 표지로 읽는다(test_decision_type).
                cause = bundled_err if "failed to load library" in bundled_err.lower() else text
                msg = (f"임베디드 algo 확장 미가용({self.driver_name}) — {_ALGO_UNAVAILABLE_NOTE}"
                       f" 원인: {cause}")
                self._ext_failed[ext] = msg
                raise RuntimeError(msg) from exc
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

    #: 드롭할 색인이 **없을 때** 엔진이 내는 문구(벡터·FTS 공통, 0.18.0 실행·0.20.4 확장 바이너리):
    #:   "Binder exception: Table ChunkVec doesn't have an index with name chunk_vec."
    _INDEX_ABSENT_MARK = "doesn't have an index with name"

    def _drop_index(self, stmt: str, what: str) -> None:
        """색인 드롭 DDL 하나(멱등). 흡수하는 것은 '색인 없음' **하나뿐**이다.

        그 밖의 실패(다른 커넥션이 쓰기 트랜잭션을 쥔 경우 등)는 색인이 **아직 걸려 있다**는
        뜻이라 던진다. 삼키면 부르는 쪽이 드롭된 줄 알고 벡터를 넣고, 그게 세그폴트 자리다.
        근거: [[vinc.audit.rag-code-review-2026-09-29]] (RV-34)"""
        try:
            self._conn.execute(stmt)
        except Exception as exc:
            if self._INDEX_ABSENT_MARK not in _driver_error_text(exc):
                raise
        else:
            self._checkpoint_after_drop(what)

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
        if self._vec_index_dropped:     # 접힌 드롭은 DDL 이 아니다 — 가드도 안 잡는다
            return
        with DDL_GUARD.exclusive():     # 시간 초과는 삼키지 않는다(`DdlGuardTimeout`)
            self._load_ext("vector")
            self._drop_index(f"CALL DROP_VECTOR_INDEX('ChunkVec','{self._VEC_INDEX}')",
                             "ChunkVec 벡터 색인")
            self._vec_index_dropped = True      # 드롭이 던지면 접지 않는다 — 다음 쓰기가 다시 드롭

    def _checkpoint_after_drop(self, what: str) -> None:
        """확장 색인(벡터·FTS)을 드롭한 **바로 뒤에** 체크포인트한다.

        🔴 드롭이 WAL 에만 있는 채로 프로세스가 죽으면, 재기동 때 WAL 이 **확장을 올리기
        전에** 재생되어 카탈로그 항목만 지워지고 저장층에는 로드되지 않은 같은 이름의
        색인이 남는다. 그 뒤로 같은 이름의 생성은 `... is not loaded yet` 로 실패하고,
        그 표에 행을 넣으면 **세그폴트**다. 벡터·FTS 둘 다 그렇다. 생성이 WAL 에 남는
        것은 무해하다. 실패는 삼키고 알린다 — 드롭은 이미 됐고, 막아야 하는 것은 이
        프로세스가 아니라 다음 기동이다.
        근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]"""
        try:
            self._conn.execute("CHECKPOINT")
        except Exception as exc:     # noqa: BLE001
            print(f"vinc: ⚠ {what} 드롭 뒤 체크포인트 실패 — 다음 체크포인트 전에 죽으면 "
                  f"잔해가 남는다: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    @_exclusive_ddl
    def drop_search_indexes(self) -> None:
        """벡터+FTS 인덱스 **둘 다** 제거(멱등). 전량 재적재·재빌드 구간 전용이다.

        쓰기 경로(문서 저장·임베딩 적용)에서는 이걸 부르지 말 것 — FTS 까지 끊겨
        그동안 키워드 검색이 죽는다. 그쪽은 :meth:`drop_vector_index` 를 쓴다."""
        self.drop_vector_index()
        self._load_ext("fts")
        self._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')", "Chunk FTS 색인")

    #: 엔진이 FTS 색인 불일치를 **스스로 식별해** 던지는 문구의 판별 표지.
    #: 실측 원문(ladybug 0.18.0, 2026-08-09 라이브·사본 양쪽):
    #:   "FTS index 'chunk_ft' is inconsistent: document for node offset 992 is
    #:    missing during delete. Drop and recreate the FTS index."
    #: 0.20.4(확장 v0.20.0)도 바이너리 안의 문구가 글자 그대로 같다(2026-09-27 대조). 같은
    #: 계열로 "FTS index '…' is inconsistent: term '…' is missing during delete." 도 있고
    #: 두 표지에 똑같이 걸린다 — 처방(드롭 후 재생성)도 같다.
    #: 문구 전체가 아니라 두 표지의 **동시 등장**으로 본다 — 오프셋·색인명은 매번 다르고,
    #: 한쪽만 보면 무관한 오류를 물어 엉뚱하게 재빌드한다.
    _FTS_INCONSISTENT_MARKS = ("fts index", "inconsistent")

    @classmethod
    def is_fts_inconsistent(cls, exc: BaseException) -> bool:
        """이 예외가 **그** 불일치인가. 재빌드로 고쳐지는 조건만 참이어야 한다."""
        m = str(exc).lower()
        return all(k in m for k in cls._FTS_INCONSISTENT_MARKS)

    @_exclusive_ddl
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
        self._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{self._FTS_INDEX}')",
                         "Chunk FTS 색인")          # 없으면 그냥 만든다(멱등)
        # 열은 표지가 정한다(v21). `lex` 위의 재빌드는 `text` 의 7배쯤 걸린다 — 그동안 이
        # 프로세스의 다른 문장이 기다린다. 근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §2.6]]
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',"
                           f"{self._fts_columns()!r})")

    @_exclusive_ddl
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
        self._conn.execute(f"CALL CREATE_FTS_INDEX('Chunk','{self._FTS_INDEX}',"
                           f"{self._fts_columns()!r})")

    @_exclusive_ddl
    def ensure_search_indexes(self, *, dim: int | None = None, conn=None) -> dict:
        """검색 인덱스 **지연 보장**(멱등) — 부재 시에만 생성한다(T3b 실사용 사고 대응).

        `build_search_indexes` 와 달리 이미 있으면 'already exists' 를 흡수해 재빌드하지
        않는다 — search 경로가 '인덱스 부재'로 실패했을 때 1회만 부르는 자동 복구용
        (`api.query.search`). FTS(BM25)는 항상, 벡터(HNSW)는 `dim` 이 주어질 때만
        보장한다(쿼리 임베딩 검색이 필요할 때만 벡터 인덱스 생성). 반환 {fts, vector}
        = 이번 호출에서 실제 생성했는지. 잔해가 막으면 그 표의 :class:`IndexRemnant` 를
        던진다(FTS 가 먼저다 — FTS 잔해면 벡터는 시도하지 않는다).

        ⚠ 이 메서드는 **쓰기 지점에서만** 불린다 — reader 뷰는 잡 큐로 되돌려 보낸다
        (:meth:`_ReaderView.ensure_search_indexes`). 예전엔 reader 가 자기 커넥션을
        `conn` 으로 넘겨 여기서 DDL 을 돌렸는데, 그게 라이터를 막았다(2026-07-31
        실측). `conn` 은 그 시절 잔재로 지금은 넘기는 호출부가 없다 — 남겨 두되
        (하위호환) 새 호출부가 쓰지 않게 한다."""
        built = {"fts": self._ensure_index("Chunk", conn=conn), "vector": False}
        if dim is not None:
            built["vector"] = self._ensure_index("ChunkVec", conn=conn)
        return built

    #: 확장 색인이 걸리는 표 → (확장, 색인 이름을 든 속성, 생성 인자, 잔해 예외).
    #: 잔해 판별·쓰기 거절·기동 수리가 이 하나를 읽는다. 여기 없는 표에 확장 색인을
    #: 걸면 그 표의 잔해는 아무도 못 본다 — 걸 때 여기에도 적을 것.
    _REMNANT_INDEXES = {
        "Chunk": ("fts", "_FTS_INDEX", "['text']", FtsIndexRemnant),
        "ChunkVec": ("vector", "_VEC_INDEX", "'embedding'", VectorIndexRemnant),
        "NodeVec": ("vector", "_NODE_VEC_INDEX", "'embedding'", VectorIndexRemnant),
    }
    #: 잔해 색인의 판별 표지(ladybug 0.18.0 실측 원문, 2026-09-25 · 0.20.4 의 엔진·FTS 확장
    #: 바이너리에 두 문구가 글자 그대로 있다, 2026-09-27 대조). 잔해는 카탈로그에 없어서
    #: `SHOW_INDEXES` 에 안 나온다 — 생성을 시도해야만 보인다. 문구가 둘이다:
    #:   "Runtime exception: Index chunk_vec is not loaded yet. Please load the index
    #:    before accessing it."                              — 첫 시도(벡터·FTS 같다)
    #:   "Binder exception: Table: 8_chunk_ft_docs already exists. Please drop or rename
    #:    the table before creating a full text search index." — FTS 의 **두 번째부터**
    #: 🔴 FTS 생성은 원자적이지 않다: 잔해 앞에서 실패하면서도 내부 표(`<표 id>_chunk_ft_*`)를
    #: 남기고, 그 뒤의 시도는 둘째 문구를 낸다. 여기 'already exists' 가 들어 있어서 한때
    #: "이미 있다"로 읽혔고, 기동 수리가 잔해를 두고 `present` 라고 답했다(실측). 그래서
    #: 정상의 "이미 있다"는 :meth:`_ensure_index` 가 **색인 이름까지** 보고 가른다.
    #: 벡터 생성은 실패해도 아무것도 안 남긴다(몇 번을 해도 첫째 문구 — 실측).
    #: 근거: [[record:a-failed-fts-create-on-a-remnant-leaves-undroppable-internal-tables-2026-09-25]]
    _INDEX_REMNANT_MARKS = ("is not loaded yet", "before creating a full text search index")
    #: `ChunkVec` 수리가 옮겨 담는 표(= ``_rebuild_name("ChunkVec")``). 테스트가 이 이름을 쓴다.
    _CHUNK_VEC_REBUILD = "ChunkVecRebuild"

    @classmethod
    def is_index_remnant(cls, exc: BaseException) -> bool:
        m = str(exc).lower()
        return any(mark in m for mark in cls._INDEX_REMNANT_MARKS)

    @classmethod
    def _remnant_note(cls, table: str) -> str:
        kind = "full-text" if cls._REMNANT_INDEXES[table][0] == "fts" else "vector"
        return (f"{kind} index cannot be built: {table} holds a remnant index left by a "
                f"crash and writes to {table} are refused; restarting the sidecar repairs it")

    def _remnant_error(self, table: str) -> IndexRemnant:
        return self._REMNANT_INDEXES[table][3](self._remnant_note(table), table=table)

    @staticmethod
    def _rebuild_name(table: str) -> str:
        """수리가 옮겨 담는 표의 이름. 이 이름이 남아 있으면 앞선 수리가 커밋 전에 죽은 것이다."""
        return f"{table}Rebuild"

    def _index_create_stmt(self, table: str) -> str:
        ext, attr, args, _ = self._REMNANT_INDEXES[table]
        fn = "CREATE_FTS_INDEX" if ext == "fts" else "CREATE_VECTOR_INDEX"
        if table == "Chunk":
            args = repr(self._fts_columns())      # 표의 `['text']` 는 표지 전의 기본값이다
        return f"CALL {fn}('{table}','{getattr(self, attr)}',{args})"

    def _ensure_index(self, table: str, *, conn=None) -> bool:
        """``table`` 의 확장 색인을 부재 시에만 만든다. 만들었으면 True, 이미 있으면 False.

        잔해면 표시하고(`_remnants`) 그 표의 :class:`IndexRemnant` 를 던진다. 확인된 뒤로는
        빌드를 다시 돌리지 않는다 — 매번 수십 초 돌고 같은 자리에서 실패한다. 허브에서는
        검색마다 그 잡이 걸렸고 잡마다 스냅샷을 불렀다."""
        if table in self._remnants:
            raise self._remnant_error(table)
        # 인덱스 생성은 쓰기다 — 이 경로는 잡 워커(또는 CLI·기동의 단일 스레드)에서만
        # 돈다(`_ReaderView.ensure_search_indexes` 가 잡 큐로 되돌린다). 따라서
        # 여기서는 writer 로 로드해도 R4 를 어기지 않는다.
        self._load_ext(self._REMNANT_INDEXES[table][0])
        name = getattr(self, self._REMNANT_INDEXES[table][1])
        try:
            (conn or self._conn).execute(self._index_create_stmt(table))
            built = True
        except Exception as exc:
            # 정상의 '이미 있음'은 "Index chunk_ft already exists in table Chunk." 하나다.
            # 'already exists' 만 보면 FTS 잔해의 둘째 문구까지 먹는다(`_INDEX_REMNANT_MARKS`).
            if f"index {name} already exists" in str(exc).lower():   # 멱등
                built = False
            elif self.is_index_remnant(exc):
                self._remnants.add(table)
                print(f"vinc: ⚠ {self._remnant_note(table)}", file=sys.stderr, flush=True)
                raise self._remnant_error(table) from exc
            else:
                raise
        if table == "ChunkVec":
            # 방금 만들었든 이미 있었든(`already exists` 흡수) 결과는 같다 — 인덱스는
            # 존재한다. 드롭 접기 플래그를 내려 다음 쓰기가 제대로 한 번 드롭하게 한다.
            self._vec_index_dropped = False
        if table == "Chunk" and self._fts_recovery_path:
            # 카탈로그의 '이미 있음'은 WAL 재생된 행이 FTS에 들어 있다는 보장이 아니다.
            # 새 색인은 현재 행에서 만들었으므로 기존 색인만 다시 만든다.
            if not built:
                self.rebuild_fts_index()
            self._conn.execute("CHECKPOINT")
            os.unlink(self._fts_recovery_path)
            self._fts_recovery_path = None
            built = True
        return built

    @property
    def vector_index_remnant(self) -> bool:
        return "ChunkVec" in self._remnants

    @property
    def fts_index_remnant(self) -> bool:
        return "Chunk" in self._remnants

    @property
    def node_vector_index_remnant(self) -> bool:
        return "NodeVec" in self._remnants

    @_exclusive_ddl
    def heal_vector_index(self, *, dim: int) -> dict:
        """**기동 때 한 번, 요청을 받기 전에** 부른다. 벡터가 있는데 색인이 없으면 세우고,
        잔해 때문에 못 세우면 `ChunkVec` 을 새 표로 떠서 옮긴 뒤 세운다.

        표를 버리고 다시 만드는 DDL 이라 다른 커넥션이 읽는 중에 돌리면 안 된다 — 그래서
        실행 중에는 잔해를 **표시만** 하고(`_remnants`) 수리는 다음 기동에 맡긴다.
        옮기는 동안 행은 하나도 버리지 않는다: 옮긴 수가 다르면 옛 표를 그대로 두고 던진다.
        허브 사본(벡터 11,905개) 1코어 실측: 빌드 시도가 실패하기까지 포함해 64초.
        근거: [[record:a-crash-between-a-vector-index-drop-and-the-next-checkpoint-leaves-a-remnant-2026-09-25]]

        FTS 는 여기서 만들지 않는다 — :meth:`heal_fts_index` 몫이다. FTS 잔해가 벡터
        수리까지 막으면 안 된다.

        **빈 표에서도 세워 본다.** 잔해는 행이 없어도 남고(색인은 빈 표에도 선다), 여기서 못
        보면 잔해 표시가 안 서서 쓰기 거절도 없다 — 첫 임베딩 적용이 그대로 세그폴트다. 빈
        표의 생성·이동은 즉시 끝난다. #407 은 빈 표를 건너뛰었다.

        반환 ``state``: empty(벡터 없음) · present(이미 있음) · built(세움) ·
        repaired(표를 다시 떠서 세움)."""
        t0 = time.monotonic()
        self._finish_interrupted_rebuild()
        n = self._count("ChunkVec")
        if self._has_index("ChunkVec", self._VEC_INDEX):
            return {"state": "present", "rows": n} if n else {"state": "empty"}
        try:
            self._ensure_index("ChunkVec")
            if not n:
                return {"state": "empty"}
            return {"state": "built", "rows": n, "seconds": round(time.monotonic() - t0, 1)}
        except VectorIndexRemnant:
            pass
        self._rebuild_table("ChunkVec", dim=dim)
        self._remnants.discard("ChunkVec")
        self._ensure_index("ChunkVec")
        return {"state": "repaired", "rows": n, "seconds": round(time.monotonic() - t0, 1)}

    @_exclusive_ddl
    def heal_fts_index(self, *, dim: int) -> dict:
        """**기동 때 한 번, 요청을 받기 전에** 부른다. `Chunk` 의 FTS 색인이 없으면 세우고,
        잔해 때문에 못 세우면 `Chunk` 와 거기 걸린 관계 표(`MENTIONS`)를 새 표로 떠서
        옮긴 뒤 세운다. 청크 행과 엣지는 하나도 버리지 않는다(수가 다르면 옛 표를 두고 던진다).

        잔해가 있는 `Chunk` 에는 문서 저장·삭제가 전부 세그폴트다(아무 열의 SET 까지). 그래서
        이것이 실패하면 쓰기는 거절된 채로 남는다(:meth:`run`) — 키워드 검색도 선다.
        ``dim`` 은 `Chunk.embedding` 폭을 못 읽을 때의 대비값이다(수리는 저장된 폭을 쓴다).
        근거: [[record:an-fts-remnant-on-chunk-stops-every-document-write-and-the-heal-moves-chunk-with-its-edges-2026-09-25]]

        반환 ``state``: present(이미 있음) · built(세움) · repaired(표를 다시 떠서 세움)."""
        return self._heal_index("Chunk", dim=dim)

    @_exclusive_ddl
    def heal_node_vector_index(self, *, dim: int) -> dict:
        """**기동 때 한 번** — 노드 미러(`NodeVec`)의 벡터 색인. :meth:`heal_fts_index` 와 같다.

        미러는 배포마다 통째로 다시 만드는 파생물이지만 그 재구축이 잔해를 **고치지 못한다**:
        `node_mirror.replace` 는 옛 행을 지우고 새 행을 넣는데, 잔해 위에서는 둘 다
        세그폴트다(실측). 그래서 기동이 먼저 고친다. 행은 버리지 않고 옮긴다 — 재기동은
        배포가 아니어서 미러를 다시 채우지 않고, 버리면 다음 배포까지 의미 팔이 빈다."""
        return self._heal_index("NodeVec", dim=dim)

    def _heal_index(self, table: str, *, dim: int) -> dict:
        t0 = time.monotonic()
        self._drop_interrupted_rebuild(table)
        try:
            built = self._ensure_index(table)
            return {"state": "built" if built else "present",
                    "seconds": round(time.monotonic() - t0, 1)}
        except IndexRemnant:
            pass
        rows = self._rebuild_table(table, dim=dim)
        self._remnants.discard(table)
        self._ensure_index(table)
        return {"state": "repaired", "rows": rows, "seconds": round(time.monotonic() - t0, 1)}

    def _count(self, label: str) -> int:
        r = _exec_to_dicts(self._conn, f"MATCH (v:{label}) RETURN count(v) AS n", None)
        return int(r[0]["n"]) if r else 0

    def _count_rel(self, label: str) -> int:
        r = _exec_to_dicts(self._conn, f"MATCH ()-[e:{label}]->() RETURN count(e) AS n", None)
        return int(r[0]["n"]) if r else 0

    def _has_index(self, table: str, name: str) -> bool:
        rows = _exec_to_dicts(self._conn, "CALL SHOW_INDEXES() RETURN table_name AS t, "
                                          "index_name AS i", None)
        return any(r.get("t") == table and r.get("i") == name for r in rows)

    def _has_table(self, name: str) -> bool:
        rows = _exec_to_dicts(self._conn, "CALL SHOW_TABLES() RETURN name AS n", None)
        return any(r.get("n") == name for r in rows)

    def _rebuild_table(self, table: str, *, dim: int) -> int:
        """``table`` 을 같은 열의 새 표로 옮기고 옛 표(잔해 포함)를 버린다. 반환은 옮긴 행 수.

        노드 표는 관계 표가 걸린 채로는 버릴 수 없으므로 거기 걸린 관계 표도 **전부** 같이
        옮긴다(`Chunk` 면 `MENTIONS` — 새 관계 표는 새 노드 표를 끝점으로 갖는다).

        순서: 새 표들 → COPY(엔진 안에서 — 물리 키 그대로 옮긴다) → 수 대조 → 옛 관계 표·
        옛 표 DROP 과 RENAME 을 **한 트랜잭션**으로 → 체크포인트. 교체가 한 트랜잭션이라
        죽어도 반쪽 상태가 없다: 커밋 전이면 옛 표가 온전하고 새 표만 남으며(다음 기동의
        :meth:`_drop_interrupted_rebuild` 가 버린다), 커밋 뒤면 WAL 재생이 교체를 마저
        한다(둘 다 실측). 수가 하나라도 다르면 옛 표를 그대로 두고 던진다.
        근거: [[record:ddl-in-one-manual-transaction-survives-a-crash-whole-or-not-at-all-2026-09-25]]"""
        tmp = self._rebuild_name(table)
        rels = rel_tables_on(table)
        specs = node_table_specs()
        swap = {table: tmp}
        rows = self._count(table)
        edges = {r["label"]: self._count_rel(r["label"]) for r in rels}
        created: list[str] = []          # 버릴 순서 — 관계 표가 노드 표보다 앞이다

        def _drop_created() -> None:
            for name in created:
                try:
                    self._conn.execute(f"DROP TABLE {name}")
                except Exception:        # noqa: BLE001 — 되돌리는 중이다. 원래 예외가 중요하다
                    pass

        try:
            # 폭은 설정이 아니라 **지금 표의 열**에서 읽는다 — 수리는 옮기는 것이지 바꾸는 것이
            # 아니다. 설정과 저장이 어긋난 스토어에서 설정 폭으로 만들면 COPY 가 타입 오류로 선다.
            self._conn.execute(render_node_table(
                table, dim=self._stored_vec_dim(dim, table), as_label=tmp))
            created.append(tmp)
            self._conn.execute(f"COPY {tmp} FROM (MATCH (v:{table}) RETURN "
                               + ", ".join(f"v.{c}" for c in specs[table]["cols"]) + ")")
            for rel in rels:
                rtmp = self._rebuild_name(rel["label"])
                self._conn.execute(render_rel_table(rel["label"], as_label=rtmp, swap=swap))
                created.insert(0, rtmp)
                for src, dst in rel["pairs"]:
                    proj = ([f"a.{specs[src]['pk']}", f"b.{specs[dst]['pk']}"]
                            + [f"r.{p}" for p in rel["props"]])
                    self._conn.execute(
                        f"COPY {rtmp} FROM (MATCH (a:{src})-[r:{rel['label']}]->(b:{dst}) "
                        f"RETURN {', '.join(proj)}) "
                        f"(from='{swap.get(src, src)}', to='{swap.get(dst, dst)}')")
            short = [f"{table} {self._count(tmp)}≠{rows}"] if self._count(tmp) != rows else []
            short += [f"{lbl} {self._count_rel(self._rebuild_name(lbl))}≠{n}"
                      for lbl, n in edges.items()
                      if self._count_rel(self._rebuild_name(lbl)) != n]
            if short:
                raise RuntimeError(f"{table} 수리 중단: 옮긴 수가 다르다 ({', '.join(short)}) "
                                   "— 옛 표는 그대로다")
        except Exception:
            _drop_created()
            raise
        swap_stmts = ([f"DROP TABLE {r['label']}" for r in rels]
                      + [f"DROP TABLE {table}", f"ALTER TABLE {tmp} RENAME TO {table}"]
                      + [f"ALTER TABLE {self._rebuild_name(r['label'])} RENAME TO {r['label']}"
                         for r in rels])
        self._conn.execute("BEGIN TRANSACTION")
        try:
            for stmt in swap_stmts:
                self._conn.execute(stmt)
            self._conn.execute("COMMIT")
        except Exception:
            # 실패한 문장은 트랜잭션을 스스로 되돌린다(실측). 그때 ROLLBACK 은 '활성 트랜잭션
            # 없음'으로 던지므로 삼킨다 — 어느 쪽이든 옛 표는 온전하다.
            try:
                self._conn.execute("ROLLBACK")
            except Exception:    # noqa: BLE001
                pass
            _drop_created()
            raise
        self._conn.execute("CHECKPOINT")
        return rows

    def _stored_vec_dim(self, fallback: int, table: str = "ChunkVec") -> int:
        """``table.embedding`` 의 저장된 폭(`FLOAT[n]`). 못 읽으면 ``fallback``."""
        try:
            for r in _exec_to_dicts(self._conn, f"CALL TABLE_INFO('{table}') RETURN *", None):
                if r.get("name") == "embedding":
                    m = re.search(r"\[(\d+)\]", str(r.get("type") or ""))
                    if m:
                        return int(m.group(1))
        except Exception:
            pass
        return fallback

    def _drop_interrupted_rebuild(self, table: str) -> None:
        """앞선 수리가 **커밋 전에** 죽었으면 옮기던 표들을 버린다. 옛 표는 온전하다 — 교체가
        한 트랜잭션이라 커밋 전에는 아무것도 바뀌지 않았다. 관계 표가 새 노드 표를 끝점으로
        물고 있으므로 관계 표부터 버린다."""
        names = ([self._rebuild_name(r["label"]) for r in rel_tables_on(table)]
                 + [self._rebuild_name(table)])
        left = [name for name in names if self._has_table(name)]
        for name in left:
            self._conn.execute(f"DROP TABLE {name}")
        if left:
            self._conn.execute("CHECKPOINT")

    def _finish_interrupted_rebuild(self) -> None:
        """앞선 `ChunkVec` 수리가 중간에 죽었으면 마무리한다. 옮겨 담던 표가 남아 있을 때만 움직인다.

        옛 표가 비었고 옮긴 표에 행이 있으면 교체를 마저 한다 — #407 의 수리는 DROP 과
        RENAME 을 따로 커밋해서 그 사이에서 죽으면 이 상태가 남았다(기동의 프로비저닝이 빈
        `ChunkVec` 을 다시 만든다). 지금의 수리(:meth:`_rebuild_table`)는 교체가 한
        트랜잭션이라 이 상태를 만들지 않지만, 그 코드로 죽은 스토어를 위해 남긴다.
        아니면 옛 표가 온전하므로 옮기던 표를 버린다."""
        tmp = self._CHUNK_VEC_REBUILD
        if not self._has_table(tmp):
            return
        if self._count("ChunkVec") == 0 and self._count(tmp) > 0:
            self._conn.execute("DROP TABLE ChunkVec")
            self._conn.execute(f"ALTER TABLE {tmp} RENAME TO ChunkVec")
        else:
            self._conn.execute(f"DROP TABLE {tmp}")
        self._conn.execute("CHECKPOINT")

    @request_timing.timed("vector")
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
        with request_timing.measure("vector_index"):
            with _vector_query_guard():
                rows = _exec_to_dicts(c, cypher, params)
        best: dict[str, dict] = {}
        for r in rows:                       # 이미 distance 오름차순 — 첫 등장이 최소
            best.setdefault(r.get("id"), r)
        out = list(best.values())[:int(top_k)]
        if filters and len(out) < int(top_k):
            # 필터는 **전역** HNSW 상위 k 뒤에 걸린다. 그 안에 이 필터의 행이 모자라면(작은
            # 스페이스·문서 하나) 필터 안을 정확히 훑는다. 거리는 색인의 기본(코사인)과 같은
            # 값이고(실측), 매개변수를 묶는 평범한 조회라 읽기 커넥션에서 돈다(DDL 없음).
            scope = _filter_clause("p", filters, params)       # 여기선 첫 WHERE 다
            with request_timing.measure("vector_fallback"):
                out = _exec_to_dicts(
                    c, f"MATCH (p:Chunk) {scope}"
                       "MATCH (v:ChunkVec) WHERE v.chunk_id = p.chunk_id "
                       "RETURN p.chunk_id AS id, p.text AS text, "
                       "min(1 - array_cosine_similarity(v.embedding, $emb)) AS distance "
                       "ORDER BY distance LIMIT $k", {**params, "k": int(top_k)})
        return out

    @request_timing.timed("node_vector")
    def node_vector_search(self, embedding, top_k, *, owner=None, conn=None,
                           use_index: bool = False) -> list[dict]:
        """노드 미러(NodeVec)에서 의미로 찾는다 — 어휘가 하나도 안 겹쳐도 닿는 유일한 팔.

        Chunk 쪽 :meth:`vector_search` 와 **모양이 다르다**. 저쪽은 자식(ChunkVec)에서
        찾아 부모로 접지만, 노드는 제목이 짧아 한 행에 벡터 하나다. 접을 것이 없다.

        SEC-1: owner 를 반드시 건다. 미러는 노드의 사본이라 스코프가 새면 남의 그래프
        제목이 후보로 올라온다 — Chunk 팔이 같은 이유로 fail-closed 인 것과 같다.

        확장·미러가 없거나(큰 미러에서는) 인덱스가 없으면 **예외가 아니라 빈 목록**이다. 이 팔은 증강이고,
        없다고 검색이 죽으면 안 된다(그래프 팔과 같은 태도).
        미러가 `_NODE_VEC_EXACT_MAX_ROWS` 밑이면 HNSW 를 부르지 않고 정확 코사인으로 훑는다
        (진단 구간 `node_vector_exact`). `use_index=True` 는 행 수와 무관하게 HNSW 경로를
        탄다 — 인덱스가 실제로 답하는지 재는 쪽(:func:`api.node_mirror.index_answers`)이 쓴다.
        근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]"""
        c = conn or self._conn
        try:
            with request_timing.measure("node_vector_load"):
                self._load_ext("vector", reader=conn is not None)
        except Exception:
            with request_timing.measure("node_vector_load_error"):
                return []
        params: dict = {"emb": list(embedding)}
        where = ""
        if owner:
            where = "WHERE node.owner_id = $owner "
            params["owner"] = owner
        # 정확 스캔 한 문장. 인덱스 없이 테이블을 훑으므로 벡터 질의 가드 밖이다(원래 폴백과 같다).
        exact = (f"MATCH (node:NodeVec) {where}"
                 "RETURN node.node_id AS id, node.kind AS kind, node.title AS title, "
                 "node.text AS text, "
                 "1 - array_cosine_similarity(node.embedding, $emb) AS distance "
                 "ORDER BY distance LIMIT $k")
        try:
            if not use_index:
                n = _exec_to_dicts(c, "MATCH (v:NodeVec) RETURN count(v) AS n", None)
                if (int(n[0]["n"]) if n else 0) < _NODE_VEC_EXACT_MAX_ROWS:
                    # 미러가 작은 동안은 정확 스캔이 HNSW 보다 싸고 재현율도 그 이상이다.
                    with request_timing.measure("node_vector_exact"):
                        return _exec_to_dicts(c, exact, {**params, "k": int(top_k)})
            k = int(top_k) * (self._OVERSAMPLE if owner else 1)
            cypher = (
                f"CALL QUERY_VECTOR_INDEX('NodeVec','{self._NODE_VEC_INDEX}', $emb, $k) "
                f"{where}"
                "RETURN node.node_id AS id, node.kind AS kind, node.title AS title, "
                "node.text AS text, distance "
                "ORDER BY distance"
            )
            with request_timing.measure("node_vector_index"):
                with _vector_query_guard():
                    rows = _exec_to_dicts(c, cypher, {**params, "k": k})
            if owner and len(rows) < int(top_k):
                # 전역 상위 k 에 이 소유자의 행이 모자랐다 — 그 소유자 안을 정확히 훑는다
                # (:meth:`vector_search` 와 같은 이유·같은 거리).
                with request_timing.measure("node_vector_fallback"):
                    rows = _exec_to_dicts(c, exact, {**params, "k": int(top_k)})
        except Exception:
            with request_timing.measure("node_vector_query_error"):
                return []       # 증강 팔의 강등 계약은 유지하고 실패 분기만 숫자로 표시
        return rows[:int(top_k)]

    @_exclusive_ddl
    def drop_node_vector_index(self) -> None:
        """노드 미러의 HNSW 만 제거(멱등).

        🔴 **미러를 채우기 전에 반드시 부른다.** "없던 벡터가 생기는" 것이 HNSW 삽입이고
        그것만 세그폴트를 낸다(:meth:`drop_vector_index` 주석의 실측 — exit 139). 미러
        채우기는 정확히 그 모양이라 드롭이 방어 장치다. 그래서 확장 로드 실패도 삼키지
        않는다: 드롭을 못 했으면 채우기가 멈춰야 한다."""
        self._load_ext("vector")
        self._drop_index(f"CALL DROP_VECTOR_INDEX('NodeVec','{self._NODE_VEC_INDEX}')",
                         "NodeVec 벡터 색인")

    @_exclusive_ddl
    def create_node_vector_index(self) -> str:
        """노드 미러의 HNSW 생성(멱등). 채우기가 끝난 **뒤에** 부른다.

        반환은 **삼킨 사유**다(성공이면 빈 문자열). 멱등이어야 하므로 예외를 밖으로
        내보내지 않지만, 그냥 삼키면 "이미 있음"과 "쓰기 트랜잭션을 못 잡았음"이 같은
        침묵이 된다 — 뒤엣것은 행만 있고 인덱스가 없는 미러를 남기고, 그 상태의 의미
        팔은 예외가 아니라 빈 목록이라 밖에서 **팔을 안 켠 것과 구분되지 않는다**
        (2026-09-10 라이브). 부르는 쪽은 무시해도 되고, 값을 보면 이유를 안다.

        잔해면 표시한다(`node_vector_index_remnant`) — 그 뒤로 미러 쓰기는 거절된다."""
        if "NodeVec" in self._remnants:
            return f"{VectorIndexRemnant.__name__}: {self._remnant_note('NodeVec')}"
        try:
            self._load_ext("vector")
            self._conn.execute(
                f"CALL CREATE_VECTOR_INDEX('NodeVec','{self._NODE_VEC_INDEX}','embedding')")
            return ""
        except Exception as exc:
            if self.is_index_remnant(exc):
                self._remnants.add("NodeVec")
                print(f"vinc: ⚠ {self._remnant_note('NodeVec')}", file=sys.stderr, flush=True)
            return f"{type(exc).__name__}: {exc}"

    @request_timing.timed("keyword")
    def fulltext_search(self, text, top_k, filters=None, *, conn=None) -> list[dict]:
        """BM25 키워드 팔. 색인이 `lex` 위면(표지) 질의도 같은 함수로 바꾸고, 행마다 `lex` 를
        함께 돌려준다 — 융합 앞의 관문이 질의를 얼마나 덮는지 그 열로 잰다(재분석 없이).
        `lex` 는 응답에 나가지 않는다: 소비자(`vinc_rag.retrieval._chunk_arms`)가 뗀다."""
        if self._fts_recovery_path:
            raise RuntimeError("FTS recovery is incomplete; rebuild the replayed index before searching")
        c = conn or self._conn
        self._load_ext("fts", reader=conn is not None)
        k = int(top_k) * (self._OVERSAMPLE if filters else 1)
        lex = self.lex_ready()
        if lex:
            from .lexical import lex_text
            text = lex_text(text)
            if not text.strip():
                return []                 # 기호만 있는 질의 — 엔진에 빈 질의를 보내지 않는다
        params: dict = {"q": text, "k": k}
        where = _filter_clause("node", filters, params)
        cypher = (
            f"CALL QUERY_FTS_INDEX('Chunk','{self._FTS_INDEX}', $q) "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, "
            + ("node.lex AS lex, " if lex else "")
            + "score ORDER BY score DESC LIMIT $k"
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
    # 기본 프로젝션 = 의미층을 **실제로 잇는** 관계들(방향 무관 구조 연결성).
    # **노드 테이블은 여기 안 적는다** — 아래 _algo_projection 이 끝점에서 파생한다.
    #
    # ⚠ ABOUT·ATTACHES_TO 가 원래 빠져 있었고, v12 가 의미층을 늘리자 그것이
    # **고아 오판**으로 드러났다: 결정 67건 중 43건이 `vinc_diagnose` 의 orphans 에
    # 올라왔는데, 이관이 결정→개념을 `ABOUT` 으로 이었으므로 전부 거짓이었다
    # (`decision:d68` 은 ABOUT 이웃 1건을 갖고도 고아로 보고됐다, 2026-08-09 실측).
    #
    # 4종이던 시절에는 이 누락이 안 보였다 — ABOUT 의 끝점 Episode 와 ATTACHES_TO 의
    # Record 는 투영에 아예 없었으니 "고아로 잘못 뜨는" 일 자체가 없었다. 층이 늘면서
    # **투영에는 들어오는데 그 층의 엣지는 안 걷는** 비대칭이 생긴 것이 원인이다.
    # 그래서 "의미층 관계 4종"이 아니라 "의미층을 잇는 관계 전부"로 규칙을 바꾼다.
    #
    # 부수효과(의도한 것): Episode·Record 도 진단에 들어온다. `ABOUT`/`ATTACHES_TO`
    # 가 없는 사건·기록은 실제로 지식그래프에서 떨어져 있으므로 고아로 보고되는 것이
    # 맞다. 커뮤니티(louvain)는 이 기본값을 안 쓴다 — 호출부가 RELATED_TO 만 넘긴다.
    _ALGO_REL_TABLES = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM",
                        "INHERITS_FROM", "ABOUT", "ATTACHES_TO")

    @staticmethod
    def _algo_projection(rel_tables: tuple) -> tuple[tuple[str, ...], str]:
        """투영할 노드 테이블과 id 식을 **스키마에서 파생**한다. 반환 (라벨들, id식).

        ⚠ 여기 원래 `_ALGO_NODE_TABLES = ("Concept",)` 상수가 있었고, 그것이
        2026-08-09 에 진단을 통째로 죽였다. `project_graph` 는 **투영한 rel 테이블의
        끝점 노드 테이블이 전부 투영돼 있기를 요구한다**:

            Binder exception: Decision is connected to RELATED_TO but not projected.

        즉 rel 테이블에 끝점을 하나 더 여는 순간(v12 의 Decision) 이 상수가 낡고,
        `vinc_diagnose` 가 **결정과 무관한 고아 조회까지** 전부 `executed=False` 로
        강등된다. 상수와 스키마가 따로 관리되는 한 같은 일이 또 난다. 그래서 파생한다.

        id 식도 같은 이유로 파생한다. 노드 테이블마다 PK 이름이 달라 `node.concept_id`
        하나로 뽑으면 Decision 행의 id 가 `None` 이 되고, 호출부의 `if not cid:
        continue` 가 그것을 **조용히** 버린다(실측: 투영은 성공하고 결과만 빈다)."""
        from . import embedded_schema as _schema
        want = set(rel_tables)
        labels: list[str] = []
        for spec in _schema.rel_table_specs():
            if spec["label"] not in want:
                continue
            for lbl in (spec["from"], spec["to"]):
                if lbl not in labels:
                    labels.append(lbl)
        node_specs = _schema.node_table_specs()
        pks: list[str] = []
        for lbl in labels:
            pk = node_specs.get(lbl, {}).get("pk")
            if pk and pk not in pks:
                pks.append(pk)
        if not pks:
            return tuple(labels), "node.concept_id"
        expr = (f"node.{pks[0]}" if len(pks) == 1
                else "coalesce(" + ", ".join(f"node.{p}" for p in pks) + ")")
        return tuple(labels), expr

    @_exclusive_ddl
    def graph_algo(self, algo: str, *, node_tables: tuple | None = None,
                   rel_tables: tuple | None = None, id_col: str | None = None,
                   graph_name: str = "_vinc_algo", conn=None) -> list[dict]:
        """algo 확장 그래프 알고리즘 실행(E-G3). 반환 ``[{id, value}]`` — id 는 투영된
        노드 테이블들의 PK(여럿이면 coalesce), value 는 알고리즘 지표(group_id/
        louvain_id/rank). 확장 미가용(오프라인 미번들) 시 :meth:`_load_ext` 가
        RuntimeError 로 강등한다(호출부가 executed=False 로 표면화). project_graph 는
        전체 테이블을 투영하므로 (owner 필터 불가) 소유자 스코프는 **호출부가 결과를
        owner 개념 집합과 교차** 해 적용한다(교차소유자 엣지 부재 → 컴포넌트/커뮤니티는
        owner-clean).

        ``node_tables``·``id_col`` 은 명시 override 다. 미지정이 정상 경로이고, 그때
        둘 다 :meth:`_algo_projection` 이 스키마에서 파생한다."""
        c = conn or self._conn
        metric = self._ALGO_METRIC.get(algo)
        if metric is None:
            raise ValueError(f"unsupported graph algo: {algo!r} "
                             f"(지원: {sorted(self._ALGO_METRIC)})")
        self._load_ext("algo", reader=conn is not None)
        rels = tuple(rel_tables or self._ALGO_REL_TABLES)
        derived, id_expr = self._algo_projection(rels)
        if id_col:
            id_expr = f"node.{id_col}"
        nt = "[" + ", ".join(f"'{t}'" for t in (tuple(node_tables) if node_tables
                                                else derived)) + "]"
        rt = "[" + ", ".join(f"'{t}'" for t in rels) + "]"

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
                   f"RETURN {id_expr} AS id, {metric} AS value", None)
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
        로컬 실엔진 재현. 0.20.4 도 `Database(auto_checkpoint=True)` 가 기본이다).
        그 틀린 주석을 근본원인으로 채택해 사고 보고서까지 갔다 — 주석 하나가 오진의
        발원지가 된 사례다.

        ⚠ Windows: 열려 있는 DB 파일에는 **바이트 범위 락**이 걸려 있어 복사가
        `WinError 33` 으로 실패한다(읽기조차 막힌다 — 실측). 리눅스는 같은 코드가
        그냥 되므로 여태 드러나지 않았다. 그래서 복사 직전에 드라이버 핸들을 닫고,
        복사 후 **같은 경로로 다시 연다** — 온디스크 형식이 그대로라 복원 경로
        (`IMPORT` 아님, 폴더 교체)도 그대로다. 재오픈까지가 이 메서드의 계약이다.

        🔴 CHECKPOINT 는 **배타 가드 안에서 혼자** 돈다. 다른 커넥션의 읽기와 겹치면
        엔진이 네이티브로 죽는다(segfault·double free — 0.18.0·0.20.4 둘 다). 이 백엔드의
        다른 체크포인트(드롭 뒤·재구축)는 전부 배타 가드 안인데 여기만 공유 가드였다.
        그리고 끝나지 않은 체크포인트의 짝 파일(`.wal.checkpoint`·`.shadow`)이 남아
        있으면 **복사하지 않는다** — 본 파일과 `.wal` 만 옮긴 세대는 온전하지 않고,
        복원하면 커밋된 쓰기를 잃을 수 있다(상류 ladybug 0063666, 0.20.4 미포함).
        던지면 `save_snapshot` 이 영속본을 건드리지 않고 `error:` 를 돌려준다 — 그
        상태가 이어지면 스냅샷이 멈추므로 `/health` 의 스냅샷 상태를 볼 것."""
        import os
        import shutil
        if self._fts_recovery_path:
            raise RuntimeError("FTS recovery is incomplete; not copying a snapshot without its repair marker")
        try:
            self._checkpoint_alone()
        except Exception as exc:     # noqa: BLE001 — 방언 미지원·가드 시간 초과
            # 체크포인트 없이 복사해도 본 파일 + `.wal` 짝은 일관이다(쓰기는 이 스레드뿐).
            # 다만 조용히 삼키면 실패가 몇 번 났는지 셀 수 없다 — 찍는다.
            print(f"vinc: snapshot CHECKPOINT skipped: {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
        src = self._cfg.data_dir or os.path.join(os.getcwd(), "vinc_store")
        if not os.path.isdir(src):
            left = [src + s for s in (".wal.checkpoint", ".shadow") if os.path.exists(src + s)]
            if left:
                raise RuntimeError("store has an unfinished checkpoint; not copying "
                                   f"a partial generation ({', '.join(left)})")
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

        # 닫았다 여는 동안 다른 스레드가 이 Database 로 문장을 돌리면 해제된 메모리를
        # 밟는다 — DDL 과 같이 혼자 돈다.
        with DDL_GUARD.exclusive():
            return self._snapshot_reopen(src, _copy, dest)

    def _checkpoint_alone(self) -> None:
        """수동 CHECKPOINT 를 다른 문장 없이 돌린다 — 모든 읽기가 끝나길 기다렸다가,
        도는 동안 새 읽기를 세운다. 읽기와 겹친 체크포인트는 엔진을 죽인다(`snapshot`)."""
        with DDL_GUARD.exclusive():
            self.run("CHECKPOINT")

    def _snapshot_reopen(self, src: str, _copy, dest: str) -> str:
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
                self._conns = weakref.WeakSet()
                self._conn = _open_connection(drv, self._db, self._conns)
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
