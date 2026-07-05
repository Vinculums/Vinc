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
    # 임베디드 드라이버 선호(계보 포크 — R1 교체 보험). None=자동(ladybug→kuzu).
    #   "kuzu": 벡터/FTS 확장이 정적 링크(오프라인 즉시). "ladybug": v0.18.0(확장 별도).
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
