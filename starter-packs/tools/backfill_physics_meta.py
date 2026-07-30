"""One-off: backfill `domain: physics` and missing `created:` in physics learning notes.

DOMAIN-DATA-ANALYSIS §6-1: physics has domain 0/51, created missing in 12
(self-checks 6, 2026-05-10-* 2, READMEs 4).

Rules:
- every .md with frontmatter and no `domain:` gets `domain: physics` (inserted after tags/aliases block, before closing ---)
- files with `date:` but no `created:` get `created: <date value>`
- READMEs (no date at all) get `created: 2026-05-05T00:00:00+09:00` (first batch day)

Idempotent: re-running changes nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:\Projects\AIProjects\weg-9000\weg9000-hub\WORKSPACES\learning\physics")
FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
README_CREATED = "2026-05-05T00:00:00+09:00"


def process(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m:
        return [f"SKIP(no-frontmatter): {path.name}"]
    fm = m.group(1)
    lines = fm.split("\n")
    changes = []

    has_domain = any(re.match(r"\s*domain\s*:", ln) for ln in lines)
    has_created = any(re.match(r"\s*created\s*:", ln) for ln in lines)
    date_val = None
    for ln in lines:
        dm = re.match(r"\s*date\s*:\s*(.+?)\s*$", ln)
        if dm:
            date_val = dm.group(1)
            break

    if not has_domain:
        lines.append("domain: physics")
        changes.append("domain")
    if not has_created:
        lines.append(f"created: {date_val or README_CREATED}")
        changes.append(f"created({'from date' if date_val else 'default'})")

    if not changes:
        return []
    new_fm = "\n".join(lines)
    new_text = text[: m.start(1)] + new_fm + text[m.end(1) :]
    path.write_text(new_text, encoding="utf-8", newline="")
    return [f"FIXED {path.relative_to(ROOT)}: {', '.join(changes)}"]


def main() -> None:
    reports = []
    for p in sorted(ROOT.rglob("*.md")):
        reports.extend(process(p))
    for r in reports:
        print(r)
    print(f"-- {len(reports)} file(s) touched --")


if __name__ == "__main__":
    sys.exit(main())
