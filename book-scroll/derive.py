#!/usr/bin/env python3
"""Derive the technical chapters' beats from snapshots of their graph documents.

  python -X utf8 derive.py             every slot that has src/<slot>.md
  python -X utf8 derive.py 16 18       only those slots
  python -X utf8 derive.py --part0     Part 0: drop motion data and the four drawing sentences

src/<slot>.md is a verbatim snapshot of vinc_doc_get(<doc id>) on the account graph; the
graph is canon and the snapshot is a build input, the same discipline as
scripts/graph-refs.json. Re-fetch it, never edit it. src/manifest.json names the document
behind each file.

Kept from the existing ch/<slot>.json: n, no, part, file, slot, title, side, viewBox, the
close beat (surface chrome, not document text), and a claim sentence wherever the same
sentence exists in the document. Everything about motion (marks, lines, anchors, figures,
stage, conveyor, hub, pair, ring, ground, focusAt, focusScrub) is dropped: v2 is discarded.

From the document: the intro is the open beat and every `## ` section is one beat. Fenced
code blocks stay on the beat as `diagrams` (never rendered; they are what the new motion has
to draw). A line set as «...» or **...** is the claim. Bullet lists and tables fold into one
paragraph. Em dashes are not allowed on screen, so " — " becomes a sentence break.
"""
import json, os, re, sys, glob, difflib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
CH = os.path.join(HERE, "ch")
KEEP = ("n", "no", "part", "file", "slot", "title", "side", "viewBox")


def part_word(p):
    return "Closing" if p == 5 else f"Part {p}"


# A long intro (or a document with no `## ` headings at all) is split by hand:
# (index of the intro block that opens the new beat, its heading).
SPLITS = {
    "10": [(3, "Six Kinds, Six Questions"), (7, "Kinds Are Closed, Contents Are Not"),
           (9, "An Edge Is Not a Line"), (13, "Two Clocks and One Space"),
           (16, "The Source Text Is Kept Beside It")],
    "13": [(3, "Kind and Concept Are Different Layers")],
    "14": [(2, "What the Grammar Covers"), (5, "The Name Alone Is Not Enough")],
    "35": [(2, "Each Chapter Solved One Problem"), (4, "No Single Technology"),
           (5, "The Questions That Have to Survive"), (7, "Then It Is Not Data")],
}

# A paragraph at the end of the document that hands over to the next chapter belongs
# to the close beat, where the surface already keeps its transition and the next link.
TRANSITION = re.compile(r"\b(next chapter|following chapters|chapters look at|from here on|"
                        r"what's left is|what remains|one chapter remains)\b", re.I)

# Part 0 is not derived (its chapters were written long by the author and were not padded);
# only the drawing sentences leave. (slot, beat, field, old, new)
PART0_EDITS = [
    ("05", 5, "paras", " Same diagram, three strengths, and the strength is carried in how much ink the relationship is drawn with.", ""),
    ("06", 6, "paras", ", and the drawing should stay a diagram rather than become a physics figure.", "."),
    ("06", 8, "paras", "So changing the view must not change the memory. ", ""),
    ("06", 8, "claim", "No mark enters and none leaves. Only the arrangement changes.", "Changing the view must not change the memory."),
    ("09", 3, "paras", "Fill one in and the mark stops being the same substance: it looks complete and it is no longer evidence of anything.", "Fill one in and it looks complete, and it is no longer evidence of anything."),
]

CLAIM_RE = re.compile(r"^\s*(?:«(.+?)»|\*\*(.+?)\*\*)\s*$")


def clean(s):
    s = s.replace("**", "").replace("`", "")
    s = re.sub(r"\s+—\s+([^—.!?]+?)\s+—\s+", r" (\1) ", s)   # a parenthetical pair
    s = re.sub(r"\s*—\s*", ": ", s)                          # a single dash breaks the clause
    return re.sub(r"\s+", " ", s).strip()


def parse(md):
    """-> [[heading or None, [(kind, payload), ...]], ...]"""
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        lines = lines[i + 1:]
    sections, cur, i = [], [None, []], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            cur[1].append(("code", "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        if ln.startswith("# "):
            i += 1
            continue
        if ln.startswith("## "):
            sections.append(cur)
            cur = [ln[3:].strip(), []]
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        j, buf = i, []
        while j < len(lines) and lines[j].strip() and not lines[j].startswith("```") and not lines[j].startswith("#"):
            buf.append(lines[j])
            j += 1
        if all(b.lstrip().startswith(("- ", "* ")) for b in buf):
            cur[1].append(("list", [re.sub(r"^\s*[-*]\s+", "", b) for b in buf]))
        elif all(b.strip().startswith("|") for b in buf):
            rows = [[c.strip() for c in b.strip().strip("|").split("|")] for b in buf]
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            cur[1].append(("table", rows))
        else:
            cur[1].append(("para", " ".join(b.lstrip("> ").strip() for b in buf)))
        i = j
    sections.append(cur)
    return sections


def beat_from(blocks, h=None):
    b = {"paras": []}
    if h:
        b["h"] = h
    for kind, payload in blocks:
        if kind == "code":
            b.setdefault("diagrams", []).append(payload)
        elif kind == "list":
            items = [clean(x) for x in payload]
            if all(re.search(r"[.?!]$", x) for x in items):      # whole sentences or questions
                b["paras"].append(" ".join(x[0].upper() + x[1:] for x in items))
            else:                                               # fragments
                b["paras"].append("; ".join(x.rstrip(".") for x in items) + ".")
        elif kind == "table":
            head, rows = payload[0], payload[1:]
            b["paras"].append(" ".join(f"{clean(r[0])}: {clean(r[1]).rstrip('.')}." for r in rows if len(r) >= 2))
        else:
            m = CLAIM_RE.match(payload)
            if m and "claim" not in b:
                b["claim"] = clean(m.group(1) or m.group(2))
            else:
                b["paras"].append(clean(payload))
    return b


def sentences(p):
    return [s for s in re.split(r"(?<=[.?!])\s+", p) if s]


def similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def carry_claims(old_beats, new_beats):
    """Where an old claim sentence exists in the document, set it apart again."""
    claims = [b["claim"] for b in old_beats if b.get("claim")]
    for nb in new_beats:
        if nb.get("claim") or not claims:
            continue
        best = None
        for pi, p in enumerate(nb["paras"]):
            for s in sentences(p):
                for c in claims:
                    r = similar(s, c)
                    if r >= 0.6 and (best is None or r > best[0]):
                        best = (r, pi, s)
        if best:
            _, pi, s = best
            nb["claim"] = s
            rest = " ".join(x for x in sentences(nb["paras"][pi]) if x != s)
            if rest:
                nb["paras"][pi] = rest
            else:
                del nb["paras"][pi]


def derive(slot):
    path = os.path.join(CH, f"ch{slot}.json")
    old = json.load(open(path, encoding="utf-8"))
    md = open(os.path.join(SRC, f"{slot}.md"), encoding="utf-8").read()
    secs = parse(md)
    if slot in SPLITS:                              # hand split of the intro
        blocks, cuts = secs[0][1], SPLITS[slot]
        head = [[None, blocks[:cuts[0][0]]]]
        for k, (start, h) in enumerate(cuts):
            end = cuts[k + 1][0] if k + 1 < len(cuts) else len(blocks)
            head.append([h, blocks[start:end]])
        secs = head + secs[1:]
    intro, body = secs[0], secs[1:]
    if not any(k == "para" for k, _ in intro[1]) and body:   # document opens on a heading
        intro = [None, intro[1] + body[0][1]]
        body = body[1:]

    no, part = old["no"], old["part"]
    close = next((b for b in old["beats"] if b.get("kind") == "close"), None)
    beats = [dict(kind="open", label=f"{no}.0", eyebrow=f"{part_word(part)} · Chapter {no}",
                  h=old["title"], **beat_from(intro[1]))]
    for h, blocks in body:
        beats.append(beat_from(blocks, h))
    # the document's own hand-over paragraphs become the close beat's text
    if close:
        moved = []
        while beats[-1]["paras"]:
            p = beats[-1]["paras"][-1]
            if TRANSITION.search(p) or any(similar(p, q) >= 0.6 for q in close.get("paras", [])):
                moved.insert(0, beats[-1]["paras"].pop())
            else:
                break
        if not beats[-1]["paras"] and "diagrams" not in beats[-1]:
            beats.pop()
        if moved:
            close["paras"] = moved
    carry_claims(old["beats"], beats)
    for j, b in enumerate(beats):
        b["label"] = f"{no}.{j}"
        if "h" not in b and j:
            b["h"] = ""
    if close:
        close["label"] = f"{no}.close"
        beats.append(close)
    out = {k: old[k] for k in KEEP if k in old}
    out["beats"] = beats
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    words = sum(len(re.findall(r"\S+", p)) for b in beats for p in b["paras"])
    print(f"ch{slot}: {len(beats)} beats, {words}w, claims {sum(1 for b in beats if b.get('claim'))}, "
          f"diagrams {sum(len(b.get('diagrams', [])) for b in beats)}")


def part0():
    for slot in ["01", "02", "03", "04", "05", "06", "07", "08", "09"]:
        path = os.path.join(CH, f"ch{slot}.json")
        ch = json.load(open(path, encoding="utf-8"))
        for s, bi, field, old, new in PART0_EDITS:
            if s != slot:
                continue
            b = ch["beats"][bi]
            if field == "claim":
                if b.get("claim") == new:
                    continue
                assert b.get("claim") == old, (slot, bi, b.get("claim"))
                b["claim"] = new
            else:
                hits = [i for i, p in enumerate(b["paras"]) if old in p]
                if not hits and any(new and new in p for p in b["paras"]):
                    continue
                assert len(hits) == 1, (slot, bi, old, hits)
                b["paras"][hits[0]] = b["paras"][hits[0]].replace(old, new).strip()
        out = {k: ch[k] for k in KEEP if k in ch}
        out["beats"] = ch["beats"]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        print(f"ch{slot}: motion dropped, {len(out['beats'])} beats")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--part0" in sys.argv:
        part0()
    slots = args or sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(SRC, "*.md")))
    if not ("--part0" in sys.argv and not args):
        for s in slots:
            derive(s)
