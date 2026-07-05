# -*- coding: utf-8 -*-
"""Provision / migrate a memory store.

``provision(cfg, dry_run=True)`` returns the ordered Cypher statements without
touching a database (used in tests and CI). With ``dry_run=False`` it opens a
Neo4j driver (imported lazily) and applies each statement idempotently.

    python -m store.provision --dry-run
    python -m store.provision --apply        # requires NEO4J_PASSWORD + neo4j pkg
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from .config import StoreConfig, load_config
from .schema import MIGRATIONS, render


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
    """The ordered Cypher statements for this config (pure, no I/O)."""
    return render(MIGRATIONS, dim=cfg.embedding_dim)


def _connect(cfg: StoreConfig):
    try:
        from neo4j import GraphDatabase  # type: ignore
    except ImportError:
        raise RuntimeError("neo4j package not installed; run `pip install neo4j`")
    if not cfg.password:
        raise RuntimeError("NEO4J_PASSWORD is required to apply migrations")
    driver = GraphDatabase.driver(cfg.uri, auth=cfg.auth())
    driver.verify_connectivity()
    return driver


def provision(cfg: StoreConfig, *, dry_run: bool = True) -> ProvisionResult:
    statements = plan(cfg)
    if dry_run:
        return ProvisionResult(dry_run=True, statements=statements)

    driver = _connect(cfg)
    applied = 0
    errors: list[str] = []
    try:
        sess_kwargs = {"database": cfg.database} if cfg.database else {}
        with driver.session(**sess_kwargs) as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                    applied += 1
                except Exception as exc:  # pragma: no cover - needs live db
                    errors.append(f"{stmt[:60]}...: {exc}")
    finally:
        driver.close()
    return ProvisionResult(dry_run=False, statements=statements, applied=applied, errors=errors)


# ── Ownership backfill (non-destructive) ─────────────────────────────────────

# Episodes ingested before the hybrid-ownership model have no owner_id. Until a
# real owner is known they belong to the tenant-shared pool, never to a person.
SHARED_OWNER = "tenant:shared"


def backfill_owner_plan(*, owner: str = SHARED_OWNER, tenant: str | None = None) -> list[tuple[str, dict]]:
    """(cypher, params) statements that set ``owner_id``/``tenant_id`` only where
    they are missing. Non-destructive: existing values are never overwritten."""
    stmts: list[tuple[str, dict]] = [
        ("MATCH (e:Episode) WHERE e.owner_id IS NULL SET e.owner_id = $owner",
         {"owner": owner}),
    ]
    if tenant:
        stmts.append(
            ("MATCH (e:Episode) WHERE e.tenant_id IS NULL SET e.tenant_id = $tenant",
             {"tenant": tenant})
        )
    return stmts


def backfill_owner(cfg: StoreConfig, *, owner: str = SHARED_OWNER,
                   tenant: str | None = None, dry_run: bool = True) -> dict:
    """Apply :func:`backfill_owner_plan` to a live store (or report it on dry-run).

    ``tenant`` defaults to ``cfg.tenant_id`` so an existing single-tenant store
    is stamped with its own id. Idempotent — re-running touches nothing once
    every Episode has an owner."""
    tenant = tenant if tenant is not None else cfg.tenant_id
    statements = backfill_owner_plan(owner=owner, tenant=tenant)
    rendered = [s for s, _ in statements]
    if dry_run:
        return {"dry_run": True, "owner": owner, "tenant": tenant,
                "statements": rendered, "updated": 0}

    driver = _connect(cfg)
    updated = 0
    try:
        sess_kwargs = {"database": cfg.database} if cfg.database else {}
        with driver.session(**sess_kwargs) as session:  # pragma: no cover - needs live db
            for cypher, params in statements:
                res = session.run(cypher, **params)
                updated += res.consume().counters.properties_set
    finally:
        driver.close()
    return {"dry_run": False, "owner": owner, "tenant": tenant,
            "statements": rendered, "updated": updated}


def backfill_domain_plan() -> list[tuple[str, dict]]:
    """(cypher, params) that derive ``domain`` where it is missing — for stores
    loaded by pre-domain engines (v1). Episode: domain ← workspace (when set).
    Non-destructive; Concepts already carry domain from their ingester."""
    return [
        ("MATCH (e:Episode) WHERE e.domain IS NULL AND e.workspace IS NOT NULL "
         "AND e.workspace <> '' SET e.domain = e.workspace", {}),
    ]


def backfill_domain(cfg: StoreConfig, *, dry_run: bool = True) -> dict:
    """Apply :func:`backfill_domain_plan` (or report it on dry-run). Idempotent."""
    statements = backfill_domain_plan()
    rendered = [s for s, _ in statements]
    if dry_run:
        return {"dry_run": True, "statements": rendered, "updated": 0}
    driver = _connect(cfg)
    updated = 0
    try:
        sess_kwargs = {"database": cfg.database} if cfg.database else {}
        with driver.session(**sess_kwargs) as session:  # pragma: no cover - needs live db
            for cypher, params in statements:
                res = session.run(cypher, **params)
                updated += res.consume().counters.properties_set
    finally:
        driver.close()
    return {"dry_run": False, "statements": rendered, "updated": updated}


def drop_plan(cfg: StoreConfig) -> list[str]:
    """The Cypher to drop this tenant's database. Refuses non-``mem_`` targets."""
    db = cfg.database
    if not db:
        raise RuntimeError("cfg.database (mem_{tenant}) is required to drop")
    if not db.startswith("mem_"):
        raise RuntimeError(f"refusing to drop non-tenant database: {db}")
    return [f"DROP DATABASE {db} IF EXISTS"]


def drop_database(cfg: StoreConfig, *, dry_run: bool = True) -> dict:
    """Stage-2 purge of a tenant's Neo4j database (irreversible).

    Validates the target is a ``mem_`` tenant DB, then (when not dry_run) runs
    ``DROP DATABASE`` against the ``system`` database with a lazily-imported
    driver. (Server-path multitenancy only — not used by Vinc single-user.)
    """
    statements = drop_plan(cfg)  # safety-validates cfg.database
    if dry_run:
        return {"dry_run": True, "database": cfg.database, "statements": statements, "dropped": False}
    driver = _connect(cfg)
    try:
        with driver.session(database="system") as session:  # pragma: no cover - needs live db
            for stmt in statements:
                session.run(stmt)
    finally:
        driver.close()
    return {"dry_run": False, "database": cfg.database, "statements": statements, "dropped": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provision a memory store (Neo4j schema)")
    ap.add_argument("--apply", action="store_true", help="apply to a live Neo4j (default: dry-run)")
    ap.add_argument("--embedding-model", default=None, choices=["anthropic", "local", None])
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--drop", action="store_true",
                    help="DROP the tenant database mem_{tenant} (requires --tenant; --apply to execute)")
    ap.add_argument("--backfill-owner", action="store_true",
                    help="stamp owner_id/tenant_id on Episodes that lack them (non-destructive)")
    ap.add_argument("--backfill-domain", action="store_true",
                    help="derive Episode.domain from workspace where missing (v1-store migration)")
    ap.add_argument("--owner", default=SHARED_OWNER,
                    help=f"owner_id to assign to un-owned Episodes (default: {SHARED_OWNER})")
    args = ap.parse_args(argv)

    if args.backfill_owner or args.backfill_domain:
        cfg = load_config({"tenant_id": args.tenant} if args.tenant else None)
        out = {}
        if args.backfill_owner:
            out["owner"] = backfill_owner(cfg, owner=args.owner, dry_run=not args.apply)
        if args.backfill_domain:
            out["domain"] = backfill_domain(cfg, dry_run=not args.apply)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.drop:
        if not args.tenant:
            print(json.dumps({"status": "error", "error": "--drop requires --tenant"}),
                  file=sys.stderr)
            return 2
        cfg = load_config({"database": f"mem_{args.tenant}", "tenant_id": args.tenant})
        try:
            res = drop_database(cfg, dry_run=not args.apply)
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False),
                  file=sys.stderr)
            return 2
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    overrides = {}
    if args.embedding_model:
        overrides["embedding_model"] = args.embedding_model
    if args.tenant:
        overrides["tenant_id"] = args.tenant
    cfg = load_config(overrides)

    try:
        result = provision(cfg, dry_run=not args.apply)
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
