# -*- coding: utf-8 -*-
"""Store layer — Neo4j + vector provisioning, schema, and migrations.

The 6 engines each open their own Neo4j driver (preserved as-is for backward
compatibility). This layer centralizes what they all *assume* exists: the
constraints and the vector / fulltext indexes. ``provision`` applies them
idempotently; ``--dry-run`` prints the Cypher without a database.
"""
from __future__ import annotations

from .config import StoreConfig, load_config
from .schema import MIGRATIONS, Migration

__all__ = ["StoreConfig", "load_config", "MIGRATIONS", "Migration"]
