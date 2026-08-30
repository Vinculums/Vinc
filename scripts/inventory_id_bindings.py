#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR-0 inventory (v0.8.4 axis 1): where node ids get bound into Cypher today.

Read-only static scan of api/, store/ and engines/ (no store is opened, no
code is imported). This is the checklist PR-C works through when it composes
owner+slug at every read/write binding site (see vinc.plan.node-pk-namespacing
sec 7 and vinc.release.0-8-4 PR-C). It answers the gap the plan calls out
between its own count (~164 sites) and an earlier estimate (~60).

Patterns, one regex sweep per line (mechanical, not an AST parse -- a match
spanning a line break is not caught; a site that needs review because of
this scan is still a site, so a wider net here is worth more than a clever
one):

  dollar_id            literal `$id` Cypher parameter
  in_dollar_ids        `IN $ids` list-parameter binding
  starts_with          any `STARTS WITH` prefix filter, literal or
                       parameterized (both break once the physical key is
                       owner+slug -- neither matches on a composed key --
                       PR-C plan)
  split_colon          `.split(":")` / `.split(':')` -- Python-side id parsing
  kind_prefix_literal  a legacy-kind prefix string literal anywhere in the
                       file (concept:/record:/episode:/decision:/bridge:/
                       entity:)
  alias_id_eq_alias_id `<alias>.id = <alias>.id` -- node-to-node id comparison

Usage:
    python scripts/inventory_id_bindings.py [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("api", "store", "engines")

_KINDS = ("concept", "record", "episode", "decision", "bridge", "entity")

_PATTERNS: dict[str, re.Pattern] = {
    "dollar_id": re.compile(r"\$id\b"),
    "in_dollar_ids": re.compile(r"IN\s+\$ids\b"),
    "starts_with": re.compile(r"STARTS WITH"),
    "split_colon": re.compile(r"""\.split\(\s*["']:["']\s*(?:,\s*\d+\s*)?\)"""),
    "kind_prefix_literal": re.compile(
        r"""['"](""" + "|".join(_KINDS) + r"""):"""),
    "alias_id_eq_alias_id": re.compile(
        r"\b[A-Za-z_][A-Za-z0-9_]*\.id\s*=\s*[A-Za-z_][A-Za-z0-9_]*\.id\b"),
}


def scan_file(path: Path) -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {name: [] for name in _PATTERNS}
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pat in _PATTERNS.items():
            if pat.search(line):
                hits[name].append(lineno)
    return hits


def scan() -> dict[str, list[str]]:
    """pattern name -> sorted list of 'relative/path.py:lineno' hits."""
    grouped: dict[str, list[str]] = {name: [] for name in _PATTERNS}
    for dirname in SCAN_DIRS:
        base = REPO_ROOT / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            hits = scan_file(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            for name, linenos in hits.items():
                grouped[name].extend(f"{rel}:{n}" for n in linenos)
    return grouped


def format_text(grouped: dict[str, list[str]]) -> str:
    lines: list[str] = []
    total = 0
    for name, hits in grouped.items():
        lines.append(f"== {name} ({len(hits)}) ==")
        lines.extend(f"  {h}" for h in hits)
        lines.append("")
        total += len(hits)
    lines.append(f"total: {total}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of plain text")
    args = ap.parse_args()

    grouped = scan()
    if args.json:
        out = dict(grouped)
        out["total"] = sum(len(v) for v in grouped.values())
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(format_text(grouped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
