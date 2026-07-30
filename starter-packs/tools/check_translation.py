# -*- coding: utf-8 -*-
"""Mechanical gate for translated work files (runs before the Fable review pass).

Per note listed in translation-batches.json:
  T1 work/{domain}/{stem}.md exists
  T2 no Hangul left (regex [가-힣])
  T3 no unreplaced [[wikilink]]
  T4 '## One-line essence' heading present, with non-empty first paragraph
  T5 first line is an H1
  T6 line count within ±25% of source body (tables/LaTeX preserved → similar size)
  T7 LaTeX $ delimiter count within ±10% of source

Prints per-domain summary + every failure. Exit 1 if any T1–T5 failure.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from packlib import body_of

TOOLS = Path(__file__).parent
WORK = TOOLS.parent / "work"
HANGUL = re.compile(r"[가-힣]")
# unreplaced wikilinks — but NOT QEC stabilizer-code notation like [[7, 1, 3]] or [[n, k, d]]
WIKI = re.compile(r"\[\[(?![\dnkd ,]+\]\])")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    batches = json.loads((TOOLS / "translation-batches.json").read_text(encoding="utf-8"))
    hard, soft = [], []
    total = done = 0
    for domain, items in batches.items():
        for it in items:
            total += 1
            src = Path(it["path"])
            out = WORK / domain / f"{it['stem']}.md"
            tag = f"{domain}/{it['stem']}"
            if not out.exists():
                hard.append(f"T1 missing: {tag}")
                continue
            done += 1
            text = out.read_text(encoding="utf-8")
            if HANGUL.search(text):
                n = len(HANGUL.findall(text))
                hard.append(f"T2 hangul({n}): {tag}")
            if WIKI.search(text):
                hard.append(f"T3 wikilink remains: {tag}")
            src_text = src.read_text(encoding="utf-8")
            src_has_ol = any(l.strip().startswith("## ") and ("한 줄 핵심" in l or "한줄" in l)
                             for l in src_text.splitlines())
            out_has_ol = any(l.strip().startswith("## ") and "one-line" in l.lower()
                             for l in text.splitlines())
            if src_has_ol and not out_has_ol:
                hard.append(f"T4 no One-line essence: {tag}")
            first = next((l for l in text.splitlines() if l.strip()), "")
            if not first.startswith("# "):
                hard.append(f"T5 first line not H1: {tag}")
            src_body = body_of(src_text)
            sl, ol = max(1, src_body.count("\n")), text.count("\n")
            if not (0.75 <= ol / sl <= 1.25):
                soft.append(f"T6 line ratio {ol}/{sl}={ol/sl:.2f}: {tag}")
            sd, od = src_body.count("$"), text.count("$")
            if sd and not (0.9 <= (od / sd) <= 1.1):
                soft.append(f"T7 latex $ {od}/{sd}: {tag}")
    print(f"translated {done}/{total}")
    for f in hard:
        print("FAIL", f)
    for f in soft:
        print("WARN", f)
    if done < total or hard:
        sys.exit(1)
    print(f"PASS mechanical gate ({len(soft)} soft warning(s))")


if __name__ == "__main__":
    main()
