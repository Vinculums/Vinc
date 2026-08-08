# -*- coding: utf-8 -*-
"""Store configuration — embedded (offline) store + embedding settings.

Vinc 는 임베디드 전용(설계 §1 — 외부 인프라·API 키 없이 오프라인). Neo4j 서버
설정(uri/user/password)은 서버 경로 레포(`weg-9000-memory`)의 관심사이며 여기에는
없다. 해석 순서: 인자 > 환경변수 > 기본값.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Embedding dimensions are model-dependent (must match embed_pipeline.py).
# 영어 우선(v0.4, design §8): 기본은 로컬 오프라인 retrieval 모델.
#   local-en = bge-base-en-v1.5 (768), local-en-large = bge-large-en-v1.5 (1024).
#   anthropic (voyage-3, 1024) 은 API 키 필요 — 옵션(F5: 키 없이 기동 가능해야 함).
#   "local" (legacy multilingual mpnet 768) 은 back-compat 만.
EMBEDDING_DIM_BY_MODEL = {
    "local-en": 768,          # bge-base-en-v1.5 — 기본(오프라인, design §8·D10)
    "local-en-large": 1024,   # bge-large-en-v1.5 — 품질 옵션
    "anthropic": 1024,        # voyage-3 — API 키 옵션
    "local": 768,             # legacy multilingual mpnet (back-compat)
}
DEFAULT_EMBEDDING_MODEL = "local-en"


@dataclass
class StoreConfig:
    # backend selects the GraphStore implementation (store/backend.py):
    #   "embedded" — 인프로세스 LadybugDB(Kùzu 계보) — Vinc 기본·유일 배포 백엔드.
    # (서버 Neo4j 백엔드는 weg-9000-memory 레포 소관 — Vinc 미탑재.)
    backend: str = "embedded"
    data_dir: str | None = None          # 임베디드 스토어 데이터 디렉토리(§3-2)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # retention tiers (days) for the 3-tier recall memory model
    core_days: int = 30
    archival_days: int = 90
    tenant_id: str | None = None         # 단일 사용자: 상수 tenant:local (§3-3)
    # ── 드라이버 자원 상한(Windows 다중 오픈 대응) ─────────────────────────────
    # LadybugDB(Kùzu 계보)는 **Database 하나마다 가상주소 8 TiB 를 예약**한다
    # (실측: `VirtualAlloc for size 8796093022208 failed`). 리눅스는 오버커밋으로
    # 넘어가지만 Windows 는 커밋 한도가 있어 한 프로세스에서 14~15개까지만 열리고,
    # 그 다음 오픈부터 `MemoryError: bad allocation` 이 난다. 앱은 스토어를 하나만
    # 열어 무관하지만, **테스트 스위트는 수십 개를 연다** — 그래서 로컬 Windows 에서만
    # 10여 건이 무너져 왔다(스코프 격리 실패처럼 보였지만 실제로는 오픈 실패였다).
    # 0/None = 드라이버 기본값(무제한 예약). 값을 주면 그만큼만 예약한다.
    max_db_size: int = 0                 # bytes. VINC_MAX_DB_SIZE
    buffer_pool_mb: int = 0              # MB.    VINC_BUFFER_POOL_MB

    # WAL 이 이만큼 자라면 **엔진이 스스로** 체크포인트한다(쓰기 트랜잭션 끝에서
    # 판정, 활성 트랜잭션이 없을 때). 드라이버 기본은 16MB.
    #
    # 왜 낮추나(2026-08-08 사고). 16MB 기본으로는 WAL 이 11.9MB 까지 자란 채
    # 5시간 반을 버텼고, 그 상태에서 프로세스가 죽자 WAL 이 재생 불가여서
    # **그 5시간 반이 통째로 사라졌다**. 임계값은 곧 **최대 손실 창**이다.
    # 4MB 면 잃는 것이 그만큼으로 묶이고 재생 비용도 줄어든다.
    #
    # ⚠ 공짜가 아니다. 체크포인트는 WAL 을 본 파일에 반영하는 순간이라 **손상
    # 노출이 최대인 연산**이다(SQLite 계열의 알려진 성질: sync 실패가 무결성을
    # 깨는 것은 체크포인트 중뿐이고 커밋 중이면 내구성만 잃는다). 저장 계층이
    # 미덥지 않으면 자주 하는 것이 되레 해롭다 — 그래서 이 값은 **저장 계층을
    # 로컬로 옮기는 것과 한 벌**이고, 그것이 정본 처방이다.
    checkpoint_threshold_mb: int = 4     # MB. VINC_CHECKPOINT_THRESHOLD_MB (0=드라이버 기본)
    extra: dict = field(default_factory=dict)

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIM_BY_MODEL.get(self.embedding_model, 768)

    def redacted(self) -> dict:
        d = self.__dict__.copy()
        d["embedding_dim"] = self.embedding_dim
        return d


def load_config(overrides: dict | None = None) -> StoreConfig:
    """Build a StoreConfig from env + overrides (overrides win)."""
    overrides = overrides or {}

    def pick(key: str, env: str, default):
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        return os.environ.get(env, default)

    cfg = StoreConfig(
        backend=pick("backend", "MEMORY_STORE_BACKEND", "embedded"),
        data_dir=pick("data_dir", "MEMORY_DATA_DIR", None) or None,
        embedding_model=pick("embedding_model", "MEMORY_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        tenant_id=overrides.get("tenant_id"),
    )
    cfg.max_db_size = int(pick("max_db_size", "VINC_MAX_DB_SIZE", 0) or 0)
    cfg.buffer_pool_mb = int(pick("buffer_pool_mb", "VINC_BUFFER_POOL_MB", 0) or 0)
    cfg.checkpoint_threshold_mb = int(
        pick("checkpoint_threshold_mb", "VINC_CHECKPOINT_THRESHOLD_MB",
             StoreConfig.checkpoint_threshold_mb) or 0)
    cfg.core_days = int(pick("core_days", "MEMORY_CORE_DAYS", 30))
    cfg.archival_days = int(pick("archival_days", "MEMORY_ARCHIVAL_DAYS", 90))
    return cfg

def resolve_data_dir(cli_value: str | None = None, *, create: bool = True) -> str:
    """앱 진입점(cli serve·데스크톱 셸)용 데이터 경로 해석(D43):
    ``--data`` > ``MEMORY_DATA_DIR`` > **``~/.vinc/db``**(설치형 기본 홈).

    라이브러리 기본값(load_config 의 data_dir=None)은 그대로 둔다 — 검증
    (verify.py 무 DB 강등)의 순수성을 앱 기본 경로가 오염하지 않게, 홈 폴더
    적용은 진입점만의 결정이다. 기본 홈일 때만 표준 하위 구조를 만든다:
    ``~/.vinc/{db, packs/, inbox/, backups/}`` — 백업 = 폴더 하나 복사."""
    import pathlib

    explicit = cli_value or os.environ.get("MEMORY_DATA_DIR")
    if explicit:
        return explicit
    root = pathlib.Path.home() / ".vinc"
    if create:
        for sub in ("packs", "inbox", "backups"):
            (root / sub).mkdir(parents=True, exist_ok=True)
    return str(root / "db")
