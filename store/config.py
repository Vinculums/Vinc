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
    # 임베디드 드라이버 선호(계보 포크 — R1 교체 보험). None=기본 **kuzu**(D14):
    #   벡터/FTS/algo 정적 링크로 오프라인 즉시(§1). "ladybug"=v0.18.0 옵트인(확장
    #   런타임 다운로드 — 오프라인 배포 시 번들 필요, §9).
    #
    #   **LadybugDB 전환(D14 재평가, PLAN-GRAPHRAG-LADYBUG-2026-07)**: 활성 후계로
    #   전환 방향은 확정이나 **G0 게이트(오프라인 확장 번들 검증) 통과가 전제**다 —
    #   ladybug 확장은 런타임 다운로드형이라 "설치 즉시 오프라인" 불변식(§1)을 지키려면
    #   번들 경로 확증이 필요하다. G0 미통과 동안 기본은 kuzu 유지(정적 링크). 전환은
    #   저장 포맷 비호환이라 파일 복사가 아닌 **논리 이관**(store/migrate.py — 두
    #   프로세스: kuzu export → ladybug import)이다. 이 노브가 프로세스별 드라이버
    #   고정점(MEMORY_EMBEDDED_DRIVER).
    embedded_driver: str | None = None
    data_dir: str | None = None          # 임베디드 스토어 데이터 디렉토리(§3-2)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # retention tiers (days) for the 3-tier recall memory model
    core_days: int = 30
    archival_days: int = 90
    tenant_id: str | None = None         # 단일 사용자: 상수 tenant:local (§3-3)
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
        embedded_driver=pick("embedded_driver", "MEMORY_EMBEDDED_DRIVER", None) or None,
        data_dir=pick("data_dir", "MEMORY_DATA_DIR", None) or None,
        embedding_model=pick("embedding_model", "MEMORY_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        tenant_id=overrides.get("tenant_id"),
    )
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
