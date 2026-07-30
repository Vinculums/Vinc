# -*- coding: utf-8 -*-
"""weg-9000 learning notes → Vinc starter packs (.vincpack, share-pack v1).

Contract: Vinc/api/share.py (vinc_pack:1, kind:share, manifest, fragment) +
Vinc/api/fragment.py (5 node types, 5 edge types, props free JSON).

Key rules honored:
- every edge's endpoints are nodes of the SAME pack (fragment type-checks edges
  against its own node list);
- nodes duplicated across packs are built once globally → byte-identical
  (import order must not change content, since MERGE SET overwrites on match);
- bridge nodes carry only {id,type,title,props} (Bridge table has no tags/domain
  columns; domain link goes to props.source_domain/target_domain);
- domain nodes carry only {id,type} (props on Domain → import warning).

Pack membership (per domain D):
  own concepts (+persons for art) + bridges touching D
  + 1-hop closure: every concept referenced by those nodes' edges
  → edges filtered to the pack's node set.

Body language: reads English markdown from work/{domain}/{stem}.md when present,
else falls back to Korean source (counted in the report — must be 0 for release).

Usage: python export_vinc_pack.py [--created ISO8601] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from packlib import (DOMAINS, HUB, Note, body_of, collect_bridges,
                     collect_concepts, collect_persons, dump_json, load_json,
                     one_liner, parse_influence, resolve_wikilinks)

TOOLS = Path(__file__).parent
ROOT = TOOLS.parent
WORK = ROOT / "work"
GLOSSARY = TOOLS / "glossary.json"

# UI-visible copy: no em-dash (project rule — use ·/colon instead)
PACK_NOTE = {
    "physics": "Physics starter pack: classical mechanics through quantum gravity frontiers, with cross-domain bridges to economics and art.",
    "math": "Mathematics starter pack: foundations through Langlands and combinatorics, with bridges into physics, economics, and art.",
    "economics": "Economics starter pack: micro and macro foundations through financial engineering and econometrics, bridged to statistical mechanics.",
    "art": "Art starter pack: aesthetics, cross-arts, non-western traditions, and artist profiles with influence ties.",
}

PERSON_PROP_KEYS = ("artist_name", "artist_kr", "born", "died", "nationality",
                    "civilization", "field", "period", "gender", "methodology")


def en_body(note: Note, report: dict) -> str:
    """English body from work/, falling back to Korean source."""
    cand = WORK / note.domain / f"{note.stem}.md"
    if cand.exists():
        return body_of(cand.read_text(encoding="utf-8")).strip()
    report["ko_fallback"].append(f"{note.domain}/{note.stem}")
    return body_of(note.text).strip()


_ONE_LINERS = TOOLS / "one_liners.json"
_ol_supplement: dict[str, str] = load_json(_ONE_LINERS) if _ONE_LINERS.exists() else {}


def en_one_liner(note: Note) -> str:
    cand = WORK / note.domain / f"{note.stem}.md"
    if cand.exists():
        ol = one_liner(cand.read_text(encoding="utf-8"))
        if ol:
            return ol
    # sources without a 한 줄 핵심 section: curated supplement (tools/one_liners.json)
    return _ol_supplement.get(f"{note.domain}/{note.stem}", "") or note.one_liner_ko


def build_all(created: str) -> tuple[dict[str, dict], dict]:
    report: dict = {"ko_fallback": [], "unresolved_links": {}, "packs": {}}
    gl = load_json(GLOSSARY)

    concepts = collect_concepts()
    persons = collect_persons()
    bridges = collect_bridges()
    notes = concepts + persons + bridges

    # glossary application
    missing_gl = []
    for n in notes:
        entry = gl.get(f"{n.domain}/{n.stem}")
        if not entry or entry.get("needs_review"):
            missing_gl.append(f"{n.domain}/{n.stem}")
            continue
        n.slug = entry["slug"]
        n.title_en = entry["title_en"]
        n.node_id = (f"bridge:{n.slug}" if n.kind == "bridge"
                     else f"concept:{n.domain}-{n.slug}")
    if missing_gl:
        raise SystemExit(f"[ERROR] glossary missing/unreviewed for: {missing_gl} — run build_glossary.py")

    by_key = {(n.domain, n.stem): n for n in notes if n.kind != "bridge"}
    by_id = {n.node_id: n for n in notes}

    # ── global edges ─────────────────────────────────────────────────────────
    edges: set[tuple[str, str, str]] = set()
    neighbor: dict[str, set[str]] = defaultdict(set)

    for n in [x for x in notes if x.kind in ("concept", "person")]:
        targets, unresolved = resolve_wikilinks(n, by_key)
        if unresolved:
            report["unresolved_links"][f"{n.domain}/{n.stem}"] = sorted(set(unresolved))
        for tgt, rel in targets:
            edges.add((n.node_id, rel, tgt.node_id))
            neighbor[n.node_id].add(tgt.node_id)
            neighbor[tgt.node_id].add(n.node_id)

    for b in bridges:
        for dom, stems in b.extra.get("inputs", {}).items():
            for stem in stems:
                tgt = by_key.get((dom, stem))
                if tgt is None:
                    report["unresolved_links"].setdefault(
                        f"{b.domain}/{b.stem}", []).append(f"{dom}:{stem}")
                    continue
                edges.add((b.node_id, "connects_via", tgt.node_id))
                neighbor[b.node_id].add(tgt.node_id)

    influences = parse_influence(persons)
    infl_by_person: dict[str, list[dict]] = defaultdict(list)
    for a, bnote, types in influences:
        edges.add((a.node_id, "relates_to", bnote.node_id))
        neighbor[a.node_id].add(bnote.node_id)
        neighbor[bnote.node_id].add(a.node_id)
        infl_by_person[a.node_id].append(
            {"to": bnote.fm.get("artist_name", bnote.stem), "types": types, "direction": "influences"})
        infl_by_person[bnote.node_id].append(
            {"from": a.fm.get("artist_name", a.stem), "types": types, "direction": "influenced_by"})

    # ── global node dicts (built once → identical across packs) ─────────────
    # body_md 는 더 이상 props 에 싣지 않는다(D45 Chunk=SSoT) — 원문은 pack 의
    # documents 로 나가 import 시 Chunk 문서가 된다. bodies[node_id] 에 원문을 모아
    # 두었다가 pack 조립에서 그 pack 의 노드에 해당하는 문서만 싣는다(자기완결).
    node_json: dict[str, dict] = {}
    bodies: dict[str, dict] = {}
    for n in notes:
        src_file = str(n.path.relative_to(HUB)).replace("\\", "/")
        body = en_body(n, report)
        if body:
            bodies[n.node_id] = {"title": n.title_en, "origin": src_file, "text": body}
        if n.kind == "bridge":
            props = {
                "one_liner": en_one_liner(n),
                "source_domain": n.extra["source_domain"],
                "target_domain": n.extra["target_domain"],
                "status": n.fm.get("status", ""),
                "created": n.fm.get("created", ""),
                "source_file": src_file,
                "aliases": (n.fm.get("aliases") or []) + [n.title_ko],
            }
            node_json[n.node_id] = {"id": n.node_id, "type": "bridge",
                                    "title": n.title_en, "props": props}
            continue
        # scaffolding tags carry no retrieval value (every note has them) and
        # crowd out real topic chips in the community gallery summary
        tags = [t for t in (n.fm.get("tags") or [])
                if t and t not in ("learning-note", "learning")]
        if n.kind == "person" and "person" not in tags:
            tags.append("person")
        props = {
            "one_liner": en_one_liner(n),
            "topic": n.topic,
            "status": n.fm.get("status", ""),
            "created": n.fm.get("created", ""),
            "source_file": src_file,
            "aliases": sorted(set((n.fm.get("aliases") or []) + [n.title_ko])),
        }
        for k in ("curriculum_no", "sequence"):
            if n.fm.get(k):
                props["order"] = n.fm[k]
                break
        if n.kind == "person":
            for k in PERSON_PROP_KEYS:
                if n.fm.get(k):
                    props[k] = n.fm[k]
            if infl_by_person.get(n.node_id):
                props["influences"] = infl_by_person[n.node_id]
        node_json[n.node_id] = {"id": n.node_id, "type": "concept",
                                "title": n.title_en, "domain": n.domain,
                                "tags": tags, "props": props}

    # ── pack assembly ────────────────────────────────────────────────────────
    packs: dict[str, dict] = {}
    for D in DOMAINS:
        own = {n.node_id for n in notes if n.kind != "bridge" and n.domain == D}
        own |= {b.node_id for b in bridges
                if D in (b.extra["source_domain"], b.extra["target_domain"])}
        closure = set(own)
        for nid in own:
            closure |= {t for t in neighbor.get(nid, set()) if t in node_json}
        pack_edges = sorted(
            (s, r, t) for (s, r, t) in edges if s in closure and t in closure)
        pack_nodes = [node_json[nid] for nid in sorted(closure)]
        dom_names = {D} | {
            node_json[nid]["props"][k] for nid in closure
            if node_json[nid]["type"] == "bridge"
            for k in ("source_domain", "target_domain")
            if node_json[nid]["props"][k] in DOMAINS}
        nodes = [{"id": d, "type": "domain"} for d in sorted(dom_names)] + pack_nodes
        edge_dicts = [{"from": s, "to": t, "type": r} for (s, r, t) in pack_edges]
        n_concepts = sum(1 for x in nodes if x["type"] == "concept")
        # 자기완결(D45) — 이 pack 노드의 원문을 documents 로 싣는다(개념+브릿지 body).
        # import 시 put_doc(doc_id=None)이 수신 owner 로 재파생하므로 여기 doc_id·
        # content_hash 는 pack 자체 정합용(수신측 재계산). MENTIONS 는 import 가 재링크.
        documents = []
        for nid in sorted(closure):
            b = bodies.get(nid)
            if not b:
                continue
            ch = hashlib.sha256(b["text"].encode("utf-8")).hexdigest()
            documents.append({
                "doc_id": "doc:" + hashlib.sha256(b["origin"].encode("utf-8")).hexdigest()[:16],
                "title": b["title"], "origin": b["origin"],
                "content_hash": ch, "updated_at": created, "text": b["text"],
            })
        pack = {
            "vinc_pack": 1,
            "kind": "share",
            "manifest": {
                "created": created,
                "scope": {"domain": D, "tags": [], "seed": None, "depth": None, "ids": None},
                "counts": {"concepts": n_concepts, "records": 0,
                           "edges": len(edge_dicts), "documents": len(documents)},
                "note": PACK_NOTE[D],
            },
            "fragment": {"nodes": nodes, "edges": edge_dicts},
        }
        if documents:
            pack["documents"] = documents
        packs[D] = pack
        report["packs"][D] = {
            "nodes": len(nodes), "concepts": n_concepts,
            "bridges": sum(1 for x in nodes if x["type"] == "bridge"),
            "domains": len(dom_names), "edges": len(edge_dicts),
            "documents": len(documents),
            "closure_pulled": len(closure - own),
        }
    return packs, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--created", default="2026-07-16T00:00:00Z")
    ap.add_argument("--out", default=str(ROOT))
    args = ap.parse_args()

    packs, report = build_all(args.created)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # D55 — 팩은 zip 컨테이너로 쓴다(사이드카 api/packfile.py 와 **같은 계약**).
    # 이 도구가 Vinc 리포 안으로 들어오면서(2026-07-31) 개발 기기 절대경로를
    # 하드코딩하던 것을 리포 상대경로로 바꿨다 — 예전엔
    # `D:/Projects/AIProjects/vinc-project/Vinc` 를 sys.path 에 얹어서 그 기기
    # 밖에서는 아예 돌지 않았다. 지금은 starter-packs/tools/ 기준 2단계 위가 리포 루트.
    import sys as _sys
    _sys.path.insert(0, str(TOOLS.parents[1]))
    from api import packfile as _pf
    for D, pack in packs.items():
        size = _pf.write_file(out / f"vinc-pack-{D}.vincpack", pack)
        report.setdefault("bytes", {})[D] = size
    dump_json(TOOLS / "export-report.json", report)

    print("packs written:")
    for D, st in report["packs"].items():
        print(f"  {D:10} nodes={st['nodes']:4} (concepts={st['concepts']}, bridges={st['bridges']}, "
              f"+closure={st['closure_pulled']}) edges={st['edges']} documents={st['documents']}")
    print(f"ko_fallback bodies: {len(report['ko_fallback'])}")
    print(f"notes with unresolved links: {len(report['unresolved_links'])}")


if __name__ == "__main__":
    main()
