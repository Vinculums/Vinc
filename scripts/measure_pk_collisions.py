#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-0 measurement (v0.8.4 axis 1): node PK collision and legacy-owner exposure.

Read-only. Opens the store through store/backend.py open_store (the only
opener, per [[vinc.spec.dataplane sec 1]]) and reports, per label, the
numbers vinc.release.0-8-4 PR-0 asks for before any namespacing code lands.
No production write path is touched.

Per label (Concept, Record, Episode, Decision, Bridge, Entity):
  - owner_id distribution, including empty/NULL
  - rows owned by one of the three legacy default owners
    (tenant:local, user:local, tenant:shared)
  - rows whose primary key or a string field already contains U+001F
    (the separator design A would use to compose owner|slug)
  - slugs (primary key values) held by more than one owner, with the owners
  - rows where title equals the primary key (fragment ingest's "no title
    given, seed it from the id" fallback -- see api/fragment.py _merge_node)

Bridge also gets: rows with an empty owner, and rows with an empty domain.
Bridge has no `domain` column (it has source_domain/target_domain); every
query plan and layout function in api/query.py treats source_domain as the
bridge's domain (see bridge_rows_plan), so that is what this script checks.

Also: concept:doc.* mirror ids, counted by the doc_id format they embed --
hashed (make_doc_id / doc_id_for_origin: "d" + 16 hex chars) vs unhashed
(an authored dotted id such as "vinc.spec.dataplane").

Usage:
    python scripts/measure_pk_collisions.py [--data-dir PATH] [--json]

Note on the slug-collision count: the embedded backend enforces primary-key
uniqueness per label at the engine level (verified empirically: a second
CREATE with a duplicate primary key raises a RuntimeError). So on any single
store today this count is always zero -- there is exactly one physical row
per slug, which is the whole reason H1 is a *silent* defect: the collision
never becomes visible data, only a dropped write and a warning nobody kept.
This script still runs the query (schema-correct, ready to catch a real
positive once PR-D's composed keys make same-slug/different-owner rows
possible), and callers should not read a zero here as "no collisions ever
happened" -- only as "none are visible in this store's data."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.backend import open_store          # noqa: E402
from store.config import load_config           # noqa: E402
from store.embedded_schema import _NODE_TABLES  # noqa: E402  (schema SSoT, script-internal use)

LABELS = ["Concept", "Record", "Episode", "Decision", "Bridge", "Entity"]
LEGACY_OWNERS = ["tenant:local", "user:local", "tenant:shared"]
SEP = "\x1f"
_HASHED_DOC_ID = re.compile(r"^d[0-9a-f]{16}$")
_DOC_CONCEPT_PREFIX = "concept:doc."


def _pk_col(label: str) -> str:
    return _NODE_TABLES[label]["pk"]


def _string_cols(label: str) -> list[str]:
    # ponytail: only STRING columns are scanned for the separator byte.
    # STRING[] (tags) is skipped -- Cypher CONTAINS does not apply to a list
    # column and nothing writes \x1f into a list field today. Widen this if
    # that ever changes.
    return [c for c, t in _NODE_TABLES[label]["cols"] if t == "STRING"]


def _count(rows: list[dict]) -> int:
    return rows[0]["c"] if rows else 0


def owner_distribution(store, label: str) -> dict:
    rows = store.run(f"MATCH (n:{label}) RETURN n.owner_id AS owner, count(*) AS c")
    return {(r["owner"] or ""): r["c"] for r in rows}


def legacy_owner_rows(store, label: str) -> dict:
    rows = store.run(
        f"MATCH (n:{label}) WHERE n.owner_id IN $legacy "
        "RETURN n.owner_id AS owner, count(*) AS c",
        {"legacy": LEGACY_OWNERS})
    return {r["owner"]: r["c"] for r in rows}


def separator_rows(store, label: str) -> int:
    pk = _pk_col(label)
    cols = [pk] + [c for c in _string_cols(label) if c != pk]
    checks = " OR ".join(f"n.{c} CONTAINS $sep" for c in cols)
    rows = store.run(f"MATCH (n:{label}) WHERE {checks} RETURN count(*) AS c", {"sep": SEP})
    return _count(rows)


def slug_collisions(store, label: str) -> list[dict]:
    """Slugs (pk values) held by more than one owner. See module docstring:
    structurally always empty pre-migration (engine-enforced PK uniqueness),
    kept ready for PR-D onward."""
    pk = _pk_col(label)
    rows = store.run(
        f"MATCH (n:{label}) WHERE n.owner_id IS NOT NULL "
        f"WITH n.{pk} AS slug, collect(DISTINCT n.owner_id) AS owners "
        "WHERE size(owners) > 1 RETURN slug, owners")
    return [{"slug": r["slug"], "owners": r["owners"]} for r in rows]


def title_equals_id(store, label: str) -> int:
    pk = _pk_col(label)
    rows = store.run(f"MATCH (n:{label}) WHERE n.title = n.{pk} RETURN count(*) AS c")
    return _count(rows)


def bridge_empty_owner(store) -> int:
    rows = store.run(
        "MATCH (n:Bridge) WHERE n.owner_id IS NULL OR n.owner_id = '' RETURN count(*) AS c")
    return _count(rows)


def bridge_empty_domain(store) -> int:
    # Bridge's de-facto domain is source_domain (api/query.py bridge_rows_plan
    # projects it as `domain`); there is no plain `domain` column on Bridge.
    rows = store.run(
        "MATCH (n:Bridge) WHERE n.source_domain IS NULL OR n.source_domain = '' "
        "RETURN count(*) AS c")
    return _count(rows)


def doc_mirror_id_formats(store) -> dict:
    rows = store.run(
        "MATCH (n:Concept) WHERE n.concept_id STARTS WITH $prefix RETURN n.concept_id AS id",
        {"prefix": _DOC_CONCEPT_PREFIX})
    hashed = unhashed = 0
    for r in rows:
        rest = r["id"][len(_DOC_CONCEPT_PREFIX):]
        if _HASHED_DOC_ID.match(rest):
            hashed += 1
        else:
            unhashed += 1
    return {"hashed": hashed, "unhashed": unhashed, "total": len(rows)}


def measure(store) -> dict:
    out: dict = {"labels": {}}
    for label in LABELS:
        entry = {
            "owner_distribution": owner_distribution(store, label),
            "legacy_owner_rows": legacy_owner_rows(store, label),
            "separator_rows": separator_rows(store, label),
            "slug_collisions": slug_collisions(store, label),
            "title_equals_id": title_equals_id(store, label),
        }
        if label == "Bridge":
            entry["empty_owner"] = bridge_empty_owner(store)
            entry["empty_domain"] = bridge_empty_domain(store)
        out["labels"][label] = entry
    out["doc_mirror_ids"] = doc_mirror_id_formats(store)
    return out


def format_text(result: dict) -> str:
    lines: list[str] = []
    for label in LABELS:
        e = result["labels"][label]
        lines.append(f"== {label} ==")
        dist = e["owner_distribution"]
        total = sum(dist.values())
        lines.append(f"  rows: {total}")
        lines.append("  owner distribution:")
        for owner, c in sorted(dist.items(), key=lambda kv: -kv[1]):
            shown = owner if owner else "(empty)"
            lines.append(f"    {shown}: {c}")
        legacy = e["legacy_owner_rows"]
        lines.append(f"  legacy default owner rows: {sum(legacy.values())} {legacy or '{}'}")
        lines.append(f"  rows with U+001F in id or a string field: {e['separator_rows']}")
        coll = e["slug_collisions"]
        lines.append(f"  slugs held by more than one owner: {len(coll)}")
        for row in coll:
            lines.append(f"    {row['slug']}: {row['owners']}")
        lines.append(f"  rows where title == id: {e['title_equals_id']}")
        if label == "Bridge":
            lines.append(f"  Bridge rows with empty owner: {e['empty_owner']}")
            lines.append(f"  Bridge rows with empty domain (source_domain): {e['empty_domain']}")
        lines.append("")
    d = result["doc_mirror_ids"]
    lines.append("== concept:doc.* mirror ids ==")
    lines.append(f"  total: {d['total']}  hashed: {d['hashed']}  unhashed (dotted): {d['unhashed']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", help="store data directory (default: env/config resolution)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of plain text")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data_dir} if args.data_dir else None)
    store = open_store(cfg)
    if store is None:
        print("could not open the store (see store/backend.py open_store)", file=sys.stderr)
        return 1
    try:
        result = measure(store)
    finally:
        store.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
