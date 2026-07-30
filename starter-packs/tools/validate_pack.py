# -*- coding: utf-8 -*-
"""Validate .vincpack files against the Vinc share-pack contract
(Vinc/api/share.py + Vinc/api/fragment.py) plus starter-pack invariants.

Checks per pack:
  P1  vinc_pack == 1, kind == "share", manifest/fragment present
  P2  node types ∈ {record,concept,domain,bridge,episode}; non-empty ids; no dup ids
  P3  id prefix convention: concept:* / bridge:* ; domain ids are bare names
  P4  non-domain nodes have title; domain nodes have NO props (import warning otherwise)
  P5  bridge nodes have no tags/domain keys (Bridge table lacks those columns)
  P6  edge types ∈ enum; endpoints exist in the same fragment;
      pair legality: cc-types = concept→concept (record→concept also legal),
      connects_via = bridge→concept
  P7  manifest.counts match actuals
  P8  language: props.body_md / title should not contain Hangul (release = English)
Cross-pack:
  X1  same node id ⇒ byte-identical JSON in every pack (MERGE SET overwrites on match)

Exit 0 = all PASS. Usage: python validate_pack.py [pack1 pack2 ...] (default: ../*.vincpack)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CC = {"relates_to", "prerequisite_of", "derives_from", "inherits_from"}
EDGE_TYPES = CC | {"connects_via"}
NODE_TYPES = {"record", "concept", "domain", "bridge", "episode"}
HANGUL = re.compile(r"[가-힣]")


def check_pack(path: Path, errors: list[str], warns: list[str]) -> dict:
    pk = f"[{path.name}]"
    pack = json.loads(path.read_text(encoding="utf-8"))
    if pack.get("vinc_pack") != 1:
        errors.append(f"{pk} P1 vinc_pack != 1")
    if pack.get("kind") != "share":
        errors.append(f"{pk} P1 kind != share")
    frag = pack.get("fragment") or {}
    nodes, edges = frag.get("nodes") or [], frag.get("edges") or []

    ids: dict[str, str] = {}
    hangul_hits = 0
    for n in nodes:
        nid, ntype = n.get("id", ""), n.get("type", "")
        if ntype not in NODE_TYPES:
            errors.append(f"{pk} P2 bad node type {ntype!r} ({nid})")
        if not nid:
            errors.append(f"{pk} P2 empty node id")
        if nid in ids:
            errors.append(f"{pk} P2 duplicate node id {nid}")
        ids[nid] = ntype
        if ntype in ("concept", "bridge") and not nid.startswith(ntype + ":"):
            errors.append(f"{pk} P3 id prefix mismatch: {nid} (type {ntype})")
        if ntype == "domain":
            if ":" in nid:
                errors.append(f"{pk} P3 domain id should be bare name: {nid}")
            if n.get("props") is not None:
                errors.append(f"{pk} P4 domain node {nid} carries props (import warning)")
        else:
            if not n.get("title"):
                errors.append(f"{pk} P4 node {nid} missing title")
        if ntype == "bridge":
            for bad in ("tags", "domain"):
                if bad in n:
                    errors.append(f"{pk} P5 bridge {nid} carries '{bad}' (no such column)")
        body = (n.get("props") or {}).get("body_md", "")
        if HANGUL.search(n.get("title", "")) or HANGUL.search(
                (n.get("props") or {}).get("one_liner", "")):
            hangul_hits += 1
        elif HANGUL.search(body):
            hangul_hits += 1
    if hangul_hits:
        warns.append(f"{pk} P8 {hangul_hits} node(s) contain Hangul in title/one_liner/body "
                     f"(expected 0 for the English release)")

    for e in edges:
        et, s, t = e.get("type", ""), e.get("from", ""), e.get("to", "")
        if et not in EDGE_TYPES:
            errors.append(f"{pk} P6 bad edge type {et!r} ({s}→{t})")
            continue
        st, tt = ids.get(s), ids.get(t)
        if st is None or tt is None:
            errors.append(f"{pk} P6 edge endpoint not in fragment: {s}→{t}")
            continue
        if et == "connects_via" and (st, tt) != ("bridge", "concept"):
            errors.append(f"{pk} P6 connects_via must be bridge→concept: {s}({st})→{t}({tt})")
        if et in CC and not (st in ("concept", "record") and tt == "concept"):
            errors.append(f"{pk} P6 {et} must be concept/record→concept: {s}({st})→{t}({tt})")

    counts = (pack.get("manifest") or {}).get("counts") or {}
    actual_c = sum(1 for n in nodes if n.get("type") == "concept")
    if counts.get("concepts") != actual_c:
        errors.append(f"{pk} P7 manifest concepts={counts.get('concepts')} actual={actual_c}")
    if counts.get("edges") != len(edges):
        errors.append(f"{pk} P7 manifest edges={counts.get('edges')} actual={len(edges)}")

    return {n["id"]: json.dumps(n, ensure_ascii=False, sort_keys=True) for n in nodes}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    paths = ([Path(a) for a in args] if args
             else sorted((Path(__file__).parent.parent).glob("*.vincpack")))
    if not paths:
        print("no packs found"); sys.exit(1)
    errors: list[str] = []
    warns: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    for p in paths:
        for nid, blob in check_pack(p, errors, warns).items():
            if nid in seen and seen[nid][1] != blob:
                errors.append(f"X1 node {nid} differs between {seen[nid][0]} and {p.name}")
            seen.setdefault(nid, (p.name, blob))
    for w in warns:
        print("WARN", w)
    if errors:
        for e in errors:
            print("FAIL", e)
        print(f"\n{len(errors)} error(s) across {len(paths)} pack(s)")
        sys.exit(1)
    print(f"PASS — {len(paths)} pack(s), {len(seen)} distinct nodes, 0 contract errors"
          + (f", {len(warns)} warning(s)" if warns else ""))


if __name__ == "__main__":
    main()
