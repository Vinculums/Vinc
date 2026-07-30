# -*- coding: utf-8 -*-
"""Shared parsing library for weg-9000 learning notes → Vinc starter packs.

Parsing logic ported from weg9000-core/scripts/{concepts,bridges,persons}_to_neo4j.py
(same regexes/filters), extended for cross-domain wikilink resolution and the
Vinc share-pack fragment contract (Vinc/api/fragment.py, Vinc/api/share.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HUB = Path(r"D:\Projects\AIProjects\weg-9000\weg9000-hub")
LEARNING = HUB / "WORKSPACES" / "learning"
DOMAINS = ["physics", "math", "economics", "art"]
INCLUDE_SUBDIRS = {"foundations", "modern", "frontier", "theory", "deep", "non-western", "cross-arts"}
BRIDGE_DIRS = [
    (LEARNING / "math" / "physics-bridges", "math", "physics"),
    (LEARNING / "math" / "cross-domain-bridges", "math", None),
    (LEARNING / "physics" / "cross-domain-bridges", "physics", None),
]
ARTISTS_DIR = LEARNING / "art" / "artists"
INFLUENCE_GRAPH = ARTISTS_DIR / "influence-graph-v1.md"

_NUMBERED = re.compile(r"^\d{2,3}-")
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_INFL = re.compile(r"^####\s+(.+?)\s*→\s*(.+?)\s*\(([^)]*)\)", re.MULTILINE)
_TYPES = re.compile(r"T\d{1,2}")

# *_inputs frontmatter key → concept domain the stems live in
INPUT_KEYS = {
    "math_inputs": "math",
    "physics_inputs": "physics",
    "econ_inputs": "economics",
    "economics_inputs": "economics",
    "art_inputs": "art",
}


def parse_fm(text: str) -> dict[str, Any]:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, _, v = line.partition(":")
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                fm[k.strip()] = [x.strip().strip('"').strip("'")
                                 for x in v[1:-1].split(",") if x.strip()]
            else:
                fm[k.strip()] = v.strip('"').strip("'")
    return fm


def body_of(text: str) -> str:
    return _FM_RE.sub("", text, count=1)


def h1(text: str) -> str:
    for line in body_of(text).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def one_liner(text: str) -> str:
    body = body_of(text).splitlines()
    for i, line in enumerate(body):
        s = line.strip()
        if s.startswith("## ") and ("한 줄 핵심" in s or "한줄" in s or "one-line" in s.lower()):
            for nxt in body[i + 1:]:
                if nxt.strip():
                    return nxt.strip().lstrip("*#①").strip()[:400]
    return ""


def slugify(s: str) -> str:
    s = re.sub(r"^\d{2,3}-", "", s)
    s = re.sub(r"[^0-9A-Za-z]+", "-", s).strip("-").lower()
    return s


def is_ascii(s: str) -> bool:
    return all(ord(ch) < 128 for ch in s)


@dataclass
class Note:
    kind: str            # concept | person | bridge
    domain: str          # owning domain
    stem: str            # file stem (e.g. 01-뉴턴역학, Bach-J-S)
    topic: str           # parent dir (foundations/modern/…/artists/bridge dir)
    path: Path
    fm: dict[str, Any]
    title_ko: str
    one_liner_ko: str
    text: str
    # filled later
    slug: str = ""
    title_en: str = ""
    node_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def collect_concepts() -> list[Note]:
    out: list[Note] = []
    for domain in DOMAINS:
        ddir = LEARNING / domain
        for md in sorted(ddir.rglob("*.md")):
            parent = md.parent.name
            if parent not in INCLUDE_SUBDIRS:
                continue
            if not _NUMBERED.match(md.stem):
                continue
            if "README" in md.stem or md.stem == "index":
                continue
            text = md.read_text(encoding="utf-8")
            fm = parse_fm(text)
            out.append(Note(
                kind="concept", domain=domain, stem=md.stem, topic=parent,
                path=md, fm=fm, title_ko=h1(text) or (fm.get("aliases") or [md.stem])[0],
                one_liner_ko=one_liner(text), text=text,
            ))
    return out


def collect_persons() -> list[Note]:
    out: list[Note] = []
    if not ARTISTS_DIR.exists():
        return out
    for md in sorted(ARTISTS_DIR.glob("*.md")):
        if md.stem in ("index",) or "README" in md.stem or md.stem.startswith("influence-graph"):
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_fm(text)
        if fm.get("type") != "artist-note":
            continue
        out.append(Note(
            kind="person", domain="art", stem=md.stem, topic="artists",
            path=md, fm=fm,
            title_ko=fm.get("artist_kr") or h1(text) or md.stem,
            one_liner_ko=one_liner(text), text=text,
        ))
    return out


def collect_bridges() -> list[Note]:
    out: list[Note] = []
    for bdir, source_domain, fixed_target in BRIDGE_DIRS:
        if not bdir.exists():
            continue
        for md in sorted(bdir.glob("*.md")):
            if md.stem in ("index",) or "README" in md.stem:
                continue
            text = md.read_text(encoding="utf-8")
            fm = parse_fm(text)
            if fixed_target:
                target = fixed_target
            else:
                target = _cross_domain_key(md.stem + " " + str(fm.get("domain", "")))
            n = Note(
                kind="bridge", domain=source_domain, stem=md.stem, topic=bdir.name,
                path=md, fm=fm, title_ko=h1(text) or md.stem,
                one_liner_ko=one_liner(text), text=text,
            )
            n.extra["source_domain"] = source_domain
            n.extra["target_domain"] = target
            n.extra["inputs"] = {
                dom: [t for t in (fm.get(key) or []) if isinstance(fm.get(key), list)]
                for key, dom in INPUT_KEYS.items() if fm.get(key)
            }
            out.append(n)
    return out


def _cross_domain_key(s: str) -> str:
    low = s.lower()
    if "econ" in low or "경제" in s:
        return "economics"
    if "art" in low or "예술" in s or "음악" in s or "music" in low:
        return "art"
    if "cs-ai" in low or "cs/ai" in low or "ai" in low or "cs" in low:
        return "cs-ai"
    return "other"


def _name_tokens(stem: str) -> list[str]:
    s = re.sub(r"^\d{2,3}-", "", stem).replace("·", "-")
    return [t for t in s.split("-") if t and not t.isdigit()]


def _fuzzy_resolve(dom: str, raw_path: str, stem: str,
                   by_key: dict[tuple[str, str], Note]) -> "Note | None":
    """Recover alias-style links: shortened stems (01-미시-기초 → 01-미시경제-기초),
    stale numbering (13-양자장론입문 → 12-양자장론), '·' vs '-', and bare numbers
    with a topic-dir hint (cross-arts/01). Resolves only on a UNIQUE match."""
    cands = [n for (d, _), n in by_key.items() if d == dom]
    norm = stem.replace("·", "-")
    # exact stem after '·' normalization
    hit = [n for n in cands if n.stem == norm]
    if len(hit) == 1:
        return hit[0]
    # bare number with topic-dir hint
    parts = raw_path.replace("\\", "/").split("/")
    if re.fullmatch(r"\d{2,3}", norm) and len(parts) >= 2:
        topic = parts[-2]
        hit = [n for n in cands if n.topic == topic and n.stem.startswith(norm + "-")]
        return hit[0] if len(hit) == 1 else None
    # token containment (both directions per token), unique winner only
    toks = _name_tokens(norm)
    if not toks:
        return None
    def match(n: Note) -> bool:
        ctoks = _name_tokens(n.stem)
        return all(any(t in c or c in t for c in ctoks) for t in toks)
    hit = [n for n in cands if match(n)]
    return hit[0] if len(hit) == 1 else None


def resolve_wikilinks(note: Note, by_key: dict[tuple[str, str], Note]) -> tuple[list[tuple[Note, str]], list[str]]:
    """Resolve [[wikilinks]] in a note body → [(target Note, fragment edge type)], [unresolved raw].

    Cross-domain aware: a link whose raw path contains another domain dir
    (e.g. ../../physics/modern/10-양자역학I) resolves in that domain; otherwise
    same-domain by stem (ported from concepts_to_neo4j.extract_edges, with the
    same line-keyword edge classification, lowered to fragment enum).
    """
    edges: list[tuple[Note, str]] = []
    unresolved: list[str] = []
    body = body_of(note.text)
    for line in body.splitlines():
        for raw in _WIKILINK.findall(line):
            target_path = raw.split("|")[0].split("#")[0].strip()
            stem = target_path.split("/")[-1]
            if stem.endswith(".md"):
                stem = stem[:-3]
            dom = note.domain
            for d in DOMAINS:
                if f"{d}/" in target_path.replace("\\", "/"):
                    dom = d
                    break
            tgt = by_key.get((dom, stem))
            if tgt is None and dom != note.domain:
                tgt = by_key.get((note.domain, stem))
            if tgt is None:
                # unique stem across all domains
                cands = [n for (d, s), n in by_key.items() if s == stem]
                tgt = cands[0] if len(cands) == 1 else None
            if tgt is None:
                tgt = _fuzzy_resolve(dom, target_path, stem, by_key)
            if tgt is None or tgt is note:
                if tgt is None:
                    unresolved.append(raw)
                continue
            if any(w in line for w in ("다음", "prereq", "선행")):
                rel = "prerequisite_of"
            elif any(w in line for w in ("한계", "이전", "한 단계 전")):
                rel = "derives_from"
            else:
                rel = "relates_to"
            edges.append((tgt, rel))
    return edges, unresolved


def parse_influence(persons: list[Note]) -> list[tuple[Note, Note, list[str]]]:
    """#### A → B (Tx) lines from influence-graph + artist-note bodies,
    kept only when both endpoints are collected artist notes."""
    def norm(name: str) -> str:
        base = re.sub(r"\s*\([^)]*\)\s*", "", name.split("/")[0]).strip().lower()
        return re.sub(r"[^0-9a-z가-힣]+", "", base)

    by_name: dict[str, Note] = {}
    for p in persons:
        names = [p.fm.get("artist_name", ""), p.fm.get("artist_kr", ""), p.stem.replace("-", " ")]
        names += p.fm.get("aliases") or []
        for nm in names:
            if nm:
                by_name.setdefault(norm(nm), p)

    texts = []
    if INFLUENCE_GRAPH.exists():
        texts.append(INFLUENCE_GRAPH.read_text(encoding="utf-8"))
    texts += [p.text for p in persons]

    seen: set[tuple[str, str]] = set()
    out: list[tuple[Note, Note, list[str]]] = []
    for text in texts:
        for m in _INFL.finditer(text):
            a, b = by_name.get(norm(m.group(1))), by_name.get(norm(m.group(2)))
            if not a or not b or a is b:
                continue
            key = (a.stem, b.stem)
            if key in seen:
                continue
            seen.add(key)
            out.append((a, b, _TYPES.findall(m.group(3))))
    return out


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                    encoding="utf-8")
