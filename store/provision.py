# -*- coding: utf-8 -*-
"""Provision / migrate the embedded store schema (설계 §5).

임베디드 백엔드(LadybugDB/Kùzu)는 스키마 선행이다 — 노드/관계 테이블을 먼저
선언한다. `provision(cfg, dry_run=True)` 는 렌더된 DDL 문장을 DB 없이 반환하고
(테스트·CI), `dry_run=False` 는 `open_store().provision()` 로 멱등 적용한다.

    python -m store.provision --dry-run
    python -m store.provision --apply

"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from .config import StoreConfig, load_config
from .embedded_schema import render_embedded_ddl


@dataclass
class ProvisionResult:
    dry_run: bool
    statements: list[str]
    applied: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "statement_count": len(self.statements),
            "applied": self.applied,
            "errors": self.errors or [],
            "statements": self.statements,
        }


def plan(cfg: StoreConfig) -> list[str]:
    """이 config 의 임베디드 스키마 DDL 문장(순수·무 I/O)."""
    return render_embedded_ddl(dim=cfg.embedding_dim)


def provision(cfg: StoreConfig, *, dry_run: bool = True) -> ProvisionResult:
    statements = plan(cfg)
    if dry_run:
        return ProvisionResult(dry_run=True, statements=statements)

    from .backend import open_store
    store = open_store(cfg)
    if store is None:
        return ProvisionResult(dry_run=False, statements=statements, applied=0,
                               errors=["no reachable embedded store (드라이버 미설치?)"])
    try:
        applied = store.provision(dim=cfg.embedding_dim)
    finally:
        store.close()
    return ProvisionResult(dry_run=False, statements=statements, applied=len(applied))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provision the embedded store schema (§5)")
    ap.add_argument("--apply", action="store_true",
                    help="apply to the embedded store (default: dry-run)")
    ap.add_argument("--embedding-model", default=None,
                    help="override embedding model (dim source)")
    args = ap.parse_args(argv)

    overrides = {}
    if args.embedding_model:
        overrides["embedding_model"] = args.embedding_model
    cfg = load_config(overrides)

    result = provision(cfg, dry_run=not args.apply)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if not result.errors else 2


if __name__ == "__main__":
    sys.exit(main())
