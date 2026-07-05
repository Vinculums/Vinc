# -*- coding: utf-8 -*-
"""Store configuration — tenant-agnostic Neo4j + embedding settings.

Resolution order for each field: explicit argument > environment variable >
``profile.yml`` default > built-in default. Per-tenant overrides are layered on
top by the server-path CP adapter (not bundled in Vinc — WO §3 F3).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Embedding dimensions are model-dependent (must match embed_pipeline.py):
#   voyage-3 (anthropic) = 1024. English-first (v0.4, design §8):
#   local-en = bge-base-en-v1.5 (768), local-en-large = bge-large-en-v1.5 (1024).
#   "local" (legacy multilingual mpnet 768) retained for back-compat only.
EMBEDDING_DIM_BY_MODEL = {
    "anthropic": 1024,
    "local-en": 768,          # bge-base-en-v1.5 — desktop default (design §8, D10)
    "local-en-large": 1024,   # bge-large-en-v1.5 — quality option
    "local": 768,             # legacy multilingual mpnet (back-compat)
}
DEFAULT_EMBEDDING_MODEL = "anthropic"


@dataclass
class StoreConfig:
    # backend selects the GraphStore implementation (store/backend.py):
    #   "neo4j"    — server backend (default; migration path + server deployments)
    #   "embedded" — in-process LadybugDB (Vinc desktop default — P1)
    backend: str = "neo4j"
    uri: str = "neo4j://127.0.0.1:7687"
    user: str = "neo4j"
    password: str = ""
    database: str | None = None
    data_dir: str | None = None          # embedded backend data directory (P1)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # retention tiers (days) for the 3-tier recall memory model
    core_days: int = 30
    archival_days: int = 90
    tenant_id: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIM_BY_MODEL.get(self.embedding_model, 1024)

    def auth(self) -> tuple[str, str]:
        return (self.user, self.password)

    def redacted(self) -> dict:
        d = self.__dict__.copy()
        d["password"] = "***" if self.password else ""
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
        backend=pick("backend", "MEMORY_STORE_BACKEND", "neo4j"),
        uri=pick("uri", "NEO4J_URI", "neo4j://127.0.0.1:7687"),
        user=pick("user", "NEO4J_USER", "neo4j"),
        password=pick("password", "NEO4J_PASSWORD", ""),
        database=pick("database", "NEO4J_DATABASE", None) or None,
        data_dir=pick("data_dir", "MEMORY_DATA_DIR", None) or None,
        embedding_model=pick("embedding_model", "MEMORY_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        tenant_id=overrides.get("tenant_id"),
    )
    cfg.core_days = int(pick("core_days", "MEMORY_CORE_DAYS", 30))
    cfg.archival_days = int(pick("archival_days", "MEMORY_ARCHIVAL_DAYS", 90))
    return cfg
