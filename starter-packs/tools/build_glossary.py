# -*- coding: utf-8 -*-
"""Build/refresh glossary.json — the ko→en title/slug SSoT for pack conversion.

Draft heuristics:
- persons: artist_name frontmatter is already English → title_en, slug from file stem
- bridges: H1 is usually already English → use it when ASCII
- concepts: first mostly-ASCII alias wins; else H1 if ASCII; else needs_review=true

Existing glossary entries with needs_review=false are preserved (manual fixes win).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from packlib import (collect_bridges, collect_concepts, collect_persons,
                     dump_json, is_ascii, load_json, slugify)

GLOSSARY = Path(__file__).parent / "glossary.json"


def mostly_ascii(s: str) -> bool:
    if not s:
        return False
    non = sum(1 for ch in s if ord(ch) > 127)
    return non <= max(1, len(s) // 10)


def pick_en(note) -> str:
    if note.kind == "person":
        return note.fm.get("artist_name", "") or note.stem.replace("-", " ")
    if mostly_ascii(note.title_ko):
        return note.title_ko
    for a in note.fm.get("aliases") or []:
        if mostly_ascii(a) and len(a) > 2:
            return a
    if is_ascii(note.stem.split("-", 1)[-1]):
        return note.stem.split("-", 1)[-1].replace("-", " ")
    return ""


def main() -> None:
    old = load_json(GLOSSARY) if GLOSSARY.exists() else {}
    notes = collect_concepts() + collect_persons() + collect_bridges()
    gl: dict[str, dict] = {}
    stats = {"kept": 0, "auto": 0, "review": 0}
    seen_slugs: dict[str, str] = {}
    for n in notes:
        key = f"{n.domain}/{n.stem}"
        prev = old.get(key)
        if prev and not prev.get("needs_review"):
            gl[key] = prev
            stats["kept"] += 1
        else:
            en = pick_en(n)
            slug = slugify(en) if en else slugify(n.stem)
            gl[key] = {
                "kind": n.kind, "title_ko": n.title_ko, "title_en": en,
                "slug": slug, "needs_review": not en,
            }
            stats["auto" if en else "review"] += 1
        slug = gl[key]["slug"]
        # slug uniqueness within domain (id = concept:{domain}-{slug})
        skey = f"{n.domain}:{slug}" if n.kind != "bridge" else f"bridge:{slug}"
        if skey in seen_slugs and seen_slugs[skey] != key:
            gl[key]["slug"] = slug + "-" + slugify(n.stem)[:12]
            gl[key]["slug_deduped"] = True
        seen_slugs.setdefault(skey, key)

    dump_json(GLOSSARY, gl)
    print(f"glossary: {len(gl)} entries | kept={stats['kept']} auto={stats['auto']} needs_review={stats['review']}")
    for k, v in gl.items():
        if v.get("needs_review"):
            print(f"  REVIEW {k}: {v['title_ko']}")


if __name__ == "__main__":
    main()
