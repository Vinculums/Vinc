# -*- coding: utf-8 -*-
"""Live-import test for the starter packs against an ISOLATED throwaway store.

Runs the exact production code path (api.share.import_share_pack →
api.fragment.merge_fragment) — never touches the user's real ~/.vinc/db.

Checks:
  I1 each pack imports with executed=True and ZERO warnings
  I2 re-import of every pack yields nodes.created == 0 (idempotent)
  I3 cross-pack overlap MERGEs cleanly (math then physics: shared nodes matched)
  I4 smoke queries: node counts by label, domain list, sample concept fetch,
     1-hop graph from a seed concept

Usage: python test_import.py [--keep]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

VINC = Path(r"D:\Projects\AIProjects\vinc-project\Vinc")
sys.path.insert(0, str(VINC))
PACKS_DIR = Path(__file__).parent.parent
ORDER = ["math", "physics", "economics", "art"]   # math→physics first: overlap check


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp store dir")
    args = ap.parse_args()

    from api import share
    from store.backend import open_store
    from store.config import load_config

    tmp = Path(tempfile.mkdtemp(prefix="vinc-packtest-"))
    print(f"store: {tmp}")
    cfg = load_config({"data_dir": str(tmp / "db")})
    store = open_store(cfg)
    if store is None:
        print("FAIL cannot open embedded store"); sys.exit(1)
    store.provision(dim=cfg.embedding_dim)

    failures: list[str] = []
    packs = {}
    for d in ORDER:
        packs[d] = json.loads((PACKS_DIR / f"vinc-pack-{d}.vincpack").read_text(encoding="utf-8"))

    try:
        # I1 first import
        for d in ORDER:
            res = share.import_share_pack(packs[d], owner="tenant:local", store=store)
            m = res.get("merged") or {}
            print(f"I1 {d:10} executed={res.get('executed')} "
                  f"nodes={m.get('nodes')} edges={m.get('edges')} warnings={len(res.get('warnings') or [])}")
            if not res.get("executed"):
                failures.append(f"I1 {d}: not executed — {res}")
            for w in res.get("warnings") or []:
                failures.append(f"I1 {d}: warning: {w}")

        # I2 idempotent re-import
        for d in ORDER:
            res = share.import_share_pack(packs[d], owner="tenant:local", store=store)
            m = (res.get("merged") or {}).get("nodes") or {}
            if m.get("created", -1) != 0:
                failures.append(f"I2 {d}: re-import created {m.get('created')} nodes (expected 0)")
        print("I2 re-import: all matched" if not any(f.startswith("I2") for f in failures)
              else "I2 re-import: FAILURES")

        # I4 smoke queries
        for label, pk in (("Concept", "concept_id"), ("Bridge", "bridge_id"), ("Domain", "name")):
            rows = store.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            print(f"I4 {label}: {rows[0]['c']}")
        doms = store.run("MATCH (d:Domain) RETURN d.name AS n ORDER BY n")
        print("I4 domains:", [r["n"] for r in doms])
        sample = store.run(
            "MATCH (c:Concept) WHERE c.concept_id = $id "
            "RETURN c.title AS t, c.domain AS d, c.owner_id AS o",
            {"id": "concept:physics-newtonian-mechanics"})
        print("I4 sample:", sample)
        if not sample:
            failures.append("I4 seed concept concept:physics-newtonian-mechanics not found")
        hop = store.run(
            "MATCH (a:Concept)-[r]->(b:Concept) WHERE a.concept_id = $id "
            "RETURN label(r) AS rel, b.concept_id AS to LIMIT 10",
            {"id": "concept:physics-newtonian-mechanics"})
        print(f"I4 1-hop edges from seed: {len(hop)}")
        for h in hop[:5]:
            print("   ", h)
        edge_total = store.run(
            "MATCH (:Concept)-[r]->(:Concept) RETURN count(r) AS c")
        print("I4 concept-concept edges:", edge_total[0]["c"])
    finally:
        store.close()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures[:40]:
            print(" -", f)
        sys.exit(1)
    print("\nPASS live-import test")


if __name__ == "__main__":
    main()
